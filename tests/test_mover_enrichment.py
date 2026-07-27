#!/usr/bin/env python3
"""Mover enrichments: velocity, heat board, reds, isolation from target alerts."""

import sys
import tempfile
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from mexc_bot.movers.heat import (
    board_fingerprint,
    heat_snapshot,
    is_widespread_panic,
)
from mexc_bot.movers.history import PriceHistory
from mexc_bot.movers.klines import consecutive_red_count
from mexc_bot.movers.scanner import MoverScanner
from mexc_bot.movers.storage import MoverStore
from mexc_bot.movers.velocity import BAND_FAST, BAND_GRIND, BAND_PANIC, score_dump
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
        "mover_cooldown_seconds": 0,
        "mover_recovery_percent": 3.0,
        "mover_markets": "futures",
        "mover_enrich_velocity": True,
        "mover_enrich_volume": False,
        "mover_enrich_klines": False,
        "mover_velocity_panic": 2.0,
        "mover_velocity_fast": 0.8,
        "mover_heat_auto": True,
        "mover_heat_on_mw": True,
        "mover_heat_breadth_min": 3,
        "mover_heat_breadth_pct": 3.0,
        "mover_heat_top_n": 5,
        "mover_heat_min_gap_seconds": 45,
        "mover_heat_refresh_seconds": 90,
        "mexc_api_base": "https://api.mexc.com/api/v3",
        "mexc_futures_api_base": "https://contract.mexc.com/api/v1",
    }
    base.update(kwargs)
    return type("S", (), base)()


def test_velocity_bands():
    # −6% in 1 minute → 6%/min → PANIC
    vel, mins, band = score_dump(0.0, 100.0, 60.0, 94.0, panic_per_min=2.0, fast_per_min=0.8)
    assert band == BAND_PANIC, (vel, mins, band)
    assert vel >= 2.0
    # −6% over 20 minutes → 0.3%/min → GRIND
    vel2, mins2, band2 = score_dump(0.0, 100.0, 1200.0, 94.0, panic_per_min=2.0, fast_per_min=0.8)
    assert band2 == BAND_GRIND, (vel2, mins2, band2)
    # −5% over 5 min → 1%/min → FAST
    vel3, _, band3 = score_dump(0.0, 100.0, 300.0, 95.0, panic_per_min=2.0, fast_per_min=0.8)
    assert band3 == BAND_FAST, (vel3, band3)
    print("PASS: velocity bands")


def test_consecutive_reds():
    # oldest → newest; last 3 red after green
    candles = [(10, 11), (11, 10), (10, 9), (9, 8)]  # G R R R
    assert consecutive_red_count(candles) == 3
    assert consecutive_red_count([(10, 11), (11, 12)]) == 0
    assert consecutive_red_count([]) == 0
    print("PASS: consecutive red count")


def test_heat_rank_and_breadth():
    h = PriceHistory(max_age_seconds=1200)
    now = time.time()
    # A worst dump, B medium, C flat
    for name, end in [("A_USDT", 90.0), ("B_USDT", 95.0), ("C_USDT", 100.0)]:
        h.record("futures", name, 100.0, ts=now - 90)
        h.record("futures", name, end, ts=now)
    wl = [
        {"symbol": "A_USDT", "market": "futures"},
        {"symbol": "B_USDT", "market": "futures"},
        {"symbol": "C_USDT", "market": "futures"},
    ]
    board = heat_snapshot(h, wl, 60, now=now, breadth_pct=3.0)
    assert board.ranked[0].symbol == "A_USDT"
    assert board.ranked[0].dd_pct < board.ranked[1].dd_pct
    assert board.dumping_count == 2  # A -10%, B -5%, C 0% with breadth 3%
    assert is_widespread_panic(board, 2)
    assert not is_widespread_panic(board, 3)
    fp1 = board_fingerprint(board.ranked, 3)
    fp2 = board_fingerprint(board.ranked, 3)
    assert fp1 == fp2
    print("PASS: heat rank and breadth")


