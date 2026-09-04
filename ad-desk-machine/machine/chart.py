"""Chart helpers: AD met band. Met stays met once entered."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AD:
    top: float
    bottom: float

    @property
    def length(self) -> float:
        return self.top - self.bottom

    @property
    def band_high(self) -> float:
        """Top of met band: 5% of L above B."""
        return self.bottom + 0.05 * self.length

    @property
    def band_low(self) -> float:
        """Through B (include prints at/under B)."""
        return self.bottom


def is_in_met_band(low: float, ad: AD) -> bool:
    """Low entered last 5% of AD length above B through B."""
    if ad.length <= 0:
        return False
    # Met when low has reached into [band_low effectively through B, band_high]
    # "through the bottom" — low at or below band_high and has touched the band
    # (a 90% drop is met from about 85% down → low <= T - 0.85*L = B + 0.15*L... wait)
    # SPEC: "band is 5% of that length above the bottom, through the bottom"
    # so low <= band_high (B + 0.05*L) means entered the last 5% above B.
    # Through B means lows at or below B also count as in band.
    return low <= ad.band_high


def update_met(prev_met: bool, low: float, ad: AD) -> bool:
    """Once met, stays met."""
    if prev_met:
        return True
    return is_in_met_band(low, ad)


def at_ad(price: float, ad: AD, slack: float | None = None) -> bool:
    """Current price is at the AD (in or through the met band)."""
    if slack is None:
        slack = 0.0
    return price <= ad.band_high + slack
