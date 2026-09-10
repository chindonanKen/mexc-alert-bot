"""Exit sell fix: bar-high wick fills; buy bag split across hung sell shares.

live_orders_allowed stays false. Does not invent ticks.
"""

from machine import LIVE_ORDERS_ALLOWED, LIVE_RESTING_SELLS
from machine.feeds import Print, print_from_klines
from machine.fills import FillState, allocate_sell_usd, bag_usd, try_fill_buys, try_fill_sells
from machine.size import BuyLayer, SellLayer


def test_live_orders_and_resting_sells_stay_off():
    assert LIVE_ORDERS_ALLOWED is False
    assert LIVE_RESTING_SELLS is False


def test_print_from_klines_high_is_1m_index_2():
    # [open, o, h, l, c, vol, close_time, quote]
    rows = [[1_700_000_000_000, "0.10", "0.1234", "0.09", "0.095", "100", 1, "9.5"]]
    pr = print_from_klines("SYNUSDT", rows)
    assert pr is not None
    assert pr.price == 0.095
    assert pr.low == 0.09
    assert pr.high == 0.1234


def test_bar_high_wick_fills_sell_when_close_is_below(engine, habit_play):
    """Close below hung sell; 1m high through the sell → paper-sell. Not invent ticks."""
    play = dict(habit_play)
    play["play_usd"] = 100
    play["layers"] = [{"idx": 1, "price": 0.86, "usd": 5, "share_pct": 5, "role": "AD"}]
    play["sell_layers"] = [
        {"idx": 1, "price": 0.90, "usd": 20, "why": "usual_bounce"},
        {"idx": 2, "price": 0.95, "usd": 35, "why": "usual_bounce"},
        {"idx": 3, "price": 1.00, "usd": 45, "why": "big_base"},
    ]
    plan = engine.hang_play(play)
    engine.on_print(
        Print(name="DEMO", price=0.86, low=0.86, high=0.86, volume_usd=50_000, chosen_tf_reds=0)
    )
    assert plan.state == "live"
    assert engine.live_orders_allowed is False
    # Close 0.89 stays under 0.90; high 0.91 tags sell 1
    r = engine.on_print(
        Print(name="DEMO", price=0.89, low=0.88, high=0.91, volume_usd=50_000, chosen_tf_reds=0)
    )
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells, "bar high through sell must fill"
    assert sells[0]["usd"] == 1.0  # $5 buy × 20/100
    assert sells[0]["live_order"] is False
    assert any(f.get("side") == "sell" for f in r.get("fills") or [])
    # Close-only would miss 0.90 — high did the work
    rem = plan.fills.remaining_sells()
    assert all(s.idx != 1 for s in rem)


def test_close_below_sell_without_high_does_not_fill(engine, habit_play):
    play = dict(habit_play)
    play["play_usd"] = 100
    play["layers"] = [{"idx": 1, "price": 0.86, "usd": 5, "share_pct": 5, "role": "AD"}]
    play["sell_layers"] = [
        {"idx": 1, "price": 0.90, "usd": 20, "why": "usual_bounce"},
        {"idx": 2, "price": 0.95, "usd": 35, "why": "usual_bounce"},
        {"idx": 3, "price": 1.00, "usd": 45, "why": "big_base"},
    ]
    engine.hang_play(play)
    engine.on_print(
        Print(name="DEMO", price=0.86, low=0.86, high=0.86, volume_usd=50_000, chosen_tf_reds=0)
    )
    engine.on_print(
        Print(name="DEMO", price=0.89, low=0.88, high=0.89, volume_usd=50_000, chosen_tf_reds=0)
    )
    assert not any(t["side"] == "sell" for t in engine.trades)


