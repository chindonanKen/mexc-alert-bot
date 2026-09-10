"""Path recut 2026-09-07: tag hung AD buy layer → Path may buy. Not habit sit."""

from machine.feeds import Print
from machine.path import PathHabit, PathSnapshot, evaluate_path


def test_tagged_ad_layer_buys_when_habit_ready_false():
    habit = PathHabit(chosen_tf="15m", habit_ready=False)
    snap = PathSnapshot(chosen_tf_reds=0, tagged_ad_layer=True, ad_met=True)
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert "tagged hung AD buy layer" in d.why
    assert "habit_ready" not in d.why


def test_tagged_ad_layer_buys_with_zero_reds():
    habit = PathHabit(
        chosen_tf="4h",
        habit_ready=True,
        chosen_tf_reds_into_met=3,
        faster_tfs=["1h"],
        faster_tf_reds_at_low=2,
    )
    snap = PathSnapshot(chosen_tf_reds=0, faster_tf_reds={"1h": 0}, tagged_ad_layer=True)
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert d.habit_match


def test_untagged_price_waits():
    habit = PathHabit(chosen_tf="15m", habit_ready=True, chosen_tf_reds_into_met=1)
    snap = PathSnapshot(chosen_tf_reds=9, at_ad=True, ad_met=True, tagged_ad_layer=False)
    d = evaluate_path(habit, snap)
    assert d.action == "wait"
    assert "has not tagged" in d.why


def test_board_panic_still_buys():
    habit = PathHabit(chosen_tf="15m", habit_ready=False)
    snap = PathSnapshot(tagged_ad_layer=False, board_panic=True)
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert "panic" in d.why.lower()


def test_red_count_is_not_a_buy_gate():
    habit = PathHabit(chosen_tf="15m", habit_ready=False)
    for reds in (0, 1, 2, 9):
        d = evaluate_path(habit, PathSnapshot(chosen_tf_reds=reds, tagged_ad_layer=True))
        assert d.action == "buy", f"reds={reds} must not sit"


def test_faster_tf_habit_match_is_not_a_buy_gate():
    habit = PathHabit(
        chosen_tf="15m",
        faster_tfs=["5m"],
        faster_tf_reds_at_low=2,
        vol_at_bottom_usd=40_000,
        habit_ready=True,
    )
    no_faster = PathSnapshot(tagged_ad_layer=True, faster_tf_reds={"5m": 0}, volume_at_ad_usd=0)
    assert evaluate_path(habit, no_faster).action == "buy"
    untagged = PathSnapshot(tagged_ad_layer=False, faster_tf_reds={"5m": 9}, volume_at_ad_usd=99_000)
    assert evaluate_path(habit, untagged).action == "wait"


def test_require_5m_is_not_a_path_sit_gate():
    habit = PathHabit(
        chosen_tf="15m",
        habit_ready=True,
        chosen_tf_reds_into_met=2,
        require_5m_volume_spike=True,
        vol_5m_usual_usd=40_000,
    )
    snap = PathSnapshot(tagged_ad_layer=True, volume_usd_5m=0, chosen_tf_reds=2)
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert "5m volume spike" not in d.why


def test_path_habit_loads_nested_path_block():
    h = PathHabit.from_play(
        {
            "chosen_tf": "4h",
            "habit_ready": True,
            "path": {
                "require_5m_volume_spike": True,
                "vol_5m_usual_usd": 12_000,
            },
        }
    )
    assert h.require_5m_volume_spike is True
    assert h.vol_5m_usual_usd == 12_000
    unset = PathHabit.from_play({"chosen_tf": "4h"})
    assert unset.require_5m_volume_spike is False
    assert unset.vol_5m_usual_usd is None


def test_engine_habit_false_paper_buy_on_tagged_layer(engine, sit_play):
    """habit_ready false + tagged AD layer + Size real volume → paper-buy, not habit sit."""
    plan = engine.hang_play(sit_play)
    r = engine.on_print(
        Print(name="SIT1", price=1.61, low=1.61, chosen_tf_reds=0, volume_usd=99_000)
    )
    assert r["action"] == "buy"
    assert "habit_ready" not in r["why"]
    assert any(b.status == "filled" for b in plan.fills.buy_layers)
    assert engine.live_orders_allowed is False


def test_engine_quiet_volume_fills_tagged_layer_not_habit_sit(engine, habit_play):
    """Kenneth 2026-09-10: grind-wait is not standing — tagged AD layer fills."""
    play = dict(habit_play)
    play["habit_ready"] = False
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(name="DEMO", price=0.81, low=0.81, chosen_tf_reds=0, volume_usd=0)
    )
    assert r["action"] == "buy"
    assert "habit_ready" not in r["why"]
    assert any(b.status == "filled" for b in plan.fills.buy_layers)
    assert engine.live_orders_allowed is False


def test_engine_untagged_price_waits(engine, habit_play):
    engine.hang_play(habit_play)
    r = engine.on_print(
        Print(name="DEMO", price=0.95, low=0.95, chosen_tf_reds=9, volume_usd=50_000)
    )
    assert r["action"] == "wait"
    assert "has not tagged" in r["why"]


def test_engine_5m_weak_is_not_a_path_or_size_sit(engine, habit_play):
    play = dict(habit_play)
    play["require_5m_volume_spike"] = True
    play["vol_5m_usual_usd"] = 40_000
    plan = engine.hang_play(play)
    weak = engine.on_print(
        Print(
            name="DEMO",
            price=0.81,
            low=0.81,
            chosen_tf_reds=0,
            volume_usd=50_000,
            volume_usd_5m=100,
            reds_5m=1,
        )
    )
    assert weak["action"] == "buy"
    assert "missing 5m volume spike" not in weak["why"]
    assert any(b.status == "filled" for b in plan.fills.buy_layers)


def test_engine_5m_spike_paper_buy(engine, habit_play):
    play = dict(habit_play)
    play["require_5m_volume_spike"] = True
    play["vol_5m_usual_usd"] = 40_000
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="DEMO",
            price=0.81,
            low=0.81,
            chosen_tf_reds=0,
            volume_usd=50_000,
            volume_usd_5m=50_000,
            reds_5m=1,
        )
    )
    assert r["action"] == "buy"
    assert any(b.status == "filled" for b in plan.fills.buy_layers)
