"""POST /api/machine/kill — hang then kill → out/killed; ranked hide contract.

live_orders_allowed stays false. Does not invent prices.
"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("MACHINE_TOKEN", "dev-token")

from machine import api as api_mod
from machine.engine import Engine


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MACHINE_LOOP", "0")
    monkeypatch.setattr(api_mod, "LOOP_ENABLED", False)
    api_mod.engine = Engine()
    api_mod.decision_loop = None
    return TestClient(api_mod.app)


def auth():
    return {"Authorization": "Bearer dev-token"}


MINIMAL = {
    "id": "ANSEMUSDT_1h",
    "name": "ANSEMUSDT",
    "chosen_tf": "1h",
    "ad_top": 1.0,
    "ad_bottom": 0.8,
    "play_usd": 100,
    "sell_layers": [],
}


def test_kill_requires_bearer(client):
    r = client.post("/api/machine/kill", json={"id": "ANSEMUSDT_1h"})
    assert r.status_code == 401


def test_kill_missing_plan_404(client):
    r = client.post("/api/machine/kill", headers=auth(), json={"id": "NOPE"})
    assert r.status_code == 404


def test_hang_kill_out_killed_hide_contract(client):
    hung = client.post("/api/machine/hang", headers=auth(), json=MINIMAL)
    assert hung.status_code == 200
    assert hung.json()["live_orders_allowed"] is False
    listed = client.get("/api/machine/plans", headers=auth()).json()["plans"]
    assert any(p["id"] == "ANSEMUSDT_1h" for p in listed)

    killed = client.post(
        "/api/machine/kill",
        headers=auth(),
        json={"id": "ANSEMUSDT_1h", "why": "Kenneth marked done"},
    )
    assert killed.status_code == 200
    body = killed.json()
    assert body["state"] == "out"
    assert body["killed"] is True
    assert body["live_orders_allowed"] is False
    assert "dev-token" not in killed.text

    # GET one still returns hide-contract fields
    one = client.get("/api/machine/plans/ANSEMUSDT_1h", headers=auth()).json()
    assert one["state"] == "out"
    assert one["killed"] is True

    # Ranked / sheet paint contract: finished plays not listed
    after = client.get("/api/machine/plans", headers=auth()).json()["plans"]
    assert not any(p["id"] == "ANSEMUSDT_1h" for p in after)
    assert not any(p.get("state") == "out" or p.get("killed") for p in after)


def test_kill_accepts_plan_id_key(client):
    client.post("/api/machine/hang", headers=auth(), json=MINIMAL)
    r = client.post(
        "/api/machine/kill",
        headers=auth(),
        json={"plan_id": "ANSEMUSDT_1h", "why": "done"},
    )
    assert r.status_code == 200
    assert r.json()["killed"] is True
    assert r.json()["state"] == "out"


def test_engine_kill_ranked_skips(engine):
    plan = engine.hang_play(MINIMAL)
    assert plan.state == "watch"
    row = engine.plan_row(plan)
    assert row["killed"] is False
    engine.kill(plan.id, why="Kenneth marked done")
    assert plan.killed is True
    assert plan.state == "out"
    assert engine.plan_row(plan)["killed"] is True
    assert engine.live_orders_allowed is False
    ranked = engine.ranked()
    assert not any(r["id"] == plan.id for r in ranked)
