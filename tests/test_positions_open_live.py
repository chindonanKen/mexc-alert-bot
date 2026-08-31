#!/usr/bin/env python3
"""Prove Positions Open live poll (O1–O5). Do not change leftover remaining-cost."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
JS = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text(encoding="utf-8")
HTML = (ROOT / "mexc_bot/webapi/static/index.html").read_text(encoding="utf-8")

from mexc_bot.webapi.position_math import (
    apply_open_mark_math,
    remaining_cost_average,
)


def _old_pos_fingerprint(positions):
    """desk.js posFingerprint BEFORE this fix (status / % / size / flags only)."""
    parts = []
    for p in positions or []:
        parts.append(
            f"{p.get('entity_key') or p.get('symbol')}:{p.get('status')}:"
            f"{p.get('realized_pnl_usd', '')}:{p.get('upnl_pct', '')}:"
            f"{p.get('size_remaining', '')}:{p.get('outcome') or ''}:"
            f"{p.get('position_book') or ''}:"
            f"{1 if p.get('is_hold') else 0}:{1 if p.get('free_coins') else 0}:"
            f"{p.get('free_coins_override') or ''}"
        )
    return "|".join(parts)


def _pos_live_n(v):
    if v is None or v == "":
        return ""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n == int(n) and abs(n) < 1e15:
        # JS String(n) for 10 is "10"; for 10.5 is "10.5"
        if float(n) == int(n):
            return str(int(n))
    return str(n).rstrip("0").rstrip(".") if "." in str(n) else str(n)


def _new_pos_fingerprint(positions):
    """Mirrors desk.js posFingerprint after O1 (includes mark / leftover / In Out / uPnL $)."""
    parts = []
    for p in positions or []:
        mark = p.get("mark_price") if p.get("mark_price") is not None else p.get("mark")
        leftover_avg = (
            p.get("remaining_avg")
            if p.get("remaining_avg") is not None
            else p.get("leftover_avg")
        )
        parts.append(
            f"{p.get('entity_key') or p.get('symbol')}:{p.get('status')}:"
            f"{_pos_live_n(p.get('realized_pnl_usd'))}:"
            f"{_pos_live_n(p.get('upnl_pct'))}:"
            f"{_pos_live_n(p.get('upnl_usd_est'))}:"
            f"{_pos_live_n(mark)}:"
            f"{_pos_live_n(p.get('remaining_mark_usd'))}:"
            f"{_pos_live_n(p.get('remaining_cost_usd'))}:"
            f"{_pos_live_n(leftover_avg)}:"
            f"{_pos_live_n(p.get('bought_usd'))}:"
            f"{_pos_live_n(p.get('sold_usd'))}:"
            f"{_pos_live_n(p.get('size_remaining'))}:"
            f"{p.get('outcome') or ''}:"
            f"{p.get('position_book') or ''}:"
            f"{1 if p.get('is_hold') else 0}:{1 if p.get('free_coins') else 0}:"
            f"{p.get('free_coins_override') or ''}"
        )
    return "|".join(parts)


def _syn_open(*, mark, upnl_usd=0.0, remaining_mark=None):
    rem = 4352.0
    leftover_avg = -0.046
    return {
        "entity_key": "fopen:SYN_USDT",
        "symbol": "SYN_USDT",
        "status": "open",
        "is_open": True,
        "realized_pnl_usd": 0,
        "upnl_pct": None,  # leftover < 0 used to leave % empty
        "upnl_usd_est": upnl_usd,
        "mark_price": mark,
        "remaining_mark_usd": remaining_mark if remaining_mark is not None else rem * mark,
        "remaining_cost_usd": leftover_avg * rem,
        "remaining_avg": leftover_avg,
        "leftover_avg": leftover_avg,
        "bought_usd": 1000.0,
        "sold_usd": 1200.0,
        "size_remaining": rem,
        "outcome": "open",
        "position_book": "ad",
        "is_hold": False,
        "free_coins": True,
        "free_coins_override": "",
    }


class TestO1FingerprintMarkTick(unittest.TestCase):
    def test_mark_only_tick_would_skip_before_and_paints_now(self):
        before = _syn_open(mark=0.10, upnl_usd=0.0)
        after = _syn_open(mark=0.11, upnl_usd=678.91, remaining_mark=478.72)
        # Only live money fields moved — status / size / flags / upnl_pct unchanged
        self.assertEqual(before["status"], after["status"])
        self.assertEqual(before["size_remaining"], after["size_remaining"])
        self.assertEqual(before["upnl_pct"], after["upnl_pct"])
        self.assertNotEqual(before["mark_price"], after["mark_price"])
        self.assertEqual(_old_pos_fingerprint([before]), _old_pos_fingerprint([after]))
        self.assertNotEqual(_new_pos_fingerprint([before]), _new_pos_fingerprint([after]))

    def test_js_fingerprint_includes_live_money_fields(self):
        body = JS[JS.index("function posFingerprint") : JS.index("function posStructFingerprint")]
        for needle in (
            "mark_price",
            "remaining_mark_usd",
            "remaining_cost_usd",
            "remaining_avg",
            "leftover_avg",
            "bought_usd",
            "sold_usd",
            "size_remaining",
            "upnl_usd_est",
        ):
            self.assertIn(needle, body, needle)


class TestO2CollapseUnchanged(unittest.TestCase):
    def test_js_still_collapses_layers_by_price(self):
        self.assertIn("function collapseLayersByPrice", JS)
        self.assertIn("collapseLayersByPrice(p.buy_orders || [])", JS)
        self.assertIn("collapseLayersByPrice(p.sell_orders || [])", JS)
        self.assertIn("One user order at one price = one BUY/SELL line", JS)


class TestO3OpenFetchVsClosed(unittest.TestCase):
    def test_soft_open_path_is_open_only(self):
        self.assertIn("function positionsApiPath", JS)
        self.assertIn("?closed=true", JS)
        self.assertIn("/api/positions?marks=1", JS)
        self.assertIn("await api(positionsApiPath({ marks:", JS)
        self.assertIn('data-pos-view="open"', HTML)
        self.assertIn('data-pos-view="closed"', HTML)
        self.assertIn('_posView === "closed"', JS)

    def test_closed_view_still_requests_closed_book(self):
        self.assertIn("closed=true&limit=", JS)
        self.assertIn("&mix=1", JS)


class TestO4HoverExpandDoesNotFreeze(unittest.TestCase):
    def test_soft_poll_no_longer_returns_before_fetch_on_hover_or_expand(self):
        load = JS[JS.index("async function loadPositions") : JS.index("let _activeMoverSetId")]
        self.assertNotIn("if (soft && Date.now() - _posLastInteract < 8000) return", load)
        self.assertNotIn('if (soft && host.querySelector("details[open]")) return', load)
        self.assertNotIn('if (soft && host.matches(":hover")) return', load)
        self.assertIn("await api(positionsApiPath({ marks:", load)
        self.assertIn("applyPosLiveTicks", load)
        self.assertIn("function applyPosLiveTicks", JS)
        self.assertIn("paintPosCardLive", JS)
        # Expanded cards restore; parent numbers still update
        self.assertIn("if (openIds.has(el.dataset.posId)) el.open = true", load)


class TestClosedMixFill(unittest.TestCase):
    def test_mix_fills_from_fat_book_so_load_more_can_page(self):
        from mexc_bot.webapi.positions_enrich import _pick_recent_closed

        rows = [
            {"market": "spot", "closed_at": float(i), "symbol": f"S{i}"}
            for i in range(200)
        ] + [
            {"market": "futures", "closed_at": 1000.0 + i, "symbol": f"F{i}"}
            for i in range(2)
        ]
        out = _pick_recent_closed(rows, 80, mix_books=True)
        self.assertEqual(len(out), 80)
        self.assertEqual(sum(1 for x in out if x["market"] == "futures"), 2)
        self.assertEqual(sum(1 for x in out if x["market"] == "spot"), 78)


class TestO5NegativeLeftoverUpnl(unittest.TestCase):
    def test_remaining_cost_formula_unchanged_can_be_negative(self):
        leftover = remaining_cost_average(1000.0, 1200.16, 4352.0)
        self.assertAlmostEqual(leftover, (1000.0 - 1200.16) / 4352.0, places=12)
        self.assertLess(leftover, 0)

    def test_syn_negative_leftover_shows_real_upnl_dollar(self):
        rem = 4352.0
        leftover = remaining_cost_average(1000.0, 1200.16, rem)
        mark = 0.10
        d = {
            "market": "futures",
            "status": "open",
            "is_open": True,
            "symbol": "SYN_USDT",
            "size_remaining": rem,
            "leftover_avg": leftover,
            "remaining_avg": leftover,
            "entry_display": leftover,
            "entry_avg": leftover,
            "hold_avg": 0.0998,
            "mark_price": mark,
            "contract_size": 1.0,
            "position_side": "long",
            "unrealized_pnl": 0.0,
        }
        apply_open_mark_math(d)
        expected = (mark - leftover) * rem * 1.0
        self.assertAlmostEqual(d["upnl_usd_est"], expected, places=4)
        self.assertGreater(d["upnl_usd_est"], 0)
        self.assertNotEqual(d["upnl_usd_est"], 0.0)

    def test_spot_negative_leftover_same_rule(self):
        d = {
            "market": "spot",
            "status": "open",
            "size_remaining": 100.0,
            "leftover_avg": -0.20,
            "remaining_avg": -0.20,
            "mark_price": 0.10,
        }
        apply_open_mark_math(d)
        self.assertAlmostEqual(d["upnl_usd_est"], 30.0, places=6)


class TestNoForbiddenTouches(unittest.TestCase):
    def test_no_order_or_learning_or_sqlite_wipe(self):
        self.assertNotIn("place_order", JS)
        self.assertNotIn("DESK_ALLOW_LIVE_ORDERS", JS)
        self.assertNotIn("DROP TABLE", JS)
        self.assertNotIn("learning_lessons", JS)
        self.assertNotIn("data-view=\"machine\"", HTML)


if __name__ == "__main__":
    unittest.main()
