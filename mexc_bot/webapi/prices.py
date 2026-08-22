"""Live public prices for the desk (MEXC primary, Binance fallback).

Hot path (Overview pulse + Movers marks) used to call /ticker/24hr once per
symbol. Lab 2026-08-22: 10 sequential symbols = 1.8s (~179ms each) → ~4s
for a 37-name watchlist.

Request path fetches only needed names in parallel (MEXC wave, then Binance
for leftovers). In-process TTL (12s) + negative cache so polls are a lookup.
A background thread may fill the full MEXC 24hr book without blocking paint.
Stale marks for a few seconds are intentional.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:
    _CA = True

logger = logging.getLogger(__name__)

# Soft poll is 10s; 12s keeps a warm hit on the next Movers/Overview refresh.
TICKER_CACHE_TTL_S = 12.0
# First-paint path fetches only requested names (not the 800KB all-ticker book).
# Desk watchlist asks for up to 40; one wave keeps wall clock at ~1 RTT.
_NEEDED_PARALLEL = 40

_session = requests.Session()
_session.verify = _CA
_session.headers.update({"User-Agent": "mexc-desk-v2/0.1"})

_lock = threading.Lock()
_book: Dict[str, Dict[str, Any]] = {}
_book_ts: float = 0.0
_last_fetch_ms: float = 0.0
_last_source: str = ""
_miss_until: Dict[str, float] = {}
_bg_lock = threading.Lock()
_bg_inflight = False


def reset_ticker_cache() -> None:
    """Tests only — drop the in-process book."""
    global _book_ts, _last_fetch_ms, _last_source
    with _lock:
        _book.clear()
        _book_ts = 0.0
        _last_fetch_ms = 0.0
        _last_source = ""
        _miss_until.clear()


def ticker_cache_info() -> Dict[str, Any]:
    """Tests / debug — no secrets."""
    with _lock:
        age = (time.time() - _book_ts) if _book_ts else None
        return {
            "size": len(_book),
            "age_s": age,
            "ttl_s": TICKER_CACHE_TTL_S,
            "last_fetch_ms": _last_fetch_ms,
            "last_source": _last_source,
            "fresh": bool(_book_ts) and age is not None and age < TICKER_CACHE_TTL_S,
        }


def normalize_ticker_symbol(symbol: str) -> str:
    """Compact USDT pair: BTC_USDT / btc-usdt → BTCUSDT."""
    sym = (symbol or "").upper().replace("_", "").replace("-", "").strip()
    if not sym:
        return ""
    if not sym.endswith("USDT"):
        sym = sym + "USDT"
    return sym


def _row_from_24h(d: Any, source: str) -> Optional[Dict[str, Any]]:
    if not isinstance(d, dict):
        return None
    try:
        last = float(d.get("lastPrice") or d.get("price") or 0)
        chg = float(d.get("priceChangePercent") or 0)
    except (TypeError, ValueError):
        return None
    if last <= 0:
        return None
    raw = str(d.get("symbol") or "").upper().replace("_", "").replace("-", "")
    if not raw:
        return None
    return {
        "symbol": raw,
        "price": last,
        "changePercent": chg,
        "source": source,
    }


def _parse_24h_payload(data: Any, source: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(data, dict):
        row = _row_from_24h(data, source)
        if row:
            out[row["symbol"]] = row
        return out
    if not isinstance(data, list):
        return out
    for item in data:
        row = _row_from_24h(item, source)
        if row:
            out[row["symbol"]] = row
    return out


def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    r = _session.get(url, params=params, timeout=8)
    if r.status_code != 200:
        return None
    return r.json()


def _fetch_mexc_24h_all() -> Dict[str, Dict[str, Any]]:
    try:
        data = _get_json("https://api.mexc.com/api/v3/ticker/24hr")
        return _parse_24h_payload(data, "mexc")
    except Exception as e:
        logger.debug("mexc batch 24hr: %s", e)
        return {}


def _ticker_24h_single(sym: str) -> Optional[Dict[str, Any]]:
    """One-symbol path (MEXC then Binance). Used for needed-parallel + rare miss."""
    for base, source in (
        ("https://api.mexc.com", "mexc"),
        ("https://api.binance.com", "binance"),
    ):
        try:
            data = _get_json(f"{base}/api/v3/ticker/24hr", params={"symbol": sym})
            row = _row_from_24h(data, source) if data is not None else None
            if row:
                return row
        except Exception as e:
            logger.debug("ticker %s via %s: %s", sym, base, e)
    return None


def _fetch_exchange_parallel(
    keys: List[str], base: str, source: str
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not keys:
        return out
    workers = min(_NEEDED_PARALLEL, len(keys))

    def _one(sym: str) -> Optional[Dict[str, Any]]:
        try:
            data = _get_json(f"{base}/api/v3/ticker/24hr", params={"symbol": sym})
            return _row_from_24h(data, source) if data is not None else None
        except Exception as e:
            logger.debug("ticker %s via %s: %s", sym, base, e)
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, k) for k in keys]
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception:
                continue
            if row:
                out[row["symbol"]] = row
    return out


def _fetch_needed_parallel(keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """Needed names only, one MEXC wave (not N sequential).

    Binance is not on this path — a second wave was the extra RTT that
    pushed first /api/watchlist over 800ms. ticker_24h still falls back.
    """
    if not keys:
        return {}
    return _fetch_exchange_parallel(keys, "https://api.mexc.com", "mexc")


def _needed_keys(needed: Optional[Iterable[str]]) -> List[str]:
    keys: List[str] = []
    seen = set()
    for raw in needed or ():
        key = normalize_ticker_symbol(raw)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def schedule_ticker_prewarm() -> None:
    """Non-blocking full MEXC book so later mark attach is a dict lookup."""
    global _bg_inflight, _book_ts, _last_fetch_ms, _last_source
    with _bg_lock:
        if _bg_inflight:
            return
        _bg_inflight = True

    def _run() -> None:
        global _bg_inflight, _book_ts, _last_fetch_ms, _last_source
        try:
            t0 = time.perf_counter()
            fresh = _fetch_mexc_24h_all()
            elapsed = (time.perf_counter() - t0) * 1000.0
            if fresh:
                with _lock:
                    _book.update(fresh)
                    _book_ts = time.time()
                    _last_fetch_ms = elapsed
                    _last_source = "mexc_batch_bg"
                logger.info(
                    "ticker cache bg mexc_batch n=%s ms=%.0f",
                    len(fresh),
                    elapsed,
                )
            else:
                with _lock:
                    _last_fetch_ms = elapsed
                    _last_source = "mexc_batch_bg_fail"
        except Exception as e:
            logger.debug("ticker prewarm: %s", e)
        finally:
            with _bg_lock:
                _bg_inflight = False

    threading.Thread(target=_run, daemon=True, name="desk-ticker-prewarm").start()


def ensure_ticker_book(needed: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Return the compact-symbol book.

    Request path: if the TTL is cold or names are missing, fetch *those names*
    in parallel (not the full 800KB 24hr dump). A background thread may fill
    the rest of the book without blocking first paint.
    """
    global _book_ts, _last_fetch_ms, _last_source
    now = time.time()
    with _lock:
        fresh = _book_ts > 0 and (now - _book_ts) < TICKER_CACHE_TTL_S
        book = dict(_book)

    now2 = time.time()
    keys = _needed_keys(needed)
    if fresh:
        missing = [
            k
            for k in keys
            if k not in book and _miss_until.get(k, 0) < now2
        ]
    else:
        # TTL expired: refetch the names this request needs (keep stale as fallback).
        missing = list(keys)

    if missing:
        t0 = time.perf_counter()
        fill = _fetch_needed_parallel(missing)
        elapsed = (time.perf_counter() - t0) * 1000.0
        if fill:
            with _lock:
                _book.update(fill)
                _book_ts = time.time()
                _last_fetch_ms = elapsed
                _last_source = "needed_parallel"
                for k in missing:
                    if k in fill:
                        _miss_until.pop(k, None)
                    else:
                        _miss_until[k] = time.time() + TICKER_CACHE_TTL_S
            book.update(fill)
            logger.info(
                "ticker cache needed_parallel n=%s/%s ms=%.0f",
                len(fill),
                len(missing),
                elapsed,
            )
        else:
            with _lock:
                _book_ts = time.time()
                _last_fetch_ms = elapsed
                _last_source = "needed_parallel_empty"
                for k in missing:
                    _miss_until[k] = time.time() + TICKER_CACHE_TTL_S

    return book


