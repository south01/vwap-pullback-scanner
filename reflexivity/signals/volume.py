"""Volume acceleration signal — returns score 0-100.

Composite of:
- Latest bar relative volume vs intraday average
- Volume trend (recent bars vs prior bars)
- Snapshot relative volume (today vs prev day)
"""

import logging

log = logging.getLogger("vwap_scanner")


def score(ticker: str, engine) -> float:
    try:
        return _compute(ticker, engine)
    except Exception as exc:
        log.warning("volume.score(%s) error: %s", ticker, exc)
        return 0.0


def _compute(ticker: str, engine) -> float:
    from utils import today_str

    today = today_str()
    bars  = engine._fetch_bars(ticker, "minute", 5, today, today)
    if not bars or len(bars) < 3:
        return 0.0

    vols    = [b["v"] for b in bars]
    avg_vol = sum(vols) / len(vols)

    # Latest bar vs intraday average
    rel_vol = vols[-1] / avg_vol if avg_vol > 0 else 1.0

    # Volume trend: recent 3 bars vs prior 3 bars
    if len(vols) >= 6:
        recent_avg = sum(vols[-3:]) / 3
        prior_avg  = sum(vols[-6:-3]) / 3
        vol_trend  = recent_avg / prior_avg if prior_avg > 0 else 1.0
    else:
        vol_trend = 1.0

    # Snapshot: today total volume vs prev day total
    snap_rvol = 1.0
    try:
        snap = engine._fetch_snapshot(ticker)
        if snap:
            day_v  = float(snap["ticker"]["day"].get("v", 0))
            prev_v = float(snap["ticker"]["prevDay"].get("v", 0))
            if prev_v > 0:
                snap_rvol = day_v / prev_v
    except Exception:
        pass

    # Scores: 1× = 25, 3× = 100
    bar_s   = min(100.0, max(0.0, (rel_vol   - 1.0) * 37.5 + 25))
    trend_s = min(100.0, max(0.0, (vol_trend - 1.0) / 1.0 * 50 + 50))
    snap_s  = min(100.0, max(0.0, (snap_rvol - 1.0) * 37.5 + 25))

    return round(bar_s * 0.40 + trend_s * 0.30 + snap_s * 0.30, 1)
