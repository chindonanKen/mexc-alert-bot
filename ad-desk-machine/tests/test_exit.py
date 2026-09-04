"""Exit live-read prove: into base, panic-like volume, defensive, no invent, static fill."""

from __future__ import annotations

from machine.exit import (
    ExitFacts,
    ExitLiveState,
    RemainingCost,
    load_exit_facts,
    live_read_exit,
    parse_base_zone,
    remaining_cost_average,
    remaining_cost_from_fill_events,
    score_bounce_kind,
    snapshot_sells,
)
from machine.feeds import Print
from machine.fills import FillEvent, FillState, remaining_cost_from_state, try_fill_sells
from machine.size import SellLayer, load_sell_layers


def _live_plan(engine, *, sells=None, exit_facts=None, ad_top=1.0, ad_bottom=0.8):
    play = {
        "id": "EXIT1",
        "name": "EXIT1",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "chosen_tf_reds_into_met": 2,
        "faster_tf_reds_at_low": 2,
        "vol_at_bottom_usd": 10_000,
        "habit_ready": True,
        "ad_top": ad_top,
        "ad_bottom": ad_bottom,
        "play_usd": 100,
        "layers": [
            {"idx": 1, "price": 0.86, "usd": 20, "share_pct": 20, "role": "AD"},
            {"idx": 2, "price": 0.82, "usd": 30, "share_pct": 30, "role": "AD"},
            {"idx": 3, "price": 0.80, "usd": 50, "share_pct": 50, "role": "AD"},
        ],
        "sell_layers": sells
        if sells is not None
        else [
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.92, "usd": 30, "why": "big_base"},
            {"idx": 3, "price": 0.96, "usd": 50, "why": "usual_bounce"},
        ],
    }
    if exit_facts is not None:
        play["exit_facts"] = exit_facts
    plan = engine.hang_play(play)
    # Buy into live at AD
    engine.on_print(
        Print(
            name="EXIT1",
            price=0.80,
            low=0.80,
            chosen_tf_reds=2,
            faster_tf_reds={"5m": 2},
            volume_usd=50_000,
        )
    )
    assert plan.state == "live"
    return plan


def test_parse_base_zone_ranges():
    assert parse_base_zone("0.081–0.086") == (0.081, 0.086)
    lo, hi = parse_base_zone("~0.041")
    assert lo < 0.041 < hi


def test_load_exit_facts_from_syn_file():
    facts = load_exit_facts("data/.grokbot/SYNUSDT_4h_exit_facts.json")
    assert facts.has_repeat
    assert facts.usual_bounce_abs is not None
    assert facts.bases  # big bases present
    assert facts.vol_ratio_panic_like is not None
    assert facts.vol_ratio_panic_like >= 3.0
    assert facts.vol_at_low_usd is not None
    assert facts.candles_to_bounce == 3  # repeating start from two mets


def test_into_big_base_forces_sell(engine):
    """Current price into a big base → sell invested bag (do not wait for bounce-length)."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [{"zone": "0.90–0.93"}],
        "volume": {"source": {"low_bar_usd": 10_000, "ratio": 3.2}},
    }
    plan = _live_plan(engine, exit_facts=facts)
    rem_before = [s.price for s in plan.fills.remaining_sells()]
    assert rem_before  # hung sells present
    # Bounce into the base zone (still below hung 0.92 until live-read pulls)
    r = engine.on_print(
        Print(name="EXIT1", price=0.91, low=0.90, volume_usd=5_000, chosen_tf_reds=0)
    )
    assert any("big base" in x for x in r.get("exit_live", []))
    # Invested bag sold (force fill at current)
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells, "into big base must force sell fills"
    assert plan.fills.remaining_sells() == [] or all(
        s.status == "filled" for s in plan.fills.sell_layers
    )


def test_panic_like_volume_accelerates(engine):
    """Panic-like volume on the way up → sell matching amount; do not wait for usual height."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.16},
        "big_bases": [],
        "volume": {
            "at_low_bar_4h_usd": {"mid": 10_000},
            "source": {"low_bar_usd": 10_000, "ratio": 3.2},
            "copy": {"low_bar_usd": 10_000, "ratio": 3.5},
        },
    }
    plan = _live_plan(
        engine,
        exit_facts=facts,
        sells=[
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.94, "usd": 30, "why": "usual_bounce"},
            {"idx": 3, "price": 0.98, "usd": 50, "why": "usual_bounce"},
        ],
    )
    # Price still below hung sells; volume ≥ ~3× low-bar
    r = engine.on_print(
        Print(
            name="EXIT1",
            price=0.85,
            low=0.84,
            volume_usd=35_000,  # 3.5× 10k
            chosen_tf_reds=0,
        )
    )
    assert any("panic-like volume" in x for x in r.get("exit_live", []))
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells, "panic-like volume should accelerate at least one sell fill"
    # Not all required — matching amount (nearest layer)
    assert len(sells) >= 1


