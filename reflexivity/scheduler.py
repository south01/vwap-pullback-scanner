"""
Background scheduler — runs ReflexivityEngine on a fixed interval
during market hours, reloading tickers once per day.
"""

import logging
import os
import threading
import time
from datetime import date

from utils import is_market_open, now_et

from .db import get_tickers, set_tickers, init_db
from .ticker_loader import load_tickers
from .alerts import maybe_alert

log = logging.getLogger("vwap_scanner")

SCAN_INTERVAL_SEC = int(os.environ.get("REFLEXIVITY_SCAN_SEC", 300))


def start_reflexivity_scheduler(engine, app) -> threading.Thread:
    """
    Initialise DB, load tickers, start background loop.
    Returns the daemon thread (already started).
    """
    init_db()

    tickers = load_tickers()
    set_tickers(tickers, "gdrive_or_env")
    log.info("Reflexivity scheduler: %d tickers — %s", len(tickers), tickers)

    t = threading.Thread(target=_loop, args=(engine,), name="reflexivity-scheduler", daemon=True)
    t.start()
    return t


def _loop(engine) -> None:
    last_reload_date = date.min

    while True:
        try:
            today = now_et().date()

            # Reload ticker list once per calendar day
            if today != last_reload_date:
                tickers = load_tickers()
                set_tickers(tickers, "gdrive_or_env")
                log.info("Reflexivity tickers reloaded for %s: %s", today, tickers)
                last_reload_date = today

            if is_market_open():
                active = get_tickers()
                if active:
                    log.info("Reflexivity scan — %d tickers", len(active))
                    engine.run_cycle(active)
                    for result in engine.latest():
                        maybe_alert(result)

        except Exception as exc:
            log.error("Reflexivity scheduler error: %s", exc)

        time.sleep(SCAN_INTERVAL_SEC)
