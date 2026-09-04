"""Simulated fills only. Last must reach the layer. Never send to MEXC."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .exit import leftover_remaining_cost
from .settings import LIVE_ORDERS_ALLOWED, MAX_PER_PLAY_USD


def last_reached_layer(last: Any, layer: Any) -> bool:
    try:
        return float(last) <= float(layer) * (1.0 + 1e-9)
    except (TypeError, ValueError):
        return False


def last_reached_sell(last: Any, layer: Any) -> bool:
    try:
        return float(last) >= float(layer) * (1.0 - 1e-9)
    except (TypeError, ValueError):
        return False


def _px(raw: Any) -> Optional[float]:
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def simulate_buy_fills(
    layers: Sequence[Dict[str, Any]],
    last: Any,
    *,
    already: Optional[Sequence[int]] = None,
    scale: float = 1.0,
    bag_usd: float = 0.0,
) -> List[Dict[str, Any]]:
    """Fill buy layers last has actually traded. Simulated book only."""
    if LIVE_ORDERS_ALLOWED:
        raise RuntimeError("live orders are hard-off")
    px = _px(last)
    if px is None:
        return []
    filled_idx = {int(i) for i in (already or [])}
    out: List[Dict[str, Any]] = []
    bag = float(bag_usd)
    for layer in layers or []:
        if not isinstance(layer, dict):
            continue
        try:
            idx = int(layer.get("idx") or 0)
        except (TypeError, ValueError):
            continue
        if idx in filled_idx:
            continue
        line = _px(layer.get("price"))
        if line is None or not last_reached_layer(px, line):
            continue
        try:
            usd = float(layer.get("usd") or 0) * float(scale)
        except (TypeError, ValueError):
            usd = 0.0
        if usd <= 0:
            continue
        if bag + usd > MAX_PER_PLAY_USD + 1e-6:
            continue
        bag += usd
        filled_idx.add(idx)
        out.append(
            {
                "side": "buy",
                "idx": idx,
                "price": line,
                "filled_price": px,
                "usd": round(usd, 4),
                "size_pct": float(layer.get("size_pct") or 0) * float(scale),
                "band": str(layer.get("band") or "ad"),
                "simulated": True,
                "live_sent": False,
            }
        )
    return out


def simulate_sell_fills(
    sell_layers: Sequence[Dict[str, Any]],
    last: Any,
    *,
    already: Optional[Sequence[int]] = None,
    remaining_usd: float = 0.0,
) -> List[Dict[str, Any]]:
    """Fill hung sell layers last has reached. Empty list → nothing. No invent."""
    if LIVE_ORDERS_ALLOWED:
        raise RuntimeError("live orders are hard-off")
    if not sell_layers:
        return []
    px = _px(last)
    if px is None:
        return []
    filled_idx = {int(i) for i in (already or [])}
    out: List[Dict[str, Any]] = []
    left = float(remaining_usd)
    for layer in sell_layers:
        if not isinstance(layer, dict):
            continue
        try:
            idx = int(layer.get("idx") or 0)
        except (TypeError, ValueError):
            continue
        if idx in filled_idx:
            continue
        line = _px(layer.get("price"))
        if line is None or not last_reached_sell(px, line):
            continue
        pct = float(layer.get("pct") or layer.get("size_pct") or 0)
        usd = round(left * (pct / 100.0), 4) if pct > 0 else 0.0
        if usd <= 0 and left > 0:
            usd = round(min(left, left if idx == int(sell_layers[-1].get("idx") or 0) else 0), 4)
        if usd <= 0:
            continue
        left = max(0.0, left - usd)
        filled_idx.add(idx)
        out.append(
            {
                "side": "sell",
                "idx": idx,
                "price": line,
                "filled_price": px,
                "usd": usd,
                "pct": pct,
                "simulated": True,
                "live_sent": False,
            }
        )
    return out


def empty_out_after_buy(buy_fills: Sequence[Dict[str, Any]], sell_layers: Sequence[Any]) -> bool:
    """needs_you when a buy fills and sell layers are empty."""
    return bool(buy_fills) and not bool(sell_layers)


def book_leftover(fills: Sequence[Dict[str, Any]]) -> Optional[float]:
    return leftover_remaining_cost(fills)


def assert_no_live_send() -> None:
    if LIVE_ORDERS_ALLOWED:
        raise RuntimeError("live orders are hard-off")
