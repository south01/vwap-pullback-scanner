from dataclasses import dataclass, field


@dataclass
class TickerState:
    ticker: str
    vwap_touch_count: int = 0
    in_touch_zone: bool = False
    tier1_fired_for_touch: bool = False
    tier2_fired_for_touch: bool = False
    last_1min_high: float = 0.0
    consecutive_above_vwap: int = 0
    grind_bar_count: int = 0
    grind_warning_fired: bool = False
    last_bar_ts: int = 0

    def reset(self) -> None:
        """Reset all per-session state. Called at 9:30 AM ET each day."""
        self.vwap_touch_count = 0
        self.in_touch_zone = False
        self.tier1_fired_for_touch = False
        self.tier2_fired_for_touch = False
        self.last_1min_high = 0.0
        self.consecutive_above_vwap = 0
        self.grind_bar_count = 0
        self.grind_warning_fired = False
        self.last_bar_ts = 0

    def new_touch(self) -> None:
        """Advance touch counter and clear per-touch flags."""
        self.vwap_touch_count += 1
        self.in_touch_zone = True
        self.tier1_fired_for_touch = False
        self.tier2_fired_for_touch = False
        self.grind_bar_count = 0
        self.grind_warning_fired = False
