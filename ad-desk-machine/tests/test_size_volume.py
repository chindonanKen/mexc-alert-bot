"""Size volume gate, grind-wait, 0.5× late volume, watch_only."""

from machine.feeds import Print
from machine.fills import FillState, try_fill_buys
from machine.size import BuyLayer, is_real_volume


def test_is_real_volume_threshold_and_none():
    assert is_real_volume(40_000, 40_000)
    assert not is_real_volume(39_999, 40_000)
    assert is_real_volume(1, None)
    assert not is_real_volume(0, None)


def test_try_fill_buys_default_unchanged():
    layers = [
        BuyLayer(1, 0.86, 5.0, 5.0, "AD"),
        BuyLayer(2, 0.84, 7.5, 7.5, "AD"),
    ]
    state = FillState(buy_layers=layers)
    events = try_fill_buys(state, 0.84)
    assert len(events) == 2


def test_quiet_volume_at_ad_path_take_fills_band_only(engine, habit_play):
    plan = engine.hang_play(habit_play)
    r = engine.on_print(
        Print(
            name="DEMO",
            price=0.80,
            low=0.80,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 3},
            volume_usd=0,
        )
    )
    assert r["action"] == "buy"
    filled = [b for b in plan.fills.buy_layers if b.status == "filled"]
    assert filled
    assert all(b.role == "AD" and b.price <= plan.ad.band_high for b in filled)
    assert not any(b.role == "panic" and b.status == "filled" for b in plan.fills.buy_layers)


def test_quiet_volume_cancels_upper_then_half_scale_on_late_volume(engine, habit_play):
    plan = engine.hang_play(habit_play)
    # Quiet print through layer 1–3 (prices 0.86/0.84/0.82) while still above band take path?
    # Not at AD yet (price 0.85 > band_high 0.81) — Path wait, no cancel via gate.
    # Force Path buy off-AD via board panic, quiet volume → cancel reached AD.
    engine.set_board_panic(True)
    engine.on_print(
        Print(name="DEMO", price=0.82, low=0.82, chosen_tf_reds=1, volume_usd=0)
    )
    cancelled = [b for b in plan.fills.buy_layers if b.status == "cancelled"]
    assert cancelled, "quiet board-panic print should cancel reached AD layers"
    # Real volume at layer 4/5 (price 0.80); earlier AD cancelled → 0.5×
    engine.on_print(
        Print(
            name="DEMO",
            price=0.80,
            low=0.80,
            chosen_tf_reds=1,
            volume_usd=50_000,
        )
    )
    filled_ad = [b for b in plan.fills.buy_layers if b.status == "filled" and b.role == "AD"]
    assert filled_ad
    first = min(filled_ad, key=lambda b: b.idx)
    assert first.idx >= 4
    # 0.5× of original share usd
    assert first.usd == round(habit_play["layers"][first.idx - 1]["usd"] * 0.5, 4)


def test_watch_only_blocks_board_panic_buy(engine, habit_play):
    play = dict(habit_play)
    play["watch_only"] = True
    plan = engine.hang_play(play)
    engine.set_board_panic(True)
    r = engine.on_print(
        Print(
            name="DEMO",
            price=0.80,
            low=0.80,
            chosen_tf_reds=1,
            volume_usd=50_000,
        )
    )
    assert r["action"] == "sit"
    assert "watch_only" in r["why"]
    assert not any(b.status == "filled" for b in plan.fills.buy_layers)


def test_board_grind_wait_why(engine, habit_play):
    plan = engine.hang_play(habit_play)
    engine.set_board_grind(True)
    engine.set_board_panic(True)  # Path buy off-AD
    r = engine.on_print(
        Print(name="DEMO", price=0.83, low=0.83, chosen_tf_reds=1, volume_usd=0)
    )
    assert r["action"] == "wait"
    assert "Size grind wait" in r["why"]
    notes = [e.why for e in engine.log.entries]
    assert any("board-wide grind — Size wait for volume" in w for w in notes)


def test_habit_ready_low_volume_not_at_ad_cancels(engine, habit_play):
    """habit_ready Path buy + volume below threshold off-AD → no fills, cancel AD."""
    plan = engine.hang_play(habit_play)
    engine.set_board_panic(True)
    r = engine.on_print(
        Print(name="DEMO", price=0.85, low=0.85, chosen_tf_reds=3, volume_usd=1_000)
    )
    assert r["action"] == "wait"
    assert "Size grind wait" in r["why"]
    assert not any(b.status == "filled" for b in plan.fills.buy_layers)
    assert any(b.status == "cancelled" for b in plan.fills.buy_layers)


def test_watch_only_on_plan_row(engine, habit_play):
    play = dict(habit_play)
    play["watch_only"] = True
    plan = engine.hang_play(play)
    row = engine.plan_row(plan, sheet=False)
    assert row["watch_only"] is True


def test_fail_add_panic_under_B_without_path_habit(engine):
    """Fail: break AD adds panic half even when habit_ready false (no board panic)."""
    play = {
        "id": "FAIL1",
        "name": "FAIL1",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "habit_ready": False,
        "watch_only": False,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 100,
        "vol_at_bottom_usd": 10_000,
        "layers": [
            {"idx": 1, "price": 0.86, "usd": 5, "share_pct": 5, "role": "AD"},
            {"idx": 5, "price": 0.80, "usd": 15, "share_pct": 15, "role": "AD"},
            {"idx": 6, "price": 0.72, "usd": 10, "share_pct": 10, "role": "panic"},
            {"idx": 7, "price": 0.648, "usd": 15, "share_pct": 15, "role": "panic"},
            {"idx": 8, "price": 0.576, "usd": 25, "share_pct": 25, "role": "panic"},
        ],
        "sell_layers": [],
    }
    plan = engine.hang_play(play)
    # Enter met first at band
    engine.on_print(Print(name="FAIL1", price=0.80, low=0.80, chosen_tf_reds=1, volume_usd=50_000))
    assert plan.met
    # Break under B with volume — Fail add panic
    r = engine.on_print(Print(name="FAIL1", price=0.70, low=0.70, chosen_tf_reds=3, volume_usd=50_000))
    assert r["action"] == "buy"
    assert "Fail" in r["why"]
    filled = [b for b in plan.fills.buy_layers if b.status == "filled"]
    assert filled
    assert all(b.role == "panic" for b in filled)


def test_empty_out_pings_needs_you_on_first_buy(engine, habit_play):
    play = dict(habit_play)
    play["sell_layers"] = []
    play["watch_only"] = False
    plan = engine.hang_play(play)
    engine.on_print(
        Print(
            name="DEMO",
            price=0.80,
            low=0.80,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 3},
            volume_usd=50_000,
        )
    )
    assert plan.state == "live"
    assert any(n.get("kind") == "empty_out_after_buy" for n in engine.needs_you)
    # second buy print should not duplicate ping
    n0 = len(engine.needs_you)
    engine.on_print(
        Print(name="DEMO", price=0.78, low=0.78, chosen_tf_reds=3, volume_usd=50_000)
    )
    assert len(engine.needs_you) == n0
