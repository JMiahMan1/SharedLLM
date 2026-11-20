"""Garden's Skylight chore routes: who may link a plant's chore, and to whom.

An adult child looking after a plant may put its watering on the Skylight chore
chart -- for themselves or a child/teen, never for a parent or another adult.
Parents and admins may choose anyone in the household. The eligibility is
enforced here, not trusted from the browser.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.gateway import garden_routes
from services.gateway.main import app

HOUSEHOLD = [
    {"username": "michele", "display_name": "Michele", "nextcloud_user": None, "avatar": None, "family_role": "mother"},
    {"username": "jeremiah", "display_name": "Jeremiah", "nextcloud_user": None, "avatar": None, "family_role": "father"},
    {"username": "kaleb", "display_name": "Kaleb", "nextcloud_user": None, "avatar": None, "family_role": "adult_child"},
    {"username": "gracie", "display_name": "Gracie", "nextcloud_user": None, "avatar": None, "family_role": "teen"},
    {"username": "noah", "display_name": "Noah", "nextcloud_user": None, "avatar": None, "family_role": "child"},
    {"username": "default", "display_name": "Default", "nextcloud_user": None, "avatar": None, "family_role": "other"},
]
CHART = [
    {"id": "11", "label": "Michele", "color": None},
    {"id": "12", "label": "Jeremiah", "color": None},
    {"id": "13", "label": "Kaleb", "color": None},
    {"id": "14", "label": "Gracie", "color": None},
    {"id": "15", "label": "Noah", "color": None},
]


@pytest.fixture()
def world(monkeypatch):
    """A plant assigned to Kaleb, with identity/execution/garden stubbed at the edges."""
    state = {"plant": None, "created": [], "patched": [], "deleted": []}

    async def acting_identity(request):
        token = request.headers.get("Authorization", "")
        if token == "Bearer mother-key":
            return {"user": "michele", "is_admin": True, "family_role": "mother"}
        if token == "Bearer adult-key":
            return {"user": "kaleb", "is_admin": False, "family_role": "adult_child"}
        if token == "Bearer child-key":
            return {"user": "noah", "is_admin": False, "family_role": "child"}
        return None

    async def household(_request):
        return HOUSEHOLD

    async def chart():
        return CHART

    async def garden_json(method, path, *, headers=None, json=None):
        if method == "GET" and path.startswith("/plants/"):
            return 200, dict(state["plant"])
        if method == "PATCH" and path.endswith("/assign"):
            state["patched"].append(json)
            updated = {**state["plant"], **(json or {})}
            state["plant"] = updated
            return 200, updated
        return 404, None

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"status": "SUCCESS", "message": "Chore added", "created": 1, "chore_ids": ["52409380"]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            # Only the chore call is ours: the gateway also posts its own logs
            # through this same client.
            if "skylight/chores" in str(url):
                state["created"].append(json)
            return _Resp()

        async def delete(self, url, params=None, headers=None, timeout=None):
            state["deleted"].append(url)
            return _Resp()

    monkeypatch.setattr(gateway_main := __import__("services.gateway.main", fromlist=["main"]), "_acting_identity", acting_identity)
    monkeypatch.setattr(garden_routes, "_household", household)
    monkeypatch.setattr(garden_routes, "_skylight_chart", chart)
    monkeypatch.setattr(garden_routes, "_garden_json", garden_json)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    state["plant"] = {
        "id": "plant-monty", "assigned_to": "kaleb", "skylight_sync_enabled": False,
        "skylight_chore_id": None, "skylight_assignee": None,
    }
    return TestClient(app), state


MOTHER = {"Authorization": "Bearer mother-key"}
ADULT = {"Authorization": "Bearer adult-key"}
CHILD = {"Authorization": "Bearer child-key"}


# --------------------------------------------------------------------------- options

def test_an_assigned_adult_child_may_only_reach_themselves_and_children(world):
    client, _state = world

    body = client.get("/api/garden/plants/plant-monty/skylight-options", headers=ADULT).json()

    assert body["can_assign_skylight"] is True
    assert body["skylight_configured"] is True
    assert [who["username"] for who in body["eligible_assignees"]] == ["kaleb", "gracie", "noah"]
    assert next(who for who in body["eligible_assignees"] if who["username"] == "kaleb")["is_self"] is True
    assert next(who for who in body["eligible_assignees"] if who["username"] == "gracie")["skylight_name"] == "Gracie"


def test_a_parent_may_reach_the_whole_household_but_not_system_accounts(world):
    client, _state = world

    body = client.get("/api/garden/plants/plant-monty/skylight-options", headers=MOTHER).json()

    usernames = [who["username"] for who in body["eligible_assignees"]]
    assert usernames == ["michele", "jeremiah", "kaleb", "gracie", "noah"]
    assert "default" not in usernames


def test_a_child_is_not_allowed_at_all(world):
    client, _state = world
    assert client.get("/api/garden/plants/plant-monty/skylight-options", headers=CHILD).status_code == 403


# --------------------------------------------------------------------------- creating

def test_the_adult_creates_the_chore_for_a_child(world):
    client, state = world

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore",
        headers=ADULT,
        json={"assignee_username": "noah", "repeat": "weekly"},
    )

    assert resp.status_code == 200, resp.text
    assert state["created"][0]["assignees"] == ["Noah"]
    assert state["created"][0]["emoji"] == "🪴"
    assert state["patched"][-1] == {
        "skylight_sync_enabled": True, "skylight_chore_id": "52409380", "skylight_assignee": "noah",
    }


def test_the_adult_cannot_assign_a_parent(world):
    client, state = world

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore",
        headers=ADULT,
        json={"assignee_username": "michele"},
    )

    assert resp.status_code == 403
    assert state["created"] == []


def test_the_adult_may_assign_themselves(world):
    client, state = world

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore",
        headers=ADULT,
        json={"assignee_username": "kaleb"},
    )

    assert resp.status_code == 200, resp.text
    assert state["created"][-1]["assignees"] == ["Kaleb"]


def test_a_parent_may_assign_anyone(world):
    client, state = world

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore",
        headers=MOTHER,
        json={"assignee_username": "jeremiah", "title": "Water Monty", "repeat": "FREQ=DAILY;INTERVAL=3"},
    )

    assert resp.status_code == 200, resp.text
    assert state["created"][-1]["assignees"] == ["Jeremiah"]
    assert state["created"][-1]["repeat"] == "FREQ=DAILY;INTERVAL=3"


def test_unlinking_removes_the_chore_and_clears_the_plant(world):
    client, state = world
    state["plant"].update({"skylight_sync_enabled": True, "skylight_chore_id": "52409380", "skylight_assignee": "kaleb"})

    resp = client.delete("/api/garden/plants/plant-monty/skylight-chore", headers=ADULT)

    assert resp.status_code == 200, resp.text
    assert state["deleted"] and "52409380" in state["deleted"][0]
    assert state["patched"][-1] == {
        "skylight_sync_enabled": False, "skylight_chore_id": None, "skylight_assignee": None,
    }


# --------------------------------------------------------------------------- ticking a chore off

def _completion_world(monkeypatch, garden_raises: bool = False):
    import services.gateway.main as gateway_main

    seen: list[str] = []

    async def member_or_device(_request):
        return {"user": "kaleb", "skylight_enabled": True}

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"status": "SUCCESS", "message": "Chore completed"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None, params=None, timeout=None):
            seen.append(str(url))
            if "skylight-chore-completed" in str(url):
                if garden_raises:
                    raise RuntimeError("garden is down")
            return _Resp()

    monkeypatch.setattr(gateway_main, "_member_or_device", member_or_device)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    monkeypatch.setattr(gateway_main, "_forget_chores", lambda *_a, **_k: None)
    return TestClient(app), seen


def test_completing_a_chore_tells_garden(monkeypatch):
    client, seen = _completion_world(monkeypatch)

    resp = client.post("/api/integrations/skylight/chores/52409380-2026-11-20/complete", headers=ADULT)

    assert resp.status_code == 200, resp.text
    assert any("/api/integrations/skylight/chores/52409380-2026-11-20/complete" in url for url in seen)
    assert any(url.endswith("/hooks/skylight-chore-completed") for url in seen)


def test_a_garden_outage_never_fails_the_tick_off(monkeypatch):
    client, seen = _completion_world(monkeypatch, garden_raises=True)

    resp = client.post("/api/integrations/skylight/chores/52409380/complete", headers=ADULT)

    assert resp.status_code == 200, resp.text
    assert any(url.endswith("/hooks/skylight-chore-completed") for url in seen)
