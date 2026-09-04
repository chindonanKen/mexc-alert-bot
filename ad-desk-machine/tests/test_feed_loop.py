"""Live MEXC feed conversion + decision loop (no invent; loop without POST /simulate)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from machine.engine import Engine
from machine.feeds import (
    MexcLiveFeed,
    ascending_bounce,
    descending_dump,
    print_from_klines,
    trailing_red_count,
)
from machine.loop import DecisionLoop, feed_names_from_engine, feed_tfs_from_engine, sync_feed_names

ROOT = Path(__file__).resolve().parent.parent


def _kline(open_ms: int, o: str, h: str, low: str, c: str, vol: str, quote: str):
    return [open_ms, o, h, low, c, vol, open_ms + 60_000, quote]


def test_trailing_red_count():
    rows = [
        _kline(1, "1", "1", "0.9", "1.1", "1", "1"),  # green
        _kline(2, "1.1", "1.1", "1.0", "1.05", "1", "1"),  # red? 1.05 < 1.1 yes
        _kline(3, "1.05", "1.05", "1.0", "1.0", "1", "1"),  # red
    ]
    assert trailing_red_count(rows) == 2
    assert trailing_red_count(rows[:1]) == 0


def test_print_from_klines_no_invent_on_empty():
    assert print_from_klines("SYNUSDT", []) is None


def test_print_from_klines_uses_real_close_low_quote():
    rows = [_kline(1_700_000_000_000, "0.10", "0.11", "0.09", "0.095", "100", "9.5")]
    chosen = [
        _kline(1, "1", "1", "0.9", "0.9", "1", "1"),  # red
        _kline(2, "0.9", "0.9", "0.8", "0.85", "1", "1"),  # red
    ]
    faster = [_kline(1, "1", "1", "0.9", "0.95", "1", "1")]  # red
    pr = print_from_klines(
        "SYNUSDT",
        rows,
        chosen_tf_klines=chosen,
        faster_tf="1h",
        faster_tf_klines=faster,
    )
    assert pr is not None
    assert pr.name == "SYNUSDT"
    assert pr.price == 0.095
    assert pr.low == 0.09
    # Dollar volume prefers chosen-TF newest bar quote (not the 1m forming bar).
    assert pr.volume_usd == 1.0
    assert pr.chosen_tf_reds == 2
    assert pr.faster_tf_reds == {"1h": 1}
    assert pr.source == "mexc"
    assert pr.open_time_ms == 1_700_000_000_000


def test_mexc_feed_poll_mocked():
    """Feed builds Prints from transport responses — never invents when body empty."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        sym = params.get("symbol")
        interval = params.get("interval")
        if sym != "SYNUSDT":
            return httpx.Response(404, json={"msg": "no"})
        if interval == "1m":
            return httpx.Response(
                200,
                json=[_kline(1000, "0.09", "0.09", "0.088", "0.089", "10", "0.89")],
            )
        if interval == "4h":
            return httpx.Response(
                200,
                json=[
                    _kline(1, "0.1", "0.1", "0.09", "0.09", "1", "1"),
                    _kline(2, "0.09", "0.09", "0.08", "0.085", "1", "1"),
                ],
            )
        if interval == "1h":
            return httpx.Response(
                200,
                json=[_kline(1, "0.1", "0.1", "0.09", "0.095", "1", "1")],
            )
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    feed = MexcLiveFeed(names=["SYNUSDT"], client=client)
    first = feed.poll_once()
    assert len(first) == 1
    assert first[0].price == 0.089
    assert first[0].chosen_tf_reds == 2
    # Identical fingerprint skipped
    second = feed.poll_once()
    assert second == []
    client.close()


def test_mexc_feed_skips_failed_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="err")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    feed = MexcLiveFeed(names=["SYNUSDT"], client=client)
    assert feed.poll_once() == []
    client.close()


