#!/usr/bin/env python3
"""Desk writes SQLite; bot AlertStore must see new targets without restart.

Regression for: AD Desk INSERT bypasses AlertStore cache → monitor never fires.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mexc_bot.storage import AlertStore
from mexc_bot.monitor import PriceMonitor


class FakePriceProvider:
    def __init__(self, prices: dict):
        self._prices = dict(prices)

    def get_all_prices(self):
        return dict(self._prices)

    def get_price(self, symbol: str):
        return self._prices.get(str(symbol).upper())

    def close(self):
        pass


def _desk_insert(db_path: Path, user_id: int, symbol: str, price: float, market: str = "spot"):
    """Simulate AD Desk raw SQL write (no AlertStore)."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO alerts (user_id, symbol, price, enabled, market) VALUES (?, ?, ?, 1, ?)",
        (user_id, symbol.upper(), float(price), market),
    )
    con.commit()
    con.close()


def test_external_insert_visible_to_get_user_alerts():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "alerts.db"
        store = AlertStore(db)
        uid = 8630949601
        store.add_alert(uid, "BTCUSDT", 50000.0, market="spot")
        before = store.get_user_alerts(uid)
        assert len(before) == 1

        # Desk-style external insert while bot process holds cache
        _desk_insert(db, uid, "ETHUSDT", 3000.0, "spot")

        after = store.get_user_alerts(uid)
        symbols = {a["symbol"] for a in after}
        assert "ETHUSDT" in symbols, f"cache missed desk insert: {after}"
        assert len(after) == 2


def test_external_insert_new_user_in_get_all_user_ids():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "alerts.db"
        store = AlertStore(db)
        assert store.get_all_user_ids() == []

        _desk_insert(db, 42, "BTCUSDT", 1.0, "spot")
        uids = store.get_all_user_ids()
        assert 42 in uids


def test_monitor_fires_desk_added_alert():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "alerts.db"
        store = AlertStore(db)
        uid = 99
        store.add_alert(uid, "AAAUSDT", 100.0, market="spot")
        prices = FakePriceProvider({"AAAUSDT": 100.0, "BBBUSDT": 50.0})
        fired = []

        def notify(user_id, msg, parse_mode=None):
            fired.append((user_id, msg))

        settings = type(
            "S",
            (),
            {"alert_tolerance_percent": 0.5, "price_poll_interval_seconds": 1},
        )()
        mon = PriceMonitor(
            store=store,
            price_provider=prices,
            settings=settings,
            notifier=notify,
        )
        # Warm cache with first cycle
        mon._check_once()
        fired.clear()

        # Desk adds BBB; price already at target → band fire
        _desk_insert(db, uid, "BBBUSDT", 50.0, "spot")
        mon._check_once()
        assert fired, "monitor must fire desk-added target without bot-side add"


def test_resolve_target_symbol_spot():
    from mexc_bot.webapi.actions import _resolve_target_symbol

    assert _resolve_target_symbol("BTW", "spot").endswith("USDT")
    assert "BTC" in _resolve_target_symbol("btc", "spot").upper()


if __name__ == "__main__":
    test_external_insert_visible_to_get_user_alerts()
    test_external_insert_new_user_in_get_all_user_ids()
    test_monitor_fires_desk_added_alert()
    test_resolve_target_symbol_spot()
    print("OK")
