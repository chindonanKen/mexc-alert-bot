"""Always-on official tape → evaluate. Paper fills only. No MEXC sends."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from .settings import feature_ad_machine, machine_user_id
from .tape import (
    fatal_news_hits,
    fetch_official_klines,
    fetch_official_ticker,
    snapshot_for_plan,
)

logger = logging.getLogger(__name__)

TICKER_SECONDS = 2.0
KLINE_SECONDS = 8.0

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def tape_loop_wanted() -> bool:
    if not feature_ad_machine():
        return False
    raw = os.getenv("MACHINE_TAPE_LOOP")
    if raw is None:
        return True
    return raw.strip().lower() in ("1", "true", "yes", "on")


def ensure_tape_loop() -> None:
    """Start once when the flag is on. Tests set MACHINE_TAPE_LOOP=false."""
    if not tape_loop_wanted():
        return
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(
            target=_run, name="machine-tape", daemon=True
        )
        _thread.start()


def stop_tape_loop() -> None:
    _stop.set()


def poll_once(
    *,
    fetch_klines: bool = True,
    store=None,
    user_id: Optional[int] = None,
    spot=None,
    futures=None,
    kline_client=None,
) -> Dict[str, Any]:
    """One tape pass → evaluate. live_orders_sent stays false."""
    from .engine import evaluate, get_store, seed_plans

    store = store or get_store()
    uid = int(user_id if user_id is not None else machine_user_id())
    seed_plans(store, uid)
    snapshot: Dict[str, Dict[str, Any]] = {}
    for plan in store.list_plans(uid):
        key = f"{plan['symbol']}|{plan['market']}"
        ticker = fetch_official_ticker(
            plan["market"], plan["symbol"], spot=spot, futures=futures
        )
        bars = None
        news = None
        if fetch_klines:
            tf = plan.get("tf") or "15m"
            bars = fetch_official_klines(
                plan["market"], plan["symbol"], str(tf), client=kline_client
            )
            news = fatal_news_hits(store.db_path, plan["symbol"])
        snapshot[key] = snapshot_for_plan(
            plan, ticker=ticker, bars=bars, news=news
        )
    return evaluate(store, uid, snapshot)


def _run() -> None:
    last_kline = 0.0
    while not _stop.is_set():
        now = time.time()
        fetch_k = (now - last_kline) >= KLINE_SECONDS
        try:
            poll_once(fetch_klines=fetch_k)
            if fetch_k:
                last_kline = now
        except Exception:
            logger.exception("machine tape loop")
        _stop.wait(TICKER_SECONDS)
