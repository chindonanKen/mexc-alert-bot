"""P1 chip honesty — process/AD chips must match the taught incident.

Does not invent coach judgment. Only:
- drop illegal dual ad_met + ad_missed
- apply owner-curated chip sets for known lesson ids
- keep free structured tags (sym/base/ts/px/ev/…)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set

from .integrity import ALLOWED_BEHAVIOR

ALLOWED_AD = frozenset({"ad_met", "ad_missed"})
ALLOWED_PROCESS = frozenset(b for b in ALLOWED_BEHAVIOR if b)

# Owner-reviewed chips for the first 19 prod lessons (id → process/AD only).
# Timestamp/incident is separate (ts:/px: tags).
OWNER_LESSON_CHIPS: Dict[int, List[str]] = {
    2: ["plan_ok", "ad_missed"],  # KORU skip — AD not reached
    5: ["plan_ok", "ad_met"],  # SYN long AD
    6: ["plan_ok", "ad_met"],
    9: ["plan_ok", "ad_met"],  # AXTI wait into zone
    10: ["plan_ok", "ad_met"],  # AXTI layered take
    12: ["plan_ok", "ad_missed"],  # ASTEROID structure / slept
    14: ["plan_ok", "ad_missed"],  # 1000RATS no AD
    15: ["plan_ok", "ad_met"],
    16: ["plan_ok", "ad_met", "process_skip"],  # good setup, missed awake
    17: ["fomo", "rule_break", "ad_missed"],  # SYN FOMO
    18: ["plan_ok", "hesitant", "ad_met"],  # HFT size
    19: ["plan_ok", "ad_met"],  # HFT good opportunity
    20: ["plan_ok", "ad_met", "free_coins", "free_tp_ok"],
    21: ["plan_ok", "ad_missed"],  # failed AD / wait 3rd rule
    22: ["ad_met", "hesitant", "process_skip"],  # BTW late AD chicken
    23: ["plan_ok", "ad_met", "false_panic"],  # BLUAI first-candle wait (zone exists)
    24: ["plan_ok", "false_panic", "ad_missed"],  # BTW re-top patience
    25: ["rule_break"],  # delist ban
    26: ["plan_ok", "ad_missed"],  # BANANAS not in zone
}


def sanitize_process_chips(chips: Sequence[str]) -> List[str]:
    """Closed set + no dual ad_met/ad_missed (last AD chip wins)."""
    out: List[str] = []
    seen: Set[str] = set()
    ad_last: Optional[str] = None
    for c in chips or []:
        s = str(c or "").strip().lower().replace(" ", "_")
        if not s or ":" in s:
            continue
        if s in ALLOWED_AD:
            ad_last = s
            continue
        if s in ALLOWED_PROCESS and s not in seen:
            out.append(s)
            seen.add(s)
    if ad_last:
        out.append(ad_last)
    return out


def honest_chips_for_lesson(
    lesson_id: Optional[int],
    tags_or_chips: Sequence[str],
) -> List[str]:
    """Prefer owner map; else sanitize existing process chips."""
    if lesson_id is not None and int(lesson_id) in OWNER_LESSON_CHIPS:
        return list(OWNER_LESSON_CHIPS[int(lesson_id)])
    chips = [t for t in tags_or_chips if t and ":" not in str(t)]
    return sanitize_process_chips(chips)


def merge_tags_with_honest_chips(
    tags: Sequence[str],
    *,
    lesson_id: Optional[int] = None,
    chips_override: Optional[Sequence[str]] = None,
) -> List[str]:
    """Keep structured tags; replace free process/AD chips with honest set."""
    structured = []
    for t in tags or []:
        ts = str(t or "").strip()
        if not ts:
            continue
        if ":" in ts:
            # drop old free process mis-tags stored as structured? keep all :
            structured.append(ts)
        # free chips dropped; re-added below
    if chips_override is not None:
        chips = sanitize_process_chips(chips_override)
    else:
        chips = honest_chips_for_lesson(lesson_id, tags)
    # structured first, then chips
    out = list(structured)
    for c in chips:
        if c not in out:
            out.append(c)
    return out
