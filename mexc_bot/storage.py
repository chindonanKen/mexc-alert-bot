"""Alert storage layer. Currently JSON file backed with basic concurrency safety."""

import json
import logging
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertStore:
    """
    Thread-safe JSON-backed alert storage.

    Data shape:
    {
      "<user_id>": [
        {"id": 0, "symbol": "BTCUSDT", "price": 65000.0, "enabled": true},
        ...
      ]
    }

    IDs are per-user sequential (starting from 0 or next available).
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self._data: Dict[int, List[dict]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_locked()
            self._loaded = True

    def _load_locked(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Keys are stored as strings in JSON; convert back to int
            self._data = {int(k): v for k, v in raw.items()}
            logger.info(f"Loaded alerts for {len(self._data)} user(s) from {self.path}")
        except Exception as e:
            logger.error(f"Failed to load alerts file: {e}. Starting empty.")
            self._data = {}

    def _atomic_save_locked(self) -> None:
        """Write to temp file then rename for atomicity on POSIX filesystems."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Prepare JSON with string keys for nice file readability
        serializable = {str(uid): alerts for uid, alerts in self._data.items()}

        # Use same directory for atomic rename to work reliably
        dirpath = self.path.parent
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=dirpath, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(serializable, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, self.path)  # atomic on same filesystem

    def load(self) -> Dict[int, List[dict]]:
        self._ensure_loaded()
        with self._lock:
            # Return a deep copy to prevent external mutation
            return {uid: [a.copy() for a in alerts] for uid, alerts in self._data.items()}

    def save(self) -> None:
        with self._lock:
            self._atomic_save_locked()

    def get_user_alerts(self, user_id: int) -> List[dict]:
        self._ensure_loaded()
        with self._lock:
            return [a.copy() for a in self._data.get(user_id, [])]

    def add_alert(self, user_id: int, symbol: str, price: float) -> int:
        """Add a new alert. Returns the assigned alert id."""
        self._ensure_loaded()
        with self._lock:
            if user_id not in self._data:
                self._data[user_id] = []

            user_alerts = self._data[user_id]
            # Compute next id (max + 1, or 0)
            next_id = max((a["id"] for a in user_alerts), default=-1) + 1

            alert = {
                "id": next_id,
                "symbol": symbol.upper(),
                "price": float(price),
                "enabled": True,
            }
            user_alerts.append(alert)
            self._atomic_save_locked()
            logger.info(f"Added alert #{next_id} for user {user_id}: {symbol} @ {price}")
            return next_id

    def remove_alert(self, user_id: int, alert_id: int) -> bool:
        self._ensure_loaded()
        with self._lock:
            if user_id not in self._data:
                return False
            before = len(self._data[user_id])
            self._data[user_id] = [a for a in self._data[user_id] if a["id"] != alert_id]
            if len(self._data[user_id]) < before:
                self._atomic_save_locked()
                logger.info(f"Removed alert #{alert_id} for user {user_id}")
                return True
            return False

    def toggle_alert(self, user_id: int, alert_id: int) -> Optional[bool]:
        """Toggle enabled state. Returns new state or None if not found."""
        self._ensure_loaded()
        with self._lock:
            for a in self._data.get(user_id, []):
                if a["id"] == alert_id:
                    a["enabled"] = not a["enabled"]
                    self._atomic_save_locked()
                    logger.info(f"Toggled alert #{alert_id} for user {user_id} -> {a['enabled']}")
                    return a["enabled"]
            return None

    def count_for_user(self, user_id: int) -> int:
        self._ensure_loaded()
        with self._lock:
            return len(self._data.get(user_id, []))

    def get_all_user_ids(self) -> List[int]:
        self._ensure_loaded()
        with self._lock:
            return list(self._data.keys())
