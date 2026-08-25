"""Canonical symbols for learning tags + setup cases.

One coin must not split across HFTUSDT / HFT_USDT / HFT.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Bare aliases seen in teaches / watchlists
_BASE_ALIASES = {
    "HFI": "HFT",
    "TESLA": "TSLA",
    "BANANA": "BANANAS31",
    "BANANAS": "BANANAS31",
}


def _strip_noise(raw: str) -> str:
    s = (raw or "").strip().upper()
    s = s.replace("-", "_").replace("/", "_").replace(" ", "")
    # drop accidental prefixes
    if s.startswith("SYM:"):
        s = s[4:]
    return s


def learning_base(symbol: str) -> str:
    """Market-agnostic base for matching (HFT, SYN, AXTISTOCK, BANANAS31)."""
    s = _strip_noise(symbol)
    if not s:
        return ""
    if "_" in s:
        parts = [p for p in s.split("_") if p]
        if parts and parts[-1] in ("USDT", "USDC", "USD"):
            parts = parts[:-1]
        base = "_".join(parts) if parts else s
    else:
        base = s
        for q in ("USDT", "USDC", "USD"):
            if base.endswith(q) and len(base) > len(q):
                base = base[: -len(q)]
                break
    base = _BASE_ALIASES.get(base, base)
    # compact stock form TSLASTOCK already fine
    return base


def same_book_name(left: str, right: str) -> bool:
    """AXTI matches AXTISTOCK_USDT. Used to bind a locked visual_ad to a decide."""
    a = learning_base(left)
    b = learning_base(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if a + "STOCK" == b or b + "STOCK" == a:
        return True
    if a.endswith("STOCK") and a[: -len("STOCK")] == b:
        return True
    if b.endswith("STOCK") and b[: -len("STOCK")] == a:
        return True
    return False


def normalize_learning_symbol(symbol: str, market: Optional[str] = None) -> str:
    """Canonical symbol for storage on a given market.

    spot → compact BASEUSDT (HFTUSDT)
    futures → BASE_USDT (HFT_USDT); keeps *STOCK* multi-part bases
    unknown market → prefer underscore form if input had one, else compact USDT
    """
    s = _strip_noise(symbol)
    if not s:
        return s
    mkt = (market or "").lower().strip()
    base = learning_base(s)
    if not base:
        return s

    if mkt == "spot":
        # Spot pairs on MEXC are usually compact
        if base.endswith("STOCK") or "STOCK" in base:
            # rare spot stock-style — keep base + USDT compact
            return f"{base.replace('_', '')}USDT"
        return f"{base.replace('_', '')}USDT"

    if mkt == "futures":
        # Preserve multi-token bases (AXTISTOCK, 1000RATS)
        return f"{base}_USDT"

    # No market: if original looked futures-like, keep underscore
    if "_" in s or s.endswith("_USDT"):
        return f"{base}_USDT"
    if s.endswith("USDT") or s.endswith("USDC"):
        return f"{base.replace('_', '')}USDT"
    return f"{base}_USDT"


def symbol_aliases(symbol: str, market: Optional[str] = None) -> Tuple[str, ...]:
    """Forms that should match the same coin (for queries / merge)."""
    base = learning_base(symbol)
    if not base:
        return tuple()
    compact = f"{base.replace('_', '')}USDT"
    fut = f"{base}_USDT"
    bare = base
    out = []
    for x in (normalize_learning_symbol(symbol, market), compact, fut, bare, symbol.upper()):
        x = _strip_noise(x)
        if x and x not in out:
            out.append(x)
    return tuple(out)


def rewrite_sym_tags(tags: list, market: Optional[str] = None) -> list:
    """Normalize sym: tags; add base: for cross-market match. Preserve other tags."""
    out = []
    seen = set()
    mkt = market
    if mkt is None:
        for t in tags or []:
            if str(t).lower().startswith("mkt:"):
                mkt = str(t).split(":", 1)[-1].lower()
                break
    base_val = None
    canon = None
    for t in tags or []:
        ts = str(t or "").strip()
        if not ts:
            continue
        low = ts.lower()
        if low.startswith("sym:"):
            raw = ts.split(":", 1)[1]
            canon = normalize_learning_symbol(raw, mkt)
            base_val = learning_base(raw)
            key = f"sym:{canon}"
            if key not in seen:
                out.append(key)
                seen.add(key)
            continue
        if low.startswith("base:"):
            continue  # re-add once below
        if ts not in seen:
            out.append(ts)
            seen.add(ts)
    if base_val:
        bk = f"base:{base_val}"
        if bk not in seen:
            out.append(bk)
            seen.add(bk)
    return out


_TS_RE = re.compile(r"^ts:([0-9]+(?:\.[0-9]+)?)$", re.I)
_PX_RE = re.compile(r"^px:([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)$", re.I)


def parse_incident_from_tags(tags: list) -> dict:
    """Extract incident ts/price from structured tags."""
    out: dict = {}
    for t in tags or []:
        ts = str(t)
        m = _TS_RE.match(ts)
        if m:
            try:
                out["incident_ts"] = float(m.group(1))
            except ValueError:
                pass
            continue
        m = _PX_RE.match(ts)
        if m:
            try:
                out["incident_price"] = float(m.group(1))
            except ValueError:
                pass
            continue
        low = ts.lower()
        if low.startswith("ev:"):
            try:
                out["event_id"] = int(ts.split(":", 1)[1])
            except ValueError:
                pass
        elif low.startswith("case:"):
            try:
                out["case_id"] = int(ts.split(":", 1)[1])
            except ValueError:
                pass
        elif low.startswith("bucket:"):
            out["bucket"] = ts.split(":", 1)[1].lower()
        elif low.startswith("sym:"):
            out["symbol"] = ts.split(":", 1)[1]
        elif low.startswith("base:"):
            out["base"] = ts.split(":", 1)[1]
        elif low.startswith("mkt:"):
            out["market"] = ts.split(":", 1)[1].lower()
    return out