def test_defensive_after_under_ad_without_board_panic(engine):
    """Drop past AD, not board-wide panic → lower sell layers vs original, not parked at bottom."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
        "volume": {"source": {"low_bar_usd": 10_000, "ratio": 3.2}},
    }
    plan = _live_plan(engine, exit_facts=facts)
    originals = {s["idx"]: s["price"] for s in plan.exit_live.original_sells}
    engine.board_panic = False
    # Print under AD bottom 0.8
    r = engine.on_print(
        Print(name="EXIT1", price=0.75, low=0.74, volume_usd=5_000, chosen_tf_reds=0)
    )
    assert any("defensive" in x for x in r.get("exit_live", []))
    rem = plan.fills.remaining_sells()
    assert rem, "defensive must not invent flat / empty OUT"
    for s in rem:
        assert s.price < originals[s.idx], "sell layers lowered vs original"
        assert s.price > 0.74, "do not park sells at the new bottom"


def test_no_invent_when_empty_sell_layers(engine):
    plan = _live_plan(engine, sells=[], exit_facts={"usual_bounce": {"n": 0}})
    assert plan.fills.sell_layers == []
    before = list(plan.fills.sell_layers)
    r = engine.on_print(
        Print(name="EXIT1", price=0.91, low=0.90, volume_usd=99_000, chosen_tf_reds=0)
    )
    assert plan.fills.sell_layers == before == []
    assert not any(t["side"] == "sell" for t in engine.trades)
    # Direct adapter also invents nothing
    adapt = live_read_exit(
        [],
        ExitFacts(),
        ExitLiveState(),
        current_price=0.91,
        low=0.90,
        volume_usd=99_000,
        ad_bottom=0.8,
    )
    assert adapt.reasons == []
    assert load_sell_layers([]) == []


def test_static_fill_still_works_when_hung_sells_present(engine):
    """Print >= sell price still fills (static), with hung sells present."""
    plan = _live_plan(
        engine,
        sells=[{"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"}],
        exit_facts={"usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.10}},
    )
    assert plan.fills.remaining_sells()
    r = engine.on_print(
        Print(name="EXIT1", price=0.88, low=0.87, volume_usd=1_000, chosen_tf_reds=0)
    )
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells
    assert sells[0]["price"] == 0.88
    assert sells[0]["live_order"] is False
    assert plan.fills.remaining_sells() == []


def test_board_panic_skips_weak_bounce_pull():
    sells = [
        SellLayer(1, 0.90, 20, "usual_bounce"),
        SellLayer(2, 0.95, 30, "usual_bounce"),
    ]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.84
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    prices_before = [s.price for s in sells]
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.83,
        low=0.82,
        volume_usd=1_000,
        ad_bottom=0.80,
        board_panic=True,
        weak_bounce=True,
    )
    assert any("board-wide panic" in r for r in adapt.reasons)
    assert [s.price for s in sells] == prices_before


def test_try_fill_sells_unit_static():
    state_sells = [
        SellLayer(1, 0.88, 20, "usual_bounce"),
        SellLayer(2, 0.95, 30, "usual_bounce"),
    ]
    from machine.fills import FillState

    st = FillState(buy_layers=[], sell_layers=state_sells)
    ev = try_fill_sells(st, 0.88)
    assert len(ev) == 1
    assert ev[0].price == 0.88


def test_candles_to_bounce_at_ad_past_count_no_bounce_considers_exit(engine):
    """With candles_to_bounce set, at AD past that count, no bounce → consider exit."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
        "candles_to_bounce": 3,
        "volume": {"source": {"low_bar_usd": 10_000, "ratio": 3.2}},
    }
    plan = _live_plan(
        engine,
        exit_facts=facts,
        sells=[
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.94, "usd": 30, "why": "usual_bounce"},
        ],
        ad_top=1.0,
        ad_bottom=0.8,
    )
    # Still in AD band (band_high = 0.8 + 0.05*0.2 = 0.81), sideways, 3 candles passed
    r = engine.on_print(
        Print(
            name="EXIT1",
            price=0.805,
            low=0.80,
            volume_usd=1_000,
            chosen_tf_reds=0,
            candles_since_ad_tag=3,
        )
    )
    assert any("sideways too long" in x for x in r.get("exit_live", [])), r
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells, "past candles_to_bounce with no bounce must consider exit fills"
    assert plan.exit_facts.candles_to_bounce == 3


