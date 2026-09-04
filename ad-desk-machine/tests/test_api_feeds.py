from __future__ import annotations

import os

from fastapi.testclient import TestClient

from machine.api import app
from machine.feeds import consecutive_reds, dollar_volume, is_red_bar, parse_mexc_klines
from machine.settings import live_orders_allowed

TOKEN = os.environ.get("MACHINE_TOKEN", "test-machine-token")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_health_open_and_live_off():
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["live_orders_allowed"] is False
    assert body["live_orders_sent"] is False
    assert "SYNUSDT_4h" in body["hung"]


def test_plays_require_bearer():
    c = TestClient(app)
    assert c.get("/plays").status_code in (401, 503)
    r = c.get("/plays", headers=AUTH)
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()["hung_plans"]}
    assert ids == {"SYNUSDT_4h", "AGIUSDT_4h", "USUSDT_4h"}
    assert r.json()["live_orders_allowed"] is False


def test_evaluate_endpoint_simulated():
    c = TestClient(app)
    r = c.post(
        "/plays/AGIUSDT_4h/evaluate",
        headers=AUTH,
        json={"snapshot": {"current_price": 0.00420, "chosen_tf_reds": 1, "vol_spike": True}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["live_orders_allowed"] is False
    assert body["simulated"] is True
    assert body["live_orders_sent"] is False


def test_log_and_needs_auth():
    c = TestClient(app)
    assert c.get("/log").status_code in (401, 503)
    assert c.get("/log", headers=AUTH).status_code == 200
    assert c.get("/needs-you", headers=AUTH).status_code == 200


def test_bad_token_rejected():
    c = TestClient(app)
    r = c.get("/plays", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_missing_play_404():
    c = TestClient(app)
    r = c.get("/plays/NOPE_4h", headers=AUTH)
    assert r.status_code == 404


def test_red_streak_closed_only():
    bars = [
        {"o": 2, "c": 1},  # red
        {"o": 2, "c": 1},  # red
        {"o": 1, "c": 1.1},  # forming green — dropped
    ]
    assert consecutive_reds(bars) == 2
    assert consecutive_reds(bars, include_forming=True) == 0
    assert is_red_bar({"o": 2, "c": 1})
    assert not is_red_bar({"o": 1, "c": 1})


def test_parse_mexc_klines_and_quote_volume():
    raw = [
        [1_700_000_000_000, "1", "2", "0.5", "0.9", "10", 0, "100"],
        [1_700_000_400_000, "0.9", "1", "0.8", "0.85", "12", 0, "200"],
    ]
    bars = parse_mexc_klines(raw)
    assert len(bars) == 2
    assert bars[0]["o"] == 1.0
    assert dollar_volume(bars) == 200.0


def test_doji_breaks_red_streak():
    bars = [
        {"o": 2, "c": 1},
        {"o": 1, "c": 1},
        {"o": 1, "c": 0.9},
        {"o": 1, "c": 1.1},
    ]
    assert consecutive_reds(bars) == 1


def test_live_orders_still_off_in_api_module():
    assert live_orders_allowed() is False
