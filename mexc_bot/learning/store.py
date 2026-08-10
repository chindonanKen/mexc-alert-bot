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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                exchange_trade_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'spot',
                side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                quote_qty REAL,
                ts REAL NOT NULL,
                raw_json TEXT,
                UNIQUE(user_id, exchange_trade_id, market)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_fills_user_ts "
            "ON journal_fills (user_id, ts DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_pending_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_id INTEGER,
                symbol TEXT,
                question TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'engagement',
                status TEXT NOT NULL DEFAULT 'open',
                payload_json TEXT,
                created_at REAL NOT NULL,
                answered_at REAL,
                answer_text TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_pending_user_status "
            "ON learning_pending_questions (user_id, status, created_at DESC)"
        )
        self._ensure_column(conn, "learning_pending_questions", "symbol", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                tags_json TEXT,
                weight REAL NOT NULL DEFAULT 1.0,
                needs_approval INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'owner',
                kind TEXT NOT NULL DEFAULT 'lesson',
                evidence_event_ids_json TEXT,
                created_at REAL NOT NULL,
                approved_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_lessons_user "
            "ON learning_lessons (user_id, needs_approval, created_at DESC)"
        )
        # Additive columns on labels (source/confidence for auto engagement)
        self._ensure_column(conn, "learning_labels", "source", "TEXT")
        self._ensure_column(conn, "learning_labels", "confidence", "REAL")
        # P1 case factory — structured setup freeze (features index; chips annotate)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_setup_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_id INTEGER,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                frozen_at REAL NOT NULL,
                fire_ts REAL,
                fire_price REAL,
                ref_price REAL,
                drop_pct REAL,
                velocity_band TEXT,
                heat_breadth INTEGER,
                features_json TEXT,
                features_ok INTEGER NOT NULL DEFAULT 0,
                chips_json TEXT,
                note TEXT,
                lesson_id INTEGER,
                trade_key TEXT,
                source TEXT NOT NULL DEFAULT 'fire',
                UNIQUE(user_id, event_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_setup_cases_user_ts "
            "ON agent_setup_cases (user_id, frozen_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_setup_cases_sym "
            "ON agent_setup_cases (user_id, symbol, market, frozen_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_flags (
                user_id INTEGER NOT NULL,
                entity_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                free_coins_override TEXT,
                free_since_ts REAL,
                free_mark_usd REAL,
                notes TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, entity_key)
            )
            """
        )
        # book: 'ad' (default AD desk learning) | 'hold' (long-term invest — exclude from AD teach)
        self._ensure_column(conn, "position_flags", "book", "TEXT")

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, col_type: str
    ) -> None:
        cols = {
            str(r[1])
            for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

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
        source: Optional[str] = None,
        confidence: Optional[float] = None,
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
                        event_id, user_id, action, bounce_quality, behavior, notes,
                        ts, source, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        user_id,
                        action,
                        bounce_quality,
                        behavior,
                        notes,
                        time.time(),
                        source,
                        confidence,
                    ),
                )
                logger.info(
                    "learning.label event=%s user=%s action=%s bounce=%s behavior=%s src=%s",
                    event_id,
                    user_id,
                    action,
                    bounce_quality,
                    behavior,
                    source,
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
        source: Optional[str] = "human",
        confidence: Optional[float] = None,
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
            source=source,
            confidence=confidence,
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
                     WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_bounce,
                    (SELECT behavior FROM learning_labels l
                     WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_behavior,
                    (SELECT source FROM learning_labels l
                     WHERE l.event_id = e.id ORDER BY l.ts DESC LIMIT 1) AS last_label_source,
                    (SELECT max_bounce_pct FROM learning_outcomes o
                     WHERE o.event_id = e.id
                     ORDER BY o.horizon_seconds ASC LIMIT 1) AS outcome_bounce,
                    (SELECT max_dd_pct FROM learning_outcomes o
                     WHERE o.event_id = e.id
                     ORDER BY o.horizon_seconds ASC LIMIT 1) AS outcome_dd
                FROM learning_events e
                WHERE e.user_id = ?
                ORDER BY e.ts DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def unlabeled_events_for_bridge(
        self,
        *,
        older_than_ts: Optional[float] = None,
        limit: int = 80,
        user_ids: Optional[Sequence[int]] = None,
    ) -> List[dict]:
        """Events with no action label yet (for engagement bridge)."""
        limit = max(1, min(int(limit), 200))
        with self._lock:
            conn = self._get_conn()
            sql = """
                SELECT e.*,
                    (SELECT action FROM learning_labels l
                     WHERE l.event_id = e.id AND l.action IS NOT NULL
                     ORDER BY l.ts DESC LIMIT 1) AS last_action
                FROM learning_events e
                WHERE 1=1
            """
            params: List[Any] = []
            if older_than_ts is not None:
                sql += " AND e.ts <= ?"
                params.append(float(older_than_ts))
            if user_ids:
                placeholders = ",".join("?" * len(user_ids))
                sql += f" AND e.user_id IN ({placeholders})"
                params.extend(int(u) for u in user_ids)
            sql += """
                AND NOT EXISTS (
                    SELECT 1 FROM learning_labels l
                    WHERE l.event_id = e.id AND l.action IS NOT NULL
                )
                ORDER BY e.ts ASC
                LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _norm_symbol(symbol: Optional[str]) -> str:
        return (
            (symbol or "")
            .upper()
            .replace("_", "")
            .replace("STOCK", "")
            .replace("-", "")
            .strip()
        )

    def enqueue_pending_question(
        self,
        user_id: int,
        *,
        question: str,
        event_id: Optional[int] = None,
        symbol: Optional[str] = None,
        kind: str = "engagement",
        max_open: int = 2,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Insert open pending question if under cap.

        Coalesce: same event_id OR same symbol (normalized) among open rows.
        Returns new id, existing id if coalesced, or None if cap full.
        """
        q = (question or "").strip()
        if not q:
            return None
        max_open = max(1, int(max_open))
        try:
            with self._lock:
                conn = self._get_conn()
                sym = symbol
                if not sym and event_id:
                    erow = conn.execute(
                        "SELECT symbol FROM learning_events WHERE id = ?",
                        (int(event_id),),
                    ).fetchone()
                    if erow:
                        sym = erow["symbol"]
                norm = self._norm_symbol(sym)

                if event_id:
                    existing = conn.execute(
                        """
                        SELECT id FROM learning_pending_questions
                        WHERE user_id = ? AND event_id = ? AND status = 'open'
                        LIMIT 1
                        """,
                        (user_id, int(event_id)),
                    ).fetchone()
                    if existing:
                        return int(existing["id"])

                if norm:
                    open_rows = conn.execute(
                        """
                        SELECT id, event_id, symbol FROM learning_pending_questions
                        WHERE user_id = ? AND status = 'open'
                        """,
                        (user_id,),
                    ).fetchall()
                    for row in open_rows:
                        if self._norm_symbol(row["symbol"]) == norm:
                            return int(row["id"])
                        # also match via event's symbol if pending.symbol empty
                        if row["event_id"] and not row["symbol"]:
                            er = conn.execute(
                                "SELECT symbol FROM learning_events WHERE id = ?",
                                (int(row["event_id"]),),
                            ).fetchone()
                            if er and self._norm_symbol(er["symbol"]) == norm:
                                return int(row["id"])

                open_n = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM learning_pending_questions
                    WHERE user_id = ? AND status = 'open'
                    """,
                    (user_id,),
                ).fetchone()
                if int(open_n["c"] if open_n else 0) >= max_open:
                    return None
                cur = conn.execute(
                    """
                    INSERT INTO learning_pending_questions (
                        user_id, event_id, symbol, question, kind, status,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                    """,
                    (
                        int(user_id),
                        int(event_id) if event_id else None,
                        sym,
                        q,
                        kind,
                        json.dumps(payload) if payload else None,
                        time.time(),
                    ),
                )
                return int(cur.lastrowid)
        except Exception as e:
            logger.error("enqueue_pending_question failed: %s", e)
            return None

    def list_pending_questions(
        self, user_id: int, *, status: str = "open", limit: int = 20
    ) -> List[dict]:
        limit = max(1, min(int(limit), 50))
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT * FROM learning_pending_questions
                WHERE user_id = ? AND status = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (user_id, status, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def answer_pending_question(
        self,
        user_id: int,
        question_id: int,
        *,
        answer_text: Optional[str] = None,
        action: Optional[str] = None,
        behavior: Optional[str] = None,
        dismiss: bool = False,
    ) -> bool:
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    """
                    SELECT * FROM learning_pending_questions
                    WHERE id = ? AND user_id = ? AND status = 'open'
                    """,
                    (int(question_id), int(user_id)),
                ).fetchone()
                if not row:
                    return False
                status = "dismissed" if dismiss else "answered"
                conn.execute(
                    """
                    UPDATE learning_pending_questions
                    SET status = ?, answered_at = ?, answer_text = ?
                    WHERE id = ?
                    """,
                    (status, time.time(), answer_text, int(question_id)),
                )
                eid = row["event_id"]
            if not dismiss and eid and (action or behavior or answer_text):
                self.label_event(
                    int(eid),
                    user_id,
                    action=action,
                    behavior=behavior,
                    notes=answer_text,
                    source="human",
                    confidence=1.0,
                )
            return True
        except Exception as e:
            logger.error("answer_pending_question failed: %s", e)
            return False

    def find_open_draft(
        self,
        user_id: int,
        *,
        text: Optional[str] = None,
        evidence_event_id: Optional[int] = None,
        kind: Optional[str] = None,
    ) -> Optional[int]:
        """Return id of open (needs_approval) draft matching text or evidence event."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT id, text, evidence_event_ids_json, kind
                FROM learning_lessons
                WHERE user_id = ? AND needs_approval = 1
                ORDER BY created_at DESC LIMIT 40
                """,
                (int(user_id),),
            ).fetchall()
            for r in rows:
                if kind and (r["kind"] or "") != kind:
                    continue
                if text and (r["text"] or "").strip() == text.strip():
                    return int(r["id"])
                if evidence_event_id is not None:
                    try:
                        ids = json.loads(r["evidence_event_ids_json"] or "[]")
                    except Exception:
                        ids = []
                    if int(evidence_event_id) in [int(x) for x in ids]:
                        return int(r["id"])
            return None

    def teach_lesson(
        self,
        user_id: int,
        text: str,
        *,
        tags: Optional[List[str]] = None,
        needs_approval: bool = False,
        source: str = "owner",
        kind: str = "lesson",
        evidence_event_ids: Optional[List[int]] = None,
        weight: float = 1.0,
        dedupe: bool = True,
    ) -> int:
        body = (text or "").strip()
        if not body:
            return 0
        try:
            evid = list(evidence_event_ids or [])
            if dedupe and needs_approval:
                existing = self.find_open_draft(
                    user_id,
                    text=body,
                    evidence_event_id=int(evid[0]) if evid else None,
                    kind=kind,
                )
                if existing:
                    return int(existing)
            with self._lock:
                cur = self._get_conn().execute(
                    """
                    INSERT INTO learning_lessons (
                        user_id, text, tags_json, weight, needs_approval,
                        source, kind, evidence_event_ids_json, created_at,
                        approved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(user_id),
                        body,
                        json.dumps(tags or []),
                        float(weight),
                        1 if needs_approval else 0,
                        source,
                        kind,
                        json.dumps(evid),
                        time.time(),
                        None if needs_approval else time.time(),
                    ),
                )
                return int(cur.lastrowid)
        except Exception as e:
            logger.error("teach_lesson failed: %s", e)
            return 0

    def list_lessons(
        self,
        user_id: int,
        *,
        pending_only: bool = False,
        approved_only: bool = False,
        limit: int = 30,
    ) -> List[dict]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            sql = "SELECT * FROM learning_lessons WHERE user_id = ?"
            params: List[Any] = [int(user_id)]
            if pending_only:
                sql += " AND needs_approval = 1"
            if approved_only:
                sql += " AND needs_approval = 0"
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            return [dict(r) for r in self._get_conn().execute(sql, params).fetchall()]

    def upsert_setup_case(
        self,
        user_id: int,
        *,
        symbol: str,
        market: str,
        event_id: Optional[int] = None,
        fire_ts: Optional[float] = None,
        fire_price: Optional[float] = None,
        ref_price: Optional[float] = None,
        drop_pct: Optional[float] = None,
        velocity_band: Optional[str] = None,
        heat_breadth: Optional[int] = None,
        features: Optional[Dict[str, Any]] = None,
        chips: Optional[List[str]] = None,
        note: Optional[str] = None,
        lesson_id: Optional[int] = None,
        trade_key: Optional[str] = None,
        source: str = "fire",
    ) -> int:
        """Insert or update a P1 setup case. Returns case id. Soft-fail → 0."""
        try:
            feats = features or {}
            features_ok = 1 if feats.get("ok") else 0
            now = time.time()
            with self._lock:
                conn = self._get_conn()
                existing = None
                if event_id is not None:
                    existing = conn.execute(
                        "SELECT id FROM agent_setup_cases WHERE user_id = ? AND event_id = ?",
                        (int(user_id), int(event_id)),
                    ).fetchone()
                if existing:
                    cid = int(existing["id"])
                    conn.execute(
                        """
                        UPDATE agent_setup_cases SET
                            symbol = ?, market = ?, frozen_at = ?,
                            fire_ts = COALESCE(?, fire_ts),
                            fire_price = COALESCE(?, fire_price),
                            ref_price = COALESCE(?, ref_price),
                            drop_pct = COALESCE(?, drop_pct),
                            velocity_band = COALESCE(?, velocity_band),
                            heat_breadth = COALESCE(?, heat_breadth),
                            features_json = COALESCE(?, features_json),
                            features_ok = CASE WHEN ? = 1 THEN 1 ELSE features_ok END,
                            chips_json = COALESCE(?, chips_json),
                            note = COALESCE(?, note),
                            lesson_id = COALESCE(?, lesson_id),
                            trade_key = COALESCE(?, trade_key),
                            source = ?
                        WHERE id = ?
                        """,
                        (
                            str(symbol).upper(),
                            str(market).lower(),
                            now,
                            fire_ts,
                            fire_price,
                            ref_price,
                            drop_pct,
                            velocity_band,
                            heat_breadth,
                            json.dumps(feats) if feats else None,
                            features_ok,
                            json.dumps(chips) if chips is not None else None,
                            note,
                            lesson_id,
                            trade_key,
                            source,
                            cid,
                        ),
                    )
                    return cid
                cur = conn.execute(
                    """
                    INSERT INTO agent_setup_cases (
                        user_id, event_id, symbol, market, frozen_at,
                        fire_ts, fire_price, ref_price, drop_pct, velocity_band,
                        heat_breadth, features_json, features_ok, chips_json,
                        note, lesson_id, trade_key, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(user_id),
                        int(event_id) if event_id is not None else None,
                        str(symbol).upper(),
                        str(market).lower(),
                        now,
                        fire_ts,
                        fire_price,
                        ref_price,
                        drop_pct,
                        velocity_band,
                        heat_breadth,
                        json.dumps(feats) if feats else None,
                        features_ok,
                        json.dumps(chips or []),
                        note,
                        lesson_id,
                        trade_key,
                        source,
                    ),
                )
                return int(cur.lastrowid)
        except Exception as e:
            logger.error("upsert_setup_case failed: %s", e)
            return 0

    def get_setup_case(
        self,
        user_id: int,
        *,
        case_id: Optional[int] = None,
        event_id: Optional[int] = None,
    ) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            if case_id is not None:
                row = conn.execute(
                    "SELECT * FROM agent_setup_cases WHERE id = ? AND user_id = ?",
                    (int(case_id), int(user_id)),
                ).fetchone()
            elif event_id is not None:
                row = conn.execute(
                    "SELECT * FROM agent_setup_cases WHERE event_id = ? AND user_id = ?",
                    (int(event_id), int(user_id)),
                ).fetchone()
            else:
                return None
            return dict(row) if row else None

    def list_setup_cases(self, user_id: int, *, limit: int = 40) -> List[dict]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM agent_setup_cases WHERE user_id = ? "
                "ORDER BY frozen_at DESC LIMIT ?",
                (int(user_id), limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_position_flags(self, user_id: int) -> List[dict]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM position_flags WHERE user_id = ?",
                (int(user_id),),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_position_flag(
        self,
        user_id: int,
        entity_key: str,
        *,
        symbol: str,
        market: str = "spot",
        free_coins_override: Optional[str] = None,
        free_mark_usd: Optional[float] = None,
        book: Optional[str] = None,
        notes: Optional[str] = None,
        update_free: bool = False,
        update_book: bool = False,
    ) -> dict:
        """Update position flags.

        free_coins_override: 'on' | 'off' | None (clear) when update_free.
        book: 'hold' (long-term invest) | 'ad' (default AD learning) when update_book.
        """
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT * FROM position_flags WHERE user_id = ? AND entity_key = ?",
                (int(user_id), str(entity_key)),
            ).fetchone()
            ex = dict(existing) if existing else {}

            ov = ex.get("free_coins_override")
            free_since = ex.get("free_since_ts")
            mark = ex.get("free_mark_usd")
            if update_free:
                ov = free_coins_override
                if ov is not None:
                    ov = str(ov).lower().strip()
                    if ov in ("", "null", "none", "clear"):
                        ov = None
                    elif ov not in ("on", "off"):
                        raise ValueError("free_coins_override must be on|off|null")
                if ov == "on":
                    free_since = float(free_since) if free_since else now
                    if free_mark_usd is not None:
                        mark = free_mark_usd
                    elif mark is None and free_mark_usd is None:
                        mark = None
                else:
                    free_since = None
                    if free_mark_usd is not None:
                        mark = free_mark_usd

            book_val = ex.get("book") or "ad"
            if update_book:
                b = (book or "ad").lower().strip()
                if b in ("", "null", "none", "clear", "ad", "default"):
                    book_val = "ad"
                elif b in ("hold", "invest", "long", "long_term", "lt"):
                    book_val = "hold"
                else:
                    raise ValueError("book must be hold|ad")

            note_val = notes if notes is not None else ex.get("notes")

            conn.execute(
                """
                INSERT INTO position_flags (
                    user_id, entity_key, symbol, market, free_coins_override,
                    free_since_ts, free_mark_usd, notes, updated_at, book
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, entity_key) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    free_coins_override = excluded.free_coins_override,
                    free_since_ts = excluded.free_since_ts,
                    free_mark_usd = excluded.free_mark_usd,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at,
                    book = excluded.book
                """,
                (
                    int(user_id),
                    str(entity_key),
                    str(symbol).upper(),
                    str(market or "spot").lower(),
                    ov,
                    free_since,
                    mark,
                    note_val,
                    now,
                    book_val,
                ),
            )
            # Mirror hold flag on stable sopen:SYMBOL for spot so entity_key churn keeps it
            if update_book and str(market or "").lower() == "spot":
                alt = f"sopen:{str(symbol).upper().replace('_', '')}"
                if not alt.endswith("USDT") and "USDT" not in str(symbol).upper():
                    alt = f"sopen:{str(symbol).upper()}USDT"
                # Prefer compact spot form without underscore
                base = str(symbol).upper().replace("_", "").replace("-", "")
                alt = f"sopen:{base if base.endswith('USDT') else base + 'USDT'}"
                if alt != str(entity_key):
                    conn.execute(
                        """
                        INSERT INTO position_flags (
                            user_id, entity_key, symbol, market, free_coins_override,
                            free_since_ts, free_mark_usd, notes, updated_at, book
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, entity_key) DO UPDATE SET
                            symbol = excluded.symbol,
                            book = excluded.book,
                            notes = excluded.notes,
                            updated_at = excluded.updated_at
                        """,
                        (
                            int(user_id),
                            alt,
                            str(symbol).upper(),
                            "spot",
                            ov,
                            free_since,
                            mark,
                            note_val,
                            now,
                            book_val,
                        ),
                    )
            row = conn.execute(
                "SELECT * FROM position_flags WHERE user_id = ? AND entity_key = ?",
                (int(user_id), str(entity_key)),
            ).fetchone()
            return dict(row) if row else {}

    def approve_lesson(
        self, user_id: int, lesson_id: int, *, dismiss: bool = False
    ) -> bool:
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    "SELECT id FROM learning_lessons WHERE id = ? AND user_id = ?",
                    (int(lesson_id), int(user_id)),
                ).fetchone()
                if not row:
                    return False
                if dismiss:
                    conn.execute(
                        "DELETE FROM learning_lessons WHERE id = ?",
                        (int(lesson_id),),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE learning_lessons
                        SET needs_approval = 0, approved_at = ?
                        WHERE id = ?
                        """,
                        (time.time(), int(lesson_id)),
                    )
                return True
        except Exception as e:
            logger.error("approve_lesson failed: %s", e)
            return False

    def delete_lesson(self, user_id: int, lesson_id: int) -> bool:
        """Permanently remove a lesson (owner unteach)."""
        return self.approve_lesson(user_id, lesson_id, dismiss=True)

    def get_lesson(self, user_id: int, lesson_id: int) -> Optional[dict]:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM learning_lessons WHERE id = ? AND user_id = ?",
                (int(lesson_id), int(user_id)),
            ).fetchone()
            return dict(row) if row else None

    def normalize_learning_index(self, user_id: int) -> Dict[str, int]:
        """Rewrite legacy sym tags + case symbols to canonical form; stamp buckets.

        Safe / additive. Does not delete lessons or cases.
        """
        from .buckets import (
            OWNER_LESSON_BUCKETS,
            ensure_bucket_in_chips_or_tags,
            infer_case_bucket,
        )
        from .incident import build_incident, incident_tags
        from .symbols import normalize_learning_symbol, rewrite_sym_tags

        n_les = 0
        n_case = 0
        bucket_counts: Dict[str, int] = {}
        try:
            with self._lock:
                conn = self._get_conn()
                # event prices for incident backfill
                ev_by_id: Dict[int, dict] = {}
                try:
                    for er in conn.execute(
                        "SELECT id, ts, price, ref_price, symbol, market "
                        "FROM learning_events WHERE user_id = ?",
                        (int(user_id),),
                    ):
                        ev_by_id[int(er["id"])] = dict(er)
                except Exception:
                    pass

                lessons = conn.execute(
                    "SELECT id, tags_json, text, evidence_event_ids_json, created_at "
                    "FROM learning_lessons WHERE user_id = ?",
                    (int(user_id),),
                ).fetchall()
                for row in lessons:
                    try:
                        tags = json.loads(row["tags_json"] or "[]")
                    except Exception:
                        tags = []
                    mkt = None
                    ev_id = None
                    for t in tags:
                        tl = str(t).lower()
                        if tl.startswith("mkt:"):
                            mkt = str(t).split(":", 1)[-1]
                        if tl.startswith("ev:"):
                            try:
                                ev_id = int(str(t).split(":", 1)[-1])
                            except ValueError:
                                pass
                    if ev_id is None:
                        try:
                            evid = json.loads(
                                row["evidence_event_ids_json"] or "[]"
                            )
                            if evid:
                                ev_id = int(evid[0])
                        except Exception:
                            pass

                    new_tags = rewrite_sym_tags(list(tags), mkt)
                    # Drop old bucket tags; re-stamp
                    new_tags = [
                        t
                        for t in new_tags
                        if not str(t).lower().startswith("bucket:")
                        and not str(t).lower().startswith("ts:")
                        and not str(t).lower().startswith("px:")
                    ]
                    chips = [t for t in new_tags if ":" not in str(t)]
                    lid = int(row["id"])
                    explicit = OWNER_LESSON_BUCKETS.get(lid)
                    new_tags = ensure_bucket_in_chips_or_tags(
                        new_tags,
                        chips=chips,
                        note=row["text"],
                        explicit=explicit,
                    )
                    # Incident from linked fire when possible
                    fire_ts = None
                    fire_px = None
                    if ev_id and ev_id in ev_by_id:
                        fire_ts = ev_by_id[ev_id].get("ts")
                        fire_px = ev_by_id[ev_id].get("price")
                        if not mkt and ev_by_id[ev_id].get("market"):
                            mkt = ev_by_id[ev_id]["market"]
                            new_tags = [
                                t
                                for t in new_tags
                                if not str(t).lower().startswith("mkt:")
                            ]
                            new_tags.append(f"mkt:{str(mkt).lower()}")
                    if fire_ts is None:
                        fire_ts = row["created_at"]
                    inc = build_incident(
                        incident_ts=fire_ts,
                        incident_price=fire_px,
                        event_id=ev_id,
                    )
                    new_tags.extend(incident_tags(inc))
                    # de-dupe preserve order
                    seen = set()
                    deduped = []
                    for t in new_tags:
                        if t not in seen:
                            seen.add(t)
                            deduped.append(t)
                    new_tags = deduped
                    b = explicit or infer_case_bucket(
                        chips=chips, note=row["text"], explicit=explicit
                    )
                    bucket_counts[b] = bucket_counts.get(b, 0) + 1
                    conn.execute(
                        "UPDATE learning_lessons SET tags_json = ? WHERE id = ?",
                        (json.dumps(new_tags), lid),
                    )
                    n_les += 1

                cases = conn.execute(
                    "SELECT id, symbol, market, chips_json, note, features_json, "
                    "fire_ts, fire_price, event_id, lesson_id "
                    "FROM agent_setup_cases WHERE user_id = ?",
                    (int(user_id),),
                ).fetchall()
                for row in cases:
                    mkt = (row["market"] or "futures").lower()
                    sym = normalize_learning_symbol(row["symbol"] or "", mkt)
                    try:
                        chips = json.loads(row["chips_json"] or "[]")
                    except Exception:
                        chips = []
                    try:
                        feats = json.loads(row["features_json"] or "{}")
                    except Exception:
                        feats = {}
                    if not isinstance(feats, dict):
                        feats = {}
                    explicit = None
                    if row["lesson_id"] is not None:
                        explicit = OWNER_LESSON_BUCKETS.get(int(row["lesson_id"]))
                    bucket = infer_case_bucket(
                        chips=chips,
                        features=feats,
                        note=row["note"],
                        explicit=explicit,
                    )
                    feats["bucket"] = bucket
                    fire_ts = row["fire_ts"]
                    fire_px = row["fire_price"]
                    if fire_ts is None and row["event_id"] and int(row["event_id"]) in ev_by_id:
                        fire_ts = ev_by_id[int(row["event_id"])].get("ts")
                        fire_px = fire_px or ev_by_id[int(row["event_id"])].get(
                            "price"
                        )
                    if fire_ts is not None:
                        feats["incident_ts"] = fire_ts
                        feats["incident_price"] = fire_px
                        feats["incident"] = {
                            "ts": fire_ts,
                            "price": fire_px,
                            "event_id": row["event_id"],
                            "chart_tfs": ["5m", "15m", "1h"],
                            "chart_lookback_seconds": 6 * 3600,
                            "anchor": "fire",
                        }
                    conn.execute(
                        "UPDATE agent_setup_cases SET symbol = ?, features_json = ?, "
                        "fire_ts = COALESCE(fire_ts, ?), "
                        "fire_price = COALESCE(fire_price, ?) "
                        "WHERE id = ?",
                        (
                            sym,
                            json.dumps(feats),
                            fire_ts,
                            fire_px,
                            int(row["id"]),
                        ),
                    )
                    n_case += 1
            return {
                "lessons_rewritten": n_les,
                "cases_touched": n_case,
                "bucket_counts": bucket_counts,
            }
        except Exception as e:
            logger.error("normalize_learning_index failed: %s", e)
            return {
                "lessons_rewritten": n_les,
                "cases_touched": n_case,
                "bucket_counts": bucket_counts,
                "error": str(e),
            }

    def update_lesson(
        self,
        user_id: int,
        lesson_id: int,
        *,
        text: Optional[str] = None,
        tags: Optional[List[str]] = None,
        weight: Optional[float] = None,
    ) -> Optional[dict]:
        """Owner edit of a durable lesson (text and/or tags). Returns updated row or None."""
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    "SELECT * FROM learning_lessons WHERE id = ? AND user_id = ?",
                    (int(lesson_id), int(user_id)),
                ).fetchone()
                if not row:
                    return None
                new_text = (text if text is not None else row["text"]) or ""
                new_text = str(new_text).strip()
                if not new_text:
                    raise ValueError("Lesson text cannot be empty")
                if tags is not None:
                    tags_json = json.dumps(list(tags))
                else:
                    tags_json = row["tags_json"]
                new_weight = float(weight) if weight is not None else float(row["weight"] or 1.0)
                # Optional updated_at column (additive)
                cols = {
                    str(r[1])
                    for r in conn.execute("PRAGMA table_info(learning_lessons)").fetchall()
                }
                if "updated_at" not in cols:
                    conn.execute(
                        "ALTER TABLE learning_lessons ADD COLUMN updated_at REAL"
                    )
                conn.execute(
                    """
                    UPDATE learning_lessons
                    SET text = ?, tags_json = ?, weight = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        new_text,
                        tags_json,
                        new_weight,
                        time.time(),
                        int(lesson_id),
                        int(user_id),
                    ),
                )
                out = conn.execute(
                    "SELECT * FROM learning_lessons WHERE id = ? AND user_id = ?",
                    (int(lesson_id), int(user_id)),
                ).fetchone()
                return dict(out) if out else None
        except ValueError:
            raise
        except Exception as e:
            logger.error("update_lesson failed: %s", e)
            return None

    def learning_stats(self, user_id: int) -> Dict[str, Any]:
        """Aggregate stats for coach/desk — store-backed only."""
        with self._lock:
            conn = self._get_conn()
            n_events = conn.execute(
                "SELECT COUNT(*) AS c FROM learning_events WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            events_n = int(n_events["c"] if n_events else 0)
            # latest action per event
            rows = conn.execute(
                """
                SELECT e.velocity_band,
                    (SELECT action FROM learning_labels l
                     WHERE l.event_id = e.id AND l.action IS NOT NULL
                     ORDER BY l.ts DESC LIMIT 1) AS action,
                    (SELECT behavior FROM learning_labels l
                     WHERE l.event_id = e.id AND l.behavior IS NOT NULL
                     ORDER BY l.ts DESC LIMIT 1) AS behavior
                FROM learning_events e
                WHERE e.user_id = ?
                """,
                (user_id,),
            ).fetchall()
            took = skip = partial = late = panic = 0
            by_band: Dict[str, Dict[str, int]] = {}
            behaviors: Dict[str, int] = {}
            for r in rows:
                act = r["action"]
                band = r["velocity_band"] or "—"
                if act == "took":
                    took += 1
                elif act == "skip":
                    skip += 1
                elif act == "partial":
                    partial += 1
                elif act == "late":
                    late += 1
                if band == "PANIC":
                    panic += 1
                by_band.setdefault(band, {"took": 0, "skip": 0, "n": 0})
                by_band[band]["n"] += 1
                if act in ("took", "skip"):
                    by_band[band][act] = by_band[band].get(act, 0) + 1
                beh = r["behavior"]
                if beh:
                    behaviors[beh] = behaviors.get(beh, 0) + 1
            bounce_rows = conn.execute(
                """
                SELECT o.max_bounce_pct FROM learning_outcomes o
                JOIN learning_events e ON e.id = o.event_id
                WHERE e.user_id = ? AND o.max_bounce_pct IS NOT NULL
                """,
                (user_id,),
            ).fetchall()
            bounces = [
                float(r["max_bounce_pct"])
                for r in bounce_rows
                if r["max_bounce_pct"] is not None
            ]
            median_bounce = None
            if bounces:
                bounces.sort()
                median_bounce = bounces[len(bounces) // 2]
            pending_n = conn.execute(
                """
                SELECT COUNT(*) AS c FROM learning_pending_questions
                WHERE user_id = ? AND status = 'open'
                """,
                (user_id,),
            ).fetchone()
            drafts_n = conn.execute(
                """
                SELECT COUNT(*) AS c FROM learning_lessons
                WHERE user_id = ? AND needs_approval = 1
                """,
                (user_id,),
            ).fetchone()
            lessons_n = conn.execute(
                """
                SELECT COUNT(*) AS c FROM learning_lessons
                WHERE user_id = ? AND needs_approval = 0
                """,
                (user_id,),
            ).fetchone()
            return {
                "events": events_n,
                "took": took,
                "skip": skip,
                "partial": partial,
                "late": late,
                "panic_band": panic,
                "by_band": by_band,
                "behaviors": behaviors,
                "median_bounce_pct": median_bounce,
                "outcome_n": len(bounces),
                "pending_questions": int(pending_n["c"] if pending_n else 0),
                "pending_drafts": int(drafts_n["c"] if drafts_n else 0),
                "approved_lessons": int(lessons_n["c"] if lessons_n else 0),
            }

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

    def insert_fill(
        self,
        *,
        user_id: int,
        exchange_trade_id: str,
        symbol: str,
        market: str,
        side: str,
        price: float,
        qty: float,
        quote_qty: Optional[float] = None,
        ts: float,
        raw: Optional[dict] = None,
    ) -> bool:
        """Insert fill if new. Returns True if inserted."""
        try:
            with self._lock:
                conn = self._get_conn()
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO journal_fills (
                        user_id, exchange_trade_id, symbol, market, side,
                        price, qty, quote_qty, ts, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        str(exchange_trade_id),
                        symbol,
                        market,
                        side,
                        price,
                        qty,
                        quote_qty,
                        ts,
                        json.dumps(raw) if raw else None,
                    ),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("insert_fill failed: %s", e)
            return False

    def recent_fills(self, user_id: int, limit: int = 20) -> List[dict]:
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT * FROM journal_fills
                WHERE user_id = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (user_id, max(1, min(limit, 2000))),
            ).fetchall()
            return [dict(r) for r in rows]

    def fills_for_symbol(
        self, user_id: int, symbol: str, *, limit: int = 500
    ) -> List[dict]:
        """All fills for a symbol (any underscore/compact form)."""
        from .engagement import symbols_match

        rows = self.recent_fills(user_id, limit=limit)
        return [r for r in rows if symbols_match(symbol, r.get("symbol") or "")]

    def symbols_for_fill_sync(self, user_id: int) -> List[str]:
        """Spot-compact symbols for myTrades (BTCUSDT form)."""
        with self._lock:
            conn = self._get_conn()
            syms = set()
            for row in conn.execute(
                "SELECT symbol, market FROM journal_trades WHERE user_id = ?",
                (user_id,),
            ):
                m = (row["market"] or "spot").lower()
                if m == "futures":
                    continue
                syms.add(str(row["symbol"]).upper().replace("_", ""))
            for row in conn.execute(
                """
                SELECT DISTINCT symbol FROM journal_fills
                WHERE user_id = ? AND market = 'spot' ORDER BY ts DESC LIMIT 80
                """,
                (user_id,),
            ):
                syms.add(str(row["symbol"]).upper().replace("_", ""))
            for row in conn.execute(
                """
                SELECT symbol, market FROM learning_events
                WHERE user_id = ? ORDER BY ts DESC LIMIT 50
                """,
                (user_id,),
            ):
                if (row["market"] or "").lower() == "futures":
                    continue
                s = str(row["symbol"]).upper().replace("_", "")
                if "USDT" in s:
                    syms.add(s)
            return sorted(syms)

    def futures_symbols_for_fill_sync(self, user_id: int) -> List[str]:
        """Futures contract symbols in BASE_USDT form."""
        from ..exchange_private import normalize_futures_symbol

        with self._lock:
            conn = self._get_conn()
            syms = set()
            for row in conn.execute(
                """
                SELECT DISTINCT symbol FROM journal_fills
                WHERE user_id = ? AND market = 'futures' ORDER BY ts DESC LIMIT 80
                """,
                (user_id,),
            ):
                s = normalize_futures_symbol(str(row["symbol"]))
                if s:
                    syms.add(s)
            for row in conn.execute(
                """
                SELECT symbol, market FROM learning_events
                WHERE user_id = ? AND market = 'futures' ORDER BY ts DESC LIMIT 50
                """,
                (user_id,),
            ):
                s = normalize_futures_symbol(str(row["symbol"]))
                if s:
                    syms.add(s)
            return sorted(syms)

    def purge_auto_journal_trades(self, user_id: int) -> int:
        """Delete auto-generated journal rows that polluted position history."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """
                DELETE FROM journal_trades
                WHERE user_id = ?
                  AND (
                    notes LIKE '%auto from MEXC fill%'
                    OR notes LIKE '%auto close from MEXC fill%'
                  )
                """,
                (user_id,),
            )
            return int(cur.rowcount or 0)

    def upsert_journal_from_fill(self, fill: dict) -> None:
        """Open journal on buy; close on sell if open exists (heuristic)."""
        user_id = int(fill["user_id"])
        symbol = fill["symbol"]
        market = fill.get("market") or "spot"
        side = (fill.get("side") or "").lower()
        price = float(fill["price"])
        if side == "buy":
            opens = self.journal_list(user_id, open_only=True)
            for t in opens:
                if str(t["symbol"]).upper().replace("_", "") == symbol.replace("_", ""):
                    return  # already open
            self.journal_open(
                user_id,
                symbol,
                market,
                entry_avg=price,
                notes="auto from MEXC fill",
            )
        elif side == "sell":
            self.journal_close(
                user_id,
                symbol=symbol,
                exit_avg=price,
                notes="auto close from MEXC fill",
            )