def test_buy_five_splits_three_sell_slices():
    """buy $5; hung $20 / $35 / $45 → live $1 / $1.75 / $2.25. Sum $5."""
    state = FillState(
        buy_layers=[BuyLayer(1, 0.86, 5.0, 5.0, "AD")],
        sell_layers=[
            SellLayer(1, 0.90, 20.0, "usual_bounce"),
            SellLayer(2, 0.95, 35.0, "usual_bounce"),
            SellLayer(3, 1.00, 45.0, "big_base"),
        ],
    )
    ev = try_fill_buys(state, 0.86)
    assert ev and ev[0].usd == 5.0
    live = [(s.idx, s.usd) for s in state.remaining_sells()]
    assert live == [(1, 1.0), (2, 1.75), (3, 2.25)]
    assert abs(sum(s.usd for s in state.remaining_sells()) - 5.0) < 1e-9
    assert all(s.plan_usd in (20.0, 35.0, 45.0) for s in state.sell_layers)


def test_first_sell_slice_then_reallocate_leftover():
    state = FillState(
        buy_layers=[BuyLayer(1, 0.86, 5.0, 5.0, "AD", status="filled")],
        sell_layers=[
            SellLayer(1, 0.90, 20.0, "usual_bounce"),
            SellLayer(2, 0.95, 35.0, "usual_bounce"),
            SellLayer(3, 1.00, 45.0, "big_base"),
        ],
        fills=[],
    )
    from machine.fills import FillEvent

    state.fills.append(FillEvent(side="buy", layer_idx=1, price=0.86, usd=5.0, role="AD"))
    allocate_sell_usd(state)
    sells = try_fill_sells(state, 0.90)
    assert len(sells) == 1
    assert sells[0].usd == 1.0
    rem = state.remaining_sells()
    assert [s.idx for s in rem] == [2, 3]
    assert rem[0].usd == 1.75
    assert rem[1].usd == 2.25
    assert abs(sum(s.usd for s in rem) - 4.0) < 1e-9


def test_empty_bag_cancels_phantom_hung_usd():
    state = FillState(
        buy_layers=[BuyLayer(1, 0.86, 5.0, 5.0, "AD", status="filled")],
        sell_layers=[
            SellLayer(1, 0.90, 20.0, "usual_bounce", status="filled"),
            SellLayer(2, 0.95, 35.0, "usual_bounce"),
            SellLayer(3, 1.00, 45.0, "big_base"),
        ],
    )
    from machine.fills import FillEvent

    state.fills.append(FillEvent(side="buy", layer_idx=1, price=0.86, usd=5.0, role="AD"))
    state.fills.append(FillEvent(side="sell", layer_idx=1, price=0.90, usd=5.0, why="usual_bounce"))
    allocate_sell_usd(state)
    assert bag_usd(state) == 0.0
    assert state.remaining_sells() == []
    assert all(s.status == "cancelled" for s in state.sell_layers if s.idx in (2, 3))
    assert all(s.usd == 0.0 for s in state.sell_layers if s.idx in (2, 3))


def test_engine_buy_five_live_sell_slices_not_hung_usd(engine, habit_play):
    play = dict(habit_play)
    play["play_usd"] = 100
    play["layers"] = [{"idx": 1, "price": 0.86, "usd": 5, "share_pct": 5, "role": "AD"}]
    play["sell_layers"] = [
        {"idx": 1, "price": 0.90, "usd": 20, "why": "usual_bounce"},
        {"idx": 2, "price": 0.95, "usd": 35, "why": "usual_bounce"},
        {"idx": 3, "price": 1.00, "usd": 45, "why": "big_base"},
    ]
    plan = engine.hang_play(play)
    engine.on_print(
        Print(name="DEMO", price=0.86, low=0.86, high=0.86, volume_usd=50_000, chosen_tf_reds=0)
    )
    rem = plan.fills.remaining_sells()
    assert [round(s.usd, 4) for s in rem] == [1.0, 1.75, 2.25]
    assert abs(sum(s.usd for s in rem) - 5.0) < 1e-9
    assert not any(s.usd in (20, 35, 45) for s in rem)
    assert engine.live_orders_allowed is False