def test_without_candles_to_bounce_does_not_use_sideways_line(engine):
    """No repeating candles_to_bounce → do not invent / do not use the line."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
        # no candles_to_bounce
        "volume": {"source": {"low_bar_usd": 10_000, "ratio": 3.2}},
    }
    plan = _live_plan(
        engine,
        exit_facts=facts,
        sells=[
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.94, "usd": 30, "why": "usual_bounce"},
        ],
    )
    assert plan.exit_facts.candles_to_bounce is None
    # Ignore entry-print fills (panic-vol etc.); this line is about sideways unused.
    engine.trades.clear()
    prices_before = [s.price for s in plan.fills.remaining_sells()]
    assert prices_before, "need remaining sells to prove the line stays unused"
    r = engine.on_print(
        Print(
            name="EXIT1",
            price=0.805,
            low=0.80,
            volume_usd=1_000,
            chosen_tf_reds=0,
            candles_since_ad_tag=99,
        )
    )
    assert not any("sideways too long" in x for x in r.get("exit_live", []))
    assert not any(t["side"] == "sell" for t in engine.trades)
    assert [s.price for s in plan.fills.remaining_sells()] == prices_before


def test_candles_to_bounce_single_met_not_repeating():
    """One finished met candle count is not a repeating start — leave unused."""
    facts = load_exit_facts(
        {
            "candles_to_bounce_after_ad_tag": {
                "source_met": {"candles_4h": 3},
                "repeating_start": "no line",
            }
        }
    )
    assert facts.candles_to_bounce is None
    facts2 = load_exit_facts(
        {"candles_to_bounce_after_ad_tag": {"source_met": {"candles_4h": 3}}}
    )
    assert facts2.candles_to_bounce is None  # n=1 → do not invent


def test_candles_to_bounce_unit_no_bounce_forces_exit():
    sells = [
        SellLayer(1, 0.90, 20, "usual_bounce"),
        SellLayer(2, 0.95, 30, "usual_bounce"),
    ]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.805  # still inside AD band
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12, candles_to_bounce=3)
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.805,
        low=0.80,
        volume_usd=1_000,
        ad_bottom=0.80,
        at_ad=True,
        candles_since_ad_tag=4,
        ad_band_high=0.81,
    )
    assert any("sideways too long" in r for r in adapt.reasons)
    assert adapt.force_fill
    assert all(s.price <= 0.805 for s in sells if s.status == "remaining")


def test_candles_not_yet_passed_does_not_exit():
    sells = [SellLayer(1, 0.90, 20, "usual_bounce")]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.805
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12, candles_to_bounce=3)
    before = sells[0].price
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.805,
        low=0.80,
        ad_bottom=0.80,
        at_ad=True,
        candles_since_ad_tag=2,  # not yet
        ad_band_high=0.81,
    )
    assert not any("sideways too long" in r for r in adapt.reasons)
    assert sells[0].price == before


def test_remaining_cost_average_formula():
    """leftover avg = (bought USD − sold USD) / remaining qty."""
    assert remaining_cost_average(200.0, 150.0, 50.0) == 1.0
    # Sell above remaining cost → leftover average goes down
    before = remaining_cost_average(100.0, 0.0, 100.0)  # avg 1.0
    assert before == 1.0
    # Sold $40 of qty at price 1.25 → sold_qty=32, rem_qty=68, rem_avg=(100-40)/68
    after_above = remaining_cost_average(100.0, 40.0, 68.0)
    assert after_above < before
    # Sell below remaining cost → leftover average goes up
    after_below = remaining_cost_average(100.0, 20.0, 75.0)  # sold at ~0.8
    assert after_below > before
    assert remaining_cost_average(100.0, 50.0, 0.0) is None
    # SYN-class negative leftover when sold USD > bought USD
    neg = remaining_cost_average(1000.0, 1200.0, 100.0)
    assert neg is not None and neg < 0


def test_remaining_cost_from_fills_tracks_leftover():
    fills = [
        FillEvent(side="buy", layer_idx=1, price=1.0, usd=100.0),
        FillEvent(side="sell", layer_idx=1, price=1.25, usd=25.0),
    ]
    rc = remaining_cost_from_fill_events(fills)
    assert rc.has_leftover
    assert abs(rc.bought_usd - 100.0) < 1e-12
    assert abs(rc.sold_usd - 25.0) < 1e-12
    # qty bought 100, sold 20 → rem 80; avg (100-25)/80 = 0.9375
    assert abs(rc.remaining_qty - 80.0) < 1e-9
    assert abs(rc.leftover_avg - 0.9375) < 1e-12


def test_leftover_full_exit_above_remaining_cost_on_good_bounce():
    """Leftover may full-exit above remaining cost on usual good bounce."""
    sells = [
        SellLayer(1, 0.95, 30, "usual_bounce"),
        SellLayer(2, 0.99, 50, "usual_bounce"),
    ]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90  # bounce started vs usual 0.12
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    rc = RemainingCost(bought_usd=100.0, sold_usd=40.0, remaining_qty=70.0)
    # leftover avg ≈ 0.857; current 0.90 > avg
    assert rc.leftover_avg is not None and 0.90 > rc.leftover_avg
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        volume_usd=1_000,
        ad_bottom=0.80,
        weak_bounce=False,
        ad_band_high=0.81,
        remaining_cost=rc,
    )
    assert any("leftover full-exit above remaining cost" in r for r in adapt.reasons)
    assert adapt.force_fill
    assert all(s.price <= 0.90 for s in sells if s.status == "remaining")


def test_leftover_no_full_exit_on_weak_bounce():
    sells = [SellLayer(1, 0.95, 30, "usual_bounce")]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    rc = RemainingCost(bought_usd=100.0, sold_usd=40.0, remaining_qty=70.0)
    before = sells[0].price
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        ad_bottom=0.80,
        weak_bounce=True,
        ad_band_high=0.81,
        remaining_cost=rc,
    )
    assert not any("leftover full-exit" in r for r in adapt.reasons)
    # weak path may pull; must not force leftover full-exit
    assert not adapt.force_fill or "weak bounce" in " ".join(adapt.reasons)


def test_leftover_no_full_exit_below_remaining_cost():
    sells = [SellLayer(1, 0.95, 30, "usual_bounce")]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    rc = RemainingCost(bought_usd=100.0, sold_usd=10.0, remaining_qty=50.0)
    # leftover avg = 1.8; current 0.90 is below
    assert rc.leftover_avg is not None and 0.90 < rc.leftover_avg
    before = sells[0].price
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        ad_bottom=0.80,
        weak_bounce=False,
        ad_band_high=0.81,
        remaining_cost=rc,
    )
    assert not any("leftover full-exit" in r for r in adapt.reasons)
    assert sells[0].price == before


def test_leftover_no_full_exit_without_prior_sells():
    """Not leftover yet — leave room; do not sell the open above entry."""
    sells = [SellLayer(1, 0.95, 30, "usual_bounce")]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    rc = RemainingCost(bought_usd=100.0, sold_usd=0.0, remaining_qty=125.0)
    assert not rc.has_leftover
    before = sells[0].price
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        ad_bottom=0.80,
        weak_bounce=False,
        ad_band_high=0.81,
        remaining_cost=rc,
    )
    assert not any("leftover full-exit" in r for r in adapt.reasons)
    assert sells[0].price == before


def test_engine_leftover_full_exit_simulated_fills(engine):
    """After a sell above cost, Machine can full-exit leftover on usual good bounce."""
    # Quiet volume on entry so panic-like volume does not steal the first sell.
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
    }
    play = {
        "id": "EXIT1",
        "name": "EXIT1",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "chosen_tf_reds_into_met": 2,
        "faster_tf_reds_at_low": 2,
        "vol_at_bottom_usd": 10_000,
        "habit_ready": True,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 100,
        "layers": [
            {"idx": 1, "price": 0.86, "usd": 20, "share_pct": 20, "role": "AD"},
            {"idx": 2, "price": 0.82, "usd": 30, "share_pct": 30, "role": "AD"},
            {"idx": 3, "price": 0.80, "usd": 50, "share_pct": 50, "role": "AD"},
        ],
        "sell_layers": [
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.96, "usd": 30, "why": "usual_bounce"},
            {"idx": 3, "price": 1.00, "usd": 50, "why": "usual_bounce"},
        ],
        "exit_facts": facts,
    }
    plan = engine.hang_play(play)
    engine.on_print(
        Print(
            name="EXIT1",
            price=0.80,
            low=0.80,
            chosen_tf_reds=2,
            faster_tf_reds={"5m": 2},
            volume_usd=1_000,
        )
    )
    assert plan.state == "live"
    assert engine.live_orders_allowed is False
    assert len(plan.fills.remaining_sells()) == 3
    # Static fill first sell at 0.88 (above entry) → leftover opens
    engine.on_print(
        Print(name="EXIT1", price=0.88, low=0.80, volume_usd=1_000, chosen_tf_reds=0)
    )
    rc = remaining_cost_from_state(plan.fills)
    assert rc.has_leftover
    assert rc.leftover_avg is not None
    rem_before = plan.fills.remaining_sells()
    assert rem_before, "need leftover sell layers"
    # Bounce continues above leftover avg but still under hung 0.96 → full-exit leftover
    px = max(rc.leftover_avg + 0.01, 0.90)
    assert px < 0.96
    engine.trades.clear()
    r = engine.on_print(
        Print(name="EXIT1", price=px, low=0.80, volume_usd=1_000, chosen_tf_reds=0)
    )
    assert any("leftover full-exit above remaining cost" in x for x in r.get("exit_live", [])), r
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells, "leftover full-exit must simulate sell fills"
    assert all(t["live_order"] is False for t in sells)
    assert engine.live_orders_allowed is False
    assert plan.fills.remaining_sells() == []


def test_leftover_no_invent_when_no_repeat_bounce_book():
    """No repeating bounce book → do not invent leftover full-exit targets."""
    sells = [SellLayer(1, 0.95, 30, "usual_bounce")]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90
    facts = ExitFacts(usual_n=0, usual_bounce_abs=None)  # no repeat
    rc = RemainingCost(bought_usd=100.0, sold_usd=40.0, remaining_qty=70.0)
    before = sells[0].price
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        ad_bottom=0.80,
        weak_bounce=False,
        remaining_cost=rc,
    )
    assert not any("leftover full-exit" in r for r in adapt.reasons)
    assert sells[0].price == before


def test_score_too_early_does_not_sell_first_weak_tick():
    """TOO_EARLY: bounce has not travelled yet vs usual — first weak tick is not the sell."""
    sells = [
        SellLayer(1, 0.90, 20, "usual_bounce"),
        SellLayer(2, 0.95, 30, "usual_bounce"),
    ]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.82  # progress = 0.02/0.12 ≈ 0.167 < 0.25
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    prices_before = [s.price for s in sells]
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.82,
        low=0.80,
        volume_usd=1_000,
        ad_bottom=0.80,
        weak_bounce=False,
        ad_band_high=0.81,
    )
    assert adapt.bounce_kind == "TOO_EARLY"
    assert any("too early" in r for r in adapt.reasons)
    assert [s.price for s in sells] == prices_before
    assert not adapt.force_fill
    assert not adapt.pulled


def test_score_good_leftover_usual_path():
    """GOOD: tracking usual → leftover full-exit above remaining cost allowed."""
    sells = [
        SellLayer(1, 0.95, 30, "usual_bounce"),
        SellLayer(2, 0.99, 50, "usual_bounce"),
    ]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90  # progress ≈ 0.83 → GOOD
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    score = score_bounce_kind(
        live,
        facts,
        current_price=0.90,
        ad_band_high=0.81,
    )
    assert score.kind == "GOOD"
    rc = RemainingCost(bought_usd=100.0, sold_usd=40.0, remaining_qty=70.0)
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        volume_usd=1_000,
        ad_bottom=0.80,
        weak_bounce=False,
        ad_band_high=0.81,
        remaining_cost=rc,
    )
    assert adapt.bounce_kind == "GOOD"
    assert any("leftover full-exit above remaining cost" in r for r in adapt.reasons)
    assert adapt.force_fill


def test_score_weak_pulls_layers_from_tape():
    """WEAK auto from tape (no Print.weak_bounce): stalled under usual floor → pull layers."""
    sells = [
        SellLayer(1, 0.92, 20, "usual_bounce"),
        SellLayer(2, 0.96, 30, "usual_bounce"),
    ]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.85  # progress ≈ 0.417 in [0.25, 0.50)
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    # Off the bounce high = stall
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.83,
        low=0.82,
        volume_usd=1_000,
        ad_bottom=0.80,
        weak_bounce=False,
        ad_band_high=0.81,
    )
    assert adapt.bounce_kind == "WEAK"
    assert any("weak bounce" in r for r in adapt.reasons)
    assert adapt.pulled
    assert all(s.price <= 0.85 for s in sells if s.status == "remaining")
    assert max(s.price for s in sells) < 0.96


def test_score_fail_considers_exit_from_tape():
    """FAIL auto: candles-to-start passed at AD, no bounce → consider exit."""
    sells = [
        SellLayer(1, 0.90, 20, "usual_bounce"),
        SellLayer(2, 0.95, 30, "usual_bounce"),
    ]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.805
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12, candles_to_bounce=3)
    score = score_bounce_kind(
        live,
        facts,
        current_price=0.805,
        at_ad=True,
        candles_since_ad_tag=4,
        ad_band_high=0.81,
    )
    assert score.kind == "FAIL"
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.805,
        low=0.80,
        volume_usd=1_000,
        ad_bottom=0.80,
        at_ad=True,
        candles_since_ad_tag=4,
        ad_band_high=0.81,
        weak_bounce=False,
    )
    assert adapt.bounce_kind == "FAIL"
    assert any("sideways too long" in r for r in adapt.reasons)
    assert adapt.force_fill
    assert all(s.price <= 0.805 for s in sells if s.status == "remaining")


def test_weak_bounce_flag_optional_override():
    """Print.weak_bounce still forces WEAK even when tape would score GOOD."""
    sells = [SellLayer(1, 0.95, 30, "usual_bounce")]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90  # would be GOOD
    facts = ExitFacts(usual_n=2, usual_bounce_abs=0.12)
    score_open = score_bounce_kind(
        live, facts, current_price=0.90, ad_band_high=0.81
    )
    assert score_open.kind == "GOOD"
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        ad_bottom=0.80,
        weak_bounce=True,
        ad_band_high=0.81,
        remaining_cost=RemainingCost(bought_usd=100.0, sold_usd=40.0, remaining_qty=70.0),
    )
    assert adapt.bounce_kind == "WEAK"
    assert not any("leftover full-exit" in r for r in adapt.reasons)


def test_no_kind_invent_without_usual_bounce_book():
    """No repeating bounce map → do not invent GOOD/WEAK/TOO_EARLY."""
    sells = [SellLayer(1, 0.95, 30, "usual_bounce")]
    live = ExitLiveState(original_sells=snapshot_sells(sells))
    live.bounce_low = 0.80
    live.bounce_high = 0.90
    facts = ExitFacts(usual_n=0, usual_bounce_abs=None)
    score = score_bounce_kind(
        live, facts, current_price=0.90, ad_band_high=0.81
    )
    assert score.kind is None
    prices_before = [s.price for s in sells]
    adapt = live_read_exit(
        sells,
        facts,
        live,
        current_price=0.90,
        low=0.88,
        ad_bottom=0.80,
        weak_bounce=False,
    )
    assert adapt.bounce_kind is None
    assert [s.price for s in sells] == prices_before
    assert not adapt.pulled


def test_engine_auto_weak_from_tape_no_flag(engine):
    """Engine live-read scores WEAK from tape without Print.weak_bounce."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
    }
    plan = _live_plan(
        engine,
        exit_facts=facts,
        sells=[
            {"idx": 1, "price": 0.92, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.96, "usd": 30, "why": "usual_bounce"},
        ],
    )
    assert engine.live_orders_allowed is False
    # Climb to 0.85 (progress ≈ 0.42), then roll off high → WEAK pull
    engine.on_print(
        Print(name="EXIT1", price=0.85, low=0.80, volume_usd=1_000, chosen_tf_reds=0)
    )
    prices_mid = [s.price for s in plan.fills.remaining_sells()]
    assert prices_mid, "need remaining sells"
    r = engine.on_print(
        Print(
            name="EXIT1",
            price=0.83,
            low=0.82,
            volume_usd=1_000,
            chosen_tf_reds=0,
            weak_bounce=False,
        )
    )
    assert r.get("bounce_kind") == "WEAK"
    assert any("weak bounce" in x for x in r.get("exit_live", [])), r
    rem = plan.fills.remaining_sells()
    assert rem
    assert max(s.price for s in rem) < max(prices_mid)
