"""Desk fill lifecycle. A BUY fill is not an open.

Fact: ``POSITION OPENED`` lives on the **live desk bind**
(``mexc-desk-s1`` ``fill_lifecycle.py``), not the git Telegram/alert-bot
image. Code bind and data bind are separate — this module is code only.

Git trunk: that Telegram ping is **removed**. ``NOTIFY_POSITION_OPENED``
stays false. Open = exchange ``open_positions`` / spot balances only.
Do not enable the ping. Do not import this from the alert-bot fill sync.
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
