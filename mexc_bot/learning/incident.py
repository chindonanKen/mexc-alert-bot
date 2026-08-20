"""Bind a lesson/case to the exact price-action moment (incident).

Each teach/fire has its own timestamp + price. Multiple lessons on BLUAI at
different times are different incidents — never merge by symbol alone.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def build_incident(
    *,
    incident_ts: Optional[float] = None,
    incident_price: Optional[float] = None,
    ref_price: Optional[float] = None,
    event_id: Optional[int] = None,
    trade_key: Optional[str] = None,
    drop_pct: Optional[float] = None,
    chart_tfs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    ts = float(incident_ts) if incident_ts is not None else time.time()
    px = float(incident_price) if incident_price is not None else None
    return {
        "incident_ts": ts,
        "incident_price": px,
        "ref_price": float(ref_price) if ref_price is not None else None,
        "event_id": int(event_id) if event_id is not None else None,
        "trade_key": trade_key,
        "drop_pct": drop_pct,
        # Chart window: look back from incident for multi-TF structure
        "chart_tfs": chart_tfs or ["5m", "15m", "1h"],
        "chart_lookback_seconds": 6 * 3600,
        "anchor": "fire" if event_id is not None else ("trade" if trade_key else "teach"),
    }


def incident_tags(inc: Dict[str, Any]) -> List[str]:
    """Structured tags for lesson rows (ts:/px:)."""
    tags: List[str] = []
    ts = inc.get("incident_ts")
    if ts is not None:
        # integer seconds is enough for UI + join; keep float if sub-second
        tags.append(f"ts:{float(ts):.3f}".rstrip("0").rstrip("."))
    px = inc.get("incident_price")
    if px is not None and float(px) > 0:
        tags.append(f"px:{float(px):.10g}")
    return tags


def merge_incident_into_features(
    features: Optional[Dict[str, Any]], inc: Dict[str, Any]
) -> Dict[str, Any]:
    feats = dict(features or {})
    feats["incident"] = {
        "ts": inc.get("incident_ts"),
        "price": inc.get("incident_price"),
        "ref_price": inc.get("ref_price"),
        "event_id": inc.get("event_id"),
        "trade_key": inc.get("trade_key"),
        "drop_pct": inc.get("drop_pct"),
        "chart_tfs": inc.get("chart_tfs"),
        "chart_lookback_seconds": inc.get("chart_lookback_seconds"),
        "anchor": inc.get("anchor"),
    }
    # Flat keys for simple indexers
    if inc.get("incident_ts") is not None:
        feats["incident_ts"] = inc["incident_ts"]
    if inc.get("incident_price") is not None:
        feats["incident_price"] = inc["incident_price"]
    return feats


def enrich_lesson_row(row: dict) -> dict:
    """Add incident + base + bucket fields for API/UI from tags_json."""
    from .symbols import parse_incident_from_tags, learning_base
    from .buckets import normalize_bucket

    out = dict(row)
    tags = []
    raw = row.get("tags_json")
    if isinstance(raw, list):
        tags = raw
    elif isinstance(raw, str):
        try:
            import json

            tags = json.loads(raw or "[]")
        except Exception:
            tags = []
    inc = parse_incident_from_tags(tags)
    out["tags"] = tags
    out["incident_ts"] = inc.get("incident_ts") or row.get("created_at")
    out["incident_price"] = inc.get("incident_price")
    out["event_id"] = inc.get("event_id")
    if out["event_id"] is None:
        raw_ev = row.get("evidence_event_ids_json")
        evid = []
        if isinstance(raw_ev, list):
            evid = raw_ev
        elif isinstance(raw_ev, str):
            try:
                import json

                evid = json.loads(raw_ev or "[]")
            except Exception:
                evid = []
        if evid:
            try:
                out["event_id"] = int(evid[0])
            except (TypeError, ValueError):
                pass
    out["case_id"] = inc.get("case_id")
    out["bucket"] = normalize_bucket(inc.get("bucket"))
    out["symbol_norm"] = inc.get("symbol")
    out["base"] = inc.get("base") or (
        learning_base(inc["symbol"]) if inc.get("symbol") else None
    )
    out["market"] = inc.get("market")
    # Human time for desk
    try:
        ts = float(out["incident_ts"] or 0)
        if ts > 0:
            out["incident_iso"] = time.strftime(
                "%Y-%m-%d %H:%M", time.gmtime(ts)
            ) + " UTC"
    except Exception:
        pass
    return out
