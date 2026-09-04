"""Size layers: AD-side + panic shares. Does not invent sell layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["AD", "panic"]
LayerStatus = Literal["empty", "next", "filled", "cancelled"]


@dataclass
class BuyLayer:
    idx: int
    price: float
    usd: float
    share_pct: float  # percent of play
    role: Role
    status: LayerStatus = "empty"

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "price": self.price,
            "usd": self.usd,
            "share_pct": self.share_pct,
            "role": self.role,
            "status": self.status,
        }


@dataclass
class SellLayer:
    idx: int
    price: float
    usd: float
    why: str  # usual_bounce | big_base | panic_like_volume
    status: str = "remaining"

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "price": self.price,
            "usd": self.usd,
            "why": self.why,
            "status": self.status,
        }


# AD-side half shares of whole play: 5 / 7.5 / 10 / 12.5 / 15
AD_SIDE_SHARES = (5.0, 7.5, 10.0, 12.5, 15.0)
# Panic half of whole play: 10 / 15 / 25  (20/30/50 of the half)
PANIC_SHARES = (10.0, 15.0, 25.0)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def ad_side_prices(top: float, bottom: float, high_magnet: bool = False, copy_count: int = 0) -> list[float]:
    """Standing dump-depth spacing (Kenneth 2026-09-01)."""
    L = top - bottom
    if L <= 0:
        return [bottom] * 5
    D = L / top if top else 0.0
    start_pct = _clip(70 + 30 * D, 78, 95)
    end_pct = _clip(98 + 5 * D, 97.5, 100.8)
    if D >= 0.50:
        end_pct = max(end_pct, 100.3)
    magnet = high_magnet or copy_count >= 2
    if magnet:
        start_pct = max(start_pct, 93)
        end_pct = max(end_pct, 99.6)
    end_pct = max(end_pct, 100.4)
    end_pct = min(end_pct, 101.0)
    p1 = top - (start_pct / 100.0) * L
    p5 = top - (end_pct / 100.0) * L
    step = (p5 - p1) / 4.0
    return [p1 + i * step for i in range(5)]


def panic_prices(top: float, bottom: float) -> list[float]:
    """Kenneth 2026-09-04: depth is % of bottom price B, not of AD length L."""
    B = bottom
    # Q_i = B - B * (0.10 + 0.18 * (i-1)/2) for i=1,2,3 → 10/19/28% under B
    return [B - B * (0.10 + 0.18 * (i / 2.0)) for i in range(3)]


def build_buy_layers(
    top: float,
    bottom: float,
    play_usd: float,
    *,
    high_magnet: bool = False,
    copy_count: int = 0,
) -> list[BuyLayer]:
    """One buy set: 5 AD-side + 3 panic. USD = Size share of play."""
    prices = ad_side_prices(top, bottom, high_magnet=high_magnet, copy_count=copy_count)
    layers: list[BuyLayer] = []
    for i, (px, share) in enumerate(zip(prices, AD_SIDE_SHARES), start=1):
        layers.append(
            BuyLayer(
                idx=i,
                price=round(px, 10),
                usd=round(play_usd * share / 100.0, 4),
                share_pct=share,
                role="AD",
                status="empty",
            )
        )
    for j, (px, share) in enumerate(zip(panic_prices(top, bottom), PANIC_SHARES), start=1):
        layers.append(
            BuyLayer(
                idx=5 + j,
                price=round(px, 10),
                usd=round(play_usd * share / 100.0, 4),
                share_pct=share,
                role="panic",
                status="empty",
            )
        )
    # Mark first remaining AD as next if any empty
    _refresh_next(layers)
    return layers


def _refresh_next(layers: list[BuyLayer]) -> None:
    """Mark the first empty layer as next. Skip filled and cancelled."""
    for ly in layers:
        if ly.status == "next":
            ly.status = "empty"
    for ly in layers:
        if ly.status == "cancelled":
            continue
        if ly.status == "empty":
            ly.status = "next"
            break


def is_real_volume(volume_usd: float | None, vol_at_bottom_usd: float | None) -> bool:
    """Size real volume at the layer vs this chart's usual AD-tag volume."""
    vol = float(volume_usd or 0.0)
    if vol_at_bottom_usd is None:
        return vol > 0.0
    return vol >= float(vol_at_bottom_usd)


@dataclass
class SizeGateResult:
    action: str  # buy | wait
    why: str
    layer_idxs: set[int]
    ad_usd_scale: float = 1.0


def gate_buy_layers(
    layers: list[BuyLayer],
    *,
    print_price: float,
    volume_usd: float,
    vol_at_bottom_usd: float | None,
    at_ad: bool,
    path_take_at_ad: bool,
    band_high: float,
    board_grind: bool = False,
) -> SizeGateResult:
    """
    Size owns volume at fill. Skip no-volume AD layers; grind wait without spike;
    Path-allowed take at AD still fills at-AD layers; late first volume on 4/5 → 0.5× AD.
    """
    reached = [
        ly for ly in layers
        if ly.status in ("empty", "next") and print_price <= ly.price
    ]
    if not reached:
        return SizeGateResult(action="wait", why="no buy layer reached", layer_idxs=set())

    real = is_real_volume(volume_usd, vol_at_bottom_usd)
    # board_grind does not change fill math; engine logs the grind wait note.
    _ = board_grind

    if not real:
        if path_take_at_ad and at_ad:
            to_fill = {
                ly.idx for ly in reached
                if ly.role == "AD" and ly.price <= band_high
            }
            for ly in reached:
                if ly.role == "AD" and ly.idx not in to_fill:
                    ly.status = "cancelled"
            _refresh_next(layers)
            if not to_fill:
                return SizeGateResult(
                    action="wait",
                    why="Size grind wait — no real volume at layer",
                    layer_idxs=set(),
                )
            return SizeGateResult(
                action="buy",
                why="Path-allowed take at AD — Size fills at-AD layer despite quiet volume",
                layer_idxs=to_fill,
                ad_usd_scale=1.0,
            )
        for ly in reached:
            if ly.role == "AD":
                ly.status = "cancelled"
        _refresh_next(layers)
        return SizeGateResult(
            action="wait",
            why="Size grind wait — no real volume at layer",
            layer_idxs=set(),
        )

    # Real volume: fill all reached; 0.5× AD if first AD fill is layer 4 or 5
    idxs = {ly.idx for ly in reached}
    ad_reaching = [ly for ly in reached if ly.role == "AD"]
    scale = 1.0
    if ad_reaching:
        first_ad = min(ly.idx for ly in ad_reaching)
        earlier_filled = any(
            ly.role == "AD" and ly.idx < first_ad and ly.status == "filled"
            for ly in layers
        )
        if first_ad >= 4 and not earlier_filled:
            scale = 0.5
    return SizeGateResult(
        action="buy",
        why="Size real volume — fill reached layers",
        layer_idxs=idxs,
        ad_usd_scale=scale,
    )


def load_sell_layers(raw: list[dict[str, Any]] | None) -> list[SellLayer]:
    """Empty allowed. Do not invent sells."""
    if not raw:
        return []
    out: list[SellLayer] = []
    for i, row in enumerate(raw, start=1):
        why = row.get("why") or "usual_bounce"
        if why not in ("usual_bounce", "big_base", "panic_like_volume"):
            why = "usual_bounce"
        status = row.get("status") or "remaining"
        if status in ("filled", "cancelled"):
            continue  # remaining only on OUT
        out.append(
            SellLayer(
                idx=int(row.get("idx", i)),
                price=float(row["price"]),
                usd=float(row.get("usd", 0)),
                why=why,
                status="remaining",
            )
        )
    return out
