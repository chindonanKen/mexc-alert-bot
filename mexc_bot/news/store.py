"""news_events table — same SQLite file, never touches alerts."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class NewsStore:
    def __init__(self, path: Path):
        self._lock = RLock()
        if str(path).endswith(".json"):
            self.db_path = path.with_suffix(".db")
        else:
            self.db_path = path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path, check_same_thread=False, isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                class TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                source TEXT NOT NULL,
                source_trust TEXT NOT NULL,
                ts REAL NOT NULL,
                raw_json TEXT,
                fingerprint TEXT UNIQUE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_ts ON news_events (ts DESC)"
        )

    def has_fingerprint(self, fp: str) -> bool:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT 1 FROM news_events WHERE fingerprint = ?", (fp,)
            ).fetchone()
            return row is not None

    def insert(
        self,
        *,
        symbol: Optional[str],
        class_: str,
        severity: str,
        title: str,
        url: Optional[str],
        source: str,
        source_trust: str,
        ts: Optional[float] = None,
        raw: Optional[dict] = None,
        fingerprint: str,
    ) -> int:
        with self._lock:
            try:
                cur = self._get_conn().execute(
                    """
                    INSERT INTO news_events (
                        symbol, class, severity, title, url, source,
                        source_trust, ts, raw_json, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        class_,
                        severity,
                        title,
                        url,
                        source,
                        source_trust,
                        float(ts if ts is not None else time.time()),
                        json.dumps(raw) if raw else None,
                        fingerprint,
                    ),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return 0
            except Exception as e:
                logger.error("news insert failed: %s", e)
                return 0

    def recent(self, limit: int = 20) -> List[dict]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM news_events ORDER BY ts DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_for_symbol(self, symbol_base: str, within_seconds: float = 86400) -> List[dict]:
        cutoff = time.time() - within_seconds
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT * FROM news_events
                WHERE ts >= ? AND (symbol IS NULL OR UPPER(symbol) = UPPER(?))
                ORDER BY ts DESC LIMIT 10
                """,
                (cutoff, symbol_base),
            ).fetchall()
            return [dict(r) for r in rows]
