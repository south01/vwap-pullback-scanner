import logging
import sys
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(15, 55)   # stop scanning at 3:55 PM ET
SESSION_OPEN = time(9, 30)


def now_et() -> datetime:
    return datetime.now(ET)


def ms_to_et(ts_ms: int) -> datetime:
    """Convert a Unix-millisecond timestamp (UTC) to a tz-aware ET datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(ET)


def is_market_open() -> bool:
    t = now_et().time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def today_str() -> str:
    return now_et().strftime("%Y-%m-%d")


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("vwap_scanner")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ETFormatter("[%(asctime_et)s] %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


class _ETFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.asctime_et = now_et().strftime("%Y-%m-%d %H:%M:%S ET")
        return super().format(record)


def load_watchlist(path: str) -> list[str]:
    with open(path) as fh:
        tickers = [line.strip().upper() for line in fh if line.strip() and not line.startswith("#")]
    return tickers
