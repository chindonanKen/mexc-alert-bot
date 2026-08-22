"""Ticker-hunt doorbell: still-up vs already-off. Names only.

Scanner / week window decides which list a name belongs on.
That is a doorbell — not an AD, not a buy line, not last price on the desk.

Rank starts as surge + dump + volume for *order only*.
New names stay unranked until a human mark is stored.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..db_safety import create_table_if_not_exists
from . import db

logger = logging.getLogger(__name__)

_bars_lock = threading.Lock()
_bars_cache: Dict[Tuple[str, str], Tuple[float, List[dict]]] = {}
_BARS_TTL_S = 180.0

# Week window: need a real surge off the low, then ask if last is still
# crowded at the high (wait) or already off it (look at that chart).
SURGE_MIN_PCT = 20.0
OFF_HIGH_PCT = 10.0

HUNT_PUBLIC_KEYS = frozenset({"symbol", "market", "rank"})
_PRICE_LEAK_KEYS = frozenset(
    {
        "price",
        "last",
        "last_price",
        "buy",
        "buy_price",
        "ad",
        "ad_line",
        "visual_ad",
        "high",
        "low",
        "mark",
        "entry",
        "target",
        "distance",
        "distance_pct",
        "surge_pct",
        "dump_pct",
        "volume",
        "start_key",
        "week_high",
        "week_low",
    }
)

HUNT_MARKS_DDL = """
CREATE TABLE IF NOT EXISTS desk_hunt_marks (
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    rank INTEGER,
    marked_at REAL NOT NULL,
    PRIMARY KEY (user_id, symbol, market)
)
"""


def _bar_hlc(bar: dict) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(bar, dict):
        return None
    try:
        h = float(bar["h"] if bar.get("h") is not None else bar.get("high"))
        lo = float(bar["l"] if bar.get("l") is not None else bar.get("low"))
        c = float(bar["c"] if bar.get("c") is not None else bar.get("close"))
    except (TypeError, ValueError, KeyError):
        return None
    if h <= 0 or lo <= 0 or c <= 0:
        return None
    try:
        vol = float(bar["v"] if bar.get("v") is not None else bar.get("volume") or 0)
    except (TypeError, ValueError):
        vol = 0.0
    return h, lo, c, max(0.0, vol)


def week_window_stats(bars: Sequence[dict]) -> Optional[Dict[str, float]]:
    """Internal week stats. Never ship these on the hunt list payload."""
    parsed: List[Tuple[float, float, float, float]] = []
    for b in bars or []:
        row = _bar_hlc(b)
        if row:
            parsed.append(row)
    if len(parsed) < 2:
        return None
    highs = [p[0] for p in parsed]
    lows = [p[1] for p in parsed]
    last = parsed[-1][2]
    week_high = max(highs)
    week_low = min(lows)
    if week_low <= 0 or week_high <= 0:
        return None
    surge_pct = (week_high - week_low) / week_low * 100.0
    dump_pct = max(0.0, (week_high - last) / week_high * 100.0)
    volume = sum(p[3] for p in parsed)
    return {
        "surge_pct": surge_pct,
        "dump_pct": dump_pct,
        "volume": volume,
    }


def classify_week_bars(bars: Sequence[dict]) -> Optional[str]:
    """still_up | already_off | None (no surge → not a hunt name)."""
    return classify_from_stats(week_window_stats(bars))


def classify_from_stats(stats: Optional[Dict[str, float]]) -> Optional[str]:
    if not stats:
        return None
    try:
        surge = float(stats.get("surge_pct") or 0)
        dump = float(stats.get("dump_pct") or 0)
    except (TypeError, ValueError):
        return None
    if surge < SURGE_MIN_PCT:
        return None
    if dump >= OFF_HIGH_PCT:
        return "already_off"
    return "still_up"


def start_sort_key(
    surge_pct: Any,
    dump_pct: Any,
    volume: Any = 0,
) -> float:
    """Surge + dump + volume. Order only — not a Kenneth rank."""
    try:
        surge = float(surge_pct or 0)
    except (TypeError, ValueError):
        surge = 0.0
    try:
        dump = float(dump_pct or 0)
    except (TypeError, ValueError):
        dump = 0.0
    try:
        vol = float(volume or 0)
        vol_term = math.log10(1.0 + vol) if vol > 0 else 0.0
    except (TypeError, ValueError):
        vol_term = 0.0
    return surge + dump + vol_term


def public_hunt_row(symbol: str, market: str, rank: Optional[int]) -> Dict[str, Any]:
    """Names + optional human rank. No prices, no AD."""
    return {
        "symbol": str(symbol or "").strip(),
        "market": (market or "futures").strip().lower() or "futures",
        "rank": int(rank) if rank is not None else None,
    }


def payload_has_price_or_ad(obj: Any) -> bool:
    """True if a hunt payload leaked a price / AD field."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in _PRICE_LEAK_KEYS or lk in ("ad_high", "ad_low", "buy_px"):
                return True
            if payload_has_price_or_ad(v):
                return True
        return False
    if isinstance(obj, (list, tuple)):
        return any(payload_has_price_or_ad(x) for x in obj)
    return False


