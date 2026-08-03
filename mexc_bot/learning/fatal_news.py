"""Ticker-specific fatal news for agent judgment.

Only brutal classes (delist/hack/scam/closure) on the matching symbol affect calls.
Generic market FUD / non-fatal intel does NOT flip verdicts.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

FATAL_CLASSES = frozenset({"DELIST", "HACK", "SCAM", "CLOSURE"})
# Prefer confirmed fatal; unconfirmed still surfaces but is softer
SEVERITY_HARD = frozenset({"fatal"})
SEVERITY_SOFT = frozenset({"unconfirmed", "fatal"})


def _norm_base(symbol: str) -> str:
    s = (symbol or "").upper().replace("_", "").replace("-", "")
    for suf in ("USDT", "USD", "STOCK"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    return s


def _symbol_matches_news(symbol: str, news_symbol: Optional[str], title: str) -> bool:
    """Exact base match only (ETH ≠ ETHFI). Token match in title for same base."""
    base = _norm_base(symbol)
    if not base or len(base) < 2:
        return False
    ns = _norm_base(news_symbol or "")
    if ns and ns == base:
        return True
    # whole-token match in title — exact base only
    title_u = (title or "").upper()
    return bool(re.search(rf"\b{re.escape(base)}\b", title_u))


def lookup_fatal_for_ticker(
    db_path_or_store,
    symbol: str,
    *,
    lookback_seconds: float = 72 * 3600,
    now: Optional[float] = None,
    include_unconfirmed: bool = True,
) -> Dict[str, Any]:
    """Query news_events for fatal-class hits on this ticker only.

    Accepts EventStore (has .db_path) or path-like.
    """
    wall = float(now if now is not None else time.time())
    since = wall - lookback_seconds
    path = getattr(db_path_or_store, "db_path", None) or db_path_or_store
    hits: List[dict] = []
    try:
        import sqlite3
        from pathlib import Path

        p = Path(str(path))
        if not p.exists():
            return {"fatal": False, "hits": [], "hard_fatal": False}
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        try:
            # table may not exist
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_events'"
            ).fetchone()
            if not row:
                return {"fatal": False, "hits": [], "hard_fatal": False}
            rows = conn.execute(
                """
                SELECT id, symbol, class, severity, title, source, ts, url
                FROM news_events
                WHERE ts >= ?
                  AND UPPER(class) IN ('DELIST','HACK','SCAM','CLOSURE')
                ORDER BY ts DESC
                LIMIT 80
                """,
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.debug("lookup_fatal_for_ticker: %s", e)
        return {"fatal": False, "hits": [], "hard_fatal": False, "error": str(e)[:120]}

    for r in rows:
        d = dict(r)
        cls = (d.get("class") or "").upper()
        sev = (d.get("severity") or "").lower()
        if cls not in FATAL_CLASSES:
            continue
        if sev not in SEVERITY_SOFT:
            continue
        if not include_unconfirmed and sev not in SEVERITY_HARD:
            continue
        if not _symbol_matches_news(symbol, d.get("symbol"), d.get("title") or ""):
            continue
        hits.append(
            {
                "id": d.get("id"),
                "class": cls,
                "severity": sev,
                "title": d.get("title"),
                "source": d.get("source"),
                "ts": d.get("ts"),
                "url": d.get("url"),
                "symbol": d.get("symbol"),
            }
        )

    hard = [h for h in hits if h.get("severity") == "fatal"]
    soft = [h for h in hits if h.get("severity") != "fatal"]
    return {
        "fatal": bool(hits),
        "hard_fatal": bool(hard),
        "hits": hits[:5],
        "primary": (hard or soft or [None])[0],
        "count": len(hits),
    }


def apply_fatal_to_verdict(
    verdict: str,
    size_hint: str,
    fatal_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Force no_trade on hard fatal; strongly bias unconfirmed."""
    if not fatal_info.get("fatal"):
        return {
            "verdict": verdict,
            "size_hint": size_hint,
            "overridden": False,
            "note": None,
        }
    primary = fatal_info.get("primary") or {}
    cls = primary.get("class") or "FATAL"
    title = (primary.get("title") or "")[:100]
    if fatal_info.get("hard_fatal"):
        return {
            "verdict": "no_trade",
            "size_hint": "none",
            "overridden": True,
            "note": f"HARD FATAL {cls}: {title}",
            "force": "hard",
        }
    # unconfirmed: still no full layers; max scout only if already taking
    if verdict in ("take_layers", "take_scout"):
        return {
            "verdict": "no_trade",
            "size_hint": "none",
            "overridden": True,
            "note": f"UNCONFIRMED FATAL-CLASS {cls}: {title} — treat as no-trade until cleared",
            "force": "soft",
        }
    return {
        "verdict": verdict,
        "size_hint": size_hint,
        "overridden": False,
        "note": f"Unconfirmed fatal-class noise nearby: {cls}",
        "force": "soft_note",
    }
