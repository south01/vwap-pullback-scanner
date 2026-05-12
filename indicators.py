"""
Pure-Python indicator calculations.
All functions operate on lists of bar dicts with keys: o, h, l, c, v, vw, t.
"""

from utils import ms_to_et, SESSION_OPEN


def session_bars(bars: list[dict]) -> list[dict]:
    """Return only bars whose ET open time is at or after 9:30 AM."""
    return [b for b in bars if ms_to_et(b["t"]).time() >= SESSION_OPEN]


def compute_vwap(bars: list[dict]) -> float:
    """Session-anchored VWAP from a list of bars (each must have vw and v)."""
    total_vol = sum(b["v"] for b in bars)
    if total_vol == 0:
        return 0.0
    return sum(b["vw"] * b["v"] for b in bars) / total_vol


def compute_atr(bars: list[dict], period: int = 14) -> float:
    """Wilder ATR over `period` bars. Returns 0 if insufficient data."""
    if len(bars) < 2:
        return 0.0

    trs: list[float] = []
    for i in range(1, len(bars)):
        high  = bars[i]["h"]
        low   = bars[i]["l"]
        prev_close = bars[i - 1]["c"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0

    # Wilder smoothing
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_rsi(bars: list[dict], period: int = 14) -> float:
    """RSI using Wilder smoothing. Returns 50.0 if insufficient data."""
    closes = [b["c"] for b in bars]
    if len(closes) < period + 1:
        return 50.0

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_series(bars: list[dict], period: int = 14) -> list[float]:
    """Return RSI value for every bar once enough history exists, else 50.0."""
    result: list[float] = [50.0] * len(bars)
    closes = [b["c"] for b in bars]

    if len(closes) < period + 1:
        return result

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + ag / al))

    result[period] = _rsi(avg_gain, avg_loss)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result[i + 1] = _rsi(avg_gain, avg_loss)

    return result


def volume_moving_avg(bars: list[dict], period: int = 20) -> list[float]:
    """Simple moving average of volume for each bar position."""
    vols = [b["v"] for b in bars]
    result: list[float] = [0.0] * len(bars)
    for i in range(len(bars)):
        window = vols[max(0, i - period + 1): i + 1]
        result[i] = sum(window) / len(window)
    return result
