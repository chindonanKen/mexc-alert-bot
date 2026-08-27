"""Fill lifecycle. A BUY fill is not an open.

Live bind historically sent a Telegram ``POSITION OPENED`` ping from this
path when a futures BUY fill arrived. That is wrong: open = exchange
``open_positions`` / spot balances only.

This git trunk keeps the module so the path is findable, and **disables**
the ping. Do not enable ``NOTIFY_POSITION_OPENED``. A fill is never an open.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Locked off. A fill is not a position start.
NOTIFY_POSITION_OPENED = False


def fill_starts_position(fill: Optional[Dict[str, Any]]) -> bool:
    """False for every fill. Futures BUY is not an open."""
    return False


def position_opened_message(fill: Optional[Dict[str, Any]]) -> Optional[str]:
    """Never build a POSITION OPENED Telegram body."""
    return None


def maybe_notify_position_opened(
    fill: Optional[Dict[str, Any]],
    notifier=None,
    *,
    user_id: Optional[int] = None,
) -> bool:
    """No-op. Returns False (nothing sent)."""
    return False
