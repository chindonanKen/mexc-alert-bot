"""Consecutive red-candle streak on one timeframe.

Measure live streak on this TF. Compare later to *this chart’s* own
historical bottoms — not a global “wait 3–5” law and not user preference.
P1 stores the count. P2 sizes to how the dump matches printed history.

Definition (agent-canonical):
- A bar is **red** iff close < open (strict). Doji (close == open) breaks the streak.
- Count **closed** bars only. The still-forming bar is excluded by default
  (KlineClient already drops the last forming candle).
- Walk **newest → older**. Streak length N means the last N closed bars are red
  and the bar before that is not red (or there is no older bar).
- Labels: 1st, 2nd, 3rd, 4th, 5th, 6plus — used as **one factor** on the board.

Always computed **per TF independently**. Never mix 5m reds with 1h reds.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

Bar = Dict[str, Any]

# Rule 2.5 window
ENTRY_RED_MIN = 3
ENTRY_RED_MAX = 5


def is_red_bar(bar: Optional[Bar]) -> bool:
    """True only for a completed down candle (close < open)."""
    if not bar:
        return False
    try:
        o = float(bar.get("o") if bar.get("o") is not None else bar.get("open"))
        c = float(bar.get("c") if bar.get("c") is not None else bar.get("close"))
    except (TypeError, ValueError):
        return False
    return c < o


def consecutive_red_streak(
    bars: Sequence[Bar],
    *,
    include_forming: bool = False,
) -> int:
    """How many reds in a row ending at the newest closed bar.

    ``bars`` oldest → newest. If ``include_forming`` is False and the caller
    already stripped the forming bar, count as-is. If True, skip the last bar.
    """
    if not bars:
        return 0
    seq = list(bars)
    if include_forming and len(seq) >= 2:
        seq = seq[:-1]
    n = 0
    for b in reversed(seq):
        if is_red_bar(b):
            n += 1
        else:
            break
    return n


def streak_label(n: int) -> str:
    """Human/agent label: 1st, 2nd, … 5th, 6plus."""
    if n <= 0:
        return "none"
    if n == 1:
        return "1st"
    if n == 2:
        return "2nd"
    if n == 3:
        return "3rd"
    if n == 4:
        return "4th"
    if n == 5:
        return "5th"
    return "6plus"


def entry_window(n: int) -> bool:
    """True if streak is in the historically common 3–5 red band (guideline)."""
    return ENTRY_RED_MIN <= int(n) <= ENTRY_RED_MAX


def first_or_second_red(n: int) -> bool:
    return 0 < int(n) < ENTRY_RED_MIN


def streak_pack(bars: Sequence[Bar], *, include_forming: bool = False) -> Dict[str, Any]:
    """Full P1 payload for one TF."""
    n = consecutive_red_streak(bars, include_forming=include_forming)
    return {
        "red_streak": n,
        "red_label": streak_label(n),
        "first_red": n == 1,
        "first_or_second_red": first_or_second_red(n),
        "entry_red_window": entry_window(n),
    }
