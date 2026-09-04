#!/usr/bin/env python3
"""Money sample M2 closed — SYN hung plan through dump buys + bounce sells.

Uses synthetic prints (not live MEXC) so buy layers at/through AD can fill, then
bounce prints fill hung sell layers / exit live-read. live_orders_allowed stays false.

Writes data/money_sample_m2_closed.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from machine.engine import Engine
from machine.feeds import Print, ascending_bounce, descending_dump

MANILA = timezone(timedelta(hours=8))
OUT = ROOT / "data" / "money_sample_m2_closed.json"
PLAY = ROOT / "data" / "plays" / "SYNUSDT_4h.json"


def _pnl_for_name(trades: list[dict], name: str) -> float | None:
    buys = [t for t in trades if t.get("name") == name and t.get("side") == "buy"]
    sells = [t for t in trades if t.get("name") == name and t.get("side") == "sell"]
    if not buys or not sells:
        return None
    cost = sum(float(t["usd"]) for t in buys)
    qty = sum(float(t["usd"]) / float(t["price"]) for t in buys if float(t["price"]) > 0)
    if qty <= 0 or cost <= 0:
        return None
    avg_buy = cost / qty
    # Size-share USD on sell layers treated as bag slice at avg buy; mark vs sell price
    pnl = 0.0
    for s in sells:
        pnl += float(s["usd"]) * (float(s["price"]) / avg_buy - 1.0)
    return round(pnl, 6)


def _stats(closes: list[dict], trades: list[dict]) -> dict:
    pnls: list[float] = []
    for c in closes:
        name = c.get("name")
        if not name:
            continue
        p = _pnl_for_name(trades, name)
        if p is not None:
            pnls.append(p)
            c["pnl_usd"] = p
    if not pnls:
        return {
            "closes_exist": False,
            "expectancy": None,
            "payoff": None,
            "tail": None,
            "note": "still no closes",
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    expectancy = round(sum(pnls) / len(pnls), 6)
    if wins and losses:
        payoff = round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 4)
    elif wins and not losses:
        payoff = None  # undefined — no losing closes
    else:
        payoff = 0.0
    return {
        "closes_exist": True,
        "expectancy": expectancy,
        "payoff": payoff,
        "tail": round(min(pnls), 6),
        "n_closes": len(pnls),
        "note": "closes present — expectancy=mean pnl_usd; payoff=avg_win/|avg_loss|; tail=worst",
    }


def run(play_path: Path = PLAY) -> dict:
    eng = Engine()
    plan = eng.load_play_file(play_path)
    # Sample only: lift watch_only so board-panic dump can fill Size layers.
    # Hung play file stays watch_only; this does not change the play file on disk.
    plan.watch_only = False
    # habit_ready false → need board-wide panic for buys at AD
    eng.set_board_panic(True)

    # Dump through all buy layers (AD + panic) so flat-close is possible
    deepest = min(b.price for b in plan.fills.buy_layers)
    start = plan.ad.band_high * 0.99 if hasattr(plan.ad, "band_high") else plan.ad.top * 0.9
    # chart.AD — use top*0.95 into below deepest
    start = max(plan.ad.top * 0.95, plan.fills.buy_layers[0].price * 1.02)
    dump = descending_dump(
        plan.name,
        start,
        deepest * 0.98,
        steps=16,
        volume_usd=float(plan.habit.vol_at_bottom_usd or 80_000),
        faster_tf=plan.habit.faster_tfs[0] if plan.habit.faster_tfs else "1h",
    )
    for pr in dump:
        eng.on_print(pr)

    eng.set_board_panic(False)

    # Bounce from session low through hung sell layers
    sells = plan.fills.sell_layers
    if sells and plan.state == "live":
        bounce_start = plan.current_price or deepest
        bounce_end = max(s.price for s in sells) * 1.02
        for pr in ascending_bounce(
            plan.name,
            bounce_start,
            bounce_end,
            steps=20,
            volume_usd=250_000,
            faster_tf=plan.habit.faster_tfs[0] if plan.habit.faster_tfs else "1h",
        ):
            eng.on_print(pr)

    now = datetime.now(MANILA)
    stats = _stats(list(eng.closes), list(eng.trades))
    payload = {
        "when": now.strftime("%Y-%m-%d %H:%M PHT"),
        "play": str(play_path.relative_to(ROOT)),
        "note": (
            "Money sample M2 closed. SYN hung plan: board panic dump fills buy layers, "
            "then bounce prints fill sell layers (simulated). live_orders_allowed=false."
        ),
        "live_orders_allowed": False,
        "plan_state": plan.state,
        "buys_filled": sum(1 for b in plan.fills.buy_layers if b.status == "filled"),
        "sells_filled": sum(1 for s in plan.fills.sell_layers if s.status == "filled"),
        "sells_remaining": len(plan.fills.remaining_sells()),
        "log": eng.log.as_list(),
        "trades": eng.trades,
        "closes": eng.closes,
        **stats,
    }
    if not eng.closes:
        payload["plain"] = (
            "still no closes — sells and/or buys not fully flat "
            f"(buys_filled={payload['buys_filled']}, "
            f"sells_filled={payload['sells_filled']}, "
            f"sells_remaining={payload['sells_remaining']})"
        )
    else:
        payload["plain"] = (
            f"closes exist: {len(eng.closes)}; expectancy={stats['expectancy']}; "
            f"payoff={stats['payoff']}; tail={stats['tail']}"
        )
    return payload


def main() -> int:
    payload = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(payload["plain"])
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
