"""Smart PnL aggregates for AD Desk — remaining-cost money facts.

P5: full closed history, no day cutoff. P6: same leftover math as Positions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .position_math import ensure_position_display_fields
from .positions_enrich import list_position_entities


def _f(x: Any) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _n(x: Any) -> float:
    v = _f(x)
    return 0.0 if v is None else v


def _window_cutoff(window: str) -> Optional[float]:
    """Score window only. History list is never cut by day."""
    w = (window or "all").lower()
    if w in ("all", "forever", "teach", ""):
        return None
    import time

    now = time.time()
    if w == "7d":
        return now - 7 * 86400
    if w == "30d":
        return now - 30 * 86400
    if w == "90d":
        return now - 90 * 86400
    return None


def _closed_row(e: dict) -> Dict[str, Any]:
    ensure_position_display_fields(e)
    return {
        "symbol": e.get("symbol") or "",
        "market": (e.get("market") or e.get("book") or "spot"),
        "book": e.get("book") or e.get("market") or "spot",
        "bought_usd": _n(e.get("bought_usd")),
        "sold_usd": _n(e.get("sold_usd")),
        "realized_pnl_usd": _n(e.get("realized_pnl_usd")),
        "realized_pnl_pct": _n(e.get("realized_pnl_pct")),
        "remaining_cost_usd": _n(e.get("remaining_cost_usd")),
        "leftover_avg": _n(e.get("leftover_avg")),
        "remaining_avg": _n(e.get("remaining_avg") or e.get("leftover_avg")),
        "entry_avg": _n(e.get("entry_avg") or e.get("entry_display")),
        "exit_avg": _n(e.get("exit_avg")),
        "size_qty": _n(e.get("size_qty")),
        "size_remaining": _n(e.get("size_remaining")),
        "closed_at": e.get("closed_at"),
        "opened_at": e.get("opened_at"),
        "entity_key": e.get("entity_key"),
        "outcome": e.get("outcome"),
    }


def _open_row(e: dict) -> Dict[str, Any]:
    ensure_position_display_fields(e)
    return {
        "symbol": e.get("symbol") or "",
        "market": (e.get("market") or e.get("book") or "spot"),
        "book": e.get("book") or e.get("market") or "spot",
        "bought_usd": _n(e.get("bought_usd")),
        "sold_usd": _n(e.get("sold_usd")),
        "realized_pnl_usd": _n(e.get("realized_pnl_usd")),
        "remaining_mark_usd": _n(e.get("remaining_mark_usd")),
        "remaining_cost_usd": _n(e.get("remaining_cost_usd")),
        "leftover_avg": _n(e.get("leftover_avg") or e.get("entry_display")),
        "remaining_avg": _n(
            e.get("remaining_avg") or e.get("leftover_avg") or e.get("entry_display")
        ),
        "entry_avg": _n(e.get("entry_avg") or e.get("entry_display")),
        "size_remaining": _n(e.get("size_remaining")),
        "upnl_usd_est": _n(e.get("upnl_usd_est")),
        "free_coins": bool(e.get("free_coins")),
        "free_coins_status": e.get("free_coins_status") or "",
        "principal_recovered": bool(e.get("principal_recovered")),
        "hold_hours": _n(e.get("hold_hours")),
        "entity_key": e.get("entity_key"),
        "money_truth": e.get("money_truth"),
    }


def build_pnl_summary(
    user_id: int,
    *,
    window: str = "all",
) -> Dict[str, Any]:
    entities = list_position_entities(user_id, include_closed=True, closed_limit=0)
    cutoff = _window_cutoff(window)

    opens = [e for e in entities if e.get("status") == "open"]
    closed = [e for e in entities if e.get("status") == "closed"]
    closed.sort(
        key=lambda x: float(x.get("closed_at") or x.get("opened_at") or 0),
        reverse=True,
    )

    def in_window(e: dict) -> bool:
        if cutoff is None:
            return True
        ts = _f(e.get("closed_at") or e.get("opened_at")) or 0
        return ts >= cutoff

    # Score may honor 7d/30d; the history list is always the full book.
    closed_w = [e for e in closed if in_window(e)]

    open_mark = sum(_n(e.get("remaining_mark_usd")) for e in opens)
    open_cost = sum(_n(e.get("remaining_cost_usd")) for e in opens)
    open_upnl = sum(_n(e.get("upnl_usd_est")) for e in opens)
    free_bags = [
        e for e in opens if e.get("free_coins") and not e.get("is_hold")
    ]
    hold_bags = [e for e in opens if e.get("is_hold") or e.get("position_book") == "hold"]
    free_mark = sum(_n(e.get("remaining_mark_usd")) for e in free_bags)
    hold_mark = sum(_n(e.get("remaining_mark_usd")) for e in hold_bags)
    at_risk_mark = max(0.0, open_mark - free_mark - hold_mark)

    realized = 0.0
    win_n = miss_n = flat_n = 0
    win_usd = miss_usd = 0.0
    best = worst = None
    for e in closed_w:
        r = _n(e.get("realized_pnl_usd"))
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
        _n(e.get("realized_pnl_usd"))
        for e in closed_w
        if (e.get("market") or "").lower() == "spot"
    )
    fut_r = sum(
        _n(e.get("realized_pnl_usd"))
        for e in closed_w
        if (e.get("market") or "").lower() == "futures"
    )

    return {
        "window": window or "all",
        "cutoff_days": None if cutoff is None else None,
        "history_cutoff": None,
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
            "closed_all_n": len(closed),
        },
        "by_book": {
            "spot_realized_usd": round(spot_r, 2),
            "futures_realized_usd": round(fut_r, 2),
        },
        "open_book": [_open_row(e) for e in opens],
        "closed_history": [_closed_row(e) for e in closed],
        "free_bags": [
            {
                "symbol": e.get("symbol") or "",
                "remaining_mark_usd": _n(e.get("remaining_mark_usd")),
                "bought_usd": _n(e.get("bought_usd")),
                "sold_usd": _n(e.get("sold_usd")),
                "realized_pnl_usd": _n(e.get("realized_pnl_usd")),
                "remaining_cost_usd": _n(e.get("remaining_cost_usd")),
                "leftover_avg": _n(e.get("leftover_avg") or e.get("entry_display")),
                "remaining_avg": _n(
                    e.get("remaining_avg")
                    or e.get("leftover_avg")
                    or e.get("entry_display")
                ),
                "entity_key": e.get("entity_key"),
            }
            for e in free_bags
        ],
    }
