"""Poll MEXC private spot myTrades + futures order deals → journal_fills.

Read-only. Soft-fail. Does **not** write auto journal_trades (those polluted
position history); desk positions come from segmented fills only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Set

from ..exchange_private import (
    MexcPrivateFuturesClient,
    MexcPrivateSpotClient,
    futures_deal_to_fill_row,
    futures_position_snapshot,
    history_position_to_closed_entity,
    normalize_futures_symbol,
    trade_to_fill_row,
)
from .store import EventStore

logger = logging.getLogger(__name__)


class FillSyncPoller:
    def __init__(
        self,
        event_store: EventStore,
        private_client: MexcPrivateSpotClient,
        user_id: int,
        get_symbols: Callable[[], Set[str]],
        *,
        poll_seconds: float = 120.0,
        notifier: Optional[Callable[..., None]] = None,
        notify_on_new: bool = False,
        futures_client: Optional[MexcPrivateFuturesClient] = None,
        get_futures_symbols: Optional[Callable[[], Set[str]]] = None,
        write_auto_journal: bool = False,
    ):
        self.event_store = event_store
        self.client = private_client
        self.futures_client = futures_client
        self.user_id = int(user_id)
        self.get_symbols = get_symbols
        self.get_futures_symbols = get_futures_symbols
        self.poll_seconds = max(30.0, float(poll_seconds))
        self.notifier = notifier
        self.notify_on_new = notify_on_new
        # Off by default — auto journal open/close was garbage for the desk
        self.write_auto_journal = write_auto_journal
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fills_new = 0
        self._futures_fills_new = 0
        self._last_cycle_ms = 0
        self._last_open_futures: List[dict] = []
        self._last_spot_balances: List[dict] = []

    def get_health(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "fills_new": self._fills_new,
            "futures_fills_new": self._futures_fills_new,
            "last_cycle_ms": self._last_cycle_ms,
            "user_id": self.user_id,
            "futures_enabled": bool(
                self.futures_client and self.futures_client.configured
            ),
            "open_futures_n": len(self._last_open_futures),
            "spot_balance_n": len(self._last_spot_balances),
            "write_auto_journal": self.write_auto_journal,
        }

    def start(self) -> None:
        if not self.client.configured or self.user_id <= 0:
            logger.warning("FillSyncPoller not started (missing keys or user_id)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="fill-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run(self) -> None:
        logger.info(
            "Fill sync started user=%s poll=%ss futures=%s",
            self.user_id,
            self.poll_seconds,
            bool(self.futures_client and self.futures_client.configured),
        )
        while not self._stop.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.exception("Fill sync error: %s", e)
            slept = 0.0
            while slept < self.poll_seconds and not self._stop.is_set():
                time.sleep(min(1.0, self.poll_seconds - slept))
                slept += 1.0

    def _check_once(self) -> None:
        t0 = time.perf_counter()
        new_rows: List[dict] = []
        new_rows.extend(self._sync_spot())
        new_rows.extend(self._sync_futures())
        if new_rows and self.notify_on_new and self.notifier:
            try:
                lines = [f"MEXC fills synced: {len(new_rows)} new"]
                for r in new_rows[:5]:
                    lines.append(
                        f"  {r.get('market','?')} {r['side'].upper()} "
                        f"{r['symbol']} qty={r['qty']} @ {r['price']}"
                    )
                self.notifier(self.user_id, "\n".join(lines), parse_mode=None)
            except Exception as e:
                logger.warning("fill notify failed: %s", e)
        self._last_cycle_ms = int((time.perf_counter() - t0) * 1000)

    def _insert_row(self, row: dict) -> bool:
        inserted = self.event_store.insert_fill(**{
            k: row[k]
            for k in (
                "user_id",
                "exchange_trade_id",
                "symbol",
                "market",
                "side",
                "price",
                "qty",
                "quote_qty",
                "ts",
                "raw",
            )
            if k in row
        })
        if inserted and self.write_auto_journal:
            try:
                self.event_store.upsert_journal_from_fill(row)
            except Exception as e:
                logger.debug("journal upsert from fill: %s", e)
        return inserted

    def _sync_spot(self) -> List[dict]:
        try:
            symbols = set(self.get_symbols() or set())
        except Exception:
            symbols = set()
        # Live balances = spot open authority + symbol seed
        try:
            bals = self.client.get_account_balances()
            # exclude pure stables from trading open list (still cached)
            # Skip stables + known dead bags (delisted residual)
            _ignore = {"USDT", "USDC", "BUSD", "USD", "GOONC"}
            alt = [b for b in bals if b.get("asset") not in _ignore]
            self._last_spot_balances = alt
            _write_spot_balances_cache(self.event_store, self.user_id, alt)
            for b in alt:
                if b.get("symbol"):
                    symbols.add(str(b["symbol"]).upper().replace("_", ""))
            logger.info(
                "Spot balances cached n=%s assets=%s",
                len(alt),
                [b.get("asset") for b in alt[:12]],
            )
        except Exception as e:
            logger.debug("spot balances: %s", e)

        if not symbols:
            symbols = {"BTCUSDT", "ETHUSDT"}
        new_rows: List[dict] = []
        for sym in list(symbols)[:50]:
            compact = str(sym).upper().replace("_", "").replace("-", "")
            trades = self.client.get_my_trades(compact, limit=100)
            for tr in trades:
                row = trade_to_fill_row(tr, self.user_id)
                if not row:
                    continue
                if self._insert_row(row):
                    new_rows.append(row)
                    self._fills_new += 1
        return new_rows

    def _sync_futures(self) -> List[dict]:
        if not self.futures_client or not self.futures_client.configured:
            return []
        new_rows: List[dict] = []
        # open positions — exchange truth for residual + symbol seed
        try:
            opens = self.futures_client.get_open_positions()
            snaps = []
            for p in opens:
                s = futures_position_snapshot(p)
                if s and (s.get("hold_vol") or 0) > 0:
                    # drop raw for small cache
                    snaps.append({k: v for k, v in s.items() if k != "raw"})
            self._last_open_futures = snaps
            _write_futures_open_cache(self.event_store, self.user_id, snaps)
        except Exception as e:
            logger.debug("futures open_positions: %s", e)
            snaps = list(self._last_open_futures)

        # Closed rounds — exchange history_positions is truth (not deal walk)
        try:
            closed_ents: List[dict] = []
            for page in range(1, 41):
                if self._stop.is_set():
                    break
                hist = self.futures_client.get_history_positions(
                    page_num=page, page_size=50
                )
                if not hist:
                    break
                for p in hist:
                    ent = history_position_to_closed_entity(p)
                    if ent:
                        closed_ents.append(ent)
                if len(hist) < 50:
                    break
                time.sleep(0.12)
            _write_futures_closed_cache(self.event_store, self.user_id, closed_ents)
            logger.info(
                "Futures history_positions cached n=%s user=%s",
                len(closed_ents),
                self.user_id,
            )
        except Exception as e:
            logger.debug("futures history_positions: %s", e)

        try:
            fsyms = set(self.get_futures_symbols() or set()) if self.get_futures_symbols else set()
        except Exception:
            fsyms = set()
        for s in snaps:
            fsyms.add(s["symbol"])
        # normalize to BASE_USDT
        fsyms = {normalize_futures_symbol(s) for s in fsyms if s}
        fsyms.discard("")

        # Deals still synced for optional expand layers only (not closed PnL)
        for sym in list(fsyms)[:40]:
            for page in (1, 2):
                if self._stop.is_set():
                    break
                deals = self.futures_client.get_order_deals(
                    sym, page_num=page, page_size=100
                )
                if not deals:
                    break
                for d in deals:
                    row = futures_deal_to_fill_row(d, self.user_id)
                    if not row:
                        continue
                    if self._insert_row(row):
                        new_rows.append(row)
                        self._fills_new += 1
                        self._futures_fills_new += 1
                if len(deals) < 100:
                    break
                time.sleep(0.15)
        return new_rows


def _futures_cache_path(event_store: EventStore) -> Path:
    return Path(event_store.db_path).parent / "futures_open_cache.json"


def _spot_balances_cache_path(event_store: EventStore) -> Path:
    return Path(event_store.db_path).parent / "spot_balances_cache.json"


def _write_spot_balances_cache(
    event_store: EventStore, user_id: int, bals: List[dict]
) -> None:
    path = _spot_balances_cache_path(event_store)
    try:
        payload = {"user_id": user_id, "ts": time.time(), "balances": bals}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.debug("spot balances cache write: %s", e)


def read_spot_balances_authority(
    event_store: EventStore, user_id: int, *, max_age_s: float = 900.0
) -> Optional[List[dict]]:
    """Authoritative spot alt balances (None if missing/stale)."""
    path = _spot_balances_cache_path(event_store)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("user_id") or 0) != int(user_id):
            return None
        if time.time() - float(data.get("ts") or 0) > max_age_s:
            return None
        rows = data.get("balances") or []
        return rows if isinstance(rows, list) else []
    except Exception:
        return None


def fetch_live_spot_balances(
    user_id: int, event_store: Optional[EventStore] = None
) -> Optional[List[dict]]:
    """Live GET /api/v3/account when keys present."""
    import os

    key = (os.getenv("MEXC_API_KEY") or "").strip()
    sec = (os.getenv("MEXC_API_SECRET") or "").strip()
    if not key or not sec:
        return None
    try:
        client = MexcPrivateSpotClient(key, sec)
        bals = client.get_account_balances()
        _ignore = {"USDT", "USDC", "BUSD", "USD", "GOONC"}
        alt = [b for b in bals if b.get("asset") not in _ignore]
        if event_store is not None and int(user_id) > 0:
            _write_spot_balances_cache(event_store, user_id, alt)
        return alt
    except Exception as e:
        logger.debug("live spot balances: %s", e)
        return None


def _futures_closed_cache_path(event_store: EventStore) -> Path:
    return Path(event_store.db_path).parent / "futures_closed_cache.json"


def _write_futures_open_cache(
    event_store: EventStore, user_id: int, snaps: List[dict]
) -> None:
    path = _futures_cache_path(event_store)
    try:
        payload = {"user_id": user_id, "ts": time.time(), "positions": snaps}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.debug("futures open cache write: %s", e)


def read_futures_open_cache(
    event_store: EventStore, user_id: int, *, max_age_s: float = 600.0
) -> List[dict]:
    """Positions list if cache fresh; [] if missing/stale/wrong user.

    Prefer ``read_futures_open_authority`` when empty must mean “no opens”.
    """
    auth = read_futures_open_authority(event_store, user_id, max_age_s=max_age_s)
    return auth if auth is not None else []


def read_futures_open_authority(
    event_store: EventStore, user_id: int, *, max_age_s: float = 900.0
) -> Optional[List[dict]]:
    """Authoritative futures opens from bot fill-sync cache.

    Returns:
      list (possibly empty) when cache is fresh for this user;
      None when cache is missing/stale (caller must not demote fill opens).
    """
    path = _futures_cache_path(event_store)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("user_id") or 0) != int(user_id):
            return None
        if time.time() - float(data.get("ts") or 0) > max_age_s:
            return None
        rows = data.get("positions") or []
        return rows if isinstance(rows, list) else []
    except Exception:
        return None


def _write_futures_closed_cache(
    event_store: EventStore, user_id: int, entities: List[dict]
) -> None:
    path = _futures_closed_cache_path(event_store)
    try:
        # strip bulky empty lists for cache size
        slim = []
        for e in entities:
            slim.append(
                {
                    k: v
                    for k, v in e.items()
                    if k not in ("buy_orders", "sell_orders")
                }
            )
        payload = {"user_id": user_id, "ts": time.time(), "positions": slim}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.debug("futures closed cache write: %s", e)


def read_futures_closed_authority(
    event_store: EventStore, user_id: int, *, max_age_s: float = 900.0
) -> Optional[List[dict]]:
    """Closed futures entities from history_positions cache (exchange truth)."""
    path = _futures_closed_cache_path(event_store)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("user_id") or 0) != int(user_id):
            return None
        if time.time() - float(data.get("ts") or 0) > max_age_s:
            return None
        rows = data.get("positions") or []
        return rows if isinstance(rows, list) else []
    except Exception:
        return None


def fetch_live_futures_opens(
    user_id: int, event_store: Optional[EventStore] = None
) -> Optional[List[dict]]:
    """Best-effort live open_positions when API keys are in the environment."""
    import os

    key = (os.getenv("MEXC_API_KEY") or "").strip()
    sec = (os.getenv("MEXC_API_SECRET") or "").strip()
    if not key or not sec:
        return None
    try:
        client = MexcPrivateFuturesClient(key, sec)
        if not client.configured:
            return None
        snaps = []
        for p in client.get_open_positions():
            s = futures_position_snapshot(p)
            if s and (s.get("hold_vol") or 0) > 0:
                snaps.append({k: v for k, v in s.items() if k != "raw"})
        if event_store is not None and int(user_id) > 0:
            try:
                _write_futures_open_cache(event_store, user_id, snaps)
            except Exception:
                pass
        return snaps
    except Exception as e:
        logger.debug("live futures opens: %s", e)
        return None


def fetch_live_futures_closed(
    user_id: int,
    event_store: Optional[EventStore] = None,
    *,
    max_pages: int = 40,
) -> Optional[List[dict]]:
    """Live history_positions → closed entities (exchange PnL truth)."""
    import os

    key = (os.getenv("MEXC_API_KEY") or "").strip()
    sec = (os.getenv("MEXC_API_SECRET") or "").strip()
    if not key or not sec:
        return None
    try:
        client = MexcPrivateFuturesClient(key, sec)
        if not client.configured:
            return None
        closed_ents: List[dict] = []
        for page in range(1, max_pages + 1):
            hist = client.get_history_positions(page_num=page, page_size=50)
            if not hist:
                break
            for p in hist:
                ent = history_position_to_closed_entity(p)
                if ent:
                    closed_ents.append(ent)
            if len(hist) < 50:
                break
            time.sleep(0.08)
        if event_store is not None and int(user_id) > 0:
            try:
                _write_futures_closed_cache(event_store, user_id, closed_ents)
            except Exception:
                pass
        return closed_ents
    except Exception as e:
        logger.debug("live futures closed: %s", e)
        return None
