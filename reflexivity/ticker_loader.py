"""
Load the reflexivity watchlist from (in priority order):
  1. REFLEXIVITY_TICKERS env var (comma-separated override)
  2. Main watchlist.txt (same list as the VWAP scanner)
  3. Built-in defaults (SOFI, CIFR, POET, CRML)
"""

import logging
import os

log = logging.getLogger("vwap_scanner")

_DEFAULTS = ["SOFI", "CIFR", "POET", "CRML"]


def load_tickers() -> list[str]:
    # Optional env-var override for a custom subset
    env_val = os.environ.get("REFLEXIVITY_TICKERS", "").strip()
    if env_val:
        tickers = [t.strip().upper() for t in env_val.split(",") if t.strip()]
        if tickers:
            log.info("Reflexivity tickers from REFLEXIVITY_TICKERS env: %s", tickers)
            return tickers

    # Reuse the main watchlist
    watchlist_path = os.environ.get("WATCHLIST_PATH", "watchlist.txt")
    try:
        with open(watchlist_path) as fh:
            tickers = [
                line.strip().upper()
                for line in fh
                if line.strip() and not line.startswith("#")
            ]
        if tickers:
            log.info("Reflexivity tickers from %s (%d total)", watchlist_path, len(tickers))
            return tickers
    except OSError:
        log.warning("Could not read %s — using built-in defaults", watchlist_path)

    log.warning("Reflexivity tickers: using defaults %s", _DEFAULTS)
    return list(_DEFAULTS)