def assemble_hunt_lists(
    scored: Iterable[dict],
    marks: Optional[Dict[Tuple[str, str], Optional[int]]] = None,
) -> Dict[str, Any]:
    """Split scored names into still-up / already-off. Strip internals."""
    marks = marks or {}
    still: List[dict] = []
    off: List[dict] = []
    for raw in scored or []:
        if not isinstance(raw, dict):
            continue
        sym = str(raw.get("symbol") or "").strip()
        mkt = str(raw.get("market") or "futures").strip().lower() or "futures"
        if not sym:
            continue
        state = raw.get("state") or classify_from_stats(raw)
        if state not in ("still_up", "already_off"):
            continue
        key = (sym.upper(), mkt)
        marked = marks.get(key)
        if marked is None:
            marked = marks.get((sym, mkt))
        row = public_hunt_row(sym, mkt, marked)
        row["_start"] = start_sort_key(
            raw.get("surge_pct"), raw.get("dump_pct"), raw.get("volume")
        )
        (off if state == "already_off" else still).append(row)

    def _order(rows: List[dict]) -> List[dict]:
        def sort_key(r: dict):
            rk = r.get("rank")
            # Marked names first (Kenneth's number), then start rule, then name.
            return (
                0 if rk is not None else 1,
                int(rk) if rk is not None else 0,
                -float(r.get("_start") or 0),
                str(r.get("symbol") or ""),
            )

        out = []
        for r in sorted(rows, key=sort_key):
            r.pop("_start", None)
            out.append({k: r[k] for k in ("symbol", "market", "rank")})
        return out

    payload = {
        "still_up": _order(still),
        "already_off": _order(off),
        "doorbell": True,
    }
    return payload


def ensure_hunt_marks_table(conn: Any = None) -> None:
    own = conn is None
    c = conn or db.connect()
    try:
        create_table_if_not_exists(c, HUNT_MARKS_DDL)
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def load_hunt_marks(user_id: int) -> Dict[Tuple[str, str], Optional[int]]:
    ensure_hunt_marks_table()
    rows = db.fetch_all(
        "SELECT symbol, market, rank FROM desk_hunt_marks WHERE user_id = ?",
        (int(user_id),),
    )
    out: Dict[Tuple[str, str], Optional[int]] = {}
    for r in rows:
        sym = str(r.get("symbol") or "").strip()
        mkt = str(r.get("market") or "futures").strip().lower()
        if not sym:
            continue
        rk = r.get("rank")
        try:
            out[(sym.upper(), mkt)] = int(rk) if rk is not None else None
        except (TypeError, ValueError):
            out[(sym.upper(), mkt)] = None
    return out


