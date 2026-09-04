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
    return events


def try_fill_sells(state: FillState, print_price: float) -> list[FillEvent]:
    """Fill sells when print >= layer price. Empty OUT when no sells — invent nothing."""
    events: list[FillEvent] = []
    for ly in state.sell_layers:
        if ly.status != "remaining":
            continue
        if print_price >= ly.price:
            ly.status = "filled"
            ev = FillEvent(
                side="sell",
                layer_idx=ly.idx,
                price=ly.price,
                usd=ly.usd,
                why=ly.why,
            )
            state.fills.append(ev)
            events.append(ev)
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
