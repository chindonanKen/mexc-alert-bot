#!/usr/bin/env python3
"""Wick-aware dump fire: 15m high → later 1m low (ACU 10:45 Manila miss)."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.movers.history import wick_drawdown
from mexc_bot.movers.scanner import MoverScanner
from mexc_bot.movers.storage import MoverStore


def _settings(**kwargs):
    base = {
        "mover_lookback_seconds": 900,
        "mover_threshold_percent": 7.0,
        "mover_poll_seconds": 5,
        "mover_cooldown_seconds": 0,
        "mover_recovery_percent": 3.0,
        "mover_markets": "both",
        "mover_wick_fire": True,
        "mover_enrich_klines": False,
        "mover_enrich_velocity": False,
        "mover_enrich_volume": False,
        "mover_heat_auto": False,
    }
    base.update(kwargs)
    return type("S", (), base)()


class FakePrices:
    def __init__(self, prices):
        self._prices = prices

    def get_all_prices(self):
        return dict(self._prices)

    def close(self):
        pass


def test_wick_drawdown_same_bar_counts():
    now = 1_700_000_000.0
    bars = [{"ts": now - 30, "h": 0.105, "l": 0.096}]  # −8.57% same minute
    dd = wick_drawdown(bars, 900, now=now)
    assert dd is not None
    change, peak, trough, _, _trough_ts = dd
    assert abs(peak - 0.105) < 1e-9
    assert abs(trough - 0.096) < 1e-9
    assert change <= -0.07


def test_low_before_high_is_not_a_dump():
    now = 1_700_000_000.0
    bars = [
        {"ts": now - 600, "h": 0.10, "l": 0.09},  # old low
        {"ts": now - 60, "h": 0.12, "l": 0.119},  # new high, tiny dip
    ]
    dd = wick_drawdown(bars, 900, now=now)
    assert dd is not None
    change, peak, trough, _, _ts = dd
    assert abs(peak - 0.12) < 1e-9
    assert change > -0.07  # must not fire on the earlier 0.09


def test_acu_1045_manila_wick_would_fire():
    """Replay 2026-08-14 02:27–02:57 UTC spot 1m (10:45 Manila dump)."""
    # 02:37 high 0.10518 is inside the 15m window of the 02:50 wick (0.09720)
    t_high = 1_786_669_020.0  # 02:37 UTC
    bars = [
        {"ts": t_high, "h": 0.10518, "l": 0.10465},
        {"ts": t_high + 7 * 60, "h": 0.10500, "l": 0.09983},  # 02:44
        {"ts": t_high + 13 * 60, "h": 0.10388, "l": 0.09720},  # 02:50 wick
        {"ts": t_high + 20 * 60, "h": 0.09930, "l": 0.09572},  # 02:57
    ]
    now = t_high + 13 * 60 + 40  # still in the 02:50 minute
    last = 0.10040  # close bounced — last-price vs 0.10518 = −4.5%
    dd = wick_drawdown(bars, 900, now=now, extra_prices=[(now, last)])
    assert dd is not None
    change, peak, trough, _, _ts = dd
    assert peak >= 0.1051
    assert trough <= 0.0973
    assert change <= -0.07, change


def test_scanner_fires_wick_when_last_price_recovered():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "t.db"
        store = MoverStore(path)
        u = 1
        store.set_params(u, threshold_percent=7.0, lookback_seconds=900, default_enabled=True)
        store.set_enabled(u, True, 7.0, 900)
        store.set_watchlist(u, [{"symbol": "ACUUSDT", "market": "spot"}])
        notes = []

        def notify(uid, msg, parse_mode=None, reply_markup=None):
            notes.append(msg)

        now = time.time()
        scanner = MoverScanner(
            settings=_settings(),
            mover_store=store,
            notifier=notify,
            spot_provider=FakePrices({"ACUUSDT": 0.099}),
        )
        # Last-price series: high 0.105, now 0.099 = −5.7% (under 7%)
        scanner.history.record("spot", "ACUUSDT", 0.105, ts=now - 800)
        scanner.history.record("spot", "ACUUSDT", 0.099, ts=now)
        scanner._wick_cache[("spot", "ACUUSDT")] = [
            {"ts": now - 120, "h": 0.1052, "l": 0.1050},
            {"ts": now - 50, "h": 0.1040, "l": 0.0960},  # wick −8.7% from 0.1052
        ]
        scanner._check_once()
        assert notes, "wick dump must fire even if last price bounced"
        assert "ACUUSDT" in notes[0]
        n = len(notes)
        scanner._check_once()
        scanner._check_once()
        assert len(notes) == n, "same wick must not spam after bounce"


def test_deep_dump_bounce_does_not_repeak():
    """VELVET-style: −35% then +5% bounce must stay on step, not replay peak."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "t.db"
        store = MoverStore(path)
        u = 2
        store.set_params(u, threshold_percent=7.0, lookback_seconds=900, default_enabled=True)
        store.set_enabled(u, True, 7.0, 900)
        store.set_watchlist(u, [{"symbol": "VELVETUSDT", "market": "spot"}])
        notes = []

        def notify(uid, msg, parse_mode=None, reply_markup=None):
            notes.append(msg)

        now = time.time()
        scanner = MoverScanner(
            settings=_settings(mover_wick_fire=False),
            mover_store=store,
            notifier=notify,
            spot_provider=FakePrices({"VELVETUSDT": 0.65}),
        )
        scanner.history.record("spot", "VELVETUSDT", 1.00, ts=now - 900)
        scanner.history.record("spot", "VELVETUSDT", 1.00, ts=now - 800)
        scanner.history.record("spot", "VELVETUSDT", 0.65, ts=now)
        scanner._check_once()
        assert len(notes) == 1
        # Bounce +5% off the low, still −31% from the 15m high
        scanner.spot_provider._prices = {"VELVETUSDT": 0.68}
        scanner.history.record("spot", "VELVETUSDT", 0.68, ts=now + 10)
        scanner._check_once()
        scanner._check_once()
        assert len(notes) == 1, "bounce inside the hole must not re-peak"
        assert any(k[2] == "spot" and k[3] == "VELVETUSDT" for k in scanner._anchors)


def test_stale_wick_does_not_fire():
    now = time.time()
    # Low is 10 minutes old — still in 15m window, but not "the move"
    bars = [
        {"ts": now - 700, "h": 0.105, "l": 0.105},
        {"ts": now - 600, "h": 0.104, "l": 0.096},
        {"ts": now - 30, "h": 0.101, "l": 0.100},
    ]
    dd = wick_drawdown(bars, 900, now=now)
    assert dd is not None
    trough_ts = dd[4]
    assert now - trough_ts > 90


if __name__ == "__main__":
    test_wick_drawdown_same_bar_counts()
    test_low_before_high_is_not_a_dump()
    test_acu_1045_manila_wick_would_fire()
    test_scanner_fires_wick_when_last_price_recovered()
    test_deep_dump_bounce_does_not_repeak()
    test_stale_wick_does_not_fire()
    print("PASS: wick fire")
