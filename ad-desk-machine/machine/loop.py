"""Poll hung plays against live MEXC klines. Simulated fills only."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from .engine import evaluate, hung_board
from .feeds import MexcPublicFeed
from .plays import load_hung_plays
from .settings import faster_tf_for, live_orders_allowed

logger = logging.getLogger(__name__)

POLL_SECONDS = 8.0

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_feed = MexcPublicFeed()
_last_snaps: Dict[str, Dict[str, Any]] = {}


def loop_wanted() -> bool:
    raw = os.getenv("MACHINE_LOOP")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def last_snaps() -> Dict[str, Dict[str, Any]]:
    return dict(_last_snaps)


def tick(feed: Optional[MexcPublicFeed] = None) -> Dict[str, Any]:
    """One pass. Never places a live order."""
    if live_orders_allowed():
        raise RuntimeError("live orders are hard-off")
    client = feed or _feed
    results = []
    for play in load_hung_plays():
        tf = str(play.get("tf") or "4h")
        snap = client.snapshot(str(play.get("symbol") or ""), tf, faster_tf_for(tf))
        _last_snaps[str(play.get("id"))] = snap
        results.append(evaluate(play, snap))
    return {
        "ok": True,
        "plays": hung_board(_last_snaps),
        "results": results,
        "live_orders_allowed": False,
        "live_orders_sent": False,
    }


def _run() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception:
            logger.exception("machine loop")
        _stop.wait(POLL_SECONDS)


def ensure_loop() -> None:
    if not loop_wanted():
        return
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, name="ad-desk-machine", daemon=True)
        _thread.start()


def stop_loop() -> None:
    _stop.set()
