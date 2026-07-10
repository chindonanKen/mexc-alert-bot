"""SQLite tables for mover scanner settings + watchlist.

Uses the same DB file as AlertStore but never touches the alerts table.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from threading import RLock
from typing import List, Optional

logger = logging.getLogger(__name__)


class MoverStore:
    """Per-user mover settings and watchlist. Isolated from target-price alerts."""

    def __init__(self, path: Path):
        self._lock = RLock()
        if str(path).endswith(".json"):
            self.db_path = path.with_suffix(".db")
        else:
            self.db_path = path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mover_settings (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                threshold_percent REAL NOT NULL,
                lookback_seconds INTEGER NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mover_watchlist (
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'futures',
                PRIMARY KEY (user_id, symbol, market)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mover_watch_user ON mover_watchlist (user_id)"
        )

    def get_settings(
        self,
        user_id: int,
        default_threshold: float,
        default_lookback: int,
    ) -> dict:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT enabled, threshold_percent, lookback_seconds FROM mover_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return {
                    "enabled": False,
                    "threshold_percent": default_threshold,
                    "lookback_seconds": default_lookback,
                }
            return {
                "enabled": bool(row["enabled"]),
                "threshold_percent": float(row["threshold_percent"]),
                "lookback_seconds": int(row["lookback_seconds"]),
            }

    def set_enabled(self, user_id: int, enabled: bool, default_threshold: float, default_lookback: int) -> dict:
        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT 1 FROM mover_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE mover_settings SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (1 if enabled else 0, user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO mover_settings (user_id, enabled, threshold_percent, lookback_seconds) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, 1 if enabled else 0, default_threshold, default_lookback),
                )
            return self.get_settings(user_id, default_threshold, default_lookback)

    def set_params(
        self,
        user_id: int,
        threshold_percent: float,
        lookback_seconds: int,
        default_enabled: bool = False,
    ) -> dict:
        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT enabled FROM mover_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE mover_settings SET threshold_percent = ?, lookback_seconds = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (float(threshold_percent), int(lookback_seconds), user_id),
                )
                enabled = bool(existing["enabled"])
            else:
                conn.execute(
                    "INSERT INTO mover_settings (user_id, enabled, threshold_percent, lookback_seconds) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, 1 if default_enabled else 0, float(threshold_percent), int(lookback_seconds)),
                )
                enabled = default_enabled
            return {
                "enabled": enabled,
                "threshold_percent": float(threshold_percent),
                "lookback_seconds": int(lookback_seconds),
            }

    def get_watchlist(self, user_id: int) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT symbol, market FROM mover_watchlist WHERE user_id = ? ORDER BY symbol ASC",
                (user_id,),
            ).fetchall()
            return [{"symbol": r["symbol"], "market": r["market"]} for r in rows]

    def set_watchlist(self, user_id: int, items: List[dict]) -> int:
        """Replace entire watchlist. items: [{symbol, market}, ...]."""
        with self._lock:
            conn = self._get_conn()
            with conn:
                conn.execute("DELETE FROM mover_watchlist WHERE user_id = ?", (user_id,))
                for it in items:
                    conn.execute(
                        "INSERT OR IGNORE INTO mover_watchlist (user_id, symbol, market) VALUES (?, ?, ?)",
                        (user_id, str(it["symbol"]).upper(), str(it.get("market", "futures")).lower()),
                    )
            return len(self.get_watchlist(user_id))

    def add_watchlist(self, user_id: int, symbol: str, market: str = "futures") -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR IGNORE INTO mover_watchlist (user_id, symbol, market) VALUES (?, ?, ?)",
                (user_id, symbol.upper(), market.lower()),
            )

    def clear_watchlist(self, user_id: int) -> int:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM mover_watchlist WHERE user_id = ?", (user_id,))
            return cur.rowcount

    def get_enabled_users(self) -> List[int]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT user_id FROM mover_settings WHERE enabled = 1"
            ).fetchall()
            return [int(r["user_id"]) for r in rows]
