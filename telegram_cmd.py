"""
Telegram bot command handler — runs in its own daemon thread.
Polls getUpdates every 2 seconds and dispatches commands to the Scanner.

Supported commands:
  /list             — show active tickers
  /add TICKER       — add a ticker for this session
  /remove TICKER    — remove a ticker for this session
  /pause            — pause scanning
  /resume           — resume scanning
  /status           — current scan summary
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import requests

import config
from shared_state import state

if TYPE_CHECKING:
    from scanner import Scanner

log = logging.getLogger("vwap_scanner")

_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"


class TelegramCommandHandler:
    def __init__(self, scanner: Scanner) -> None:
        self._scanner = scanner
        self._offset  = 0

    def run(self) -> None:
        log.info("Telegram command handler started")
        while True:
            try:
                updates = self._poll()
                for upd in updates:
                    self._dispatch(upd)
            except Exception as exc:
                log.error("Telegram command poll error: %s", exc)
            time.sleep(2)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll(self) -> list[dict]:
        resp = requests.get(
            f"{_BASE}/getUpdates",
            params={"offset": self._offset, "timeout": 10, "allowed_updates": ["message"]},
            timeout=15,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    def _send(self, text: str) -> None:
        requests.post(
            f"{_BASE}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, update: dict) -> None:
        msg     = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "").strip()

        # Only accept messages from the configured chat
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return
        if not text.startswith("/"):
            return

        parts   = text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]   # strip @BotName suffix
        arg     = parts[1].strip().upper() if len(parts) > 1 else ""

        handlers = {
            "/list":   self._cmd_list,
            "/status": self._cmd_status,
            "/pause":  self._cmd_pause,
            "/resume": self._cmd_resume,
        }

        if command in handlers:
            handlers[command]()
        elif command == "/add":
            self._cmd_add(arg)
        elif command == "/remove":
            self._cmd_remove(arg)
        else:
            self._send(
                "Unknown command.\n\n"
                "/list — active tickers\n"
                "/add TICKER — add ticker\n"
                "/remove TICKER — remove ticker\n"
                "/pause — pause scanning\n"
                "/resume — resume scanning\n"
                "/status — scan summary"
            )

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    def _cmd_list(self) -> None:
        tickers = self._scanner.watchlist
        if not tickers:
            self._send("No active tickers.")
            return
        lines = [f"<b>Active tickers ({len(tickers)})</b>"]
        for t in tickers:
            snap = state.snapshot()["ticker_status"].get(t, {})
            price = snap.get("price", 0.0)
            t1 = "⚡" if snap.get("tier1_fired") else ""
            t2 = "🟢" if snap.get("tier2_fired") else ""
            lines.append(f"  {t}  ${price:.2f}  {t1}{t2}")
        self._send("\n".join(lines))

    def _cmd_add(self, ticker: str) -> None:
        if not ticker:
            self._send("Usage: /add TICKER")
            return
        if ticker in self._scanner.watchlist:
            self._send(f"{ticker} is already in the watchlist.")
            return
        self._scanner.add_ticker(ticker)
        self._send(f"✅ <b>{ticker}</b> added. Watchlist now has {len(self._scanner.watchlist)} tickers.\n"
                   f"<i>Note: restarting the service resets to watchlist.txt</i>")
        log.info("CMD: added %s", ticker)

    def _cmd_remove(self, ticker: str) -> None:
        if not ticker:
            self._send("Usage: /remove TICKER")
            return
        if ticker not in self._scanner.watchlist:
            self._send(f"{ticker} is not in the watchlist.")
            return
        self._scanner.remove_ticker(ticker)
        self._send(f"🗑 <b>{ticker}</b> removed. Watchlist now has {len(self._scanner.watchlist)} tickers.")
        log.info("CMD: removed %s", ticker)

    def _cmd_pause(self) -> None:
        if state.is_paused:
            self._send("Already paused. Use /resume to restart scanning.")
            return
        state.is_paused = True
        self._send("⏸ <b>Scanner paused.</b> Use /resume to restart.")
        log.info("CMD: scanner paused")

    def _cmd_resume(self) -> None:
        if not state.is_paused:
            self._send("Scanner is already running.")
            return
        state.is_paused = False
        self._send("▶️ <b>Scanner resumed.</b>")
        log.info("CMD: scanner resumed")

    def _cmd_status(self) -> None:
        s = state.snapshot()
        status_str = "PAUSED" if s["is_paused"] else ("SCANNING" if s["is_market_open"] else "MARKET CLOSED")
        lines = [
            f"<b>Scanner Status</b>",
            f"State: {status_str}",
            f"Last scan: {s['last_scan_time']}",
            f"Cycles today: {s['scan_count']}",
            f"Tickers: {len(s['tickers'])}",
            f"T1 alerts: {s['tier1_count']} | T2 alerts: {s['tier2_count']}",
            f"SPY: {s['spy_chg']:+.2f}% | VIX: {s['vix']:.1f}",
        ]
        active_alerts = [
            t for t, snap in s["ticker_status"].items()
            if snap.get("tier1_fired") or snap.get("tier2_fired")
        ]
        if active_alerts:
            lines.append(f"Active setups: {', '.join(active_alerts)}")
        self._send("\n".join(lines))
