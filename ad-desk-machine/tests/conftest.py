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
        "play_usd": 100,
        # Explicit layers so fill tests are deterministic
        "layers": [
            {"idx": 1, "price": 0.86, "usd": 5, "share_pct": 5, "role": "AD"},
            {"idx": 2, "price": 0.84, "usd": 7.5, "share_pct": 7.5, "role": "AD"},
            {"idx": 3, "price": 0.82, "usd": 10, "share_pct": 10, "role": "AD"},
            {"idx": 4, "price": 0.81, "usd": 12.5, "share_pct": 12.5, "role": "AD"},
            {"idx": 5, "price": 0.80, "usd": 15, "share_pct": 15, "role": "AD"},
            {"idx": 6, "price": 0.78, "usd": 10, "share_pct": 10, "role": "panic"},
            {"idx": 7, "price": 0.762, "usd": 15, "share_pct": 15, "role": "panic"},
            {"idx": 8, "price": 0.744, "usd": 25, "share_pct": 25, "role": "panic"},
        ],
        "sell_layers": [
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
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
        "play_usd": 100,
        "layers": [
            {"idx": 1, "price": 1.62, "usd": 5, "share_pct": 5, "role": "AD"},
            {"idx": 5, "price": 1.60, "usd": 15, "share_pct": 15, "role": "AD"},
        ],
        "sell_layers": [],
    }


def at_ad_print(name: str, price: float, **kw) -> Print:
    return Print(name=name, price=price, low=price, **kw)
