"""Book membership for news: watchlist ∪ targets ∪ positions. No prices."""

from __future__ import annotations

import re
from typing import Iterable, Optional, Set


def normalize_news_base(symbol: str) -> str:
    s = (symbol or "").upper().replace("-", "").replace(" ", "")
    s = s.replace("_USDT", "").replace("_USD", "")
    if s.endswith("USDT") and len(s) > 4:
        s = s[:-4]
    elif s.endswith("USD") and len(s) > 3:
        s = s[:-3]
    s = s.replace("STOCK", "").replace("_", "").strip()
    return s


def collect_bases(*parts: str) -> Set[str]:
    out: Set[str] = set()
    for raw in parts:
        if not raw:
            continue
        for tok in re.split(r"[,/;|\s]+", str(raw).upper()):
            b = normalize_news_base(tok)
            if len(b) >= 2:
                out.add(b)
    return out


def news_touches_book(
    *,
    symbol: Optional[str] = None,
    title: str = "",
    bases: Optional[Iterable[str]] = None,
    book_bases: Optional[Iterable[str]] = None,
    book_syms: Optional[Iterable[str]] = None,
) -> bool:
    """True only if this headline names a watch / target / position base."""
    book = {normalize_news_base(b) for b in (book_bases or []) if b}
    book |= {normalize_news_base(s) for s in (book_syms or []) if s}
    book = {b for b in book if len(b) >= 2}
    if not book:
        return False
    found = set(collect_bases(symbol or ""))
    for b in bases or []:
        found |= collect_bases(str(b))
    if found & book:
        return True
    title_u = (title or "").upper()
    if not title_u:
        return False
    for b in book:
        if re.search(rf"(?<![A-Z0-9]){re.escape(b)}(?![A-Z0-9])", title_u):
            return True
    return False
