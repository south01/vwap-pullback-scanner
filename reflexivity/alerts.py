"""Telegram alerts for high-conviction reflexivity readings."""

import logging
import time

import requests

import config
from utils import now_et

log = logging.getLogger("vwap_scanner")

_TELEGRAM_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"

# Per-symbol last classification sent — avoid duplicate alerts
_last_alert: dict[str, str] = {}


def _send(text: str) -> bool:
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    for attempt in range(1, 4):
        try:
            resp = requests.post(_TELEGRAM_URL, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("reflexivity Telegram attempt %d/3: %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)
    return False


def maybe_alert(result: dict) -> None:
    """Fire Telegram alert on state transitions worth knowing about."""
    symbol = result["symbol"]
    cls    = result["classification"]
    prev   = _last_alert.get(symbol)

    if cls == "LOOP_ACTIVE" and prev != "LOOP_ACTIVE":
        _send_loop_active(result)
    elif result["exit_signal"] and prev == "LOOP_ACTIVE":
        _send_loop_exit(result)

    _last_alert[symbol] = cls


def _send_loop_active(r: dict) -> None:
    ts = now_et().strftime("%H:%M:%S ET")
    _send(
        f"<b>🔄 LOOP ACTIVE — {r['symbol']}</b> | {ts}\n"
        f"Score: {r['composite_score']:.0f}/100\n"
        f"M: {r['momentum_score']:.0f}  V: {r['volume_score']:.0f}  "
        f"S: {r['sentiment_score']:.0f}  C: {r['catalyst_score']:.0f}\n"
        f"{r['strategy_note']}"
    )
    log.info("reflexivity LOOP_ACTIVE alert sent for %s", r["symbol"])


def _send_loop_exit(r: dict) -> None:
    ts = now_et().strftime("%H:%M:%S ET")
    _send(
        f"<b>⚠️ LOOP COOLING — {r['symbol']}</b> | {ts}\n"
        f"Score: {r['composite_score']:.0f}/100\n"
        f"{r['strategy_note']}"
    )
    log.info("reflexivity LOOP_COOLING alert sent for %s", r["symbol"])
