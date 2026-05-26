"""
Thread-safe singleton shared between the scanner loop, Flask dashboard,
and the Telegram command handler.
"""

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TickerSnapshot:
    price: float = 0.0
    vwap: float = 0.0
    atr: float = 0.0
    rsi: float = 0.0
    touch_count: int = 0
    conditions: dict = field(default_factory=dict)   # {"C1": True, "C2": False, …}
    tier1_fired: bool = False
    tier2_fired: bool = False
    last_updated: str = "—"


@dataclass
class AlertRecord:
    time: str
    ticker: str
    tier: int
    price: float
    vwap: float
    details: str = ""


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Scanner lifecycle
        self.started_at: str = "—"
        self.last_scan_time: str = "—"
        self.scan_count: int = 0
        self.is_paused: bool = False
        self.is_market_open: bool = False

        # Watchlist (authoritative in-memory list)
        self.tickers: list[str] = []

        # Per-ticker data updated after every scan
        self.ticker_status: dict[str, TickerSnapshot] = {}

        # Touch history: ticker -> [{touch#, time, price}, ...]
        self.touch_history: dict[str, list[dict]] = {}

        # Macro
        self.spy_chg: float = 0.0
        self.vix: float = 0.0

        # Alert log
        self.alerts_today: list[AlertRecord] = []
        self.tier1_count: int = 0
        self.tier2_count: int = 0

    # ------------------------------------------------------------------
    # Writers (called from scanner / command handler threads)
    # ------------------------------------------------------------------

    def set_tickers(self, tickers: list[str]) -> None:
        with self._lock:
            self.tickers = list(tickers)
            for t in tickers:
                if t not in self.ticker_status:
                    self.ticker_status[t] = TickerSnapshot()

    def update_ticker(self, ticker: str, snap: TickerSnapshot) -> None:
        with self._lock:
            self.ticker_status[ticker] = snap

    def record_touch(self, ticker: str, touch_num: int, time: str, price: float) -> None:
        with self._lock:
            self.touch_history.setdefault(ticker, []).append({
                "num": touch_num,
                "time": time,
                "price": price,
            })

    def record_alert(self, rec: AlertRecord) -> None:
        with self._lock:
            self.alerts_today.append(rec)
            if rec.tier == 1:
                self.tier1_count += 1
            else:
                self.tier2_count += 1

    def set_scan_time(self, t: str) -> None:
        with self._lock:
            self.last_scan_time = t
            self.scan_count += 1

    def set_macro(self, spy_chg: float, vix: float) -> None:
        with self._lock:
            self.spy_chg = spy_chg
            self.vix = vix

    def set_paused(self, val: bool) -> None:
        with self._lock:
            self.is_paused = val

    def set_market_open(self, val: bool) -> None:
        with self._lock:
            self.is_market_open = val

    def reset_day(self) -> None:
        with self._lock:
            self.alerts_today = []
            self.tier1_count = 0
            self.tier2_count = 0
            self.scan_count = 0
            self.last_scan_time = "—"
            self.touch_history = {}
            for snap in self.ticker_status.values():
                snap.tier1_fired = False
                snap.tier2_fired = False
                snap.touch_count = 0
                snap.conditions = {}

    # ------------------------------------------------------------------
    # Reader — returns a plain dict (no lock held by caller)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "started_at":    self.started_at,
                "last_scan_time": self.last_scan_time,
                "scan_count":    self.scan_count,
                "is_paused":     self.is_paused,
                "is_market_open": self.is_market_open,
                "tickers":       list(self.tickers),
                "ticker_status": {k: vars(v) for k, v in self.ticker_status.items()},
                "spy_chg":       self.spy_chg,
                "vix":           self.vix,
                "touch_history":  dict(self.touch_history),
            "alerts_today":  [vars(a) for a in self.alerts_today],
                "tier1_count":   self.tier1_count,
                "tier2_count":   self.tier2_count,
            }


# Module-level singleton — import this everywhere
state = SharedState()
