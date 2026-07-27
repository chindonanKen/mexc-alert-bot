#!/usr/bin/env python3
"""V3 tests: futures market column + mover history/scanner (no Telegram, no live API)."""

import sys
import tempfile
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from mexc_bot.exchange import (
    normalize_futures_symbol,
    normalize_spot_symbol,
    resolve_futures_symbol,
    futures_symbol_candidates,
)
from mexc_bot.monitor import PriceMonitor
from mexc_bot.movers.history import PriceHistory
from mexc_bot.movers.scanner import MoverScanner
from mexc_bot.movers.storage import MoverStore
from mexc_bot.storage import AlertStore


class FakePriceProvider:
    def __init__(self, prices: Dict[str, float]):
        self._prices = prices

    def get_all_prices(self) -> Dict[str, float]:
        return dict(self._prices)

    def get_price(self, symbol: str):
        return self._prices.get(symbol.upper())

    def close(self):
        pass


def _settings(**kwargs):
    base = {
        "alert_tolerance_percent": 0.0005,
        "price_poll_interval_seconds": 2,
        "feature_futures_alerts": True,
        "feature_mover_scanner": True,
        "mover_lookback_seconds": 60,
        "mover_threshold_percent": 5.0,
        "mover_poll_seconds": 15,
        "mover_cooldown_seconds": 45,
        "mover_recovery_percent": 3.0,
        "mover_markets": "futures",
    }
    base.update(kwargs)
    return type("S", (), base)()


def test_symbol_normalization():
    assert normalize_spot_symbol("btc") == "BTCUSDT"
    assert normalize_spot_symbol("BTCUSDT") == "BTCUSDT"
    assert normalize_futures_symbol("btc") == "BTC_USDT"
    assert normalize_futures_symbol("BTC_USDT") == "BTC_USDT"
    assert normalize_futures_symbol("BTCUSDT") == "BTC_USDT"
    assert normalize_futures_symbol("eth/usdt") == "ETH_USDT"
    print("PASS: symbol normalization")


def test_resolve_stock_futures_against_known_list():
    known = {
        "BTC_USDT",
        "ETH_USDT",
        "KORU_USDT",
        "ZHIPUSTOCK_USDT",
        "SAMSUNGSTOCK_USDT",
        "TSLASTOCK_USDT",
        "BTCDOM_USDT",  # must NOT steal "BTC"
    }
    assert resolve_futures_symbol("btc", known) == "BTC_USDT"
    assert resolve_futures_symbol("BTC", known) == "BTC_USDT"
    assert resolve_futures_symbol("zhipu", known) == "ZHIPUSTOCK_USDT"
    assert resolve_futures_symbol("ZHIPU", known) == "ZHIPUSTOCK_USDT"
    assert resolve_futures_symbol("samsung", known) == "SAMSUNGSTOCK_USDT"
    assert resolve_futures_symbol("TSLA", known) == "TSLASTOCK_USDT"
    assert resolve_futures_symbol("tesla", known) == "TSLASTOCK_USDT"  # alias
    assert resolve_futures_symbol("TSLASTOCK", known) == "TSLASTOCK_USDT"
    assert resolve_futures_symbol("notarealtickerxyz", known) is None
    # candidates include STOCK form
    cands = futures_symbol_candidates("zhipu")
    assert "ZHIPU_USDT" in cands and "ZHIPUSTOCK_USDT" in cands
    print("PASS: resolve stock futures against known list")


def test_resolve_compact_stock_ui_form():
    """MEXC stock UI uses TSLAUSDT (no underscore); must resolve from TSLA."""
    known = {
        "BTC_USDT",
        "ETH_USDT",
        "TSLAUSDT",  # compact stock perpetual (UI title)
        "SAMSUNGUSDT",
        "MUUSDT",
        "ZHIPUSTOCK_USDT",
    }
    assert resolve_futures_symbol("TSLA", known) == "TSLAUSDT"
    assert resolve_futures_symbol("tsla", known) == "TSLAUSDT"
    assert resolve_futures_symbol("tesla", known) == "TSLAUSDT"
    assert resolve_futures_symbol("TSLAUSDT", known) == "TSLAUSDT"
    assert resolve_futures_symbol("samsung", known) == "SAMSUNGUSDT"
    assert resolve_futures_symbol("MU", known) == "MUUSDT"
    # crypto still underscore
    assert resolve_futures_symbol("BTC", known) == "BTC_USDT"
    # compact form in candidates
    cands = futures_symbol_candidates("TSLA")
    assert "TSLAUSDT" in cands
    assert "TSLA_USDT" in cands
    print("PASS: resolve compact stock UI form (TSLAUSDT)")


