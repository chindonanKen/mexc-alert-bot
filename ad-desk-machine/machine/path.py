"""Path: buy when print tags a hung AD buy layer, or board-wide panic.

Kenneth 2026-09-07 Path RECUT: habit_ready / red-count / faster-TF habit match
are not buy gates. Size owns live volume and grind wait. Chart owns met band.
"""

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
    # Optional Size weigh only (engine may pass 5m vol into Size). Not a Path sit gate.
    require_5m_volume_spike: bool = False
    vol_5m_usual_usd: float | None = None

    @classmethod
    def from_play(cls, play: dict[str, Any]) -> "PathHabit":
        nested = play.get("path") if isinstance(play.get("path"), dict) else {}

        def _pick(key: str, default: Any = None) -> Any:
            if key in play:
                return play[key]
            if key in nested:
                return nested[key]
            return default

        vol5u = _pick("vol_5m_usual_usd", None)
        return cls(
            chosen_tf=str(play.get("chosen_tf") or play.get("tf") or "15m"),
            faster_tfs=list(play.get("faster_tfs") or []),
            chosen_tf_reds_into_met=play.get("chosen_tf_reds_into_met"),
            faster_tf_reds_at_low=play.get("faster_tf_reds_at_low"),
            vol_at_bottom_usd=play.get("vol_at_bottom_usd"),
            habit_ready=bool(play.get("habit_ready", False)),
            example_hint=play.get("example_hint"),
            require_5m_volume_spike=bool(_pick("require_5m_volume_spike", False)),
            vol_5m_usual_usd=float(vol5u) if vol5u is not None else None,
        )


@dataclass
class PathSnapshot:
    """Live tape for Path weigh. Engine sets tagged_hung_ad_buy when print ≤ empty/next AD buy."""

    chosen_tf_reds: int = 0
    faster_tf_reds: dict[str, int] = field(default_factory=dict)
    volume_at_ad_usd: float = 0.0
    volume_usd_5m: float = 0.0
    reds_5m: int = 0
    at_ad: bool = False
    ad_met: bool = False
    board_panic: bool = False
    tagged_hung_ad_buy: bool = False
    tagged_ad_layer: bool = False  # synonym used by older tests


@dataclass
class PathDecision:
    action: str  # buy | sit | wait
    why: str
    habit_match: bool = False  # True when Path allows a take (tag or panic)


def evaluate_path(habit: PathHabit, snap: PathSnapshot) -> PathDecision:
    """
    Path may buy when:
      - board-wide panic, or
      - current price tags a hung AD buy layer (print ≤ empty/next AD-role layer).

    habit_ready, red-count, faster-TF habit match, and require_5m_volume_spike
    do NOT sit-block. Size owns live volume / grind wait. Chart owns met band.
    """
    _ = habit  # habit fields kept for Size / sheet; not Path buy gates

    if snap.board_panic:
        return PathDecision(
            action="buy",
            why="board-wide panic — buy without wait",
            habit_match=True,
        )

    if snap.tagged_hung_ad_buy or snap.tagged_ad_layer:
        return PathDecision(
            action="buy",
            why="tagged hung AD buy layer — Path may buy",
            habit_match=True,
        )

    return PathDecision(
        action="wait",
        why="no hung AD buy layer tagged by print — price has not tagged a buy layer",
    )
