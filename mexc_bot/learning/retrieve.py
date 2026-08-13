"""P1 nearest-case retrieve — index only, not advice.

Score: same base > symbol, bucket, band, dd, regime, TF overlap.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .buckets import normalize_bucket
from .symbols import learning_base, normalize_learning_symbol


def _feats(row: dict) -> Dict[str, Any]:
    raw = row.get("features_json") or row.get("features") or {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _tfs_ready(feats: Dict[str, Any]) -> set:
    out = set()
    for p in feats.get("ad_by_tf") or []:
        if isinstance(p, dict) and (p.get("ad_ready") or p.get("tf")):
            if p.get("tf"):
                out.add(str(p["tf"]).lower())
    return out


def score_case(
    query: Dict[str, Any],
    candidate: Dict[str, Any],
) -> float:
    """Higher is closer. Query/candidate are public case views or store rows."""
    qf = query.get("features") if isinstance(query.get("features"), dict) else _feats(query)
    cf = candidate.get("features") if isinstance(candidate.get("features"), dict) else _feats(candidate)
    score = 0.0

    q_sym = normalize_learning_symbol(
        str(query.get("symbol") or ""), str(query.get("market") or "futures")
    )
    c_sym = normalize_learning_symbol(
        str(candidate.get("symbol") or ""), str(candidate.get("market") or "futures")
    )
    q_base = query.get("base") or learning_base(q_sym)
    c_base = candidate.get("base") or learning_base(c_sym)
    if q_sym and q_sym == c_sym:
        score += 4.0
    elif q_base and q_base == c_base:
        score += 3.0

    qb = normalize_bucket(query.get("bucket") or qf.get("bucket"))
    cb = normalize_bucket(candidate.get("bucket") or cf.get("bucket"))
    if qb and cb and qb == cb:
        score += 2.0

    qband = (query.get("band") or query.get("velocity_band") or qf.get("band") or "").upper()
    cband = (
        candidate.get("band") or candidate.get("velocity_band") or cf.get("band") or ""
    ).upper()
    if qband and qband == cband:
        score += 1.0

    try:
        qd = float(query.get("dd_pct") if query.get("dd_pct") is not None else qf.get("dd_pct") or 0)
        cd = float(
            candidate.get("dd_pct")
            if candidate.get("dd_pct") is not None
            else cf.get("dd_pct") or 0
        )
        if qd > 0 and cd > 0:
            rel = abs(qd - cd) / max(qd, cd)
            score += max(0.0, 1.5 * (1.0 - min(rel, 1.0)))
    except (TypeError, ValueError):
        pass

    qr = (query.get("regime_guess") or qf.get("regime_taught") or qf.get("regime_guess") or "").lower()
    cr = (
        candidate.get("regime_guess")
        or cf.get("regime_taught")
        or cf.get("regime_guess")
        or ""
    ).lower()
    if qr and cr and qr == cr and qr != "unknown":
        score += 1.5

    overlap = _tfs_ready(qf) & _tfs_ready(cf)
    if overlap:
        score += 0.4 * min(len(overlap), 3)

    return round(score, 4)


def similar_cases(
    cases: List[Dict[str, Any]],
    query: Dict[str, Any],
    *,
    k: int = 5,
    exclude_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return top-k neighbors with scores. No advice text."""
    ranked = []
    qid = exclude_id if exclude_id is not None else query.get("id")
    for row in cases:
        rid = row.get("id")
        if qid is not None and rid == qid:
            continue
        sc = score_case(query, row)
        if sc <= 0:
            continue
        ranked.append((sc, row))
    ranked.sort(key=lambda x: -x[0])
    out = []
    for sc, row in ranked[: max(1, int(k))]:
        item = {
            "id": row.get("id"),
            "symbol": row.get("symbol"),
            "market": row.get("market"),
            "bucket": row.get("bucket"),
            "band": row.get("band") or row.get("velocity_band"),
            "dd_pct": row.get("dd_pct"),
            "regime_guess": row.get("regime_guess"),
            "tf_hint": row.get("tf_hint"),
            "score": sc,
        }
        out.append(item)
    return out
