"""Incident OHLC for Learning teach — that fire's candles, not live thesis.

Does not invent a visual AD. Formula chart_features / swing-median stay unused.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..movers.klines import _INTERVALS, KlineClient
from .symbols import normalize_learning_symbol

logger = logging.getLogger(__name__)

DEFAULT_TF = "15m"
DEFAULT_LOOKBACK = 6 * 3600
DEFAULT_LOOKAHEAD = 2 * 3600
MIN_BARS = 32
_SYM_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_TF_ALIASES = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "1D": "1d",
    "1w": "1w",
    "60m": "1h",
    "min1": "1m",
    "min5": "5m",
    "min15": "15m",
    "min60": "1h",
    "hour4": "4h",
}
TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


def normalize_tf(tf: Any) -> str:
    """Accept a known interval. Reject garbage (no silent 15m fallback)."""
    raw = str(tf if tf is not None else "").strip()
    if not raw or len(raw) > 24:
        raise ValueError("bad tf")
    key = raw.lower()
    mapped = _TF_ALIASES.get(raw) or _TF_ALIASES.get(key)
    if mapped and mapped in _INTERVALS:
        return mapped
    if raw in _INTERVALS:
        return "1d" if raw == "1D" else raw
    if key in _INTERVALS:
        return key
    raise ValueError("bad tf")


def sanitize_symbol(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if not s or not _SYM_OK.fullmatch(s):
        raise ValueError("bad symbol")
    return s


def sanitize_market(raw: Any) -> str:
    m = str(raw or "futures").strip().lower()
    if m not in ("spot", "futures"):
        raise ValueError("bad market")
    return m


def as_unix_ts(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    ts = float(value)
    if ts > 1e12:
        ts /= 1000.0
    if ts <= 0:
        return None
    return ts


def default_tf_for_case(
    *,
    visual_ad: Any = None,
    tf_hint: Any = None,
    requested: Any = None,
) -> str:
    """Staff TF, then case hint, then 15m. Does not invent high/low."""
    if requested is not None and str(requested).strip():
        return normalize_tf(requested)
    if isinstance(visual_ad, dict) and visual_ad.get("tf"):
        try:
            return normalize_tf(visual_ad.get("tf"))
        except ValueError:
            pass
    if tf_hint is not None and str(tf_hint).strip():
        try:
            return normalize_tf(tf_hint)
        except ValueError:
            pass
    return DEFAULT_TF


def lookback_seconds(tf: str, requested: Optional[int] = None) -> int:
    """Reuse incident lookback; keep enough bars on higher TFs for structure."""
    req = int(requested) if requested else DEFAULT_LOOKBACK
    req = max(60, min(req, 14 * 86400))
    sec = TF_SECONDS.get(tf, 900)
    return max(req, MIN_BARS * sec)


def serialize_bar(bar: Dict[str, Any]) -> Optional[Dict[str, float]]:
    try:
        return {
            "ts": float(bar["ts"]),
            "o": float(bar["o"]),
            "h": float(bar["h"]),
            "l": float(bar["l"]),
            "c": float(bar["c"]),
            "v": float(bar.get("v") or 0.0),
        }
    except (TypeError, ValueError, KeyError):
        return None


def fetch_incident_bars(
    *,
    market: str,
    symbol: str,
    tf: str,
    fire_ts: float,
    lookback: int,
    lookahead: int = DEFAULT_LOOKAHEAD,
) -> List[Dict[str, float]]:
    client = KlineClient()
    try:
        raw = client.get_ohlcv_around(
            market,
            symbol,
            tf,
            fire_ts,
            lookback_seconds=lookback,
            lookahead_seconds=lookahead,
            limit=500,
        )
    except Exception as e:
        logger.debug("incident ohlc fetch: %s", e)
        raw = []
    finally:
        try:
            client.close()
        except Exception:
            pass
    out: List[Dict[str, float]] = []
    for b in raw or []:
        packed = serialize_bar(b)
        if packed:
            out.append(packed)
    out.sort(key=lambda x: x["ts"])
    return out


def _incident_from_case_row(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from .cases import case_public_view

    view = case_public_view(row)
    inc = view.get("incident") if isinstance(view.get("incident"), dict) else {}
    return view, inc


def _pack_response(
    *,
    symbol: str,
    market: str,
    tf: str,
    fire_ts: float,
    fire_price: Optional[float],
    lookback: int,
    bars: List[Dict[str, float]],
    case_id: Optional[int] = None,
    event_id: Optional[int] = None,
) -> Dict[str, Any]:
    # Intentionally omit visual_ad — never invent a mark from klines.
    return {
        "ok": True,
        "symbol": symbol,
        "market": market,
        "tf": tf,
        "fire_ts": fire_ts,
        "fire_price": fire_price,
        "lookback_seconds": lookback,
        "lookahead_seconds": DEFAULT_LOOKAHEAD,
        "bars": bars,
        "case_id": case_id,
        "event_id": event_id,
        "anchor": "fire",
        "live": False,
    }


def incident_candles(
    store,
    user_id: int,
    *,
    case_id: Optional[int] = None,
    event_id: Optional[int] = None,
    symbol: Optional[str] = None,
    market: Optional[str] = None,
    fire_ts: Optional[float] = None,
    tf: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve identity from the case/event, then fetch that symbol's OHLC.

    Client symbol/market/fire_ts cannot retarget a known case or event.
    """
    uid = int(user_id)
    view: Optional[Dict[str, Any]] = None
    ev: Optional[Dict[str, Any]] = None
    cid: Optional[int] = None
    eid: Optional[int] = None

    if case_id is not None and str(case_id) != "":
        row = store.get_setup_case(uid, case_id=int(case_id))
        if not row:
            raise FileNotFoundError("Case not found")
        view, inc = _incident_from_case_row(row)
        cid = int(view.get("id") or case_id)
        eid = view.get("event_id")
        symbol = view.get("symbol")
        market = view.get("market") or "futures"
        fire_ts = (
            view.get("fire_ts")
            or view.get("incident_ts")
            or inc.get("ts")
        )
        fire_price = view.get("fire_price")
        if fire_price is None:
            fire_price = view.get("incident_price") or inc.get("price")
        if (fire_ts is None or fire_price is None) and eid:
            ev_row = store.get_event(uid, int(eid))
            if ev_row:
                fire_ts = fire_ts or ev_row.get("ts")
                if fire_price is None:
                    fire_price = ev_row.get("price")
        lookback_req = inc.get("chart_lookback_seconds")
        tf_use = default_tf_for_case(
            visual_ad=view.get("visual_ad"),
            tf_hint=view.get("tf_hint"),
            requested=tf,
        )
    elif event_id is not None and str(event_id) != "":
        ev = store.get_event(uid, int(event_id))
        if not ev:
            if not symbol or fire_ts is None or fire_ts == "":
                raise FileNotFoundError("Event not found")
            fire_price = None
            lookback_req = None
            tf_use = default_tf_for_case(requested=tf)
            eid = int(event_id)
        else:
            eid = int(ev.get("id") or event_id)
            symbol = ev.get("symbol")
            market = ev.get("market") or "futures"
            fire_ts = ev.get("ts")
            fire_price = ev.get("price")
            lookback_req = None
            case_row = store.get_setup_case(uid, event_id=eid)
            if case_row:
                view, inc = _incident_from_case_row(case_row)
                cid = view.get("id")
                lookback_req = inc.get("chart_lookback_seconds")
                if view.get("fire_ts") or view.get("incident_ts"):
                    fire_ts = view.get("fire_ts") or view.get("incident_ts")
                if view.get("fire_price") is not None:
                    fire_price = view.get("fire_price")
                symbol = view.get("symbol") or symbol
                market = view.get("market") or market
                tf_use = default_tf_for_case(
                    visual_ad=view.get("visual_ad"),
                    tf_hint=view.get("tf_hint"),
                    requested=tf,
                )
            else:
                tf_use = default_tf_for_case(requested=tf)
    else:
        if not symbol or fire_ts is None or fire_ts == "":
            raise ValueError("need case_id, event_id, or symbol+fire_ts")
        fire_price = None
        lookback_req = None
        tf_use = default_tf_for_case(requested=tf)

    mkt = sanitize_market(market)
    sym = normalize_learning_symbol(sanitize_symbol(symbol), mkt)
    ts = as_unix_ts(fire_ts)
    if ts is None:
        raise ValueError("need fire_ts")
    lookback = lookback_seconds(tf_use, lookback_req)
    bars = fetch_incident_bars(
        market=mkt,
        symbol=sym,
        tf=tf_use,
        fire_ts=ts,
        lookback=lookback,
    )
    return _pack_response(
        symbol=sym,
        market=mkt,
        tf=tf_use,
        fire_ts=ts,
        fire_price=float(fire_price) if fire_price not in (None, "") else None,
        lookback=lookback,
        bars=bars,
        case_id=int(cid) if cid is not None else None,
        event_id=int(eid) if eid is not None else None,
    )
