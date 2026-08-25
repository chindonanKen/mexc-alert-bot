"""Pre-hang AD from locked teaches, Learning visual AD (read-only), named klines.

Never invent ticks. If a number cannot be cited to a locked teach, a staff
visual_ad, or an official MEXC named bar, the AD is unknown.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from .logic import price_eq
from .settings import LOCKED_TEACHES, MANILA_TZ

_MANILA = ZoneInfo(MANILA_TZ)


def manila_label(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    if t > 1e12:
        t /= 1000.0
    if t <= 0:
        return None
    dt = datetime.fromtimestamp(t, tz=_MANILA)
    return dt.strftime("%Y-%m-%d %H:%M PHT")


def match_named_bar(
    bars: Sequence[Dict[str, Any]],
    price: Optional[float],
    *,
    side: str,
) -> Optional[Dict[str, Any]]:
    """Find an official kline whose high (top) or low (bottom) is this tick."""
    if price is None:
        return None
    field = "h" if side == "top" else "l"
    alt = "high" if side == "top" else "low"
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        px = bar.get(field)
        if px is None:
            px = bar.get(alt)
        if not price_eq(px, price):
            continue
        ts = bar.get("ts") or bar.get("t") or bar.get("time")
        try:
            ts_f = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_f = None
        return {
            "ts": ts_f,
            "label": manila_label(ts_f),
            "o": bar.get("o") if bar.get("o") is not None else bar.get("open"),
            "h": bar.get("h") if bar.get("h") is not None else bar.get("high"),
            "l": bar.get("l") if bar.get("l") is not None else bar.get("low"),
            "c": bar.get("c") if bar.get("c") is not None else bar.get("close"),
        }
    return None


def read_learning_visual_ad(
    db_path: Path,
    symbol: str,
    market: str,
) -> Optional[Dict[str, Any]]:
    """READ-only staff visual AD. Never writes learning / cases."""
    p = Path(db_path)
    if str(p).endswith(".json"):
        p = p.with_suffix(".db")
    if not p.exists():
        return None
    try:
        from ..learning.visual_ad import extract_visual_ad, parse_features_json
    except Exception:
        return None
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_setup_cases'"
        ).fetchone()
        if not row:
            return None
        rows = conn.execute(
            """
            SELECT features_json FROM agent_setup_cases
            WHERE UPPER(symbol)=? AND LOWER(market)=?
            ORDER BY id DESC LIMIT 12
            """,
            (str(symbol).upper(), str(market).lower()),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    for r in rows:
        vad = extract_visual_ad(parse_features_json(r["features_json"]))
        if not vad:
            continue
        high, low = vad.get("high"), vad.get("low")
        if high is None or low is None:
            continue
        try:
            top, bot = float(high), float(low)
        except (TypeError, ValueError):
            continue
        if top <= bot:
            continue
        return {
            "ad_top": top,
            "ad_bottom": bot,
            "tf": vad.get("tf"),
            "source": "visual_ad",
            "note": vad.get("note") or "Learning staff visual AD",
        }
    return None


def hang_ad(
    symbol: str,
    market: str,
    *,
    db_path: Optional[Path] = None,
    klines_by_tf: Optional[Dict[str, Sequence[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Pre-hang one name. Locked teach > staff visual AD > named-bar confirm."""
    key = (str(symbol).upper(), str(market).lower())
    locked = LOCKED_TEACHES.get(key)
    visual = None
    if db_path is not None:
        visual = read_learning_visual_ad(db_path, symbol, market)

    base: Dict[str, Any]
    if locked:
        base = {
            "ad_top": locked.get("ad_top"),
            "ad_bottom": locked.get("ad_bottom"),
            "tf": locked.get("tf"),
            "ad_source": "locked_teach",
            "ad_note": locked.get("note"),
            "zones": list(locked.get("zones") or []),
            "initial_drop_top": locked.get("initial_drop_top"),
            "initial_drop_bottom": locked.get("initial_drop_bottom"),
        }
    elif visual:
        base = {
            "ad_top": visual.get("ad_top"),
            "ad_bottom": visual.get("ad_bottom"),
            "tf": visual.get("tf"),
            "ad_source": "visual_ad",
            "ad_note": visual.get("note"),
            "zones": [],
            "initial_drop_top": None,
            "initial_drop_bottom": None,
        }
    else:
        return {
            "ad_top": None,
            "ad_bottom": None,
            "ad_status": "unknown",
            "ad_source": None,
            "ad_note": "unknown — no locked teach, visual AD, or named bar",
            "tf": None,
            "zones": [],
            "bar_top_ts": None,
            "bar_bottom_ts": None,
            "bar_top_label": None,
            "bar_bottom_label": None,
            "initial_drop_top": None,
            "initial_drop_bottom": None,
        }

    top_bar = None
    bot_bar = None
    matched_tf = None
    for tf, bars in (klines_by_tf or {}).items():
        if not bars:
            continue
        t = match_named_bar(bars, base.get("ad_top"), side="top")
        b = match_named_bar(bars, base.get("ad_bottom"), side="bottom")
        if t or b:
            if t:
                top_bar = t
            if b:
                bot_bar = b
            matched_tf = tf
            break

    if locked and klines_by_tf and not top_bar and not bot_bar:
        # Prices stay locked; bars stay unknown. Do not invent a kline tick.
        pass

    tf = base.get("tf") or matched_tf
    return {
        "ad_top": base.get("ad_top"),
        "ad_bottom": base.get("ad_bottom"),
        "ad_status": "known",
        "ad_source": base.get("ad_source"),
        "ad_note": base.get("ad_note"),
        "tf": tf,
        "zones": base.get("zones") or [],
        "bar_top_ts": (top_bar or {}).get("ts"),
        "bar_bottom_ts": (bot_bar or {}).get("ts"),
        "bar_top_label": (top_bar or {}).get("label"),
        "bar_bottom_label": (bot_bar or {}).get("label"),
        "initial_drop_top": base.get("initial_drop_top"),
        "initial_drop_bottom": base.get("initial_drop_bottom"),
    }


def official_volume_n(bars: Optional[Iterable[Dict[str, Any]]]) -> Optional[float]:
    """Last-bar or last-known official quote volume. Never invent a count."""
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    for bar in reversed(seq):
        raw = None
        for key in ("v", "volume", "q", "quote_volume", "quoteVolume"):
            if bar.get(key) is not None:
                raw = bar.get(key)
                break
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def volume_label(bars: Optional[Iterable[Dict[str, Any]]]) -> str:
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if len(seq) < 8:
        return "unknown"
    vols = []
    for b in seq:
        try:
            vols.append(float(b.get("v") if b.get("v") is not None else b.get("volume") or 0))
        except (TypeError, ValueError):
            vols.append(0.0)
    if not any(vols):
        return "unknown"
    base = vols[:-3] or vols
    avg = sum(base) / len(base)
    if avg <= 0:
        return "unknown"
    last = vols[-1]
    ratio = last / avg
    if ratio >= 2.0:
        return "climax"
    if ratio >= 1.2:
        return "elevated"
    if ratio < 0.7:
        return "dry"
    return "normal"
