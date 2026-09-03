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
    """Runtime pack wins when its version is >= the git file. Else git file."""
    file_pack = default_process_pack()
    file_ver = file_pack.get("version")
    try:
        file_ver_n = int(file_ver) if file_ver is not None else None
    except (TypeError, ValueError):
        file_ver_n = None
    if store is not None and hasattr(store, "latest_process_pack"):
        row = store.latest_process_pack()
        blob = None
        if row and isinstance(row.get("json"), dict) and row["json"].get("rules"):
            blob = row["json"]
        elif row and isinstance(row.get("pack_json"), str):
            try:
                parsed = json.loads(row["pack_json"])
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("rules"):
                blob = parsed
        if blob:
            try:
                db_ver = int(blob.get("version") or row.get("version") or 0)
            except (TypeError, ValueError):
                db_ver = 0
            if file_ver_n is None or db_ver >= file_ver_n:
                return blob
    return file_pack