def warm_ticker_cache() -> Dict[str, Any]:
    """Kick a background full-book refresh. Does not block on MEXC."""
    schedule_ticker_prewarm()
    return ticker_cache_info()


def ticker_24h(symbol: str) -> Optional[Dict[str, Any]]:
    key = normalize_ticker_symbol(symbol)
    if not key:
        return None
    book = ensure_ticker_book((key,))
    hit = book.get(key)
    if hit:
        return dict(hit)
    row = _ticker_24h_single(key)
    if row:
        with _lock:
            _book[key] = row
        return dict(row)
    return None


def watchlist_tickers(symbols: List[str]) -> List[Dict[str, Any]]:
    """Marks for Movers. Parallel needed-set + TTL, no sequential per-row HTTP."""
    out: List[Dict[str, Any]] = []
    keys: List[str] = []
    seen = set()
    for s in symbols:
        key = normalize_ticker_symbol(s)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    book = ensure_ticker_book(keys)
    for key in keys:
        t = book.get(key)
        if t:
            out.append(dict(t))
    return out


def market_context() -> Dict[str, Any]:
    """BTC/ETH/SOL pulse for AD regime context."""
    majors = []
    book = ensure_ticker_book(("BTCUSDT", "ETHUSDT", "SOLUSDT"))
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        t = book.get(s)
        if t:
            majors.append(dict(t))
    btc = next((m for m in majors if m["symbol"] == "BTCUSDT"), None)
    regime = "UNKNOWN"
    if btc:
        c = btc["changePercent"]
        if c <= -3:
            regime = "RISK_OFF"
        elif c <= -1:
            regime = "SOFT"
        elif c >= 2:
            regime = "RISK_ON"
        else:
            regime = "RANGE"
    return {"majors": majors, "regime": regime}
