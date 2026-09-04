from __future__ import annotations

import os
from pathlib import Path

from machine.settings import LIVE_ORDERS_ALLOWED, live_orders_allowed

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "machine"


def test_live_orders_allowed_hard_false():
    assert LIVE_ORDERS_ALLOWED is False
    assert live_orders_allowed() is False


def test_env_cannot_enable_live_orders(monkeypatch):
    monkeypatch.setenv("DESK_ALLOW_LIVE_ORDERS", "true")
    monkeypatch.setenv("MACHINE_LIVE_ORDERS", "1")
    from machine.settings import live_orders_allowed as fn

    assert fn() is False


def test_source_has_no_live_order_paths():
    banned = (
        "/api/v3/order",
        "place_order",
        "mx-api-key",
        "hmac.new",
        "ACCESS_KEY",
    )
    blob = ""
    for path in SRC.glob("*.py"):
        blob += path.read_text(encoding="utf-8")
    for token in banned:
        assert token not in blob, token


def test_feeds_use_public_mexc_only():
    text = (SRC / "feeds.py").read_text(encoding="utf-8")
    assert "api.mexc.com" in text
    assert "ticker/price" in text
    assert "klines" in text
    assert "api/v3/order" not in text


def test_public_payload_never_says_paper_plan_or_pack():
    from machine.engine import evaluate
    from machine.plays import load_play

    play = load_play("USUSDT_4h")
    out = evaluate(play, {"current_price": 0.02, "chosen_tf_reds": 1})
    blob = str(out).lower()
    assert "paper plan" not in blob
    assert "paper pack" not in blob
    assert out["hung_plan"] is True
    assert "buy_layers" in out
    assert "sell_layers" in out
    assert "current_price" in out
    assert out["live_orders_allowed"] is False
    assert out["live_orders_sent"] is False


def test_settings_forbidden_public_words():
    from machine.settings import FORBIDDEN_PUBLIC, PUBLIC_NOUNS

    assert "paper plan" in FORBIDDEN_PUBLIC
    assert "hung plan" in PUBLIC_NOUNS
    assert "buy layers" in PUBLIC_NOUNS
    assert "current price" in PUBLIC_NOUNS
