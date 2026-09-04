"""Load hung plays from data/plays/. File is the hung plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
PLAYS_DIR = ROOT / "data" / "plays"
GROKBOT_DIR = ROOT / "data" / ".grokbot"

HUNG_IDS = ("SYNUSDT_4h", "AGIUSDT_4h", "USUSDT_4h")


def plays_dir() -> Path:
    return PLAYS_DIR


def _safe_stem(name: str) -> str:
    raw = str(name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError("bad play id")
    return raw


def play_path(play_id: str) -> Path:
    return PLAYS_DIR / f"{_safe_stem(play_id)}.json"


def load_play(play_id: str) -> Dict[str, Any]:
    path = play_path(play_id)
    if not path.is_file():
        raise FileNotFoundError(play_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("play must be an object")
    data.setdefault("id", play_id)
    return data


def save_runtime(play_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Patch sticky fields (met) on the hung file. Additive only."""
    play = load_play(play_id)
    allowed = {"met", "in_play", "armed_at", "leftover"}
    for key, val in patch.items():
        if key in allowed:
            play[key] = val
    play_path(play_id).write_text(json.dumps(play, indent=2) + "\n", encoding="utf-8")
    return play


def list_play_ids(*, hung_only: bool = True) -> List[str]:
    if not PLAYS_DIR.is_dir():
        return []
    names = sorted(p.stem for p in PLAYS_DIR.glob("*.json") if p.is_file())
    if hung_only:
        return [n for n in names if n in HUNG_IDS]
    return names


def load_hung_plays() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pid in HUNG_IDS:
        path = play_path(pid)
        if path.is_file():
            out.append(load_play(pid))
    return out


def load_exit_facts(play_id: str) -> Optional[Dict[str, Any]]:
    """Optional staff exit facts next to the hung plan. Missing is fine."""
    path = GROKBOT_DIR / f"{_safe_stem(play_id)}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
