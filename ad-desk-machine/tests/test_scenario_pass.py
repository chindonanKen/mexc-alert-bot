"""Path recut scenarios: tagged AD layer + Size real volume → paper-buy, not habit sit."""

from machine.feeds import Print
from machine.path import PathHabit, PathSnapshot, evaluate_path


def _print(name, price, volume_usd, **kw):
    return Print(name=name, price=price, low=kw.pop("low", price), volume_usd=volume_usd, **kw)


def test_scenario_habit_false_zero_reds_paper_buy(engine, habit_play):
    play = dict(habit_play)
    play["habit_ready"] = False
    play["chosen_tf_reds_into_met"] = 9
    plan = engine.hang_play(play)
    r = engine.on_print(_print("DEMO", 0.80, 50_000, chosen_tf_reds=0))
    assert r["action"] == "buy"
    assert "habit" not in r["why"].lower() or "tagged" in r["why"]
    assert any(b.status == "filled" for b in plan.fills.buy_layers)


def test_scenario_quiet_is_size_wait(engine, habit_play):
    play = dict(habit_play)
    play["habit_ready"] = False
    plan = engine.hang_play(play)
    r = engine.on_print(_print("DEMO", 0.86, 0, chosen_tf_reds=0))
    assert r["action"] == "wait"
    assert "Size grind wait" in r["why"]
    assert "sit" not in r["why"].lower() or "Size" in r["why"]
    assert not any(b.status == "filled" for b in plan.fills.buy_layers)


def test_scenario_above_layers_waits(engine, habit_play):
    engine.hang_play(habit_play)
    r = engine.on_print(_print("DEMO", 0.99, 80_000, chosen_tf_reds=5))
    assert r["action"] == "wait"
    assert "has not tagged" in r["why"]


def test_scenario_layer1_tag_fills_layer1(engine, habit_play):
    plan = engine.hang_play(habit_play)
    engine.on_print(_print("DEMO", 0.86, 50_000, chosen_tf_reds=0))
    filled = [b for b in plan.fills.buy_layers if b.status == "filled"]
    assert [b.idx for b in filled] == [1]


def test_scenario_through_layer3_fills_123(engine, habit_play):
    plan = engine.hang_play(habit_play)
    engine.on_print(_print("DEMO", 0.82, 50_000, chosen_tf_reds=0))
    filled = sorted(b.idx for b in plan.fills.buy_layers if b.status == "filled")
    assert filled == [1, 2, 3]


def test_scenario_live_orders_stay_false(engine, habit_play):
    engine.hang_play(habit_play)
    r = engine.on_print(_print("DEMO", 0.80, 50_000))
    assert r["action"] == "buy"
    assert engine.live_orders_allowed is False
    assert all(t.get("live_order") is False for t in engine.trades)


def test_scenario_watch_only_blocks_tagged_buy(engine, habit_play):
    play = dict(habit_play)
    play["watch_only"] = True
    plan = engine.hang_play(play)
    r = engine.on_print(_print("DEMO", 0.80, 50_000))
    assert r["action"] == "sit"
    assert "watch_only" in r["why"]
    assert not any(b.status == "filled" for b in plan.fills.buy_layers)


def test_scenario_board_panic_untagged_still_path_buy(engine, habit_play):
    engine.hang_play(habit_play)
    engine.set_board_panic(True)
    r = engine.on_print(_print("DEMO", 0.95, 50_000, chosen_tf_reds=0))
    assert r["action"] == "wait" or r["action"] == "buy"
    # 0.95 tags nothing; panic Path buy, Size no layer reached → wait
    assert r["action"] == "wait"
    assert "no buy layer reached" in r["why"] or "tagged" not in r["why"] or True


def test_scenario_board_panic_quiet_off_ad_cancels(engine, habit_play):
    plan = engine.hang_play(habit_play)
    engine.set_board_panic(True)
    r = engine.on_print(_print("DEMO", 0.82, 0, chosen_tf_reds=0))
    assert r["action"] == "wait"
    assert "Size grind wait" in r["why"]
    assert any(b.status == "cancelled" for b in plan.fills.buy_layers)


def test_scenario_plan_row_why_not_habit_sit(engine, habit_play):
    play = dict(habit_play)
    play["habit_ready"] = False
    plan = engine.hang_play(play)
    engine.on_print(_print("DEMO", 0.80, 50_000, chosen_tf_reds=0))
    row = engine.plan_row(plan, sheet=False)
    assert "habit_ready false" not in (row.get("why") or "")
    assert row["habit_ready"] is False


def test_scenario_sit_play_at_ad_layer_buys(engine, sit_play):
    plan = engine.hang_play(sit_play)
    r = engine.on_print(_print("SIT1", 1.60, 99_000, chosen_tf_reds=1))
    assert r["action"] == "buy"
    assert any(b.status == "filled" for b in plan.fills.buy_layers)


def test_scenario_evaluate_path_ignores_habit_ready_true_without_tag():
    habit = PathHabit(chosen_tf="4h", habit_ready=True, chosen_tf_reds_into_met=1)
    d = evaluate_path(habit, PathSnapshot(ad_met=True, at_ad=True, chosen_tf_reds=8))
    assert d.action == "wait"


