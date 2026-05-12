"""
Entry point for the VWAP Pullback Scanner.
Handles startup, graceful shutdown on SIGTERM / KeyboardInterrupt.
"""

import logging
import signal
import sys

from utils import setup_logging

log = setup_logging()


def _build_scanner():
    # Import after logging is configured so module-level loggers inherit the handler
    from scanner import Scanner
    return Scanner()


def main() -> None:
    log.info("VWAP Pullback Scanner starting")

    scanner = _build_scanner()

    def _shutdown(signum, frame):
        log.info("Shutdown signal received (%s) — sending session summary…", signum)
        scanner.stop()
        scanner.send_shutdown_summary()
        log.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    try:
        scanner.setup()
        scanner.run()
    except KeyboardInterrupt:
        _shutdown("SIGINT", None)


if __name__ == "__main__":
    main()
