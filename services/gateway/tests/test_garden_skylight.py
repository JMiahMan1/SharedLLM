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
    {"username": "pat", "display_name": "Pat", "nextcloud_user": None, "avatar": None, "family_role": "mother"},
    {"username": "sam", "display_name": "Sam", "nextcloud_user": None, "avatar": None, "family_role": "father"},
    {"username": "alex", "display_name": "Alex", "nextcloud_user": None, "avatar": None, "family_role": "adult_child"},
    {"username": "riley", "display_name": "Riley", "nextcloud_user": None, "avatar": None, "family_role": "teen"},
    {"username": "jamie", "display_name": "Jamie", "nextcloud_user": None, "avatar": None, "family_role": "child"},
    {"username": "default", "display_name": "Default", "nextcloud_user": None, "avatar": None, "family_role": "other"},
]
CHART = [
    {"id": "11", "label": "Pat", "color": None},
    {"id": "12", "label": "Sam", "color": None},
    {"id": "13", "label": "Alex", "color": None},
    {"id": "14", "label": "Riley", "color": None},
    {"id": "15", "label": "Jamie", "color": None},
]


@pytest.fixture()
def world(monkeypatch):
    """A plant assigned to Alex, with identity/execution/garden stubbed at the edges."""
    state = {"plant": None, "created": [], "patched": [], "patch_headers": [], "deleted": [], "delete_ok": True}

    async def acting_identity(request):
        token = request.headers.get("Authorization", "")
        if token == "Bearer mother-key":
            return {"user": "pat", "is_admin": True, "family_role": "mother"}
        if token == "Bearer adult-key":
            return {"user": "alex", "is_admin": False, "family_role": "adult_child"}
        if token == "Bearer child-key":
            return {"user": "jamie", "is_admin": False, "family_role": "child"}
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
            state["patch_headers"].append(dict(headers or {}))
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

    class _Refused(_Resp):
        async def json(self, content_type=None):
            return {"status": "FAILURE", "message": "Skylight not configured"}

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
            return _Resp() if state["delete_ok"] else _Refused()

    monkeypatch.setattr(gateway_main := __import__("services.gateway.main", fromlist=["main"]), "_acting_identity", acting_identity)
    monkeypatch.setattr(garden_routes, "_household", household)
    monkeypatch.setattr(garden_routes, "_skylight_chart", chart)
    monkeypatch.setattr(garden_routes, "_garden_json", garden_json)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    state["plant"] = {
        "id": "plant-monty", "assigned_to": "alex", "skylight_sync_enabled": False,
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
    assert [who["username"] for who in body["eligible_assignees"]] == ["alex", "riley", "jamie"]
    assert next(who for who in body["eligible_assignees"] if who["username"] == "alex")["is_self"] is True
    assert next(who for who in body["eligible_assignees"] if who["username"] == "riley")["skylight_name"] == "Riley"


def test_a_parent_may_reach_the_whole_household_but_not_system_accounts(world):
    client, _state = world

    body = client.get("/api/garden/plants/plant-monty/skylight-options", headers=MOTHER).json()

    usernames = [who["username"] for who in body["eligible_assignees"]]
    assert usernames == ["pat", "sam", "alex", "riley", "jamie"]
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
        json={"assignee_username": "jamie", "repeat": "weekly"},
    )

    assert resp.status_code == 200, resp.text
    assert state["created"][0]["assignees"] == ["Jamie"]
    assert state["created"][0]["emoji"] == "🪴"
    assert state["patched"][-1] == {
        "skylight_sync_enabled": True, "skylight_chore_id": "52409380", "skylight_assignee": "jamie",
    }


def test_the_adult_cannot_assign_a_parent(world):
    client, state = world

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore",
        headers=ADULT,
        json={"assignee_username": "pat"},
    )

    assert resp.status_code == 403
    assert state["created"] == []


def test_the_adult_may_assign_themselves(world):
    client, state = world

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore",
        headers=ADULT,
        json={"assignee_username": "alex"},
    )

    assert resp.status_code == 200, resp.text
    assert state["created"][-1]["assignees"] == ["Alex"]


