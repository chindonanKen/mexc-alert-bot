"""Fills: print at-or-through layer price. Unreached stay empty. One buy set."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .size import BuyLayer, SellLayer, _refresh_next


@dataclass
class FillEvent:
    side: str  # buy | sell
    layer_idx: int
    price: float
    usd: float
    role: str | None = None
    why: str | None = None


@dataclass
class FillState:
    buy_layers: list[BuyLayer]
    sell_layers: list[SellLayer] = field(default_factory=list)
    buy_set_id: str = "1"  # one buy set per hung plan
    fills: list[FillEvent] = field(default_factory=list)
    # Written hung sell USD weights (idx -> usd); captured once before pro-rata mutates live usd.
    sell_hung_usd: dict[int, float] = field(default_factory=dict)

    def remaining_buys(self) -> list[BuyLayer]:
        return [b for b in self.buy_layers if b.status in ("empty", "next")]

    def remaining_sells(self) -> list[SellLayer]:
        return [s for s in self.sell_layers if s.status == "remaining"]


def try_fill_buys(
    state: FillState,
    print_price: float,
    layer_idxs: set[int] | None = None,
    ad_usd_scale: float = 1.0,
) -> list[FillEvent]:
    """
    Fill when print price is at or through (≤ for buys) layer price.
    filled USD = Size share already on the layer (AD may be scaled).
    Unreached stay empty. Only the one hung buy set.
    layer_idxs None → all reached empty/next (unit-test default).
    """
    events: list[FillEvent] = []
    # Fill all reached empty/next layers this print (cascade down)
    for ly in state.buy_layers:
        if ly.status not in ("empty", "next"):
            continue
        if print_price > ly.price:
            continue
        if layer_idxs is not None and ly.idx not in layer_idxs:
            continue
        fill_usd = ly.usd
        if ly.role == "AD" and ad_usd_scale != 1.0:
            fill_usd = round(ly.usd * ad_usd_scale, 4)
            ly.usd = fill_usd  # persist scaled Size USD on the layer
        ly.status = "filled"
        ev = FillEvent(
            side="buy",
            layer_idx=ly.idx,
            price=ly.price,
            usd=fill_usd,
            role=ly.role,
        )
        state.fills.append(ev)
        events.append(ev)
    _refresh_next(state.buy_layers)
    if events:
        apply_pro_rata_sell_usd(state)
    return events


def _ensure_sell_hung_usd(state: FillState) -> None:
    for s in state.sell_layers:
        if s.idx not in state.sell_hung_usd:
            state.sell_hung_usd[s.idx] = float(s.usd)


def apply_pro_rata_sell_usd(state: FillState) -> None:
    """Divide remaining bag USD across remaining sells by hung sell weights.

    live_i = remaining_usd * hung_i / sum(hung of remaining)

    Example: buy $5; hung sells $20/$35/$45 -> live $1 / $1.75 / $2.25.
    Tagging the first sell fills its live USD only; later sells stay remaining.
    Do not give the whole bag to the first sell or drop later layers.
    """
    _ensure_sell_hung_usd(state)
    rc = remaining_cost_from_state(state)
    if rc.bought_usd <= 0:
        return
    rem = [s for s in state.sell_layers if s.status == "remaining"]
    if not rem:
        return
    weights = [float(state.sell_hung_usd.get(s.idx, s.usd)) for s in rem]
    total_w = sum(weights)
    if total_w <= 1e-12:
        return
    rem_usd = float(rc.remaining_usd)
    for s, w in zip(rem, weights):
        s.usd = round(rem_usd * (w / total_w), 4)


def try_fill_sells(state: FillState, print_price: float) -> list[FillEvent]:
    """Fill sells when print >= layer price.

    Cap each fill to min(layer.usd, remaining_cost USD) from prior buys minus prior sells.
    Persist the capped USD on the layer and mark it filled. When remaining cost is flat,
    cancel further remaining sells (no phantom OUT). Empty OUT when no sells — invent nothing.
    """
    events: list[FillEvent] = []
    for ly in state.sell_layers:
        if ly.status != "remaining":
            continue
        if print_price < ly.price:
            continue
        rc = remaining_cost_from_state(state)
        # Static unit path with no buys yet: fill full layer USD (desk live always buys first).
        if rc.bought_usd <= 0:
            fill_usd = ly.usd
        else:
            rem_usd = rc.remaining_usd
            if rem_usd <= 1e-12:
                ly.status = "cancelled"
                continue
            fill_usd = min(float(ly.usd), rem_usd)
            if fill_usd <= 1e-12:
                ly.status = "cancelled"
                continue
            # Persist capped Size USD on the layer (same pattern as AD buy scale).
            ly.usd = round(fill_usd, 4)
            fill_usd = ly.usd
        ly.status = "filled"
        ev = FillEvent(
            side="sell",
            layer_idx=ly.idx,
            price=ly.price,
            usd=fill_usd,
            why=ly.why,
        )
        state.fills.append(ev)
        events.append(ev)
        # Bag flat after this fill → cancel remaining phantom sells.
        if rc.bought_usd > 0:
            after = remaining_cost_from_state(state)
            if after.remaining_usd <= 1e-12:
                for other in state.sell_layers:
                    if other.status == "remaining":
                        other.status = "cancelled"
                break
    return events


def summary(state: FillState) -> dict[str, Any]:
    return {
        "buy_set_id": state.buy_set_id,
        "layers": [b.to_dict() for b in state.buy_layers],
        "sell_layers": [s.to_dict() for s in state.remaining_sells()],
        "fills": [
            {
                "side": f.side,
                "layer_idx": f.layer_idx,
                "price": f.price,
                "usd": f.usd,
                "role": f.role,
                "why": f.why,
            }
            for f in state.fills
        ],
    }


def remaining_cost_from_state(state: FillState):
    """Remaining-cost leftover from this plan's simulated fills."""
    from .exit import remaining_cost_from_fill_events

    return remaining_cost_from_fill_events(state.fills)


def bag_usd(state: FillState) -> float:
    return float(remaining_cost_from_state(state).remaining_usd)


def allocate_sell_usd(state: FillState) -> None:
    """Split remaining bag across remaining sells. Empty bag cancels phantom OUT."""
    rc = remaining_cost_from_state(state)
    if rc.remaining_usd <= 1e-12:
        for s in state.sell_layers:
            if s.status == "remaining":
                s.status = "cancelled"
                s.usd = 0.0
        return
    apply_pro_rata_sell_usd(state)
