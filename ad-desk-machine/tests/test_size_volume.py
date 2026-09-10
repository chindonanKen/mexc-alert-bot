"""Size standing 2026-09-10: fill reached AD layers. Grind-wait / panic not standing."""

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
        BuyLayer(1, 0.86, 40.0, 20.0, "AD"),
        BuyLayer(2, 0.84, 40.0, 20.0, "AD"),
    ]
    state = FillState(buy_layers=layers)
    events = try_fill_buys(state, 0.84)
    assert len(events) == 2


def test_quiet_volume_still_fills_reached_ad(engine, habit_play):
    """Grind-wait is not a standing entry gate — tagged AD layers fill."""
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
    assert all(b.role == "AD" for b in filled)
    assert not any(b.role == "panic" for b in plan.fills.buy_layers)


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
            chosen_tf_reds=3,
            volume_usd=50_000,
        )
    )
    assert r["action"] == "sit"
    assert "watch_only" in r["why"]
    assert not any(b.status == "filled" for b in plan.fills.buy_layers)


def test_fail_does_not_add_panic_under_B(engine):
    """Kenneth 2026-09-10: Fail add-panic is not standing."""
    play = {
        "id": "FAIL1",
        "name": "FAIL1",
        "chosen_tf": "15m",
        "habit_ready": False,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 200,
        "layers": [
            {"idx": 1, "price": 0.81, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 2, "price": 0.80, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 3, "price": 0.79, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 4, "price": 0.78, "usd": 40, "share_pct": 20, "role": "AD"},
            {"idx": 5, "price": 0.77, "usd": 40, "share_pct": 20, "role": "AD"},
        ],
        "sell_layers": [],
    }
    plan = engine.hang_play(play)
    engine.on_print(Print(name="FAIL1", price=0.805, low=0.805, volume_usd=50_000))
    assert plan.met is True
    engine.on_print(Print(name="FAIL1", price=0.70, low=0.70, volume_usd=50_000))
    assert not any(b.role == "panic" for b in plan.fills.buy_layers)
    assert engine.live_orders_allowed is False
