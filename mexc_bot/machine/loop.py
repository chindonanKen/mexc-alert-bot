"""Always-on official tape → evaluate. Paper fills only. No MEXC sends."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from .settings import feature_ad_machine, machine_user_id
from .facts import faster_tf_for, is_stock_symbol
from .tape import (
    board_name_is_fast,
    fatal_news_hits,
    fetch_official_klines,
    fetch_official_ticker,
    fetch_recent_trades,
    note_board_price,
    snapshot_for_plan,
)

logger = logging.getLogger(__name__)

TICKER_SECONDS = 1.0
KLINE_SECONDS = 8.0

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_all_px_at = 0.0
_all_px: Dict[str, float] = {}


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


def _refresh_all_prices() -> Dict[str, float]:
    global _all_px, _all_px_at
    now = time.time()
    if _all_px and now - _all_px_at < 3.0:
        return _all_px
    out: Dict[str, float] = {}
    try:
        from ..exchange import MexcClient, MexcFuturesClient

        out.update(MexcFuturesClient().get_all_prices() or {})
        out.update(MexcClient().get_all_prices() or {})
    except Exception:
        logger.debug("machine board tickers", exc_info=True)
    if out:
        _all_px = out
        _all_px_at = now
        ts = now
        for key, px in out.items():
            try:
                note_board_price(str(key), float(px), ts)
            except (TypeError, ValueError):
                continue
    return _all_px


def _watchlist_board(store, uid: int) -> Dict[str, Any]:
    """Watched coins, stocks out. Mixed / few names → grind off."""
    rows: List[Dict[str, Any]] = []
    try:
        from ..movers.storage import MoverStore

        rows = MoverStore(store.db_path).get_watchlist(int(uid))
    except Exception:
        logger.debug("machine watchlist", exc_info=True)
        rows = []
    coins = [
        r
        for r in rows
        if not is_stock_symbol(r.get("symbol"), r.get("market"))
    ]
    names = len(coins)
    if names < 8:
        return {
            "names": names,
            "fast": 0,
            "slow": 0,
            "panic": False,
            "grind": False,
            "sideways": True,
        }
    pxmap = _refresh_all_prices()
    fast_n = 0
    slow_n = 0
    known = 0
    for row in coins:
        sym = str(row.get("symbol") or "")
        px = _px_for(sym, pxmap)
        if px is None:
            continue
        known += 1
        if board_name_is_fast(sym, px):
            fast_n += 1
        else:
            slow_n += 1
    if known < 8:
        return {
            "names": names,
            "fast": fast_n,
            "slow": slow_n,
            "panic": False,
            "grind": False,
            "sideways": True,
        }
    panic = fast_n >= 3 and fast_n >= max(3, int(0.25 * known))
    grind = (not panic) and slow_n >= int(0.6 * known) and fast_n < 3
    mixed = (not grind) and (not panic)
    return {
        "names": names,
        "known": known,
        "fast": fast_n,
        "slow": slow_n,
        "panic": panic,
        "grind": grind,
        "sideways": mixed,
    }


def _px_for(sym: str, pxmap: Dict[str, float]) -> Optional[float]:
    if not sym:
        return None
    if sym in pxmap:
        return pxmap[sym]
    compact = sym.replace("_", "")
    if compact in pxmap:
        return pxmap[compact]
    if "_" not in sym and sym.upper().endswith("USDT"):
        alt = sym[:-4] + "_USDT"
        if alt in pxmap:
            return pxmap[alt]
    return None


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
    from .engine import evaluate, get_store, log_board_flip, seed_plans

    store = store or get_store()
    uid = int(user_id if user_id is not None else machine_user_id())
    seed_plans(store, uid)
    plans = store.list_plans(uid)

    def _one(plan: Dict[str, Any]) -> tuple:
        ticker = fetch_official_ticker(
            plan["market"], plan["symbol"], spot=spot, futures=futures
        )
        hung = (plan.get("ad_status") == "known") or plan.get("live")
        bars_1m = None
        trades = None
        bars = None
        faster_bars = None
        ftf = None
        news = None
        if hung:
            # Prints as they happen + 1m forming. Never a 15m close as the hung tape.
            trades = fetch_recent_trades(plan["market"], plan["symbol"])
            bars_1m = fetch_official_klines(
                plan["market"], plan["symbol"], "1m", client=kline_client
            )
            ftf = "1m"
        if fetch_klines:
            tf = plan.get("tf")
            if tf:
                bars = fetch_official_klines(
                    plan["market"], plan["symbol"], str(tf), client=kline_client
                )
            elif not hung:
                bars = fetch_official_klines(
                    plan["market"], plan["symbol"], "15m", client=kline_client
                )
            if not hung:
                ftf = faster_tf_for(str(tf or "15m"))
                if ftf and ftf != str(tf or "15m") and ftf != "1m":
                    faster_bars = fetch_official_klines(
                        plan["market"], plan["symbol"], ftf, client=kline_client
                    )
            news = fatal_news_hits(store.db_path, plan["symbol"])
        return plan, ticker, bars, bars_1m, faster_bars, ftf, news, trades

    snapshot: Dict[str, Dict[str, Any]] = {}
    workers = min(8, max(1, len(plans)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        packed = list(pool.map(_one, plans))
    for plan, ticker, bars, bars_1m, faster_bars, ftf, news, trades in packed:
        key = f"{plan['symbol']}|{plan['market']}"
        snapshot[key] = snapshot_for_plan(
            plan,
            ticker=ticker,
            bars=bars,
            bars_1m=bars_1m,
            faster_bars=faster_bars,
            faster_tf=ftf,
            news=news,
            trades=trades,
        )
        if ticker is not None:
            note_board_price(str(plan.get("symbol") or ""), ticker)

    board = _watchlist_board(store, uid)
    for snap in snapshot.values():
        snap["board"] = board
        if board["panic"]:
            snap["panic_board"] = True
            snap["heat_breadth"] = int(board.get("fast") or 0)
    log_board_flip(store, uid, board)
    return evaluate(store, uid, snapshot)


def _run() -> None:
    last_kline = 0.0
    while not _stop.is_set():
        now = time.time()
        fetch_k = (now - last_kline) >= KLINE_SECONDS
        started = time.time()
        try:
            poll_once(fetch_klines=fetch_k)
            if fetch_k:
                last_kline = now
        except Exception:
            logger.exception("machine tape loop")
        elapsed = time.time() - started
        _stop.wait(max(0.2, TICKER_SECONDS - elapsed))
