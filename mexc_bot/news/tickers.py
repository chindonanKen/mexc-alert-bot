"""Extract crypto/stock ticker bases from delist announcement text."""

from __future__ import annotations

import re
from typing import List, Sequence

# Noise that is not a tradeable base in announcements
_STOP = {
    "THE", "AND", "FOR", "WILL", "SPOT", "PAIR", "PAIRS", "USDT", "USD", "BUSD",
    "PERPETUAL", "CONTRACT", "CONTRACTS", "FUTURES", "MARGIN", "TRADING", "NOTICE",
    "REMOVAL", "BINANCE", "OKX", "BYBIT", "MEXC", "UTC", "FROM", "WITH", "THAT",
    "THIS", "HTTP", "HTTPS", "WWW", "COM", "EN", "SUPPORT", "TOKEN", "COIN",
    "STOCK", "STOCKS", "GRID", "BOT", "BOTS", "OTHER", "OTHERS", "FOLLOWING",
    "IMPORTANT", "NOTES", "PLEASE", "TIME", "ZONE", "INNOVATION", "MARKET",
    "FIRST", "LIST", "LISTING", "AUG", "JUL", "JUN", "MAY", "APR", "MAR",
    "JAN", "FEB", "SEP", "OCT", "NOV", "DEC", "CONVERT", "DELIST", "DELISTING",
    "DELISTINGS", "REMOVE", "REMOVING", "SUSPEND", "SUSPENDED", "CEASE",
    "TO", "OF", "ON", "IN", "AT", "AS", "OR", "BY", "BE", "IS", "ARE", "WAS",
    "OUR", "ALL", "NEW", "NOW", "VIA", "API", "WEB", "APP", "USDTM", "USD-M",
}


def extract_delist_bases(*texts: str, limit: int = 40) -> List[str]:
    """Pull ordered unique bases from announcement title/body/meta."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return []
    found: List[str] = []
    seen = set()

    def _add(tok: str) -> None:
        t = re.sub(r"(?:STOCK)?_?USDT$", "", tok.upper().strip(" ,.;:/"))
        t = t.strip("_-")
        if not t or len(t) < 2 or len(t) > 15:
            return
        if t in _STOP or t.isdigit() or t in seen:
            return
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,14}", t):
            return
        seen.add(t)
        found.append(t)

    upper = blob.upper()

    # 1) Parenthetical (ACX)
    for m in re.findall(r"\(([A-Z]{2,12})\)", upper):
        _add(m)

    # 2) Explicit lists in bold-ish contexts: "UPST, AFRM, AKAM, HBM"
    for m in re.findall(
        r"\b([A-Z]{2,15}(?:\s*,\s*[A-Z]{2,15}){1,30})\b",
        upper,
    ):
        for part in re.split(r"\s*,\s*", m):
            _add(part)

    # 3) BASEUSDT / BASESTOCK_USDT / BASE/USDT
    for m in re.findall(r"\b([A-Z]{2,12})(?:STOCK)?(?:_USDT|/USDT|USDT)\b", upper):
        _add(m)

    # 4) "Delist A, B and C" / "Delisting of A, B and C"
    m = re.search(
        r"(?:TO\s+)?DELIST(?:ING)?(?:\s+OF)?\s+([A-Z0-9_,\s]+?)(?:\s+AND\s+(\d+)\s+OTHER)?",
        upper,
    )
    if m:
        chunk = m.group(1)
        for part in re.split(r"[\s,/]+", chunk):
            _add(part)
        # "and N other" alone doesn't give names — body must supply rest

    # 5) "and IONS" trailing single after list
    for m in re.findall(r"\bAND\s+([A-Z]{2,12})\b", upper):
        _add(m)

    return found[:limit]


def bases_display(bases: Sequence[str]) -> str:
    return ", ".join(bases) if bases else "—"
