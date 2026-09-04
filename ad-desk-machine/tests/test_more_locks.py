from __future__ import annotations

from machine.chart import ad_length, at_ad_now
from machine.engine import evaluate
from machine.fail import is_rumor
from machine.fills import last_reached_layer, last_reached_sell
from machine.plays import load_exit_facts, load_play, play_path
from machine.settings import faster_tf_for, is_faster_tf, tf_slow_rank
from machine.size import at_ad_layer, size_layers


def test_faster_tf_for_4h_is_15m():
    assert faster_tf_for("4h") == "15m"
    assert faster_tf_for("1d") == "15m"
    assert faster_tf_for("1d") != "12h"
    assert is_faster_tf("15m", "4h")
    assert not is_faster_tf("4h", "15m")
    assert tf_slow_rank("4h") > tf_slow_rank("15m")


def test_ad_length_and_at_ad_now():
    assert abs(ad_length(0.14753, 0.0413) - (0.14753 - 0.0413)) < 1e-12
    assert at_ad_now(last=0.0413, ad_top=0.14753, ad_bottom=0.0413)
    assert not at_ad_now(last=0.10, ad_top=0.14753, ad_bottom=0.0413)


def test_at_ad_layer_picks_just_above_b():
    layers = size_layers(load_play("SYNUSDT_4h"))
    hit = at_ad_layer(layers, 0.0413)
    assert hit is not None
    assert hit["band"] == "ad"
    assert hit["price"] >= 0.0413 or hit["idx"] == 5


def test_last_reached_helpers():
    assert last_reached_layer(0.04, 0.0413)
    assert not last_reached_layer(0.05, 0.0413)
    assert last_reached_sell(0.015, 0.0148)
    assert not last_reached_sell(0.01, 0.0148)


def test_rumor_helper():
    assert is_rumor({"title": "allegedly hacked"})
    assert is_rumor({"kind": "rumour"})
    assert not is_rumor({"title": "exchange delist notice", "class": "DELIST"})


def test_exit_facts_optional():
    assert load_exit_facts("SYNUSDT_4h") is None
    assert play_path("SYNUSDT_4h").is_file()


def test_engine_grind_wait_no_fill():
    play = load_play("SYNUSDT_4h")
    out = evaluate(
        play,
        {
            "current_price": 0.08,
            "chosen_tf_reds": 4,
            "quiet_grind": True,
            "vol_spike": False,
        },
    )
    assert out["fills"] == []


def test_engine_add_panic_under_b():
    play = load_play("SYNUSDT_4h")
    # get in first via board panic at AD, then dump under B
    evaluate(
        play,
        {
            "current_price": 0.042,
            "chosen_tf_reds": 1,
            "board_panic": True,
            "vol_spike": True,
        },
    )
    out = evaluate(
        play,
        {
            "current_price": 0.036,
            "chosen_tf_reds": 4,
            "fast_dump": True,
            "vol_spike": True,
            "in_play": True,
        },
    )
    assert out["action"] in {"add_panic", "buy", "wait"}
    panic_fills = [f for f in out["fills"] if f.get("band") == "panic"]
    # last 0.036 is through Q1 (0.03717)
    assert panic_fills or out["action"] == "add_panic"


def test_engine_defensive_under_ad_without_board_panic():
    play = dict(load_play("USUSDT_4h"))
    play["in_play"] = True
    out = evaluate(
        play,
        {
            "current_price": 0.010,
            "chosen_tf_reds": 4,
            "board_panic": False,
            "in_play": True,
        },
    )
    assert out["defensive"] is True
    sells = out["sell_layers"]
    assert sells
    assert sells[0]["price"] < 0.0139


def test_public_size_layers_match_buy_layers():
    from machine.engine import public_play

    p = public_play(load_play("USUSDT_4h"))
    assert p["buy_layers"] == p["Size_layers"]
    assert len(p["buy_layers"]) == 8