def test_decision_loop_step_feeds_engine(habit_play):
    """Loop step → engine.evaluate without POST /simulate."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        interval = params.get("interval")
        # Price in AD met band for DEMO (bottom 0.8, band_high = 0.81)
        if interval == "1m":
            return httpx.Response(
                200,
                json=[_kline(5000, "0.805", "0.81", "0.80", "0.805", "1", "50000")],
            )
        if interval == "4h":
            return httpx.Response(
                200,
                json=[_kline(1, "1", "1", "0.8", "0.9", "1", "1")],  # green → 0 reds
            )
        return httpx.Response(200, json=[_kline(1, "1", "1", "0.8", "0.9", "1", "1")])

    eng = Engine()
    # Rename habit play to match feed symbol path — hang as DEMO, feed DEMO
    habit_play = dict(habit_play)
    plan = eng.hang_play(habit_play)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    feed = MexcLiveFeed(names=[plan.name], client=client, chosen_tf="4h", faster_tf="5m")
    # Override faster interval fetch: MexcLiveFeed uses faster_tf as interval string
    feed.faster_tf = "1h"
    loop = DecisionLoop(engine=eng, feed=feed, interval_sec=0.01)
    results = loop.step_once()
    assert loop.polls == 1
    assert loop.prints_seen == 1
    assert results
    assert eng.feed  # print landed in Machine feed
    assert plan.current_price == 0.805
    client.close()


def test_feed_names_from_hung_plans():
    eng = Engine()
    eng.load_plays_dir(ROOT / "data" / "plays")
    names = feed_names_from_engine(eng)
    assert "SYNUSDT" in names
    assert "AGIUSDT" in names
    assert "USUSDT" in names


def test_hang_play_sync_feed_adds_new_name():
    """API hang hole: hung USDT name must join live feed without restart."""
    from machine.feeds import MexcLiveFeed

    eng = Engine()
    feed = MexcLiveFeed(names=["SYNUSDT", "AGIUSDT", "USUSDT"])
    loop = DecisionLoop(engine=eng, feed=feed, interval_sec=99)
    assert "BPUSDT" not in list(loop.feed.names)
    eng.hang_play(
        {
            "id": "BPUSDT_4h",
            "name": "BPUSDT",
            "chosen_tf": "4h",
            "habit_ready": False,
            "ad_top": 1.0,
            "ad_bottom": 0.8,
            "play_usd": 100,
            "sell_layers": [],
        }
    )
    # hang_play alone leaves feed frozen — sync is required
    assert "BPUSDT" not in list(loop.feed.names)
    names = sync_feed_names(loop, eng)
    assert "BPUSDT" in names
    assert "BPUSDT" in list(loop.feed.names)
    assert isinstance(loop.feed.names, list)
    # Hang also refreshes per-name TFs (4h / fallback 1h)
    assert loop.feed.name_tfs["BPUSDT"] == ("4h", "1h")


def test_feed_tfs_from_hung_plans_use_play_intervals():
    """1d play maps to 1d/4h; 4h play stays 4h/1h; missing TFs fall back."""
    eng = Engine()
    eng.hang_play(
        {
            "id": "BPUSDT_1d",
            "name": "BPUSDT",
            "chosen_tf": "1d",
            "faster_tfs": ["4h"],
            "habit_ready": False,
            "ad_top": 1.0,
            "ad_bottom": 0.8,
            "play_usd": 100,
            "sell_layers": [],
        }
    )
    eng.hang_play(
        {
            "id": "SYNUSDT_4h",
            "name": "SYNUSDT",
            "chosen_tf": "4h",
            "faster_tfs": ["1h"],
            "habit_ready": False,
            "ad_top": 1.0,
            "ad_bottom": 0.8,
            "play_usd": 100,
            "sell_layers": [],
        }
    )
    eng.hang_play(
        {
            "id": "MISSING_TF",
            "name": "MISSINGUSDT",
            "habit_ready": False,
            "ad_top": 1.0,
            "ad_bottom": 0.8,
            "play_usd": 100,
            "sell_layers": [],
        }
    )
    tfs = feed_tfs_from_engine(eng)
    assert tfs["BPUSDT"] == ("1d", "4h")
    assert tfs["SYNUSDT"] == ("4h", "1h")
    assert tfs["MISSINGUSDT"] == ("4h", "1h")


def test_poll_uses_plan_chosen_tf_not_global_4h():
    """Hung 1d plan fetches 1d chosen reds; 4h plan still uses 4h. Not one global pair."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        sym = params.get("symbol") or ""
        interval = params.get("interval") or ""
        seen.append((sym, interval))
        if interval == "1m":
            # Distinct fingerprints so both names emit a print
            open_ms = 2000 if sym == "BPUSDT" else 1000
            return httpx.Response(
                200,
                json=[_kline(open_ms, "0.09", "0.09", "0.088", "0.089", "10", "0.89")],
            )
        if interval == "1d":
            # 3 trailing reds — must be BPUSDT chosen, never SYN
            return httpx.Response(
                200,
                json=[
                    _kline(1, "0.12", "0.12", "0.10", "0.11", "1", "1"),  # red
                    _kline(2, "0.11", "0.11", "0.09", "0.10", "1", "1"),  # red
                    _kline(3, "0.10", "0.10", "0.08", "0.09", "1", "1"),  # red
                ],
            )
        if interval == "4h":
            # 1 trailing red — SYN chosen; BP faster
            return httpx.Response(
                200,
                json=[
                    _kline(1, "0.10", "0.10", "0.09", "0.11", "1", "1"),  # green
                    _kline(2, "0.11", "0.11", "0.09", "0.10", "1", "1"),  # red
                ],
            )
        if interval == "1h":
            # 2 trailing reds — SYN faster only
            return httpx.Response(
                200,
                json=[
                    _kline(1, "0.10", "0.10", "0.09", "0.09", "1", "1"),  # red
                    _kline(2, "0.09", "0.09", "0.08", "0.08", "1", "1"),  # red
                ],
            )
        return httpx.Response(200, json=[])

    eng = Engine()
    eng.hang_play(
        {
            "id": "BPUSDT_1d",
            "name": "BPUSDT",
            "chosen_tf": "1d",
            "faster_tfs": ["4h"],
            "habit_ready": False,
            "ad_top": 1.0,
            "ad_bottom": 0.8,
            "play_usd": 100,
            "sell_layers": [],
        }
    )
    eng.hang_play(
        {
            "id": "SYNUSDT_4h",
            "name": "SYNUSDT",
            "chosen_tf": "4h",
            "faster_tfs": ["1h"],
            "habit_ready": False,
            "ad_top": 1.0,
            "ad_bottom": 0.8,
            "play_usd": 100,
            "sell_layers": [],
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    feed = MexcLiveFeed(names=["BPUSDT", "SYNUSDT"], client=client)
    loop = DecisionLoop(engine=eng, feed=feed, interval_sec=99)
    sync_feed_names(loop, eng)
    prints = feed.poll_once()
    by_name = {p.name: p for p in prints}
    assert set(by_name) == {"BPUSDT", "SYNUSDT"}
    # Intervals requested per play — not one global 4h/1h for both
    assert ("BPUSDT", "1d") in seen
    assert ("BPUSDT", "4h") in seen
    assert ("SYNUSDT", "4h") in seen
    assert ("SYNUSDT", "1h") in seen
    assert ("BPUSDT", "1h") not in seen
    assert ("SYNUSDT", "1d") not in seen
    # 1d has 3 reds; if the bug still used global 4h, BP would be 1
    assert by_name["BPUSDT"].chosen_tf_reds == 3
    assert by_name["BPUSDT"].faster_tf_reds == {"4h": 1}
    # 4h plan unchanged
    assert by_name["SYNUSDT"].chosen_tf_reds == 1
    assert by_name["SYNUSDT"].faster_tf_reds == {"1h": 2}
    client.close()


def test_load_plays_dir_hangs_syn_agi_us_not_only_examples():
    eng = Engine()
    plans = eng.load_plays_dir(ROOT / "data" / "plays")
    ids = {p.id for p in plans}
    assert "SYNUSDT_4h" in ids
    assert "AGIUSDT_4h" in ids
    assert "USUSDT_4h" in ids
    # examples/ is a subdir — not loaded by *.json glob on plays/
    assert not any(p.id.startswith("demo_") for p in plans)


def test_money_sample_m2_closed_has_sells_and_stats():
    from scripts import money_sample_m2_closed as ms

    # Allow importing scripts/
    payload = ms.run(ROOT / "data" / "plays" / "SYNUSDT_4h.json")
    assert payload["live_orders_allowed"] is False
    sells = [t for t in payload["trades"] if t["side"] == "sell"]
    buys = [t for t in payload["trades"] if t["side"] == "buy"]
    assert buys, "expected buy fills from dump"
    assert sells, "expected sell fills from bounce"
    if payload["closes"]:
        assert payload["closes_exist"] is True
        assert payload["expectancy"] is not None
        assert "tail" in payload
    else:
        assert payload["closes_exist"] is False
        assert "still no closes" in payload["plain"]


def test_money_sample_file_writable(tmp_path, monkeypatch):
    from scripts import money_sample_m2_closed as ms

    out = tmp_path / "money_sample_m2_closed.json"
    monkeypatch.setattr(ms, "OUT", out)
    assert ms.main() == 0
    data = json.loads(out.read_text())
    assert "trades" in data
    assert data["live_orders_allowed"] is False
