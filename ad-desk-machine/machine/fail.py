"""Fail live-read. A timer is not a fail. Last under the AD is not a fail."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from .settings import NEWS_KILL_CLASSES

_RUMOR = re.compile(
    r"\b(rumou?r(s)?|allegedly|hearsay|gossip|unconfirmed chatter)\b",
    re.I,
)


def is_rumor(hit: Dict[str, Any]) -> bool:
    blob = " ".join(
        str(hit.get(k) or "")
        for k in ("title", "kind", "class", "severity", "source", "note")
    )
    if _RUMOR.search(blob):
        return True
    kind = str(hit.get("kind") or "").lower()
    return kind in {"rumor", "rumour", "gossip"}


def news_kill(hits: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Delist/scam/hack flattens. Rumors are not news."""
    for raw in hits or []:
        if not isinstance(raw, dict):
            continue
        if is_rumor(raw):
            continue
        cls = str(raw.get("class") or raw.get("kind") or "").upper()
        sev = str(raw.get("severity") or "").lower()
        if cls not in NEWS_KILL_CLASSES:
            continue
        if sev in {"unconfirmed", "rumor", "rumour"}:
            continue
        return {"kill": True, "hit": raw, "class": cls}
    return None


def last_under_ad(last: Any, ad_bottom: Any) -> bool:
    """Tape fact only. Not a fail. Not a close."""
    try:
        return float(last) <= float(ad_bottom)
    except (TypeError, ValueError):
        return False


def failed_ad(
    *,
    armed_at: Optional[float],
    now: float,
    tf: Optional[str],
    bounced: bool,
) -> bool:
    """A timer is not a fail. Clock expiry never fails a hung plan."""
    del armed_at, now, tf, bounced
    return False


def fail_decision(play: Dict[str, Any], tape: Dict[str, Any]) -> Dict[str, Any]:
    """Live-read Fail. Breaking B is usually add, not flatten."""
    kill = news_kill(tape.get("news") or [])
    if kill:
        return {
            "action": "flatten",
            "reason": "news_kill",
            "decision": "Real bad news, flatten.",
            "add_panic": False,
            "fail": True,
            "news": kill,
        }
    if failed_ad(
        armed_at=tape.get("armed_at") or play.get("armed_at"),
        now=float(tape.get("now") or 0),
        tf=play.get("tf"),
        bounced=bool(tape.get("bounced")),
    ):
        return {
            "action": "reassess",
            "reason": "fail",
            "decision": "Reassess. Do not flatten from a clock.",
            "add_panic": False,
            "fail": True,
            "news": None,
        }

    past_b = bool(tape.get("past_b"))
    if not past_b:
        last = tape.get("current_price") if tape.get("current_price") is not None else tape.get("last")
        past_b = last_under_ad(last, play.get("ad_bottom"))

    board_panic = bool(tape.get("board_panic") or tape.get("panic_board"))
    fast = bool(tape.get("fast_dump") or tape.get("fast_dump_volume") or tape.get("vol_spike"))
    grind_not_this = bool(tape.get("grind_not_this_chart"))
    in_play = bool(tape.get("in_play") or play.get("in_play"))

    if in_play and grind_not_this:
        return {
            "action": "reassess",
            "reason": "grind_not_this_chart",
            "decision": "In, grinding lower. This chart's AD bounce does not look like this.",
            "add_panic": False,
            "fail": True,
            "news": None,
        }

    if past_b and (fast or board_panic) and not play.get("watch_only"):
        return {
            "action": "add_panic",
            "reason": "break_ad_add_panic",
            "decision": "Break of AD, add with the panic half.",
            "add_panic": True,
            "fail": False,
            "news": None,
        }

    return {
        "action": None,
        "reason": None,
        "decision": None,
        "add_panic": False,
        "fail": False,
        "news": None,
    }
