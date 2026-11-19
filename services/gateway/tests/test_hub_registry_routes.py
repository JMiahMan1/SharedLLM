"""Devices > Home setup: areas, floors, labels, zones and persons; admins only, only known fields forwarded."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import discovery_routes
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
        return answers.get((method, path), (200, {"ok": True}))

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(discovery_routes, "_hub", call)
    return seen, answers


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


KINDS = ("areas", "floors", "labels", "zones", "persons")
ROUTES = ([("get", f"/api/homehub/{k}") for k in KINDS] + [("post", f"/api/homehub/{k}") for k in KINDS]
          + [("patch", f"/api/homehub/{k}/x1") for k in KINDS] + [("delete", f"/api/homehub/{k}/x1") for k in KINDS]
          + [("get", "/api/homehub/categories"), ("get", "/api/homehub/tags"), ("post", "/api/homehub/zones/sync")]
          + [("get", "/api/homehub/users"), ("post", "/api/homehub/users"), ("patch", "/api/homehub/users/u1"),
             ("get", "/api/homehub/auth/providers"), ("put", "/api/homehub/auth/providers"),
             ("post", "/api/homehub/auth/providers/jarvis_identity/sync")])


@pytest.mark.parametrize("verb,path", ROUTES)
def test_only_admins_reach_the_registries(client, hub, verb, path):
    seen, _ = hub
    assert getattr(client, verb)(path, headers=KID).status_code == 403
    assert getattr(client, verb)(path).status_code == 401
    assert seen == []


@pytest.mark.parametrize("verb,path", ROUTES)
def test_each_route_reaches_its_hub_path(client, hub, verb, path):
    seen, _ = hub
    kwargs = {"json": {}} if verb in ("post", "patch", "put") else {}
    assert getattr(client, verb)(path, headers=ADMIN, **kwargs).status_code == 200
    assert seen[-1][0] == verb.upper() and seen[-1][1] == path.replace("/api/homehub/", "/hub/")


def test_only_known_fields_are_forwarded(client, hub):
    seen, _ = hub
    client.post("/api/homehub/zones", headers=ADMIN, json={
        "name": "Field", "latitude": 1.5, "longitude": 2.5, "radius": 100, "passive": False, "owner": "x"})
    assert seen[-1][2] == {"name": "Field", "latitude": 1.5, "longitude": 2.5, "radius": 100, "passive": False}
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
    answers[("POST", "/hub/zones/sync")] = (503, {"status": "error", "message": "GEO_SVC_URL is not set"})
    assert client.post("/api/homehub/zones/sync", headers=ADMIN).status_code == 503


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


def test_users_and_providers_forward_only_their_fields(client, hub):
    seen, answers = hub
    client.post("/api/homehub/users", headers=ADMIN, json={"name": "Visitor", "username": "visitor",
                                                          "password": "made-up-pass", "is_admin": False, "id": "x"})
    assert seen[-1][2] == {"name": "Visitor", "username": "visitor", "password": "made-up-pass", "is_admin": False}
    client.patch("/api/homehub/users/u1", headers=ADMIN, json={"is_active": False, "provider": {"type": "x"}})
    assert seen[-1][2] == {"is_active": False}
    client.put("/api/homehub/auth/providers", headers=ADMIN, json={"local_logins": False, "x": 1})
    assert seen[-1] == ("PUT", "/hub/auth/providers", {"local_logins": False})
    answers[("PUT", "/hub/auth/providers")] = (409, {"message": "No admin could log in"})
    resp = client.put("/api/homehub/auth/providers", headers=ADMIN, json={"local_logins": False})
    assert resp.status_code == 409 and resp.json()["detail"] == "No admin could log in"
    answers[("POST", "/hub/auth/providers/jarvis_identity/sync")] = (502, {"ok": False, "error": "down"})
    assert client.post("/api/homehub/auth/providers/jarvis_identity/sync", headers=ADMIN).status_code == 502
    assert client.post("/api/homehub/auth/providers/Bad.Kind/sync", headers=ADMIN).status_code == 404
