"""Position enrichment: full fill history → correct size/avg entry + mark."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from . import db
from .prices import ticker_24h

logger = logging.getLogger(__name__)


def enrich_positions(rows: List[dict], user_id: int) -> List[dict]:
    """Rebuild each open position from full historic fills + live mark."""
    if not rows:
        return []
    try:
        from ..learning.store import EventStore
        from ..learning.trades import reconstruct_open_from_fills

        store = EventStore(db.db_path())
        fills_all = store.recent_fills(user_id, limit=500)
    except Exception as e:
        logger.debug("enrich fills: %s", e)
        store = None
        fills_all = []
        reconstruct_open_from_fills = None  # type: ignore

    now = time.time()
    out: List[dict] = []
    for p in rows:
        d = dict(p)
        sym = str(d.get("symbol") or "")
        mkt = str(d.get("market") or "spot")
        recon = None
        if reconstruct_open_from_fills and fills_all:
            try:
                recon = reconstruct_open_from_fills(
                    fills_all, symbol=sym, market=mkt
                )
            except Exception as e:
                logger.debug("recon %s: %s", sym, e)

        if recon and (recon.get("n_buys") or recon.get("n_sells")):
            d["buy_orders"] = recon["buy_orders"]
            d["sell_orders"] = recon["sell_orders"]
            d["n_buys"] = recon["n_buys"]
            d["n_sells"] = recon["n_sells"]
            d["size_qty"] = recon.get("total_bought_qty")
            d["size_sold"] = recon.get("total_sold_qty")
            d["size_remaining"] = recon.get("size_remaining")
            d["entry_avg_fills"] = recon.get("entry_avg")
            d["entry_display"] = recon.get("entry_avg") or d.get("entry_avg")
            d["first_open_ts"] = recon.get("first_open_ts")
            d["last_fill_ts"] = recon.get("last_fill_ts")
            # Prefer reconstructed avg over stale journal
            if recon.get("entry_avg") is not None:
                d["entry_avg"] = recon["entry_avg"]
            if recon.get("first_open_ts"):
                d["opened_at"] = recon["first_open_ts"]
            d["recon_from_fills"] = True
        else:
            d["buy_orders"] = []
            d["sell_orders"] = []
            d["n_buys"] = 0
            d["n_sells"] = 0
            d["size_remaining"] = None
            d["entry_display"] = d.get("entry_avg")
            d["recon_from_fills"] = False

        opened = float(d.get("opened_at") or 0) or None
        if opened:
            d["hold_seconds"] = max(0.0, now - opened)
            d["hold_hours"] = round(d["hold_seconds"] / 3600.0, 2)
        else:
            d["hold_hours"] = None

        entry = d.get("entry_display") or d.get("entry_avg")
        mark = None
        chg = None
        try:
            t = ticker_24h(sym)
            if t:
                mark = t.get("price")
                chg = t.get("changePercent")
                d["mark_source"] = t.get("source")
        except Exception:
            pass
        d["mark_price"] = mark
        d["change_24h_pct"] = chg
        if mark is not None and entry is not None and float(entry) > 0:
            d["upnl_pct"] = round(
                (float(mark) - float(entry)) / float(entry) * 100.0, 3
            )
            rem = d.get("size_remaining")
            if rem:
                d["upnl_usd_est"] = round(
                    (float(mark) - float(entry)) * float(rem), 4
                )
            else:
                d["upnl_usd_est"] = None
        else:
            d["upnl_pct"] = None
            d["upnl_usd_est"] = None
        out.append(d)
    return out


def positions_by_symbol(positions: List[dict]) -> Dict[str, dict]:
    """Map compact symbol key → position snapshot for overview."""
    by: Dict[str, dict] = {}
    for p in positions:
        s = (p.get("symbol") or "").upper().replace("_", "")
        if s:
            by[s] = p
    return by
