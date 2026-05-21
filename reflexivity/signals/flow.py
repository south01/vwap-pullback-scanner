from __future__ import annotations

"""Institutional / options flow signal — returns score 0-100.

Tries Polygon /v3/snapshot/options for call-vs-put volume bias.
Falls back to OBV-based intraday flow proxy.
"""

import logging

log = logging.getLogger("vwap_scanner")


def score(ticker: str, engine) -> float:
    try:
        return _compute(ticker, engine)
    except Exception as exc:
        log.warning("flow.score(%s) error: %s", ticker, exc)
        return 50.0


def _compute(ticker: str, engine) -> float:
    opt = _options_flow(ticker, engine)
    if opt is not None:
        return opt
    return _obv_flow(ticker, engine)


def _options_flow(ticker: str, engine) -> float | None:
    data = engine._fetch(f"/v3/snapshot/options/{ticker}", {
        "limit": 50, "order": "desc", "sort": "open_interest",
    })
    if not data or "results" not in data:
        return None

    calls_v = 0
    puts_v  = 0
    for opt in data["results"]:
        ct  = opt.get("details", {}).get("contract_type", "")
        vol = opt.get("day", {}).get("volume", 0) or 0
        if ct == "call":
            calls_v += vol
        elif ct == "put":
            puts_v += vol

    total = calls_v + puts_v
    if total == 0:
        return None

    # 80 % calls → 74, 50/50 → 50, 80 % puts → 18
    call_ratio = calls_v / total
    return round(call_ratio * 80.0 + 10.0, 1)


def _obv_flow(ticker: str, engine) -> float:
    from utils import today_str

    today = today_str()
    bars  = engine._fetch_bars(ticker, "minute", 5, today, today)
    if not bars or len(bars) < 3:
        return 50.0

    obv       = 0.0
    total_vol = sum(b["v"] for b in bars)
    for i in range(1, len(bars)):
        if bars[i]["c"] > bars[i - 1]["c"]:
            obv += bars[i]["v"]
        elif bars[i]["c"] < bars[i - 1]["c"]:
            obv -= bars[i]["v"]

    if total_vol == 0:
        return 50.0

    obv_ratio = obv / total_vol  # -1 to +1
    return round((obv_ratio + 1) / 2 * 100, 1)
