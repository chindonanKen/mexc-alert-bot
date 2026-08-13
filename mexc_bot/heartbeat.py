"""Fail-soft live-bot heartbeat.

Docker healthcheck and post-deploy smoke read this file. Writes must never
raise — a lock/permission bug here already crash-looped Telegram.
Path: next to SQLite (``data/bot_heartbeat.json``), not root-owned ``.safety``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HEARTBEAT_NAME = "bot_heartbeat.json"
DEFAULT_MAX_AGE_SEC = 90.0


def heartbeat_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / HEARTBEAT_NAME


def touch_heartbeat(data_dir: Path | str, **fields: Any) -> None:
    """Merge fields into the heartbeat file. Never raises."""
    path = heartbeat_path(data_dir)
    try:
        prev: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    prev = raw
            except (OSError, json.JSONDecodeError):
                prev = {}
        prev.update(fields)
        prev["ts"] = time.time()
        prev["pid"] = os.getpid()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(prev), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning("heartbeat write failed: %s", e)


def read_heartbeat(data_dir: Path | str) -> Optional[dict[str, Any]]:
    path = heartbeat_path(data_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def check_alive(
    data_dir: Path | str = "/app/data",
    *,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
    require_polling: bool = True,
) -> bool:
    """True when the bot has written a fresh heartbeat (and polling started)."""
    hb = read_heartbeat(data_dir)
    if not hb:
        return False
    ts = hb.get("ts")
    try:
        age = time.time() - float(ts)
    except (TypeError, ValueError):
        return False
    if age < 0 or age > max_age_sec:
        return False
    if require_polling and not hb.get("polling"):
        return False
    return True
