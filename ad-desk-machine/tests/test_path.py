"""Path prove tests: habit_ready sit; habit match buy on first chosen red; no fixed count."""

from machine.feeds import Print
from machine.path import PathHabit, PathSnapshot, evaluate_path


def test_habit_ready_false_sits_on_first_and_second_red():
    habit = PathHabit(chosen_tf="15m", habit_ready=False)
    for reds in (1, 2):
        snap = PathSnapshot(
            chosen_tf_reds=reds,
            at_ad=True,
            ad_met=True,
            board_panic=False,
        )
        d = evaluate_path(habit, snap)
        assert d.action == "sit", f"red {reds} should sit when habit_ready false"
        assert "habit_ready false" in d.why


def test_board_panic_buys_even_when_habit_not_ready():
    habit = PathHabit(chosen_tf="15m", habit_ready=False)
    snap = PathSnapshot(
        chosen_tf_reds=1,
        at_ad=True,
        ad_met=True,
        board_panic=True,
    )
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert "panic" in d.why.lower()


def test_habit_match_buys_on_first_chosen_red_via_faster_tf():
    """No fixed 15m≥3 — faster TF reds+volume can buy on first chosen red."""
    habit = PathHabit(
        chosen_tf="15m",
        faster_tfs=["5m"],
        chosen_tf_reds_into_met=3,
        faster_tf_reds_at_low=2,
        vol_at_bottom_usd=40_000,
        habit_ready=True,
    )
    snap = PathSnapshot(
        chosen_tf_reds=1,  # first red of chosen TF
        faster_tf_reds={"5m": 2},
        volume_at_ad_usd=50_000,
        at_ad=True,
        ad_met=True,
    )
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert d.habit_match
    assert "faster" in d.why.lower() or "habit" in d.why.lower()


def test_no_fixed_count_required_chosen_tf_habit():
    """Play with chosen_tf_reds_into_met=2 buys at 2 — not a global 15m≥3 rule."""
    habit = PathHabit(
        chosen_tf="15m",
        chosen_tf_reds_into_met=2,
        habit_ready=True,
        faster_tfs=[],
    )
    snap = PathSnapshot(
        chosen_tf_reds=2,
        at_ad=True,
        ad_met=True,
    )
    d = evaluate_path(habit, snap)
    assert d.action == "buy"


def test_at_ad_alone_not_enough_without_habit_match(engine, habit_play):
    engine.hang_play(habit_play)
    # At AD, habit ready, but only 1 chosen red and no faster match
    r = engine.on_print(
        Print(
            name="DEMO",
            price=0.805,  # in met band (B=0.8, band high=0.81)
            low=0.805,
            chosen_tf_reds=1,
            faster_tf_reds={"5m": 1},
            volume_usd=10_000,
        )
    )
    assert r["action"] == "sit"
    assert r["met"] is True


def test_engine_habit_false_sits(engine, sit_play):
    engine.hang_play(sit_play)
    # Met band for 2.0/1.6: band_high = 1.6 + 0.05*0.4 = 1.62
    r = engine.on_print(
        Print(name="SIT1", price=1.61, low=1.61, chosen_tf_reds=1, volume_usd=99_000)
    )
    assert r["action"] == "sit"
    assert "habit_ready false" in r["why"]


def test_require_5m_volume_spike_sits_on_weak_vol():
    habit = PathHabit(
        chosen_tf="15m",
        chosen_tf_reds_into_met=2,
        habit_ready=True,
        require_5m_volume_spike=True,
        vol_5m_usual_usd=40_000,
    )
    snap = PathSnapshot(
        chosen_tf_reds=2,
        at_ad=True,
        ad_met=True,
        volume_usd_5m=1_000,
    )
    d = evaluate_path(habit, snap)
    assert d.action == "sit"
    assert d.why == "missing 5m volume spike — sit"


def test_require_5m_volume_spike_buys_on_spike():
    habit = PathHabit(
        chosen_tf="15m",
        chosen_tf_reds_into_met=2,
        habit_ready=True,
        require_5m_volume_spike=True,
        vol_5m_usual_usd=40_000,
    )
    snap = PathSnapshot(
        chosen_tf_reds=2,
        at_ad=True,
        ad_met=True,
        volume_usd_5m=50_000,
    )
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert d.habit_match


def test_unset_5m_habit_unchanged():
    habit = PathHabit(
        chosen_tf="15m",
        chosen_tf_reds_into_met=2,
        habit_ready=True,
    )
    snap = PathSnapshot(
        chosen_tf_reds=2,
        at_ad=True,
        ad_met=True,
        volume_usd_5m=0,
    )
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert "5m volume spike" not in d.why


def test_require_5m_none_usual_needs_positive():
    habit = PathHabit(
        chosen_tf="15m",
        chosen_tf_reds_into_met=2,
        habit_ready=True,
        require_5m_volume_spike=True,
        vol_5m_usual_usd=None,
    )
    weak = PathSnapshot(chosen_tf_reds=2, at_ad=True, ad_met=True, volume_usd_5m=0)
    assert evaluate_path(habit, weak).action == "sit"
    assert evaluate_path(habit, weak).why == "missing 5m volume spike — sit"
    ok = PathSnapshot(chosen_tf_reds=2, at_ad=True, ad_met=True, volume_usd_5m=1)
    assert evaluate_path(habit, ok).action == "buy"


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


def test_board_panic_skips_5m_volume_gate():
    habit = PathHabit(
        chosen_tf="15m",
        habit_ready=True,
        chosen_tf_reds_into_met=3,
        require_5m_volume_spike=True,
        vol_5m_usual_usd=40_000,
    )
    snap = PathSnapshot(
        chosen_tf_reds=1,
        at_ad=True,
        ad_met=True,
        board_panic=True,
        volume_usd_5m=0,
    )
    d = evaluate_path(habit, snap)
    assert d.action == "buy"
    assert "panic" in d.why.lower()


def test_engine_sit_then_buy_on_5m_spike(engine, habit_play):
    play = dict(habit_play)
    play["require_5m_volume_spike"] = True
    play["vol_5m_usual_usd"] = 40_000
    engine.hang_play(play)
    weak = engine.on_print(
        Print(
            name="DEMO",
            price=0.805,
            low=0.80,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 2},
            volume_usd=50_000,
            volume_usd_5m=100,
            reds_5m=2,
        )
    )
    assert weak["action"] == "sit"
    assert weak["why"] == "missing 5m volume spike — sit"
    strong = engine.on_print(
        Print(
            name="DEMO",
            price=0.805,
            low=0.80,
            chosen_tf_reds=3,
            faster_tf_reds={"5m": 2},
            volume_usd=50_000,
            volume_usd_5m=50_000,
            reds_5m=2,
        )
    )
    assert strong["action"] == "buy"
    row = engine.plan_row(engine.plans["DEMO"])
    assert row["reds_5m"] == 2
    assert row["vol_usd_5m"] == 50_000
    assert engine.feed[-1]["reds_5m"] == 2
    assert engine.feed[-1]["volume_usd_5m"] == 50_000
