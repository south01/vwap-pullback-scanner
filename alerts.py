"""
Telegram alert builders and senders.
"""

import logging
import time

import requests

import config
from utils import now_et

log = logging.getLogger("vwap_scanner")

_TELEGRAM_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"


def _send(text: str, retries: int = 3) -> bool:
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(_TELEGRAM_URL, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("Telegram send attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(2)
    return False


def send_startup(
    active: list[str],
    removed: list[tuple[str, float]] | None = None,
) -> None:
    date_str = now_et().strftime("%Y-%m-%d")
    if len(active) <= 10:
        tickers_str = ", ".join(active)
    else:
        tickers_str = ", ".join(active[:10]) + f" ... (+{len(active) - 10} more)"
    lines = [
        f"<b>📋 SCANNER ACTIVE — {date_str}</b>",
        f"✅ Scanning: {tickers_str} ({len(active)} tickers)",
    ]
    if removed:
        removed_str = ", ".join(f"{t} (low volume)" for t, _ in removed)
        lines.append(f"🚫 Removed: {removed_str}")
    lines.append(f"Polling every {config.POLL_INTERVAL_SEC}s | Session: 9:30–15:55 ET")
    _send("\n".join(lines))


def send_volume_removal(ticker: str, avg_vol: float) -> None:
    _send(
        f"<b>🚫 WATCHLIST REMOVAL</b>\n"
        f"{ticker} dropped for this session\n"
        f"Reason: Avg 5-min volume {avg_vol:.0f} &lt; minimum {config.MIN_AVG_VOLUME}\n"
        f"(Based on last {config.VOLUME_LOOKBACK_DAYS} trading days)"
    )


def send_tier1(
    ticker: str,
    price: float,
    vwap: float,
    atr: float,
    touch_count: int,
    rsi: float,
    sl: float,
    spy_chg_pct: float,
    vix: float | None,
) -> bool:
    ts = now_et().strftime("%H:%M:%S ET")
    vix_display = f"{vix:.1f}" if vix is not None else "N/A"
    text = (
        f"<b>⚡ SETUP FORMING</b>\n"
        f"{ticker} | ${price:.2f} | {ts}\n"
        f"VWAP: ${vwap:.2f} | Touch #{touch_count}\n"
        f"RSI: {rsi:.1f} ↑ | ATR: ${atr:.2f}\n"
        f"Est. SL: ${sl:.2f}\n"
        f"SPY: {spy_chg_pct:+.2f}% | VIX: {vix_display}"
    )
    sent = _send(text)
    if sent:
        log.info("%s | Tier 1 alert sent", ticker)
    return sent


def send_tier2(
    ticker: str,
    price: float,
    vwap: float,
    atr: float,
    sl: float,
    tp1: float,
    tp2: float,
    r1: float,
    r2: float,
) -> bool:
    ts = now_et().strftime("%H:%M:%S ET")
    risk = price - sl
    text = (
        f"<b>🟢 ENTRY SIGNAL</b>\n"
        f"{ticker} | ${price:.2f} | {ts}\n"
        f"SL:  ${sl:.2f}\n"
        f"TP1: ${tp1:.2f} (+{r1:.1f}R)\n"
        f"TP2: ${tp2:.2f} (+{r2:.1f}R)\n"
        f"Risk/share: ${risk:.2f}"
    )
    sent = _send(text)
    if sent:
        log.info("%s | Tier 2 alert sent", ticker)
    return sent


def send_grind_warning(ticker: str, n_bars: int) -> bool:
    ts = now_et().strftime("%H:%M:%S ET")
    text = (
        f"<b>⚠️ VWAP GRIND — {ticker}</b> | {ts}\n"
        f"Stuck near VWAP for {n_bars} bars\n"
        f"Consider exiting flat — momentum stalled"
    )
    sent = _send(text)
    if sent:
        log.info("%s | Grind warning sent", ticker)
    return sent


def send_session_summary(
    tier1_total: int,
    tier2_total: int,
    breakdown: dict[str, dict],
    et_time_str: str,
) -> None:
    lines = [f"<b>📊 Session Summary — {et_time_str}</b>"]
    lines.append(f"Tier 1 alerts: {tier1_total}")
    lines.append(f"Tier 2 alerts: {tier2_total}")
    if breakdown:
        lines.append("")
        for ticker, counts in sorted(breakdown.items()):
            t1 = counts.get("tier1", 0)
            t2 = counts.get("tier2", 0)
            if t1 or t2:
                lines.append(f"  {ticker}: T1={t1} T2={t2}")
    _send("\n".join(lines))


def compute_targets(
    entry: float,
    atr: float,
) -> tuple[float, float, float]:
    """Return (sl, tp1, tp2) given entry price and ATR."""
    sl  = entry - config.SL_ATR_MULTIPLIER * atr
    risk = entry - sl
    tp1 = entry + config.TP1_R * risk
    tp2 = entry + config.TP2_R * risk
    return sl, tp1, tp2
