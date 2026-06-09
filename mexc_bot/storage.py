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
            # Renumber on load so IDs always match current positions (0-based from top)
            for uid in list(self._data.keys()):
                self._renumber_user(uid)
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

    def _renumber_user(self, user_id: int) -> None:
        """Ensure IDs are always 1, 2, 3, ... matching current position in the list from top (1-based for humans).
        Must be called under lock after any add/remove that changes the list length/order.
        """
        if user_id in self._data:
            for idx, alert in enumerate(self._data[user_id]):
                alert["id"] = idx + 1

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

            alert = {
                "id": -1,  # placeholder, will be set by renumber
                "symbol": symbol.upper(),
                "price": float(price),
                "enabled": True,
            }
            user_alerts.append(alert)
            self._renumber_user(user_id)
            new_id = user_alerts[-1]["id"]  # now the last position
            self._atomic_save_locked()
            logger.info(f"Added alert #{new_id} for user {user_id}: {symbol} @ {price}")
            return new_id

    def remove_alert(self, user_id: int, alert_id: int) -> bool:
        self._ensure_loaded()
        with self._lock:
            if user_id not in self._data:
                return False
            before = len(self._data[user_id])
            self._data[user_id] = [a for a in self._data[user_id] if a["id"] != alert_id]
            if len(self._data[user_id]) < before:
                self._renumber_user(user_id)
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

    def remove_alerts_by_ids(self, user_id: int, alert_ids: List[int]) -> int:
        """Remove multiple alerts by ID. Returns number actually removed."""
        self._ensure_loaded()
        if not alert_ids:
            return 0
        with self._lock:
            if user_id not in self._data:
                return 0
            id_set = set(alert_ids)
            before = len(self._data[user_id])
            self._data[user_id] = [a for a in self._data[user_id] if a["id"] not in id_set]
            removed = before - len(self._data[user_id])
            if removed > 0:
                self._renumber_user(user_id)
                self._atomic_save_locked()
                logger.info(f"Removed {removed} alerts for user {user_id} (ids: {alert_ids})")
            return removed

    def remove_alerts_by_symbol(self, user_id: int, symbol: str) -> int:
        """Remove all alerts for a given symbol. Returns number removed."""
        self._ensure_loaded()
        sym = symbol.upper()
        with self._lock:
            if user_id not in self._data:
                return 0
            before = len(self._data[user_id])
            self._data[user_id] = [a for a in self._data[user_id] if a["symbol"] != sym]
            removed = before - len(self._data[user_id])
            if removed > 0:
                self._renumber_user(user_id)
                self._atomic_save_locked()
                logger.info(f"Removed {removed} alerts for user {user_id} symbol={sym}")
            return removed

    def disable_all(self, user_id: int) -> int:
        """Disable all alerts for the user. Returns number that were changed."""
        self._ensure_loaded()
        with self._lock:
            if user_id not in self._data:
                return 0
            changed = 0
            for a in self._data[user_id]:
                if a.get("enabled"):
                    a["enabled"] = False
                    changed += 1
            if changed > 0:
                self._atomic_save_locked()
                logger.info(f"Disabled all ({changed}) alerts for user {user_id}")
            return changed
