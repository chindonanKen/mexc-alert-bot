"""Shared fixtures: synthetic prints without MEXC."""

from __future__ import annotations

import pytest

from machine.engine import Engine
from machine.feeds import Print


@pytest.fixture
def engine() -> Engine:
    return Engine()


@pytest.fixture
def habit_play() -> dict:
    # Standing 2026-09-10: five equal 20% AD layers, no panic.
    return {
        "id": "DEMO",
        "name": "DEMO",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "chosen_tf_reds_into_met": 3,
        "faster_tf_reds_at_low": 2,
        "vol_at_bottom_usd": 40_000,
        "habit_ready": True,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 200,
        "layers": [
            {"idx": 1, "price": 0.81, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 2, "price": 0.8075, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 3, "price": 0.805, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 4, "price": 0.8025, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 5, "price": 0.80, "usd": 40, "share_pct": 20, "role": "AD"},
        ],
        "sell_layers": [
            {"idx": 1, "price": 0.85, "usd": 40, "share_pct": 20, "why": "research_tape"},
            {"idx": 2, "price": 0.88, "usd": 40, "share_pct": 20, "why": "research_tape"},
            {"idx": 3, "price": 0.91, "usd": 40, "share_pct": 20, "why": "research_tape"},
            {"idx": 4, "price": 0.94, "usd": 40, "share_pct": 20, "why": "research_tape"},
            {"idx": 5, "price": 0.98, "usd": 40, "share_pct": 20, "why": "research_tape"},
        ],
    }


@pytest.fixture
def sit_play() -> dict:
    return {
        "id": "SIT1",
        "name": "SIT1",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "habit_ready": False,
        "ad_top": 2.0,
        "ad_bottom": 1.6,
        "play_usd": 200,
        "layers": [
            {"idx": 1, "price": 1.62, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 2, "price": 1.615, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 3, "price": 1.61, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 4, "price": 1.605, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 5, "price": 1.60, "usd": 40, "share_pct": 20, "role": "AD"},
        ],
        "sell_layers": [],
    }


def at_ad_print(name: str, price: float, **kw) -> Print:
    return Print(name=name, price=price, low=price, **kw)
