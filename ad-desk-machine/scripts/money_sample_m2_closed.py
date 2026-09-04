#!/usr/bin/env python3
"""Closed-book leftover remaining-cost sample (M2). No live orders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from machine.exit import leftover_remaining_cost, leftover_usd  # noqa: E402
from machine.settings import live_orders_allowed  # noqa: E402

# Canned closed cycle: two buys, one sell. Remaining bag cost is FIFO leftover.
SAMPLE = [
    {"side": "buy", "price": 0.0115, "filled_price": 0.0115, "usd": 10.0},
    {"side": "buy", "price": 0.0110, "filled_price": 0.0110, "usd": 15.0},
    {"side": "sell", "price": 0.0139, "filled_price": 0.0139, "usd": 10.0},
]


def main() -> int:
    if live_orders_allowed():
        print("REFUSE: live orders are hard-off", file=sys.stderr)
        return 2
    leftover = leftover_remaining_cost(SAMPLE)
    remaining = leftover_usd(SAMPLE)
    print(
        json.dumps(
            {
                "sample": "m2_closed",
                "fills": SAMPLE,
                "leftover": leftover,
                "remaining_usd": remaining,
                "live_orders_allowed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
