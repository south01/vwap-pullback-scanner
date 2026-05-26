"""
Detect potential loop exhaustion / exit signals.

Returns True when the current reading suggests the loop is about to reverse:
- Composite was >= 65 but dropped below 50 (peak-to-trough fade)
- Three consecutive declining composite readings, starting from >= 60
- Momentum extremely overbought (>= 88) while volume starts to dry up (< 40)
"""

import logging
import threading

log = logging.getLogger("vwap_scanner")

_MAX_HIST = 5


class ExitDetector:
    def __init__(self) -> None:
        self._history: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def detect(self, symbol: str, composite: float, momentum: float, volume: float) -> bool:
        with self._lock:
            hist = self._history.setdefault(symbol, [])
            hist.append(composite)
            if len(hist) > _MAX_HIST:
                hist.pop(0)

            if len(hist) >= 3:
                prev_high = max(hist[:-1])
                if prev_high >= 65 and composite < 50:
                    log.info("%s | exit signal: composite %.0f → %.0f", symbol, prev_high, composite)
                    return True

                if hist[-1] < hist[-2] < hist[-3] and hist[-3] >= 60:
                    return True

        if momentum >= 88 and volume < 40:
            return True

        return False


# Module-level instance for backward compatibility
_detector = ExitDetector()


def detect_exit(symbol: str, composite: float, momentum: float, volume: float) -> bool:
    return _detector.detect(symbol, composite, momentum, volume)