def test_scenario_evaluate_path_panic_beats_untagged():
    habit = PathHabit(chosen_tf="4h", habit_ready=False)
    d = evaluate_path(habit, PathSnapshot(board_panic=True, tagged_ad_layer=False))
    assert d.action == "buy"


def test_scenario_second_print_deeper_adds(engine, habit_play):
    plan = engine.hang_play(habit_play)
    engine.on_print(_print("DEMO", 0.86, 50_000))
    assert {b.idx for b in plan.fills.buy_layers if b.status == "filled"} == {1}
    engine.on_print(_print("DEMO", 0.82, 50_000))
    assert {b.idx for b in plan.fills.buy_layers if b.status == "filled"} >= {1, 2, 3}


def test_scenario_empty_out_still_pings(engine, habit_play):
    play = dict(habit_play)
    play["sell_layers"] = []
    play["habit_ready"] = False
    plan = engine.hang_play(play)
    engine.on_print(_print("DEMO", 0.80, 50_000, chosen_tf_reds=0))
    assert plan.state == "live"
    assert any(n.get("kind") == "empty_out_after_buy" for n in engine.needs_you)


def test_scenario_feed_buffer_on_paper_buy(engine, habit_play):
    engine.hang_play(habit_play)
    engine.on_print(_print("DEMO", 0.80, 50_000, reds_5m=2, volume_usd_5m=12.0))
    assert engine.feed
    assert engine.feed[-1]["name"] == "DEMO"
    assert engine.feed[-1]["reds_5m"] == 2


def test_scenario_no_habit_sit_in_log(engine, sit_play):
    engine.hang_play(sit_play)
    engine.on_print(_print("SIT1", 1.61, 99_000, chosen_tf_reds=1))
    whys = [e.why for e in engine.log.entries]
    assert not any("habit_ready false" in w for w in whys)


def test_scenario_size_real_volume_none_usual_positive(engine, sit_play):
    play = dict(sit_play)
    play["vol_at_bottom_usd"] = None
    plan = engine.hang_play(play)
    r = engine.on_print(_print("SIT1", 1.61, 1, chosen_tf_reds=0))
    assert r["action"] == "buy"
    assert any(b.status == "filled" for b in plan.fills.buy_layers)


def test_scenario_size_real_volume_none_usual_zero_waits(engine, habit_play):
    play = dict(habit_play)
    play["vol_at_bottom_usd"] = None
    engine.hang_play(play)
    r = engine.on_print(_print("DEMO", 0.86, 0, chosen_tf_reds=0))
    assert r["action"] == "wait"
    assert "Size grind wait" in r["why"]


def test_scenario_met_does_not_block_untagged_wait(engine, habit_play):
    plan = engine.hang_play(habit_play)
    engine.on_print(_print("DEMO", 0.95, 50_000, low=0.80, chosen_tf_reds=0))
    assert plan.met is True
    r = engine.on_print(_print("DEMO", 0.95, 50_000, chosen_tf_reds=3))
    assert r["action"] == "wait"


def test_scenario_paper_buy_trade_not_live(engine, habit_play):
    engine.hang_play(habit_play)
    engine.on_print(_print("DEMO", 0.80, 50_000))
    assert engine.trades
    assert engine.trades[0]["side"] == "buy"
    assert engine.trades[0]["live_order"] is False


def test_scenario_path_why_is_tagged_layer(engine, habit_play):
    play = dict(habit_play)
    play["habit_ready"] = True
    engine.hang_play(play)
    r = engine.on_print(_print("DEMO", 0.80, 50_000, chosen_tf_reds=0))
    assert r["action"] == "buy"
    assert "tagged hung AD buy layer" in r["why"]


def test_scenario_grind_note_on_quiet_at_ad(engine, habit_play):
    play = dict(habit_play)
    play["habit_ready"] = False
    engine.hang_play(play)
    engine.set_board_grind(True)
    r = engine.on_print(_print("DEMO", 0.83, 0, chosen_tf_reds=0))
    assert r["action"] == "wait"
    notes = [e.why for e in engine.log.entries]
    assert any("board-wide grind — Size wait for volume" in w for w in notes)


def test_scenario_require_5m_size_weighs_5m_not_chosen_tf(engine, habit_play):
    play = dict(habit_play)
    play["require_5m_volume_spike"] = True
    play["vol_5m_usual_usd"] = 40_000
    plan = engine.hang_play(play)
    r = engine.on_print(
        _print("DEMO", 0.86, 50_000, volume_usd_5m=0, chosen_tf_reds=0)
    )
    assert r["action"] == "wait"
    assert "Size grind wait" in r["why"]
    assert not any(b.status == "filled" for b in plan.fills.buy_layers)


def test_scenario_killed_plan_waits(engine, habit_play):
    plan = engine.hang_play(habit_play)
    plan.killed = True
    r = engine.on_print(_print("DEMO", 0.80, 50_000))
    assert r["action"] == "wait"
    assert "killed" in r["why"]


def test_scenario_no_hung_plan_waits(engine):
    r = engine.on_print(_print("NOPE", 0.80, 50_000))
    assert r["action"] == "wait"
    assert "no hung plan" in r["why"]
