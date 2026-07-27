"""Dump velocity / panic score from peak → now timing.

Pure helpers — no I/O. Sharp drops score higher than grinds with the same %.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Bands ordered by severity
BAND_PANIC = "PANIC"
BAND_FAST = "FAST"
BAND_GRIND = "GRIND"
BAND_UNKNOWN = "—"


def score_dump(
    peak_ts: float,
    peak_price: float,
    now_ts: float,
    price_now: float,
    *,
    panic_per_min: float = 2.0,
    fast_per_min: float = 0.8,
) -> Tuple[float, float, str]:
    """
    Returns (velocity_pct_per_min, minutes_since_peak, band).

    velocity uses abs(drawdown %) / minutes so more negative dumps score higher.
    minutes floor is a small epsilon so instantaneous samples do not explode.
    """
    if peak_price <= 0 or price_now <= 0:
        return 0.0, 0.0, BAND_UNKNOWN

    dd_frac = (price_now - peak_price) / peak_price  # negative when dumping
    dd_pct = abs(dd_frac) * 100.0
    minutes = max((now_ts - peak_ts) / 60.0, 1.0 / 60.0)  # floor 1 second
    vel = dd_pct / minutes

    if dd_frac >= 0:
        return 0.0, minutes, BAND_UNKNOWN

    if vel >= panic_per_min:
        band = BAND_PANIC
    elif vel >= fast_per_min:
        band = BAND_FAST
    else:
        band = BAND_GRIND
    return vel, minutes, band


def format_velocity_line(vel: float, minutes: float, band: str) -> str:
    if band == BAND_UNKNOWN:
        return ""
    return f"Velocity: −{vel:.1f}%/min · {band} (peak {minutes:.1f}m ago)"
