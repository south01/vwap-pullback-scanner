"""
Entry condition functions — each returns bool.
All bar-based conditions operate on CLOSED bars only.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import config
from indicators import compute_rsi, rsi_series, volume_moving_avg

if TYPE_CHECKING:
    from state import TickerState


def c1_established_trend(bars: list[dict], vwap_series: list[float]) -> bool:
    """
    At least MIN_BARS_ABOVE_VWAP consecutive closed bars with close > VWAP
    must appear earlier in the session (not counting the current touch bar).
    """
    if len(bars) < config.MIN_BARS_ABOVE_VWAP:
        return False

    # Walk through all pairs except the very last bar (which may be the pullback bar)
    check_bars = bars[:-1]
    check_vwap = vwap_series[:-1]

    max_consecutive = 0
    run = 0
    for bar, vw in zip(check_bars, check_vwap):
        if bar["c"] > vw:
            run += 1
            max_consecutive = max(max_consecutive, run)
        else:
            run = 0

    return max_consecutive >= config.MIN_BARS_ABOVE_VWAP


def c2_in_touch_zone(current_price: float, vwap: float, atr: float) -> bool:
    """Price within ATR_TOUCH_MULTIPLIER * ATR above VWAP (approaching from above)."""
    zone = config.ATR_TOUCH_MULTIPLIER * atr
    return vwap <= current_price <= vwap + zone


def c3_touch_count_valid(state: "TickerState") -> bool:
    """Touch count must not exceed MAX_VWAP_TOUCHES (1-indexed after increment)."""
    return state.vwap_touch_count <= config.MAX_VWAP_TOUCHES


def c4_volume_drying_up(bars: list[dict], avg_period: int = None) -> bool:
    """
    The prior TWO closed bars both have volume below the rolling average.
    Requires at least avg_period + 2 bars for a meaningful average.
    """
    period = avg_period if avg_period is not None else config.VOLUME_AVG_PERIOD
    if len(bars) < 3:
        return False

    vol_avg = volume_moving_avg(bars, period)
    # Check the two bars immediately before the current (last) bar
    for idx in [-3, -2]:
        if bars[idx]["v"] >= vol_avg[idx]:
            return False
    return True


def c5_rsi_in_zone_and_rising(bars: list[dict], period: int = None) -> bool:
    """
    RSI is between RSI_MIN and RSI_MAX AND the current RSI is higher than one bar ago.
    """
    p = period if period is not None else config.RSI_PERIOD
    if len(bars) < p + 2:
        return False

    series = rsi_series(bars, p)
    rsi_now  = series[-1]
    rsi_prev = series[-2]

    return (config.RSI_MIN <= rsi_now <= config.RSI_MAX) and (rsi_now > rsi_prev)


def c6_vwap_reclaim(bars: list[dict], vwap: float) -> bool:
    """Most recent closed bar has close above VWAP (the reclaim candle)."""
    if not bars:
        return False
    return bars[-1]["c"] > vwap


def c7_spy_green(spy_snapshot: dict | None) -> bool:
    """SPY current price is at or above its previous close."""
    if spy_snapshot is None:
        return False
    try:
        day = spy_snapshot["ticker"]["day"]
        prev = spy_snapshot["ticker"]["prevDay"]
        return day["c"] >= prev["c"]
    except (KeyError, TypeError):
        return False


def c8_vix_below_threshold(vix_value: float | None) -> bool:
    """VIX is below VIX_MAX."""
    if vix_value is None:
        return False
    return vix_value < config.VIX_MAX


def evaluate_tier1(
    bars: list[dict],
    vwap_series: list[float],
    current_price: float,
    vwap: float,
    atr: float,
    state: "TickerState",
    spy_snapshot: dict | None,
    vix_value: float | None,
) -> tuple[bool, dict[str, bool]]:
    """
    Evaluate all Tier 1 conditions (C1–C5, C7, C8).
    Returns (all_pass, per_condition_results).
    """
    results = {
        "C1": c1_established_trend(bars, vwap_series),
        "C2": c2_in_touch_zone(current_price, vwap, atr),
        "C3": c3_touch_count_valid(state),
        "C4": c4_volume_drying_up(bars),
        "C5": c5_rsi_in_zone_and_rising(bars),
        "C7": True,  # SPY check disabled
        "C8": True,  # VIX check disabled
    }
    return all(results.values()), results
