from __future__ import annotations

from machine.chart import bars_ever_met, is_met, met_ceiling, met_floor
from machine.path import first_or_second_red, path_decision
from machine.plays import load_play
from machine.settings import AD_SIDE_HALF_PCTS, PANIC_HALF_PCTS
from machine.size import (
    dump_depth_layers,
    equal_spread_prices,
    first_volume_near_b,
    pack_notional,
    size_gate,
    size_layers,
    volume_match,
)


def test_met_ceiling_is_last_five_percent_of_l():
    # T=10 B=5 L=5 → ceiling 5.25
    assert abs(met_ceiling(10, 5) - 5.25) < 1e-9
    assert abs(met_floor(10, 5) - (5 - 0.03 * 5)) < 1e-9


def test_met_when_last_in_band_including_b():
    assert is_met(last=5.0, ad_top=10, ad_bottom=5)
    assert is_met(last=5.2, ad_top=10, ad_bottom=5)
    assert is_met(last=4.9, ad_top=10, ad_bottom=5)  # slightly through B


def test_not_met_far_above_or_deep_panic():
    assert not is_met(last=7.0, ad_top=10, ad_bottom=5)
    assert not is_met(last=4.0, ad_top=10, ad_bottom=5)
    assert not is_met(last=5.2, ad_top=10, ad_bottom=5, ad_known=False)


def test_met_stays_met_after_bounce():
    play = {"ad_top": 10, "ad_bottom": 5, "met": True}
    assert bars_ever_met(play, last=8.0) is True


def test_kline_low_in_band_sets_met():
    play = {"ad_top": 10, "ad_bottom": 5}
    bars = [{"o": 6, "h": 6.2, "l": 5.1, "c": 5.8}]
    assert bars_ever_met(play, bars, last=8.0) is True


def test_last_above_band_without_history_is_not_met():
    play = {"ad_top": 10, "ad_bottom": 5}
    assert bars_ever_met(play, [{"l": 7.0}], last=8.0) is False


def test_dump_depth_five_ad_three_panic():
    layers = dump_depth_layers(10.0, 5.0, budget_usd=100)
    ad = [L for L in layers if L["band"] == "ad"]
    panic = [L for L in layers if L["band"] == "panic"]
    assert len(ad) == 5
    assert len(panic) == 3
    assert [L["half_pct"] for L in ad] == list(AD_SIDE_HALF_PCTS)
    assert [L["half_pct"] for L in panic] == list(PANIC_HALF_PCTS)


def test_dump_depth_is_not_equal_spread():
    layers = dump_depth_layers(10.0, 5.0)
    ad_px = [L["price"] for L in layers if L["band"] == "ad"]
    assert ad_px != equal_spread_prices(10.0, 5.0)


def test_panic_is_percent_of_b_not_l():
    # T=10 B=5 L=5. Old L-based Q1 would also be 4.5; use SYN where L ≠ B.
    syn = load_play("SYNUSDT_4h")
    layers = size_layers(syn)
    panic = [L for L in layers if L["band"] == "panic"]
    b = 0.0413
    assert abs(panic[0]["price"] - (b - b * 0.10)) < 1e-8
    assert abs(panic[1]["price"] - (b - b * 0.19)) < 1e-8
    assert abs(panic[2]["price"] - (b - b * 0.28)) < 1e-8
    length = 0.14753 - 0.0413
    assert abs(panic[0]["price"] - (b - length * 0.10)) > 1e-4


def test_high_magnet_clusters_near_b():
    layers = dump_depth_layers(10.0, 5.0)
    ad = [L["price"] for L in layers if L["band"] == "ad"]
    assert max(ad) < 5.4  # not mid-range
    assert min(ad) < 5.0  # last AD row slightly under B


def test_size_budget_is_one_hundred():
    layers = dump_depth_layers(10.0, 5.0, budget_usd=100)
    assert pack_notional(layers) == 100.0


def test_ad_side_size_pcts_are_half_of_play():
    layers = dump_depth_layers(10.0, 5.0)
    ad = [L for L in layers if L["band"] == "ad"]
    assert [L["size_pct"] for L in ad] == [5.0, 7.5, 10.0, 12.5, 15.0]


