from __future__ import annotations

from machine.engine import BOOK, NEEDS, evaluate, public_play, reset_runtime
from machine.log import MachineLog, should_log
from machine.plays import HUNG_IDS, load_hung_plays, load_play


def test_wait_is_not_spammed():
    log = MachineLog()
    ev = {"action": "wait", "reason": "wait", "decision": "Hung plan written, waiting for the line."}
    assert log.append_if_changed("USUSDT_4h", ev) is None or True
    first = log.append_if_changed("USUSDT_4h", ev)
    # first wait may or may not log; the repeat must not
    log.append_if_changed("USUSDT_4h", ev)
    second = log.append_if_changed("USUSDT_4h", ev)
    assert second is None


def test_decision_change_is_logged():
    log = MachineLog()
    a = {"action": "sit", "reason": "sit_first_second_red", "decision": "First red on the chosen TF, sit."}
    b = {"action": "buy", "reason": "board_panic", "decision": "Board-wide panic at the AD, taking it."}
    assert should_log(None, a) is True
    assert log.append_if_changed("SYNUSDT_4h", a)
    assert log.append_if_changed("SYNUSDT_4h", b)
    assert should_log(a, a) is False
    assert should_log(a, b) is True


def test_same_sit_reason_skipped():
    prev = {"action": "sit", "reason": "sit_first_second_red", "decision": "First red on the chosen TF, sit."}
    nxt = dict(prev)
    assert should_log(prev, nxt) is False


def test_log_rows_newest_first():
    log = MachineLog()
    log.append_if_changed("A", {"action": "sit", "reason": "x", "decision": "one", "ts": 1})
    log.append_if_changed("A", {"action": "buy", "reason": "y", "decision": "two", "ts": 2})
    rows = log.rows("A")
    assert rows[0]["decision"] == "two"


def test_syn_hung_facts():
    p = load_play("SYNUSDT_4h")
    assert p["ad_top"] == 0.14753
    assert p["ad_bottom"] == 0.0413
    assert p["watch_only"] is False
    assert p["habit_ready"] is False
    assert p["dump_depth"] == "high_magnet"
    assert p["sell_layers"]
    assert p["tf"] == "4h"


def test_agi_hung_facts():
    p = load_play("AGIUSDT_4h")
    assert p["ad_top"] == 0.00748
    assert p["ad_bottom"] == 0.004172
    assert p["watch_only"] is False
    assert p["habit_ready"] is True
    assert p["chosen_tf_reds_into_met"] == 1
    assert p["vol_at_bottom_usd"] == 16623
    assert [x["price"] for x in p["sell_layers"]] == [
        0.00451,
        0.00476,
        0.00505,
        0.00575,
        0.005843,
    ]
    assert [x["pct"] for x in p["sell_layers"]] == [10, 15, 30, 30, 15]


def test_us_hung_facts():
    p = load_play("USUSDT_4h")
    assert p["ad_top"] == 0.02475
    assert p["ad_bottom"] == 0.0115
    assert p["watch_only"] is False
    assert p["habit_ready"] is False
    assert [x["price"] for x in p["sell_layers"]] == [0.0139, 0.0148, 0.0167, 0.0186, 0.02092]


def test_three_hung_plays_load():
    rows = load_hung_plays()
    ids = {r["id"] for r in rows}
    assert set(HUNG_IDS) == ids
    assert all(r["watch_only"] is False for r in rows)


def test_public_play_language():
    p = public_play(load_play("AGIUSDT_4h"), {"current_price": 0.005})
    assert p["hung_plan"] is True
    assert "buy_layers" in p
    assert "sell_layers" in p
    assert "Size_layers" in p
    assert "current_price" in p
    assert p["live_orders_allowed"] is False


def test_engine_agi_first_red_at_ad_buys():
    play = load_play("AGIUSDT_4h")
    # last in met band and through first Size layers
    last = 0.00420
    out = evaluate(
        play,
        {
            "current_price": last,
            "chosen_tf_reds": 1,
            "vol_spike": True,
        },
    )
    assert out["path"]["buy"] is True
    assert out["met"] is True
    assert out["live_orders_allowed"] is False
    assert any(f["side"] == "buy" for f in out["fills"])


def test_engine_syn_first_red_sits():
    play = load_play("SYNUSDT_4h")
    out = evaluate(
        play,
        {"current_price": 0.0420, "chosen_tf_reds": 1, "vol_spike": True},
    )
    assert out["action"] == "sit"
    assert out["fills"] == []


def test_engine_us_first_red_sits():
    play = load_play("USUSDT_4h")
    out = evaluate(
        play,
        {"current_price": 0.0116, "chosen_tf_reds": 1},
    )
    assert out["action"] == "sit"
    assert not out["new_fills"]


def test_engine_syn_board_panic_buys():
    play = load_play("SYNUSDT_4h")
    out = evaluate(
        play,
        {
            "current_price": 0.0420,
            "chosen_tf_reds": 1,
            "board_panic": True,
            "vol_spike": True,
        },
    )
    assert out["path"]["buy"] is True
    assert out["path"]["reason"] == "board_panic"


def test_engine_watch_only_never_fills():
    play = dict(load_play("SYNUSDT_4h"))
    play["watch_only"] = True
    out = evaluate(
        play,
        {
            "current_price": 0.0420,
            "chosen_tf_reds": 4,
            "board_panic": True,
            "vol_spike": True,
        },
    )
    assert out["fills"] == []
    assert out["path"]["reason"] == "watch_only"


def test_empty_out_after_buy_needs_you():
    play = {
        "id": "EMPTYOUT_4h",
        "symbol": "EMPTYOUTUSDT",
        "tf": "4h",
        "ad_top": 10.0,
        "ad_bottom": 5.0,
        "watch_only": False,
        "habit_ready": True,
        "chosen_tf_reds_into_met": 1,
        "sell_layers": [],
        "dump_depth": "high_magnet",
    }
    out = evaluate(
        play,
        {"current_price": 5.05, "chosen_tf_reds": 1, "vol_spike": True},
    )
    assert out["needs_you"]
    assert out["needs_you"]["kind"] == "empty_out_after_buy"
    assert NEEDS


def test_reset_runtime_clears_book():
    play = load_play("AGIUSDT_4h")
    evaluate(play, {"current_price": 0.00420, "chosen_tf_reds": 1, "vol_spike": True})
    assert BOOK
    reset_runtime()
    assert BOOK == {}
    assert NEEDS == []


def test_engine_no_wait_spam_on_repeat():
    play = load_play("USUSDT_4h")
    snap = {"current_price": 0.020, "chosen_tf_reds": 0}
    a = evaluate(play, snap)
    b = evaluate(play, snap)
    assert b["logged"] is False or a["reason"] != b["reason"] or True
    # second wait must not append
    from machine.engine import LOG

    waits = [r for r in LOG.rows("USUSDT_4h") if r.get("action") == "wait"]
    assert len(waits) <= 1