def test_resolve_tesla_company_name_contract():
    """MEXC path /futures/TESLA_USDT — API id may be TESLA_USDT not TSLA_USDT."""
    known = {
        "BTC_USDT",
        "ETH_USDT",
        "TESLA_USDT",  # company-name contract id
        "ZHIPUSTOCK_USDT",
    }
    assert resolve_futures_symbol("TSLA", known) == "TESLA_USDT"
    assert resolve_futures_symbol("tesla", known) == "TESLA_USDT"
    assert resolve_futures_symbol("TESLA", known) == "TESLA_USDT"
    cands = futures_symbol_candidates("TSLA")
    assert "TESLA_USDT" in cands
    assert "TESLAUSDT" in cands
    print("PASS: resolve TESLA_USDT company-name contract from TSLA")


def test_existing_spot_alerts_default_market():
    with tempfile.TemporaryDirectory() as td:
        store = AlertStore(Path(td) / "alerts.db")
        u = 1
        vid = store.add_alert(u, "BTCUSDT", 65000.0)  # no market arg — V1 API
        assert vid == 1
        alerts = store.get_user_alerts(u)
        assert len(alerts) == 1
        assert alerts[0]["market"] == "spot"
        assert alerts[0]["symbol"] == "BTCUSDT"
        print("PASS: default market=spot for legacy add_alert")


def test_futures_alert_isolated_price_book():
    """Spot book must not fire a futures alert (and vice versa)."""
    with tempfile.TemporaryDirectory() as td:
        store = AlertStore(Path(td) / "alerts.db")
        u = 42
        store.add_alert(u, "BTC_USDT", 100.0, market="futures")
        store.add_alert(u, "BTCUSDT", 100.0, market="spot")

        spot = FakePriceProvider({"BTCUSDT": 50.0})  # far from 100, no cross yet
        fut = FakePriceProvider({"BTC_USDT": 50.0})

        mon = PriceMonitor(
            settings=_settings(),
            store=store,
            price_provider=spot,
            notifier=lambda *a, **k: None,
            futures_provider=fut,
        )
        mon._check_once()  # seed

        # Only futures crosses
        mon.price_provider = FakePriceProvider({"BTCUSDT": 50.0})
        mon.futures_provider = FakePriceProvider({"BTC_USDT": 150.0})

        fired = []

        def notify(uid, msg, parse_mode=None):
            fired.append((uid, msg))

        mon.notifier = notify
        mon._check_once()

        assert len(fired) == 1, f"expected 1 futures fire, got {fired}"
        assert "BTC_USDT" in fired[0][1]
        assert "[F]" in fired[0][1]
        assert "<b>" in fired[0][1]  # HTML parse mode (safe for underscores)

        remaining = store.get_user_alerts(u)
        assert len(remaining) == 1
        assert remaining[0]["market"] == "spot"
        assert remaining[0]["symbol"] == "BTCUSDT"
        print("PASS: futures alert uses futures book only; spot untouched")


def test_futures_skipped_without_provider():
    """With futures rows present but no futures_provider, they are never removed."""
    with tempfile.TemporaryDirectory() as td:
        store = AlertStore(Path(td) / "alerts.db")
        u = 7
        store.add_alert(u, "BTC_USDT", 100.0, market="futures")

        mon = PriceMonitor(
            settings=_settings(),
            store=store,
            price_provider=FakePriceProvider({"BTCUSDT": 999.0}),
            notifier=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not notify")),
            futures_provider=None,
        )
        mon._check_once()
        assert len(store.get_user_alerts(u)) == 1
        print("PASS: futures rows skipped when provider absent (flag-off path)")


def test_mover_history_downside_pct():
    # max_age must be > lookback so the "then" sample is retained
    h = PriceHistory(max_age_seconds=1200)
    now = time.time()
    h.record("futures", "BTC_USDT", 100.0, ts=now - 700)
    h.record("futures", "BTC_USDT", 100.0, ts=now - 100)
    h.record("futures", "BTC_USDT", 90.0, ts=now)  # -10% vs sample at-or-before now-600

    ch = h.pct_change_over("futures", "BTC_USDT", lookback_seconds=600, now=now)
    assert ch is not None
    assert abs(ch - (-0.10)) < 1e-9, ch

    # Upside should be positive (scanner will ignore)
    h2 = PriceHistory(max_age_seconds=1200)
    h2.record("futures", "ETH_USDT", 100.0, ts=now - 600)
    h2.record("futures", "ETH_USDT", 110.0, ts=now)
    ch2 = h2.pct_change_over("futures", "ETH_USDT", 600, now=now)
    assert ch2 is not None and ch2 > 0
    print("PASS: mover history % change")