def mark_hunt_rank(
    user_id: int,
    symbol: str,
    market: str,
    rank: int,
) -> Dict[str, Any]:
    """Store Kenneth's hunt rank. Additive upsert. Does not touch lessons."""
    ensure_hunt_marks_table()
    sym = str(symbol or "").strip()
    mkt = str(market or "futures").strip().lower() or "futures"
    if not sym:
        raise ValueError("symbol required")
    rk = int(rank)
    if rk < 1 or rk > 99:
        raise ValueError("rank must be 1–99")
    conn = db.connect()
    try:
        conn.execute(
            """
            INSERT INTO desk_hunt_marks (user_id, symbol, market, rank, marked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, symbol, market) DO UPDATE SET
                rank = excluded.rank,
                marked_at = excluded.marked_at
            """,
            (int(user_id), sym, mkt, rk, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return public_hunt_row(sym, mkt, rk)


def _candidate_watch_rows(user_id: int) -> List[dict]:
    try:
        from . import actions

        rows = actions.list_watchlist(user_id)
    except Exception:
        rows = []
    out: List[dict] = []
    seen = set()
    for r in rows or []:
        sym = str(r.get("symbol") or "").strip()
        mkt = str(r.get("market") or "futures").strip().lower() or "futures"
        key = (sym.upper(), mkt)
        if not sym or key in seen:
            continue
        seen.add(key)
        out.append({"symbol": sym, "market": mkt})
    return out


def _default_fetch_bars(market: str, symbol: str) -> List[dict]:
    """Soft-fail week window (closed daily bars). Empty if the book is quiet."""
    cache_key = (str(market).lower(), str(symbol).upper())
    now = time.time()
    with _bars_lock:
        hit = _bars_cache.get(cache_key)
        if hit and (now - hit[0]) < _BARS_TTL_S:
            return hit[1]
    bars: List[dict] = []
    try:
        from ..movers.klines import KlineClient

        client = KlineClient(timeout=2.5, cache_ttl=180.0)
        try:
            bars = client.get_ohlcv(market, symbol, "1d", limit=8) or []
        finally:
            client.close()
    except Exception as e:
        logger.debug("hunt bars skip %s %s: %s", market, symbol, e)
        bars = []
    with _bars_lock:
        _bars_cache[cache_key] = (now, list(bars))
    return bars


def _prefetch_bars(
    names: Sequence[dict],
    fetch_bars: Callable[[str, str], Sequence[dict]],
) -> Dict[Tuple[str, str], Sequence[dict]]:
    out: Dict[Tuple[str, str], Sequence[dict]] = {}
    if not names:
        return out
    workers = min(8, max(1, len(names)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {}
        for row in names:
            sym = str(row.get("symbol") or "").strip()
            mkt = str(row.get("market") or "futures").strip().lower() or "futures"
            if not sym:
                continue
            futs[pool.submit(fetch_bars, mkt, sym)] = (sym.upper(), mkt)
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                out[key] = fut.result() or []
            except Exception:
                out[key] = []
    return out


def hunt_lists(
    user_id: Optional[int] = None,
    *,
    bars_by_key: Optional[Dict[Tuple[str, str], Sequence[dict]]] = None,
    fetch_bars: Optional[Callable[[str, str], Sequence[dict]]] = None,
    candidates: Optional[Sequence[dict]] = None,
    marks: Optional[Dict[Tuple[str, str], Optional[int]]] = None,
) -> Dict[str, Any]:
    """Build the two doorbell lists. Tests inject bars — no network required."""
    uid = int(user_id or db.default_user_id() or 0)
    if marks is None and uid:
        try:
            marks = load_hunt_marks(uid)
        except Exception:
            marks = {}
    marks = marks or {}
    names = list(candidates) if candidates is not None else (
        _candidate_watch_rows(uid) if uid else []
    )
    names = names[:24]
    scored: List[dict] = []
    if bars_by_key is None and names:
        fetcher = fetch_bars or _default_fetch_bars
        bars_by_key = _prefetch_bars(names, fetcher)
    for row in names:
        sym = str(row.get("symbol") or "").strip()
        mkt = str(row.get("market") or "futures").strip().lower() or "futures"
        if not sym:
            continue
        key = (sym.upper(), mkt)
        bars: Sequence[dict] = []
        if bars_by_key is not None:
            bars = bars_by_key.get(key) or bars_by_key.get((sym, mkt)) or []
        stats = week_window_stats(bars)
        state = classify_from_stats(stats)
        if not state or not stats:
            continue
        scored.append(
            {
                "symbol": sym,
                "market": mkt,
                "state": state,
                "surge_pct": stats["surge_pct"],
                "dump_pct": stats["dump_pct"],
                "volume": stats["volume"],
            }
        )
    return assemble_hunt_lists(scored, marks)
