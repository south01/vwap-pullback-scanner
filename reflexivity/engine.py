"""
Reflexivity Engine orchestrator.

Runs all signal modules for a list of tickers, persists results to SQLite,
and holds the latest in-memory snapshot for Flask routes.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

import config
from utils import now_et, today_str

from .db import upsert_score
from .classifier import classify
from .exit_detector import detect_exit
from .strategy import generate_note
from .signals import momentum, volume, sentiment, flow, catalyst

log = logging.getLogger("vwap_scanner")

_SESSION = requests.Session()
_SESSION.headers.update({"Authorization": f"Bearer {config.MASSIVE_API_KEY}"})


class ReflexivityEngine:
    def __init__(self, cfg=None) -> None:
        self._lock    = threading.Lock()
        self._latest: list[dict] = []
        self._last_run: str      = "—"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_cycle(self, tickers: list[str]) -> None:
        """Analyse every ticker and persist results. Thread-safe."""
        results = []
        for symbol in tickers:
            try:
                result = self._analyse(symbol)
                results.append(result)
            except Exception as exc:
                log.error("reflexivity engine error for %s: %s", symbol, exc)
            time.sleep(float(getattr(config, "REQUEST_DELAY_SEC", 0.2)))

        results.sort(key=lambda r: r["composite_score"], reverse=True)
        with self._lock:
            self._latest   = results
            self._last_run = now_et().strftime("%H:%M:%S ET")

    def latest(self) -> list[dict]:
        with self._lock:
            return list(self._latest)

    def last_run(self) -> str:
        with self._lock:
            return self._last_run

    def get_ticker_detail(self, symbol: str) -> dict | None:
        from .db import get_ticker_history
        rows = get_ticker_history(symbol.upper(), limit=20)
        if not rows:
            return None
        latest = rows[0]
        return {"symbol": symbol.upper(), "latest": latest, "history": rows}

    # ------------------------------------------------------------------
    # Core analysis
    # ------------------------------------------------------------------

    def _analyse(self, symbol: str) -> dict:
        ts = now_et().strftime("%Y-%m-%d %H:%M:%S")

        m_score = momentum.score(symbol, self)
        v_score = volume.score(symbol, self)
        s_score = sentiment.score(symbol, self)
        f_score = flow.score(symbol, self)
        c_score = catalyst.score(symbol, self)

        composite = round(
            m_score * 0.30
            + v_score * 0.25
            + c_score * 0.20
            + s_score * 0.15
            + f_score * 0.10,
            1,
        )

        classification = classify(composite, m_score, v_score)
        exit_sig       = detect_exit(symbol, composite, m_score, v_score)
        note           = generate_note(symbol, composite, classification, m_score, v_score, s_score)

        upsert_score(
            symbol          = symbol,
            timestamp       = ts,
            momentum_score  = m_score,
            volume_score    = v_score,
            sentiment_score = s_score,
            flow_score      = f_score,
            catalyst_score  = c_score,
            composite_score = composite,
            classification  = classification,
            exit_signal     = exit_sig,
            strategy_note   = note,
        )

        log.info(
            "reflexivity %s | composite=%.1f [M=%.0f V=%.0f S=%.0f F=%.0f C=%.0f] → %s%s",
            symbol, composite, m_score, v_score, s_score, f_score, c_score,
            classification, " EXIT" if exit_sig else "",
        )

        return {
            "symbol":           symbol,
            "timestamp":        ts,
            "momentum_score":   m_score,
            "volume_score":     v_score,
            "sentiment_score":  s_score,
            "flow_score":       f_score,
            "catalyst_score":   c_score,
            "composite_score":  composite,
            "classification":   classification,
            "exit_signal":      exit_sig,
            "strategy_note":    note,
        }

    # ------------------------------------------------------------------
    # API helpers — mirror scanner.py pattern with exponential backoff
    # ------------------------------------------------------------------

    def _fetch(self, path: str, params: dict | None = None) -> dict | None:
        url = config.MASSIVE_BASE_URL.rstrip("/") + path
        for attempt in range(1, 4):
            try:
                resp = _SESSION.get(url, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                log.debug("reflexivity API error (attempt %d/3) %s: %s", attempt, url, exc)
                if attempt < 3:
                    time.sleep(2 ** attempt)
        return None

    def _fetch_bars(
        self,
        ticker: str,
        timespan: str = "minute",
        multiplier: int = 5,
        from_: str | None = None,
        to: str | None = None,
    ) -> list[dict]:
        date = from_ or today_str()
        to_s = to    or date
        path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{date}/{to_s}"
        data = self._fetch(path, {"adjusted": "true", "sort": "asc", "limit": 500})
        if data and data.get("resultsCount", 0) > 0:
            return data["results"]
        return []

    def _fetch_snapshot(self, ticker: str) -> dict | None:
        return self._fetch(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