def test_mover_peak_drawdown_within_window():
    """High → now within lookback (catches dumps endpoint-to-endpoint can miss)."""
    h = PriceHistory(max_age_seconds=1200)
    now = time.time()
    # Left edge only -2%, but mid-window spike then dump -8% from high
    h.record("spot", "PENGUINUSDT", 100.0, ts=now - 900)  # left edge
    h.record("spot", "PENGUINUSDT", 102.0, ts=now - 600)  # mild drift
    h.record("spot", "PENGUINUSDT", 110.0, ts=now - 120)  # peak
    h.record("spot", "PENGUINUSDT", 101.2, ts=now)  # -8% from peak; ~+1.2% vs left edge

    endpoint = h.pct_change_over("spot", "PENGUINUSDT", 900, now=now)
    assert endpoint is not None
    assert endpoint > -0.05, "endpoint alone would miss a 7% threshold dump"

    dd = h.peak_drawdown("spot", "PENGUINUSDT", 900, now=now)
    assert dd is not None
    change, peak, price_now, peak_ts = dd
    assert abs(peak - 110.0) < 1e-9
    assert abs(price_now - 101.2) < 1e-9
    assert abs(change - ((101.2 - 110.0) / 110.0)) < 1e-9
    assert change <= -0.07, change  # fires a 7% threshold
    assert abs(peak_ts - (now - 120)) < 1e-6

    # Cold start: no sample at/before window start → None
    h3 = PriceHistory(max_age_seconds=1200)
    h3.record("spot", "X", 100.0, ts=now - 30)
    h3.record("spot", "X", 90.0, ts=now)
    assert h3.peak_drawdown("spot", "X", 900, now=now) is None
    print("PASS: peak drawdown within window")


def test_mover_scanner_downside_only_and_no_spam_same_level():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 99
        mstore.set_params(u, threshold_percent=5.0, lookback_seconds=60, default_enabled=True)
        mstore.set_enabled(u, True, 5.0, 60)
        mstore.set_watchlist(u, [{"symbol": "BTC_USDT", "market": "futures"}])

        notifications = []

        def notify(uid, msg, parse_mode=None):
            notifications.append((uid, msg))

        settings = _settings(
            mover_lookback_seconds=60,
            mover_threshold_percent=5.0,
            mover_cooldown_seconds=0,  # no min-gap; step rule alone must prevent spam
        )
        fut = FakePriceProvider({"BTC_USDT": 100.0})
        scanner = MoverScanner(
            settings=settings,
            mover_store=mstore,
            notifier=notify,
            futures_provider=fut,
        )

        now = time.time()
        scanner.history.record("futures", "BTC_USDT", 100.0, ts=now - 90)
        scanner.history.record("futures", "BTC_USDT", 90.0, ts=now)  # -10%

        fut._prices = {"BTC_USDT": 90.0}
        scanner._check_once()

        assert len(notifications) == 1, notifications
        assert "MOVER" in notifications[0][1]
        assert "BTC_USDT" in notifications[0][1]
        assert "within" in notifications[0][1] or "High" in notifications[0][1]

        # Same price again → no second fire (need another full step from anchor 90)
        scanner._check_once()
        assert len(notifications) == 1, "same level must not re-fire"

        # Upside should not fire
        mstore.add_watchlist(u, "ETH_USDT", "futures")
        scanner.history.record("futures", "ETH_USDT", 100.0, ts=now - 90)
        scanner.history.record("futures", "ETH_USDT", 120.0, ts=now)
        fut._prices = {"BTC_USDT": 90.0, "ETH_USDT": 120.0}
        scanner._check_once()
        assert len(notifications) == 1, "upside must not fire"
        print("PASS: mover scanner downside-only + no spam at same level")


