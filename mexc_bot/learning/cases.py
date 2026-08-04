"""P1 Case factory — freeze structured setup features for the AD agent.

Features index the setup; owner chips + note annotate judgment.
Never block mover fires: freeze soft-fails and can run async.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .chart_features import compute_fire_features
from .store import EventStore

logger = logging.getLogger(__name__)


def _parse_features(row: Optional[dict]) -> Dict[str, Any]:
    if not row:
        return {}
    raw = row.get("features_json")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def case_public_view(row: dict) -> Dict[str, Any]:
    """UI/API shape for a frozen case."""
    feats = _parse_features(row)
    chips = []
    try:
        chips = json.loads(row.get("chips_json") or "[]")
    except Exception:
        chips = []
    ok = bool(row.get("features_ok")) or bool(feats.get("ok"))
    freeze = "ok" if ok else ("partial" if row else "none")
    band = row.get("velocity_band") or feats.get("band")
    drop = row.get("drop_pct")
    if drop is None and feats.get("dd_pct") is not None:
        drop = -abs(float(feats["dd_pct"]))
    return {
        "id": row.get("id"),
        "event_id": row.get("event_id"),
        "symbol": row.get("symbol"),
        "market": row.get("market"),
        "frozen_at": row.get("frozen_at"),
        "fire_ts": row.get("fire_ts"),
        "fire_price": row.get("fire_price"),
        "ref_price": row.get("ref_price"),
        "drop_pct": drop,
        "velocity_band": band,
        "heat_breadth": row.get("heat_breadth")
        if row.get("heat_breadth") is not None
        else feats.get("heat_breadth"),
        "features_ok": ok,
        "freeze": freeze,
        "band": band,
        "dd_pct": feats.get("dd_pct"),
        "vel_pct_min": feats.get("vel_pct_min"),
        "ad_zone": feats.get("ad_zone"),
        "ad_depth_ratio": feats.get("ad_depth_ratio"),
        "ad_ready": feats.get("ad_ready"),
        "vol_flag": feats.get("vol_flag"),
        "vol_ratio": feats.get("vol_ratio"),
        "setup_prior": feats.get("setup_prior"),
        "sharp_score": feats.get("sharp_score"),
        "rsi_now_5m": feats.get("rsi_now_5m"),
        "div_bull": feats.get("div_bull"),
        "chips": chips,
        "note": row.get("note"),
        "lesson_id": row.get("lesson_id"),
        "trade_key": row.get("trade_key"),
        "source": row.get("source"),
        "features": feats,
    }


def build_features_for_event(
    *,
    market: str,
    symbol: str,
    fire_px: Optional[float],
    fire_ts: Optional[float],
    ref_price: Optional[float] = None,
    velocity_band: Optional[str] = None,
    heat_breadth: Optional[int] = None,
) -> Dict[str, Any]:
    px = float(fire_px or 0) or 0.0
    ts = float(fire_ts or time.time())
    return compute_fire_features(
        market=(market or "futures").lower(),
        symbol=symbol or "",
        fire_px=px if px > 0 else 1e-12,
        fire_ts=ts,
        peak_px=float(ref_price) if ref_price else None,
        peak_ts=None,
        heat_breadth=heat_breadth,
        velocity_band=velocity_band,
    )


def freeze_case(
    store: EventStore,
    user_id: int,
    *,
    symbol: str,
    market: str,
    event_id: Optional[int] = None,
    fire_ts: Optional[float] = None,
    fire_price: Optional[float] = None,
    ref_price: Optional[float] = None,
    drop_pct: Optional[float] = None,
    velocity_band: Optional[str] = None,
    heat_breadth: Optional[int] = None,
    chips: Optional[List[str]] = None,
    note: Optional[str] = None,
    lesson_id: Optional[int] = None,
    trade_key: Optional[str] = None,
    source: str = "fire",
    recompute: bool = True,
) -> Dict[str, Any]:
    """Freeze or update a setup case. Soft-fail friendly."""
    feats: Dict[str, Any] = {}
    if recompute:
        feats = build_features_for_event(
            market=market,
            symbol=symbol,
            fire_px=fire_price,
            fire_ts=fire_ts,
            ref_price=ref_price,
            velocity_band=velocity_band,
            heat_breadth=heat_breadth,
        )
    cid = store.upsert_setup_case(
        user_id,
        symbol=symbol,
        market=market,
        event_id=event_id,
        fire_ts=fire_ts,
        fire_price=fire_price,
        ref_price=ref_price,
        drop_pct=drop_pct,
        velocity_band=velocity_band,
        heat_breadth=heat_breadth,
        features=feats if recompute else None,
        chips=chips,
        note=note,
        lesson_id=lesson_id,
        trade_key=trade_key,
        source=source,
    )
    row = store.get_setup_case(user_id, case_id=cid) if cid else None
    if not row:
        return {
            "ok": False,
            "freeze": "none",
            "error": "case write failed",
            "symbol": symbol,
            "market": market,
        }
    view = case_public_view(row)
    view["ok"] = True
    return view


def freeze_case_async(
    store: EventStore,
    user_id: int,
    **kwargs: Any,
) -> None:
    """Background freeze so mover notify path stays snappy."""

    def _run() -> None:
        try:
            freeze_case(store, user_id, **kwargs)
        except Exception as e:
            logger.warning("async freeze_case failed: %s", e)

    threading.Thread(target=_run, name="p1-case-freeze", daemon=True).start()


def case_preview(
    store: EventStore,
    user_id: int,
    *,
    event_id: Optional[int] = None,
    symbol: Optional[str] = None,
    market: Optional[str] = None,
    fire_price: Optional[float] = None,
    fire_ts: Optional[float] = None,
    ref_price: Optional[float] = None,
    drop_pct: Optional[float] = None,
    velocity_band: Optional[str] = None,
    heat_breadth: Optional[int] = None,
    trade_key: Optional[str] = None,
    compute_if_missing: bool = True,
) -> Dict[str, Any]:
    """Preview for Learning UI — prefer stored case, else live compute."""
    if event_id is not None:
        row = store.get_setup_case(user_id, event_id=int(event_id))
        if row:
            return case_public_view(row)
        # load event if present
        try:
            ev = None
            for e in store.recent_events(user_id, limit=80):
                if int(e.get("id") or 0) == int(event_id):
                    ev = e
                    break
            if ev:
                symbol = symbol or ev.get("symbol")
                market = market or ev.get("market")
                fire_price = fire_price if fire_price is not None else ev.get("price")
                fire_ts = fire_ts if fire_ts is not None else ev.get("ts")
                ref_price = ref_price if ref_price is not None else ev.get("ref_price")
                drop_pct = drop_pct if drop_pct is not None else ev.get("drop_pct")
                velocity_band = velocity_band or ev.get("velocity_band")
                heat_breadth = (
                    heat_breadth
                    if heat_breadth is not None
                    else ev.get("heat_breadth")
                )
        except Exception:
            pass

    if not symbol:
        return {"ok": False, "freeze": "none", "error": "no symbol"}

    mkt = (market or "futures").lower()
    if not compute_if_missing:
        return {
            "ok": False,
            "freeze": "none",
            "symbol": symbol,
            "market": mkt,
            "drop_pct": drop_pct,
            "velocity_band": velocity_band,
            "fire_price": fire_price,
        }

    # Live preview (not necessarily persisted until teach/fire freeze)
    feats = build_features_for_event(
        market=mkt,
        symbol=symbol,
        fire_px=fire_price,
        fire_ts=fire_ts,
        ref_price=ref_price,
        velocity_band=velocity_band,
        heat_breadth=heat_breadth,
    )
    ok = bool(feats.get("ok"))
    drop = drop_pct
    if drop is None and feats.get("dd_pct") is not None:
        drop = -abs(float(feats["dd_pct"]))
    return {
        "ok": ok,
        "freeze": "ok" if ok else "partial",
        "event_id": event_id,
        "symbol": str(symbol).upper(),
        "market": mkt,
        "fire_ts": fire_ts,
        "fire_price": fire_price,
        "ref_price": ref_price,
        "drop_pct": drop,
        "velocity_band": velocity_band or feats.get("band"),
        "heat_breadth": heat_breadth if heat_breadth is not None else feats.get("heat_breadth"),
        "features_ok": ok,
        "band": feats.get("band") or velocity_band,
        "dd_pct": feats.get("dd_pct"),
        "vel_pct_min": feats.get("vel_pct_min"),
        "ad_zone": feats.get("ad_zone"),
        "ad_depth_ratio": feats.get("ad_depth_ratio"),
        "ad_ready": feats.get("ad_ready"),
        "vol_flag": feats.get("vol_flag"),
        "vol_ratio": feats.get("vol_ratio"),
        "setup_prior": feats.get("setup_prior"),
        "sharp_score": feats.get("sharp_score"),
        "rsi_now_5m": feats.get("rsi_now_5m"),
        "div_bull": feats.get("div_bull"),
        "trade_key": trade_key,
        "features": feats,
        "persisted": False,
    }
