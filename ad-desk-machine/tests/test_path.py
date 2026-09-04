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