def test_mover_scanner_step_down_cascade():
    """After first fire, another full threshold from fire price alerts again."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 77
        mstore.set_params(u, threshold_percent=7.0, lookback_seconds=900, default_enabled=True)
        mstore.set_enabled(u, True, 7.0, 900)
        mstore.set_watchlist(u, [{"symbol": "ABC_USDT", "market": "futures"}])

        notifications = []

        def notify(uid, msg, parse_mode=None):
            notifications.append((uid, msg))

        settings = _settings(
            mover_lookback_seconds=900,
            mover_threshold_percent=7.0,
            mover_cooldown_seconds=0,
            mover_recovery_percent=3.0,
        )
        fut = FakePriceProvider({"ABC_USDT": 100.0})
        scanner = MoverScanner(
            settings=settings,
            mover_store=mstore,
            notifier=notify,
            futures_provider=fut,
        )
        now = time.time()
        # Leg 1: high 100 → 92 (−8%)
        scanner.history.record("futures", "ABC_USDT", 100.0, ts=now - 900)
        scanner.history.record("futures", "ABC_USDT", 92.0, ts=now)
        fut._prices = {"ABC_USDT": 92.0}
        scanner._check_once()
        assert len(notifications) == 1, notifications
        assert "within" in notifications[0][1]

        # Mild further drop not yet another full 7% from 92 (92 * 0.93 = 85.56)
        fut._prices = {"ABC_USDT": 88.0}
        scanner.history.record("futures", "ABC_USDT", 88.0, ts=time.time())
        scanner._check_once()
        assert len(notifications) == 1, "partial step must not fire"

        # Leg 2: below 85.56
        fut._prices = {"ABC_USDT": 85.0}
        scanner.history.record("futures", "ABC_USDT", 85.0, ts=time.time())
        scanner._check_once()
        assert len(notifications) == 2, notifications
        assert "step from last alert" in notifications[1][1]

        # Leg 3: another 7% from 85 → 79.05
        fut._prices = {"ABC_USDT": 79.0}
        scanner.history.record("futures", "ABC_USDT", 79.0, ts=time.time())
        scanner._check_once()
        assert len(notifications) == 3, notifications
        print("PASS: mover step-down cascade")


def test_mover_scanner_recovery_clears_anchor():
    """Bounce above anchor clears cascade; new peak dump can fire again."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 55
        mstore.set_params(u, threshold_percent=7.0, lookback_seconds=900, default_enabled=True)
        mstore.set_enabled(u, True, 7.0, 900)
        mstore.set_watchlist(u, [{"symbol": "XYZ_USDT", "market": "futures"}])

        notifications = []

        def notify(uid, msg, parse_mode=None):
            notifications.append((uid, msg))

        settings = _settings(
            mover_lookback_seconds=900,
            mover_threshold_percent=7.0,
            mover_cooldown_seconds=0,
            mover_recovery_percent=3.0,
        )
        fut = FakePriceProvider({"XYZ_USDT": 100.0})
        scanner = MoverScanner(
            settings=settings,
            mover_store=mstore,
            notifier=notify,
            futures_provider=fut,
        )
        t0 = time.time()
        scanner.history.record("futures", "XYZ_USDT", 100.0, ts=t0 - 900)
        scanner.history.record("futures", "XYZ_USDT", 90.0, ts=t0)
        fut._prices = {"XYZ_USDT": 90.0}
        scanner._check_once()
        assert len(notifications) == 1

        # Bounce +3% above anchor 90 → clears anchor
        fut._prices = {"XYZ_USDT": 93.0}
        scanner.history.record("futures", "XYZ_USDT", 93.0, ts=time.time())
        scanner._check_once()
        assert len(notifications) == 1
        assert (u, "futures", "XYZ_USDT") not in scanner._anchors

        # New wave: need peak in window and −7% from that peak
        # Record a higher peak then dump
        t1 = time.time()
        scanner.history.record("futures", "XYZ_USDT", 100.0, ts=t1 - 100)
        scanner.history.record("futures", "XYZ_USDT", 90.0, ts=t1)
        fut._prices = {"XYZ_USDT": 90.0}
        # Ensure left edge exists for 900s lookback
        scanner.history.record("futures", "XYZ_USDT", 100.0, ts=t1 - 900)
        scanner._check_once()
        assert len(notifications) == 2, notifications
        assert "within" in notifications[1][1]
        print("PASS: mover recovery clears anchor")


