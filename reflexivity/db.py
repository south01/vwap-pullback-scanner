"""SQLite schema and helpers for the Reflexivity Engine."""

import logging
import os
import sqlite3
from contextlib import contextmanager

log = logging.getLogger("vwap_scanner")

DB_PATH = os.environ.get("REFLEXIVITY_DB_PATH", "/tmp/reflexivity.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reflexivity_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    timestamp       TEXT    NOT NULL,
    momentum_score  REAL    DEFAULT 0,
    volume_score    REAL    DEFAULT 0,
    sentiment_score REAL    DEFAULT 0,
    flow_score      REAL    DEFAULT 0,
    catalyst_score  REAL    DEFAULT 0,
    composite_score REAL    DEFAULT 0,
    classification  TEXT    DEFAULT 'NO_LOOP',
    exit_signal     INTEGER DEFAULT 0,
    strategy_note   TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ref_symbol_ts
    ON reflexivity_scores(symbol, timestamp);

CREATE TABLE IF NOT EXISTS reflexivity_tickers (
    symbol   TEXT PRIMARY KEY,
    source   TEXT DEFAULT 'manual',
    added_at TEXT DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    if DB_PATH.startswith("/tmp"):
        log.warning(
            "REFLEXIVITY_DB_PATH is under /tmp (%s) — data will be lost on restart. "
            "Set REFLEXIVITY_DB_PATH to a persistent volume path (e.g. /data/reflexivity.db).",
            DB_PATH,
        )
    with _conn() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_score(
    symbol: str,
    timestamp: str,
    momentum_score: float,
    volume_score: float,
    sentiment_score: float,
    flow_score: float,
    catalyst_score: float,
    composite_score: float,
    classification: str,
    exit_signal: bool,
    strategy_note: str,
) -> None:
    sql = """
    INSERT INTO reflexivity_scores
        (symbol, timestamp, momentum_score, volume_score, sentiment_score,
         flow_score, catalyst_score, composite_score, classification,
         exit_signal, strategy_note)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """
    with _conn() as conn:
        conn.execute(sql, (
            symbol, timestamp,
            momentum_score, volume_score, sentiment_score,
            flow_score, catalyst_score, composite_score,
            classification, int(exit_signal), strategy_note,
        ))


def get_latest_scores(limit: int = 100) -> list[dict]:
    """One row per symbol — the most recent score."""
    sql = """
    SELECT s.*
    FROM reflexivity_scores s
    INNER JOIN (
        SELECT symbol, MAX(id) AS max_id
        FROM reflexivity_scores
        GROUP BY symbol
    ) latest ON s.id = latest.max_id
    ORDER BY s.composite_score DESC
    LIMIT ?
    """
    with _conn() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_ticker_history(symbol: str, limit: int = 20) -> list[dict]:
    sql = """
    SELECT * FROM reflexivity_scores
    WHERE symbol = ?
    ORDER BY id DESC LIMIT ?
    """
    with _conn() as conn:
        rows = conn.execute(sql, (symbol, limit)).fetchall()
        return [dict(r) for r in rows]


def set_tickers(symbols: list[str], source: str = "watchlist") -> None:
    if not symbols:
        log.warning("set_tickers called with empty list — skipping to avoid clearing DB")
        return
    with _conn() as conn:
        conn.execute("DELETE FROM reflexivity_tickers")
        conn.executemany(
            "INSERT OR IGNORE INTO reflexivity_tickers (symbol, source) VALUES (?, ?)",
            [(s.upper(), source) for s in symbols],
        )


def get_tickers() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT symbol FROM reflexivity_tickers ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]
