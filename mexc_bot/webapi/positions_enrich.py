"""Position entities from segmented fill history + live marks."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

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
    # Spot only from fill walk. Futures closed fill-walk is untrustworthy
    # (truncated deals + side model) — use history_positions instead.
    for sym, mkt in pairs:
        if mkt == "futures":
            continue
        segs = segment_positions_from_fills(fills_all, symbol=sym, market=mkt)
        for s in segs:
            if not include_closed and s.get("status") != "open":
                continue
            entities.append(s)

    # Futures OPEN: exchange open_positions + deal layers from fills
    entities = _reconcile_futures_with_exchange(
        entities, store, user_id, fills_all=fills_all
    )

    # Futures CLOSED: exchange history_positions (openAvg/closeAvg/realised)
    if include_closed:
        entities = _merge_futures_closed_history(
            entities, store, user_id, fills_all, closed_limit=closed_limit
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
            # Futures: exchange uPnL is vs residual holdAvg (not funding-adjusted)
            if (
                (d.get("market") or "").lower() == "futures"
                and d.get("unrealized_pnl") is not None
                and d.get("size_remaining")
            ):
                try:
                    inv = float(d.get("hold_avg") or d.get("entry_display") or 0)
                    live = float(d.get("entry_display") or inv)
                    rem = float(d["size_remaining"])
                    upnl = float(d["unrealized_pnl"])
                    d["upnl_usd_est"] = round(upnl, 4)
                    if rem > 0 and inv > 0:
                        if d.get("position_type") == 2:
                            d["mark_price"] = inv - upnl / rem
                        else:
                            d["mark_price"] = inv + upnl / rem
                        # % vs live avg (funding-adjusted) when we show that as entry
                        if live > 0:
                            d["upnl_pct"] = round(
                                (float(d["mark_price"]) - live) / live * 100.0, 3
                            )
                        d["mark_source"] = "mexc_position"
                except Exception:
                    pass
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


def _norm_fut_key(symbol: str) -> str:
    return str(symbol or "").upper().replace("_", "").replace("-", "")


def _reconcile_futures_with_exchange(
    entities: List[dict],
    store: Any,
    user_id: int,
    *,
    fills_all: Optional[List[dict]] = None,
) -> List[dict]:
    """Futures OPEN from exchange open_positions + deal layers from fills."""
    from ..learning.fills import (
        fetch_live_futures_opens,
        read_futures_open_authority,
    )

    fills_all = fills_all or []
    fut_opens = fetch_live_futures_opens(user_id, event_store=store)
    if fut_opens is None:
        fut_opens = read_futures_open_authority(store, user_id, max_age_s=900.0)
    if fut_opens is None:
        logger.debug("futures open authority unavailable")
        return [
            e
            for e in entities
            if not (
                (e.get("market") or "").lower() == "futures"
                and (e.get("status") == "open" or e.get("is_open"))
            )
        ]

    by_exch: Dict[str, dict] = {}
    for fo in fut_opens:
        k = _norm_fut_key(str(fo.get("symbol") or ""))
        hold = float(fo.get("hold_vol") or 0)
        if k and hold > 0:
            by_exch[k] = fo

    kept = [
        e
        for e in entities
        if not (
            (e.get("market") or "").lower() == "futures"
            and (e.get("status") == "open" or e.get("is_open"))
        )
    ]
    for k, fo in by_exch.items():
        fsym = str(fo.get("symbol") or "").upper()
        hold = float(fo.get("hold_vol") or 0)
        entry = fo.get("entry_live")
        if entry is None:
            entry = fo.get("entry_avg")
        hold_avg = fo.get("hold_avg") or fo.get("entry_avg")
        hold_fee = fo.get("hold_fee")
        notes_bits = ["open on MEXC futures · residual hold avg"]
        if fo.get("close_vol"):
            notes_bits.append(f"partial sold {fo.get('close_vol')}")
        if hold_fee:
            notes_bits.append(f"funding {hold_fee}")
        opened_at = fo.get("opened_at")
        if opened_at is None and fo.get("create_time"):
            try:
                ct = float(fo["create_time"])
                opened_at = ct / 1000.0 if ct > 1e12 else ct
            except (TypeError, ValueError):
                opened_at = None
        ent = {
            "symbol": fsym,
            "market": "futures",
            "status": "open",
            "outcome": "open",
            "is_open": True,
            "opened_at": opened_at,
            "closed_at": None,
            "entry_avg": entry,
            "entry_display": entry,
            "hold_avg": hold_avg,
            "entry_live": entry,
            "exit_avg": None,
            "size_remaining": hold,
            "size_qty": hold,
            "size_sold": fo.get("close_vol") or 0,
            "buy_orders": [],
            "sell_orders": [],
            "n_buys": 0,
            "n_sells": 0,
            "recon_from_fills": False,
            "exchange_hold": True,
            "leverage": fo.get("leverage"),
            "realized_on_pos": fo.get("realized"),
            "hold_fee": hold_fee,
            "close_profit_loss": fo.get("close_profit_loss"),
            "unrealized_pnl": fo.get("unrealized_pnl"),
            "position_type": fo.get("position_type"),
            "position_side": (
                "long"
                if fo.get("position_type") == 1
                else ("short" if fo.get("position_type") == 2 else None)
            ),
            "entity_key": f"fopen:{fsym}",
            "notes": " · ".join(notes_bits),
        }
        # Deal layers for expand (entries + bounce partials)
        _attach_fills_window(
            ent, fills_all, market="futures", open_position=True
        )
        kept.append(ent)
    return kept


def _attach_fills_window(
    ent: dict,
    fills_all: List[dict],
    *,
    market: str = "futures",
    pad_s: float = 120.0,
    open_position: bool = False,
) -> None:
    """Attach buy/sell layers from journal_fills for expand UI.

    Closed: [opened_at, closed_at]. Open: [opened_at or lookback, now].
    Layers sorted oldest→newest (AD scale-in story).
    """
    from ..learning.engagement import symbols_match

    now = time.time()
    o = float(ent.get("opened_at") or 0)
    c = float(ent.get("closed_at") or 0)
    if open_position:
        if o <= 0:
            # Fall back: last 90d of deals for this symbol (createTime missing)
            o = now - 90 * 86400
        c = now + pad_s
    else:
        if o <= 0 or c <= 0:
            return
    mwant = (market or "futures").lower()
    buys: List[dict] = []
    sells: List[dict] = []
    for f in fills_all:
        fm = (f.get("market") or "").lower()
        if fm and fm != mwant:
            continue
        if not symbols_match(ent.get("symbol") or "", f.get("symbol") or ""):
            continue
        ts = float(f.get("ts") or 0)
        if ts < o - pad_s or ts > c + pad_s:
            continue
        layer = {
            "price": f.get("price"),
            "qty": f.get("qty"),
            "ts": ts,
            "side": f.get("side"),
        }
        if (f.get("side") or "").lower() == "buy":
            buys.append(layer)
        else:
            sells.append(layer)
    buys.sort(key=lambda x: x.get("ts") or 0)
    sells.sort(key=lambda x: x.get("ts") or 0)
    ent["buy_orders"] = buys
    ent["sell_orders"] = sells
    ent["n_buys"] = len(buys)
    ent["n_sells"] = len(sells)


def _attach_fills_to_closed(
    ent: dict, fills_all: List[dict], *, pad_s: float = 120.0
) -> None:
    """Optional expand layers for closed history_positions rows."""
    _attach_fills_window(
        ent, fills_all, market="futures", pad_s=pad_s, open_position=False
    )


def _merge_futures_closed_history(
    entities: List[dict],
    store: Any,
    user_id: int,
    fills_all: List[dict],
    *,
    closed_limit: int,
) -> List[dict]:
    """Replace fill-walk futures closed with history_positions entities."""
    from ..learning.fills import (
        fetch_live_futures_closed,
        read_futures_closed_authority,
    )

    # Drop any futures closed from other sources
    kept = [
        e
        for e in entities
        if not (
            (e.get("market") or "").lower() == "futures"
            and e.get("status") == "closed"
        )
    ]
    closed = fetch_live_futures_closed(user_id, event_store=store, max_pages=4)
    if closed is None:
        closed = read_futures_closed_authority(store, user_id, max_age_s=900.0)
    if not closed:
        return kept

    closed = sorted(
        closed,
        key=lambda x: float(x.get("closed_at") or x.get("opened_at") or 0),
        reverse=True,
    )[:closed_limit]
    for ent in closed:
        e = dict(ent)
        e.setdefault("buy_orders", [])
        e.setdefault("sell_orders", [])
        _attach_fills_to_closed(e, fills_all)
        kept.append(e)
    return kept


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
