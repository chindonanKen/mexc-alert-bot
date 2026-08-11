"""SQLite tables for mover scanner settings + watchlist + named sets.

Uses the same DB file as AlertStore but never touches the alerts table.

Multiple **sets** per user: each has its own name, on/off, threshold %, lookback,
and coin list. Legacy single-row mover_settings is mirrored to the Default set.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from threading import RLock
from typing import List, Optional

from mexc_bot.db_safety import SchemaSafetyError, ensure_column, safe_rebuild_table

logger = logging.getLogger(__name__)

DEFAULT_SET_NAME = "Default"


class MoverStore:
    """Per-user mover sets (settings + watchlist). Isolated from target-price alerts."""

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mover_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                threshold_percent REAL NOT NULL,
                lookback_seconds INTEGER NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, name)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mover_sets_user ON mover_sets (user_id)"
        )
        # Additive set_id on watchlist (nullable until backfill) — never drop data
        ensure_column(conn, "mover_watchlist", "set_id", "INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mover_watch_set ON mover_watchlist (set_id)"
        )
        self._migrate_legacy_to_sets(conn)
        self._migrate_watchlist_pk(conn)
        self._migrate_spot_bare_bases(conn)

    def _migrate_spot_bare_bases(self, conn: sqlite3.Connection) -> None:
        """Spot book ids are BASEUSDT. Fix rows stored as bare bases (OXT → OXTUSDT)."""
        try:
            rows = conn.execute(
                "SELECT user_id, symbol, market, set_id FROM mover_watchlist "
                "WHERE lower(market) = 'spot'"
            ).fetchall()
            for r in rows:
                sym = str(r["symbol"] or "").upper().strip()
                if not sym or sym.endswith("USDT") or sym.endswith("USDC"):
                    continue
                # Skip if already pair-like with underscore non-usdt (rare)
                if "_" in sym:
                    continue
                new_sym = sym + "USDT"
                set_id = r["set_id"]
                uid = int(r["user_id"])
                # Avoid unique conflict if both bare and full exist
                if set_id is None:
                    exists = conn.execute(
                        "SELECT 1 FROM mover_watchlist WHERE user_id = ? AND symbol = ? "
                        "AND market = 'spot' AND set_id IS NULL",
                        (uid, new_sym),
                    ).fetchone()
                    if exists:
                        conn.execute(
                            "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ? "
                            "AND market = 'spot' AND set_id IS NULL",
                            (uid, sym),
                        )
                    else:
                        conn.execute(
                            "UPDATE mover_watchlist SET symbol = ? "
                            "WHERE user_id = ? AND symbol = ? AND market = 'spot' "
                            "AND set_id IS NULL",
                            (new_sym, uid, sym),
                        )
                else:
                    exists = conn.execute(
                        "SELECT 1 FROM mover_watchlist WHERE user_id = ? AND symbol = ? "
                        "AND market = 'spot' AND set_id = ?",
                        (uid, new_sym, int(set_id)),
                    ).fetchone()
                    if exists:
                        conn.execute(
                            "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ? "
                            "AND market = 'spot' AND set_id = ?",
                            (uid, sym, int(set_id)),
                        )
                    else:
                        conn.execute(
                            "UPDATE mover_watchlist SET symbol = ? "
                            "WHERE user_id = ? AND symbol = ? AND market = 'spot' "
                            "AND set_id = ?",
                            (new_sym, uid, sym, int(set_id)),
                        )
        except Exception as e:
            logger.warning("spot bare base migration: %s", e)

    def _migrate_watchlist_pk(self, conn: sqlite3.Connection) -> None:
        """Allow the same symbol in multiple sets: PK (set_id, symbol, market).

        Uses :func:`mexc_bot.db_safety.safe_rebuild_table` — never drops the live
        table unless every source row was copied (abort on any row loss).
        """
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='mover_watchlist'"
        ).fetchone()
        if not row:
            # Table missing (failed prior migration) — recreate empty shell only
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mover_watchlist (
                    user_id INTEGER NOT NULL,
                    set_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'futures',
                    PRIMARY KEY (set_id, symbol, market)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mover_watch_user ON mover_watchlist (user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mover_watch_set ON mover_watchlist (set_id)"
            )
            return
        sql = (row["sql"] if row else "") or ""
        compact = sql.replace(" ", "")
        if "PRIMARY KEY (set_id, symbol, market)" in compact:
            return
        if "PRIMARY KEY (set_id" in compact and "symbol, market)" in compact:
            return
        self._migrate_legacy_to_sets(conn)

        def _indexes(c: sqlite3.Connection) -> None:
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_mover_watch_user ON mover_watchlist (user_id)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_mover_watch_set ON mover_watchlist (set_id)"
            )

        try:
            safe_rebuild_table(
                conn,
                table="mover_watchlist",
                create_new_ddl="""
                CREATE TABLE mover_watchlist_new (
                    user_id INTEGER NOT NULL,
                    set_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'futures',
                    PRIMARY KEY (set_id, symbol, market)
                )
                """,
                # INSERT OR IGNORE only collapses true PK duplicates; count must still
                # satisfy after >= before or rebuild aborts (no silent wipe).
                copy_sql="""
                INSERT OR IGNORE INTO mover_watchlist_new (user_id, set_id, symbol, market)
                SELECT user_id, COALESCE(set_id, (
                    SELECT id FROM mover_sets ms
                    WHERE ms.user_id = mover_watchlist.user_id
                    ORDER BY sort_order ASC, id ASC LIMIT 1
                )), symbol, market FROM mover_watchlist
                WHERE symbol IS NOT NULL AND symbol != ''
                """,
                after_swap=_indexes,
            )
        except SchemaSafetyError as e:
            logger.error("mover_watchlist PK migration aborted (data preserved): %s", e)
            return

    def _migrate_legacy_to_sets(self, conn: sqlite3.Connection) -> None:
        """Ensure every user with settings/watchlist has a Default set + set_id backfill."""
        user_ids = set()
        for r in conn.execute("SELECT user_id FROM mover_settings"):
            user_ids.add(int(r["user_id"]))
        for r in conn.execute("SELECT DISTINCT user_id FROM mover_watchlist"):
            user_ids.add(int(r["user_id"]))
        for r in conn.execute("SELECT DISTINCT user_id FROM mover_sets"):
            user_ids.add(int(r["user_id"]))

        for uid in user_ids:
            row = conn.execute(
                "SELECT id FROM mover_sets WHERE user_id = ? AND name = ?",
                (uid, DEFAULT_SET_NAME),
            ).fetchone()
            if row:
                set_id = int(row["id"])
            else:
                st = conn.execute(
                    "SELECT enabled, threshold_percent, lookback_seconds "
                    "FROM mover_settings WHERE user_id = ?",
                    (uid,),
                ).fetchone()
                if st:
                    enabled = int(st["enabled"] or 0)
                    thr = float(st["threshold_percent"])
                    lb = int(st["lookback_seconds"])
                else:
                    enabled, thr, lb = 0, 5.0, 900
                cur = conn.execute(
                    "INSERT INTO mover_sets "
                    "(user_id, name, enabled, threshold_percent, lookback_seconds, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (uid, DEFAULT_SET_NAME, enabled, thr, lb),
                )
                set_id = int(cur.lastrowid)

            conn.execute(
                "UPDATE mover_watchlist SET set_id = ? "
                "WHERE user_id = ? AND (set_id IS NULL OR set_id = 0)",
                (set_id, uid),
            )

    def _ensure_default_set(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        default_threshold: float = 5.0,
        default_lookback: int = 900,
    ) -> int:
        row = conn.execute(
            "SELECT id FROM mover_sets WHERE user_id = ? AND name = ?",
            (user_id, DEFAULT_SET_NAME),
        ).fetchone()
        if row:
            return int(row["id"])
        st = conn.execute(
            "SELECT enabled, threshold_percent, lookback_seconds FROM mover_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if st:
            enabled = int(st["enabled"] or 0)
            thr = float(st["threshold_percent"])
            lb = int(st["lookback_seconds"])
        else:
            enabled, thr, lb = 0, float(default_threshold), int(default_lookback)
        cur = conn.execute(
            "INSERT INTO mover_sets "
            "(user_id, name, enabled, threshold_percent, lookback_seconds, sort_order) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (user_id, DEFAULT_SET_NAME, enabled, thr, lb),
        )
        return int(cur.lastrowid)

    def _mirror_settings_from_default(self, conn: sqlite3.Connection, user_id: int) -> None:
        """Keep legacy mover_settings in sync with Default set (Telegram + old desk)."""
        row = conn.execute(
            "SELECT enabled, threshold_percent, lookback_seconds FROM mover_sets "
            "WHERE user_id = ? AND name = ?",
            (user_id, DEFAULT_SET_NAME),
        ).fetchone()
        if not row:
            # Any enabled set → settings on
            any_en = conn.execute(
                "SELECT enabled, threshold_percent, lookback_seconds FROM mover_sets "
                "WHERE user_id = ? ORDER BY sort_order, id LIMIT 1",
                (user_id,),
            ).fetchone()
            if not any_en:
                return
            row = any_en
        existing = conn.execute(
            "SELECT 1 FROM mover_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE mover_settings SET enabled = ?, threshold_percent = ?, "
                "lookback_seconds = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (
                    int(row["enabled"]),
                    float(row["threshold_percent"]),
                    int(row["lookback_seconds"]),
                    user_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO mover_settings (user_id, enabled, threshold_percent, lookback_seconds) "
                "VALUES (?, ?, ?, ?)",
                (
                    user_id,
                    int(row["enabled"]),
                    float(row["threshold_percent"]),
                    int(row["lookback_seconds"]),
                ),
            )

    def _set_row(self, conn: sqlite3.Connection, set_id: int) -> Optional[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM mover_sets WHERE id = ?", (set_id,)
        ).fetchone()

    def list_sets(self, user_id: int) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            self._ensure_default_set(conn, user_id)
            rows = conn.execute(
                "SELECT id, user_id, name, enabled, threshold_percent, lookback_seconds, "
                "sort_order FROM mover_sets WHERE user_id = ? ORDER BY sort_order ASC, id ASC",
                (user_id,),
            ).fetchall()
            out = []
            for r in rows:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM mover_watchlist WHERE set_id = ?",
                    (int(r["id"]),),
                ).fetchone()["c"]
                out.append(
                    {
                        "id": int(r["id"]),
                        "user_id": int(r["user_id"]),
                        "name": r["name"],
                        "enabled": bool(r["enabled"]),
                        "threshold_percent": float(r["threshold_percent"]),
                        "lookback_seconds": int(r["lookback_seconds"]),
                        "sort_order": int(r["sort_order"] or 0),
                        "watch_count": int(n),
                    }
                )
            return out

    def create_set(
        self,
        user_id: int,
        name: str,
        *,
        threshold_percent: float = 5.0,
        lookback_seconds: int = 900,
        enabled: bool = False,
    ) -> dict:
        name = (name or "").strip() or "Set"
        if len(name) > 40:
            name = name[:40]
        with self._lock:
            conn = self._get_conn()
            self._ensure_default_set(conn, user_id, threshold_percent, lookback_seconds)
            max_ord = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) AS m FROM mover_sets WHERE user_id = ?",
                (user_id,),
            ).fetchone()["m"]
            try:
                cur = conn.execute(
                    "INSERT INTO mover_sets "
                    "(user_id, name, enabled, threshold_percent, lookback_seconds, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        name,
                        1 if enabled else 0,
                        float(threshold_percent),
                        int(lookback_seconds),
                        int(max_ord) + 1,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Set name already exists: {name}") from e
            sid = int(cur.lastrowid)
            return self.get_set(user_id, sid)  # type: ignore[return-value]

    def get_set(self, user_id: int, set_id: int) -> Optional[dict]:
        with self._lock:
            conn = self._get_conn()
            r = conn.execute(
                "SELECT * FROM mover_sets WHERE id = ? AND user_id = ?",
                (set_id, user_id),
            ).fetchone()
            if not r:
                return None
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM mover_watchlist WHERE set_id = ?",
                (set_id,),
            ).fetchone()["c"]
            return {
                "id": int(r["id"]),
                "user_id": int(r["user_id"]),
                "name": r["name"],
                "enabled": bool(r["enabled"]),
                "threshold_percent": float(r["threshold_percent"]),
                "lookback_seconds": int(r["lookback_seconds"]),
                "sort_order": int(r["sort_order"] or 0),
                "watch_count": int(n),
            }

    def update_set(
        self,
        user_id: int,
        set_id: int,
        *,
        name: Optional[str] = None,
        enabled: Optional[bool] = None,
        threshold_percent: Optional[float] = None,
        lookback_seconds: Optional[int] = None,
    ) -> dict:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM mover_sets WHERE id = ? AND user_id = ?",
                (set_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError("Set not found")
            new_name = row["name"]
            if name is not None:
                new_name = (name or "").strip() or row["name"]
                if len(new_name) > 40:
                    new_name = new_name[:40]
            new_en = int(row["enabled"]) if enabled is None else (1 if enabled else 0)
            new_thr = (
                float(row["threshold_percent"])
                if threshold_percent is None
                else float(threshold_percent)
            )
            new_lb = (
                int(row["lookback_seconds"])
                if lookback_seconds is None
                else int(lookback_seconds)
            )
            try:
                conn.execute(
                    "UPDATE mover_sets SET name = ?, enabled = ?, threshold_percent = ?, "
                    "lookback_seconds = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_name, new_en, new_thr, new_lb, set_id),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Set name already exists: {new_name}") from e
            if new_name == DEFAULT_SET_NAME or row["name"] == DEFAULT_SET_NAME:
                self._mirror_settings_from_default(conn, user_id)
            # If any set enabled, also flip legacy enabled for scanner health UX
            any_on = conn.execute(
                "SELECT 1 FROM mover_sets WHERE user_id = ? AND enabled = 1 LIMIT 1",
                (user_id,),
            ).fetchone()
            if any_on:
                # Ensure mover_settings exists and at least reflects on when any set on
                self._mirror_settings_from_default(conn, user_id)
                if not conn.execute(
                    "SELECT enabled FROM mover_settings WHERE user_id = ?", (user_id,)
                ).fetchone():
                    pass
                else:
                    # Prefer: enabled if ANY set on
                    conn.execute(
                        "UPDATE mover_settings SET enabled = 1, updated_at = CURRENT_TIMESTAMP "
                        "WHERE user_id = ?",
                        (user_id,),
                    )
            else:
                conn.execute(
                    "UPDATE mover_settings SET enabled = 0, updated_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = ?",
                    (user_id,),
                )
            return self.get_set(user_id, set_id)  # type: ignore[return-value]

    def delete_set(self, user_id: int, set_id: int) -> None:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT name FROM mover_sets WHERE id = ? AND user_id = ?",
                (set_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError("Set not found")
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM mover_sets WHERE user_id = ?",
                (user_id,),
            ).fetchone()["c"]
            if int(n) <= 1:
                raise ValueError("Cannot delete the last mover set")
            if row["name"] == DEFAULT_SET_NAME:
                raise ValueError("Cannot delete the Default set — rename or clear it instead")
            conn.execute("DELETE FROM mover_watchlist WHERE set_id = ?", (set_id,))
            conn.execute(
                "DELETE FROM mover_sets WHERE id = ? AND user_id = ?",
                (set_id, user_id),
            )
            self._mirror_settings_from_default(conn, user_id)

    def list_enabled_set_scans(self) -> List[dict]:
        """All enabled sets across users — scanner iterates these."""
        with self._lock:
            conn = self._get_conn()
            # Backfill any legacy users first
            for r in conn.execute(
                "SELECT DISTINCT user_id FROM mover_settings WHERE enabled = 1"
            ):
                self._ensure_default_set(conn, int(r["user_id"]))
            rows = conn.execute(
                "SELECT id, user_id, name, enabled, threshold_percent, lookback_seconds "
                "FROM mover_sets WHERE enabled = 1 ORDER BY user_id, sort_order, id"
            ).fetchall()
            # Fallback: users with legacy enabled but no set rows yet
            if not rows:
                legacy = conn.execute(
                    "SELECT user_id, threshold_percent, lookback_seconds FROM mover_settings "
                    "WHERE enabled = 1"
                ).fetchall()
                out = []
                for r in legacy:
                    sid = self._ensure_default_set(
                        conn,
                        int(r["user_id"]),
                        float(r["threshold_percent"]),
                        int(r["lookback_seconds"]),
                    )
                    conn.execute(
                        "UPDATE mover_sets SET enabled = 1 WHERE id = ?", (sid,)
                    )
                    out.append(
                        {
                            "id": sid,
                            "user_id": int(r["user_id"]),
                            "name": DEFAULT_SET_NAME,
                            "enabled": True,
                            "threshold_percent": float(r["threshold_percent"]),
                            "lookback_seconds": int(r["lookback_seconds"]),
                        }
                    )
                return out
            return [
                {
                    "id": int(r["id"]),
                    "user_id": int(r["user_id"]),
                    "name": r["name"],
                    "enabled": True,
                    "threshold_percent": float(r["threshold_percent"]),
                    "lookback_seconds": int(r["lookback_seconds"]),
                }
                for r in rows
            ]

    def get_settings(
        self,
        user_id: int,
        default_threshold: float,
        default_lookback: int,
        set_id: Optional[int] = None,
    ) -> dict:
        with self._lock:
            conn = self._get_conn()
            if set_id is not None:
                r = conn.execute(
                    "SELECT enabled, threshold_percent, lookback_seconds FROM mover_sets "
                    "WHERE id = ? AND user_id = ?",
                    (set_id, user_id),
                ).fetchone()
                if r:
                    return {
                        "enabled": bool(r["enabled"]),
                        "threshold_percent": float(r["threshold_percent"]),
                        "lookback_seconds": int(r["lookback_seconds"]),
                        "set_id": set_id,
                    }
            sid = self._ensure_default_set(
                conn, user_id, default_threshold, default_lookback
            )
            r = conn.execute(
                "SELECT enabled, threshold_percent, lookback_seconds FROM mover_sets WHERE id = ?",
                (sid,),
            ).fetchone()
            if r:
                return {
                    "enabled": bool(r["enabled"]),
                    "threshold_percent": float(r["threshold_percent"]),
                    "lookback_seconds": int(r["lookback_seconds"]),
                    "set_id": sid,
                }
            row = conn.execute(
                "SELECT enabled, threshold_percent, lookback_seconds FROM mover_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return {
                    "enabled": False,
                    "threshold_percent": default_threshold,
                    "lookback_seconds": default_lookback,
                    "set_id": sid,
                }
            return {
                "enabled": bool(row["enabled"]),
                "threshold_percent": float(row["threshold_percent"]),
                "lookback_seconds": int(row["lookback_seconds"]),
                "set_id": sid,
            }

    def set_enabled(
        self, user_id: int, enabled: bool, default_threshold: float, default_lookback: int
    ) -> dict:
        with self._lock:
            conn = self._get_conn()
            sid = self._ensure_default_set(
                conn, user_id, default_threshold, default_lookback
            )
            conn.execute(
                "UPDATE mover_sets SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (1 if enabled else 0, sid),
            )
            self._mirror_settings_from_default(conn, user_id)
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
            sid = self._ensure_default_set(
                conn, user_id, threshold_percent, lookback_seconds
            )
            row = conn.execute(
                "SELECT enabled FROM mover_sets WHERE id = ?", (sid,)
            ).fetchone()
            en = int(row["enabled"]) if row else (1 if default_enabled else 0)
            if row is None and default_enabled:
                en = 1
            conn.execute(
                "UPDATE mover_sets SET threshold_percent = ?, lookback_seconds = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (float(threshold_percent), int(lookback_seconds), sid),
            )
            if not row and default_enabled:
                conn.execute(
                    "UPDATE mover_sets SET enabled = 1 WHERE id = ?", (sid,)
                )
            self._mirror_settings_from_default(conn, user_id)
            return {
                "enabled": bool(en),
                "threshold_percent": float(threshold_percent),
                "lookback_seconds": int(lookback_seconds),
                "set_id": sid,
            }

    def get_watchlist(
        self, user_id: int, set_id: Optional[int] = None
    ) -> List[dict]:
        with self._lock:
            conn = self._get_conn()
            if set_id is None:
                # Union all sets for user (Telegram /mw heat); include set metadata
                rows = conn.execute(
                    "SELECT w.symbol, w.market, w.set_id, s.name AS set_name "
                    "FROM mover_watchlist w "
                    "LEFT JOIN mover_sets s ON s.id = w.set_id "
                    "WHERE w.user_id = ? ORDER BY s.sort_order, w.symbol ASC",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT w.symbol, w.market, w.set_id, s.name AS set_name "
                    "FROM mover_watchlist w "
                    "LEFT JOIN mover_sets s ON s.id = w.set_id "
                    "WHERE w.user_id = ? AND w.set_id = ? ORDER BY w.symbol ASC",
                    (user_id, set_id),
                ).fetchall()
            return [
                {
                    "symbol": r["symbol"],
                    "market": r["market"],
                    "set_id": int(r["set_id"]) if r["set_id"] is not None else None,
                    "set_name": r["set_name"] or DEFAULT_SET_NAME,
                }
                for r in rows
            ]

    def set_watchlist(
        self, user_id: int, items: List[dict], set_id: Optional[int] = None
    ) -> int:
        """Replace watchlist for one set (default set if set_id omitted)."""
        with self._lock:
            conn = self._get_conn()
            sid = set_id or self._ensure_default_set(conn, user_id)
            own = conn.execute(
                "SELECT 1 FROM mover_sets WHERE id = ? AND user_id = ?",
                (sid, user_id),
            ).fetchone()
            if not own:
                raise ValueError("Set not found")
            with conn:
                conn.execute(
                    "DELETE FROM mover_watchlist WHERE user_id = ? AND set_id = ?",
                    (user_id, sid),
                )
                for it in items:
                    conn.execute(
                        "INSERT OR IGNORE INTO mover_watchlist "
                        "(user_id, symbol, market, set_id) VALUES (?, ?, ?, ?)",
                        (
                            user_id,
                            str(it["symbol"]).upper(),
                            str(it.get("market", "futures")).lower(),
                            sid,
                        ),
                    )
            return len(self.get_watchlist(user_id, set_id=sid))

    def add_watchlist(
        self,
        user_id: int,
        symbol: str,
        market: str = "futures",
        set_id: Optional[int] = None,
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            sid = set_id or self._ensure_default_set(conn, user_id)
            own = conn.execute(
                "SELECT 1 FROM mover_sets WHERE id = ? AND user_id = ?",
                (sid, user_id),
            ).fetchone()
            if not own:
                raise ValueError("Set not found")
            conn.execute(
                "INSERT OR IGNORE INTO mover_watchlist (user_id, symbol, market, set_id) "
                "VALUES (?, ?, ?, ?)",
                (user_id, symbol.upper(), market.lower(), sid),
            )

    def remove_from_watchlist(
        self,
        user_id: int,
        symbols: List[str],
        market: Optional[str] = None,
        set_id: Optional[int] = None,
    ) -> int:
        if not symbols:
            return 0
        with self._lock:
            conn = self._get_conn()
            removed = 0
            for raw in symbols:
                sym = str(raw).upper()
                if set_id is not None and market:
                    cur = conn.execute(
                        "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ? "
                        "AND market = ? AND set_id = ?",
                        (user_id, sym, market.lower(), set_id),
                    )
                elif set_id is not None:
                    cur = conn.execute(
                        "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ? AND set_id = ?",
                        (user_id, sym, set_id),
                    )
                elif market:
                    cur = conn.execute(
                        "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ? AND market = ?",
                        (user_id, sym, market.lower()),
                    )
                else:
                    cur = conn.execute(
                        "DELETE FROM mover_watchlist WHERE user_id = ? AND symbol = ?",
                        (user_id, sym),
                    )
                removed += cur.rowcount
            return removed

    def restore_watchlist_from_recent_fires(
        self,
        user_id: int,
        *,
        set_id: Optional[int] = None,
        days: float = 7.0,
        limit: int = 80,
    ) -> dict:
        """Re-add symbols that recently mover-fired (recovery after wipe/migration)."""
        import time as _time

        since = _time.time() - max(1.0, float(days)) * 86400.0
        with self._lock:
            conn = self._get_conn()
            sid = int(set_id or self._ensure_default_set(conn, user_id))
            try:
                rows = conn.execute(
                    """
                    SELECT symbol, market, MAX(ts) AS last_ts
                    FROM learning_events
                    WHERE user_id = ?
                      AND source IN ('mover_peak', 'mover_step')
                      AND ts >= ?
                    GROUP BY symbol, market
                    ORDER BY last_ts DESC
                    LIMIT ?
                    """,
                    (int(user_id), since, int(limit)),
                ).fetchall()
            except Exception as e:
                logger.warning("restore from fires: %s", e)
                return {"ok": False, "added": 0, "error": str(e)}
            added = 0
            symbols = []
            for r in rows:
                sym = str(r["symbol"] or "").upper()
                mkt = str(r["market"] or "futures").lower()
                if not sym:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO mover_watchlist (user_id, symbol, market, set_id) "
                    "VALUES (?, ?, ?, ?)",
                    (int(user_id), sym, mkt, sid),
                )
                if cur.rowcount:
                    added += 1
                    symbols.append({"symbol": sym, "market": mkt})
            return {
                "ok": True,
                "added": added,
                "set_id": sid,
                "candidates": len(rows),
                "symbols": symbols,
            }

    def clear_watchlist(self, user_id: int, set_id: Optional[int] = None) -> int:
        with self._lock:
            conn = self._get_conn()
            if set_id is not None:
                cur = conn.execute(
                    "DELETE FROM mover_watchlist WHERE user_id = ? AND set_id = ?",
                    (user_id, set_id),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM mover_watchlist WHERE user_id = ?", (user_id,)
                )
            return cur.rowcount

    def get_enabled_users(self) -> List[int]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM mover_sets WHERE enabled = 1"
            ).fetchall()
            uids = [int(r["user_id"]) for r in rows]
            if uids:
                return uids
            # Legacy fallback
            rows = conn.execute(
                "SELECT user_id FROM mover_settings WHERE enabled = 1"
            ).fetchall()
            return [int(r["user_id"]) for r in rows]
