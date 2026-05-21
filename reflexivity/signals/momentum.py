"""Price momentum signal — returns score 0-100.

Composite of:
- Intraday return (open → current)
- 1-day return
- 5-day return
- RSI position
"""

import logging
from datetime import timedelta

log = logging.getLogger("vwap_scanner")


def score(ticker: str, engine) -> float:
    try:
        return _compute(ticker, engine)
    except Exception as exc:
        log.warning("momentum.score(%s) error: %s", ticker, exc)
        return 0.0


def _compute(ticker: str, engine) -> float:
    from utils import now_et, today_str

    snap = engine._fetch_snapshot(ticker)
    if not snap:
        return 0.0

    try:
        t       = snap["ticker"]
        day     = t["day"]
        prev    = t["prevDay"]
        current = float(t["min"]["c"]) if t.get("min") else float(day.get("c", 0))

        open_px   = float(day.get("o", current))
        prev_c    = float(prev.get("c", current))
        intraday  = (current - open_px) / open_px * 100 if open_px else 0.0
        one_day   = (current - prev_c)  / prev_c  * 100 if prev_c  else 0.0
    except (KeyError, TypeError, ZeroDivisionError):
        return 0.0

    # 5-day return via daily bars
    five_day = 0.0
    try:
        now   = now_et()
        start = (now - timedelta(days=12)).strftime("%Y-%m-%d")
        end   = now.strftime("%Y-%m-%d")
        bars  = engine._fetch_bars(ticker, "day", 1, start, end)
        if len(bars) >= 5:
            five_day = (bars[-1]["c"] - bars[-5]["c"]) / bars[-5]["c"] * 100
    except Exception:
        pass

    # RSI from today's intraday 5-min bars
    rsi = 50.0
    try:
        from indicators import compute_rsi
        today  = today_str()
        ibars  = engine._fetch_bars(ticker, "minute", 5, today, today)
        if len(ibars) >= 15:
            rsi = compute_rsi(ibars[:-1], 14)
    except Exception:
        pass

    # Normalise each component to 0-100
    intraday_s = min(100.0, max(0.0, (intraday + 5)  / 10  * 100))
    one_day_s  = min(100.0, max(0.0, (one_day  + 10) / 20  * 100))
    five_day_s = min(100.0, max(0.0, (five_day + 20) / 40  * 100))
    rsi_s      = min(100.0, max(0.0, (rsi - 30)      / 40  * 100))

    return round(intraday_s * 0.40 + one_day_s * 0.25 + five_day_s * 0.20 + rsi_s * 0.15, 1)
