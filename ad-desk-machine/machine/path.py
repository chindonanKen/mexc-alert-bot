"""Path: buy when current price tags a hung AD buy layer.

habit_ready and red-count / faster-TF habit match are not buy gates.
Size owns live volume and grind wait.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _pick(play: dict[str, Any], key: str, default: Any = None) -> Any:
    """Play root first, then nested `path` block. Unset keys stay default."""
    nested = play.get("path")
    nested = nested if isinstance(nested, dict) else {}
    if key in play and play[key] is not None:
        return play[key]
    if key in nested and nested[key] is not None:
        return nested[key]
    return default


@dataclass
class PathHabit:
    """Hung-plan Path facts. Not used as buy gates (Size owns volume)."""

    chosen_tf: str
    faster_tfs: list[str] = field(default_factory=list)
    chosen_tf_reds_into_met: int | None = None
    faster_tf_reds_at_low: int | None = None
    vol_at_bottom_usd: float | None = None
    habit_ready: bool = False
    example_hint: str | None = None
    require_5m_volume_spike: bool = False
    vol_5m_usual_usd: float | None = None

    @classmethod
    def from_play(cls, play: dict[str, Any]) -> "PathHabit":
        usual = _pick(play, "vol_5m_usual_usd", None)
        try:
            usual_f = float(usual) if usual is not None else None
        except (TypeError, ValueError):
            usual_f = None
        return cls(
            chosen_tf=str(play.get("chosen_tf") or play.get("tf") or "15m"),
            faster_tfs=list(play.get("faster_tfs") or []),
            chosen_tf_reds_into_met=play.get("chosen_tf_reds_into_met"),
            faster_tf_reds_at_low=play.get("faster_tf_reds_at_low"),
            vol_at_bottom_usd=play.get("vol_at_bottom_usd"),
            habit_ready=bool(play.get("habit_ready", False)),
            example_hint=play.get("example_hint"),
            require_5m_volume_spike=bool(_pick(play, "require_5m_volume_spike", False)),
            vol_5m_usual_usd=usual_f,
        )


@dataclass
class PathSnapshot:
    """Live reds / volume / layer tag for Path weigh."""

    chosen_tf_reds: int = 0
    faster_tf_reds: dict[str, int] = field(default_factory=dict)
    volume_at_ad_usd: float = 0.0
    volume_usd_5m: float = 0.0
    reds_5m: int = 0
    at_ad: bool = False
    ad_met: bool = False
    board_panic: bool = False
    tagged_ad_layer: bool = False


@dataclass
class PathDecision:
    action: str  # buy | sit | wait
    why: str
    habit_match: bool = False


def evaluate_path(habit: PathHabit, snap: PathSnapshot) -> PathDecision:
    """
    Path may buy when current price tags a hung AD buy layer.
    Board-wide panic still buys. habit_ready / red-count / faster-TF
    match are not buy gates — Size owns live volume and grind wait.
    """
    _ = habit  # facts stay on the plan for Size; not Path buy gates
    if snap.board_panic:
        return PathDecision(
            action="buy",
            why="board-wide panic — buy",
            habit_match=True,
        )

    if snap.tagged_ad_layer:
        return PathDecision(
            action="buy",
            why="current price tagged hung AD buy layer",
            habit_match=True,
        )

    return PathDecision(
        action="wait",
        why="current price has not tagged a hung AD buy layer",
    )
