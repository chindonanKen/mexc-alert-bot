"""Extreme + isolated dump criteria for the specialist agent.

Hot-path safe: pure functions, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IsolatedDumpCriteria:
    """Thresholds for waking the isolated-dump investigator."""

    # Absolute drop % (negative dumps use abs). Extreme bar.
    min_drop_pct: float = 8.0
    # Or at least this × user mover threshold (e.g. 1.6 × 5% = 8%)
    threshold_multiplier: float = 1.6
    # Max watchlist names dumping to still count as isolated
    max_heat_breadth: int = 2
    # Require sharp velocity when known
    require_fast_or_panic: bool = True
    # Skip grinds entirely when band known
    allow_grind: bool = False


def should_investigate_isolated(
    *,
    drop_pct: float,
    user_threshold_pct: float,
    velocity_band: Optional[str],
    heat_dumping_count: Optional[int],
    watchlist_count: Optional[int] = None,
    criteria: Optional[IsolatedDumpCriteria] = None,
) -> bool:
    """
    True only for *extreme isolated* dumps.

    - Large enough drop (abs)
    - Not market-wide (low heat breadth)
    - Prefer PANIC/FAST velocity; reject GRIND when band known
    """
    c = criteria or IsolatedDumpCriteria()
    mag = abs(float(drop_pct))
    floor = max(float(c.min_drop_pct), float(user_threshold_pct) * float(c.threshold_multiplier))
    if mag + 1e-9 < floor:
        return False

    band = (velocity_band or "").upper().strip()
    if band and band not in ("—", "-", "UNKNOWN", ""):
        if band == "GRIND" and not c.allow_grind:
            return False
        if c.require_fast_or_panic and band not in ("PANIC", "FAST"):
            return False

    if heat_dumping_count is not None:
        if int(heat_dumping_count) > int(c.max_heat_breadth):
            return False
        # If almost entire book is dumping, never isolated
        if watchlist_count is not None and watchlist_count > 0:
            if heat_dumping_count >= max(3, int(0.5 * watchlist_count)):
                return False

    return True
