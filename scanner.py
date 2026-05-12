"""
Core scan loop — polls Massive Market Data API, manages per-ticker state,
evaluates conditions, and fires Telegram alerts.
"""

from __future__ import annotations

import logging
import time

import requests

import config
from alerts import (
    compute_targets,
    send_grind_warning,
    send_session_summary,
    send_startup,
    send_tier1,
    send_tier2,
)
from conditions import c2_in_touch_zone, evaluate_tier1
from indicators import compute_atr, compute_rsi, compute_vwap, rsi_series, session_bars
from shared_state import AlertRecord, TickerSnapshot, state
from state import TickerState
from utils import is_market_open, load_watchlist, ms_to_et, now_et, today_str

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
        return None
    try:
        return snap["ticker"]["day"]["c"]
    except (KeyError, TypeError):
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

def _build_vwap_series(bars: list[dict]) -> list[float]:
    series = []
    cum_vol = 0.0
    cum_pv  = 0.0
    for b in bars:
        cum_vol += b["v"]
        cum_pv  += b["vw"] * b["v"]
        series.append(cum_pv / cum_vol if cum_vol else 0.0)
    return series


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
    # --- Real-time price + VWAP from snapshot (no delay) ---
    snap = fetch_snapshot(ticker)
    pv   = _price_and_vwap_from_snapshot(snap) if snap else None
    if pv is None:
        log.info("%s | Snapshot unavailable", ticker)
        return
    current_price, vwap = pv

    # --- Bars for ATR + RSI (may be delayed up to ~2 h on this API tier) ---
    raw_bars  = fetch_bars(ticker, timespan="minute", multiplier=5)
    # Use all returned bars (pre-market included) for indicator computation;
    # the aggregate endpoint has a delay so we can't filter to session only.
    indicator_bars = raw_bars if raw_bars else []

    if len(indicator_bars) < 3:
        log.info("%s | Too few bars for indicators (%d) — skipping", ticker, len(indicator_bars))
        return

    closed_bars = indicator_bars[:-1]
    atr = compute_atr(closed_bars, config.ATR_PERIOD)
    rsi = compute_rsi(closed_bars, config.RSI_PERIOD)

    # Build a synthetic vwap_series aligned to closed_bars for C1
    # (all values = session VWAP from snapshot so trend check uses real VWAP)
    vwap_series = [vwap] * len(closed_bars)

    if atr == 0:
        log.info("%s | ATR zero — skipping", ticker)
        return

    in_zone_now = c2_in_touch_zone(current_price, vwap, atr)

    if current_price > vwap and not in_zone_now:
        ticker_state.consecutive_above_vwap += 1

    if in_zone_now and not ticker_state.in_touch_zone:
        ticker_state.new_touch()
        touch_time = now_et().strftime("%H:%M:%S")
        log.info("%s | TOUCH #%d @ $%.2f at %s ET",
                 ticker, ticker_state.vwap_touch_count, current_price, touch_time)
        state.record_touch(ticker, ticker_state.vwap_touch_count, touch_time, current_price)
    elif not in_zone_now and ticker_state.in_touch_zone:
        ticker_state.in_touch_zone = False
        log.info("%s | Exited touch zone", ticker)

    # Grind warning
    if ticker_state.tier2_fired_for_touch and not ticker_state.grind_warning_fired:
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

    # Use snapshot for real-time price + VWAP
    snap = fetch_snapshot(ticker)
    pv   = _price_and_vwap_from_snapshot(snap) if snap else None
    if pv is None:
        return
    current_price, vwap = pv

    # Prior 1-min bar high from snapshot (min.h = high of the last complete minute bar)
    try:
        prior_bar_high = float(snap["ticker"]["min"]["h"])
    except (KeyError, TypeError):
        prior_bar_high = 0.0

    # ATR from whatever bars are available
    bars_5m   = fetch_bars(ticker, timespan="minute", multiplier=5)
    closed_5m = bars_5m[:-1] if len(bars_5m) > 1 else bars_5m

    if not closed_5m:
        return

    atr = compute_atr(closed_5m, config.ATR_PERIOD)

    if atr == 0:
        return

    in_zone = c2_in_touch_zone(current_price, vwap, atr)

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
# Scanner
# ---------------------------------------------------------------------------

class Scanner:
    def __init__(self) -> None:
        self.watchlist:    list[str] = []
        self.states:       dict[str, TickerState] = {}
        self.spy_snapshot: dict | None = None
        self.vix_value:    float | None = None
        self.alert_counts: dict[str, dict] = {}

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
        self.states[ticker] = TickerState(ticker=ticker)
        state.set_tickers(self.watchlist)
        log.info("Ticker added: %s", ticker)

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self.watchlist:
            return
        self.watchlist.remove(ticker)
        self.states.pop(ticker, None)
        state.set_tickers(self.watchlist)
        log.info("Ticker removed: %s", ticker)

    def setup(self) -> None:
        self.watchlist = load_watchlist(config.WATCHLIST_PATH)
        self.states    = {t: TickerState(ticker=t) for t in self.watchlist}
        state.set_tickers(self.watchlist)
        state.started_at = now_et().strftime("%Y-%m-%d %H:%M ET")
        log.info("Loaded %d tickers from watchlist", len(self.watchlist))

        send_startup(self.watchlist)
        self._refresh_spy()
        self._refresh_vix()

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        log.info("Scan loop started")
        while self._running:
            now   = now_et()
            today = now.strftime("%Y-%m-%d")

            if today != self._last_reset_date and now.time() >= __import__("utils").SESSION_OPEN:
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
                time.sleep(3600)
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
        for ticker in list(self.watchlist):
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
            t for t in self.watchlist
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
