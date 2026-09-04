"""Path: red-habit sit / buy. No fixed red count for every name."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PathHabit:
    chosen_tf: str
    faster_tfs: list[str] = field(default_factory=list)
    chosen_tf_reds_into_met: int | None = None
    faster_tf_reds_at_low: int | None = None
    vol_at_bottom_usd: float | None = None
    habit_ready: bool = False
    example_hint: str | None = None

    @classmethod
    def from_play(cls, play: dict[str, Any]) -> "PathHabit":
        return cls(
            chosen_tf=str(play.get("chosen_tf") or play.get("tf") or "15m"),
            faster_tfs=list(play.get("faster_tfs") or []),
            chosen_tf_reds_into_met=play.get("chosen_tf_reds_into_met"),
            faster_tf_reds_at_low=play.get("faster_tf_reds_at_low"),
            vol_at_bottom_usd=play.get("vol_at_bottom_usd"),
            habit_ready=bool(play.get("habit_ready", False)),
            example_hint=play.get("example_hint"),
        )


@dataclass
class PathSnapshot:
    """Live reds / volume for Path weigh."""

    chosen_tf_reds: int = 0
    faster_tf_reds: dict[str, int] = field(default_factory=dict)
    volume_at_ad_usd: float = 0.0
    at_ad: bool = False
    ad_met: bool = False
    board_panic: bool = False


@dataclass
class PathDecision:
    action: str  # buy | sit | wait
    why: str
    habit_match: bool = False


def evaluate_path(habit: PathHabit, snap: PathSnapshot) -> PathDecision:
    """
    habit_ready false → sit on first/second red of chosen TF
    (board-wide panic still buys).

    When AD met + at AD + habit_ready true → BUY if chosen TF habit OR
    faster TF reds+volume match — even on first red of chosen TF.
    No fixed 15m≥3 for every name.
    """
    if snap.board_panic:
        return PathDecision(
            action="buy",
            why="board-wide panic — buy without red-habit wait",
            habit_match=True,
        )

    if not snap.ad_met or not snap.at_ad:
        return PathDecision(
            action="wait",
            why="AD not met or current price not at AD",
        )

    # At AD + met
    if not habit.habit_ready:
        # Sit on first or second red of chosen TF
        if snap.chosen_tf_reds <= 2:
            return PathDecision(
                action="sit",
                why=(
                    f"habit_ready false — sit on red {snap.chosen_tf_reds} "
                    f"of {habit.chosen_tf}"
                ),
            )
        # Past second red without habit: still no fixed template buy —
        # sit / wait for human weigh unless panic
        return PathDecision(
            action="sit",
            why="habit_ready false — no chart habit to match; sit",
        )

    # habit_ready true: check chosen TF match OR faster TF hint
    chosen_match = (
        habit.chosen_tf_reds_into_met is not None
        and snap.chosen_tf_reds >= habit.chosen_tf_reds_into_met
    )

    faster_match = False
    if habit.faster_tfs and habit.faster_tf_reds_at_low is not None:
        need_vol = habit.vol_at_bottom_usd or 0.0
        for tf in habit.faster_tfs:
            reds = snap.faster_tf_reds.get(tf, 0)
            if reds >= habit.faster_tf_reds_at_low and snap.volume_at_ad_usd >= need_vol:
                faster_match = True
                break

    if chosen_match or faster_match:
        parts = []
        if chosen_match:
            parts.append(
                f"{habit.chosen_tf} reds {snap.chosen_tf_reds} "
                f"match habit {habit.chosen_tf_reds_into_met}"
            )
        if faster_match:
            parts.append("faster TF reds+volume at the line")
        return PathDecision(
            action="buy",
            why="habit match — " + "; ".join(parts),
            habit_match=True,
        )

    # At AD, habit ready, but no match yet — sit (no smaller-TF bottom hint)
    return PathDecision(
        action="sit",
        why="at AD but no chosen-TF or faster-TF habit match yet",
    )
