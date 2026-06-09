"""Alert storage layer. Currently JSON file backed with basic concurrency safety."""

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
        self.path = path
        # If someone passes a .json path from old config, convert to .db
        if str(path).endswith(".json"):
            self.path = path.with_suffix(".db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()
        self._migrate_from_json_if_needed(path)  # path may be the old .json

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(
                    self.path,
                    check_same_thread=False,  # we serialize with our own lock
                    isolation_level=None,     # we control transactions manually
                )
                self._conn.row_factory = sqlite3.Row
            except sqlite3.OperationalError as e:
                # This is almost always a permission problem when running in Docker
                # because the volume is owned by root on the host but the container
                # runs as non-root (appuser).
                msg = (
                    f"Failed to open SQLite database at {self.path}\n"
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

    def _migrate_from_json_if_needed(self, old_json_path: Path):
        """One-time migration from the old JSON format."""
        if not old_json_path.exists() or self.path.exists():
            return
        try:
            with open(old_json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
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
            logger.info(f"Migrated alerts from {old_json_path} to {self.path}")
            # Optionally leave the json as backup; user can delete later
        except Exception as e:
            logger.error(f"JSON migration failed: {e}")

    def _get_visual_alerts(self, user_id: int) -> List[dict]:
        """Return alerts for user with 'id' set to current 1-based visual position."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id as stable_id, symbol, price, enabled FROM alerts "
            "WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        ).fetchall()

        result = []
        for rank, row in enumerate(rows, 1):  # 1-based visual id
            result.append({
                "id": rank,                    # what the user sees and types in /r /t
                "stable_id": row["stable_id"], # internal DB key
                "symbol": row["symbol"],
                "price": float(row["price"]),
                "enabled": bool(row["enabled"]),
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
            return self._get_visual_alerts(user_id)

    def add_alert(self, user_id: int, symbol: str, price: float) -> int:
        """Add a new alert. Returns the *visual* id (current rank in the user's list)."""
        with self._lock:
            conn = self._get_conn()
            try:
                with conn:
                    cur = conn.execute(
                        "INSERT INTO alerts (user_id, symbol, price, enabled) VALUES (?, ?, ?, 1)",
                        (user_id, symbol.upper(), float(price))
                    )
                    stable_id = cur.lastrowid

                # Compute the visual rank for the newly inserted row
                # (number of alerts for this user with stable_id <= this one, ordered by stable_id)
                rank = conn.execute(
                    "SELECT COUNT(*) FROM alerts WHERE user_id = ? AND id <= ?",
                    (user_id, stable_id)
                ).fetchone()[0]

                logger.info(f"Added alert (visual #{rank}) for user {user_id}: {symbol} @ {price}")
                return rank
            except Exception as e:
                logger.error(f"Failed to add alert for user {user_id} {symbol}: {e}")
                raise

    def remove_alert(self, user_id: int, visual_id: int) -> bool:
        """Remove by the visual id the user sees (1-based rank in current list)."""
        with self._lock:
            alerts = self._get_visual_alerts(user_id)
            for a in alerts:
                if a["id"] == visual_id:
                    conn = self._get_conn()
                    with conn:
                        conn.execute("DELETE FROM alerts WHERE id = ?", (a["stable_id"],))
                    logger.info(f"Removed alert (was visual #{visual_id}) for user {user_id}")
                    return True
            return False

    def toggle_alert(self, user_id: int, visual_id: int) -> Optional[bool]:
        with self._lock:
            alerts = self._get_visual_alerts(user_id)
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
            conn = self._get_conn()
            rows = conn.execute("SELECT DISTINCT user_id FROM alerts").fetchall()
            return [r[0] for r in rows]

    def remove_alerts_by_ids(self, user_id: int, visual_ids: List[int]) -> int:
        """Remove by list of visual ids (current ranks)."""
        if not visual_ids:
            return 0
        with self._lock:
            alerts = self._get_visual_alerts(user_id)
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
            return len(to_delete)

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
            return changed
