#!/usr/bin/env python3
"""Slice 3: desk fire sound hook + one Telegram ping per full open/exit."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.learning.fill_lifecycle import (
    format_lifecycle_message,
    lifecycle_events_from_fills,
    send_lifecycle_telegram,
)
from mexc_bot.learning.fills import FillSyncPoller
from mexc_bot.learning.store import EventStore


def _fill(*, tid, symbol="ABCUSDT", market="spot", side="buy", qty, price=1.0, ts=1000, raw):
    return {
        "user_id": 99,
        "exchange_trade_id": tid,
        "symbol": symbol,
        "market": market,
        "side": side,
        "price": price,
        "qty": qty,
        "quote_qty": price * qty,
        "ts": ts,
        "raw": raw,
    }


def _partial_raw(oid="ord-1", filled=4, orig=10):
    return {
        "orderId": oid,
        "origQty": orig,
        "executedQty": filled,
        "status": "PARTIALLY_FILLED",
    }


def _filled_raw(oid="ord-1", filled=10, orig=10):
    return {
        "orderId": oid,
        "origQty": orig,
        "executedQty": filled,
        "status": "FILLED",
    }


class TestLifecycleTelegram(unittest.TestCase):
    def test_full_open_one_send(self):
        new = [
            _fill(tid="a", qty=5, ts=1, raw=_partial_raw(filled=5)),
            _fill(tid="b", qty=5, ts=2, raw=_filled_raw()),
        ]
        evs = lifecycle_events_from_fills([], new)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "opened")
        sink = []

        def notifier(uid, text, **kwargs):
            sink.append((uid, text, kwargs))

        n = send_lifecycle_telegram(notifier, 99, evs, enabled=True)
        self.assertEqual(n, 1)
        self.assertEqual(len(sink), 1)
        self.assertEqual(sink[0][0], 99)
        self.assertIn("POSITION OPENED", sink[0][1])
        self.assertNotIn("api.telegram.org", sink[0][1])

    def test_full_exit_one_send(self):
        opened = [_fill(tid="buy1", qty=10, ts=1, raw=_filled_raw(oid="o-buy"))]
        exit_fills = [
            _fill(
                tid="s1",
                side="sell",
                qty=10,
                ts=3,
                raw=_filled_raw(oid="o-sell"),
            )
        ]
        evs = lifecycle_events_from_fills(opened, exit_fills)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "exited")
        sink = []
        n = send_lifecycle_telegram(
            lambda uid, text, **kw: sink.append(text), 7, evs, enabled=True
        )
        self.assertEqual(n, 1)
        self.assertIn("POSITION EXITED", sink[0])

    def test_partial_fill_zero_telegram(self):
        new = [_fill(tid="p", qty=4, raw=_partial_raw())]
        evs = lifecycle_events_from_fills([], new)
        self.assertEqual(evs, [])
        sink = []
        n = send_lifecycle_telegram(
            lambda *a, **k: sink.append(1), 99, evs, enabled=True
        )
        self.assertEqual(n, 0)
        self.assertEqual(sink, [])

    def test_already_complete_order_does_not_reping(self):
        done = [
            _fill(tid="a", qty=5, ts=1, raw=_partial_raw(filled=5)),
            _fill(tid="b", qty=5, ts=2, raw=_filled_raw()),
        ]
        evs = lifecycle_events_from_fills(done, [])
        self.assertEqual(evs, [])
        # A later poll that only re-sees the same complete order
        evs2 = lifecycle_events_from_fills(done, [])
        self.assertEqual(evs2, [])

    def test_partial_then_rest_is_one_open(self):
        first = [_fill(tid="a", qty=4, ts=1, raw=_partial_raw(filled=4))]
        self.assertEqual(lifecycle_events_from_fills([], first), [])
        rest = [_fill(tid="b", qty=6, ts=2, raw=_filled_raw(filled=10))]
        evs = lifecycle_events_from_fills(first, rest)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "opened")
        self.assertAlmostEqual(float(evs[0]["qty"]), 10.0, places=5)

    def test_disabled_or_missing_notifier_never_sends(self):
        evs = lifecycle_events_from_fills(
            [], [_fill(tid="a", qty=10, raw=_filled_raw())]
        )
        self.assertEqual(len(evs), 1)
        self.assertEqual(send_lifecycle_telegram(None, 99, evs, enabled=True), 0)
        sink = []
        self.assertEqual(
            send_lifecycle_telegram(lambda *a, **k: sink.append(1), 99, evs, enabled=False),
            0,
        )
        self.assertEqual(sink, [])

    def test_message_is_plain_open_or_exit(self):
        ev = {
            "kind": "opened",
            "side": "buy",
            "symbol": "ABCUSDT",
            "market": "spot",
            "qty": 10,
            "price": 1.1,
        }
        text = format_lifecycle_message(ev)
        self.assertIn("POSITION OPENED", text)
        self.assertIn("ABCUSDT", text)


class TestFillSyncPollerSink(unittest.TestCase):
    """Poller send path uses the injected sink — never Telegram HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "a.db")
        self.sink = []

        def notifier(uid, text, **kwargs):
            self.sink.append((uid, text, kwargs))

        client = MagicMock()
        client.configured = False
        self.poller = FillSyncPoller(
            self.store,
            client,
            99,
            get_symbols=lambda: set(),
            notifier=notifier,
            notify_on_new=True,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _insert(self, row):
        kw = dict(row)
        self.store.insert_fill(**kw)
        return row

    def test_poller_partial_silent_full_open_one_ping(self):
        p = self._insert(_fill(tid="p1", qty=4, ts=10, raw=_partial_raw()))
        self.assertEqual(self.poller.notify_new_fills([p]), 0)
        self.assertEqual(self.sink, [])
        a = self._insert(
            _fill(tid="p2", qty=6, ts=11, raw=_filled_raw(filled=10))
        )
        self.assertEqual(self.poller.notify_new_fills([a]), 1)
        self.assertEqual(len(self.sink), 1)
        self.assertIn("POSITION OPENED", self.sink[0][1])

    def test_poller_full_exit_one_ping(self):
        self._insert(_fill(tid="b1", qty=10, ts=1, raw=_filled_raw(oid="ob")))
        s = self._insert(
            _fill(
                tid="s1",
                side="sell",
                qty=10,
                ts=2,
                raw=_filled_raw(oid="os"),
            )
        )
        self.assertEqual(self.poller.notify_new_fills([s]), 1)
        self.assertEqual(len(self.sink), 1)
        self.assertIn("POSITION EXITED", self.sink[0][1])

    def test_notify_off_is_silent(self):
        self.poller.notify_on_new = False
        row = self._insert(_fill(tid="x", qty=10, raw=_filled_raw(oid="ox")))
        self.assertEqual(self.poller.notify_new_fills([row]), 0)
        self.assertEqual(self.sink, [])


class TestDeskFireSoundHook(unittest.TestCase):
    def test_js_plays_on_mover_and_target(self):
        js = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
        self.assertIn("function playAlarmSound", js)
        self.assertIn("function playAlarmBeeps", js)
        self.assertIn("function alarmIsMoverOrTarget", js)
        self.assertIn('s === "target"', js)
        self.assertIn('s === "mover_peak"', js)
        self.assertIn('s === "mover_step"', js)
        self.assertIn("const fireFresh = fresh.filter(alarmIsMoverOrTarget)", js)
        self.assertIn("if (fireFresh.length) playAlarmSound()", js)
        # Must be a multi-pulse alarm, not the old 0.3s sine chirp
        self.assertIn("Three square pulses", js)
        self.assertIn("[880, 1174.7, 1396.9]", js)
        self.assertNotIn("o.frequency.exponentialRampToValueAtTime(1320", js)
        self.assertIn("function alarmWavDataUri", js)
        html = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
        self.assertIn('id="alarmSound"', html)
        self.assertIn("desk.js?v=slicelab7b", html)

    def test_alarms_api_is_mover_and_target_only(self):
        src = (ROOT / "mexc_bot/webapi/app.py").read_text()
        self.assertIn("@app.get(\"/api/desk/alarms\")", src)
        self.assertIn("mover_peak", src)
        self.assertIn("mover_step", src)
        self.assertIn("'target'", src)
        # Fire decision path is not rewritten here
        self.assertNotIn("def should_fire", src)


if __name__ == "__main__":
    unittest.main()
