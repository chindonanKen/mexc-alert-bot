"""Alert storage layer. SQLite-backed (with migration from legacy JSON) + in-memory caches for visuals/ranks + RLock for thread safety across bot commands + monitor thread."""

import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertStore:
    """
    SQLite-backed alert storage with proper ACID semantics.

    This replaces the previous fragile JSON + manual locking approach.
    - Stable internal IDs (auto-increment)
    - Visual "id" shown to the user is always the current 1-based rank in their list
      (so #1 is always the top item in /l, #2 the next, etc.)
    - Easy to extend with more tables/columns for future features (movers, settings, etc.)
    """

    def __init__(self, path: Path):
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._visual_cache: Dict[int, List[dict]] = {}
        self._user_ids_cache: Optional[List[int]] = None

        # Convert legacy .json path to .db
        if str(path).endswith(".json"):
            self.db_path = path.with_suffix(".db")
            self.old_json_path = path
        else:
            self.db_path = path
            self.old_json_path = None

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # IMPORTANT: Check for migration *before* connecting/creating the DB file.
        # This way the first connect will only happen for the real DB (or during migration).
        migrated = self._migrate_from_json_if_needed()

        # Now initialize (creates the .db file + table if they don't exist)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,  # we serialize with our own lock
                    isolation_level=None,     # we control transactions manually
                )
                self._conn.row_factory = sqlite3.Row

                # Performance pragmas for a read-heavy, low-write workload like this
                # (monitor reads every second, writes only on alert changes)
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
                self._conn.execute("PRAGMA cache_size=10000;")
                self._conn.execute("PRAGMA temp_store=MEMORY;")
            except sqlite3.OperationalError as e:
                # This is almost always a permission problem when running in Docker
                # because the volume is owned by root on the host but the container
                # runs as non-root (appuser).
                msg = (
                    f"Failed to open SQLite database at {self.db_path}\n"
                    f"Original error: {e}\n\n"
                    "This is usually a file permission issue inside Docker.\n"
                    "On your VPS host, run:\n"
                    "    cd ~/mexc-alert-bot\n"
                    "    mkdir -p data\n"
                    "    chown -R 1000:1000 data     # or the uid of appuser inside the container\n"
                    "    chmod 755 data\n"
                    "Then: docker compose up -d --build\n"
                )
                logger.error(msg)
                raise RuntimeError(msg) from e
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON alerts (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_symbol ON alerts (user_id, symbol)")
        # V3 additive migration: market column ('spot' | 'futures'). Existing rows → spot.
        self._ensure_market_column(conn)

    def _ensure_market_column(self, conn: sqlite3.Connection) -> None:
        """Add market column if missing. Safe on every startup; no data rewrite."""
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(alerts)").fetchall()
        }
        if "market" not in cols:
            conn.execute(
                "ALTER TABLE alerts ADD COLUMN market TEXT NOT NULL DEFAULT 'spot'"
            )
            logger.info("Added alerts.market column (default 'spot') — existing alerts unchanged")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_market ON alerts (user_id, market)"
        )

    def _invalidate_caches(self, user_id: Optional[int] = None) -> None:
        """Invalidate in-memory caches after mutations so next reads are fresh."""
        if user_id is None:
            self._visual_cache.clear()
            self._user_ids_cache = None
        else:
            self._visual_cache.pop(user_id, None)
            # user_ids only needs full invalidation if a brand new user appears
            if self._user_ids_cache is not None and user_id not in self._user_ids_cache:
                self._user_ids_cache = None

    def _migrate_from_json_if_needed(self) -> bool:
        """One-time migration from the old JSON format.
        Returns True if migration was performed.
        Must be called *before* any connect that would create the .db file.
        """
        if not self.old_json_path or not self.old_json_path.exists():
            return False
        if self.db_path.exists():
            return False

        try:
            with open(self.old_json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            # This connect will create the empty .db + we will populate it
            conn = self._get_conn()
            with conn:
                for uid_str, alerts in raw.items():
                    uid = int(uid_str)
                    for a in alerts:
                        conn.execute(
                            "INSERT INTO alerts (user_id, symbol, price, enabled, created_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (uid, a["symbol"], float(a["price"]), 1 if a.get("enabled", True) else 0, datetime.utcnow().isoformat())
                        )
            logger.info(f"Migrated alerts from {self.old_json_path} to {self.db_path}")
            self._invalidate_caches()
            # Leave the old json as backup for now; user can delete it later if happy.
            return True
        except Exception as e:
            logger.error(f"JSON migration failed: {e}")
            return False

    def _get_visual_alerts(self, user_id: int) -> List[dict]:
        """Return alerts for user with 'id' set to current 1-based visual position."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id as stable_id, symbol, price, enabled, market FROM alerts "
            "WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        ).fetchall()

        result = []
        for rank, row in enumerate(rows, 1):  # 1-based visual id
            market = (row["market"] or "spot").lower()
            if market not in ("spot", "futures"):
                market = "spot"
            result.append({
                "id": rank,                    # what the user sees and types in /r /t
                "stable_id": row["stable_id"], # internal DB key
                "symbol": row["symbol"],
                "price": float(row["price"]),
                "enabled": bool(row["enabled"]),
                "market": market,
            })
        return result

    # --- Public API (kept compatible with previous callers) ---

    def load(self) -> Dict[int, List[dict]]:
        # For backward compatibility with any old call sites. Not really used anymore.
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT user_id, id as stable_id, symbol, price, enabled FROM alerts").fetchall()
            data: Dict[int, List[dict]] = {}
            for row in rows:
                uid = row["user_id"]
                if uid not in data:
                    data[uid] = []
                # We don't assign visual here; get_user_alerts does it
                data[uid].append({
                    "stable_id": row["stable_id"],
                    "symbol": row["symbol"],
                    "price": float(row["price"]),
                    "enabled": bool(row["enabled"]),
                })
            return data

    def save(self) -> None:
        # No-op for SQLite (writes are immediate on commit). Kept for compatibility.
        pass

    def get_user_alerts(self, user_id: int) -> List[dict]:
        with self._lock:
            if user_id in self._visual_cache:
                return [a.copy() for a in self._visual_cache[user_id]]
            visuals = self._get_visual_alerts(user_id)
            self._visual_cache[user_id] = visuals
            return [a.copy() for a in visuals]

    def add_alert(
        self,
        user_id: int,
        symbol: str,
        price: float,
        market: str = "spot",
    ) -> int:
        """Add a new alert. Returns the *visual* id (current rank in the user's list).

        market: 'spot' (default, V1 behavior) or 'futures'. Existing callers omit market → spot.
        """
        mkt = (market or "spot").strip().lower()
        if mkt not in ("spot", "futures"):
            mkt = "spot"
        with self._lock:
            conn = self._get_conn()
            try:
                with conn:
                    cur = conn.execute(
                        "INSERT INTO alerts (user_id, symbol, price, enabled, market) "
                        "VALUES (?, ?, ?, 1, ?)",
                        (user_id, symbol.upper(), float(price), mkt),
                    )
                    stable_id = cur.lastrowid

                # Compute the visual rank for the newly inserted row
                # (number of alerts for this user with stable_id <= this one, ordered by stable_id)
                rank = conn.execute(
                    "SELECT COUNT(*) FROM alerts WHERE user_id = ? AND id <= ?",
                    (user_id, stable_id)
                ).fetchone()[0]

                logger.info(
                    f"Added alert (visual #{rank}) for user {user_id}: "
                    f"{symbol} @ {price} market={mkt}"
                )
                self._invalidate_caches(user_id)
                return rank
            except Exception as e:
                logger.error(f"Failed to add alert for user {user_id} {symbol}: {e}")
                raise

    def remove_alert(self, user_id: int, visual_id: int) -> bool:
        """Remove by the visual id the user sees (1-based rank in current list)."""
        with self._lock:
            alerts = self.get_user_alerts(user_id)
            for a in alerts:
                if a["id"] == visual_id:
                    conn = self._get_conn()
                    with conn:
                        conn.execute("DELETE FROM alerts WHERE id = ?", (a["stable_id"],))
                    logger.info(f"Removed alert (was visual #{visual_id}) for user {user_id}")
                    self._invalidate_caches(user_id)
                    return True
            return False

    def toggle_alert(self, user_id: int, visual_id: int) -> Optional[bool]:
        with self._lock:
            alerts = self.get_user_alerts(user_id)
            for a in alerts:
                if a["id"] == visual_id:
                    new_enabled = not a["enabled"]
                    conn = self._get_conn()
                    with conn:
                        conn.execute(
                            "UPDATE alerts SET enabled = ? WHERE id = ?",
                            (1 if new_enabled else 0, a["stable_id"])
                        )
                    logger.info(f"Toggled alert (visual #{visual_id}) for user {user_id} -> {new_enabled}")
                    self._invalidate_caches(user_id)
                    return new_enabled
            return None

    def count_for_user(self, user_id: int) -> int:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            return row[0] if row else 0

    def get_all_user_ids(self) -> List[int]:
        with self._lock:
            if self._user_ids_cache is not None:
                return self._user_ids_cache[:]
            conn = self._get_conn()
            rows = conn.execute("SELECT DISTINCT user_id FROM alerts").fetchall()
            self._user_ids_cache = [r[0] for r in rows]
            return self._user_ids_cache[:]

    def remove_alerts_by_ids(self, user_id: int, visual_ids: List[int]) -> int:
        """Remove by list of visual ids (current ranks)."""
        if not visual_ids:
            return 0
        with self._lock:
            alerts = self.get_user_alerts(user_id)
            id_map = {a["id"]: a["stable_id"] for a in alerts}
            to_delete = [id_map[vid] for vid in visual_ids if vid in id_map]
            if not to_delete:
                return 0
            conn = self._get_conn()
            with conn:
                placeholders = ",".join("?" * len(to_delete))
                conn.execute(
                    f"DELETE FROM alerts WHERE id IN ({placeholders})",
                    to_delete
                )
            logger.info(f"Removed {len(to_delete)} alerts for user {user_id} (visual ids: {visual_ids})")
            self._invalidate_caches(user_id)
            return len(to_delete)

    def remove_alerts_by_stable_ids(self, user_id: int, stable_ids: List[int]) -> int:
        """Remove by internal DB PKs (stable_ids). Used by monitor for fired alerts to target exact rows
        from decision-time snapshot, immune to visual rank shifts from concurrent bot removes or prior fires.
        """
        if not stable_ids:
            return 0
        with self._lock:
            to_delete = [int(sid) for sid in stable_ids]
            conn = self._get_conn()
            with conn:
                placeholders = ",".join("?" * len(to_delete))
                cur = conn.execute(
                    f"DELETE FROM alerts WHERE user_id = ? AND id IN ({placeholders})",
                    [user_id] + to_delete
                )
            removed = cur.rowcount
            if removed > 0:
                logger.info(f"Removed {removed} alerts for user {user_id} (stable ids: {stable_ids})")
                self._invalidate_caches(user_id)
            return removed

    def has_any_futures_alerts(self) -> bool:
        """True if any enabled futures target-alerts exist (used to skip futures fetch when unused)."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT 1 FROM alerts WHERE market = 'futures' AND enabled = 1 LIMIT 1"
            ).fetchone()
            return row is not None

    def remove_alerts_by_symbol(self, user_id: int, symbol: str) -> int:
        with self._lock:
            sym = symbol.upper()
            conn = self._get_conn()
            with conn:
                cur = conn.execute(
                    "DELETE FROM alerts WHERE user_id = ? AND symbol = ?",
                    (user_id, sym)
                )
            removed = cur.rowcount
            if removed > 0:
                logger.info(f"Removed {removed} alerts for user {user_id} symbol={sym}")
                self._invalidate_caches(user_id)
            return removed

    def disable_all(self, user_id: int) -> int:
        with self._lock:
            conn = self._get_conn()
            with conn:
                cur = conn.execute(
                    "UPDATE alerts SET enabled = 0 WHERE user_id = ? AND enabled = 1",
                    (user_id,)
                )
            changed = cur.rowcount
            if changed > 0:
                logger.info(f"Disabled {changed} alerts for user {user_id}")
                self._invalidate_caches(user_id)
            return changed
