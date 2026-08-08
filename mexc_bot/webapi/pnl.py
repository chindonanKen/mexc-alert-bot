"""Smart PnL aggregates for AD Desk — teach_ok / exchange-leaning money facts."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .positions_enrich import list_position_entities


def _f(x: Any) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _window_cutoff(window: str) -> Optional[float]:
    w = (window or "30d").lower()
    now = time.time()
    if w in ("all", "forever", "teach"):
        return None
    if w == "7d":
        return now - 7 * 86400
    if w == "30d":
        return now - 30 * 86400
    if w == "90d":
        return now - 90 * 86400
    return now - 30 * 86400


def build_pnl_summary(
    user_id: int,
    *,
    window: str = "30d",
) -> Dict[str, Any]:
    entities = list_position_entities(user_id, include_closed=True, closed_limit=80)
    cutoff = _window_cutoff(window)

    opens = [e for e in entities if e.get("status") == "open"]
    closed = [e for e in entities if e.get("status") == "closed"]

    def in_window(e: dict) -> bool:
        if cutoff is None:
            return True
        ts = _f(e.get("closed_at") or e.get("opened_at")) or 0
        return ts >= cutoff

    closed_w = [e for e in closed if in_window(e)]

    open_mark = sum(_f(e.get("remaining_mark_usd")) or 0 for e in opens)
    open_cost = sum(_f(e.get("remaining_cost_usd")) or 0 for e in opens)
    open_upnl = sum(_f(e.get("upnl_usd_est")) or 0 for e in opens)
    free_bags = [
        e
        for e in opens
        if e.get("free_coins") and not e.get("is_hold")
    ]
    hold_bags = [e for e in opens if e.get("is_hold") or e.get("position_book") == "hold"]
    free_mark = sum(_f(e.get("remaining_mark_usd")) or 0 for e in free_bags)
    hold_mark = sum(_f(e.get("remaining_mark_usd")) or 0 for e in hold_bags)
    at_risk_mark = max(0.0, open_mark - free_mark - hold_mark)

    realized = 0.0
    win_n = miss_n = flat_n = 0
    win_usd = miss_usd = 0.0
    best = worst = None
    for e in closed_w:
        r = _f(e.get("realized_pnl_usd"))
        if r is None:
            continue
        realized += r
        if r > 0.5:
            win_n += 1
            win_usd += r
        elif r < -0.5:
            miss_n += 1
            miss_usd += r
        else:
            flat_n += 1
        if best is None or r > best["realized_pnl_usd"]:
            best = {
                "symbol": e.get("symbol"),
                "market": e.get("market"),
                "realized_pnl_usd": r,
            }
        if worst is None or r < worst["realized_pnl_usd"]:
            worst = {
                "symbol": e.get("symbol"),
                "market": e.get("market"),
                "realized_pnl_usd": r,
            }

    spot_r = sum(
        _f(e.get("realized_pnl_usd")) or 0
        for e in closed_w
        if (e.get("market") or "").lower() == "spot"
    )
    fut_r = sum(
        _f(e.get("realized_pnl_usd")) or 0
        for e in closed_w
        if (e.get("market") or "").lower() == "futures"
    )

    open_book = []
    for e in opens:
        open_book.append(
            {
                "symbol": e.get("symbol"),
                "market": e.get("market"),
                "bought_usd": e.get("bought_usd"),
                "sold_usd": e.get("sold_usd"),
                "realized_pnl_usd": e.get("realized_pnl_usd"),
                "remaining_mark_usd": e.get("remaining_mark_usd"),
                "remaining_cost_usd": e.get("remaining_cost_usd"),
                "free_coins": bool(e.get("free_coins")),
                "free_coins_status": e.get("free_coins_status"),
                "principal_recovered": e.get("principal_recovered"),
                "hold_hours": e.get("hold_hours"),
                "entity_key": e.get("entity_key"),
                "money_truth": e.get("money_truth"),
            }
        )

    return {
        "window": window,
        "bankroll": {
            "open_mark_usd": round(open_mark, 2),
            "at_risk_mark_usd": round(at_risk_mark, 2),
            "open_cost_usd": round(open_cost, 2),
            "open_upnl_usd": round(open_upnl, 2),
            "free_bags_n": len(free_bags),
            "free_mark_usd": round(free_mark, 2),
            "hold_bags_n": len(hold_bags),
            "hold_mark_usd": round(hold_mark, 2),
            "open_n": len(opens),
        },
        "realized": {
            "pnl_usd": round(realized, 2),
            "win_n": win_n,
            "miss_n": miss_n,
            "flat_n": flat_n,
            "win_usd": round(win_usd, 2),
            "miss_usd": round(miss_usd, 2),
            "best": best,
            "worst": worst,
            "closed_n": len(closed_w),
        },
        "by_book": {"spot_realized_usd": round(spot_r, 2), "futures_realized_usd": round(fut_r, 2)},
        "open_book": open_book,
        "free_bags": [
            {
                "symbol": e.get("symbol"),
                "remaining_mark_usd": e.get("remaining_mark_usd"),
                "bought_usd": e.get("bought_usd"),
                "sold_usd": e.get("sold_usd"),
                "realized_pnl_usd": e.get("realized_pnl_usd"),
                "entity_key": e.get("entity_key"),
            }
            for e in free_bags
        ],
    }
