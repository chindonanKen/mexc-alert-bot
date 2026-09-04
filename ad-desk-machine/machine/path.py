"""Path live-read. Sit / buy from habit_ready, watch_only, reds, board panic."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _int(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def first_or_second_red(reds: Any) -> bool:
    n = _int(reds)
    return n in (1, 2)


def path_decision(play: Dict[str, Any], tape: Dict[str, Any]) -> Dict[str, Any]:
    """Hung Path lock.

    - watch_only blocks all buys until lifted.
    - habit_ready false → sit first/second chosen TF red.
      Board-wide panic still buys.
    - habit_ready true + AD met + at AD → BUY on chosen TF reds ≥ habit
      OR faster TF reds + volume match — even on the first chosen red.
    """
    if play.get("watch_only"):
        return {
            "action": "sit",
            "buy": False,
            "reason": "watch_only",
            "decision": "Watch only. Buys blocked until lifted.",
        }

    reds = _int(tape.get("chosen_tf_reds") if tape.get("chosen_tf_reds") is not None else tape.get("reds"))
    faster = _int(tape.get("faster_tf_reds"))
    board_panic = bool(tape.get("board_panic") or tape.get("panic_board"))
    met = bool(tape.get("met") or play.get("met"))
    at_ad = bool(tape.get("at_ad") or tape.get("met_now"))
    vol_match = bool(tape.get("volume_match") or tape.get("vol_spike"))
    habit_ready = bool(play.get("habit_ready"))
    habit = _int(play.get("chosen_tf_reds_into_met")) or 1

    if habit_ready and met and at_ad:
        if reds is not None and reds >= habit:
            word = "First" if reds == 1 else f"{reds}"
            return {
                "action": "buy",
                "buy": True,
                "reason": "habit_ready_chosen_reds",
                "decision": f"{word} chosen TF red, habit ready, AD met, taking it.",
            }
        if faster is not None and faster >= 1 and vol_match:
            return {
                "action": "buy",
                "buy": True,
                "reason": "faster_tf_reds_volume",
                "decision": "Faster TF reds and volume match at the AD, taking it.",
            }
        return {
            "action": "wait",
            "buy": False,
            "reason": "habit_ready_waiting_reds",
            "decision": "Habit ready and at the AD, waiting for the red / volume tell.",
        }

    if first_or_second_red(reds) and not board_panic:
        word = "Second" if reds == 2 else "First"
        return {
            "action": "sit",
            "buy": False,
            "reason": "sit_first_second_red",
            "decision": f"{word} red on the chosen TF, sit.",
        }

    if board_panic and met and at_ad:
        return {
            "action": "buy",
            "buy": True,
            "reason": "board_panic",
            "decision": "Board-wide panic at the AD, taking it.",
        }

    if reds is not None and reds >= 3 and met and at_ad:
        return {
            "action": "buy",
            "buy": True,
            "reason": "chosen_reds_past_sit",
            "decision": f"{reds} red on the chosen TF at the AD, taking it.",
        }

    return {
        "action": "wait",
        "buy": False,
        "reason": "wait",
        "decision": "Hung plan written, waiting for the line.",
    }
