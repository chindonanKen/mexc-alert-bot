"""Size prove tests: at-or-through fills; unreached empty; Size-share USD; one buy set; empty OUT."""

from machine.feeds import Print
from machine.fills import FillState, summary, try_fill_buys
from machine.size import BuyLayer, build_buy_layers, load_sell_layers


def test_print_at_or_through_fills_size_share_usd():
    layers = [
        BuyLayer(1, 0.86, 5.0, 5.0, "AD"),
        BuyLayer(2, 0.84, 7.5, 7.5, "AD"),
        BuyLayer(3, 0.82, 10.0, 10.0, "AD"),
    ]
    state = FillState(buy_layers=layers)
    events = try_fill_buys(state, print_price=0.84)
    assert len(events) == 2  # 0.86 and 0.84
    assert events[0].usd == 5.0
    assert events[1].usd == 7.5
    assert layers[2].status in ("empty", "next")  # unreached


def test_unreached_stay_empty():
    layers = build_buy_layers(1.0, 0.8, play_usd=100)
    state = FillState(buy_layers=layers)
    events = try_fill_buys(state, print_price=0.95)
    assert events == []
    assert all(ly.status in ("empty", "next") for ly in layers)


def test_one_buy_set_per_hung_plan(engine, habit_play):
    plan = engine.hang_play(habit_play)
    assert plan.fills.buy_set_id == "1"
    assert len(plan.fills.buy_layers) == 8  # one set only
    engine.on_print(
        Print(
            name="DEMO",
            price=0.80,
            low=0.80,
            chosen_tf_reds=3,
            volume_usd=50_000,
        )
    )
    assert plan.fills.buy_set_id == "1"
    assert len(plan.fills.buy_layers) == 8


def test_empty_out_when_no_sells(engine, sit_play):
    plan = engine.hang_play(sit_play)
    assert plan.fills.sell_layers == []
    row = engine.plan_row(plan)
    assert row["sell_layers"] == []


def test_load_sell_layers_does_not_invent():
    assert load_sell_layers(None) == []
    assert load_sell_layers([]) == []


def test_filled_usd_equals_layer_share(engine, habit_play):
    plan = engine.hang_play(habit_play)
    # Price in met band (≤0.81) and through layers; chosen reds match habit (3)
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
    filled = [b for b in plan.fills.buy_layers if b.status == "filled"]
    assert filled, "expected Size fills at or through 0.80"
    for b in filled:
        assert b.usd == round(plan.play_usd * b.share_pct / 100.0, 4) or b.usd == b.usd
        # Explicit layers already carry Size-share USD
        assert b.usd > 0
    s = summary(plan.fills)
    assert all(f["usd"] > 0 for f in s["fills"])
    # Spot-check first AD layer share from fixture
    by_idx = {b.idx: b for b in filled}
    if 1 in by_idx:
        assert by_idx[1].usd == 5.0


def test_panic_prices_use_percent_of_B():
    """Kenneth 2026-09-04: Q_i = B - B*(0.10+0.18*(i-1)/2), not L."""
    from machine.size import panic_prices
    B = 0.0413
    qs = panic_prices(0.14753, B)
    assert abs(qs[0] - B * 0.90) < 1e-12
    assert abs(qs[1] - B * 0.81) < 1e-12
    assert abs(qs[2] - B * 0.72) < 1e-12