def test_grind_wait_blocks_away_from_ad():
    play = load_play("SYNUSDT_4h")
    gate = size_gate(play, {"quiet_grind": True, "vol_spike": False, "at_ad": False})
    assert gate["ok"] is False
    assert gate["reason"] == "grind_wait"


def test_first_volume_near_b_is_half():
    play = load_play("SYNUSDT_4h")
    last = 0.0415
    assert first_volume_near_b(
        last=last,
        ad_top=play["ad_top"],
        ad_bottom=play["ad_bottom"],
        vol_spike=True,
        prior_spike=False,
    )
    gate = size_gate(
        play,
        {
            "current_price": last,
            "at_ad": True,
            "vol_spike": True,
            "prior_vol_spike": False,
        },
    )
    assert gate["scale"] == 0.5
    assert gate["reason"] == "first_volume_near_b"


def test_no_volume_at_ad_sizes_down_not_skip():
    play = load_play("USUSDT_4h")
    gate = size_gate(play, {"at_ad": True, "vol_spike": False, "quiet_grind": False})
    assert gate["ok"] is True
    assert gate["scale"] == 0.5


def test_volume_match_spike():
    assert volume_match(20000, 16623) is True
    assert volume_match(1000, 16623) is False


def test_watch_only_blocks_all_buys():
    play = {"watch_only": True, "habit_ready": False}
    tape = {
        "chosen_tf_reds": 4,
        "at_ad": True,
        "met": True,
        "board_panic": True,
    }
    d = path_decision(play, tape)
    assert d["buy"] is False
    assert d["reason"] == "watch_only"


def test_habit_false_sits_first_and_second_red():
    play = load_play("SYNUSDT_4h")
    assert play["habit_ready"] is False
    one = path_decision(play, {"chosen_tf_reds": 1, "at_ad": True, "met": True})
    two = path_decision(play, {"chosen_tf_reds": 2, "at_ad": True, "met": True})
    assert one["reason"] == "sit_first_second_red"
    assert two["reason"] == "sit_first_second_red"
    assert one["buy"] is False and two["buy"] is False


def test_habit_false_third_red_at_ad_buys():
    play = load_play("USUSDT_4h")
    d = path_decision(play, {"chosen_tf_reds": 3, "at_ad": True, "met": True})
    assert d["buy"] is True
    assert d["reason"] == "chosen_reds_past_sit"


def test_board_panic_buys_on_first_red():
    play = load_play("SYNUSDT_4h")
    d = path_decision(
        play,
        {"chosen_tf_reds": 1, "at_ad": True, "met": True, "board_panic": True},
    )
    assert d["buy"] is True
    assert d["reason"] == "board_panic"


def test_habit_ready_first_chosen_red_buys():
    play = load_play("AGIUSDT_4h")
    assert play["habit_ready"] is True
    assert play["chosen_tf_reds_into_met"] == 1
    d = path_decision(play, {"chosen_tf_reds": 1, "at_ad": True, "met": True})
    assert d["buy"] is True
    assert d["reason"] == "habit_ready_chosen_reds"


def test_habit_ready_faster_tf_reds_and_volume():
    play = load_play("AGIUSDT_4h")
    d = path_decision(
        play,
        {
            "chosen_tf_reds": 0,
            "faster_tf_reds": 3,
            "volume_match": True,
            "at_ad": True,
            "met": True,
        },
    )
    assert d["buy"] is True
    assert d["reason"] == "faster_tf_reds_volume"


def test_habit_ready_waits_without_tell():
    play = load_play("AGIUSDT_4h")
    d = path_decision(
        play,
        {
            "chosen_tf_reds": 0,
            "faster_tf_reds": 0,
            "volume_match": False,
            "at_ad": True,
            "met": True,
        },
    )
    assert d["buy"] is False
    assert d["reason"] == "habit_ready_waiting_reds"


def test_watch_only_blocks_board_panic():
    play = dict(load_play("SYNUSDT_4h"))
    play["watch_only"] = True
    d = path_decision(
        play,
        {"chosen_tf_reds": 1, "at_ad": True, "met": True, "board_panic": True},
    )
    assert d["buy"] is False


def test_first_or_second_helper():
    assert first_or_second_red(1)
    assert first_or_second_red(2)
    assert not first_or_second_red(3)
    assert not first_or_second_red(None)
