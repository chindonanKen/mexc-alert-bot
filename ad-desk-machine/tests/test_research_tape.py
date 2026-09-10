"""Preferred-three tape hang: load play-file prices; never dump-depth / panic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from machine.engine import Engine, PLAYS_DIR
from machine.research_tape import load_research_tape_layers
from machine.size import ad_side_prices, build_buy_layers, met_band_buy_prices, panic_prices

ROOT = Path(__file__).resolve().parent.parent
PREFERRED = ("ETHUSDT_1h", "XPINUSDT_4h", "SYNUSDT_1h")


def _load(name: str) -> dict:
    return json.loads((PLAYS_DIR / f"{name}.json").read_text())


def test_active_plays_are_only_preferred_three():
    ids = sorted(p.stem for p in PLAYS_DIR.glob("*.json"))
    assert ids == sorted(PREFERRED)
    assert not (PLAYS_DIR / "SYNUSDT_4h.json").exists()
    assert not (PLAYS_DIR / "AGIUSDT_4h.json").exists()
    assert not (PLAYS_DIR / "USUSDT_4h.json").exists()
    assert not (PLAYS_DIR / "ANSEMUSDT_1h.json").exists()


def test_default_hang_loads_preferred_three_tape_only():
    eng = Engine()
    plans = eng.load_plays_dir()
    ids = {p.id for p in plans}
    assert ids == set(PREFERRED)
    assert eng.live_orders_allowed is False
    for plan in plans:
        assert plan.layer_source == "research_tape"
        assert len(plan.fills.buy_layers) == 5
        assert all(b.role == "AD" for b in plan.fills.buy_layers)
        assert all(abs(b.share_pct - 20.0) < 1e-9 for b in plan.fills.buy_layers)
        assert all(abs(b.usd - 40.0) < 1e-9 for b in plan.fills.buy_layers)
        assert not any(b.role == "panic" for b in plan.fills.buy_layers)
        assert len(plan.fills.sell_layers) == 5
        assert all(abs(s.usd - 40.0) < 1e-9 for s in plan.fills.sell_layers)


def test_eth_xpin_syn_tape_prices_match_files():
    eng = Engine()
    for name in PREFERRED:
        play = _load(name)
        plan = eng.hang_play(play)
        file_buys = [float(r["price"]) for r in play["layers"]]
        hung_buys = [b.price for b in plan.fills.buy_layers]
        assert hung_buys == file_buys
        file_sells = [float(r["price"]) for r in play["sell_layers"]]
        hung_sells = [s.price for s in plan.fills.sell_layers]
        assert hung_sells == file_sells
        assert play["live_orders_allowed"] is False


def test_hang_research_tape_does_not_call_dump_depth_or_panic(monkeypatch):
    called: list[str] = []

    def boom_ad(*_a, **_k):
        called.append("ad_side_prices")
        raise AssertionError("dump-depth must not run for research tape")

    def boom_panic(*_a, **_k):
        called.append("panic_prices")
        raise AssertionError("panic generator must not run for research tape")

    def boom_build(*_a, **_k):
        called.append("build_buy_layers")
        raise AssertionError("build_buy_layers must not run for research tape")

    monkeypatch.setattr("machine.engine.build_buy_layers", boom_build)
    monkeypatch.setattr("machine.size.ad_side_prices", boom_ad)
    monkeypatch.setattr("machine.size.panic_prices", boom_panic)
    eng = Engine()
    for name in PREFERRED:
        eng.hang_play(_load(name))
    assert called == []


def test_research_tape_missing_layers_refuses_dump_depth(monkeypatch):
    called = []
    monkeypatch.setattr(
        "machine.engine.build_buy_layers",
        lambda *a, **k: called.append("build") or [],
    )
    play = _load("ETHUSDT_1h")
    play["layers"] = []
    with pytest.raises(ValueError, match="refuse dump-depth"):
        Engine().hang_play(play)
    assert called == []


def test_research_tape_forbids_panic_layers():
    play = _load("ETHUSDT_1h")
    play["layers"].append(
        {"idx": 6, "price": 2000.0, "usd": 40, "share_pct": 20, "role": "panic"}
    )
    with pytest.raises(ValueError, match="panic"):
        load_research_tape_layers(play, 200)


def test_standing_build_buy_layers_is_met_band_not_dump_depth():
    top, bottom, usd = 2484.12, 2454.945456475584, 200.0
    layers = build_buy_layers(top, bottom, usd, high_magnet=True, copy_count=9)
    assert len(layers) == 5
    assert all(ly.role == "AD" for ly in layers)
    assert all(ly.share_pct == 20.0 for ly in layers)
    met = met_band_buy_prices(top, bottom)
    dump = ad_side_prices(top, bottom, high_magnet=True, copy_count=9)
    hung = [ly.price for ly in layers]
    assert hung == [round(p, 10) for p in met]
    assert hung != [round(p, 10) for p in dump]
    assert panic_prices(top, bottom)
    assert len(layers) != 8


def test_feed_names_from_preferred_three():
    from machine.loop import feed_names_from_engine

    eng = Engine()
    eng.load_plays_dir()
    names = feed_names_from_engine(eng)
    assert set(names) == {"ETHUSDT", "XPINUSDT", "SYNUSDT"}
    assert "AGIUSDT" not in names
    assert "USUSDT" not in names
    assert "ANSEMUSDT" not in names
