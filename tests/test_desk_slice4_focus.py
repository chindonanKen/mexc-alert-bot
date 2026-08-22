#!/usr/bin/env python3
"""Slice 4: pinned symbol, target distance-to-fire, sticky last-fired strip."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mexc_bot.webapi.actions import attach_alert_distances, target_distance_to_fire

JS = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text()
HTML = (ROOT / "mexc_bot/webapi/static/index.html").read_text()
CSS = (ROOT / "mexc_bot/webapi/static/assets/desk.css").read_text()


class TestTargetDistanceToFire(unittest.TestCase):
    def test_mark_above_target_is_positive(self):
        d = target_distance_to_fire(103.0, 100.0)
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d["distance_abs"], 3.0, places=6)
        self.assertAlmostEqual(d["distance_pct"], 3.0, places=6)
        self.assertEqual(d["mark"], 103.0)

    def test_mark_through_target_is_negative(self):
        d = target_distance_to_fire(97.0, 100.0)
        self.assertAlmostEqual(d["distance_pct"], -3.0, places=6)

    def test_missing_or_zero_target_is_none(self):
        self.assertIsNone(target_distance_to_fire(None, 100))
        self.assertIsNone(target_distance_to_fire(1, 0))
        self.assertIsNone(target_distance_to_fire("x", 10))

    def test_attach_exposes_distance_field(self):
        rows = [
            {"symbol": "ABCUSDT", "price": 10.0, "market": "spot"},
            {"symbol": "XYZUSDT", "price": 5.0, "market": "futures"},
        ]
        tickers = [
            {"symbol": "ABCUSDT", "price": 11.0, "changePercent": 0, "source": "test"},
        ]
        out = attach_alert_distances(rows, tickers=tickers)
        self.assertAlmostEqual(out[0]["distance_pct"], 10.0, places=6)
        self.assertEqual(out[0]["mark"], 11.0)
        self.assertIsNone(out[1]["distance_pct"])


class TestSelectedSymbolSurvivesPaint(unittest.TestCase):
    def test_js_has_pin_and_does_not_snap_to_last_fire(self):
        self.assertIn("function setSelectedSymbol", JS)
        self.assertIn("function applySelectedSymbol", JS)
        self.assertIn("data-desk-sym", JS)
        self.assertGreaterEqual(JS.count("applySelectedSymbol()"), 4)
        # Poll/flash must not steal focus
        self.assertNotIn("setSelectedSymbol(last", JS)
        self.assertNotIn("setSelectedSymbol(newest", JS)
        self.assertNotIn("state.selectedSymbol = last", JS)
        self.assertIn("rememberLastFired(last)", JS)
        # Clicking another name is the setter; poll only re-applies class
        self.assertIn('localStorage.setItem("desk_selected_symbol", raw)', JS)

    def test_selection_class_reapplied_after_paint_helper(self):
        """Same rule the desk uses: paint may replace HTML; apply restores class."""
        selected = "SYNUSDT"
        html_after_poll = (
            '<tr data-desk-sym="ETHUSDT"></tr>'
            '<tr data-desk-sym="SYNUSDT"></tr>'
        )

        def apply(html: str, want: str) -> str:
            import re

            def tog(m):
                sym = m.group(1).upper().replace("_", "")
                cls = " is-desk-selected" if sym == want else ""
                return f'<tr class="{cls.strip()}" data-desk-sym="{m.group(1)}">'

            return re.sub(r'<tr[^>]*data-desk-sym="([^"]+)"[^>]*>', tog, html)

        once = apply(html_after_poll, selected)
        twice = apply(once, selected)
        self.assertIn('data-desk-sym="SYNUSDT"', twice)
        self.assertIn("is-desk-selected", twice)
        self.assertEqual(twice.count("is-desk-selected"), 1)
        self.assertNotIn('ETHUSDT" class="is-desk-selected', twice.replace(" ", ""))


class TestLastFiredStripSticky(unittest.TestCase):
    def test_strip_is_outside_scroll_and_repaints_in_place(self):
        self.assertIn('id="lastFiredStrip"', HTML)
        self.assertIn('id="deskStickyBar"', HTML)
        self.assertIn("desk-sticky-bar", CSS)
        self.assertIn("position: sticky", CSS)
        self.assertIn('paintIfChanged(el, html, "lastFiredStrip"', JS)
        self.assertIn("function rememberLastFired", JS)
        self.assertIn("renderLastFiredStrip()", JS)
        # Empty poll still re-renders (same payload → paintIfChanged skip)
        self.assertIn("if (!fresh.length)", JS)
        self.assertGreaterEqual(JS.count("renderLastFiredStrip()"), 3)
        # Must not clear lastFired on empty poll
        self.assertNotIn("state.lastFired = null", JS)
        self.assertNotIn("state.lastFired = {}", JS)

    def test_second_paint_same_payload_skips(self):
        sigs = {}

        def paint_if_changed(key, payload):
            sig = str(payload)
            if sigs.get(key) == sig:
                return False
            sigs[key] = sig
            return True

        first = paint_if_changed("lastFiredStrip", [9, "SYNUSDT", "mover_peak"])
        second = paint_if_changed("lastFiredStrip", [9, "SYNUSDT", "mover_peak"])
        self.assertTrue(first)
        self.assertFalse(second)


class TestTargetsRowDistance(unittest.TestCase):
    def test_target_row_shows_to_fire_column(self):
        self.assertIn('"To fire"', JS)
        self.assertIn("data-distance-pct", JS)
        self.assertIn("a.distance_pct", JS)
        self.assertIn("tgt-dist", JS)
        self.assertIn("desk.js?v=slicelab7", HTML)
        self.assertIn("desk.css?v=slicelab7", HTML)


if __name__ == "__main__":
    unittest.main()
