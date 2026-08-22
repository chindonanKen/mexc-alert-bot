"""Position open/exit Telegram pings — fully filled orders only.

Uses the same collapse + full-fill rule as the Positions tab.
A partial fill of an in-progress order is silent.
Never calls Telegram itself; the caller injects a notifier (tests use a sink).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..webapi.position_math import fully_filled_orders, order_id_from_fill


def _tid(fill: dict[str, Any]) -> str:
    return str(fill.get("exchange_trade_id") or fill.get("id") or "")


def _order_key(order: dict[str, Any]) -> str:
    oid = str(order.get("_order_id") or order_id_from_fill(order) or "")
    side = str(order.get("side") or "").upper() or "?"
    return f"{oid}:{side}"


def lifecycle_events_from_fills(
    existing_fills: list[dict[str, Any]],
    new_fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Orders that become fully filled because of ``new_fills``.

    ``existing_fills`` = journal rows already stored before this batch.
    Buy-side complete → opened. Sell-side complete → exited.
    """
    if not new_fills:
        return []
    before_keys = {_order_key(o) for o in fully_filled_orders(list(existing_fills or []))}
    after = list(existing_fills or []) + list(new_fills)
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order in fully_filled_orders(after):
        key = _order_key(order)
        if not key or key in before_keys or key in seen:
            continue
        seen.add(key)
        side = str(order.get("side") or "").lower()
        kind = "opened" if side == "buy" else "exited"
        events.append(
            {
                "kind": kind,
                "side": side or "sell",
                "symbol": order.get("symbol"),
                "market": (order.get("market") or "spot").lower(),
                "qty": order.get("qty"),
                "price": order.get("price"),
                "order_id": order.get("_order_id") or order_id_from_fill(order),
                "fill_count": int(order.get("_fill_count") or 1),
            }
        )
    return events


def format_lifecycle_message(ev: dict[str, Any]) -> str:
    head = "POSITION OPENED" if ev.get("kind") == "opened" else "POSITION EXITED"
    mkt = str(ev.get("market") or "spot").upper()
    side = str(ev.get("side") or "").upper()
    sym = ev.get("symbol") or "?"
    qty = ev.get("qty")
    px = ev.get("price")
    bits = [head, f"{mkt} {side} {sym}".strip()]
    if qty not in (None, "") and px not in (None, ""):
        bits.append(f"qty={qty} @ {px}")
    return "\n".join(bits)


def send_lifecycle_telegram(
    notifier: Optional[Callable[..., None]],
    user_id: int,
    events: list[dict[str, Any]],
    *,
    enabled: bool,
) -> int:
    """One notifier call per opened/exited event. No-op without a sink."""
    if not enabled or notifier is None or not events:
        return 0
    sent = 0
    for ev in events:
        notifier(int(user_id), format_lifecycle_message(ev), parse_mode=None)
        sent += 1
    return sent


def existing_fills_excluding(
    all_fills: list[dict[str, Any]],
    new_fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rows already in the journal before this insert batch."""
    new_tids = {_tid(f) for f in (new_fills or []) if _tid(f)}
    if not new_tids:
        return list(all_fills or [])
    out: list[dict[str, Any]] = []
    for f in all_fills or []:
        tid = _tid(f)
        if tid and tid in new_tids:
            continue
        out.append(f)
    return out
