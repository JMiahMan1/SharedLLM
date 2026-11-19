"""Devices > Home setup: areas, floors, labels, zones and persons; admins only, only known fields forwarded."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import discovery_routes, family_safety
from services.gateway import main as gateway_main
from services.gateway.main import app

ADMIN = {"Authorization": "Bearer admin-key"}
KID = {"Authorization": "Bearer kid-key"}


@pytest.fixture
def hub(monkeypatch):
    seen = []
    answers = {}

    async def admin(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "parent"
        raise HTTPException(status_code=403 if auth else 401, detail="Admin only")

    async def call(method, path, *, params=None, json=None, timeout=None):
        seen.append((method, path, json))
        params_seen.append(params)
        return answers.get((method, path), (200, {"ok": True}))

    async def refreshed():
        answers["geo_refreshed"] = answers.get("geo_refreshed", 0) + 1

    params_seen: list = []
    answers["params"] = params_seen
    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(discovery_routes, "_hub", call)
    monkeypatch.setattr(family_safety, "places_changed", refreshed)
    return seen, answers


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


KINDS = ("areas", "floors", "labels", "zones", "persons")
ROUTES = ([("get", f"/api/homehub/{k}") for k in KINDS] + [("post", f"/api/homehub/{k}") for k in KINDS]
          + [("patch", f"/api/homehub/{k}/x1") for k in KINDS] + [("delete", f"/api/homehub/{k}/x1") for k in KINDS]
          + [("get", "/api/homehub/categories"), ("get", "/api/homehub/tags"),
             ("patch", "/api/homehub/zones/home"), ("delete", "/api/homehub/zones/home")])


@pytest.mark.parametrize("verb,path", ROUTES)
def test_only_admins_reach_the_registries(client, hub, verb, path):
    seen, _ = hub
    assert getattr(client, verb)(path, headers=KID).status_code == 403
    assert getattr(client, verb)(path).status_code == 401
    assert seen == []


@pytest.mark.parametrize("verb,path", ROUTES)
def test_each_route_reaches_its_hub_path(client, hub, verb, path):
    seen, _ = hub
    kwargs = {"json": {}} if verb in ("post", "patch") else {}
    assert getattr(client, verb)(path, headers=ADMIN, **kwargs).status_code == 200
    assert seen[-1][0] == verb.upper() and seen[-1][1] == path.replace("/api/homehub/", "/hub/")


def test_only_known_fields_are_forwarded(client, hub):
    seen, _ = hub
    client.post("/api/homehub/zones", headers=ADMIN, json={
        "name": "Field", "latitude": 1.5, "longitude": 2.5, "radius": 100, "passive": False, "owner": "x"})
    assert seen[-1][2] == {"name": "Field", "latitude": 1.5, "longitude": 2.5, "radius": 100, "passive": False}
    # The owner of a place is never the browser's to say.
    client.patch("/api/homehub/zones/z1", headers=ADMIN, json={"important": True, "notify": ["sam"], "pin": "park",
                                                               "owner": "x", "visibility": "private"})
    assert seen[-1][2] == {"important": True, "notify": ["sam"], "pin": "park"}
    client.patch("/api/homehub/persons/p1", headers=ADMIN, json={
        "name": "Sam", "jarvis_user": "sam", "device_trackers": ["device_tracker.pixel"], "user_id": "u"})
    assert seen[-1][2] == {"name": "Sam", "jarvis_user": "sam", "device_trackers": ["device_tracker.pixel"]}
    client.patch("/api/homehub/areas/den", headers=ADMIN, json={"floor_id": "up", "labels": ["kids"], "x": 1})
    assert seen[-1][2] == {"floor_id": "up", "labels": ["kids"]}
    client.patch("/api/homehub/devices/d1", headers=ADMIN, json={"labels": ["kids"]})
    assert seen[-1] == ("PATCH", "/hub/devices/d1", {"labels": ["kids"]})
    client.patch("/api/homehub/entities/light.lamp", headers=ADMIN, json={"labels": ["kids"]})
    assert seen[-1] == ("PATCH", "/hub/entities/light.lamp", {"labels": ["kids"]})


def test_hub_refusals_keep_their_status_and_words(client, hub):
    _, answers = hub
    answers[("POST", "/hub/floors")] = (409, {"status": "error", "message": "There is already a floor named 'Up'"})
    resp = client.post("/api/homehub/floors", headers=ADMIN, json={"name": "Up"})
    assert resp.status_code == 409 and resp.json()["detail"] == "There is already a floor named 'Up'"
    answers[("DELETE", "/hub/zones/home")] = (409, {"status": "error", "message": "Home cannot be removed"})
    resp = client.delete("/api/homehub/zones/home", headers=ADMIN)
    assert resp.status_code == 409 and "cannot be removed" in resp.json()["detail"]


def test_odd_ids_never_reach_the_hub(client, hub):
    seen, _ = hub
    assert client.patch("/api/homehub/zones/a.b", headers=ADMIN, json={}).status_code == 404
    assert client.delete("/api/homehub/persons/" + "x" * 65, headers=ADMIN).status_code == 404
    assert seen == []


def test_these_routes_do_not_shadow_the_other_hub_routes(client, hub):
    seen, _ = hub
    client.get("/api/homehub/entries", headers=ADMIN)
    client.get("/api/homehub/discovered", headers=ADMIN)
    assert [s[1] for s in seen] == ["/hub/entries", "/hub/discovered"]


def test_zones_are_asked_for_as_the_admin_so_nobody_elses_private_place_shows(client, hub):
    seen, answers = hub
    client.get("/api/homehub/zones", headers=ADMIN)
    client.patch("/api/homehub/zones/home", headers=ADMIN, json={"latitude": 1.0, "longitude": 2.0, "radius": 90})
    client.delete("/api/homehub/zones/z1", headers=ADMIN)
    assert answers["params"][-3:] == [{"viewer": "parent"}] * 3
    assert seen[-2] == ("PATCH", "/hub/zones/home", {"latitude": 1.0, "longitude": 2.0, "radius": 90})
    client.get("/api/homehub/floors", headers=ADMIN)
    assert answers["params"][-1] is None
    # A change to a place tells geo to read the places again.
    assert answers["geo_refreshed"] == 2


def test_the_saved_places_mirror_is_gone(client, hub):
    assert client.post("/api/homehub/zones/sync", headers=ADMIN).status_code in (404, 405)
