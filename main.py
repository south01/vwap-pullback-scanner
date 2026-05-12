"""
Entry point — starts three concurrent components:
  1. Scanner loop          (background thread)
  2. Telegram cmd handler  (background thread)
  3. Flask dashboard       (main thread, binds to $PORT for Railway)
"""

import logging
import os
import signal
import sys
import threading

from utils import setup_logging

log = setup_logging()


def main() -> None:
    log.info("VWAP Pullback Scanner starting")

    from scanner import Scanner
    from telegram_cmd import TelegramCommandHandler
    from dashboard import create_app

    scanner = Scanner()
    scanner.setup()

    def _shutdown(signum, frame):
        log.info("Shutdown signal (%s) — sending session summary…", signum)
        scanner.stop()
        scanner.send_shutdown_summary()
        log.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    # --- Scanner thread ---
    scan_thread = threading.Thread(target=scanner.run, name="scanner", daemon=True)
    scan_thread.start()

    # --- Telegram command thread ---
    cmd_handler = TelegramCommandHandler(scanner)
    cmd_thread = threading.Thread(target=cmd_handler.run, name="telegram-cmd", daemon=True)
    cmd_thread.start()

    # --- Flask dashboard (main thread) ---
    app  = create_app()
    port = int(os.environ.get("PORT", 8080))
    log.info("Dashboard starting on port %d", port)

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        # Fallback to Flask dev server if waitress not installed
        app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
