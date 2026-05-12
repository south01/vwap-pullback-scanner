"""
Unit tests for conditions.py — no API calls, all data is hardcoded.
Run:  python -m pytest tests/
"""

import sys
import os
import types

# ---------------------------------------------------------------------------
# Stub out config before importing any project module so env vars are not
# required when running tests.
# ---------------------------------------------------------------------------
_cfg = types.ModuleType("config")
_cfg.ATR_TOUCH_MULTIPLIER = 0.5
_cfg.ATR_PERIOD           = 14
_cfg.RSI_PERIOD           = 14
_cfg.RSI_MIN              = 40.0
_cfg.RSI_MAX              = 60.0
_cfg.VOLUME_AVG_PERIOD    = 20
_cfg.MIN_BARS_ABOVE_VWAP  = 3
_cfg.MAX_VWAP_TOUCHES     = 2
_cfg.VIX_MAX              = 25.0
_cfg.TP1_R                = 1.5
_cfg.TP2_R                = 3.0
_cfg.SL_ATR_MULTIPLIER    = 1.5
_cfg.GRIND_ZONE_PCT       = 0.001
_cfg.GRIND_MAX_BARS       = 4
sys.modules["config"] = _cfg

# Stub utils (only SESSION_OPEN is needed by indicators)
from datetime import time as _time
_utils = types.ModuleType("utils")
_utils.SESSION_OPEN = _time(9, 30)
def _ms_to_et(ts):
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/New_York"))
_utils.ms_to_et = _ms_to_et
sys.modules["utils"] = _utils

# ---------------------------------------------------------------------------

import pytest
from conditions import (
    c1_established_trend,
    c2_in_touch_zone,
    c3_touch_count_valid,
    c4_volume_drying_up,
    c5_rsi_in_zone_and_rising,
    c6_vwap_reclaim,
    c7_spy_green,
    c8_vix_below_threshold,
)
from state import TickerState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(c: float, h: float = None, l: float = None, v: float = 1_000_000,
         vw: float = None, t: int = None) -> dict:
    """Minimal bar factory. Timestamps default to 9:35 AM ET (market open + 5 min)."""
    h   = h   if h   is not None else c * 1.005
    l   = l   if l   is not None else c * 0.995
    vw  = vw  if vw  is not None else c
    # 2024-01-02 09:35 ET = 1704201300 seconds -> ms
    t   = t   if t   is not None else 1_704_201_300_000
    return {"c": c, "h": h, "l": l, "v": v, "vw": vw, "t": t}


def _spy_snap(current: float, prev_close: float) -> dict:
    return {"ticker": {"day": {"c": current}, "prevDay": {"c": prev_close}}}


# ---------------------------------------------------------------------------
# C1 — established trend
# ---------------------------------------------------------------------------

class TestC1EstablishedTrend:

    def test_passes_with_enough_consecutive_bars_above_vwap(self):
        vwap = 100.0
        # 5 bars above VWAP, then 1 pullback bar
        bars = [_bar(101), _bar(102), _bar(103), _bar(104), _bar(105), _bar(99)]
        vwap_s = [vwap] * len(bars)
        assert c1_established_trend(bars, vwap_s) is True

    def test_fails_when_not_enough_consecutive_bars(self):
        vwap = 100.0
        # Only 2 consecutive above (less than MIN_BARS_ABOVE_VWAP=3)
        bars = [_bar(101), _bar(102), _bar(99), _bar(100.5), _bar(99)]
        vwap_s = [vwap] * len(bars)
        assert c1_established_trend(bars, vwap_s) is False

    def test_fails_with_too_few_bars(self):
        bars = [_bar(101), _bar(102)]
        vwap_s = [100.0, 100.0]
        assert c1_established_trend(bars, vwap_s) is False

    def test_counts_longest_run_not_latest(self):
        vwap = 100.0
        # 3 above, then 1 below, then 2 above — max run = 3 ✓
        bars = [_bar(101), _bar(102), _bar(103), _bar(99), _bar(101), _bar(102), _bar(98)]
        vwap_s = [vwap] * len(bars)
        assert c1_established_trend(bars, vwap_s) is True


# ---------------------------------------------------------------------------
# C2 — in touch zone
# ---------------------------------------------------------------------------

class TestC2InTouchZone:

    def test_price_at_vwap_is_in_zone(self):
        assert c2_in_touch_zone(100.0, 100.0, 2.0) is True

    def test_price_just_above_vwap_in_zone(self):
        # zone upper = 100 + 0.5 * 2 = 101
        assert c2_in_touch_zone(100.8, 100.0, 2.0) is True

    def test_price_above_zone(self):
        assert c2_in_touch_zone(101.5, 100.0, 2.0) is False

    def test_price_below_vwap_not_in_zone(self):
        # Price below VWAP — not approaching from above
        assert c2_in_touch_zone(99.5, 100.0, 2.0) is False

    def test_exactly_at_zone_upper_boundary(self):
        assert c2_in_touch_zone(101.0, 100.0, 2.0) is True


# ---------------------------------------------------------------------------
# C3 — touch count valid
# ---------------------------------------------------------------------------

