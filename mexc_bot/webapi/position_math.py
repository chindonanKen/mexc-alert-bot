"""Positions tab math + fill collapse (AD Desk).

Futures rows use contract size / face / mark. Spot rows use qty × price.
Never apply futures scale to a spot row.

Layers and fill-walk entities are one row per fully filled order.
Partial fills of an in-progress order stay off the tab.
"""

from __future__ import annotations

import json
from typing import Any

_PARTIAL_STATUS = frozenset(
    {
        "NEW",
        "PARTIAL",
        "PARTIALLY_FILLED",
        "PARTIALLYFILLED",
        "PENDING",
        "LIVE",
        "UNFILLED",
        "1",
    }
)
_FILLED_STATUS = frozenset({"FILLED", "CLOSED", "DONE", "2", "COMPLETE", "COMPLETED"})


def parse_raw(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        inner = obj.get("raw")
        if isinstance(inner, dict):
            return inner
        raw_json = obj.get("raw_json")
        if isinstance(raw_json, str) and raw_json.strip():
            try:
                parsed = json.loads(raw_json)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        if inner is None and raw_json is None:
            return obj if _looks_like_exchange_raw(obj) else {}
        return {}
    if isinstance(obj, str) and obj.strip():
        try:
            parsed = json.loads(obj)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _looks_like_exchange_raw(d: dict[str, Any]) -> bool:
    keys = set(d.keys())
    return bool(
        keys
        & {
            "orderId",
            "order_id",
            "origQty",
            "orig_qty",
            "contractSize",
            "contract_size",
            "executedQty",
        }
    )


def contract_size_from_raw(raw: Any) -> float:
    """Futures contract multiplier. Missing → 1.0. Never use this on spot."""
    d = raw if isinstance(raw, dict) else parse_raw(raw)
    if not isinstance(d, dict):
        return 1.0
    for key in ("contractSize", "contract_size", "cs", "faceValue", "face_value"):
        val = d.get(key)
        if val in (None, ""):
            continue
        try:
            n = float(val)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return 1.0


def order_id_from_fill(fill: dict[str, Any]) -> str:
    for key in ("order_id", "orderId"):
        val = fill.get(key)
        if val not in (None, ""):
            return str(val)
    raw = parse_raw(fill)
    for key in ("orderId", "order_id", "ordId"):
        val = raw.get(key)
        if val not in (None, ""):
            return str(val)
    return ""


def _sf(val: Any) -> float | None:
    if val in (None, ""):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _entry_px(d: dict[str, Any]) -> float:
    for key in ("entry_display", "avg_entry", "entry_avg", "hold_avg"):
        n = _sf(d.get(key))
        if n is not None and n > 0:
            return n
    return 0.0


def _mark_px(d: dict[str, Any]) -> float | None:
    if d.get("mark_price") is not None:
        return _sf(d.get("mark_price"))
    if d.get("mark") is not None:
        return _sf(d.get("mark"))
    return None


def _is_short(d: dict[str, Any]) -> bool:
    if d.get("position_type") == 2:
        return True
    return str(d.get("position_side") or d.get("side") or "long").lower() == "short"


def is_order_fully_filled(order: dict[str, Any]) -> bool:
    """True only when the order is complete. In-progress partials are False."""
    if order.get("_fully_filled") is True:
        return True
    raw = parse_raw(order)
    status = (
        str(
            order.get("_order_status")
            or order.get("order_status")
            or raw.get("status")
            or raw.get("order_status")
            or raw.get("state")
            or ""
        )
        .upper()
        .replace(" ", "_")
    )
    orig = _sf(
        order.get("_orig_qty")
        or raw.get("origQty")
        or raw.get("orig_qty")
        or raw.get("volume")
    )
    filled = _sf(
        order.get("_filled_qty")
        or order.get("qty")
        or raw.get("executedQty")
        or raw.get("cumExecQty")
        or raw.get("dealVol")
    )
    if status in _FILLED_STATUS:
        return True
    if orig is not None and filled is not None:
        return filled + 1e-9 >= orig
    if status in _PARTIAL_STATUS:
        return False
    # Legacy journal fill with no order size — treat as its own completed row.
    return True


def collapse_fills_to_orders(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One dict per order_id (plus side). Fills with no id stay one-each (legacy)."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    singles: list[dict[str, Any]] = []
    for i, fill in enumerate(fills or []):
        if not isinstance(fill, dict):
            continue
        oid = order_id_from_fill(fill)
        side = str(fill.get("side") or "").upper() or "?"
        if not oid:
            row = dict(fill)
            row["_fully_filled"] = True
            row["_order_id"] = f"legacy-{fill.get('id') or i}"
            row["_fill_count"] = 1
            singles.append(row)
            continue
        groups.setdefault((oid, side), []).append(fill)

    out: list[dict[str, Any]] = []
    for (oid, _side), parts in groups.items():
        out.append(_merge_order_fills(oid, parts))
    out.extend(singles)
    out.sort(key=lambda x: float(x.get("ts") or 0))
    return out


def fully_filled_orders(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse by order_id, then drop in-progress / partial orders."""
    return [o for o in collapse_fills_to_orders(fills) if is_order_fully_filled(o)]


def _merge_order_fills(oid: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    parts = sorted(parts, key=lambda x: float(x.get("ts") or 0))
    qty = 0.0
    quote = 0.0
    orig: float | None = None
    filled_hint: float | None = None
    status = ""
    raw_acc: dict[str, Any] = {}
    for p in parts:
        q = float(p.get("qty") or 0)
        px = float(p.get("price") or 0)
        qq = p.get("quote_qty")
        qty += q
        quote += float(qq) if qq not in (None, "") else px * q
        raw = parse_raw(p)
        if raw:
            raw_acc.update(raw)
            o = _sf(raw.get("origQty") or raw.get("orig_qty") or raw.get("volume"))
            if o is not None:
                orig = o
            fv = _sf(raw.get("executedQty") or raw.get("cumExecQty") or raw.get("dealVol"))
            if fv is not None:
                filled_hint = max(filled_hint or 0.0, fv)
            st = raw.get("status") or raw.get("order_status") or raw.get("state")
            if st not in (None, ""):
                status = str(st)
    vwap = (quote / qty) if qty else 0.0
    first, last = parts[0], parts[-1]
    raw_acc["orderId"] = oid
    if orig is not None:
        raw_acc["origQty"] = orig
    raw_acc["executedQty"] = filled_hint if filled_hint is not None else qty
    if status:
        raw_acc["status"] = status
    row = dict(first)
    row.update(
        {
            "id": first.get("id"),
            "ts": last.get("ts") or first.get("ts"),
            "price": vwap,
            "qty": qty,
            "quote_qty": quote,
            "side": first.get("side"),
            "raw": raw_acc,
            "_order_id": oid,
            "_fill_count": len(parts),
            "_orig_qty": orig,
            "_filled_qty": filled_hint if filled_hint is not None else qty,
            "_order_status": status,
        }
    )
    return row


def tag_book(d: dict[str, Any]) -> None:
    market = str(d.get("market") or "spot").lower()
    book = "futures" if market == "futures" else "spot"
    d["book"] = book
    d["math"] = book


def apply_open_mark_math(d: dict[str, Any]) -> None:
    """Set book/math and remaining notional / uPnL. Mutates ``d``.

    Spot: qty × price. Futures: qty × contract_size × mark.
    Spot never reads contract_size or exchange futures uPnL.
    """
    tag_book(d)
    rem = float(d.get("size_remaining") or d.get("remaining_qty") or 0)
    entry = _entry_px(d)
    mark = _mark_px(d)
    raw = d.get("raw") if isinstance(d.get("raw"), dict) else parse_raw(d)
    short = _is_short(d)
    side = -1.0 if short else 1.0

    if d["book"] == "futures":
        cs = float(d.get("contract_size") or 0) or contract_size_from_raw(raw) or 1.0
        d["contract_size"] = cs
        if rem <= 0:
            return
        exch_upnl = d.get("unrealized_pnl")
        if exch_upnl not in (None, ""):
            upnl = float(exch_upnl)
            d["upnl_usd_est"] = upnl
            if mark is None and rem > 0 and cs > 0 and entry > 0:
                derived = entry + side * upnl / (rem * cs)
                d["mark_price"] = derived
                d["mark"] = derived
                if not d.get("mark_source"):
                    d["mark_source"] = "mexc_position"
                mark = derived
            if mark is not None:
                d["remaining_mark_usd"] = rem * cs * float(mark)
            if mark is not None and entry > 0:
                d["upnl_pct"] = round((float(mark) - entry) / entry * 100.0, 3)
            return
        if mark is None or entry <= 0:
            return
        mark_f = float(mark)
        d["upnl_usd_est"] = (mark_f - entry) * rem * cs * side
        d["remaining_mark_usd"] = rem * cs * mark_f
        return

    d["contract_size"] = 1.0
    if rem <= 0 or entry <= 0 or mark is None:
        return
    mark_f = float(mark)
    d["upnl_usd_est"] = (mark_f - entry) * rem
    d["remaining_mark_usd"] = rem * mark_f
