"""
Telegram alert builders and senders.
"""

import logging
import time

import requests

import config

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


def send_startup(watchlist: list[str]) -> None:
    tickers = ", ".join(watchlist[:10])
    suffix = f" (+{len(watchlist) - 10} more)" if len(watchlist) > 10 else ""
    _send(
        f"<b>🚀 VWAP Scanner online</b>\n"
        f"Watching {len(watchlist)} tickers: {tickers}{suffix}\n"
        f"VIX max: {config.VIX_MAX} | Touch max: {config.MAX_VWAP_TOUCHES}"
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
    vix: float,
) -> bool:
    text = (
        f"<b>⚡ SETUP FORMING</b>\n"
        f"{ticker} | ${price:.2f}\n"
        f"VWAP: ${vwap:.2f} | Touch #{touch_count}\n"
        f"RSI: {rsi:.1f} ↑ | ATR: ${atr:.2f}\n"
        f"Est. SL: ${sl:.2f}\n"
        f"SPY: {spy_chg_pct:+.2f}% | VIX: {vix:.1f}"
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
    risk = price - sl
    text = (
        f"<b>🟢 ENTRY SIGNAL</b>\n"
        f"{ticker} | ${price:.2f}\n"
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
    text = (
        f"<b>⚠️ VWAP GRIND — {ticker}</b>\n"
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
