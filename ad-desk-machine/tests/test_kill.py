"""Kill / pull: hang a play, POST kill, assert out + killed; sheet/ranked hide."""

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


def _minimal_play(plan_id: str = "ANSEMUSDT_1h", name: str = "ANSEMUSDT") -> dict:
    return {
        "id": plan_id,
        "name": name,
        "chosen_tf": "1h",
        "habit_ready": False,
        "ad_top": 1.0,
        "ad_bottom": 0.8,
        "play_usd": 100,
        "layers": [
            {"idx": 1, "price": 0.86, "usd": 5, "share_pct": 5, "role": "AD"},
        ],
        "sell_layers": [],
    }


def test_kill_sets_out_and_killed(client):
    play = _minimal_play()
    hang = client.post("/api/machine/hang", headers=auth(), json=play)
    assert hang.status_code == 200
    assert hang.json()["state"] == "watch"
    assert hang.json().get("killed") is False

    r = client.post(
        "/api/machine/kill",
        headers=auth(),
        json={"id": "ANSEMUSDT_1h", "why": "Kenneth marked done"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["live_orders_allowed"] is False
    assert body["id"] == "ANSEMUSDT_1h"
    assert body["state"] == "out"
    assert body["killed"] is True
    assert "dev-token" not in r.text

    plan = api_mod.engine.plans["ANSEMUSDT_1h"]
    assert plan.state == "out"
    assert plan.killed is True


def test_kill_accepts_plan_id_key(client):
    client.post("/api/machine/hang", headers=auth(), json=_minimal_play())
    r = client.post(
        "/api/machine/kill",
        headers=auth(),
        json={"plan_id": "ANSEMUSDT_1h"},
    )
    assert r.status_code == 200
    assert r.json()["killed"] is True
    assert r.json()["state"] == "out"


def test_kill_401_and_404(client):
    assert client.post("/api/machine/kill", json={"id": "X"}).status_code == 401
    r = client.post(
        "/api/machine/kill",
        headers=auth(),
        json={"id": "NO_SUCH_PLAN"},
    )
    assert r.status_code == 404


def test_kill_hides_from_sheet_and_ranked_paint_contract(client):
    """After kill, plan_row is out/killed; UI hide keys present for sheet/ranked."""
    client.post("/api/machine/hang", headers=auth(), json=_minimal_play())
    killed = client.post(
        "/api/machine/kill",
        headers=auth(),
        json={"id": "ANSEMUSDT_1h"},
    ).json()
    assert killed["state"] == "out" and killed["killed"] is True

    detail = client.get("/api/machine/plans/ANSEMUSDT_1h", headers=auth()).json()
    assert detail["state"] == "out"
    assert detail["killed"] is True

    ranked = client.get("/api/machine/plans", headers=auth()).json()["plans"]
    row = next(x for x in ranked if x["id"] == "ANSEMUSDT_1h")
    # Ranked still serializes the plan; UI skips paint when state out || killed
    assert row["state"] == "out"
    assert row["killed"] is True

    js = client.get("/machine/static/app.js").content.decode()
    assert "/api/machine/kill" in js
    assert "killPlan" in js
    assert 'p.state === "out" || p.killed' in js


def test_plan_row_includes_killed(engine, habit_play):
    plan = engine.hang_play(habit_play)
    row = engine.plan_row(plan)
    assert "killed" in row
    assert row["killed"] is False
    engine.kill(plan.id, why="pull")
    row2 = engine.plan_row(plan)
    assert row2["killed"] is True
    assert row2["state"] == "out"


def test_kill_persists_to_play_file(tmp_path, monkeypatch):
    """Kill must write killed_out onto the play file so reload cannot re-react."""
    from machine import engine as eng_mod
    from machine.feeds import Print
    from machine.loop import feed_names_from_engine

    monkeypatch.setattr(eng_mod, "PLAYS_DIR", tmp_path)
    play = _minimal_play()
    play_path = tmp_path / "ANSEMUSDT_1h.json"
    play_path.write_text(__import__("json").dumps(play, indent=2) + "\n")

    engine = eng_mod.Engine()
    plan = engine.load_play_file(play_path)
    assert plan.killed is False
    assert plan.state == "watch"

    engine.kill(plan.id, why="Kenneth marked done")
    disk = __import__("json").loads(play_path.read_text())
    assert disk["killed"] is True
    assert disk["state"] == "out"
    assert disk["status"] == "killed_out"
    assert disk["killed_why"] == "Kenneth marked done"
    assert "killed_at" in disk
    assert "PHT" in disk["killed_at"]
    # Size not invent-mutated
    assert disk["layers"][0]["price"] == 0.86

    # Reload: non-reacting
    engine2 = eng_mod.Engine()
    plan2 = engine2.load_play_file(play_path)
    assert plan2.killed is True
    assert plan2.state == "out"
    r = engine2.on_print(
        Print(name="ANSEMUSDT", price=0.85, volume_usd=1e6, chosen_tf_reds=3, faster_tf_reds={"15m": 5})
    )
    assert r["why"] == "plan out or killed"
    assert plan2.last_decision != "paper-buy"
    assert plan2.last_decision != "met"
    # Feed omit
    names = feed_names_from_engine(engine2)
    assert "ANSEMUSDT" not in names


def test_load_status_killed_out_without_killed_bool(tmp_path, monkeypatch):
    from machine import engine as eng_mod
    from machine.feeds import Print

    monkeypatch.setattr(eng_mod, "PLAYS_DIR", tmp_path)
    play = _minimal_play()
    play["status"] = "killed_out"
    play_path = tmp_path / "ANSEMUSDT_1h.json"
    play_path.write_text(__import__("json").dumps(play) + "\n")
    engine = eng_mod.Engine()
    plan = engine.load_play_file(play_path)
    assert plan.killed is True
    assert plan.state == "out"
    r = engine.on_print(
        Print(name="ANSEMUSDT", price=0.85, volume_usd=1e6, chosen_tf_reds=3, faster_tf_reds={})
    )
    assert "killed" in r["why"] or "out" in r["why"]


def test_feed_names_omits_killed_and_out():
    from machine.engine import Engine
    from machine.loop import feed_names_from_engine

    engine = Engine()
    live = _minimal_play("SYNUSDT_1h", "SYNUSDT")
    live["ad_top"] = 1.0
    live["ad_bottom"] = 0.8
    dead = _minimal_play("ANSEMUSDT_1h", "ANSEMUSDT")
    engine.hang_play(live)
    plan = engine.hang_play(dead)
    engine.kill(plan.id, why="done")
    names = feed_names_from_engine(engine)
    assert "SYNUSDT" in names
    assert "ANSEMUSDT" not in names
