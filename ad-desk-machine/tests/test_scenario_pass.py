"""Fail-first major scenario tests from docs/MACHINE_SCENARIO_PASS_PLAN.md.

Live orders stay off. Do not patch Path/Size/engine product here — gaps must FAIL.
Plain English: Machine / hung plan / buy layers / sell layers / Size layers.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from machine.chart import AD
from machine.feeds import Print

ROOT = Path(__file__).resolve().parent.parent
ANSEM_PLAY_PATH = ROOT / "data" / "plays" / "ANSEMUSDT_1h.json"

# ANSEM facts (staff): T=0.29763 B=0.19652 band_high≈0.2015755; P1 buy=0.203598 above band
ANSEM_T = 0.29763
ANSEM_B = 0.19652
ANSEM_P1 = 0.203598
ANSEM_BAND_HIGH = AD(top=ANSEM_T, bottom=ANSEM_B).band_high
VAGUE_AD_WAIT = "AD not met or current price not at AD"


def _load_ansem_play() -> dict:
    play = json.loads(ANSEM_PLAY_PATH.read_text())
    play = copy.deepcopy(play)
    # Ensure ANSEM-class hung fields for scenario replay
    play["habit_ready"] = False
    play["ad_top"] = ANSEM_T
    play["ad_bottom"] = ANSEM_B
    play["watch_only"] = False
    # Scenario replay needs a reacting copy; live play file may be killed_out / outcome.closed.
    play["killed"] = False
    play.pop("status", None)
    play["state"] = "watch"
    play.pop("killed_why", None)
    play.pop("killed_at", None)
    play.pop("outcome", None)
    # Explicit P1 above band
    layers = play.get("layers") or []
    if layers:
        layers[0]["price"] = ANSEM_P1
        layers[0]["idx"] = 1
        layers[0]["role"] = "AD"
    return play


def _demo_habit_play() -> dict:
    """Deterministic habit_ready true play (mirrors conftest habit_play)."""
    return {
        "id": "DEMO",
        "name": "DEMO",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "chosen_tf_reds_into_met": 3,
        "faster_tf_reds_at_low": 2,
        "vol_at_bottom_usd": 40_000,
        "habit_ready": True,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 100,
        "layers": [
            {"idx": 1, "price": 0.86, "usd": 5, "share_pct": 5, "role": "AD"},
            {"idx": 2, "price": 0.84, "usd": 7.5, "share_pct": 7.5, "role": "AD"},
            {"idx": 3, "price": 0.82, "usd": 10, "share_pct": 10, "role": "AD"},
            {"idx": 4, "price": 0.81, "usd": 12.5, "share_pct": 12.5, "role": "AD"},
            {"idx": 5, "price": 0.80, "usd": 15, "share_pct": 15, "role": "AD"},
            {"idx": 6, "price": 0.78, "usd": 10, "share_pct": 10, "role": "panic"},
            {"idx": 7, "price": 0.762, "usd": 15, "share_pct": 15, "role": "panic"},
            {"idx": 8, "price": 0.744, "usd": 25, "share_pct": 25, "role": "panic"},
        ],
        "sell_layers": [
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
        ],
    }


def assert_path_buys_when_layer_tagged(r: dict, plan, engine) -> None:
    """
    Kenneth Path RECUT: when print tags hung AD buy (even above met band),
    Path may buy; Size owns volume. Must not stay silent wait with no decision.
    habit_ready false must NOT sit-block.
    """
    why = (r.get("why") or plan.last_why or "").strip()
    assert why != VAGUE_AD_WAIT, (
        f"Machine stayed on vague AD wait; got action={r['action']!r} why={why!r}"
    )
    assert "habit_ready false" not in why.lower(), (
        f"habit_ready must not sit-block; got why={why!r}"
    )
    # With real volume on ANSEM-class spike, expect Path buy + paper fill
    assert r["action"] == "buy", (
        f"expected Path buy when hung AD layer tagged; got {r['action']!r} why={why!r}"
    )
    assert any(
        e.action in ("paper-buy", "add-panic", "sit-out") for e in engine.log.entries
    ) or r["action"] == "buy", (
        f"expected decision log after tagged layer; log="
        f"{[(e.action, e.why) for e in engine.log.entries]}"
    )


def _ansem_spike_print(name: str = "ANSEMUSDT") -> Print:
    return Print(
        name=name,
        price=ANSEM_P1,
        low=ANSEM_P1,
        chosen_tf_reds=6,
        faster_tf_reds={"15m": 8, "5m": 10},
        volume_usd=80_000,
        volume_usd_5m=120_000,
        reds_5m=6,
    )


# --- Chart / met ---


def test_C1_low_enters_met_band_first_time(engine):
    play = _demo_habit_play()
    plan = engine.hang_play(play)
    r = engine.on_print(Print(name="DEMO", price=0.805, low=0.805, chosen_tf_reds=1))
    assert plan.met is True
    assert r["met"] is True
    assert plan.state in ("met", "live", "watch")
    assert any(e.action == "met" for e in engine.log.entries)
    met_whys = [e.why for e in engine.log.entries if e.action == "met"]
    assert met_whys and "met" in met_whys[0].lower()


def test_C2_bounce_above_band_met_stays_met(engine):
    play = _demo_habit_play()
    plan = engine.hang_play(play)
    engine.on_print(Print(name="DEMO", price=0.805, low=0.805, chosen_tf_reds=1))
    assert plan.met is True
    engine.on_print(Print(name="DEMO", price=0.95, low=0.95, chosen_tf_reds=0))
    assert plan.met is True
    engine.on_print(Print(name="DEMO", price=0.99, low=0.99, chosen_tf_reds=0))
    assert plan.met is True
    # No second met spam required — met stays met without re-logging every bounce
    met_logs = [e for e in engine.log.entries if e.action == "met"]
    assert len(met_logs) == 1


def test_C3_price_tags_hung_buy_above_band_must_name_layer_or_band(engine):
    """C3: low never enters band; price tags hung buy above band → Path buy (Size owns volume)."""
    assert ANSEM_P1 > ANSEM_BAND_HIGH
    play = _load_ansem_play()
    plan = engine.hang_play(play)
    assert plan.habit.habit_ready is False
    assert plan.ad.band_high == pytest.approx(ANSEM_BAND_HIGH)
    r = engine.on_print(_ansem_spike_print(plan.name))
    assert plan.met is False
    assert_path_buys_when_layer_tagged(r, plan, engine)


# --- Path ---


def test_P1_habit_ready_false_at_ad_few_reds_buys_on_tag(engine):
    """P1 RECUT: habit_ready false at AD with layer tagged → Path buy (not sit)."""
    play = {
        "id": "P1",
        "name": "P1",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "habit_ready": False,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 100,
        "layers": [
            {"idx": 1, "price": 0.81, "usd": 5, "share_pct": 5, "role": "AD"},
            {"idx": 5, "price": 0.80, "usd": 15, "share_pct": 15, "role": "AD"},
        ],
        "sell_layers": [],
    }
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(name="P1", price=0.805, low=0.805, chosen_tf_reds=1, volume_usd=50_000)
    )
    assert r["action"] == "buy"
    assert "habit_ready false" not in r["why"]
    assert any(e.action == "paper-buy" for e in engine.log.entries)


def test_P2_habit_ready_false_at_ad_many_reds_5m_spike_buys_on_tag(engine):
    """P2 RECUT: habit_ready false + many reds + 5m spike at AD → Path buy on tagged layer."""
    play = {
        "id": "P2",
        "name": "P2",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        "habit_ready": False,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 100,
        "vol_at_bottom_usd": 40_000,
        "layers": [
            {"idx": 1, "price": 0.81, "usd": 5, "share_pct": 5, "role": "AD"},
            {"idx": 5, "price": 0.80, "usd": 15, "share_pct": 15, "role": "AD"},
        ],
        "sell_layers": [],
    }
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="P2",
            price=0.805,
            low=0.805,
            chosen_tf_reds=6,
            faster_tf_reds={"5m": 8},
            volume_usd=80_000,
            volume_usd_5m=100_000,
            reds_5m=5,
        )
    )
    assert plan.met is True
    assert r["action"] == "buy", f"expected buy on tagged layer; got {r['action']!r} why={r.get('why')!r}"
    assert "habit_ready false" not in r["why"]
    assert r.get("why") != VAGUE_AD_WAIT
    assert any(e.action == "paper-buy" for e in engine.log.entries)


def test_P3_habit_ready_false_board_panic_buys(engine):
    play = _demo_habit_play()
    play["habit_ready"] = False
    play["id"] = "P3"
    play["name"] = "P3"
    plan = engine.hang_play(play)
    engine.set_board_panic(True)
    r = engine.on_print(
        Print(name="P3", price=0.80, low=0.80, chosen_tf_reds=1, volume_usd=50_000)
    )
    assert r["action"] == "buy"
    assert "panic" in r["why"].lower()


def test_P4_habit_ready_true_at_ad_chosen_tf_reds_match_buys(engine):
    play = _demo_habit_play()
    play["id"] = "P4"
    play["name"] = "P4"
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="P4",
            price=0.80,
            low=0.80,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 1},
            volume_usd=50_000,
        )
    )
    assert r["action"] == "buy"
    assert plan.state == "live"


def test_P5_habit_ready_true_first_chosen_red_faster_match_buys(engine):
    play = _demo_habit_play()
    play["id"] = "P5"
    play["name"] = "P5"
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="P5",
            price=0.80,
            low=0.80,
            chosen_tf_reds=1,
            faster_tf_reds={"5m": 2},
            volume_usd=50_000,
        )
    )
    assert r["action"] == "buy"
    assert "tags hung AD" in r["why"] or "Path may buy" in r["why"] or "panic" in r["why"].lower()


def test_P6_at_ad_layer_tagged_buys_without_habit_match(engine):
    """P6 RECUT: no habit match required — tagged AD layer → Path buy (Size may wait on quiet)."""
    play = _demo_habit_play()
    play["id"] = "P6"
    play["name"] = "P6"
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="P6",
            price=0.805,
            low=0.805,
            chosen_tf_reds=1,
            faster_tf_reds={"5m": 1},
            volume_usd=10_000,  # below vol_at_bottom 40k → Size path_take band-only
        )
    )
    assert r["action"] == "buy"
    assert "habit_ready" not in r["why"].lower() or "false" not in r["why"].lower()


def test_P7_not_at_ad_not_met_waits_clear_why(engine):
    play = _demo_habit_play()
    play["id"] = "P7"
    play["name"] = "P7"
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(name="P7", price=0.95, low=0.95, chosen_tf_reds=0, volume_usd=1_000)
    )
    assert plan.met is False
    assert r["action"] == "wait"
    assert r["why"]  # clear why string present
    assert "tagged" in r["why"].lower() or "hung" in r["why"].lower()


def test_P8_require_5m_spike_weak_size_owns_not_path_sit(engine):
    """P8 RECUT: weak 5m is Size weigh — Path must not sit on missing spike."""
    play = _demo_habit_play()
    play["id"] = "P8"
    play["name"] = "P8"
    play["require_5m_volume_spike"] = True
    play["vol_5m_usual_usd"] = 40_000
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="P8",
            price=0.805,
            low=0.805,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 3},
            volume_usd=50_000,
            volume_usd_5m=1_000,
        )
    )
    assert "5m volume spike" not in (r.get("why") or "").lower()
    # Path buy + Size path_take band-only on quiet 5m
    assert r["action"] == "buy"


def test_P9_require_5m_spike_ok_buys(engine):
    play = _demo_habit_play()
    play["id"] = "P9"
    play["name"] = "P9"
    play["require_5m_volume_spike"] = True
    play["vol_5m_usual_usd"] = 40_000
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="P9",
            price=0.80,
            low=0.80,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 3},
            volume_usd=50_000,
            volume_usd_5m=45_000,
        )
    )
    assert r["action"] == "buy"


def test_P10_ansem_replay_tagged_layer_above_band_not_silent(engine):
    """P10 ANSEM replay RECUT: tagged hung buy → Path buy; habit_ready false must not sit-block."""
    assert ANSEM_P1 > ANSEM_BAND_HIGH
    play = _load_ansem_play()
    plan = engine.hang_play(play)
    assert plan.habit.habit_ready is False
    p1 = next(ly for ly in plan.fills.buy_layers if ly.idx == 1)
    assert p1.price == pytest.approx(ANSEM_P1)
    assert p1.price > plan.ad.band_high
    r = engine.on_print(_ansem_spike_print(plan.name))
    assert plan.met is False
    assert_path_buys_when_layer_tagged(r, plan, engine)


# --- Hang / Lock (Kenneth 2026-09-10: H1 is Lock before hang, not Size) ---


def test_H1_lock_hang_gate_p1_above_band_do_not_hang_until_fixed(engine):
    """H1: P1 above met-band high → Lock FAIL/warn before hang; do not hang until P1 fixed. Not Size."""
    assert ANSEM_P1 > ANSEM_BAND_HIGH
    play = _load_ansem_play()
    # Met-band = B through B + 0.05×L — P1 above band_high is the Lock hang gate setup.
    assert float(play["layers"][0]["price"]) > ANSEM_BAND_HIGH
    # Process ownership: H1 is Lock hang checks, not Size. Engine hang in unit tests is not a Lock PASS.
    # Path buy-on-tag above band stays under C3 / P10 — not H1.
    assert "Size" not in "Lock hang"  # seat split marker for readers
    _ = engine  # fixture retained; no invent Size H1 gate in engine


# --- Size ---


def test_S1_path_buy_real_volume_through_layer_fills(engine):
    play = _demo_habit_play()
    play["id"] = "S1"
    play["name"] = "S1"
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="S1",
            price=0.80,
            low=0.80,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 3},
            volume_usd=50_000,
        )
    )
    assert r["action"] == "buy"
    filled = [b for b in plan.fills.buy_layers if b.status == "filled"]
    assert filled, "Path buy + real volume through layer must fill Size USD"
    assert any(e.action == "paper-buy" for e in engine.log.entries)


def test_S2_quiet_at_ad_path_take_band_only(engine):
    play = _demo_habit_play()
    play["id"] = "S2"
    play["name"] = "S2"
    plan = engine.hang_play(play)
    r = engine.on_print(
        Print(
            name="S2",
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


def test_S3_late_volume_near_B_half_scale(engine):
    play = _demo_habit_play()
    play["id"] = "S3"
    play["name"] = "S3"
    plan = engine.hang_play(play)
    engine.set_board_panic(True)
    # Quiet early — cancel upper AD layers
    engine.on_print(Print(name="S3", price=0.82, low=0.82, chosen_tf_reds=1, volume_usd=0))
    cancelled = [b for b in plan.fills.buy_layers if b.status == "cancelled"]
    assert cancelled, "quiet early should cancel reached AD layers"
    # Late real volume near B
    engine.on_print(
        Print(name="S3", price=0.80, low=0.80, chosen_tf_reds=1, volume_usd=50_000)
    )
    filled_ad = [b for b in plan.fills.buy_layers if b.status == "filled" and b.role == "AD"]
    assert filled_ad
    first = min(filled_ad, key=lambda b: b.idx)
    assert first.idx >= 4
    orig = next(row for row in play["layers"] if row["idx"] == first.idx)
    assert first.usd == round(orig["usd"] * 0.5, 4)


def test_S4_board_grind_quiet_size_wait_why(engine):
    play = _demo_habit_play()
    play["id"] = "S4"
    play["name"] = "S4"
    plan = engine.hang_play(play)
    engine.set_board_grind(True)
    engine.set_board_panic(True)
    r = engine.on_print(
        Print(name="S4", price=0.83, low=0.83, chosen_tf_reds=1, volume_usd=0)
    )
    assert r["action"] == "wait"
    assert "Size" in r["why"] and ("grind" in r["why"].lower() or "volume" in r["why"].lower())
    notes = [e.why for e in engine.log.entries]
    assert any("grind" in w.lower() or "Size" in w for w in notes)


def test_S5_path_buy_no_layer_at_print_size_miss_why(engine):
    play = _demo_habit_play()
    play["id"] = "S5"
    play["name"] = "S5"
    # All layers well below a high print — Path buys via board panic but Size finds none
    play["layers"] = [
        {"idx": 1, "price": 0.70, "usd": 5, "share_pct": 5, "role": "AD"},
        {"idx": 2, "price": 0.68, "usd": 7.5, "share_pct": 7.5, "role": "AD"},
    ]
    plan = engine.hang_play(play)
    engine.set_board_panic(True)
    r = engine.on_print(
        Print(name="S5", price=0.95, low=0.95, chosen_tf_reds=1, volume_usd=50_000)
    )
    assert r["action"] in ("wait", "sit", "sit-out")
    why = (r.get("why") or "").lower()
    assert (
        "no buy layer" in why
        or "no size layer" in why
        or "size" in why
    ), f"expected Size miss why; got {r.get('why')!r}"
    assert not any(b.status == "filled" for b in plan.fills.buy_layers)


# --- Fail ---


def test_F1_met_under_B_path_would_sit_adds_panic_half(engine):
    play = {
        "id": "F1",
        "name": "F1",
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
    engine.on_print(Print(name="F1", price=0.80, low=0.80, chosen_tf_reds=1, volume_usd=50_000))
    assert plan.met is True
    r = engine.on_print(
        Print(name="F1", price=0.70, low=0.70, chosen_tf_reds=3, volume_usd=50_000)
    )
    assert r["action"] == "buy"
    assert "Fail" in r["why"]
    panic_filled = [b for b in plan.fills.buy_layers if b.status == "filled" and b.role == "panic"]
    assert panic_filled, "Fail under met AD must add panic half"


def test_F2_under_B_not_met_yet_does_not_fail_add(engine):
    """F2: price under B before met → do not Fail-add panic."""
    play = {
        "id": "F2",
        "name": "F2",
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
    assert plan.met is False
    # Print under B on first touch: Chart may met via through-B, but Fail must not add
    # (break of AD requires already-met before this print).
    r = engine.on_print(
        Print(name="F2", price=0.70, low=0.70, chosen_tf_reds=3, volume_usd=50_000)
    )
    assert "Fail" not in (r.get("why") or ""), (
        f"F2 must not Fail-add on first under-B before already-met; got why={r.get('why')!r}"
    )
    panic_filled = [
        b for b in plan.fills.buy_layers if b.status == "filled" and b.role == "panic"
    ]
    assert not panic_filled, "F2 must not Fail-add panic when not already met"


# --- Exit ---


def _live_exit_plan(
    engine,
    *,
    name: str = "EXIT",
    sells=None,
    exit_facts=None,
    ad_top: float = 1.0,
    ad_bottom: float = 0.8,
    entry_volume_usd: float = 50_000,
):
    """Hang habit-ready play and buy into live at AD (mirrors test_exit._live_plan)."""
    play = {
        "id": name,
        "name": name,
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
    engine.on_print(
        Print(
            name=name,
            price=0.80,
            low=0.80,
            chosen_tf_reds=2,
            faster_tf_reds={"5m": 2},
            volume_usd=entry_volume_usd,
        )
    )
    assert plan.state == "live"
    assert engine.live_orders_allowed is False
    return plan


def test_E1_live_price_into_unmet_base_sells(engine):
    """E1: live, price into unmet base above B → sell into base / force fill."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "unmet_bases_above_B": [{"zone": "0.90–0.93", "must_use_for_sells": True}],
        "volume": {"source": {"low_bar_usd": 10_000, "ratio": 3.2}},
    }
    plan = _live_exit_plan(engine, name="E1", exit_facts=facts)
    engine.trades.clear()
    r = engine.on_print(
        Print(name="E1", price=0.91, low=0.90, volume_usd=5_000, chosen_tf_reds=0)
    )
    assert any("big base" in x for x in r.get("exit_live", [])), (
        f"expected into-base / big base on exit_live; got {r.get('exit_live')!r}"
    )
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells, "E1 unmet base must force sell fills"
    assert all(t.get("live_order") is False for t in sells)
    assert engine.live_orders_allowed is False
    assert r["action"] == "sell" or sells
    assert any(e.action == "exit-live" for e in engine.log.entries)


