"""Load the standing process pack. Recut = new version; evaluate reads latest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_PACK_PATH = Path(__file__).with_name("process_pack.json")
_CACHED: Optional[Dict[str, Any]] = None
_CACHED_MTIME: Optional[float] = None


def default_process_pack() -> Dict[str, Any]:
    global _CACHED, _CACHED_MTIME
    mtime = _PACK_PATH.stat().st_mtime
    if _CACHED is None or _CACHED_MTIME != mtime:
        _CACHED = json.loads(_PACK_PATH.read_text(encoding="utf-8"))
        _CACHED_MTIME = mtime
    return dict(_CACHED)


def load_process_pack(store=None) -> Dict[str, Any]:
    """Runtime pack wins when a recut version exists. Else git file."""
    if store is not None and hasattr(store, "latest_process_pack"):
        row = store.latest_process_pack()
        if row and isinstance(row.get("json"), dict) and row["json"].get("rules"):
            return row["json"]
        if row and isinstance(row.get("pack_json"), str):
            try:
                blob = json.loads(row["pack_json"])
            except json.JSONDecodeError:
                blob = None
            if isinstance(blob, dict) and blob.get("rules"):
                return blob
    return default_process_pack()
