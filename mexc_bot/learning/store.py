"""SQLite tables for learning events, labels, outcomes, and journal trades.

Uses the same DB file as AlertStore / MoverStore but never touches the alerts table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class EventStore:
    """Structured memory for sensor fires, user labels, outcomes, journal."""

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
            CREATE TABLE IF NOT EXISTS learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                ts REAL NOT NULL,
                price REAL,
                ref_price REAL,
                drop_pct REAL,
                velocity_band TEXT,
                heat_breadth INTEGER,
                mode TEXT,
                payload_json TEXT,
                news_event_id INTEGER
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_events_user_ts "
            "ON learning_events (user_id, ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_events_user_sym "
            "ON learning_events (user_id, symbol, market, ts DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action TEXT,
                bounce_quality TEXT,
                behavior TEXT,
                notes TEXT,
                ts REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_labels_event "
            "ON learning_labels (event_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_outcomes (
                event_id INTEGER NOT NULL,
                horizon_seconds INTEGER NOT NULL,
                max_bounce_pct REAL,
                max_dd_pct REAL,
                last_price REAL,
                computed_at REAL NOT NULL,
                PRIMARY KEY (event_id, horizon_seconds)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_avg REAL,
                exit_avg REAL,
                notes TEXT,
                opened_at REAL,
                closed_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_user_status "
            "ON journal_trades (user_id, status)"
        )

    def log_event(
        self,
        user_id: int,
        source: str,
        symbol: str,
        market: str,
        *,
        ts: Optional[float] = None,
        price: Optional[float] = None,
        ref_price: Optional[float] = None,
        drop_pct: Optional[float] = None,
        velocity_band: Optional[str] = None,
        heat_breadth: Optional[int] = None,
        mode: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        news_event_id: Optional[int] = None,
    ) -> int:
        """Insert a learning event. Returns new event id. Soft-fails to 0 on error."""
        wall = float(ts if ts is not None else time.time())
        payload_json = json.dumps(payload) if payload else None
        try:
            with self._lock:
                conn = self._get_conn()
                cur = conn.execute(
                    """
                    INSERT INTO learning_events (
                        user_id, source, symbol, market, ts, price, ref_price,
                        drop_pct, velocity_band, heat_breadth, mode,
                        payload_json, news_event_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(user_id),
                        source,
                        symbol,
                        market,
                        wall,
                        price,
                        ref_price,
                        drop_pct,
                        velocity_band,
                        heat_breadth,
                        mode,
                        payload_json,
                        news_event_id,
                    ),
                )
                eid = int(cur.lastrowid)
                logger.info(
                    "learning.event id=%s user=%s %s %s:%s mode=%s drop=%s band=%s",
                    eid,
                    user_id,
                    source,
                    market,
                    symbol,
                    mode,
                    drop_pct,
                    velocity_band,
                )
                return eid
        except Exception as e:
            logger.error("learning.event log failed: %s", e)
            return 0

    def label_event(
        self,
        event_id: int,
        user_id: int,
        *,
        action: Optional[str] = None,
        bounce_quality: Optional[str] = None,
        behavior: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        if event_id <= 0:
            return False
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    "SELECT id FROM learning_events WHERE id = ? AND user_id = ?",
                    (event_id, user_id),
                ).fetchone()
                if not row:
                    return False
                conn.execute(
                    """
                    INSERT INTO learning_labels (
                        event_id, user_id, action, bounce_quality, behavior, notes, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        user_id,
                        action,
                        bounce_quality,
                        behavior,
                        notes,
                        time.time(),
                    ),
                )
                logger.info(
                    "learning.label event=%s user=%s action=%s bounce=%s behavior=%s",
                    event_id,
                    user_id,
                    action,
                    bounce_quality,
                    behavior,
                )
                return True
        except Exception as e:
            logger.error("learning.label failed: %s", e)
            return False

    def label_latest(
        self,
        user_id: int,
        *,
        symbol: Optional[str] = None,
        action: Optional[str] = None,
        bounce_quality: Optional[str] = None,
        behavior: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[int]:
        """Label most recent event for user (optionally filtered by symbol)."""
        with self._lock:
            conn = self._get_conn()
            if symbol:
                row = conn.execute(
                    """
                    SELECT id FROM learning_events
                    WHERE user_id = ? AND UPPER(symbol) LIKE ?
                    ORDER BY ts DESC LIMIT 1
                    """,
                    (user_id, f"%{symbol.upper().replace(' ', '')}%"),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id FROM learning_events
                    WHERE user_id = ?
                    ORDER BY ts DESC LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
            if not row:
                return None
            eid = int(row["id"])
        ok = self.label_event(
            eid,
            user_id,
            action=action,
            bounce_quality=bounce_quality,
            behavior=behavior,
            notes=notes,
        )
        return eid if ok else None

    def recent_events(self, user_id: int, limit: int = 20) -> List[dict]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT e.*,
                    (SELECT action FROM learning_labels l
                     WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_action,
                    (SELECT bounce_quality FROM learning_labels l
                     WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_bounce
                FROM learning_events e
                WHERE e.user_id = ?
                ORDER BY e.ts DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_events_since(self, user_id: int, since_ts: float) -> int:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM learning_events WHERE user_id = ? AND ts >= ?",
                (user_id, since_ts),
            ).fetchone()
            return int(row["c"]) if row else 0

    def record_outcome(
        self,
        event_id: int,
        horizon_seconds: int,
        *,
        max_bounce_pct: Optional[float],
        max_dd_pct: Optional[float],
        last_price: Optional[float],
    ) -> None:
        try:
            with self._lock:
                conn = self._get_conn()
                conn.execute(
                    """
                    INSERT INTO learning_outcomes (
                        event_id, horizon_seconds, max_bounce_pct, max_dd_pct,
                        last_price, computed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, horizon_seconds) DO UPDATE SET
                        max_bounce_pct = excluded.max_bounce_pct,
                        max_dd_pct = excluded.max_dd_pct,
                        last_price = excluded.last_price,
                        computed_at = excluded.computed_at
                    """,
                    (
                        event_id,
                        horizon_seconds,
                        max_bounce_pct,
                        max_dd_pct,
                        last_price,
                        time.time(),
                    ),
                )
        except Exception as e:
            logger.error("learning.outcome failed event=%s: %s", event_id, e)

    def pending_outcomes(
        self,
        horizons: Sequence[int],
        *,
        now: Optional[float] = None,
        limit: int = 200,
    ) -> List[dict]:
        """Events that still need an outcome row for at least one horizon."""
        wall = float(now if now is not None else time.time())
        horizons = [int(h) for h in horizons if int(h) > 0]
        if not horizons:
            return []
        out: List[dict] = []
        with self._lock:
            conn = self._get_conn()
            # Only events old enough for the smallest horizon, with a price, last N days
            min_h = min(horizons)
            rows = conn.execute(
                """
                SELECT id, user_id, symbol, market, ts, price, source, mode
                FROM learning_events
                WHERE price IS NOT NULL AND price > 0
                  AND ts <= ?
                  AND ts >= ?
                ORDER BY ts ASC
                LIMIT ?
                """,
                (wall - min_h, wall - 7 * 86400, limit * 3),
            ).fetchall()
            for r in rows:
                eid = int(r["id"])
                age = wall - float(r["ts"])
                for h in horizons:
                    if age < h:
                        continue
                    exists = conn.execute(
                        """
                        SELECT 1 FROM learning_outcomes
                        WHERE event_id = ? AND horizon_seconds = ?
                        """,
                        (eid, h),
                    ).fetchone()
                    if exists:
                        continue
                    out.append(
                        {
                            "event_id": eid,
                            "user_id": int(r["user_id"]),
                            "symbol": r["symbol"],
                            "market": r["market"],
                            "ts": float(r["ts"]),
                            "price": float(r["price"]),
                            "horizon_seconds": h,
                        }
                    )
                    if len(out) >= limit:
                        return out
        return out

    def stats_for_symbol(
        self, user_id: int, symbol: str, market: Optional[str] = None
    ) -> dict:
        with self._lock:
            conn = self._get_conn()
            if market:
                rows = conn.execute(
                    """
                    SELECT e.id, e.velocity_band, e.drop_pct, e.mode,
                           (SELECT action FROM learning_labels l
                            WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS action
                    FROM learning_events e
                    WHERE e.user_id = ? AND e.symbol = ? AND e.market = ?
                    ORDER BY e.ts DESC LIMIT 50
                    """,
                    (user_id, symbol, market),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.id, e.velocity_band, e.drop_pct, e.mode,
                           (SELECT action FROM learning_labels l
                            WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS action
                    FROM learning_events e
                    WHERE e.user_id = ? AND e.symbol = ?
                    ORDER BY e.ts DESC LIMIT 50
                    """,
                    (user_id, symbol),
                ).fetchall()
            n = len(rows)
            took = sum(1 for r in rows if r["action"] == "took")
            skip = sum(1 for r in rows if r["action"] == "skip")
            panic = sum(1 for r in rows if r["velocity_band"] == "PANIC")
            return {
                "events": n,
                "took": took,
                "skip": skip,
                "panic_band": panic,
            }

    # --- journal ---

    def journal_open(
        self,
        user_id: int,
        symbol: str,
        market: str,
        entry_avg: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> int:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """
                INSERT INTO journal_trades (
                    user_id, symbol, market, status, entry_avg, notes, opened_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?)
                """,
                (user_id, symbol, market, entry_avg, notes, time.time()),
            )
            return int(cur.lastrowid)

    def journal_close(
        self,
        user_id: int,
        trade_id: Optional[int] = None,
        *,
        symbol: Optional[str] = None,
        exit_avg: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> bool:
        with self._lock:
            conn = self._get_conn()
            if trade_id:
                row = conn.execute(
                    "SELECT id, notes FROM journal_trades WHERE id = ? AND user_id = ? AND status = 'open'",
                    (trade_id, user_id),
                ).fetchone()
            elif symbol:
                row = conn.execute(
                    """
                    SELECT id, notes FROM journal_trades
                    WHERE user_id = ? AND status = 'open' AND UPPER(symbol) LIKE ?
                    ORDER BY opened_at DESC LIMIT 1
                    """,
                    (user_id, f"%{symbol.upper()}%"),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, notes FROM journal_trades
                    WHERE user_id = ? AND status = 'open'
                    ORDER BY opened_at DESC LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
            if not row:
                return False
            merged = row["notes"] or ""
            if notes:
                merged = (merged + " | " + notes).strip(" |")
            conn.execute(
                """
                UPDATE journal_trades
                SET status = 'closed', exit_avg = ?, notes = ?, closed_at = ?
                WHERE id = ?
                """,
                (exit_avg, merged or None, time.time(), int(row["id"])),
            )
            return True

    def journal_list(self, user_id: int, *, open_only: bool = True) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            if open_only:
                rows = conn.execute(
                    """
                    SELECT * FROM journal_trades
                    WHERE user_id = ? AND status = 'open'
                    ORDER BY opened_at DESC
                    """,
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM journal_trades
                    WHERE user_id = ?
                    ORDER BY opened_at DESC LIMIT 30
                    """,
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]
