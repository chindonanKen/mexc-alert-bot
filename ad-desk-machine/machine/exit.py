"""Exit live-read: adapt remaining sell layers from play-file facts + live tape.

Own concern — not Chart / Path / Size / Fail. Does not invent sell prices.
live_orders_allowed stays false; this only moves hung sell layers and fill checks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .size import SellLayer


ROOT = Path(__file__).resolve().parent.parent

# Panic-like volume on the way up: bounce-high bar ≈ 3× low-bar class (exit facts).
DEFAULT_PANIC_VOL_RATIO = 3.0

# Bounce kind fractions of this TF's usual_bounce_abs (map only; do not invent usual).
TOO_EARLY_FRAC = 0.25  # has not travelled yet vs usual run
WEAK_FRAC = 0.50  # started but stalled below usual floor band
STRONG_FRAC = 0.85  # strong first bounce vs usual → sell matching amount

BounceKind = Literal["GOOD", "WEAK", "FAIL", "TOO_EARLY"]


def remaining_cost_average(
    bought_usd: float,
    sold_usd: float,
    remaining_qty: float,
) -> float | None:
    """Leftover average = (bought USD − sold USD) / remaining qty.

    Sell above remaining cost → leftover average goes down.
    Sell below remaining cost → leftover average goes up.
    None when remaining qty is not positive (no divide by zero).
    """
    rem = float(remaining_qty)
    if rem <= 1e-12:
        return None
    return (float(bought_usd) - float(sold_usd)) / rem


@dataclass
class RemainingCost:
    """Open leftover from simulated fills. Not a hung sell invent."""

    bought_usd: float = 0.0
    sold_usd: float = 0.0
    remaining_qty: float = 0.0

    @property
    def leftover_avg(self) -> float | None:
        return remaining_cost_average(self.bought_usd, self.sold_usd, self.remaining_qty)

    @property
    def has_leftover(self) -> bool:
        """True leftover: some sells already filled and qty still open."""
        return self.sold_usd > 0.0 and self.remaining_qty > 1e-12


def remaining_cost_from_fill_events(fills: list[Any]) -> RemainingCost:
    """Build remaining-cost leftover from simulated fill events (buy/sell)."""
    bought_usd = 0.0
    sold_usd = 0.0
    bought_qty = 0.0
    sold_qty = 0.0
    for f in fills or []:
        side = getattr(f, "side", None)
        if side is None and isinstance(f, dict):
            side = f.get("side")
            price = float(f.get("price") or 0)
            usd = float(f.get("usd") or 0)
        else:
            price = float(getattr(f, "price", 0) or 0)
            usd = float(getattr(f, "usd", 0) or 0)
        if price <= 0 or usd <= 0 or side not in ("buy", "sell"):
            continue
        qty = usd / price
        if side == "buy":
            bought_usd += usd
            bought_qty += qty
        else:
            sold_usd += usd
            sold_qty += qty
    rem_qty = bought_qty - sold_qty
    if abs(rem_qty) <= 1e-12:
        rem_qty = 0.0
    return RemainingCost(
        bought_usd=bought_usd,
        sold_usd=sold_usd,
        remaining_qty=rem_qty,
    )


@dataclass
class ExitFacts:
    """Reed play-file exit facts. Missing fields stay None — do not invent."""

    path: str | None = None
    usual_bounce_abs: float | None = None
    usual_bounce_frac_of_L: float | None = None
    usual_n: int = 0
    bases: list[tuple[float, float]] = field(default_factory=list)  # (lo, hi)
    vol_ratio_panic_like: float | None = None
    vol_at_low_usd: float | None = None
    candles_to_bounce: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_repeat(self) -> bool:
        """Repeating bounce book exists (n>=2). No repeat → do not invent targets."""
        return self.usual_n >= 2 and self.usual_bounce_abs is not None


@dataclass
class ExitLiveState:
    """Per-plan tracking while state is live — re-read as price moves."""

    original_sells: list[dict[str, Any]] = field(default_factory=list)
    session_low: float | None = None
    session_high: float | None = None
    bounce_low: float | None = None
    bounce_high: float | None = None
    candles_since_ad_tag: int | None = None
    defensive_applied: bool = False
    last_reasons: list[str] = field(default_factory=list)
    last_bounce_kind: str | None = None


@dataclass
class BounceScore:
    """Scored bounce kind from live tape + Reed facts. kind None = do not invent."""

    kind: BounceKind | None = None
    progress: float | None = None  # bounce height / usual_bounce_abs
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExitAdaptation:
    reasons: list[str] = field(default_factory=list)
    pulled: bool = False
    force_fill: bool = False  # invested bag should fill at current price
    bounce_kind: str | None = None


def _resolve_facts_path(raw: str | Path, play_path: Path | None = None) -> Path:
    p = Path(raw)
    if p.is_file():
        return p
    cand = ROOT / p
    if cand.is_file():
        return cand
    if play_path is not None:
        cand2 = play_path.parent / p
        if cand2.is_file():
            return cand2
    # Common desk mirrors
    for base in (ROOT / "data" / ".grokbot", Path("/workspace/data/.grokbot")):
        name = p.name
        cand3 = base / name
        if cand3.is_file():
            return cand3
    return p


def parse_base_zone(zone: str) -> tuple[float, float] | None:
    """Parse Reed zone strings like '0.081–0.086', '~0.100–0.101', '~0.041'."""
    if not zone or not isinstance(zone, str):
        return None
    z = zone.strip().replace("~", "").replace("–", "-").replace("—", "-")
    z = re.sub(r"\s+", "", z)
    if not z:
        return None
    if "-" in z:
        left, right = z.split("-", 1)
        try:
            lo, hi = float(left), float(right)
        except ValueError:
            return None
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi)
    try:
        mid = float(z)
    except ValueError:
        return None
    # Single shelf: thin band around the printed level (±1%, min tick-ish).
    pad = max(mid * 0.01, 1e-8)
    return (mid - pad, mid + pad)


def load_exit_facts(
    source: str | Path | dict[str, Any] | None,
    *,
    play_path: Path | None = None,
) -> ExitFacts:
    """Load Reed exit facts. Empty / missing → blank facts (no invent)."""
    if source is None:
        return ExitFacts()
    data: dict[str, Any]
    path_str: str | None = None
    if isinstance(source, dict):
        data = source
    else:
        path = _resolve_facts_path(source, play_path=play_path)
        path_str = str(path)
        if not path.is_file():
            return ExitFacts(path=path_str)
        data = json.loads(path.read_text())

    facts = ExitFacts(path=path_str, raw=data)

    usual = data.get("usual_bounce") or {}
    if isinstance(usual, dict):
        facts.usual_n = int(usual.get("n") or 0)
        abs_block = usual.get("abs_price") or {}
        if "usual_bounce_height_abs_mid" in usual:
            facts.usual_bounce_abs = float(usual["usual_bounce_height_abs_mid"])
        elif isinstance(abs_block, dict) and abs_block.get("mid") is not None:
            facts.usual_bounce_abs = float(abs_block["mid"])
        frac = usual.get("frac_of_L") or {}
        if "usual_bounce_frac_of_L_mid" in usual:
            facts.usual_bounce_frac_of_L = float(usual["usual_bounce_frac_of_L_mid"])
        elif isinstance(frac, dict) and frac.get("mid") is not None:
            facts.usual_bounce_frac_of_L = float(frac["mid"])

    # Flat aliases some plays may use
    if facts.usual_bounce_abs is None and data.get("usual_bounce_abs") is not None:
        facts.usual_bounce_abs = float(data["usual_bounce_abs"])

    bases_raw = (
        data.get("big_bases_4h")
        or data.get("big_bases")
        or data.get("bases")
        or []
    )
    for row in bases_raw:
        if isinstance(row, dict):
            zone = row.get("zone") or row.get("price") or ""
            parsed = parse_base_zone(str(zone))
            if parsed:
                facts.bases.append(parsed)
            elif row.get("lo") is not None and row.get("hi") is not None:
                facts.bases.append((float(row["lo"]), float(row["hi"])))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            facts.bases.append((float(row[0]), float(row[1])))

    vol = data.get("volume") or {}
    if isinstance(vol, dict):
        ratios: list[float] = []
        for key in ("source", "copy"):
            block = vol.get(key) or {}
            if isinstance(block, dict) and block.get("ratio") is not None:
                ratios.append(float(block["ratio"]))
        if ratios:
            facts.vol_ratio_panic_like = sum(ratios) / len(ratios)
        elif data.get("vol_ratio_panic_like") is not None:
            facts.vol_ratio_panic_like = float(data["vol_ratio_panic_like"])
        low_block = vol.get("at_low_bar_4h_usd") or vol.get("at_low_bar_usd") or {}
        if isinstance(low_block, dict) and low_block.get("mid") is not None:
            facts.vol_at_low_usd = float(low_block["mid"])
        elif isinstance(vol.get("source"), dict) and vol["source"].get("low_bar_usd") is not None:
            facts.vol_at_low_usd = float(vol["source"]["low_bar_usd"])

    if facts.vol_ratio_panic_like is None and data.get("vol_ratio_panic_like") is not None:
        facts.vol_ratio_panic_like = float(data["vol_ratio_panic_like"])

    candles = data.get("candles_to_bounce_after_ad_tag") or data.get("candles_to_bounce")
    if isinstance(candles, dict):
        rs = candles.get("repeating_start")
        if isinstance(rs, str) and rs.strip().lower() in {"no line", "none", "n/a", "no"}:
            pass  # no repeating start → do not use the line
        else:
            nums: list[int] = []
            for key, block in candles.items():
                if key == "repeating_start":
                    continue
                if isinstance(block, dict) and block.get("candles_4h") is not None:
                    nums.append(int(block["candles_4h"]))
                elif isinstance(block, dict) and block.get("candles") is not None:
                    nums.append(int(block["candles"]))
            # Repeating start needs ≥2 finished mets. One print is not a law.
            if len(nums) >= 2:
                facts.candles_to_bounce = int(round(sum(nums) / len(nums)))
    elif isinstance(candles, (int, float)):
        # Explicit recut number in play facts.
        facts.candles_to_bounce = int(candles)

    return facts


def price_in_bases(price: float, bases: list[tuple[float, float]]) -> bool:
    for lo, hi in bases:
        if lo <= price <= hi:
            return True
    return False


def snapshot_sells(sell_layers: list[SellLayer]) -> list[dict[str, Any]]:
    return [
        {"idx": s.idx, "price": s.price, "usd": s.usd, "why": s.why, "status": s.status}
        for s in sell_layers
    ]


def _pull_remaining_toward(
    sells: list[SellLayer],
    *,
    ceiling: float,
    floor: float,
) -> bool:
    """Pull remaining sell prices down into [floor, ceiling], keep order. Not at floor park."""
    rem = [s for s in sells if s.status == "remaining"]
    if not rem:
        return False
    # Keep a small lift off the floor so we do not park sells at the bottom.
    lift = max((ceiling - floor) * 0.02, abs(floor) * 0.001, 1e-12)
    usable_lo = floor + lift
    usable_hi = max(usable_lo, ceiling)
    n = len(rem)
    changed = False
    # Preserve relative order: lowest idx → nearer floor, highest → toward ceiling.
    rem_sorted = sorted(rem, key=lambda s: (s.price, s.idx))
    for i, ly in enumerate(rem_sorted):
        if n == 1:
            target = usable_hi
        else:
            t = i / (n - 1)
            target = usable_lo + t * (usable_hi - usable_lo)
        # Only pull down, never raise a sell above its hung price.
        new_px = min(ly.price, target)
        if new_px < ly.price - 1e-15:
            ly.price = round(new_px, 10)
            changed = True
    return changed


def _force_fill_at(sells: list[SellLayer], current_price: float) -> bool:
    """Lower remaining sell prices to current so at-or-through fill fires (invested bag)."""
    changed = False
    for ly in sells:
        if ly.status != "remaining":
            continue
        if ly.price > current_price:
            ly.price = float(current_price)
            changed = True
    return changed


def _defensive_rescale(
    sells: list[SellLayer],
    original: list[dict[str, Any]],
    *,
    ad_bottom: float,
    new_bottom: float,
) -> bool:
    """
    Drop past AD, not board-wide panic: lower exit layers vs original,
    scaled to the new bottom. Do not park sells at the bottom.
    """
    if new_bottom >= ad_bottom:
        return False
    by_idx = {int(r["idx"]): r for r in original}
    shift = new_bottom - ad_bottom  # negative
    changed = False
    lift = max(abs(new_bottom) * 0.01, abs(ad_bottom - new_bottom) * 0.05, 1e-12)
    for ly in sells:
        if ly.status != "remaining":
            continue
        orig = by_idx.get(ly.idx)
        if not orig:
            continue
        new_px = float(orig["price"]) + shift
        # Do not park at the new low.
        new_px = max(new_px, new_bottom + lift)
        # Still a lower pack vs original.
        new_px = min(new_px, float(orig["price"]))
        if abs(new_px - ly.price) > 1e-15:
            ly.price = round(new_px, 10)
            changed = True
    return changed


def _bounce_like_chart_started(
    live: ExitLiveState,
    facts: ExitFacts,
    *,
    ad_band_high: float | None,
) -> bool:
    """
    True if tape already printed a bounce away from the AD.

    Does not invent a bounce height for sells — only judges whether a real
    start already printed (left the AD band, or a meaningful lift vs usual map).
    """
    bh = live.bounce_high
    bl = live.bounce_low
    if bh is None or bl is None:
        return False
    if ad_band_high is not None and bh > ad_band_high:
        return True
    # Usual map only: a small fraction of usual height means the start printed.
    if facts.usual_bounce_abs is not None and facts.usual_bounce_abs > 0:
        if (bh - bl) >= 0.1 * facts.usual_bounce_abs:
            return True
    return False


def _panic_like_volume(
    facts: ExitFacts,
    volume_usd: float,
) -> bool:
    """True when live volume looks panic-like vs Reed low-bar facts."""
    if volume_usd <= 0:
        return False
    ratio = facts.vol_ratio_panic_like or DEFAULT_PANIC_VOL_RATIO
    low_vol = facts.vol_at_low_usd
    if not low_vol or low_vol <= 0:
        return False
    return volume_usd >= ratio * low_vol


def _bounce_progress(
    live: ExitLiveState,
    facts: ExitFacts,
) -> float | None:
    """bounce_high − bounce_low as a fraction of usual_bounce_abs. None if no map."""
    if facts.usual_bounce_abs is None or facts.usual_bounce_abs <= 0:
        return None
    bh = live.bounce_high
    bl = live.bounce_low
    if bh is None or bl is None:
        return 0.0
    height = max(0.0, float(bh) - float(bl))
    return height / float(facts.usual_bounce_abs)


def score_bounce_kind(
    live: ExitLiveState,
    facts: ExitFacts,
    *,
    current_price: float,
    volume_usd: float = 0.0,
    at_ad: bool = False,
    candles_since_ad_tag: int | None = None,
    ad_band_high: float | None = None,
    into_base: bool = False,
    weak_bounce_override: bool = False,
) -> BounceScore:
    """
    Score GOOD / WEAK / FAIL / TOO_EARLY from live tape + Reed exit facts.

    Does not invent a usual bounce. Without a repeating bounce map, only FAIL
    can score (when candles_to_bounce is set). weak_bounce_override is an
    optional Print-feed flag for tests — prefer scored kind when facts exist.
    """
    score = BounceScore()
    since = (
        live.candles_since_ad_tag
        if candles_since_ad_tag is None
        else int(candles_since_ad_tag)
    )
    progress = _bounce_progress(live, facts)
    score.progress = progress
    bounce_started = _bounce_like_chart_started(
        live, facts, ad_band_high=ad_band_high
    )
    panic_vol = _panic_like_volume(facts, volume_usd)

    # 1) FAIL — candles-to-start passed at AD, no bounce like this chart prints.
    if (
        facts.candles_to_bounce is not None
        and at_ad
        and since is not None
        and since >= facts.candles_to_bounce
        and not into_base
        and not bounce_started
    ):
        score.kind = "FAIL"
        score.reasons.append(
            f"FAIL — {since} candles since AD tag "
            f"(≥{facts.candles_to_bounce} usual start); "
            "no bounce like this chart prints"
        )
        return score

    # 2) Optional Print.weak_bounce override (tests / manual feed).
    if weak_bounce_override:
        score.kind = "WEAK"
        score.reasons.append("WEAK — Print.weak_bounce override")
        return score

    # Into base / panic-like volume: not TOO_EARLY (those paths sell).
    if into_base or panic_vol:
        if facts.has_repeat:
            score.kind = "GOOD"
            if into_base:
                score.reasons.append("GOOD — into big base (base path owns the sell)")
            else:
                score.reasons.append(
                    "GOOD — panic-like volume on the way up (accelerate; not too early)"
                )
        return score

    # Without a repeating bounce map, do not invent GOOD/WEAK/TOO_EARLY.
    if not facts.has_repeat or progress is None:
        return score

    # First green alone is not too early: need a real start vs the usual map
    # (or a left-band print) before scoring early/weak/good.
    tiny = progress < 0.05 and not bounce_started
    if tiny:
        return score

    # TOO_EARLY — bounce has not travelled yet vs this TF's usual run.
    if progress < TOO_EARLY_FRAC:
        score.kind = "TOO_EARLY"
        score.reasons.append(
            "TOO_EARLY — bounce has not travelled yet vs usual run; "
            "first weak tick is not the sell"
        )
        return score

    # Off the bounce high = stalling / giving up the run.
    bh = live.bounce_high
    off_high = (
        bh is not None
        and current_price < float(bh) * 0.995
        and current_price < float(bh) - 1e-12
    )

    # WEAK — started but stalled below this TF's usual floor band.
    if progress < WEAK_FRAC and off_high:
        score.kind = "WEAK"
        score.reasons.append(
            "WEAK — bounce stalled below usual run; pull remaining sells, take what it gives"
        )
        return score

    # Still climbing through the lower half of usual → leave room (too early to sell).
    if progress < WEAK_FRAC:
        score.kind = "TOO_EARLY"
        score.reasons.append(
            "TOO_EARLY — still climbing under usual floor; leave room for normal run"
        )
        return score

    # GOOD — tracking this TF's usual met-copy band (extension past typical still GOOD).
    score.kind = "GOOD"
    if progress >= 1.0:
        score.reasons.append(
            "GOOD — through usual bounce height; fat + leftover may hit"
        )
    elif progress >= STRONG_FRAC:
        score.reasons.append(
            "GOOD — strong first bounce vs usual; sell matching amount"
        )
    else:
        score.reasons.append("GOOD — tracking usual bounce; leave room for normal run")
    return score


def live_read_exit(
    sell_layers: list[SellLayer],
    facts: ExitFacts,
    live: ExitLiveState,
    *,
    current_price: float,
    low: float,
    volume_usd: float = 0.0,
    ad_bottom: float,
    board_panic: bool = False,
    weak_bounce: bool = False,
    at_ad: bool = False,
    candles_since_ad_tag: int | None = None,
    ad_band_high: float | None = None,
    remaining_cost: RemainingCost | None = None,
) -> ExitAdaptation:
    """
    Re-read remaining sell layers while live. Mutates sell_layers in place.

    Locked rules (summary):
    - Bounce kind scored from live tape + Reed facts: GOOD / WEAK / FAIL / TOO_EARLY.
      Print.weak_bounce is an optional override for tests; prefer scored kind where facts exist.
    - Usual bounce height is a map, not a freeze.
    - Into a big base → selling invested bag is required.
    - Panic-like volume on the way up (~3× low-bar) → sell matching amount; do not wait.
    - WEAK → pull remaining sell layers down. TOO_EARLY → do not sell first weak tick.
    - FAIL → consider exit (candles-to-start passed, no bounce like this chart prints).
    - GOOD → usual path; leftover may full-exit above remaining cost; strong first bounce
      may sell a matching amount; through usual height → fat + leftover may hit.
    - Under AD without board panic → defensive lower sell layers scaled to new bottom.
    - Board-wide panic → do not sell first weak bounce; into base still sells.
    - Empty sell layers / no invent.
    """
    adapt = ExitAdaptation()
    rem = [s for s in sell_layers if s.status == "remaining"]
    if not rem:
        # Empty OUT — invent nothing.
        return adapt

    # Track tape extremes.
    if live.session_low is None or low < live.session_low:
        live.session_low = low
    if live.session_high is None or current_price > live.session_high:
        live.session_high = current_price
    if live.bounce_low is None:
        live.bounce_low = low
    else:
        live.bounce_low = min(live.bounce_low, low)
    if live.bounce_high is None:
        live.bounce_high = current_price
    else:
        live.bounce_high = max(live.bounce_high, current_price)

    if not live.original_sells:
        live.original_sells = snapshot_sells(sell_layers)

    if candles_since_ad_tag is not None:
        live.candles_since_ad_tag = int(candles_since_ad_tag)

    # 1) Into big base → force sell invested bag (do not wait for bounce-length past base).
    into_base = price_in_bases(current_price, facts.bases)
    if not into_base:
        # Also: hung big_base sell layer at/under current price means price travelled into it.
        for ly in rem:
            if ly.why == "big_base" and current_price >= ly.price:
                into_base = True
                break
    if into_base:
        if _force_fill_at(sell_layers, current_price):
            adapt.pulled = True
        adapt.force_fill = True
        adapt.reasons.append(
            "into big base — sell invested bag (do not wait for bounce-length past base)"
        )

    # Score bounce kind from tape + Reed facts (override flag optional for tests).
    bounce = score_bounce_kind(
        live,
        facts,
        current_price=current_price,
        volume_usd=volume_usd,
        at_ad=at_ad,
        candles_since_ad_tag=candles_since_ad_tag,
        ad_band_high=ad_band_high,
        into_base=into_base,
        weak_bounce_override=bool(weak_bounce),
    )
    kind = bounce.kind
    adapt.bounce_kind = kind
    live.last_bounce_kind = kind
    progress = bounce.progress

    # 2) Panic-like volume on the way up → accelerate; do not wait for usual bounce height.
    ratio = facts.vol_ratio_panic_like or DEFAULT_PANIC_VOL_RATIO
    low_vol = facts.vol_at_low_usd
    if low_vol and low_vol > 0 and volume_usd >= ratio * low_vol and not into_base:
        # Sell a matching amount: pull the nearest (lowest) remaining sell to current.
        nearest = min(rem, key=lambda s: s.price)
        if nearest.price > current_price:
            nearest.price = float(current_price)
            adapt.pulled = True
            adapt.force_fill = True
            adapt.reasons.append(
                f"panic-like volume on the way up (≥~{ratio:g}× low-bar) — "
                "sell matching amount; do not wait for usual bounce height"
            )

    # 3) Defensive: drop past AD, not board-wide panic.
    session_low = live.session_low if live.session_low is not None else low
    if session_low < ad_bottom and not board_panic:
        if _defensive_rescale(
            sell_layers,
            live.original_sells,
            ad_bottom=ad_bottom,
            new_bottom=session_low,
        ):
            adapt.pulled = True
            live.defensive_applied = True
            adapt.reasons.append(
                "defensive — drop past AD without board-wide panic; "
                "lower sell layers vs original, scaled to new bottom"
            )

    # 4) TOO_EARLY — do not sell first weak tick; leave hung sells alone.
    if kind == "TOO_EARLY" and not into_base and not adapt.force_fill:
        adapt.reasons.append(
            "too early — bounce has not travelled yet vs usual; "
            "first weak tick is not the sell"
        )

    # 5) WEAK — pull remaining sell layers down (not under board-wide panic first weak).
    if kind == "WEAK" and not into_base:
        if board_panic:
            adapt.reasons.append(
                "board-wide panic — do not sell first weak bounce; give it time"
            )
        else:
            # Pull toward what the bounce is giving (session high), above bounce low.
            ceiling = live.bounce_high if live.bounce_high is not None else current_price
            floor = live.bounce_low if live.bounce_low is not None else session_low
            if ceiling <= floor:
                ceiling = current_price
                floor = min(session_low, current_price)
            if _pull_remaining_toward(sell_layers, ceiling=ceiling, floor=floor):
                adapt.pulled = True
                adapt.reasons.append("weak bounce — pull remaining sell layers down")
            elif not any("WEAK" in r or "weak bounce" in r for r in adapt.reasons):
                adapt.reasons.append("weak bounce — pull remaining sell layers down")

    # 6) FAIL — consider exit (not a clock). Scored from candles_to_bounce + no bounce.
    if kind == "FAIL" and not into_base:
        if _force_fill_at(sell_layers, current_price):
            adapt.pulled = True
        adapt.force_fill = True
        since = (
            live.candles_since_ad_tag
            if candles_since_ad_tag is None
            else int(candles_since_ad_tag)
        )
        adapt.reasons.append(
            f"sideways too long — {since} candles since AD tag "
            f"(≥{facts.candles_to_bounce} usual start); "
            "no bounce like this chart prints — consider exit"
        )

    # 7) Leftover full-exit above remaining cost on usual GOOD bounce (SYN-class).
    # Do not invent bounce height. Do not sell the open. WEAK / TOO_EARLY are not this line.
    # Prefer leftover full-exit over single strong-matching when both apply.
    if (
        remaining_cost is not None
        and remaining_cost.has_leftover
        and facts.has_repeat
        and kind == "GOOD"
        and not into_base
        and not adapt.force_fill
    ):
        avg = remaining_cost.leftover_avg
        if (
            avg is not None
            and current_price > avg
            and _bounce_like_chart_started(live, facts, ad_band_high=ad_band_high)
        ):
            if _force_fill_at(sell_layers, current_price):
                adapt.pulled = True
            adapt.force_fill = True
            adapt.reasons.append(
                "leftover full-exit above remaining cost "
                f"(leftover avg {avg:.6g}; current {current_price:g}) "
                "on usual good bounce"
            )

    # 8) GOOD — strong first bounce vs usual → sell matching amount (not into base).
    if (
        kind == "GOOD"
        and not into_base
        and not adapt.force_fill
        and progress is not None
        and progress >= STRONG_FRAC
        and facts.has_repeat
    ):
        # Only when panic-like volume did not already accelerate.
        if not any("panic-like volume" in r for r in adapt.reasons):
            nearest = min(rem, key=lambda s: s.price)
            if nearest.price > current_price:
                nearest.price = float(current_price)
                adapt.pulled = True
                adapt.force_fill = True
                adapt.reasons.append(
                    "strong first bounce vs usual — sell matching amount on this move"
                )

    # 9) Usual bounce is a map only — no freeze action here; fat 3rd/4th are hung facts.
    # If no repeat in facts, do not invent new sell prices (we never add layers).
    if not facts.has_repeat and not live.original_sells:
        pass  # hung sells may still exist from Gauge; we just do not invent new ones

    live.last_reasons = list(adapt.reasons)
    return adapt
