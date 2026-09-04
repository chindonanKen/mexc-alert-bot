"""Size layers. 50% AD-side / 50% panic. Dump-depth high_magnet. Panic is % of B."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .settings import (
    AD_SIDE_HALF_PCTS,
    AD_SIDE_L_FRACS,
    MAX_PER_PLAY_USD,
    PANIC_B_FRACS,
    PANIC_HALF_PCTS,
    PLAY_AD_HALF,
    VOL_SPIKE,
)


def _f(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return x


def equal_spread_prices(ad_top: float, ad_bottom: float, n: int = 5) -> List[float]:
    """Forbidden pack. Tests assert dump-depth is not this."""
    length = float(ad_top) - float(ad_bottom)
    return [float(ad_top) - ((i + 1) / n) * length for i in range(n)]


def dump_depth_layers(
    ad_top: Optional[float],
    ad_bottom: Optional[float],
    *,
    budget_usd: float = MAX_PER_PLAY_USD,
    dump_depth: str = "high_magnet",
) -> List[Dict[str, Any]]:
    """AD-side high_magnet + panic Q_i = B − B×(0.10+0.18×(i−1)/2)."""
    del dump_depth  # only high_magnet is hung
    top, bot = _f(ad_top), _f(ad_bottom)
    if top is None or bot is None:
        return []
    if top <= 0 or bot <= 0 or top <= bot:
        return []
    length = top - bot
    depth = length / top
    budget = max(0.0, min(float(budget_usd), MAX_PER_PLAY_USD))
    if budget <= 0:
        return []
    out: List[Dict[str, Any]] = []
    idx = 1
    ad_budget = budget * PLAY_AD_HALF
    for frac, half_pct in zip(AD_SIDE_L_FRACS, AD_SIDE_HALF_PCTS):
        px = bot + length * frac
        if px <= 0:
            continue
        usd = round(ad_budget * (half_pct / 100.0), 4)
        out.append(
            {
                "idx": idx,
                "price": round(float(px), 8),
                "usd": max(0.0, usd),
                "size_pct": round(PLAY_AD_HALF * half_pct, 4),
                "half_pct": half_pct,
                "band": "ad",
                "d": round(depth, 6),
            }
        )
        idx += 1
    panic_budget = budget * PLAY_AD_HALF
    for i, (b_frac, half_pct) in enumerate(zip(PANIC_B_FRACS, PANIC_HALF_PCTS), start=1):
        # % of B, not L.
        px = bot - bot * b_frac
        if px <= 0:
            continue
        usd = round(panic_budget * (half_pct / 100.0), 4)
        out.append(
            {
                "idx": idx,
                "price": round(float(px), 8),
                "usd": max(0.0, usd),
                "size_pct": round(PLAY_AD_HALF * half_pct, 4),
                "half_pct": half_pct,
                "band": "panic",
                "d": round(depth, 6),
            }
        )
        idx += 1
    if out:
        used = sum(x["usd"] for x in out[:-1])
        out[-1]["usd"] = round(max(0.0, budget - used), 4)
    return out


def size_layers(play: Dict[str, Any], *, budget_usd: float = MAX_PER_PLAY_USD) -> List[Dict[str, Any]]:
    """Live-read Size layers from the hung AD. Never equal-spread from T."""
    return dump_depth_layers(
        play.get("ad_top"),
        play.get("ad_bottom"),
        budget_usd=budget_usd,
        dump_depth=str(play.get("dump_depth") or "high_magnet"),
    )


def pack_notional(layers: Sequence[Dict[str, Any]]) -> float:
    total = 0.0
    for layer in layers or []:
        band = str(layer.get("band") or "ad").lower()
        if band not in ("ad", "panic"):
            continue
        try:
            total += float(layer.get("usd") or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 4)


def at_ad_layer(layers: Sequence[Dict[str, Any]], ad_bottom: Any) -> Optional[Dict[str, Any]]:
    """AD-side layer at/just above B; else last AD row."""
    bot = _f(ad_bottom)
    ad = [L for L in layers if str(L.get("band") or "ad") == "ad"]
    if not ad:
        return None
    if bot is None:
        return dict(ad[-1])
    above = [L for L in ad if float(L.get("price") or 0) >= bot]
    if above:
        return dict(above[-1])
    return dict(ad[-1])


def volume_match(vol_usd: Any, habit_usd: Any, *, spike: float = VOL_SPIKE) -> bool:
    v, h = _f(vol_usd), _f(habit_usd)
    if v is None or h is None or h <= 0:
        return False
    return v >= spike * h


def first_volume_near_b(
    *,
    last: Any,
    ad_top: Any,
    ad_bottom: Any,
    vol_spike: bool,
    prior_spike: bool = False,
) -> bool:
    """True when the first volume print is only near B (last 5% / at B)."""
    if prior_spike or not vol_spike:
        return False
    from .chart import is_met

    return is_met(last=last, ad_top=ad_top, ad_bottom=ad_bottom, ad_known=True)


def size_gate(
    play: Dict[str, Any],
    tape: Dict[str, Any],
) -> Dict[str, Any]:
    """Volume gate + grind wait. No volume = size down, not skip. Grind away = wait."""
    quiet = bool(tape.get("quiet_grind"))
    spike = bool(tape.get("vol_spike") or tape.get("volume_match"))
    at_ad = bool(tape.get("at_ad") or tape.get("met_now"))
    board_panic = bool(tape.get("board_panic"))
    if quiet and not spike and not at_ad and not board_panic:
        return {"ok": False, "scale": 0.0, "reason": "grind_wait"}
    if first_volume_near_b(
        last=tape.get("current_price") if tape.get("current_price") is not None else tape.get("last"),
        ad_top=play.get("ad_top"),
        ad_bottom=play.get("ad_bottom"),
        vol_spike=spike,
        prior_spike=bool(tape.get("prior_vol_spike")),
    ):
        return {"ok": True, "scale": 0.5, "reason": "first_volume_near_b"}
    if at_ad and not spike and not board_panic:
        return {"ok": True, "scale": 0.5, "reason": "no_volume_size_down"}
    return {"ok": True, "scale": 1.0, "reason": "size_ok"}
