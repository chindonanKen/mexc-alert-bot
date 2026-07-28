"""Telegram assistant UX: inline keyboards + plain-language intent parse.

Callback data budget: Telegram max 64 bytes.
Format:
  L:t:<event_id>  took
  L:s:<event_id>  skip
  L:w:<event_id>  later/watch
  L:bs:<event_id> bounce strong
  L:bw:<event_id> bounce weak
  L:bn:<event_id> bounce none
  L:bf:<event_id> bounce failed
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# telebot types imported lazily so unit tests need no telegram package mock if unused


def fire_action_keyboard(event_id: int):
    """Took / Skip / Later under a mover (or target) fire."""
    from telebot import types

    eid = int(event_id)
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Took", callback_data=f"L:t:{eid}"),
        types.InlineKeyboardButton("⏭ Skip", callback_data=f"L:s:{eid}"),
        types.InlineKeyboardButton("👁 Later", callback_data=f"L:w:{eid}"),
    )
    return kb


def bounce_keyboard(event_id: int):
    """Bounce quality after a Took."""
    from telebot import types

    eid = int(event_id)
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("Strong ↑", callback_data=f"L:bs:{eid}"),
        types.InlineKeyboardButton("Weak", callback_data=f"L:bw:{eid}"),
    )
    kb.row(
        types.InlineKeyboardButton("None", callback_data=f"L:bn:{eid}"),
        types.InlineKeyboardButton("Failed AD", callback_data=f"L:bf:{eid}"),
    )
    return kb


def parse_callback(data: str) -> Optional[Tuple[str, int]]:
    """Return (action, event_id) or None.

    action: took | skip | watch | bounce_strong | bounce_weak | bounce_none | bounce_failed
    """
    if not data or not data.startswith("L:"):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    _, code, eid_s = parts
    try:
        eid = int(eid_s)
    except ValueError:
        return None
    mapping = {
        "t": "took",
        "s": "skip",
        "w": "watch",
        "bs": "bounce_strong",
        "bw": "bounce_weak",
        "bn": "bounce_none",
        "bf": "bounce_failed",
    }
    action = mapping.get(code)
    if not action:
        return None
    return action, eid


def desk_text(
    *,
    learning_on: bool,
    recent_n: int = 0,
    open_trades_n: int = 0,
) -> str:
    """Single home screen — not a command encyclopedia."""
    lines = [
        "DESK — trading assistant",
        "",
        "When a dump fires, use the buttons on the alert:",
        "  Took · Skip · Later  (no typing)",
        "",
        "Or just type:",
        "  took / skip / later",
        "  brief · coach · open",
        "  pride",
        "",
    ]
    if learning_on:
        lines.append(f"Memory: last events logged ≈ {recent_n} shown on /events")
        lines.append(f"Journal open trades: {open_trades_n}")
        lines.append("")
        lines.append("Chat: say what you did — I label the latest fire.")
    else:
        lines.append("Learning is OFF (FEATURE_LEARNING). Sensors still work.")
    lines.extend(
        [
            "",
            "Sensors (power tools): /a /l /p /mw /movers /af",
            "Status: /s",
            "Full old-style labels still work: /j  /trade  (optional)",
        ]
    )
    return "\n".join(lines)


# Plain language — keep patterns tight so we do not steal random chat forever
_TOOK = re.compile(
    r"^(took|take|in|filled|bought|layered)\b", re.I
)
_SKIP = re.compile(
    r"^(skip|skipped|pass|nope|ignore|nah)\b", re.I
)
_LATER = re.compile(
    r"^(later|watch|watching|hold)\b", re.I
)
_BRIEF = re.compile(
    r"^(brief|desk|status|summary|what.?s hot|hot)\b", re.I
)
_COACH = re.compile(
    r"^(coach|help me|what do you think|advice)\b", re.I
)
_OPEN = re.compile(
    r"^(open|positions|journal)\b", re.I
)
_PRIDE = re.compile(
    r"^(pride)\b", re.I
)
_EVENTS = re.compile(
    r"^(events|fires|log)\b", re.I
)


def parse_plain_intent(text: str) -> Optional[Dict[str, Any]]:
    """Map free text to a small intent dict, or None if not assistant chat."""
    t = (text or "").strip()
    if not t or t.startswith("/"):
        return None
    # Ignore long essays — those can be /j note later
    if len(t) > 120:
        return None

    if _TOOK.search(t):
        return {"intent": "took", "raw": t}
    if _SKIP.search(t):
        return {"intent": "skip", "raw": t}
    if _LATER.search(t):
        return {"intent": "watch", "raw": t}
    if _PRIDE.search(t):
        return {"intent": "pride", "raw": t}
    if _BRIEF.search(t):
        return {"intent": "brief", "raw": t}
    if _COACH.search(t):
        rest = t.split(None, 1)
        q = rest[1] if len(rest) > 1 else "checklist"
        return {"intent": "coach", "question": q, "raw": t}
    if _OPEN.search(t):
        return {"intent": "open", "raw": t}
    if _EVENTS.search(t):
        return {"intent": "events", "raw": t}
    return None