def test_a_parent_may_assign_anyone(world):
    client, state = world

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore",
        headers=MOTHER,
        json={"assignee_username": "sam", "title": "Water Monty", "repeat": "FREQ=DAILY;INTERVAL=3"},
    )

    assert resp.status_code == 200, resp.text
    assert state["created"][-1]["assignees"] == ["Sam"]
    assert state["created"][-1]["repeat"] == "FREQ=DAILY;INTERVAL=3"


def test_unlinking_removes_the_chore_and_clears_the_plant(world):
    client, state = world
    state["plant"].update({"skylight_sync_enabled": True, "skylight_chore_id": "52409380", "skylight_assignee": "alex"})

    resp = client.delete("/api/garden/plants/plant-monty/skylight-chore", headers=ADULT)

    assert resp.status_code == 200, resp.text
    assert state["deleted"] and "52409380" in state["deleted"][0]
    assert state["patched"][-1] == {
        "skylight_sync_enabled": False, "skylight_chore_id": None, "skylight_assignee": None,
    }


def test_the_link_goes_through_the_chore_routes_rules(world):
    client, state = world

    client.post("/api/garden/plants/plant-monty/skylight-chore", headers=ADULT, json={"assignee_username": "alex"})

    assert state["patch_headers"][-1].get("X-Garden-Skylight-Link") == "chore-link"


