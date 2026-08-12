"""Durable log of target-price fires (alerts are one-shot and deleted on fire).

Additive table only — never touches ``alerts`` rows except reading.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TargetFireLog:
    def __init__(self, path: Path):
        self._lock = RLock()
        if str(path).endswith(".json"):
            self.db_path = path.with_suffix(".db")
        else:
            self.db_path = Path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init()

    def _get(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
        return self._conn

    def _init(self) -> None:
        with self._lock:
            conn = self._get()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_fire_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    fire_price REAL NOT NULL,
                    reason TEXT,
                    stable_id INTEGER,
                    ts REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_target_fire_user_ts "
                "ON target_fire_log (user_id, ts DESC)"
            )

    def log(
        self,
        user_id: int,
        symbol: str,
        market: str,
        *,
        target_price: float,
        fire_price: float,
        reason: str = "",
        stable_id: Optional[int] = None,
        ts: Optional[float] = None,
    ) -> int:
        wall = float(ts if ts is not None else time.time())
        with self._lock:
            try:
                cur = self._get().execute(
                    "INSERT INTO target_fire_log "
                    "(user_id, symbol, market, target_price, fire_price, reason, stable_id, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(user_id),
                        str(symbol).upper(),
                        str(market or "spot").lower(),
                        float(target_price),
                        float(fire_price),
                        str(reason or ""),
                        int(stable_id) if stable_id is not None else None,
                        wall,
                    ),
                )
                return int(cur.lastrowid or 0)
            except Exception as e:
                logger.warning("target_fire_log insert failed: %s", e)
                return 0

    def hits_between(
        self,
        user_id: int,
        t0: float,
        t1: float,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._get().execute(
                "SELECT * FROM target_fire_log "
                "WHERE user_id = ? AND ts >= ? AND ts < ? "
                "ORDER BY ts ASC",
                (int(user_id), float(t0), float(t1)),
            ).fetchall()
            return [dict(r) for r in rows]
