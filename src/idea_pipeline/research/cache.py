"""SQLite cache for research results.

Key: sha256(query + "|" + source_name)
Value: raw response JSON + timestamp
TTL: 7 days
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

_TTL_SECONDS = 7 * 24 * 3600

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "cache" / "research.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS research_cache (
    key        TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    query      TEXT NOT NULL,
    response   TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""


def _cache_key(query: str, source: str) -> str:
    return hashlib.sha256(f"{query}|{source}".encode()).hexdigest()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def cache_get(query: str, source: str) -> Optional[Any]:
    """Return cached response or None if missing/expired."""
    key = _cache_key(query, source)
    with _connect() as conn:
        row = conn.execute(
            "SELECT response, created_at FROM research_cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    response_json, created_at = row
    if time.time() - created_at > _TTL_SECONDS:
        return None
    return json.loads(response_json)


def cache_set(query: str, source: str, response: Any) -> None:
    """Store response in cache."""
    key = _cache_key(query, source)
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO research_cache (key, source, query, response, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (key, source, query, json.dumps(response, ensure_ascii=False), time.time()),
        )
        conn.commit()


def cache_stats() -> dict:
    """Return {total, expired} entry counts for diagnostics."""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM research_cache").fetchone()[0]
        expired = conn.execute(
            "SELECT COUNT(*) FROM research_cache WHERE created_at < ?",
            (time.time() - _TTL_SECONDS,),
        ).fetchone()[0]
    return {"total": total, "expired": expired}
