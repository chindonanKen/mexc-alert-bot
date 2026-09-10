"""Size layers — Kenneth 2026-09-10 clean preferred-three standing process.

Standing Size:
  five equal-spaced buys across the AD met-band (last 5% of L above B);
  equal 20% of play each; no dump-depth spacing; no panic under B.

Archived dump-depth / panic helpers remain only so tests can prove hang
does not call them. Live orders stay off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


Role = Literal["AD", "panic"]
LayerStatus = Literal["empty", "next", "filled", "cancelled"]

EQUAL_SHARE_PCT = 20.0
AD_LAYER_COUNT = 5
BOUNCE_FRAC_PERCENTILES = (0.20, 0.35, 0.50, 0.65, 0.80)


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
    why: str  # research_tape | usual_bounce | big_base | panic_like_volume
    status: str = "remaining"
    plan_usd: float | None = None

    def __post_init__(self) -> None:
        if self.plan_usd is None:
            self.plan_usd = float(self.usd)

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "price": self.price,
            "usd": self.usd,
            "why": self.why,
            "status": self.status,
        }


def met_band_buy_prices(top: float, bottom: float) -> list[float]:
    """Five equal steps from met-band high (B + 0.05×L) down to B."""
    L = top - bottom
    if L <= 0:
        return [bottom] * AD_LAYER_COUNT
    high = bottom + 0.05 * L
    step = (high - bottom) / 4.0
    return [high - i * step for i in range(AD_LAYER_COUNT)]


def bounce_frac_sell_prices(bottom: float, length: float, fracs: list[float]) -> list[float]:
    """Sells at B + q×L. q comes from tape percentiles — do not invent q."""
    return [bottom + float(q) * length for q in fracs]


def build_buy_layers(
    top: float,
    bottom: float,
    play_usd: float,
    *,
    high_magnet: bool = False,
    copy_count: int = 0,
) -> list[BuyLayer]:
    """Standing buy set: 5 met-band AD layers, equal 20%. No panic. No dump-depth."""
    _ = high_magnet
    _ = copy_count
    prices = met_band_buy_prices(top, bottom)
    layers: list[BuyLayer] = []
    for i, px in enumerate(prices, start=1):
        layers.append(
            BuyLayer(
                idx=i,
                price=round(px, 10),
                usd=round(play_usd * EQUAL_SHARE_PCT / 100.0, 4),
                share_pct=EQUAL_SHARE_PCT,
                role="AD",
                status="empty",
            )
        )
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
    """Kept for sheet / tests. Not a standing entry gate (2026-09-10)."""
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
    """Fill reached AD layers. Grind-wait / late 0.5× are not standing entry gates."""
    _ = volume_usd
    _ = vol_at_bottom_usd
    _ = at_ad
    _ = path_take_at_ad
    _ = band_high
    _ = board_grind
    reached = [
        ly for ly in layers
        if ly.status in ("empty", "next") and print_price <= ly.price and ly.role == "AD"
    ]
    if not reached:
        return SizeGateResult(action="wait", why="no buy layer reached", layer_idxs=set())
    return SizeGateResult(
        action="buy",
        why="Size — fill reached AD layers (equal 20%)",
        layer_idxs={ly.idx for ly in reached},
        ad_usd_scale=1.0,
    )


def load_sell_layers(raw: list[dict[str, Any]] | None) -> list[SellLayer]:
    """Empty allowed. Do not invent sells or bounce-floor prices."""
    if not raw:
        return []
    out: list[SellLayer] = []
    for i, row in enumerate(raw, start=1):
        why = row.get("why") or "research_tape"
        if why not in ("usual_bounce", "big_base", "panic_like_volume", "research_tape"):
            why = "research_tape"
        status = row.get("status") or "remaining"
        if status in ("filled", "cancelled"):
            continue
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


# --- archived 2026-09-10: not standing Size ---------------------------------

def ad_side_prices(top: float, bottom: float, high_magnet: bool = False, copy_count: int = 0) -> list[float]:
    """Archived dump-depth spacing (Kenneth 2026-09-01). Not standing after 2026-09-10."""
    L = top - bottom
    if L <= 0:
        return [bottom] * 5
    D = L / top if top else 0.0

    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

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
    """Archived panic-under-B (Kenneth 2026-09-04). Not standing after 2026-09-10."""
    B = bottom
    return [B - B * (0.10 + 0.18 * (i / 2.0)) for i in range(3)]
