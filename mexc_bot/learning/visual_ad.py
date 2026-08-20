"""Staff-written visual AD on a setup case (Learning only).

This is display + storage. Formula snapshots in chart_features.py are NOT
a visual AD — never invent high/low from klines, reds, or vol ratio.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Basename only: no slashes, no "..", no absolute paths.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_USEFUL_KEYS = ("tf", "high", "low", "note", "image_relpath")
_NOTE_MAX = 280
_TF_MAX = 24


def visual_ad_dir(db_path: Path) -> Path:
    """Images live under data/.grokbot/visual_ad/ next to the desk DB."""
    return Path(db_path).expanduser().resolve().parent / ".grokbot" / "visual_ad"


def safe_image_basename(relpath: Any) -> str:
    """Require a single basename. Reject traversal, absolute paths, odd names."""
    raw = str(relpath if relpath is not None else "").strip()
    if not raw:
        raise ValueError("empty image_relpath")
    if raw.startswith(("/", "\\", "~")) or raw.endswith(("/", "\\")):
        raise ValueError("image_relpath must be a basename")
    if any(sep in raw for sep in ("/", "\\")):
        raise ValueError("path traversal rejected")
    if ".." in raw:
        raise ValueError("path traversal rejected")
    if len(raw) >= 2 and raw[1] == ":":
        raise ValueError("absolute path rejected")
    name = Path(raw).name
    if name != raw:
        raise ValueError("image_relpath must be a basename")
    if name in {".", ".."} or not _SAFE_NAME.fullmatch(name):
        raise ValueError("invalid image_relpath")
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError("image_relpath must be an image basename")
    return name


def resolve_visual_ad_image(db_path: Path, relpath: str) -> Path:
    """Resolve basename under visual_ad_dir. Raises ValueError if unsafe."""
    name = safe_image_basename(relpath)
    base = visual_ad_dir(db_path).resolve()
    dest = (base / name).resolve()
    dest.relative_to(base)  # ValueError if outside
    return dest


def _as_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def has_useful_visual_ad(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    for key in _USEFUL_KEYS:
        val = obj.get(key)
        if val is None or val == "":
            continue
        return True
    return False


def sanitize_visual_ad(payload: Any) -> Dict[str, Any]:
    """Normalize a staff visual_ad object. Raises ValueError if unusable."""
    if not isinstance(payload, dict):
        raise ValueError("visual_ad must be an object")
    out: Dict[str, Any] = {}

    tf = payload.get("tf")
    if tf is not None and str(tf).strip():
        tf_s = str(tf).strip()[:_TF_MAX]
        out["tf"] = tf_s

    if "high" in payload and payload.get("high") is not None and payload.get("high") != "":
        out["high"] = _as_optional_float(payload.get("high"))
    if "low" in payload and payload.get("low") is not None and payload.get("low") != "":
        out["low"] = _as_optional_float(payload.get("low"))

    note = payload.get("note")
    if note is not None and str(note).strip():
        out["note"] = str(note).strip()[:_NOTE_MAX]

    rel = payload.get("image_relpath")
    if rel is not None and str(rel).strip():
        out["image_relpath"] = safe_image_basename(rel)

    source = payload.get("source")
    if source is not None and str(source).strip():
        out["source"] = str(source).strip()[:40]

    ts = payload.get("ts")
    if ts is not None and ts != "":
        out["ts"] = float(ts)

    if not has_useful_visual_ad(out):
        raise ValueError("visual_ad needs tf, high, low, note, or image_relpath")
    return out


def extract_visual_ad(features: Any) -> Optional[Dict[str, Any]]:
    """Public-safe visual_ad from features_json, or None."""
    if not isinstance(features, dict):
        return None
    raw = features.get("visual_ad")
    if not isinstance(raw, dict):
        return None
    try:
        return sanitize_visual_ad(raw)
    except (TypeError, ValueError):
        return None


def parse_features_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def preserve_visual_ad_on_features(
    new_features: Dict[str, Any],
    old_features_json: Any,
) -> Dict[str, Any]:
    """Keep an existing visual_ad when formula features omit one."""
    if not isinstance(new_features, dict):
        return new_features
    if extract_visual_ad(new_features) is not None:
        return new_features
    old = parse_features_json(old_features_json)
    kept = extract_visual_ad(old)
    if kept is None:
        return new_features
    out = dict(new_features)
    out["visual_ad"] = kept
    return out


def merge_visual_ad_into_features(
    features: Dict[str, Any],
    incoming: Dict[str, Any],
) -> Dict[str, Any]:
    """Update ONLY the visual_ad key. Formula keys stay as-is."""
    out = dict(features or {})
    old = out.get("visual_ad") if isinstance(out.get("visual_ad"), dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    merged = {**old, **{k: v for k, v in incoming.items() if v is not None}}
    if "ts" not in merged or merged.get("ts") in (None, ""):
        merged["ts"] = time.time()
    if not merged.get("source"):
        merged["source"] = "staff"
    out["visual_ad"] = sanitize_visual_ad(merged)
    return out
