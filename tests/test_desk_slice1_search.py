"""SLICE 1 — Targets/Movers symbol search + cheaper desk paint (no DB)."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "mexc_bot/webapi/static/index.html").read_text(encoding="utf-8")
JS = (ROOT / "mexc_bot/webapi/static/assets/desk.js").read_text(encoding="utf-8")
CSS = (ROOT / "mexc_bot/webapi/static/assets/desk.css").read_text(encoding="utf-8")


def _symbol_match(symbol: str, query: str) -> bool:
    """Mirrors applyTableSymbolFilter in desk.js."""
    q = str(query or "").strip().upper()
    q_compact = "".join(ch for ch in q if ch.isalnum())
    up = str(symbol or "").upper()
    compact = "".join(ch for ch in up if ch.isalnum())
    if not q:
        return True
    if q in up:
        return True
    return bool(q_compact) and q_compact in compact


class TestDeskSlice1Search(unittest.TestCase):
    def test_html_has_symbol_search_on_targets_and_movers(self):
        self.assertIn('id="moversSearch"', HTML)
        self.assertIn('id="targetsSearch"', HTML)
        self.assertIn('aria-label="Filter movers by symbol"', HTML)
        self.assertIn('aria-label="Filter targets by symbol"', HTML)
        self.assertIn('id="moversTable"', HTML)
        self.assertIn('id="alertsTable"', HTML)

    def test_js_filters_as_you_type(self):
        self.assertIn("function applyTableSymbolFilter", JS)
        self.assertIn("function _applyMoversFilter", JS)
        self.assertIn("function _applyTargetsFilter", JS)
        self.assertIn('moversSearch.addEventListener("input"', JS)
        self.assertIn('targetsSearch.addEventListener("input"', JS)
        # Symbol column indexes: movers col 1, targets col 2
        self.assertIn("_applyMoversFilter()", JS)
        self.assertIn("_applyTargetsFilter()", JS)

    def test_filter_matches_compact_and_raw_symbols(self):
        self.assertTrue(_symbol_match("BTC_USDT", "btc"))
        self.assertTrue(_symbol_match("TSLAUSDT", "TSLA"))
        self.assertTrue(_symbol_match("SIRENUSDT", "siren"))
        self.assertFalse(_symbol_match("BTC_USDT", "ETH"))
        self.assertTrue(_symbol_match("ETHUSDT", ""))

    def test_skip_rewrite_when_payload_unchanged(self):
        self.assertIn("function paintIfChanged", JS)
        self.assertIn("paintIfChanged(tableEl", JS)
        self.assertIn('paintIfChanged(\n      $("#alertsTable")', JS)
        self.assertIn('paintIfChanged(\n      $("#ovTopMovers")', JS)
        self.assertIn("_deskPaint.skipped", JS)
        self.assertIn("window.__deskPaintStats", JS)
        # Soft poll still calls loaders; skip is inside paint
        self.assertIn("loadMovers({ soft: true })", JS)
        self.assertIn("loadTargets({ soft: true })", JS)

    def test_fonts_are_not_render_blocking(self):
        self.assertIn('media="print"', HTML)
        self.assertIn("onload=\"this.media='all'\"", HTML)
        # First paint uses system stack already in CSS
        self.assertIn("system-ui", CSS)

    def test_cache_bust_slice_token(self):
        self.assertIn("desk.js?v=pnlslate1", HTML)
        self.assertIn("desk.css?v=pnlslate1", HTML)
        self.assertNotIn("desk.js?v=lessonad1", HTML)
        self.assertNotIn("desk.js?v=slicelab1", HTML)

    def test_no_new_order_or_schema_paths(self):
        self.assertNotIn("DESK_ALLOW_LIVE_ORDERS", JS)
        self.assertNotIn("place_order", JS)
        self.assertNotIn("CREATE TABLE", JS)
        self.assertNotIn("DROP TABLE", JS)


if __name__ == "__main__":
    unittest.main()
