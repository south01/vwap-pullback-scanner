"""Sentiment signal — returns score 0-100.

Tries Polygon /v2/reference/news for article count, recency, and
per-ticker sentiment insights. Falls back to price-change proxy.
"""

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("vwap_scanner")


def score(ticker: str, engine) -> float:
    try:
        return _compute(ticker, engine)
    except Exception as exc:
        log.warning("sentiment.score(%s) error: %s", ticker, exc)
        return 50.0


def _compute(ticker: str, engine) -> float:
    from utils import now_et

    now  = now_et()
    since = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    data = engine._fetch("/v2/reference/news", {
        "ticker": ticker, "published_gte": since, "limit": 10, "order": "desc",
    })

    if data and "results" in data:
        return _from_news(data["results"], ticker, now)

    # Fallback: price-change proxy
    return _price_proxy(ticker, engine)


def _from_news(articles: list, ticker: str, now) -> float:
    n = len(articles)
    if n == 0:
        return 30.0

    count_s = min(100.0, n * 15.0)  # 7+ articles → 100

    # Recency of most recent article
    recency_s = 50.0
    try:
        pub = articles[0].get("published_utc", "")
        if pub:
            pub_dt    = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            hours_ago = (now.replace(tzinfo=None) - pub_dt.replace(tzinfo=None)).total_seconds() / 3600
            recency_s = max(0.0, 100.0 - hours_ago * 5)
    except Exception:
        pass

    # Per-ticker sentiment insights
    vals = []
    for art in articles[:5]:
        for insight in art.get("insights", []):
            if insight.get("ticker") == ticker:
                s = insight.get("sentiment", "")
                if s == "positive":
                    vals.append(80.0)
                elif s == "negative":
                    vals.append(20.0)
                elif s == "neutral":
                    vals.append(50.0)
    sentiment_s = sum(vals) / len(vals) if vals else 55.0

    return round(count_s * 0.40 + recency_s * 0.30 + sentiment_s * 0.30, 1)


def _price_proxy(ticker: str, engine) -> float:
    try:
        snap = engine._fetch_snapshot(ticker)
        if not snap:
            return 50.0
        day  = snap["ticker"]["day"]
        prev = snap["ticker"]["prevDay"]
        chg  = (float(day["c"]) - float(prev["c"])) / float(prev["c"]) * 100
        # +10% → 90, -10% → 10, linear
        return float(min(90.0, max(10.0, 50.0 + chg * 4)))
    except Exception:
        return 50.0