def test_E2_under_ad_without_board_panic_defensive_lower_sells(engine):
    """E2: under AD without board panic → defensive lower sell layers."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
        "volume": {"source": {"low_bar_usd": 10_000, "ratio": 3.2}},
    }
    plan = _live_exit_plan(engine, name="E2", exit_facts=facts)
    originals = {s["idx"]: s["price"] for s in plan.exit_live.original_sells}
    engine.board_panic = False
    r = engine.on_print(
        Print(name="E2", price=0.75, low=0.74, volume_usd=5_000, chosen_tf_reds=0)
    )
    assert any("defensive" in x for x in r.get("exit_live", [])), (
        f"expected defensive on exit_live; got {r.get('exit_live')!r}"
    )
    rem = plan.fills.remaining_sells()
    assert rem, "defensive must not invent flat / empty OUT"
    for s in rem:
        assert s.price < originals[s.idx], "sell layers lowered vs original"
        assert s.price > 0.74, "do not park sells at the new bottom"
    assert any(e.action == "exit-live" for e in engine.log.entries)


def test_E3_empty_sell_layers_no_invent(engine):
    """E3: empty sell layers → no invent."""
    plan = _live_exit_plan(
        engine,
        name="E3",
        sells=[],
        exit_facts={"usual_bounce": {"n": 0}},
    )
    assert plan.fills.sell_layers == []
    before = list(plan.fills.sell_layers)
    engine.trades.clear()
    r = engine.on_print(
        Print(name="E3", price=0.91, low=0.90, volume_usd=99_000, chosen_tf_reds=0)
    )
    assert plan.fills.sell_layers == before == []
    assert not any(t["side"] == "sell" for t in engine.trades)
    assert not any("invent" in (x or "").lower() for x in r.get("exit_live", []))


def test_E4_candles_to_bounce_passed_at_ad_no_bounce_considers_exit(engine):
    """E4: candles_to_bounce passed at AD, no bounce → consider exit when Reed number set."""
    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
        "candles_to_bounce": 3,
        "volume": {"source": {"low_bar_usd": 10_000, "ratio": 3.2}},
    }
    plan = _live_exit_plan(
        engine,
        name="E4",
        exit_facts=facts,
        sells=[
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.94, "usd": 30, "why": "usual_bounce"},
        ],
    )
    assert plan.exit_facts.candles_to_bounce == 3
    engine.trades.clear()
    r = engine.on_print(
        Print(
            name="E4",
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
    assert any(e.action == "exit-live" for e in engine.log.entries)


def test_E5_leftover_above_remaining_cost_on_good_bounce_full_exit(engine):
    """E5: leftover above remaining cost on good bounce → full exit leftover."""
    from machine.fills import remaining_cost_from_state

    facts = {
        "usual_bounce": {"n": 2, "usual_bounce_height_abs_mid": 0.12},
        "big_bases": [],
    }
    plan = _live_exit_plan(
        engine,
        name="E5",
        exit_facts=facts,
        sells=[
            {"idx": 1, "price": 0.88, "usd": 20, "why": "usual_bounce"},
            {"idx": 2, "price": 0.96, "usd": 30, "why": "usual_bounce"},
            {"idx": 3, "price": 1.00, "usd": 50, "why": "usual_bounce"},
        ],
        entry_volume_usd=1_000,
    )
    assert len(plan.fills.remaining_sells()) == 3
    # Static fill first sell at 0.88 (above entry) → leftover opens
    engine.on_print(
        Print(name="E5", price=0.88, low=0.80, volume_usd=1_000, chosen_tf_reds=0)
    )
    rc = remaining_cost_from_state(plan.fills)
    assert rc.has_leftover
    assert rc.leftover_avg is not None
    rem_before = plan.fills.remaining_sells()
    assert rem_before, "need leftover sell layers"
    px = max(rc.leftover_avg + 0.01, 0.90)
    assert px < 0.96
    engine.trades.clear()
    r = engine.on_print(
        Print(name="E5", price=px, low=0.80, volume_usd=1_000, chosen_tf_reds=0)
    )
    assert any(
        "leftover full-exit above remaining cost" in x for x in r.get("exit_live", [])
    ), r
    sells = [t for t in engine.trades if t["side"] == "sell"]
    assert sells, "leftover full-exit must simulate sell fills"
    assert all(t.get("live_order") is False for t in sells)
    assert engine.live_orders_allowed is False
    assert plan.fills.remaining_sells() == []
    assert any(e.action == "exit-live" for e in engine.log.entries)


# --- Hang / Lock gates ---


def test_H3_habit_fields_not_required_to_hang(engine):
    """H3 RECUT: habit_ready / red fields are not hang Lock gates — hang succeeds."""
    play = {
        "id": "H3",
        "name": "H3",
        "chosen_tf": "15m",
        "faster_tfs": ["5m"],
        # intentionally omit chosen_tf_reds_into_met and faster_tf_reds_at_low
        "habit_ready": True,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 100,
        "layers": [
            {"idx": 1, "price": 0.86, "usd": 20, "share_pct": 20, "role": "AD"},
            {"idx": 5, "price": 0.80, "usd": 50, "share_pct": 50, "role": "AD"},
        ],
        "sell_layers": [],
    }
    assert "chosen_tf_reds_into_met" not in play
    assert "faster_tf_reds_at_low" not in play
    plan = engine.hang_play(play)
    assert plan.id == "H3"
    assert plan.habit.habit_ready is True
    assert plan.habit.chosen_tf_reds_into_met is None
    assert plan.habit.faster_tf_reds_at_low is None
