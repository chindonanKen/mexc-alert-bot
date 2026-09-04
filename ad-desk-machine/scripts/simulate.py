#!/usr/bin/env python3
"""Tiny simulator CLI — feed synthetic prints; score without MEXC.

Usage (from project root, venv active):
  python scripts/simulate.py
  python scripts/simulate.py --play data/plays/demo_habit.json --dump
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from machine.engine import Engine
from machine.feeds import Print, descending_dump


def main() -> int:
    ap = argparse.ArgumentParser(description="AD Desk Machine synthetic print feeder")
    ap.add_argument("--play", type=str, help="path to play JSON")
    ap.add_argument("--plays-dir", type=str, default=str(ROOT / "data" / "plays"))
    ap.add_argument("--dump", action="store_true", help="run a descending synthetic dump")
    ap.add_argument("--json", action="store_true", help="print decisions as JSON lines")
    args = ap.parse_args()

    eng = Engine()
    if args.play:
        plan = eng.load_play_file(args.play)
        plans = [plan]
    else:
        plans = eng.load_plays_dir(args.plays_dir)

    if not plans:
        print("no plays loaded", file=sys.stderr)
        return 1

    print(f"hung {len(plans)} plan(s); live_orders_allowed={eng.live_orders_allowed}")

    for plan in plans:
        if not args.dump:
            continue
        # Dump from above AD into the band
        start = plan.ad.top * 0.95
        end = plan.ad.bottom * 0.99
        prints = descending_dump(
            plan.name,
            start,
            end,
            steps=12,
            volume_usd=float(plan.habit.vol_at_bottom_usd or 50_000),
            faster_tf=plan.habit.faster_tfs[0] if plan.habit.faster_tfs else None,
        )
        for pr in prints:
            # If habit needs chosen reds match, ramp already in dump
            if plan.habit.habit_ready and plan.habit.chosen_tf_reds_into_met:
                # ensure last prints can match
                pass
            r = eng.on_print(pr)
            if args.json:
                print(json.dumps(r))
            else:
                if r["action"] != "wait":
                    print(
                        f"{plan.name} px={pr.price:.6g} reds={pr.chosen_tf_reds} "
                        f"→ {r['action']}: {r['why']}"
                    )

    print("--- Machine log ---")
    for e in eng.log.as_list():
        print(f"{e['manila']} {e.get('name') or '-'} {e['action']} {e['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
