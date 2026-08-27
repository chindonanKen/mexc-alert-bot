"""Public MEXC contractSize cache — cash USD = price × vol × contractSize.

Deal ``quote_qty`` in this repo is price×vol (notional), not leftover-cost cash.
ONG_USDT = 10; MRNASTOCK_USDT = 0.001. Unknown size → do not paint In/Out.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:  # pragma: no cover
    _CA = True

logger = logging.getLogger(__name__)

_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
_TTL_S = 3600.0
_cache: Dict[str, float] = {}
_cache_ts: float = 0.0
_fetch_failed_at: float = 0.0

# Live public spec (2026-08-27) — used when the catalog fetch is offline.
_KNOWN = {
    "ONG_USDT": 10.0,
    "MRNASTOCK_USDT": 0.001,
}


def _norm(symbol: Any) -> str:
    s = str(symbol or "").strip().upper().replace("-", "_").replace("/", "_")
    if s and "_" not in s and s.endswith("USDT") and len(s) > 4:
        s = s[:-4] + "_USDT"
    return s


def prime_contract_size(symbol: str, contract_size: float) -> None:
    n = _norm(symbol)
    cs = float(contract_size)
    if n and cs > 0:
        _cache[n] = cs


def clear_contract_size_cache() -> None:
    global _cache_ts, _fetch_failed_at
    _cache.clear()
    _cache_ts = 0.0
    _fetch_failed_at = 0.0


def _sf(val: Any) -> Optional[float]:
    if val in (None, ""):
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def contract_size_from_obj(obj: Any) -> Optional[float]:
    if not isinstance(obj, dict):
        return None
    for key in ("contractSize", "contract_size", "cs", "faceValue", "face_value"):
        n = _sf(obj.get(key))
        if n is not None:
            return n
    raw = obj.get("raw")
    if isinstance(raw, dict):
        for key in ("contractSize", "contract_size", "cs"):
            n = _sf(raw.get(key))
            if n is not None:
                return n
    return None


def _ingest_payload(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    data = payload.get("data")
    rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = _norm(row.get("symbol"))
        cs = _sf(row.get("contractSize") or row.get("contract_size"))
        if sym and cs is not None:
            _cache[sym] = cs
            n += 1
    return n


def refresh_contract_size_catalog(*, force: bool = False, timeout: float = 8.0) -> bool:
    """One public catalog fetch. Soft-fail. Does not invent size=1."""
    global _cache_ts, _fetch_failed_at
    now = time.time()
    if not force and _cache and (now - _cache_ts) < _TTL_S:
        return True
    if not force and _fetch_failed_at and (now - _fetch_failed_at) < 60.0:
        return False
    try:
        resp = requests.get(_DETAIL_URL, timeout=timeout, verify=_CA)
        if resp.status_code != 200:
            _fetch_failed_at = now
            return False
        n = _ingest_payload(resp.json())
        if n <= 0:
            _fetch_failed_at = now
            return False
        _cache_ts = now
        _fetch_failed_at = 0.0
        return True
    except Exception as exc:
        logger.debug("contract detail catalog: %s", exc)
        _fetch_failed_at = now
        return False


def resolve_futures_contract_size(
    symbol: Any,
    *hints: Any,
    fetch: bool = True,
) -> Optional[float]:
    """Authoritative contractSize or None. None = In/Out cannot be proven as cash.

    A bare ``1.0`` on the entity is often a missing-size default — catalog /
    known spec wins so ONG (10) and MRNASTOCK (0.001) are not painted as
    price×vol dollars.
    """
    hinted: Optional[float] = None
    for hint in hints:
        n = contract_size_from_obj(hint) if isinstance(hint, dict) else _sf(hint)
        if n is None:
            continue
        if n != 1.0:
            return n
        hinted = hinted or n
    key = _norm(symbol)
    if not key:
        return hinted
    if key in _cache:
        return _cache[key]
    if key in _KNOWN:
        return _KNOWN[key]
    if fetch:
        refresh_contract_size_catalog()
        if key in _cache:
            return _cache[key]
    return hinted
