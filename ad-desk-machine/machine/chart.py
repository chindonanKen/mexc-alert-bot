"""Chart live-read. Met when low enters the last 5% of L above B through B."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .settings import MET_FRAC, THROUGH_FRAC


def _f(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return x


def ad_length(ad_top: Any, ad_bottom: Any) -> Optional[float]:
    t, b = _f(ad_top), _f(ad_bottom)
    if t is None or b is None or t <= b or b <= 0:
        return None
    return t - b


def met_ceiling(ad_top: Any, ad_bottom: Any) -> Optional[float]:
    """Top of the met band: B + 5% of L."""
    length = ad_length(ad_top, ad_bottom)
    b = _f(ad_bottom)
    if length is None or b is None:
        return None
    return b + MET_FRAC * length


def met_floor(ad_top: Any, ad_bottom: Any) -> Optional[float]:
    """Through B: slightly under B is still the AD area, not panic Q."""
    length = ad_length(ad_top, ad_bottom)
    b = _f(ad_bottom)
    if length is None or b is None:
        return None
    return b - THROUGH_FRAC * length


def is_met(
    *,
    last: Any,
    ad_top: Any,
    ad_bottom: Any,
    ad_known: bool = True,
) -> bool:
    """Low / last in the last 5% of L above B, through B. Not a buy by itself."""
    if not ad_known:
        return False
    px = _f(last)
    ceil = met_ceiling(ad_top, ad_bottom)
    floor = met_floor(ad_top, ad_bottom)
    if px is None or ceil is None or floor is None:
        return False
    return floor <= px <= ceil


def bar_low(bar: Any) -> Optional[float]:
    if not isinstance(bar, dict):
        return None
    return _f(bar.get("l") if bar.get("l") is not None else bar.get("low"))


def bars_ever_met(
    play: Dict[str, Any],
    bars: Optional[Iterable[Any]] = None,
    *,
    last: Any = None,
) -> bool:
    """Sticky: once last or a bar low printed the met band, met stays met."""
    if play.get("met"):
        return True
    ad_top = play.get("ad_top")
    ad_bottom = play.get("ad_bottom")
    ad_known = ad_top is not None and ad_bottom is not None
    if is_met(last=last, ad_top=ad_top, ad_bottom=ad_bottom, ad_known=ad_known):
        return True
    for bar in bars or []:
        low = bar_low(bar)
        if is_met(last=low, ad_top=ad_top, ad_bottom=ad_bottom, ad_known=ad_known):
            return True
    return False


def at_ad_now(
    *,
    last: Any,
    ad_top: Any,
    ad_bottom: Any,
    ad_known: bool = True,
) -> bool:
    """Currently in the met band. Distinct from sticky met."""
    return is_met(last=last, ad_top=ad_top, ad_bottom=ad_bottom, ad_known=ad_known)