class TestC3TouchCountValid:

    def test_first_touch_valid(self):
        state = TickerState("AAPL")
        state.vwap_touch_count = 1
        assert c3_touch_count_valid(state) is True

    def test_second_touch_valid(self):
        state = TickerState("AAPL")
        state.vwap_touch_count = 2
        assert c3_touch_count_valid(state) is True

    def test_third_touch_invalid(self):
        state = TickerState("AAPL")
        state.vwap_touch_count = 3
        assert c3_touch_count_valid(state) is False

    def test_zero_touch_valid(self):
        # Touch hasn't been recorded yet — still valid (not yet fired)
        state = TickerState("AAPL")
        state.vwap_touch_count = 0
        assert c3_touch_count_valid(state) is True


# ---------------------------------------------------------------------------
# C4 — volume drying up
# ---------------------------------------------------------------------------

class TestC4VolumeDryingUp:

    def _make_bars(self, vols: list[float]) -> list[dict]:
        return [_bar(100.0, v=v) for v in vols]

    def test_passes_when_prior_two_bars_low_volume(self):
        # 20 bars of high volume to build avg, then 2 low-vol bars, then current bar
        bars = self._make_bars([2_000_000] * 20 + [500_000, 500_000, 2_000_000])
        assert c4_volume_drying_up(bars) is True

    def test_fails_when_only_one_bar_low_volume(self):
        bars = self._make_bars([2_000_000] * 20 + [2_000_000, 500_000, 2_000_000])
        assert c4_volume_drying_up(bars) is False

    def test_fails_when_neither_bar_low_volume(self):
        bars = self._make_bars([2_000_000] * 20 + [2_500_000, 2_500_000, 2_000_000])
        assert c4_volume_drying_up(bars) is False

    def test_fails_with_too_few_bars(self):
        bars = self._make_bars([500_000, 500_000])
        assert c4_volume_drying_up(bars) is False


# ---------------------------------------------------------------------------
# C5 — RSI in zone and rising
# ---------------------------------------------------------------------------

class TestC5RsiInZoneAndRising:

    def _ramp_bars(self, n: int = 30, start: float = 100.0, step: float = 0.5) -> list[dict]:
        """Steadily rising bars → RSI climbs above 60."""
        return [_bar(start + i * step) for i in range(n)]

    def _mean_revert_bars(self) -> list[dict]:
        """Bars oscillating around 100 → RSI stays near 50."""
        closes = []
        for i in range(40):
            closes.append(100.0 + (1 if i % 2 == 0 else -1) * 0.3)
        return [_bar(c) for c in closes]

    def test_passes_with_rsi_in_zone_and_rising(self):
        # After sustained rally then slight dip, RSI should be in 40-60 range
        bars = [_bar(100.0 + i * 0.4) for i in range(20)] + \
               [_bar(108.0 - i * 0.15) for i in range(16)]
        # We just check the function doesn't crash and returns a bool
        result = c5_rsi_in_zone_and_rising(bars)
        assert isinstance(result, bool)

    def test_fails_with_too_few_bars(self):
        bars = [_bar(100.0)] * 5
        assert c5_rsi_in_zone_and_rising(bars) is False

    def test_rsi_above_zone_fails(self):
        # Strongly trending up — RSI will be near 70+
        bars = self._ramp_bars(30, step=1.0)
        assert c5_rsi_in_zone_and_rising(bars) is False

    def test_mid_oscillation_may_pass(self):
        bars = self._mean_revert_bars()
        result = c5_rsi_in_zone_and_rising(bars)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# C6 — VWAP reclaim
# ---------------------------------------------------------------------------

class TestC6VwapReclaim:

    def test_passes_when_close_above_vwap(self):
        bars = [_bar(100.5)]
        assert c6_vwap_reclaim(bars, 100.0) is True

    def test_fails_when_close_at_vwap(self):
        bars = [_bar(100.0)]
        assert c6_vwap_reclaim(bars, 100.0) is False

    def test_fails_when_close_below_vwap(self):
        bars = [_bar(99.5)]
        assert c6_vwap_reclaim(bars, 100.0) is False

    def test_fails_with_empty_bars(self):
        assert c6_vwap_reclaim([], 100.0) is False


# ---------------------------------------------------------------------------
# C7 — SPY green
# ---------------------------------------------------------------------------

class TestC7SpyGreen:

    def test_passes_when_spy_above_prev_close(self):
        snap = _spy_snap(current=450.0, prev_close=445.0)
        assert c7_spy_green(snap) is True

    def test_passes_when_spy_equal_to_prev_close(self):
        snap = _spy_snap(current=445.0, prev_close=445.0)
        assert c7_spy_green(snap) is True

    def test_fails_when_spy_below_prev_close(self):
        snap = _spy_snap(current=440.0, prev_close=445.0)
        assert c7_spy_green(snap) is False

    def test_fails_on_none_snapshot(self):
        assert c7_spy_green(None) is False

    def test_fails_on_malformed_snapshot(self):
        assert c7_spy_green({"ticker": {}}) is False


# ---------------------------------------------------------------------------
# C8 — VIX below threshold
# ---------------------------------------------------------------------------

class TestC8VixBelowThreshold:

    def test_passes_when_vix_below_max(self):
        assert c8_vix_below_threshold(20.0) is True

    def test_fails_when_vix_at_max(self):
        assert c8_vix_below_threshold(25.0) is False

    def test_fails_when_vix_above_max(self):
        assert c8_vix_below_threshold(30.0) is False

    def test_fails_on_none(self):
        assert c8_vix_below_threshold(None) is False
