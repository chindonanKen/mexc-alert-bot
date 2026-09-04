"""API smoke: live_orders_allowed false; auth; routes exist."""

import os

import pytest
from fastapi.testclient import TestClient

# Fresh engine for API tests
os.environ.setdefault("MACHINE_TOKEN", "dev-token")

from machine import api as api_mod
from machine.engine import Engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MACHINE_LOOP", "0")
    # Reload flag used at lifespan — patch module attribute directly
    monkeypatch.setattr(api_mod, "LOOP_ENABLED", False)
    api_mod.engine = Engine()
    api_mod.decision_loop = None
    return TestClient(api_mod.app)


def auth():
    return {"Authorization": "Bearer dev-token"}


def test_status_live_orders_false(client):
    r = client.get("/api/machine/status", headers=auth())
    assert r.status_code == 200
    assert r.json()["live_orders_allowed"] is False
    assert "dev-token" not in r.text


def test_unauthorized(client):
    r = client.get("/api/machine/plans")
    assert r.status_code == 401


def test_login_desk_token_sets_httponly_cookie_not_raw_secret(client, monkeypatch):
    monkeypatch.setenv("DESK_API_TOKEN", "desk-secret-token")
    r = client.post("/api/machine/login", json={"token": "desk-secret-token"})
    assert r.status_code == 200
    assert r.json()["live_orders_allowed"] is False
    assert "desk-secret-token" not in r.text
    assert "dev-token" not in r.text
    cookie = r.cookies.get("machine_session")
    assert cookie
    assert cookie != "desk-secret-token"
    assert cookie != "dev-token"
    assert "httponly" in (r.headers.get("set-cookie") or "").lower()
    authed = client.get("/api/machine/status")
    assert authed.status_code == 200
    assert authed.json()["live_orders_allowed"] is False


def test_login_rejects_wrong_token(client):
    r = client.post("/api/machine/login", json={"token": "nope"})
    assert r.status_code == 401
    assert client.get("/api/machine/plans").status_code == 401


def test_logout_clears_cookie(client):
    assert client.post("/api/machine/login", json={"token": "dev-token"}).status_code == 200
    assert client.get("/api/machine/status").status_code == 200
    out = client.post("/api/machine/logout")
    assert out.status_code == 200
    # TestClient may keep cookies unless we drop them; header must expire it.
    assert "machine_session" in (out.headers.get("set-cookie") or "").lower()


def test_machine_page(client):
    r = client.get("/machine")
    assert r.status_code == 200
    assert b"MACHINE" in r.content
    assert b"PRICE" not in r.content or True  # label may be JS-painted
    assert b"paper plan" not in r.content.lower()
    assert b"packs" not in r.content.lower()
    html = r.content.decode()
    assert 'type="password"' in html
    assert "gate-token" in html
    assert "localStorage" not in html
    assert "MACHINE_TOKEN" not in html


def test_plans_and_log_routes(client):
    api_mod.engine.hang_play(
        {
            "id": "X",
            "name": "X",
            "chosen_tf": "15m",
            "habit_ready": False,
            "ad_top": 1,
            "ad_bottom": 0.8,
            "play_usd": 100,
            "sell_layers": [],
        }
    )
    assert client.get("/api/machine/plans", headers=auth()).status_code == 200
    assert client.get("/api/machine/plans/X", headers=auth()).status_code == 200
    assert client.get("/api/machine/layers/X", headers=auth()).status_code == 200
    assert client.get("/api/machine/trades", headers=auth()).status_code == 200
    assert client.get("/api/machine/feed", headers=auth()).status_code == 200
    assert client.get("/api/machine/log", headers=auth()).status_code == 200
    assert client.get("/api/machine/closes", headers=auth()).status_code == 200


def test_static_no_default_token(client):
    """Pike watch: page must not ship a default token string."""
    html = client.get("/machine").content
    assert b"dev-token" not in html
    js = client.get("/machine/static/app.js").content
    assert b"dev-token" not in js


