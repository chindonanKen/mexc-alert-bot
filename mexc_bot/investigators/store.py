"""Investigation results + source reliability learning (cause → effect).

Same SQLite file as alerts; never touches alerts rows.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InvestigatorStore:
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
            CREATE TABLE IF NOT EXISTS delist_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                base TEXT,
                title TEXT NOT NULL,
                url TEXT,
                kind TEXT NOT NULL,
                ts REAL NOT NULL,
                fingerprint TEXT UNIQUE,
                raw_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_delist_base ON delist_cache (base, ts DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS investigations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_id INTEGER,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                drop_pct REAL,
                velocity_band TEXT,
                heat_breadth INTEGER,
                verdict TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence_json TEXT,
                ts REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inv_user_ts ON investigations (user_id, ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inv_event ON investigations (event_id)"
        )
        # Expert source scores: how often a source+kind co-occurred with real dumps
        # and later bounce quality (updated by outcome learning).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_expertise (
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0,
                confirmed_moves INTEGER NOT NULL DEFAULT 0,
                false_alarms INTEGER NOT NULL DEFAULT 0,
                bounce_sum REAL NOT NULL DEFAULT 0,
                bounce_n INTEGER NOT NULL DEFAULT 0,
                weight REAL NOT NULL DEFAULT 1.0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (source, kind)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS investigation_outcomes (
                investigation_id INTEGER PRIMARY KEY,
                event_id INTEGER,
                horizon_seconds INTEGER,
                max_bounce_pct REAL,
                max_dd_pct REAL,
                scored_at REAL NOT NULL
            )
            """
        )

    def upsert_delist(
        self,
        *,
        exchange: str,
        base: Optional[str],
        title: str,
        url: Optional[str],
        kind: str,
        ts: float,
        fingerprint: str,
        raw: Optional[dict] = None,
    ) -> int:
        with self._lock:
            try:
                cur = self._get_conn().execute(
                    """
                    INSERT INTO delist_cache (
                        exchange, base, title, url, kind, ts, fingerprint, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        ts = excluded.ts,
                        title = excluded.title
                    """,
                    (
                        exchange,
                        base,
                        title[:500],
                        url,
                        kind,
                        ts,
                        fingerprint,
                        json.dumps(raw) if raw else None,
                    ),
                )
                return int(cur.lastrowid or 0)
            except Exception as e:
                logger.error("delist_cache upsert failed: %s", e)
                return 0

    def find_delists_for_base(
        self, base: str, *, within_seconds: float = 14 * 86400, limit: int = 20
    ) -> List[dict]:
        cutoff = time.time() - within_seconds
        b = (base or "").upper().strip()
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT * FROM delist_cache
                WHERE ts >= ? AND (
                    UPPER(base) = ? OR UPPER(title) LIKE ?
                )
                ORDER BY ts DESC LIMIT ?
                """,
                (cutoff, b, f"%{b}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_delists(self, limit: int = 30) -> List[dict]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM delist_cache ORDER BY ts DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_delist_announcements(self, *, limit: int = 40) -> List[dict]:
        """Group delist_cache by (exchange, title) so all tickers on one notice are visible.

        DB stores one row per base; the desk/intel UI must present the full set.
        """
        limit = max(1, min(int(limit), 80))
        with self._lock:
            # Pull enough rows to form complete announcement groups
            rows = self._get_conn().execute(
                "SELECT * FROM delist_cache ORDER BY ts DESC LIMIT ?",
                (min(500, limit * 12),),
            ).fetchall()
        groups: Dict[str, dict] = {}
        order: List[str] = []
        for r in rows:
            d = dict(r)
            title = (d.get("title") or "").strip()
            exchange = (d.get("exchange") or "").strip().lower()
            key = f"{exchange}|{title}"
            base = (d.get("base") or "").strip().upper() or None
            if key not in groups:
                groups[key] = {
                    "exchange": exchange,
                    "title": title,
                    "kind": d.get("kind") or "delist",
                    "url": d.get("url"),
                    "ts": float(d.get("ts") or 0),
                    "bases": [],
                    "n_bases": 0,
                }
                order.append(key)
            g = groups[key]
            if base and base not in g["bases"]:
                g["bases"].append(base)
            # keep newest ts
            try:
                g["ts"] = max(float(g["ts"] or 0), float(d.get("ts") or 0))
            except (TypeError, ValueError):
                pass
            if d.get("url") and not g.get("url"):
                g["url"] = d.get("url")
        out = []
        for key in order:
            g = groups[key]
            g["bases"] = sorted(g["bases"])
            g["n_bases"] = len(g["bases"])
            g["bases_text"] = ", ".join(g["bases"]) if g["bases"] else "—"
            out.append(g)
            if len(out) >= limit:
                break
        return out

    def bases_for_delist_title(
        self, exchange: str, title: str, *, limit: int = 40
    ) -> List[str]:
        """All bases stored for the same announcement title."""
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT DISTINCT base FROM delist_cache
                WHERE LOWER(exchange) = LOWER(?) AND title = ?
                  AND base IS NOT NULL AND TRIM(base) != ''
                ORDER BY base ASC LIMIT ?
                """,
                (exchange or "", title or "", max(1, min(int(limit), 80)),
                ),
            ).fetchall()
            return [str(r["base"]).upper() for r in rows if r["base"]]

    def save_investigation(
        self,
        *,
        user_id: int,
        event_id: Optional[int],
        symbol: str,
        market: str,
        drop_pct: Optional[float],
        velocity_band: Optional[str],
        heat_breadth: Optional[int],
        verdict: str,
        confidence: float,
        evidence: List[dict],
    ) -> int:
        with self._lock:
            cur = self._get_conn().execute(
                """
                INSERT INTO investigations (
                    user_id, event_id, symbol, market, drop_pct, velocity_band,
                    heat_breadth, verdict, confidence, evidence_json, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    event_id,
                    symbol,
                    market,
                    drop_pct,
                    velocity_band,
                    heat_breadth,
                    verdict,
                    confidence,
                    json.dumps(evidence),
                    time.time(),
                ),
            )
            iid = int(cur.lastrowid)
            # Bump hit counts for sources cited
            for ev in evidence:
                src = str(ev.get("source") or "unknown")
                kind = str(ev.get("kind") or "unknown")
                self._bump_source(src, kind, hit=True)
            return iid

    def _bump_source(self, source: str, kind: str, *, hit: bool = False) -> None:
        conn = self._get_conn()
        now = time.time()
        row = conn.execute(
            "SELECT hits, weight FROM source_expertise WHERE source = ? AND kind = ?",
            (source, kind),
        ).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO source_expertise (
                    source, kind, hits, confirmed_moves, false_alarms,
                    bounce_sum, bounce_n, weight, updated_at
                ) VALUES (?, ?, ?, 0, 0, 0, 0, 1.0, ?)
                """,
                (source, kind, 1 if hit else 0, now),
            )
            return
        conn.execute(
            """
            UPDATE source_expertise
            SET hits = hits + ?, updated_at = ?
            WHERE source = ? AND kind = ?
            """,
            (1 if hit else 0, now, source, kind),
        )

    def get_source_weight(self, source: str, kind: str) -> float:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT weight FROM source_expertise WHERE source = ? AND kind = ?",
                (source, kind),
            ).fetchone()
            if not row:
                return 1.0
            return float(row["weight"])

    def record_investigation_outcome(
        self,
        investigation_id: int,
        *,
        event_id: Optional[int],
        horizon_seconds: int,
        max_bounce_pct: Optional[float],
        max_dd_pct: Optional[float],
        verdict: str,
        evidence: List[dict],
    ) -> None:
        """
        Learn cause→effect:
        - NEWS_RELATED + weak bounce / more dump → confirmed_moves (source was right)
        - NEWS_RELATED + strong bounce → false_alarms (or lower weight)
        - NO_NEWS + continued dump → neutral / slight noise
        """
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO investigation_outcomes (
                    investigation_id, event_id, horizon_seconds,
                    max_bounce_pct, max_dd_pct, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    investigation_id,
                    event_id,
                    horizon_seconds,
                    max_bounce_pct,
                    max_dd_pct,
                    time.time(),
                ),
            )
            bounce = max_bounce_pct if max_bounce_pct is not None else 0.0
            dd = max_dd_pct if max_dd_pct is not None else 0.0
            # "Confirmed toxic news" if little bounce and further weakness
            toxic = bounce < 2.0 and dd <= -1.0
            benign = bounce >= 5.0

            for ev in evidence:
                src = str(ev.get("source") or "unknown")
                kind = str(ev.get("kind") or "unknown")
                row = conn.execute(
                    "SELECT * FROM source_expertise WHERE source = ? AND kind = ?",
                    (src, kind),
                ).fetchone()
                if not row:
                    self._bump_source(src, kind, hit=True)
                    row = conn.execute(
                        "SELECT * FROM source_expertise WHERE source = ? AND kind = ?",
                        (src, kind),
                    ).fetchone()
                if not row:
                    continue
                confirmed = int(row["confirmed_moves"])
                false_a = int(row["false_alarms"])
                bsum = float(row["bounce_sum"])
                bn = int(row["bounce_n"])
                if verdict in ("NEWS_RELATED", "LIKELY_NEWS") and evidence:
                    if toxic:
                        confirmed += 1
                    elif benign:
                        false_a += 1
                bsum += bounce
                bn += 1
                # weight: more confirmed toxic → higher; more false → lower
                total = max(1, confirmed + false_a)
                weight = 0.5 + (confirmed / total)  # 0.5 .. 1.5
                weight = max(0.25, min(2.0, weight))
                conn.execute(
                    """
                    UPDATE source_expertise SET
                        confirmed_moves = ?,
                        false_alarms = ?,
                        bounce_sum = ?,
                        bounce_n = ?,
                        weight = ?,
                        updated_at = ?
                    WHERE source = ? AND kind = ?
                    """,
                    (confirmed, false_a, bsum, bn, weight, time.time(), src, kind),
                )

    def pending_outcome_links(self, horizon_seconds: int = 14400, limit: int = 50) -> List[dict]:
        """Investigations with event_id old enough and not yet scored for horizon."""
        cutoff = time.time() - horizon_seconds
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT i.* FROM investigations i
                LEFT JOIN investigation_outcomes o
                  ON o.investigation_id = i.id AND o.horizon_seconds = ?
                WHERE o.investigation_id IS NULL
                  AND i.ts <= ?
                  AND i.event_id IS NOT NULL
                ORDER BY i.ts ASC
                LIMIT ?
                """,
                (horizon_seconds, cutoff, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["evidence"] = json.loads(d.get("evidence_json") or "[]")
                except Exception:
                    d["evidence"] = []
                out.append(d)
            return out

    def recent_investigations(self, user_id: int, limit: int = 15) -> List[dict]:
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT * FROM investigations
                WHERE user_id = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (user_id, max(1, min(limit, 50))),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["evidence"] = json.loads(d.get("evidence_json") or "[]")
                except Exception:
                    d["evidence"] = []
                out.append(d)
            return out

    def top_sources(self, limit: int = 15) -> List[dict]:
        with self._lock:
            rows = self._get_conn().execute(
                """
                SELECT * FROM source_expertise
                ORDER BY weight DESC, confirmed_moves DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