def test_relinking_removes_the_old_chore_from_the_chart(world):
    client, state = world
    state["plant"].update({"skylight_sync_enabled": True, "skylight_chore_id": "111", "skylight_assignee": "alex"})

    resp = client.post(
        "/api/garden/plants/plant-monty/skylight-chore", headers=ADULT, json={"assignee_username": "jamie"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["replaced_chore_id"], body["replaced_chore_removed"]) == ("111", True)
    assert "warning" not in body
    assert len(state["deleted"]) == 1 and "/chores/111" in state["deleted"][0]


def test_a_relink_whose_old_chore_stays_says_so(world):
    client, state = world
    state["plant"].update({"skylight_sync_enabled": True, "skylight_chore_id": "111", "skylight_assignee": "alex"})
    state["delete_ok"] = False

    body = client.post(
        "/api/garden/plants/plant-monty/skylight-chore", headers=ADULT, json={"assignee_username": "jamie"}
    ).json()

    assert body["replaced_chore_removed"] is False
    assert "still on the Skylight chart" in body["warning"]


def test_unlinking_never_claims_a_removal_skylight_refused(world):
    client, state = world
    state["plant"].update({"skylight_sync_enabled": True, "skylight_chore_id": "52409380", "skylight_assignee": "alex"})
    state["delete_ok"] = False

    resp = client.delete("/api/garden/plants/plant-monty/skylight-chore", headers=ADULT)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["removed_chore_id"] is None
    assert body["chore_left_on_chart"] == "52409380"
    assert "Skylight not configured" in body["warning"]
    # The plant is unlinked either way, so the garden stops following that chore.
    assert state["patched"][-1]["skylight_chore_id"] is None


@pytest.mark.parametrize(
    ("a", "b", "same"),
    (("Alex", "alex", True), ("Alex ", " ALEX", True), ("Ann", "Anna", False), ("Al", "Alex", False)),
)
def test_chart_names_match_exactly(a, b, same):
    assert garden_routes._same_person(a, b) is same


# --------------------------------------------------------------------------- ticking a chore off

def _completion_world(monkeypatch, garden_raises: bool = False):
    import services.gateway.main as gateway_main

    seen: list[str] = []

    async def member_or_device(_request):
        return {"user": "alex", "skylight_enabled": True}

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


# --------------------------------------------------------------------------- the real route stack

class _ChoreResp:
    def __init__(self, body, status: int = 200):
        self._body, self.status = body, status

    async def json(self, content_type=None):
        return self._body


def _route_world(monkeypatch, *, plant_status: int = 200):
    """The Skylight routes with only the HTTP seam faked -- never `_garden_json`.

    These two routes broke with a ``ValueError`` (unpacking a dict), because a
    second ``_garden_json`` definition shadowed the first. Patching ``_call`` and
    not the helper is what proves the route itself is sound.
    """
    import json as _json

    from fastapi.responses import Response

    import services.gateway.main as gateway_main

    captured: list[tuple[str, str, dict | None]] = []

    async def acting(_request):
        return {"user": "alex", "is_admin": False, "family_role": "adult_child"}

    async def household(_request):
        return HOUSEHOLD

    async def chart():
        return CHART

    async def call(method, path, *, params=None, json=None, headers=None):
        captured.append((method, path, json))
        if method == "GET" and path.startswith("/plants/"):
            if plant_status != 200:
                return Response(
                    content='{"detail": "Plant not found"}', status_code=plant_status,
                    media_type="application/json",
                )
            body = {
                "id": "p-1", "nickname": "Monty", "assigned_to": "alex",
                "skylight_chore_id": None, "skylight_assignee": None,
                "skylight_sync_enabled": False,
                "species": {
                    "common_name": "Monstera", "watering_interval_days": 7,
                    "dryness_cue": "top_2_inches_dry",
                },
            }
            return Response(content=_json.dumps(body), media_type="application/json")
        if method == "PATCH" and path.endswith("/assign"):
            body = {
                "id": "p-1", "nickname": "Monty",
                **(json or {}),
            }
            body["skylight_chore_id"] = str(body.get("skylight_chore_id") or "")
            return Response(content=_json.dumps(body), media_type="application/json")
        if method == "POST" and path.endswith("/skylight-chore"):
            body = {
                "skylight_chore_id": "99", "skylight_assignee": "alex",
                "skylight_sync_enabled": True,
            }
            return Response(content=_json.dumps(body), status_code=201, media_type="application/json")
        return Response(content="{}", media_type="application/json")

    class _ChoreClient:
        """Execution's chore endpoint: the one HTTP call the create route makes."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            return _ChoreResp({"status": "SUCCESS", "chore_ids": ["chore-99"]})

    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _ChoreClient())
    monkeypatch.setattr(garden_routes, "_household", household)
    monkeypatch.setattr(garden_routes, "_skylight_chart", chart)
    monkeypatch.setattr(garden_routes, "_call", call)
    return captured


def test_skylight_options_answer_through_the_real_route(monkeypatch):
    calls = _route_world(monkeypatch)
    client = TestClient(app, headers={"Authorization": "Bearer alex-key"})

    resp = client.get("/api/garden/plants/p-1/skylight-options")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_assign_skylight"] is True
    # An adult child may reach themselves, a teen and a child -- never a parent.
    assert [row["username"] for row in body["eligible_assignees"]] == ["alex", "riley", "jamie"]
    assert ("GET", "/plants/p-1") in [(method, path) for method, path, _ in calls]


def test_creating_a_chore_answers_through_the_real_route(monkeypatch):
    calls = _route_world(monkeypatch)
    client = TestClient(app, headers={"Authorization": "Bearer alex-key"})

    resp = client.post("/api/garden/plants/p-1/skylight-chore", json={"assignee_username": "alex"})

    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["chore"]["id"] == "chore-99"
    assert body["chore"]["assignee"] == "alex"
    assert body["chore"]["skylight_name"] == "Alex"
    assert body["plant"]["skylight_chore_id"] == "chore-99"
    posted = [body for method, path, body in calls if path == "/plants/p-1/assign"]
    assert posted and posted[0]["skylight_chore_id"] == "chore-99"


def test_a_missing_plant_is_a_404_not_a_crash(monkeypatch):
    _route_world(monkeypatch, plant_status=404)
    client = TestClient(app, headers={"Authorization": "Bearer alex-key"})

    options = client.get("/api/garden/plants/p-1/skylight-options")
    created = client.post("/api/garden/plants/p-1/skylight-chore", json={"assignee_username": "alex"})

    assert options.status_code == 404
    assert created.status_code == 404