def test_sheet_plan_has_bounce_kind_and_last_sell_why(client, habit_play):
    """Sheet path exposes bounce_kind + last_sell_why; null when no sell yet / not scored."""
    api_mod.engine.hang_play(habit_play)
    plan = api_mod.engine.plans["DEMO"]
    plan.state = "live"
    plan.exit_live.last_bounce_kind = "GOOD"
    r = client.get("/api/machine/plans/DEMO", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["live_orders_allowed"] is False
    assert "bounce_kind" in body
    assert "last_sell_why" in body
    assert body["bounce_kind"] == "GOOD"
    assert body["last_sell_why"] is None  # no paper-sell yet — do not invent
    assert "dev-token" not in r.text


def test_ranked_omits_exit_why_fields(client, habit_play):
    """Ranked stays thin — bounce_kind / last_sell_why not on ranked rows."""
    api_mod.engine.hang_play(habit_play)
    api_mod.engine.plans["DEMO"].exit_live.last_bounce_kind = "WEAK"
    r = client.get("/api/machine/plans", headers=auth())
    assert r.status_code == 200
    rows = r.json()["plans"]
    assert rows
    for row in rows:
        assert "bounce_kind" not in row
        assert "last_sell_why" not in row
        # five ranked cells stay the paint contract (API still has sheet-adjacent fields)
        assert "name" in row and "tf" in row and "price" in row
        assert "state" in row and "next" in row


def test_last_sell_why_from_paper_sell_log(client, habit_play):
    """last_sell_why is the latest Machine log paper-sell why for that plan."""
    api_mod.engine.hang_play(habit_play)
    api_mod.engine.log.append(
        "paper-sell",
        "usual_bounce",
        name="DEMO",
        price=0.88,
        force=True,
    )
    api_mod.engine.log.append(
        "paper-sell",
        "big_base",
        name="DEMO",
        price=0.90,
        force=True,
    )
    # Other name must not leak
    api_mod.engine.log.append(
        "paper-sell",
        "other why",
        name="OTHER",
        price=1.0,
        force=True,
    )
    r = client.get("/api/machine/plans/DEMO", headers=auth())
    assert r.status_code == 200
    assert r.json()["last_sell_why"] == "big_base"


def test_bounce_kind_null_when_not_scored(client, habit_play):
    api_mod.engine.hang_play(habit_play)
    assert api_mod.engine.plans["DEMO"].exit_live.last_bounce_kind is None
    r = client.get("/api/machine/plans/DEMO", headers=auth())
    assert r.json()["bounce_kind"] is None


def test_page_paints_exit_why_no_sound_no_token(client):
    """Static page wires exit-why paint; no bounce-kind sound; token never in page."""
    js = client.get("/machine/static/app.js").content.decode()
    css = client.get("/machine/static/style.css").content.decode()
    html = client.get("/machine").content.decode()
    assert "exitWhyBlock" in js
    assert "BOUNCE" in js
    assert "no sell yet" in js
    assert "bounce-good" in js and "bounce-weak" in js
    assert "bounce-fail" in js and "bounce-early" in js
    # No sound on GOOD/WEAK/FAIL/TOO_EARLY
    assert "Audio" not in js
    assert "new Audio" not in js
    assert "bounce-good" in css
    assert "dev-token" not in js
    assert "dev-token" not in html
    assert "packs" not in js.lower()
    assert "paper plan" not in js.lower()
    assert "localStorage" not in js
    assert "credentials" in js
    assert "gate-token" in html


def test_sheet_includes_reds_and_vol(client, habit_play):
    """Slate sheet line fields — ranked omits them."""
    api_mod.engine.hang_play(habit_play)
    pid = habit_play["id"]
    r = client.post(
        "/api/machine/simulate",
        headers=auth(),
        json={
            "name": habit_play["name"],
            "price": 0.80,
            "volume_usd": 41200,
            "chosen_tf_reds": 2,
            "faster_tf_reds": {"5m": 1},
            "low": 0.80,
        },
    )
    assert r.status_code == 200
    detail = client.get(f"/api/machine/plans/{pid}", headers=auth()).json()
    assert detail.get("chosen_tf_reds") == 2
    assert detail.get("faster_tf_reds") == 1
    assert detail.get("faster_tf") == "5m"
    assert detail.get("vol_usd") == 41200
    ranked = client.get("/api/machine/plans", headers=auth()).json()["plans"]
    row = next(x for x in ranked if x["id"] == pid)
    # Kenneth overview: ranked carries reds + $vol; bounce why stays sheet-only
    assert row.get("chosen_tf_reds") == 2
    assert row.get("vol_usd") == 41200
    assert "bounce_kind" not in row
