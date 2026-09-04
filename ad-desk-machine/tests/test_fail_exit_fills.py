from __future__ import annotations

from machine.exit import (
    bounce_kind,
    defensive_sell_layers,
    exit_decision,
    hung_sell_layers,
    leftover_remaining_cost,
    leftover_usd,
)
from machine.fail import fail_decision, failed_ad, last_under_ad, news_kill
from machine.fills import empty_out_after_buy, simulate_buy_fills, simulate_sell_fills
from machine.plays import load_play


def test_news_kill_not_rumor():
    assert news_kill([{"class": "DELIST", "title": "gone"}])
    assert news_kill([{"class": "DELIST", "title": "rumor of delist"}]) is None
    assert news_kill([{"class": "SCAM", "severity": "rumor"}]) is None


def test_timer_is_never_a_fail():
    assert failed_ad(armed_at=1.0, now=1e12, tf="4h", bounced=False) is False
    assert failed_ad(armed_at=1.0, now=1e12, tf="4h", bounced=True) is False


def test_last_under_ad_is_not_a_fail():
    play = load_play("USUSDT_4h")
    assert last_under_ad(0.010, play["ad_bottom"]) is True
    d = fail_decision(play, {"current_price": 0.010, "fast_dump": False, "in_play": False})
    assert d["fail"] is False
    assert d["add_panic"] is False


def test_news_flatten():
    play = load_play("SYNUSDT_4h")
    d = fail_decision(play, {"news": [{"class": "HACK", "title": "drained"}]})
    assert d["action"] == "flatten"
    assert d["reason"] == "news_kill"


def test_break_ad_adds_panic_half():
    play = load_play("SYNUSDT_4h")
    d = fail_decision(
        play,
        {"current_price": 0.030, "fast_dump": True, "vol_spike": True, "in_play": True},
    )
    assert d["add_panic"] is True
    assert d["fail"] is False


def test_grind_not_this_chart_is_fail():
    play = load_play("USUSDT_4h")
    d = fail_decision(play, {"in_play": True, "grind_not_this_chart": True})
    assert d["fail"] is True
    assert d["reason"] == "grind_not_this_chart"


def test_leftover_remaining_cost_fifo():
    fills = [
        {"side": "buy", "filled_price": 1.0, "qty": 100, "usd": 100},
        {"side": "sell", "filled_price": 1.2, "qty": 40, "usd": 48},
    ]
    assert leftover_remaining_cost(fills) == 1.0
    assert leftover_usd(fills) == 52.0


def test_leftover_none_when_flat():
    fills = [
        {"side": "buy", "filled_price": 1.0, "qty": 10, "usd": 10},
        {"side": "sell", "filled_price": 1.1, "qty": 10, "usd": 11},
    ]
    assert leftover_remaining_cost(fills) is None


def test_bounce_good_weak_fail_too_early():
    play = {"typical_bounce": 0.02, "candles_to_bounce": 4, "in_play": True}
    assert (
        bounce_kind(play, {"current_price": 0.13, "in_play": True}, leftover=0.10) == "GOOD"
    )
    assert (
        bounce_kind(play, {"current_price": 0.11, "in_play": True}, leftover=0.10) == "WEAK"
    )
    assert (
        bounce_kind(play, {"in_play": True, "grind_not_this_chart": True}, leftover=0.10)
        == "FAIL"
    )
    assert (
        bounce_kind(
            play,
            {
                "current_price": 0.11,
                "in_play": True,
                "board_panic": True,
                "weak_first_bounce": True,
            },
            leftover=0.10,
        )
        == "TOO_EARLY"
    )


def test_bounce_too_early_before_habit_candles():
    play = {"typical_bounce": 0.05, "candles_to_bounce": 6, "in_play": True}
    assert (
        bounce_kind(
            play,
            {"current_price": 0.12, "candles_since_arm": 2, "in_play": True},
            leftover=0.10,
        )
        == "TOO_EARLY"
    )


def test_no_invent_sells_when_empty():
    play = {"sell_layers": [], "ad_bottom": 1.0}
    out = exit_decision(play, {"current_price": 1.2, "in_play": True}, [])
    assert out["sell_layers"] == []
    assert out["empty"] is True
    assert out["invented"] is False
    assert hung_sell_layers(play) == []


def test_defensive_lowers_sells_under_ad():
    sells = [{"idx": 1, "price": 0.0139, "pct": 10}]
    out = defensive_sell_layers(
        sells, ad_bottom=0.0115, new_bottom=0.009, board_panic=False
    )
    assert out[0]["price"] < 0.0139
    assert out[0]["price"] > 0.009
    assert out[0]["defensive"] is True


def test_defensive_keeps_hung_sells_on_board_panic():
    sells = [{"idx": 1, "price": 0.0139, "pct": 10}]
    out = defensive_sell_layers(
        sells, ad_bottom=0.0115, new_bottom=0.009, board_panic=True
    )
    assert out[0]["price"] == 0.0139
    assert not out[0].get("defensive")


def test_hung_sells_on_three_plays():
    syn = hung_sell_layers(load_play("SYNUSDT_4h"))
    agi = hung_sell_layers(load_play("AGIUSDT_4h"))
    us = hung_sell_layers(load_play("USUSDT_4h"))
    assert len(syn) == 5
    assert [round(x["price"], 5) for x in agi] == [0.00451, 0.00476, 0.00505, 0.00575, 0.00584]
    assert [x["price"] for x in us] == [0.0139, 0.0148, 0.0167, 0.0186, 0.02092]
    assert [x["pct"] for x in agi] == [10, 15, 30, 30, 15]


def test_simulate_buy_when_last_tags_layer():
    layers = [{"idx": 1, "price": 0.05, "usd": 5, "size_pct": 5, "band": "ad"}]
    got = simulate_buy_fills(layers, 0.049)
    assert len(got) == 1
    assert got[0]["simulated"] is True
    assert got[0]["live_sent"] is False


def test_no_buy_when_last_above_layer():
    layers = [{"idx": 1, "price": 0.05, "usd": 5, "size_pct": 5, "band": "ad"}]
    assert simulate_buy_fills(layers, 0.06) == []


def test_simulate_sell_when_last_reaches():
    sells = [{"idx": 1, "price": 0.0148, "pct": 100}]
    got = simulate_sell_fills(sells, 0.015, remaining_usd=10)
    assert len(got) == 1
    assert got[0]["side"] == "sell"


def test_empty_sells_do_not_invent_fills():
    assert simulate_sell_fills([], 0.02, remaining_usd=10) == []


def test_empty_out_after_buy_flag():
    assert empty_out_after_buy([{"side": "buy"}], []) is True
    assert empty_out_after_buy([{"side": "buy"}], [{"price": 1}]) is False
    assert empty_out_after_buy([], []) is False
