#!/usr/bin/env python3
"""Replay a hung plan against a canned tape. Simulated fills only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from machine.engine import evaluate, reset_runtime  # noqa: E402
from machine.plays import load_play  # noqa: E402
from machine.settings import live_orders_allowed  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulate one hung plan. No live orders.")
    ap.add_argument("play_id", nargs="?", default="AGIUSDT_4h")
    ap.add_argument("--last", type=float, required=True, help="current price")
    ap.add_argument("--reds", type=int, default=1)
    ap.add_argument("--faster-reds", type=int, default=0)
    ap.add_argument("--vol-spike", action="store_true")
    ap.add_argument("--board-panic", action="store_true")
    args = ap.parse_args()
    if live_orders_allowed():
        print("REFUSE: live orders are hard-off", file=sys.stderr)
        return 2
    reset_runtime()
    play = load_play(args.play_id)
    result = evaluate(
        play,
        {
            "current_price": args.last,
            "chosen_tf_reds": args.reds,
            "faster_tf_reds": args.faster_reds,
            "vol_spike": args.vol_spike,
            "board_panic": args.board_panic,
        },
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
