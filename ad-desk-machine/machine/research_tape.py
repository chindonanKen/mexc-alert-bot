"""Preferred-three tape layer load (Kenneth 2026-09-10 standing process).

Hang loads play-file met-band buys + percentile sells. Does not call
archived dump-depth, panic-under-B, grind-wait generators, or Exit bounce-floor.

Live orders stay off.
"""

from __future__ import annotations

from typing import Any

from .size import (
    AD_LAYER_COUNT,
    EQUAL_SHARE_PCT,
    BuyLayer,
    SellLayer,
    _refresh_next,
)

RESEARCH_LAYER_METHOD = "research_tape"
RESEARCH_SHARE_PCT = EQUAL_SHARE_PCT
RESEARCH_LAYER_COUNT = AD_LAYER_COUNT


def is_research_tape_play(play: dict[str, Any]) -> bool:
    if bool(play.get("research_tape_layers")):
        return True
    return str(play.get("layer_method") or "") == RESEARCH_LAYER_METHOD


def load_research_tape_layers(
    play: dict[str, Any],
    play_usd: float,
) -> tuple[list[BuyLayer], list[SellLayer]]:
    """Load tape prices from the play file. Refuse dump-depth / panic fallback."""
    if not is_research_tape_play(play):
        raise ValueError("not a research tape play")
    if not play.get("tape_source"):
        raise ValueError("research tape play missing tape_source")
    raw_buys = play.get("layers")
    if not raw_buys:
        raise ValueError(
            "research tape play missing layers — refuse dump-depth / panic generators"
        )

    buys: list[BuyLayer] = []
    for row in raw_buys:
        role = row.get("role") or "AD"
        if role == "panic":
            raise ValueError("research tape layers forbid panic layers")
        if role != "AD":
            raise ValueError(f"research tape layers allow AD role only, got {role!r}")
        share = float(row.get("share_pct") or RESEARCH_SHARE_PCT)
        if abs(share - RESEARCH_SHARE_PCT) > 1e-9:
            raise ValueError(
                f"research tape layers require equal {RESEARCH_SHARE_PCT:g}% shares"
            )
        usd = float(row["usd"]) if row.get("usd") is not None else round(
            play_usd * RESEARCH_SHARE_PCT / 100.0, 4
        )
        buys.append(
            BuyLayer(
                idx=int(row.get("idx", len(buys) + 1)),
                price=float(row["price"]),
                usd=usd,
                share_pct=share,
                role="AD",
                status=row.get("status") or "empty",
            )
        )
    if len(buys) != RESEARCH_LAYER_COUNT:
        raise ValueError(
            f"research tape layers require {RESEARCH_LAYER_COUNT} equal AD buys, got {len(buys)}"
        )
    _refresh_next(buys)

    raw_sells = play.get("sell_layers")
    if not raw_sells:
        raise ValueError(
            "research tape play missing sell_layers — refuse Exit bounce-floor invent"
        )
    sells: list[SellLayer] = []
    for i, row in enumerate(raw_sells, start=1):
        status = row.get("status") or "remaining"
        if status in ("filled", "cancelled"):
            continue
        share = float(row.get("share_pct") or RESEARCH_SHARE_PCT)
        if abs(share - RESEARCH_SHARE_PCT) > 1e-9:
            raise ValueError(
                f"research tape sells require equal {RESEARCH_SHARE_PCT:g}% shares"
            )
        usd = float(row["usd"]) if row.get("usd") is not None else round(
            play_usd * RESEARCH_SHARE_PCT / 100.0, 4
        )
        sells.append(
            SellLayer(
                idx=int(row.get("idx", i)),
                price=float(row["price"]),
                usd=usd,
                why=str(row.get("why") or "research_tape"),
                status="remaining",
            )
        )
    if len(sells) != RESEARCH_LAYER_COUNT:
        raise ValueError(
            f"research tape layers require {RESEARCH_LAYER_COUNT} equal sells, got {len(sells)}"
        )
    return buys, sells