def test_auto_heat_board_pushes_without_command():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 501
        mstore.set_params(u, threshold_percent=5.0, lookback_seconds=60, default_enabled=True)
        mstore.set_enabled(u, True, 5.0, 60)
        mstore.set_watchlist(
            u,
            [
                {"symbol": "A_USDT", "market": "futures"},
                {"symbol": "B_USDT", "market": "futures"},
                {"symbol": "C_USDT", "market": "futures"},
            ],
        )

        notes = []

        def notify(uid, msg, parse_mode=None):
            notes.append((uid, msg, parse_mode))

        settings = _settings(
            mover_heat_breadth_min=3,
            mover_heat_breadth_pct=4.0,
            mover_threshold_percent=5.0,
            mover_cooldown_seconds=0,
        )
        fut = FakePriceProvider(
            {"A_USDT": 90.0, "B_USDT": 91.0, "C_USDT": 92.0}
        )
        scanner = MoverScanner(
            settings=settings,
            mover_store=mstore,
            notifier=notify,
            futures_provider=fut,
        )
        now = time.time()
        for sym, px in [("A_USDT", 100.0), ("B_USDT", 100.0), ("C_USDT", 100.0)]:
            scanner.history.record("futures", sym, px, ts=now - 90)
            scanner.history.record("futures", sym, fut._prices[sym], ts=now)

        scanner._check_once()

        boards = [n for n in notes if "PANIC BOARD" in n[1]]
        assert len(boards) >= 1, notes
        assert boards[0][2] == "HTML"
        # Second cycle same fingerprint soon → no spam board
        n_before = len([n for n in notes if "PANIC BOARD" in n[1]])
        scanner._check_once()
        n_after = len([n for n in notes if "PANIC BOARD" in n[1]])
        assert n_after == n_before, "heat board must not spam same fingerprint"
        print("PASS: auto heat board without /mw")


def test_mover_fire_includes_velocity_not_touch_alerts():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        # Seed a real target alert that must survive
        astore = AlertStore(path)
        astore.add_alert(99, "BTCUSDT", 65000.0, market="spot")
        assert len(astore.get_user_alerts(99)) == 1

        mstore = MoverStore(path)
        u = 99
        mstore.set_params(u, threshold_percent=5.0, lookback_seconds=60, default_enabled=True)
        mstore.set_enabled(u, True, 5.0, 60)
        mstore.set_watchlist(u, [{"symbol": "BTC_USDT", "market": "futures"}])

        notes = []

        def notify(uid, msg, parse_mode=None):
            notes.append(msg)

        scanner = MoverScanner(
            settings=_settings(mover_heat_auto=False, mover_enrich_velocity=True),
            mover_store=mstore,
            notifier=notify,
            futures_provider=FakePriceProvider({"BTC_USDT": 90.0}),
        )
        now = time.time()
        # Sharp dump: peak 2 min ago
        scanner.history.record("futures", "BTC_USDT", 100.0, ts=now - 90)
        scanner.history.record("futures", "BTC_USDT", 100.0, ts=now - 120)
        scanner.history.record("futures", "BTC_USDT", 90.0, ts=now)
        scanner._check_once()

        assert notes, "expected mover fire"
        assert "MOVER" in notes[0]
        assert "Velocity" in notes[0] or "PANIC" in notes[0] or "FAST" in notes[0] or "GRIND" in notes[0]
        # Alerts table untouched
        assert len(astore.get_user_alerts(99)) == 1
        assert astore.get_user_alerts(99)[0]["symbol"] == "BTCUSDT"
        print("PASS: velocity on fire; alerts table isolated")


def test_quiet_market_no_heat_board():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "alerts.db"
        mstore = MoverStore(path)
        u = 3
        mstore.set_enabled(u, True, 5.0, 60)
        mstore.set_watchlist(
            u,
            [
                {"symbol": "A_USDT", "market": "futures"},
                {"symbol": "B_USDT", "market": "futures"},
                {"symbol": "C_USDT", "market": "futures"},
            ],
        )
        notes = []
        scanner = MoverScanner(
            settings=_settings(mover_heat_breadth_min=3, mover_heat_breadth_pct=5.0),
            mover_store=mstore,
            notifier=lambda *a, **k: notes.append(a),
            futures_provider=FakePriceProvider(
                {"A_USDT": 100.0, "B_USDT": 100.0, "C_USDT": 100.0}
            ),
        )
        now = time.time()
        for sym in ("A_USDT", "B_USDT", "C_USDT"):
            scanner.history.record("futures", sym, 100.0, ts=now - 90)
            scanner.history.record("futures", sym, 99.0, ts=now)  # −1% only
        scanner._check_once()
        boards = [n for n in notes if n and "PANIC BOARD" in str(n)]
        assert not boards, boards
        print("PASS: quiet market no heat board")


if __name__ == "__main__":
    test_velocity_bands()
    test_consecutive_reds()
    test_heat_rank_and_breadth()
    test_auto_heat_board_pushes_without_command()
    test_mover_fire_includes_velocity_not_touch_alerts()
    test_quiet_market_no_heat_board()
    print("\nAll mover enrichment tests passed.")
