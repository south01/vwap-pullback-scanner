"""
Core scan loop — polls Massive Market Data API, manages per-ticker state,
evaluates conditions, and fires Telegram alerts.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import requests

import config
from alerts import (
    compute_targets,
    send_grind_warning,
    send_session_summary,
    send_startup,
    send_tier1,
    send_tier2,
    send_volume_removal,
)
from conditions import c2_in_touch_zone, evaluate_tier1
from indicators import compute_atr, compute_rsi, compute_vwap, rsi_series, session_bars
from shared_state import AlertRecord, TickerSnapshot, state
from state import TickerState
from utils import SESSION_OPEN, is_market_open, load_watchlist, ms_to_et, now_et, today_str

log = logging.getLogger("vwap_scanner")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({"Authorization": f"Bearer {config.MASSIVE_API_KEY}"})


def _get(path: str, params: dict | None = None, retries: int = 3) -> dict | None:
    url = config.MASSIVE_BASE_URL.rstrip("/") + path
    for attempt in range(1, retries + 1):
        try:
            resp = _SESSION.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.error("API error (attempt %d/%d) %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(10)
    return None


def fetch_bars(ticker: str, timespan: str = "minute", multiplier: int = 5) -> list[dict]:
    date = today_str()
    path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{date}/{date}"
    data = _get(path, {"adjusted": "true", "sort": "asc", "limit": 200})
    if data and data.get("resultsCount", 0) > 0:
        return data["results"]
    return []


def fetch_snapshot(ticker: str) -> dict | None:
    path = f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    return _get(path)


def fetch_vix() -> float | None:
    snap = fetch_snapshot(config.VIX_TICKER)
    if snap is None:
        log.info("[VIX] Fetch failed — displaying N/A in alerts")
        return None
    try:
        value = snap["ticker"]["day"]["c"]
        if value is None or float(value) <= 0:
            log.info("[VIX] Fetch failed — displaying N/A in alerts")
            return None
        return float(value)
    except (KeyError, TypeError):
        log.info("[VIX] Fetch failed — displaying N/A in alerts")
        return None


def verify_api() -> bool:
    log.info("Verifying API connectivity…")
    result = _get("/v2/snapshot/locale/us/markets/stocks/tickers/SPY")
    if result:
        log.info("API connectivity OK")
        return True
    log.error("API connectivity check FAILED")
    return False


# ---------------------------------------------------------------------------
# SPY helpers
# ---------------------------------------------------------------------------

def spy_change_pct(spy_snapshot: dict | None) -> float:
    if spy_snapshot is None:
        return 0.0
    try:
        day  = spy_snapshot["ticker"]["day"]
        prev = spy_snapshot["ticker"]["prevDay"]
        return (day["c"] - prev["c"]) / prev["c"] * 100
    except (KeyError, TypeError, ZeroDivisionError):
        return 0.0


# ---------------------------------------------------------------------------
# Per-ticker helpers
# ---------------------------------------------------------------------------

def _price_and_vwap_from_snapshot(snap: dict) -> tuple[float, float] | None:
    """Extract real-time current price and session VWAP from a snapshot response."""
    try:
        t   = snap["ticker"]
        # day.vw is the session VWAP (volume-weighted, from market open)
        # min.c is the most recent 1-minute bar close = current price
        price = t["min"]["c"]
        vwap  = t["day"]["vw"]
        return float(price), float(vwap)
    except (KeyError, TypeError):
        return None


def process_ticker(
    ticker: str,
    ticker_state: TickerState,
    spy_snapshot: dict | None,
    vix_value: float | None,
    alert_counts: dict[str, dict],
    spy_chg: float,
) -> None:
    # --- Bars first: current price comes from the latest bar's vw field ---
    # Snapshot prices can lag 1–3 polling cycles; bar vw is updated each poll.
    raw_bars       = fetch_bars(ticker, timespan="minute", multiplier=5)
    indicator_bars = raw_bars if raw_bars else []

    if len(indicator_bars) < 3:
        log.info("%s | Too few bars for indicators (%d) — skipping", ticker, len(indicator_bars))
        return

    current_price = indicator_bars[-1]["vw"]   # Bug 1 fix: use bar vw, not snapshot
    closed_bars   = indicator_bars[:-1]
    atr = compute_atr(closed_bars, config.ATR_PERIOD)
    rsi = compute_rsi(closed_bars, config.RSI_PERIOD)

    # --- Snapshot for session VWAP only (day.vw is a session aggregate) ---
    snap = fetch_snapshot(ticker)
    pv   = _price_and_vwap_from_snapshot(snap) if snap else None
    if pv is None:
        log.info("%s | Snapshot unavailable — skipping", ticker)
        return
    _, vwap = pv   # discard snapshot price; keep session VWAP

    # Build a synthetic vwap_series aligned to closed_bars for C1
    # (all values = session VWAP from snapshot so trend check uses real VWAP)
    vwap_series = [vwap] * len(closed_bars)

    if atr == 0:
        log.info("%s | ATR zero — skipping", ticker)
        return

    recent_closes = [bar["c"] for bar in closed_bars[-2:]]
    in_zone_now = c2_in_touch_zone(current_price, vwap, atr, recent_closes, ticker)

    if in_zone_now and not ticker_state.in_touch_zone:
        ticker_state.new_touch()
        touch_time = now_et().strftime("%H:%M:%S")
        log.info("%s | TOUCH #%d @ $%.2f at %s ET",
                 ticker, ticker_state.vwap_touch_count, current_price, touch_time)
        state.record_touch(ticker, ticker_state.vwap_touch_count, touch_time, current_price)
    elif not in_zone_now and ticker_state.in_touch_zone:
        ticker_state.in_touch_zone = False
        log.info("%s | Exited touch zone", ticker)

    # Grind warning — only count real new bars (new timestamp + non-zero volume)
    latest_bar = indicator_bars[-1]
    new_bar_appeared = (
        latest_bar["t"] > ticker_state.last_bar_ts and latest_bar["v"] > 0
    )
    if new_bar_appeared:
        ticker_state.last_bar_ts = latest_bar["t"]

    if ticker_state.tier2_fired_for_touch and not ticker_state.grind_warning_fired:
        if new_bar_appeared:
            if abs(current_price - vwap) / vwap <= config.GRIND_ZONE_PCT:
                ticker_state.grind_bar_count += 1
            else:
                ticker_state.grind_bar_count = 0
        if ticker_state.grind_bar_count >= config.GRIND_MAX_BARS:
            send_grind_warning(ticker, ticker_state.grind_bar_count)
            ticker_state.grind_warning_fired = True

    # Evaluate conditions
    all_pass, cond_results = evaluate_tier1(
        closed_bars, vwap_series, current_price, vwap, atr,
        ticker_state, spy_snapshot, vix_value,
    )

    cond_str = " ".join(
        f"{k}:{'✓' if v else '✗'}" for k, v in sorted(cond_results.items())
    )
    log.info("%s | price=%.2f vwap=%.2f atr=%.2f rsi=%.1f | %s",
             ticker, current_price, vwap, atr, rsi, cond_str)

    # Push to shared state dashboard
    snap = TickerSnapshot(
        price=current_price,
        vwap=vwap,
        atr=atr,
        rsi=rsi,
        touch_count=ticker_state.vwap_touch_count,
        conditions=dict(cond_results),
        tier1_fired=ticker_state.tier1_fired_for_touch,
        tier2_fired=ticker_state.tier2_fired_for_touch,
        last_updated=now_et().strftime("%H:%M:%S"),
    )
    state.update_ticker(ticker, snap)

    if all_pass and not ticker_state.tier1_fired_for_touch:
        sl, tp1, tp2 = compute_targets(current_price, atr)
        sent = send_tier1(
            ticker=ticker,
            price=current_price,
            vwap=vwap,
            atr=atr,
            touch_count=ticker_state.vwap_touch_count,
            rsi=rsi,
            sl=sl,
            spy_chg_pct=spy_chg,
            vix=vix_value or 0.0,
        )
        if sent:
            ticker_state.tier1_fired_for_touch = True
            counts = alert_counts.setdefault(ticker, {})
            counts["tier1"] = counts.get("tier1", 0) + 1
            state.record_alert(AlertRecord(
                time=now_et().strftime("%H:%M"),
                ticker=ticker,
                tier=1,
                price=current_price,
                vwap=vwap,
                details=f"Touch #{ticker_state.vwap_touch_count} | RSI {rsi:.1f}",
            ))


def process_tier2(
    ticker: str,
    ticker_state: TickerState,
    alert_counts: dict[str, dict],
) -> None:
    if ticker_state.tier2_fired_for_touch:
        return

    # Bars first: derive current price from latest bar's vw (avoids snapshot staleness)
    bars_5m = fetch_bars(ticker, timespan="minute", multiplier=5)
    if len(bars_5m) < 2:
        return

    current_price = bars_5m[-1]["vw"]   # Bug 1 fix: use bar vw, not snapshot
    closed_5m     = bars_5m[:-1]

    atr = compute_atr(closed_5m, config.ATR_PERIOD)
    if atr == 0:
        return

    # Snapshot for session VWAP + 1-min bar high (for tier2 trigger)
    snap = fetch_snapshot(ticker)
    pv   = _price_and_vwap_from_snapshot(snap) if snap else None
    if pv is None:
        return
    _, vwap = pv   # discard snapshot price; keep session VWAP

    # Prior 1-min bar high from snapshot (min.h = high of the last complete minute bar)
    try:
        prior_bar_high = float(snap["ticker"]["min"]["h"])
    except (KeyError, TypeError):
        prior_bar_high = 0.0

    recent_closes = [bar["c"] for bar in closed_5m[-2:]]
    in_zone = c2_in_touch_zone(current_price, vwap, atr, recent_closes, ticker)

    if ticker_state.last_1min_high > 0 and current_price > ticker_state.last_1min_high and in_zone:
        sl, tp1, tp2 = compute_targets(current_price, atr)
        sent = send_tier2(
            ticker=ticker,
            price=current_price,
            vwap=vwap,
            atr=atr,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            r1=config.TP1_R,
            r2=config.TP2_R,
        )
        if sent:
            ticker_state.tier2_fired_for_touch = True
            counts = alert_counts.setdefault(ticker, {})
            counts["tier2"] = counts.get("tier2", 0) + 1
            state.record_alert(AlertRecord(
                time=now_et().strftime("%H:%M"),
                ticker=ticker,
                tier=2,
                price=current_price,
                vwap=vwap,
                details=f"1m cross ${ticker_state.last_1min_high:.2f}",
            ))
            log.info("%s | Tier 2 fired at %.2f", ticker, current_price)

    ticker_state.last_1min_high = prior_bar_high


# ---------------------------------------------------------------------------
# Volume eligibility helpers
# ---------------------------------------------------------------------------

def _avg_session_volume(ticker: str, start_date: str, end_date: str) -> float | None:
    """Return mean 5-min bar volume during regular session hours, or None on failure."""
    path = f"/v2/aggs/ticker/{ticker}/range/5/minute/{start_date}/{end_date}"
    data = _get(path, {"adjusted": "true", "sort": "asc", "limit": 1000})
    if not data or data.get("resultsCount", 0) == 0:
        return None
    vols = []
    for bar in data["results"]:
        bar_et = ms_to_et(bar["t"])
        h, m = bar_et.hour, bar_et.minute
        if (h, m) >= (9, 30) and (h, m) < (16, 0):
            vols.append(bar["v"])
    return sum(vols) / len(vols) if vols else None


def _run_volume_eligibility(
    tickers: list[str],
) -> tuple[list[str], list[tuple[str, float]]]:
    """
    Check every ticker against the minimum average session volume threshold.
    Returns (active_tickers, [(removed_ticker, avg_vol), ...]).
    Tickers whose history cannot be fetched are kept active.
    """
    now = now_et()
    end_date   = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=config.VOLUME_LOOKBACK_DAYS * 2 + 2)).strftime("%Y-%m-%d")

    active: list[str] = []
    removed: list[tuple[str, float]] = []

    for ticker in tickers:
        avg_vol = _avg_session_volume(ticker, start_date, end_date)
        if avg_vol is None:
            log.warning("%s | Volume history unavailable — keeping in active scan", ticker)
            active.append(ticker)
        elif avg_vol < config.MIN_AVG_VOLUME:
            log.info(
                "[%s] REMOVED FROM SESSION — avg vol %.0f below threshold %d",
                ticker, avg_vol, config.MIN_AVG_VOLUME,
            )
            send_volume_removal(ticker, avg_vol)
            removed.append((ticker, avg_vol))
        else:
            active.append(ticker)
        time.sleep(config.REQUEST_DELAY_SEC)

    return active, removed


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class Scanner:
    def __init__(self) -> None:
        self.watchlist:      list[str] = []
        self.active_tickers: list[str] = []   # watchlist minus low-volume tickers
        self.states:         dict[str, TickerState] = {}
        self.spy_snapshot:   dict | None = None
        self.vix_value:      float | None = None
        self.alert_counts:   dict[str, dict] = {}

        self._last_spy_refresh:   float = 0.0
        self._last_vix_refresh:   float = 0.0
        self._last_poll:          float = 0.0
        self._last_tier2_poll:    float = 0.0
        self._last_reset_date:    str   = ""
        self._running:            bool  = True

    # ------------------------------------------------------------------
    # Public API (also called by TelegramCommandHandler)
    # ------------------------------------------------------------------

    def add_ticker(self, ticker: str) -> None:
        if ticker in self.watchlist:
            return
        self.watchlist.append(ticker)
        self.active_tickers.append(ticker)
        self.states[ticker] = TickerState(ticker=ticker)
        state.set_tickers(self.active_tickers)
        log.info("Ticker added: %s", ticker)

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self.watchlist:
            return
        self.watchlist.remove(ticker)
        if ticker in self.active_tickers:
            self.active_tickers.remove(ticker)
        self.states.pop(ticker, None)
        state.set_tickers(self.active_tickers)
        log.info("Ticker removed: %s", ticker)

    def setup(self) -> None:
        self.watchlist = load_watchlist(config.WATCHLIST_PATH)
        self.states    = {t: TickerState(ticker=t) for t in self.watchlist}
        state.started_at = now_et().strftime("%Y-%m-%d %H:%M ET")
        log.info("Loaded %d tickers from watchlist", len(self.watchlist))

        active, removed = _run_volume_eligibility(self.watchlist)
        self.active_tickers = active
        state.set_tickers(self.active_tickers)

        send_startup(self.active_tickers, removed)
        self._refresh_spy()
        self._refresh_vix()

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        log.info("Scan loop started")
        while self._running:
            now   = now_et()
            today = now.strftime("%Y-%m-%d")

            if today != self._last_reset_date and now.time() >= SESSION_OPEN:
                self._reset_daily(today)

            market_open = is_market_open()
            state.set_market_open(market_open)

            if state.is_paused or not market_open:
                time.sleep(10)
                continue

            t = time.monotonic()

            if t - self._last_spy_refresh >= config.SPY_REFRESH_SEC:
                self._refresh_spy()
                self._last_spy_refresh = t

            if t - self._last_vix_refresh >= config.VIX_REFRESH_SEC:
                self._refresh_vix()
                self._last_vix_refresh = t

            if t - self._last_poll >= config.POLL_INTERVAL_SEC:
                self._run_main_poll()
                self._last_poll = t

            if t - self._last_tier2_poll >= config.TIER2_POLL_SEC:
                self._run_tier2_poll()
                self._last_tier2_poll = t

            if now.hour == 15 and now.minute >= 55:
                self._send_eod_summary()
                log.info("End-of-day — scanner idle until next session")
                deadline = time.monotonic() + 3600
                while self._running and time.monotonic() < deadline:
                    time.sleep(5)
                continue

            time.sleep(1)

    def send_shutdown_summary(self) -> None:
        now_str = now_et().strftime("%Y-%m-%d %H:%M ET")
        t1 = sum(c.get("tier1", 0) for c in self.alert_counts.values())
        t2 = sum(c.get("tier2", 0) for c in self.alert_counts.values())
        send_session_summary(t1, t2, self.alert_counts, now_str)

    # ------------------------------------------------------------------

    def _reset_daily(self, today: str) -> None:
        log.info("Daily reset — new session %s", today)
        self.alert_counts = {}
        for s in self.states.values():
            s.reset()
        state.reset_day()
        self._last_reset_date = today

        active, removed = _run_volume_eligibility(self.watchlist)
        self.active_tickers = active
        state.set_tickers(self.active_tickers)
        send_startup(self.active_tickers, removed)

    def _refresh_spy(self) -> None:
        snap = fetch_snapshot("SPY")
        if snap:
            self.spy_snapshot = snap
            chg = spy_change_pct(snap)
            state.set_macro(chg, self.vix_value or 0.0)
            log.info("SPY refreshed: %+.2f%%", chg)
        else:
            log.warning("SPY refresh failed — using cached value")

    def _refresh_vix(self) -> None:
        vix = fetch_vix()
        if vix is not None:
            self.vix_value = vix
            spy_chg = spy_change_pct(self.spy_snapshot) if self.spy_snapshot else 0.0
            state.set_macro(spy_chg, vix)
            log.info("VIX refreshed: %.1f", vix)
        else:
            log.warning("VIX refresh failed — using cached value")

    def _run_main_poll(self) -> None:
        now_str = now_et().strftime("%H:%M:%S ET")
        log.info("--- Main poll cycle %s ---", now_str)
        spy_chg = spy_change_pct(self.spy_snapshot)
        for ticker in list(self.active_tickers):
            try:
                process_ticker(
                    ticker,
                    self.states[ticker],
                    self.spy_snapshot,
                    self.vix_value,
                    self.alert_counts,
                    spy_chg,
                )
            except Exception as exc:
                log.error("%s | Unhandled error in main poll: %s", ticker, exc)
            time.sleep(config.REQUEST_DELAY_SEC)
        state.set_scan_time(now_et().strftime("%H:%M:%S ET"))

    def _run_tier2_poll(self) -> None:
        qualifying = [
            t for t in self.active_tickers
            if self.states[t].tier1_fired_for_touch and not self.states[t].tier2_fired_for_touch
        ]
        if not qualifying:
            return
        log.info("Tier 2 poll — %d qualifying tickers", len(qualifying))
        for ticker in qualifying:
            try:
                process_tier2(ticker, self.states[ticker], self.alert_counts)
            except Exception as exc:
                log.error("%s | Unhandled error in Tier 2 poll: %s", ticker, exc)
            time.sleep(config.REQUEST_DELAY_SEC)

    def _send_eod_summary(self) -> None:
        now_str = now_et().strftime("%Y-%m-%d %H:%M ET")
        t1 = sum(c.get("tier1", 0) for c in self.alert_counts.values())
        t2 = sum(c.get("tier2", 0) for c in self.alert_counts.values())
        send_session_summary(t1, t2, self.alert_counts, now_str)
        log.info("End-of-day summary sent")
