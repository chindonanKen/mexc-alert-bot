#!/usr/bin/env python3
"""Same-price / micro-move mover dedupe (Telegram spam guard)."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent.parent))

from mexc_bot.movers.scanner import MoverScanner
from mexc_bot.movers.storage import MoverStore


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
        "mover_lookback_seconds": 900,
        "mover_threshold_percent": 5.0,
        "mover_poll_seconds": 5,
        "mover_cooldown_seconds": 0,
        "mover_recovery_percent": 3.0,
        "mover_dedupe_price_eps": 0.002,
        "mover_dedupe_window_seconds": 120.0,
        "mover_markets": "futures",
        "mover_enrich_velocity": False,
        "mover_enrich_volume": False,
        "mover_enrich_klines": False,
        "mover_heat_auto": False,
    }
    base.update(kwargs)
    return type("S", (), base)()


def test_same_price_within_window_suppressed():
    """Peak fire then micro-move still near last fire price → no second Telegram."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "a.db"
        mstore = MoverStore(path)
        u = 42
        mstore.set_params(u, threshold_percent=5.0, lookback_seconds=900, default_enabled=True)
        mstore.set_enabled(u, True, 5.0, 900)
        mstore.set_watchlist(u, [{"symbol": "BLUAI_USDT", "market": "futures"}])

        notes = []

        def notify(uid, msg, parse_mode=None, reply_markup=None):
            notes.append(msg)

        settings = _settings()
        fut = FakePriceProvider({"BLUAI_USDT": 0.02})
        scanner = MoverScanner(
            settings=settings, mover_store=mstore, notifier=notify, futures_provider=fut
        )
        now = time.time()
        scanner.history.record("futures", "BLUAI_USDT", 0.022, ts=now - 900)
        scanner.history.record("futures", "BLUAI_USDT", 0.022, ts=now - 100)
        scanner.history.record("futures", "BLUAI_USDT", 0.02, ts=now)  # ~-9%
        fut._prices = {"BLUAI_USDT": 0.02}
        scanner._check_once()
        assert len(notes) == 1, notes

        # Tiny move (0.1%) — step threshold not met; even if it were, eps blocks
        fut._prices = {"BLUAI_USDT": 0.01998}
        scanner.history.record("futures", "BLUAI_USDT", 0.01998, ts=time.time())
        scanner._check_once()
        assert len(notes) == 1, "micro-move must not spam"

        # Force step-qualified price but within eps of last fire (simulate float noise)
        # last fire 0.02; eps 0.2% → 0.01996 still within eps
        key = list(scanner._last_fire_price.keys())[0]
        scanner._anchors[key] = 0.021  # fake anchor so step would want to fire
        # 0.02 is still within 0.2% of last fire 0.02
        fut._prices = {"BLUAI_USDT": 0.02}
        scanner.history.record("futures", "BLUAI_USDT", 0.02, ts=time.time())
        # step from 0.021 to 0.02 is only ~4.7% < 5% — not enough
        # Use anchor high enough for step but price ~ last fire
        scanner._anchors[key] = 0.022
        # 0.02 is -9% from 0.022 → step qualifies; price == last fire → eps suppress
        fut._prices = {"BLUAI_USDT": 0.02}
        scanner._check_once()
        assert len(notes) == 1, f"same-price step must suppress: {notes}"
        assert scanner._fires_suppressed >= 1
        print("PASS: same-price within window suppressed")


def test_true_step_beyond_eps_still_fires():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "a.db"
        mstore = MoverStore(path)
        u = 43
        mstore.set_params(u, threshold_percent=5.0, lookback_seconds=900, default_enabled=True)
        mstore.set_enabled(u, True, 5.0, 900)
        mstore.set_watchlist(u, [{"symbol": "ABC_USDT", "market": "futures"}])
        notes = []

        def notify(uid, msg, parse_mode=None, reply_markup=None):
            notes.append(msg)

        scanner = MoverScanner(
            settings=_settings(),
            mover_store=mstore,
            notifier=notify,
            futures_provider=FakePriceProvider({"ABC_USDT": 100.0}),
        )
        now = time.time()
        scanner.history.record("futures", "ABC_USDT", 100.0, ts=now - 900)
        scanner.history.record("futures", "ABC_USDT", 90.0, ts=now)
        scanner.futures_provider._prices = {"ABC_USDT": 90.0}
        scanner._check_once()
        assert len(notes) == 1

        # Full step from 90 → 85 (beyond eps vs last fire 90)
        scanner.futures_provider._prices = {"ABC_USDT": 85.0}
        scanner.history.record("futures", "ABC_USDT", 85.0, ts=time.time())
        scanner._check_once()
        assert len(notes) == 2, notes
        assert "step" in notes[1].lower() or "Last" in notes[1]
        print("PASS: true step beyond eps still fires")


def test_outside_window_allows_near_price():
    """After dedupe window expires, a new dump near old price can fire (peak)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "a.db"
        mstore = MoverStore(path)
        u = 44
        mstore.set_params(u, threshold_percent=5.0, lookback_seconds=900, default_enabled=True)
        mstore.set_enabled(u, True, 5.0, 900)
        mstore.set_watchlist(u, [{"symbol": "ZZ_USDT", "market": "futures"}])
        notes = []

        def notify(uid, msg, parse_mode=None, reply_markup=None):
            notes.append(msg)

        settings = _settings(mover_dedupe_window_seconds=1.0)
        scanner = MoverScanner(
            settings=settings,
            mover_store=mstore,
            notifier=notify,
            futures_provider=FakePriceProvider({"ZZ_USDT": 10.0}),
        )
        now = time.time()
        scanner.history.record("futures", "ZZ_USDT", 11.0, ts=now - 900)
        scanner.history.record("futures", "ZZ_USDT", 10.0, ts=now)
        scanner.futures_provider._prices = {"ZZ_USDT": 10.0}
        scanner._check_once()
        assert len(notes) == 1

        # Expire window + recovery clear + new peak
        key = list(scanner._last_fire_price.keys())[0]
        scanner._last_fire_wall[key] = time.time() - 5.0
        scanner._anchors.pop(key, None)
        t1 = time.time()
        scanner.history.record("futures", "ZZ_USDT", 11.0, ts=t1 - 900)
        scanner.history.record("futures", "ZZ_USDT", 10.0, ts=t1)
        scanner.futures_provider._prices = {"ZZ_USDT": 10.0}
        scanner._check_once()
        assert len(notes) == 2, notes
        print("PASS: outside window allows re-fire")


if __name__ == "__main__":
    test_same_price_within_window_suppressed()
    test_true_step_beyond_eps_still_fires()
    test_outside_window_allows_near_price()
    print("ALL DEDUPE TESTS PASSED")
