"""Position entities from segmented fill history + live marks."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from . import db
from .position_math import (
    apply_open_mark_math,
    apply_open_remaining_cost_avg,
    collapse_entity_layers,
    collapse_fills_to_orders,
    ensure_position_display_fields,
    tag_book,
)
from .prices import ticker_24h

logger = logging.getLogger(__name__)

# P5: no day/count cutoff on the desk book. 0 = unlimited.
_FILLS_LIMIT = 100_000
_CLOSED_TAB_LIMIT = 50
_open_book_cache: Dict[str, Any] = {"ts": 0.0, "entities": []}
_OPEN_BOOK_TTL = 90.0


def list_position_entities(
    user_id: int,
    *,
    include_closed: bool = True,
    closed_limit: int = 0,
    marks_only: bool = False,
) -> List[dict]:
    """Discrete positions (open + closed cycles).

    Order for the desk: **all opens first** (newest open), then closed
    (newest closed). Each full flat is its own entity with success/miss.
    Journal-only opens (no fills yet) are merged in.
    """
    if marks_only and not include_closed:
        cached = _open_book_cache.get("entities") or []
        ts = float(_open_book_cache.get("ts") or 0)
        if cached and (time.time() - ts) < _OPEN_BOOK_TTL:
            import copy

            ents = copy.deepcopy(cached)
            now = time.time()
            for d in ents:
                if d.get("status") != "open":
                    continue
                _attach_mark(d)
                apply_open_mark_math(d)
                if d.get("opened_at"):
                    d["hold_seconds"] = max(0.0, now - float(d["opened_at"]))
                    d["hold_hours"] = round(d["hold_seconds"] / 3600.0, 2)
            return ents
    try:
        from ..learning.store import EventStore
        from ..learning.trades import segment_positions_from_fills

        store = EventStore(db.db_path())
        fills_all = store.recent_fills(user_id, limit=_FILLS_LIMIT)
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
    # Spot fill-walk first, then reconcile size against live balances.
    for sym, mkt in pairs:
        if mkt == "futures":
            continue
        segs = segment_positions_from_fills(fills_all, symbol=sym, market=mkt)
        for s in segs:
            if not include_closed and s.get("status") != "open":
                continue
            is_open = s.get("status") == "open" or s.get("is_open")
            n_sells = int(s.get("n_sells") or 0)
            rem = float(s.get("size_remaining") or 0)
            # Complete flat cycle from fills = teachable closed trade (spot)
            if not is_open and n_sells > 0 and rem <= 1e-8:
                s["money_truth"] = "fill_cycle"
                s["verified"] = False  # not exchange history; still process+fill $
                s["teach_ok"] = True
            else:
                s["money_truth"] = "fill_recon_unverified"
                s["verified"] = False
                s["teach_ok"] = False
            s["source"] = "fill_recon"
            s["recon_from_fills"] = True
            entities.append(s)

    entities = _reconcile_spot_with_balances(
        entities, store, user_id, fills_all=fills_all
    )

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
        jm = (j.get("market") or "spot").lower()
        key = (
            str(j.get("symbol") or "").upper().replace("_", ""),
            jm,
        )
        # Skip journal open if exchange already shows this futures symbol open
        if jm == "futures" and any(
            (e.get("market") or "").lower() == "futures"
            and (e.get("status") == "open" or e.get("is_open"))
            and e.get("exchange_hold")
            and str(e.get("symbol") or "").upper().replace("_", "") == key[0]
            for e in entities
        ):
            continue
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
        notes = str(j.get("notes") or "")
        if "auto from MEXC fill" in notes or "auto close from MEXC fill" in notes:
            # A fill is not a position. Do not paint auto-journal as Open.
            continue
        d = _fallback_from_rows([j])[0]
        d["journal_id"] = j.get("id")
        d["id"] = j.get("id")
        d["money_truth"] = "journal_manual"
        d["verified"] = False
        d["teach_ok"] = False
        entities.append(d)
        open_keys.add(key)

    if include_closed:
        # Manual journal closed only (spot). Futures closed = history_positions only.
        fill_recon_syms = {
            str(e.get("symbol") or "").upper().replace("_", "")
            for e in entities
            if e.get("recon_from_fills") or e.get("exchange_history")
        }
        try:
            jc_limit = closed_limit if closed_limit and closed_limit > 0 else 100_000
            jc = db.fetch_all(
                "SELECT * FROM journal_trades WHERE user_id=? AND status='closed' "
                "ORDER BY closed_at DESC LIMIT ?",
                (user_id, jc_limit),
            )
        except Exception:
            jc = []
        for j in jc:
            if (j.get("market") or "spot").lower() == "futures":
                continue  # never journal futures closed money facts
            sk = str(j.get("symbol") or "").upper().replace("_", "")
            if sk in fill_recon_syms:
                continue
            d = _fallback_from_rows([j])[0]
            d["journal_id"] = j.get("id")
            d["id"] = j.get("id")
            d["money_truth"] = "journal_manual"
            d["verified"] = False
            d["teach_ok"] = False
            entities.append(d)

    now = time.time()
    for d in entities:
        if d.get("status") == "open":
            apply_open_remaining_cost_avg(d)
            if d.get("opened_at"):
                d["hold_seconds"] = max(0.0, now - float(d["opened_at"]))
                d["hold_hours"] = round(d["hold_seconds"] / 3600.0, 2)
            _attach_mark(d)
            apply_open_mark_math(d)
            if d.get("upnl_usd_est") is not None:
                try:
                    d["upnl_usd_est"] = round(float(d["upnl_usd_est"]), 4)
                except (TypeError, ValueError):
                    pass
            if d.get("remaining_mark_usd") is not None:
                try:
                    d["remaining_mark_usd"] = round(float(d["remaining_mark_usd"]), 4)
                except (TypeError, ValueError):
                    pass
            d["outcome"] = d.get("outcome") or "open"
        else:
            tag_book(d)
            d["mark_price"] = d.get("mark_price")
            d["upnl_pct"] = None
            if d.get("outcome") in (None, "flat") and d.get("realized_pnl_pct") is not None:
                p = float(d["realized_pnl_pct"])
                d["outcome"] = (
                    "success" if p > 0.5 else ("miss" if p < -0.5 else "flat")
                )
            if d.get("closed_at"):
                d["closed_ago_seconds"] = max(0.0, now - float(d["closed_at"]))

    # Free-coin flags (spot open) — merge manual overrides from SQLite
    try:
        _apply_free_coins(entities, user_id=user_id, store=store)
    except Exception as e:
        logger.debug("free_coins: %s", e)

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
        _open_book_cache["ts"] = time.time()
        _open_book_cache["entities"] = list(opens)
    elif closed_limit and closed_limit > 0:
        entities = opens + closed[:closed_limit]
    else:
        entities = opens + closed

    for i, e in enumerate(entities):
        if e.get("id") is None:
            e["id"] = 100000 + i
        if "journal_id" not in e:
            e["journal_id"] = None
        e["band"] = "open" if e.get("status") == "open" else "closed"
        collapse_entity_layers(e)
        ensure_position_display_fields(e)
    return entities


def _flag_for_entity(flags: Dict[str, dict], d: dict) -> dict:
    """Match flags by entity_key, sopen:BASEUSDT, or same-symbol hold row."""
    ek = str(d.get("entity_key") or "")
    sym = str(d.get("symbol") or "").upper().replace("_", "").replace("-", "")
    if sym and not sym.endswith("USDT") and not sym.endswith("USDC"):
        sym_u = sym + "USDT"
    else:
        sym_u = sym
    alt = f"sopen:{sym_u}" if sym_u else ""
    fl = flags.get(ek) or (flags.get(alt) if alt else None)
    if fl:
        return fl
    # Symbol-level hold: any flag with book=hold matching base
    for f in flags.values():
        if (f.get("book") or "").lower() != "hold":
            continue
        fs = str(f.get("symbol") or "").upper().replace("_", "").replace("-", "")
        if not fs:
            continue
        if fs == sym or fs == sym_u or fs + "USDT" == sym_u or sym + "USDT" == fs:
            return f
    return {}


def _apply_free_coins(
    entities: List[dict], *, user_id: int, store: Any
) -> None:
    """Apply free-coins + long-term hold (book) flags from position_flags."""
    flags: Dict[str, dict] = {}
    try:
        if store is not None and hasattr(store, "list_position_flags"):
            for f in store.list_position_flags(user_id):
                flags[str(f.get("entity_key") or "")] = f
    except Exception:
        flags = {}

    for d in entities:
        fl = _flag_for_entity(flags, d)
        book = (fl.get("book") or "ad").lower().strip()
        if book not in ("hold", "ad"):
            book = "ad"
        d["position_book"] = book  # ad | hold
        d["ad_learning"] = book != "hold"
        d["is_hold"] = book == "hold"

        # Long-term invest basket: never auto free-coin as AD leftover
        if book == "hold":
            d["free_coins_status"] = "none"
            d["free_coins_source"] = "none"
            d["free_coins"] = False
            d["free_coins_override"] = None
            continue

        override = (fl.get("free_coins_override") or "").lower() or None

        bought = float(d.get("bought_usd") or 0)
        sold = float(d.get("sold_usd") or 0)
        rem = float(d.get("size_remaining") or 0)
        mkt = (d.get("market") or "").lower()
        is_open = d.get("status") == "open" or d.get("is_open")
        verified = d.get("money_truth") in (
            "exchange",
            "exchange_history",
            "spot_balance",
        ) or d.get("verified") is True

        principal = bool(d.get("principal_recovered"))
        if not principal and bought > 0 and sold + max(1.0, 0.005 * bought) >= bought:
            principal = True
            d["principal_recovered"] = True

        auto = False
        near = False
        if (
            is_open
            and mkt == "spot"
            and rem > 1e-8
            and bought > 0
            and verified
            and d.get("money_truth") != "fill_recon_unverified"
        ):
            if principal:
                auto = True
            elif sold >= 0.9 * bought:
                near = True

        if override == "on":
            status, source = "manual_on", "manual"
        elif override == "off":
            status, source = "manual_off", "manual"
        elif auto:
            status, source = "auto", "auto"
        elif near:
            status, source = "near_free", "auto"
        else:
            status, source = "none", "none"

        d["free_coins_status"] = status
        d["free_coins_source"] = source
        d["free_coins"] = status in ("auto", "manual_on")
        d["free_coins_override"] = override
        if fl.get("free_since_ts"):
            d["free_since_ts"] = fl.get("free_since_ts")
        if fl.get("free_mark_usd") is not None:
            d["free_mark_usd_at_flag"] = fl.get("free_mark_usd")
        elif d.get("free_coins") and d.get("remaining_mark_usd") is not None:
            d["free_mark_usd_at_flag"] = d.get("remaining_mark_usd")


def _norm_fut_key(symbol: str) -> str:
    return str(symbol or "").upper().replace("_", "").replace("-", "")


def _spot_base_asset(symbol: str) -> str:
    s = str(symbol or "").upper().replace("_", "").replace("-", "")
    if s.endswith("USDT"):
        return s[:-4]
    if s.endswith("USDC"):
        return s[:-4]
    return s


# Dust / delisted wallets still on account but not tradeable (no USDT market).
_SPOT_BALANCE_IGNORE_ASSETS = frozenset(
    {
        "GOONC",  # delisted; residual bag, not a trade
    }
)


def _spot_symbol_tradeable(symbol: str) -> bool:
    """False if MEXC has no spot ticker (delisted / invalid pair)."""
    sym = str(symbol or "").upper().replace("_", "").replace("-", "")
    if not sym.endswith("USDT"):
        sym = sym + "USDT"
    asset = _spot_base_asset(sym)
    if asset in _SPOT_BALANCE_IGNORE_ASSETS:
        return False
    try:
        t = ticker_24h(sym)
        return bool(t and t.get("price") and float(t["price"]) > 0)
    except Exception:
        return False


def _reconcile_spot_with_balances(
    entities: List[dict],
    store: Any,
    user_id: int,
    *,
    fills_all: Optional[List[dict]] = None,
) -> List[dict]:
    """Spot OPEN size from account balances; drop ghost fill residuals.

    Entry avg still from fill recon when available. Balances make qty authority.
    """
    from ..learning.fills import (
        fetch_live_spot_balances,
        read_spot_balances_authority,
    )
    from ..learning.trades import segment_positions_from_fills

    fills_all = fills_all or []
    bals = fetch_live_spot_balances(user_id, event_store=store)
    if bals is None:
        bals = read_spot_balances_authority(store, user_id, max_age_s=900.0)
    if bals is None:
        # No authority — leave fill-recon tags
        for e in entities:
            if (e.get("market") or "").lower() != "spot":
                continue
            if e.get("status") == "open" or e.get("is_open"):
                e["notes"] = (
                    (e.get("notes") or "")
                    + " · spot residual from fills (balance API unavailable)"
                ).strip(" ·")
        return entities

    by_asset: Dict[str, dict] = {}
    for b in bals:
        asset = str(b.get("asset") or "").upper()
        tot = float(b.get("total") or 0)
        if not asset or tot <= 1e-8:
            continue
        if asset in _SPOT_BALANCE_IGNORE_ASSETS:
            logger.info("Ignoring delisted/dust spot asset %s", asset)
            continue
        sym = str(b.get("symbol") or f"{asset}USDT")
        if not _spot_symbol_tradeable(sym):
            logger.info("Ignoring untradeable spot asset %s (no market)", asset)
            continue
        by_asset[asset] = b

    kept: List[dict] = []
    matched: Set[str] = set()
    for e in entities:
        if (e.get("market") or "").lower() != "spot":
            kept.append(e)
            continue
        if e.get("status") != "open" and not e.get("is_open"):
            # closed fill-walk spot — keep for history when we have fills
            kept.append(e)
            continue
        asset = _spot_base_asset(str(e.get("symbol") or ""))
        if asset in _SPOT_BALANCE_IGNORE_ASSETS:
            continue
        if not _spot_symbol_tradeable(str(e.get("symbol") or "")):
            logger.info("Dropping untradeable spot open %s", e.get("symbol"))
            continue
        bal = by_asset.get(asset)
        if not bal:
            # Ghost residual — zero on exchange
            logger.info("Dropping ghost spot open %s (no balance)", e.get("symbol"))
            continue
        tot = float(bal["total"])
        e["size_remaining"] = tot
        e["size_qty"] = e.get("size_qty") or tot
        e["exchange_hold"] = True
        e["spot_free"] = bal.get("free")
        e["spot_locked"] = bal.get("locked")
        e["money_truth"] = "exchange"
        e["verified"] = True
        e["teach_ok"] = True  # size balance-true; entry from fills when present
        e["source"] = "mexc_spot_account"
        e["notes"] = (
            f"spot balance {tot} (free {bal.get('free')} locked {bal.get('locked')})"
        )
        matched.add(asset)
        # attach fill layers for open window
        if not e.get("buy_orders") and not e.get("sell_orders"):
            _attach_fills_window(
                e, fills_all, market="spot", open_position=True
            )
        apply_open_remaining_cost_avg(e)
        kept.append(e)

    # Balances with no fill-open entity yet — invent open with fill entry if possible
    for asset, bal in by_asset.items():
        if asset in matched:
            continue
        tot = float(bal["total"])
        if tot <= 1e-8:
            continue
        sym = str(bal.get("symbol") or f"{asset}USDT")
        segs = segment_positions_from_fills(fills_all, symbol=sym, market="spot")
        open_seg = next((s for s in segs if s.get("status") == "open"), None)
        entry = None
        buys, sells = [], []
        if open_seg:
            entry = open_seg.get("entry_avg")
            buys = open_seg.get("buy_orders") or []
            sells = open_seg.get("sell_orders") or []
        ent = {
            "symbol": sym,
            "market": "spot",
            "status": "open",
            "outcome": "open",
            "is_open": True,
            "opened_at": open_seg.get("opened_at") if open_seg else None,
            "closed_at": None,
            "entry_avg": entry,
            "entry_display": entry,
            "exit_avg": None,
            "size_remaining": tot,
            "size_qty": open_seg.get("size_qty") if open_seg else tot,
            "size_sold": open_seg.get("size_sold") if open_seg else 0,
            "bought_usd": open_seg.get("bought_usd") if open_seg else None,
            "sold_usd": open_seg.get("sold_usd") if open_seg else None,
            "remaining_cost_usd": open_seg.get("remaining_cost_usd") if open_seg else None,
            "buy_orders": buys,
            "sell_orders": sells,
            "n_buys": len(buys),
            "n_sells": len(sells),
            "recon_from_fills": bool(open_seg),
            "exchange_hold": True,
            "money_truth": "exchange",
            "verified": True,
            "teach_ok": True,
            "source": "mexc_spot_account",
            "spot_free": bal.get("free"),
            "spot_locked": bal.get("locked"),
            "entity_key": f"sopen:{sym}",
            "notes": f"spot balance {tot} (from account)",
        }
        if not buys and not sells:
            _attach_fills_window(
                ent, fills_all, market="spot", open_position=True
            )
        apply_open_remaining_cost_avg(ent)
        kept.append(ent)

    return kept


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
            "money_truth": "exchange",
            "verified": True,
            "teach_ok": True,
            "source": "mexc_open_positions",
            "leverage": fo.get("leverage"),
            "realized_on_pos": fo.get("realized"),
            "hold_fee": hold_fee,
            "close_profit_loss": fo.get("close_profit_loss"),
            "unrealized_pnl": fo.get("unrealized_pnl"),
            "contract_size": fo.get("contract_size") or 1.0,
            "raw": fo.get("raw") if isinstance(fo.get("raw"), dict) else {},
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
        # Remaining-cost leftover from this book's fills; keep exchange hold_avg.
        try:
            from ..learning.trades import segment_positions_from_fills

            segs = segment_positions_from_fills(
                fills_all, symbol=fsym, market="futures"
            )
            open_seg = next((s for s in segs if s.get("status") == "open"), None)
            if open_seg:
                if open_seg.get("bought_usd") is not None:
                    ent["bought_usd"] = open_seg.get("bought_usd")
                if open_seg.get("sold_usd") is not None:
                    ent["sold_usd"] = open_seg.get("sold_usd")
            apply_open_remaining_cost_avg(ent)
        except Exception as exc:
            logger.debug("futures remaining-cost avg: %s", exc)
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
    matched: List[dict] = []
    for f in fills_all:
        fm = (f.get("market") or "").lower() or "spot"
        # spot fills often market=spot or empty
        if mwant == "spot":
            if fm not in ("", "spot"):
                continue
        elif fm and fm != mwant:
            continue
        if not symbols_match(ent.get("symbol") or "", f.get("symbol") or ""):
            continue
        ts = float(f.get("ts") or 0)
        if ts < o - pad_s or ts > c + pad_s:
            continue
        matched.append(f)
    # One layer per user order at one price — never one line per deal/fill.
    # quote_qty stored as leftover-cost cash. Futures deal rows are price×vol
    # (notional); cash = notional × contractSize. Unknown size → no $ on the layer.
    cs = None
    if mwant == "futures":
        from .contract_size import resolve_futures_contract_size

        cs = resolve_futures_contract_size(
            ent.get("symbol"), ent, ent.get("contract_size")
        )
        if cs is not None and cs > 0:
            ent["contract_size"] = cs
    buys: List[dict] = []
    sells: List[dict] = []
    for f in collapse_fills_to_orders(matched):
        ts = float(f.get("ts") or 0)
        qty = f.get("qty")
        px = f.get("price")
        qq = None
        try:
            px_f = float(px) if px not in (None, "") else 0.0
            qty_f = float(qty) if qty not in (None, "") else 0.0
        except (TypeError, ValueError):
            px_f = qty_f = 0.0
        notional = px_f * qty_f if px_f > 0 and qty_f > 0 else 0.0
        if mwant == "futures":
            qq = (notional * cs) if (cs is not None and cs > 0 and notional > 0) else None
        else:
            raw_qq = f.get("quote_qty")
            if raw_qq not in (None, ""):
                try:
                    qq = float(raw_qq)
                except (TypeError, ValueError):
                    qq = notional or None
            else:
                qq = notional or None
        layer = {
            "price": px,
            "qty": qty,
            "quote_qty": qq,
            "ts": ts,
            "side": f.get("side"),
            "order_id": f.get("_order_id") or f.get("order_id"),
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
    closed = fetch_live_futures_closed(user_id, event_store=store, max_pages=40)
    if closed is None:
        closed = read_futures_closed_authority(store, user_id, max_age_s=900.0)
    if not closed:
        return kept

    closed = sorted(
        closed,
        key=lambda x: float(x.get("closed_at") or x.get("opened_at") or 0),
        reverse=True,
    )
    if closed_limit and closed_limit > 0:
        closed = closed[:closed_limit]
    for ent in closed:
        e = dict(ent)
        e.setdefault("buy_orders", [])
        e.setdefault("sell_orders", [])
        e["money_truth"] = "exchange"
        e["verified"] = True
        e["teach_ok"] = True
        e["source"] = "mexc_history_positions"
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
    entry = None
    for key in ("remaining_avg", "leftover_avg", "entry_display", "entry_avg"):
        if d.get(key) is None:
            continue
        try:
            entry = float(d.get(key))
            break
        except (TypeError, ValueError):
            continue
    have = d.get("mark_price") if d.get("mark_price") not in (None, "", 0, 0.0) else d.get("mark")
    if have not in (None, "", 0, 0.0):
        try:
            d["mark_price"] = float(have)
            d.setdefault("mark_source", d.get("mark_source") or "exchange")
        except (TypeError, ValueError):
            have = None
    if have in (None, "", 0, 0.0):
        try:
            t = ticker_24h(sym)
            if t:
                d["mark_price"] = t.get("price")
                d["change_24h_pct"] = t.get("changePercent")
                d["mark_source"] = t.get("source")
        except Exception:
            d["mark_price"] = None
    mark = d.get("mark_price")
    if mark is not None and entry is not None and float(entry) != 0:
        d["upnl_pct"] = round(
            (float(mark) - float(entry)) / abs(float(entry)) * 100.0, 3
        )
    else:
        d["upnl_pct"] = None
    # Dollar notional / uPnL: apply_open_mark_math (never rem×mark on futures).


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
