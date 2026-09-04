"""Chart prove tests: met stays met once entered band."""

from machine.chart import AD, is_in_met_band, update_met
from machine.feeds import Print


def test_met_band_last_five_percent_above_b_through_b():
    ad = AD(top=1.0, bottom=0.8)
    # L=0.2; band_high = 0.8 + 0.01 = 0.81
    assert abs(ad.band_high - 0.81) < 1e-12
    assert is_in_met_band(0.81, ad)
    assert is_in_met_band(0.80, ad)
    assert is_in_met_band(0.79, ad)  # through B
    assert not is_in_met_band(0.85, ad)


def test_met_stays_met_once_entered():
    ad = AD(top=1.0, bottom=0.8)
    met = False
    met = update_met(met, low=0.90, ad=ad)
    assert met is False
    met = update_met(met, low=0.805, ad=ad)
    assert met is True
    # Bounce away — still met
    met = update_met(met, low=0.95, ad=ad)
    assert met is True
    met = update_met(met, low=1.0, ad=ad)
    assert met is True


def test_engine_met_stays_met(engine, habit_play):
    plan = engine.hang_play(habit_play)
    engine.on_print(Print(name="DEMO", price=0.90, low=0.90, chosen_tf_reds=0))
    assert plan.met is False
    engine.on_print(Print(name="DEMO", price=0.805, low=0.805, chosen_tf_reds=1))
    assert plan.met is True
    engine.on_print(Print(name="DEMO", price=0.95, low=0.95, chosen_tf_reds=0))
    assert plan.met is True
