"""Machine log: decision changes only. No wait spam."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .settings import MANILA_TZ

_MANILA = ZoneInfo(MANILA_TZ)

SKIP_SPAM = frozenset({"wait", "habit_ready_waiting_reds"})


def manila_label(ts: Optional[float], *, seconds: bool = True) -> Optional[str]:
    if ts is None:
        return None
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    if t > 1e12:
        t /= 1000.0
    if t <= 0:
        return None
    dt = datetime.fromtimestamp(t, tz=_MANILA)
    fmt = "%Y-%m-%d %H:%M:%S PHT" if seconds else "%Y-%m-%d %H:%M PHT"
    return dt.strftime(fmt)


def should_log(
    prev: Optional[Dict[str, Any]],
    nxt: Dict[str, Any],
) -> bool:
    """True only when the decision changed. Wait/sit repeats stay off the log."""
    action = str(nxt.get("action") or "")
    reason = str(nxt.get("reason") or "")
    decision = str(nxt.get("decision") or "")
    if not decision and action in {"wait"}:
        return False
    if prev is None:
        return action not in {"wait"} or reason not in SKIP_SPAM
    if str(prev.get("decision") or "") == decision and decision:
        return False
    if str(prev.get("reason") or "") == reason and reason in SKIP_SPAM:
        return False
    if str(prev.get("action") or "") == action and action in {"wait", "sit"}:
        if str(prev.get("reason") or "") == reason:
            return False
    return True


class MachineLog:
    """In-memory decision log. One process, paper week."""

    def __init__(self) -> None:
        self._rows: List[Dict[str, Any]] = []
        self._last: Dict[str, Dict[str, Any]] = {}

    def append_if_changed(self, play_id: str, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        prev = self._last.get(play_id)
        if not should_log(prev, event):
            return None
        ts = float(event.get("ts") or time.time())
        row = {
            "play_id": play_id,
            "ts": ts,
            "manila": manila_label(ts),
            "action": event.get("action"),
            "reason": event.get("reason"),
            "decision": event.get("decision"),
            "current_price": event.get("current_price"),
            "symbol": event.get("symbol"),
        }
        self._rows.append(row)
        self._last[play_id] = dict(event)
        return row

    def last(self, play_id: str) -> Optional[Dict[str, Any]]:
        return self._last.get(play_id)

    def rows(self, play_id: Optional[str] = None, limit: int = 80) -> List[Dict[str, Any]]:
        seq = self._rows
        if play_id:
            seq = [r for r in seq if r.get("play_id") == play_id]
        return list(reversed(seq[-limit:]))

    def clear(self) -> None:
        self._rows.clear()
        self._last.clear()
