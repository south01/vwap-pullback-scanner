from __future__ import annotations

"""News catalyst recency and acceleration signal — returns score 0-100."""

import logging
from datetime import timedelta

log = logging.getLogger("vwap_scanner")


def score(ticker: str, engine) -> float:
    try:
        return _compute(ticker, engine)
    except Exception as exc:
        log.warning("catalyst.score(%s) error: %s", ticker, exc)
        return 0.0


def _compute(ticker: str, engine) -> float:
    from utils import now_et

    now   = now_et()
    s_72h = (now - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_24h = (now - timedelta(hours=24)).isoformat()

    data = engine._fetch("/v2/reference/news", {
        "ticker": ticker, "published_utc.gte": s_72h, "limit": 50,
    })
    if not data:
        return 0.0

    articles = data.get("results", [])
    n72 = len(articles)
    n24 = sum(1 for a in articles if a.get("published_utc", "") >= cutoff_24h)

    # 5+ articles in last 24h → 100
    count_s = min(100.0, n24 * 20.0)

    # Acceleration: if most activity is in last 24h vs prior 48h
    accel_s = 50.0
    if n72 > 0:
        ratio   = (n24 / n72) * 3.0   # expected pace = 1/3 in any 24h window
        accel_s = min(100.0, ratio * 50.0)

    return round(count_s * 0.60 + accel_s * 0.40, 1)
