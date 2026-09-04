#!/usr/bin/env python3
"""Buy/Sell fill Telegram lines — coalesce, no POSITION OPENED."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_bot.learning.fills import format_fill_notify_lines


def test_coalesce_same_symbol_side():
    rows = [
        {"symbol": "TACUSDT", "side": "buy", "price": 0.0053, "qty": 1000, "quote_qty": 5.3},
        {"symbol": "TACUSDT", "side": "buy", "price": 0.0052, "qty": 1000, "quote_qty": 5.2},
    ]
    lines = format_fill_notify_lines(rows)
    assert len(lines) == 1
    assert lines[0].startswith("TACUSDT - Buy - ")
    assert " - $" in lines[0]
    assert lines[0].endswith("$10.50")
    assert "POSITION" not in lines[0].upper()
    assert "OPENED" not in lines[0].upper()


def test_buy_and_sell_separate():
    rows = [
        {"symbol": "GUA_USDT", "side": "buy", "price": 0.01, "qty": 100, "quote_qty": 1.0},
        {"symbol": "GUA_USDT", "side": "sell", "price": 0.012, "qty": 50, "quote_qty": 0.6},
    ]
    lines = format_fill_notify_lines(rows, min_usd=0.5)
    assert any(l.startswith("GUA_USDT - Buy -") for l in lines)
    assert any(l.startswith("GUA_USDT - Sold -") for l in lines)


def test_dust_skipped():
    rows = [
        {"symbol": "XUSDT", "side": "buy", "price": 0.001, "qty": 1, "quote_qty": 0.001},
    ]
    assert format_fill_notify_lines(rows) == []


if __name__ == "__main__":
    test_coalesce_same_symbol_side()
    test_buy_and_sell_separate()
    test_dust_skipped()
    print("PASS")
