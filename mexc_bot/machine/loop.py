"""Always-on official tape → evaluate. Paper fills only. No MEXC sends."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from .settings import feature_ad_machine, machine_user_id
from .facts import faster_tf_for, is_stock_symbol
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
    plans = store.list_plans(uid)
    snapshot: Dict[str, Dict[str, Any]] = {}
    fast_flags: List[bool] = []
    for plan in plans:
        key = f"{plan['symbol']}|{plan['market']}"
        ticker = fetch_official_ticker(
            plan["market"], plan["symbol"], spot=spot, futures=futures
        )
        bars = None
        bars_1m = None
        faster_bars = None
        ftf = None
        news = None
        hung = (plan.get("ad_status") == "known") or plan.get("live")
        if hung:
            bars_1m = fetch_official_klines(
                plan["market"], plan["symbol"], "1m", client=kline_client, limit=20
            )
        if fetch_klines:
            tf = plan.get("tf") or "15m"
            bars = fetch_official_klines(
                plan["market"], plan["symbol"], str(tf), client=kline_client
            )
            ftf = faster_tf_for(str(tf))
            if ftf and ftf != str(tf) and ftf != "1m":
                faster_bars = fetch_official_klines(
                    plan["market"], plan["symbol"], ftf, client=kline_client
                )
            news = fatal_news_hits(store.db_path, plan["symbol"])
        snap = snapshot_for_plan(
            plan,
            ticker=ticker,
            bars=bars,
            bars_1m=bars_1m,
            faster_bars=faster_bars,
            faster_tf=ftf,
            news=news,
        )
        snapshot[key] = snap
        if hung and not is_stock_symbol(plan.get("symbol")):
            fast_flags.append(bool(snap.get("vol_usd_fast") and snap.get("reds", {}).get("1m")))
    coin_n = len(fast_flags)
    fast_n = sum(1 for x in fast_flags if x)
    slow_n = coin_n - fast_n
    board = {
        "names": coin_n,
        "fast": fast_n,
        "slow": slow_n,
        "panic": coin_n >= 3 and fast_n >= 3,
        "grind": coin_n >= 3 and slow_n >= int(0.6 * coin_n) and fast_n < 3,
        "sideways": coin_n < 3 or (fast_n > 0 and slow_n > 0 and fast_n < 3),
    }
    for snap in snapshot.values():
        snap["board"] = board
        if board["panic"]:
            snap["panic_board"] = True
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
