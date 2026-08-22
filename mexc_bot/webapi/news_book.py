"""Desk book universe for news: alerts + watchlist + positions."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from ..news.book import news_touches_book, normalize_news_base
from . import actions, db


def desk_book_sets(user_id: Optional[int] = None) -> Dict[str, Set[str]]:
    uid = user_id if user_id is not None else db.default_user_id()
    syms: Set[str] = set()
    bases: Set[str] = set()

    def _add(raw: str) -> None:
        s = (raw or "").upper().strip()
        if not s:
            return
        syms.add(s)
        b = normalize_news_base(s)
        if len(b) >= 2:
            bases.add(b)

    if not uid:
        return {"syms": syms, "bases": bases}
    try:
        for a in actions.list_alerts(int(uid)):
            _add(a.get("symbol") or "")
    except Exception:
        pass
    try:
        for w in actions.list_watchlist(int(uid)):
            _add(w.get("symbol") or "")
    except Exception:
        pass
    try:
        for p in actions.list_positions(int(uid)):
            _add(p.get("symbol") or "")
    except Exception:
        pass
    return {"syms": syms, "bases": bases}


def filter_rows_to_book(
    rows: Iterable[dict],
    *,
    book_bases: Iterable[str],
    book_syms: Optional[Iterable[str]] = None,
    symbol_keys: Iterable[str] = ("symbol", "base"),
) -> List[dict]:
    out: List[dict] = []
    book_b = set(book_bases or [])
    book_s = set(book_syms or [])
    if not book_b and not book_s:
        return out
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        bases = row.get("bases") or []
        if isinstance(bases, str):
            bases = [x.strip() for x in bases.split(",") if x.strip()]
        sym = ""
        for k in symbol_keys:
            if row.get(k):
                sym = str(row.get(k) or "")
                break
        title = str(row.get("title") or "")
        if news_touches_book(
            symbol=sym,
            title=title,
            bases=bases,
            book_bases=book_b,
            book_syms=book_s,
        ):
            row["book_hit"] = True
            out.append(row)
    return out
