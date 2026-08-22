"""Overview Book intel: always surface latest bad news (delist/scam/hack/…).

Not limited to 48h or current dump symbols — older items stay as reminders
(listing announcements often land 1–2 weeks before trade halt).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Classes we always treat as bad intel for the overview strip
BAD_CLASSES = frozenset(
    {
        "DELIST",
        "HACK",
        "SCAM",
        "CLOSURE",
        "HALT",
    }
)

# Title keywords when class is missing / soft rows from delist_cache
_BAD_TITLE = re.compile(
    r"\b("
    r"delist|delisting|delisted|"
    r"scam|rug\s?pull|honeypot|ponzi|exit\s?scam|"
    r"hack(ed|ing)?|exploit|drained|stolen|"
    r"closure|shut\s?down|wind[- ]?down|cease\s+operations|"
    r"suspend(ed|ing)?\s+trad|trading\s+suspension|"
    r"team\s+(disband|resign|quit)|abandon(ed|ing)|"
    r"will\s+remove|remov(e|ing)\s+(the\s+)?(trading\s+)?pair|"
    r"listing\s+announcement|to\s+list\b"  # listing as early risk signal
    r")\b",
    re.I,
)

# Drop junk delist_cache rows (hub index pages, garbage bases)
_JUNK_TITLE = re.compile(
    r"(delistings?\s+announcements?\s*\||latest\s+delisted\s+cryptos|"
    r"help\s+center|section/announcements)",
    re.I,
)
_JUNK_BASE = frozenset(
    {
        "LATEST",
        "DELISTED",
        "CRYPTOS",
        "HELP",
        "CENTER",
        "SINGAPORE",
        "DELISTINGS",
        "DELIST",
        "ON",
        "DEC",
        "SG",
        "FINANCIAL",
        "ANNOUNCEMENTS",
    }
)


def _fp_key(title: str, url: str = "", exchange: str = "") -> str:
    t = re.sub(r"\s+", " ", (title or "").strip().lower())[:160]
    u = (url or "").strip().lower()[:120]
    return f"{exchange}|{u}|{t}"


def _age_label(ts: Optional[float], now: Optional[float] = None) -> str:
    if ts is None:
        return ""
    try:
        age = max(0.0, float(now or time.time()) - float(ts))
    except (TypeError, ValueError):
        return ""
    if age < 3600:
        return f"{int(age // 60)}m ago"
    if age < 86400:
        return f"{age / 3600:.1f}h ago"
    days = age / 86400.0
    if days < 14:
        return f"{days:.1f}d ago"
    weeks = days / 7.0
    if weeks < 8:
        return f"{weeks:.1f}w ago"
    return f"{days / 30.0:.1f}mo ago"


def _is_bad_title(title: str) -> bool:
    return bool(_BAD_TITLE.search(title or ""))


def load_bad_intel_feed(
    fetch_all,
    *,
    limit: int = 5,
    book_bases: Optional[Set[str]] = None,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return up to `limit` most recent bad-intel items (any age).

    Book-only: watchlist ∪ targets ∪ positions. No global fill.
    """
    wall = float(now if now is not None else time.time())
    book_bases = {b.upper() for b in (book_bases or set()) if b and len(b) >= 2}
    items: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def _add(row: Dict[str, Any]) -> None:
        title = (row.get("title") or "").strip()
        if not title or _JUNK_TITLE.search(title):
            return
        key = _fp_key(title, str(row.get("url") or ""), str(row.get("source") or ""))
        if key in seen:
            return
        seen.add(key)
        ts = row.get("ts")
        try:
            ts_f = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_f = None
        sym = (row.get("symbol") or row.get("base") or "").upper()
        bases = row.get("bases") or []
        if isinstance(bases, str):
            bases = [b.strip() for b in bases.split(",") if b.strip()]
        book_hit = False
        if book_bases:
            if any(b.upper() in book_bases for b in bases if b):
                book_hit = True
            else:
                for b in book_bases:
                    if b and (
                        b == sym
                        or b in sym.split(",")
                        or re.search(rf"\b{re.escape(b)}\b", title.upper())
                    ):
                        book_hit = True
                        break
        items.append(
            {
                "id": row.get("id"),
                "symbol": sym or None,
                "bases": bases,
                "class": (row.get("class") or row.get("kind") or "INTEL").upper(),
                "severity": (row.get("severity") or "fatal").lower(),
                "title": title,
                "source": row.get("source") or row.get("exchange") or "",
                "url": row.get("url") or "",
                "ts": ts_f,
                "age_label": _age_label(ts_f, wall),
                "book_hit": book_hit,
                "origin": row.get("origin") or "news",
            }
        )

    # 1) Classified news_events (no time floor)
    try:
        news_rows = fetch_all(
            """
            SELECT id, symbol, class, severity, title, url, source, ts
            FROM news_events
            ORDER BY ts DESC
            LIMIT 80
            """
        )
        for r in news_rows:
            d = dict(r)
            cls = (d.get("class") or "").upper()
            title = d.get("title") or ""
            if cls not in BAD_CLASSES and not _is_bad_title(title):
                continue
            bases = []
            if d.get("symbol"):
                bases = [x.strip() for x in str(d["symbol"]).split(",") if x.strip()]
            d["bases"] = bases
            d["origin"] = "news_events"
            _add(d)
    except Exception as e:
        logger.debug("bad_intel news_events: %s", e)

    # 2) Multi-CEX delist radar cache (dedupe by title)
    try:
        delist_rows = fetch_all(
            """
            SELECT id, exchange, base, title, url, kind, ts, fingerprint
            FROM delist_cache
            ORDER BY ts DESC
            LIMIT 200
            """
        )
        # Group by title → one row, collect bases
        by_title: Dict[str, Dict[str, Any]] = {}
        for r in delist_rows:
            d = dict(r)
            title = (d.get("title") or "").strip()
            if not title or _JUNK_TITLE.search(title):
                continue
            base = (d.get("base") or "").upper()
            if base in _JUNK_BASE or len(base) < 2:
                base = ""
            tkey = title.lower()[:160]
            if tkey not in by_title:
                by_title[tkey] = {
                    "id": d.get("id"),
                    "title": title,
                    "url": d.get("url"),
                    "source": d.get("exchange") or "delist_cache",
                    "exchange": d.get("exchange"),
                    "class": "DELIST",
                    "kind": d.get("kind") or "delist",
                    "severity": "fatal",
                    "ts": d.get("ts"),
                    "bases": [],
                    "origin": "delist_cache",
                }
            if base and base not in by_title[tkey]["bases"]:
                by_title[tkey]["bases"].append(base)
            # keep newest ts
            try:
                if float(d.get("ts") or 0) > float(by_title[tkey].get("ts") or 0):
                    by_title[tkey]["ts"] = d.get("ts")
            except (TypeError, ValueError):
                pass
        for row in sorted(
            by_title.values(),
            key=lambda x: float(x.get("ts") or 0),
            reverse=True,
        ):
            if row.get("bases"):
                row["symbol"] = ",".join(row["bases"][:8])
            if not _is_bad_title(row.get("title") or "") and row.get("class") != "DELIST":
                continue
            _add(row)
    except Exception as e:
        logger.debug("bad_intel delist_cache: %s", e)

    # Book only — never fill with a global spam feed.
    if book_bases:
        items = [x for x in items if x.get("book_hit")]
    else:
        items = []
    items.sort(key=lambda x: -(float(x.get("ts") or 0)))
    return items[: max(1, int(limit))] if items else []
