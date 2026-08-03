"""Position entities from segmented fill history + live marks."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Set, Tuple

from . import db
from .prices import ticker_24h

logger = logging.getLogger(__name__)


def list_position_entities(
    user_id: int,
    *,
    include_closed: bool = True,
    closed_limit: int = 40,
) -> List[dict]:
    """Discrete positions (open + closed cycles).

    Order for the desk: **all opens first** (newest open), then closed
    (newest closed). Each full flat is its own entity with success/miss.
    Journal-only opens (no fills yet) are merged in.
    """
    try:
        from ..learning.store import EventStore
        from ..learning.trades import segment_positions_from_fills

        store = EventStore(db.db_path())
        fills_all = store.recent_fills(user_id, limit=1500)
    except Exception as e:
        logger.debug("list_position_entities fills: %s", e)
        return _fallback_journal(user_id, include_closed=include_closed)

    pairs: Set[Tuple[str, str]] = set()
    for f in fills_all:
        if not f.get("symbol"):
            continue
        mkt = (f.get("market") or "spot").lower()
        if mkt not in ("spot", "futures"):
            mkt = "spot"
        pairs.add((str(f["symbol"]).upper(), mkt))
    try:
        for r in db.fetch_all(
            "SELECT symbol, market FROM journal_trades WHERE user_id=?",
            (user_id,),
        ):
            if r.get("symbol"):
                mkt = (r.get("market") or "spot").lower()
                if mkt not in ("spot", "futures"):
                    mkt = "spot"
                pairs.add((str(r["symbol"]).upper(), mkt))
    except Exception:
        pass

    entities: List[dict] = []
    for sym, mkt in pairs:
        segs = segment_positions_from_fills(fills_all, symbol=sym, market=mkt)
        for s in segs:
            if not include_closed and s.get("status") != "open":
                continue
            entities.append(s)

    # Futures exchange open positions override residual (holdVol is truth)
    try:
        from ..learning.fills import read_futures_open_cache

        fut_opens = read_futures_open_cache(store, user_id)
    except Exception:
        fut_opens = []
    for fo in fut_opens:
        fsym = str(fo.get("symbol") or "").upper()
        hold = float(fo.get("hold_vol") or 0)
        if not fsym or hold <= 0:
            continue
        # match open futures entity
        matched = False
        for e in entities:
            if e.get("status") != "open":
                continue
            if (e.get("market") or "").lower() != "futures":
                continue
            es = str(e.get("symbol") or "").upper().replace("_", "")
            if es == fsym.replace("_", ""):
                e["size_remaining"] = hold
                if fo.get("entry_avg") is not None:
                    e["entry_avg"] = fo["entry_avg"]
                    e["entry_display"] = fo["entry_avg"]
                e["exchange_hold"] = True
                e["leverage"] = fo.get("leverage")
                e["realized_on_pos"] = fo.get("realized")
                matched = True
                break
        if not matched:
            # open on exchange but no fill cycle yet
            entities.append(
                {
                    "symbol": fsym,
                    "market": "futures",
                    "status": "open",
                    "outcome": "open",
                    "is_open": True,
                    "opened_at": None,
                    "closed_at": None,
                    "entry_avg": fo.get("entry_avg"),
                    "entry_display": fo.get("entry_avg"),
                    "exit_avg": None,
                    "size_remaining": hold,
                    "size_qty": hold,
                    "size_sold": 0,
                    "buy_orders": [],
                    "sell_orders": [],
                    "n_buys": 0,
                    "n_sells": 0,
                    "recon_from_fills": False,
                    "exchange_hold": True,
                    "leverage": fo.get("leverage"),
                    "notes": "open on MEXC futures (awaiting deal sync)",
                }
            )

    # Journal opens with no fill inventory still need to show (manual log / test)
    open_keys = {
        (
            str(e.get("symbol") or "").upper().replace("_", ""),
            (e.get("market") or "spot").lower(),
        )
        for e in entities
        if e.get("status") == "open"
    }
    try:
        jrows = db.fetch_all(
            "SELECT * FROM journal_trades WHERE user_id=? AND status='open' "
            "ORDER BY opened_at DESC",
            (user_id,),
        )
    except Exception:
        jrows = []
    for j in jrows:
        key = (
            str(j.get("symbol") or "").upper().replace("_", ""),
            (j.get("market") or "spot").lower(),
        )
        if key in open_keys:
            # attach journal id onto matching fill open if same symbol
            for e in entities:
                if e.get("status") != "open":
                    continue
                ek = (
                    str(e.get("symbol") or "").upper().replace("_", ""),
                    (e.get("market") or "spot").lower(),
                )
                if ek == key and e.get("journal_id") is None:
                    e["journal_id"] = j.get("id")
                    if j.get("notes") and not e.get("notes"):
                        e["notes"] = j.get("notes")
            continue
        d = _fallback_from_rows([j])[0]
        d["journal_id"] = j.get("id")
        d["id"] = j.get("id")
        entities.append(d)
        open_keys.add(key)

    if include_closed:
        # journal closed without fills (manual only). Never mix auto journal rows
        # with fill-recon for the same symbol — those are often wrong timestamps/PnL.
        fill_recon_syms = {
            str(e.get("symbol") or "").upper().replace("_", "")
            for e in entities
            if e.get("recon_from_fills")
        }
        try:
            jc = db.fetch_all(
                "SELECT * FROM journal_trades WHERE user_id=? AND status='closed' "
                "ORDER BY closed_at DESC LIMIT ?",
                (user_id, closed_limit),
            )
        except Exception:
            jc = []
        for j in jc:
            sk = str(j.get("symbol") or "").upper().replace("_", "")
            if sk in fill_recon_syms:
                continue
            d = _fallback_from_rows([j])[0]
            d["journal_id"] = j.get("id")
            d["id"] = j.get("id")
            entities.append(d)

    now = time.time()
    for d in entities:
        if d.get("status") == "open":
            if d.get("opened_at"):
                d["hold_seconds"] = max(0.0, now - float(d["opened_at"]))
                d["hold_hours"] = round(d["hold_seconds"] / 3600.0, 2)
            _attach_mark(d)
            d["outcome"] = d.get("outcome") or "open"
        else:
            d["mark_price"] = d.get("mark_price")
            d["upnl_pct"] = None
            if d.get("outcome") in (None, "flat") and d.get("realized_pnl_pct") is not None:
                p = float(d["realized_pnl_pct"])
                d["outcome"] = (
                    "success" if p > 0.5 else ("miss" if p < -0.5 else "flat")
                )
            if d.get("closed_at"):
                d["closed_ago_seconds"] = max(0.0, now - float(d["closed_at"]))

    opens = [e for e in entities if e.get("status") == "open"]
    closed = [e for e in entities if e.get("status") == "closed"]

    opens.sort(
        key=lambda x: float(x.get("opened_at") or 0),
        reverse=True,
    )
    closed.sort(
        key=lambda x: float(x.get("closed_at") or x.get("opened_at") or 0),
        reverse=True,
    )
    if not include_closed:
        entities = opens
    else:
        entities = opens + closed[:closed_limit]

    for i, e in enumerate(entities):
        if e.get("id") is None:
            e["id"] = 100000 + i
        if "journal_id" not in e:
            e["journal_id"] = None
        e["band"] = "open" if e.get("status") == "open" else "closed"
    return entities


def enrich_positions(rows: List[dict], user_id: int) -> List[dict]:
    """Back-compat: open journal rows enriched; prefer list_position_entities."""
    entities = list_position_entities(user_id, include_closed=False)
    if entities:
        return entities
    return _fallback_from_rows(rows)


def _attach_mark(d: dict) -> None:
    sym = str(d.get("symbol") or "")
    entry = d.get("entry_display") or d.get("entry_avg")
    try:
        t = ticker_24h(sym)
        if t:
            d["mark_price"] = t.get("price")
            d["change_24h_pct"] = t.get("changePercent")
            d["mark_source"] = t.get("source")
    except Exception:
        d["mark_price"] = None
    mark = d.get("mark_price")
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


def _fallback_journal(user_id: int, include_closed: bool) -> List[dict]:
    if include_closed:
        rows = db.fetch_all(
            "SELECT * FROM journal_trades WHERE user_id=? ORDER BY opened_at DESC LIMIT 50",
            (user_id,),
        )
    else:
        rows = db.fetch_all(
            "SELECT * FROM journal_trades WHERE user_id=? AND status='open' ORDER BY opened_at DESC",
            (user_id,),
        )
    out = _fallback_from_rows(rows)
    opens = [e for e in out if e.get("status") == "open"]
    closed = [e for e in out if e.get("status") == "closed"]
    opens.sort(key=lambda x: float(x.get("opened_at") or 0), reverse=True)
    closed.sort(
        key=lambda x: float(x.get("closed_at") or x.get("opened_at") or 0),
        reverse=True,
    )
    if not include_closed:
        return opens
    return opens + closed


def _fallback_from_rows(rows: List[dict]) -> List[dict]:
    now = time.time()
    out = []
    for p in rows:
        d = dict(p)
        d["entry_display"] = d.get("entry_avg")
        d["buy_orders"] = d.get("buy_orders") or []
        d["sell_orders"] = d.get("sell_orders") or []
        d["recon_from_fills"] = False
        d["is_open"] = d.get("status") == "open"
        if d.get("status") == "closed" and d.get("entry_avg") and d.get("exit_avg"):
            try:
                pnl = (
                    (float(d["exit_avg"]) - float(d["entry_avg"]))
                    / float(d["entry_avg"])
                    * 100.0
                )
                d["realized_pnl_pct"] = round(pnl, 3)
                d["outcome"] = (
                    "success" if pnl > 0.5 else ("miss" if pnl < -0.5 else "flat")
                )
            except Exception:
                d["outcome"] = "flat"
            if d.get("closed_at"):
                d["closed_ago_seconds"] = max(0.0, now - float(d["closed_at"]))
        elif d.get("status") == "open":
            d["outcome"] = "open"
            if d.get("opened_at"):
                d["hold_hours"] = round((now - float(d["opened_at"])) / 3600.0, 2)
            _attach_mark(d)
        out.append(d)
    return out


def positions_by_symbol(positions: List[dict]) -> Dict[str, dict]:
    by: Dict[str, dict] = {}
    for p in positions:
        if p.get("status") != "open":
            continue
        s = (p.get("symbol") or "").upper().replace("_", "")
        if s:
            by[s] = p
    return by