def test_mover_min_gap_blocks_rapid_repeat():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 33
        mstore.set_params(u, threshold_percent=5.0, lookback_seconds=60, default_enabled=True)
        mstore.set_enabled(u, True, 5.0, 60)
        mstore.set_watchlist(u, [{"symbol": "BTC_USDT", "market": "futures"}])
        notifications = []

        def notify(uid, msg, parse_mode=None):
            notifications.append((uid, msg))

        settings = _settings(
            mover_lookback_seconds=60,
            mover_threshold_percent=5.0,
            mover_cooldown_seconds=3600,  # long min-gap
        )
        fut = FakePriceProvider({"BTC_USDT": 90.0})
        scanner = MoverScanner(
            settings=settings,
            mover_store=mstore,
            notifier=notify,
            futures_provider=fut,
        )
        now = time.time()
        scanner.history.record("futures", "BTC_USDT", 100.0, ts=now - 90)
        scanner.history.record("futures", "BTC_USDT", 90.0, ts=now)
        fut._prices = {"BTC_USDT": 90.0}
        scanner._check_once()
        assert len(notifications) == 1

        # Big further step would qualify, but min-gap blocks
        fut._prices = {"BTC_USDT": 80.0}
        scanner.history.record("futures", "BTC_USDT", 80.0, ts=time.time())
        scanner._check_once()
        assert len(notifications) == 1, "min-gap should block rapid second fire"
        print("PASS: mover min-gap blocks rapid repeat")


def test_mover_scanner_peak_not_endpoint():
    """Dump from mid-window high fires even when endpoint change is small."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 42
        mstore.set_params(u, threshold_percent=7.0, lookback_seconds=900, default_enabled=True)
        mstore.set_enabled(u, True, 7.0, 900)
        mstore.set_watchlist(u, [{"symbol": "PENGUINUSDT", "market": "spot"}])

        notifications = []

        def notify(uid, msg, parse_mode=None):
            notifications.append((uid, msg))

        settings = _settings(
            mover_lookback_seconds=900,
            mover_threshold_percent=7.0,
            mover_cooldown_seconds=3600,
        )
        spot = FakePriceProvider({"PENGUINUSDT": 101.2})
        scanner = MoverScanner(
            settings=settings,
            mover_store=mstore,
            notifier=notify,
            spot_provider=spot,
        )
        now = time.time()
        scanner.history.record("spot", "PENGUINUSDT", 100.0, ts=now - 900)
        scanner.history.record("spot", "PENGUINUSDT", 110.0, ts=now - 120)
        scanner.history.record("spot", "PENGUINUSDT", 101.2, ts=now)

        scanner._check_once()
        assert len(notifications) == 1, notifications
        assert "PENGUINUSDT" in notifications[0][1]
        assert "[S]" in notifications[0][1]
        print("PASS: mover scanner peak drawdown (not endpoint-only)")


def test_mover_does_not_touch_alerts_table():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        store = AlertStore(path)
        mstore = MoverStore(path)
        u = 5
        store.add_alert(u, "SOLUSDT", 140.0)
        mstore.set_watchlist(u, [{"symbol": "SOL_USDT", "market": "futures"}])
        mstore.set_enabled(u, True, 5.0, 60)

        assert len(store.get_user_alerts(u)) == 1
        mstore.clear_watchlist(u)
        assert len(store.get_user_alerts(u)) == 1
        print("PASS: mover store mutations leave target alerts intact")


def test_mover_watchlist_remove_and_add():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 11
        mstore.set_watchlist(
            u,
            [
                {"symbol": "BTC_USDT", "market": "futures"},
                {"symbol": "ETH_USDT", "market": "futures"},
                {"symbol": "SOL_USDT", "market": "futures"},
            ],
        )
        n = mstore.remove_from_watchlist(u, ["ETH_USDT", "ETHUSDT"])
        assert n >= 1
        left = {i["symbol"] for i in mstore.get_watchlist(u)}
        assert left == {"BTC_USDT", "SOL_USDT"}
        mstore.add_watchlist(u, "DOGE_USDT", "futures")
        left2 = {i["symbol"] for i in mstore.get_watchlist(u)}
        assert "DOGE_USDT" in left2
        print("PASS: mover watchlist remove + add")


if __name__ == "__main__":
    test_symbol_normalization()
    test_resolve_stock_futures_against_known_list()
    test_resolve_compact_stock_ui_form()
    test_resolve_tesla_company_name_contract()
    test_existing_spot_alerts_default_market()
    test_futures_alert_isolated_price_book()
    test_futures_skipped_without_provider()
    test_mover_history_downside_pct()
    test_mover_peak_drawdown_within_window()
    test_mover_scanner_downside_only_and_no_spam_same_level()
    test_mover_scanner_step_down_cascade()
    test_mover_scanner_recovery_clears_anchor()
    test_mover_min_gap_blocks_rapid_repeat()
    test_mover_scanner_peak_not_endpoint()
    test_mover_does_not_touch_alerts_table()
    test_mover_watchlist_remove_and_add()
    print("All V3 tests passed.")
