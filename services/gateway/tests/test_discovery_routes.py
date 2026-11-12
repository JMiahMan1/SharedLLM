"""Devices > New devices: admins only, the hub reached with the internal secret, its answers passed through."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import discovery_routes
from services.gateway import main as gateway_main
from services.gateway.main import app

ADMIN = {"Authorization": "Bearer admin-key"}
KID = {"Authorization": "Bearer kid-key"}

FINDING = {"id": "esphome:aabbccddee01", "integration": "esphome", "label": "ESPHome", "name": "Porch Bulb",
           "host": "192.0.2.34", "new": True, "adoptable": True, "needs": [{"key": "noise_psk"}]}


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
        seen.append((method, path, params, json))
        return answers.get(path, (200, {"devices": [FINDING], "last_scan": {}}))

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(discovery_routes, "_hub", call)
    return seen, answers


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("verb,path", [
    ("get", "/api/homehub/discovered"), ("post", "/api/homehub/discovered/scan"),
    ("post", "/api/homehub/discovered/esphome:aabbccddee01/adopt"),
    ("post", "/api/homehub/discovered/esphome:aabbccddee01/ignore"),
    ("post", "/api/homehub/matter/commission"),
])
def test_only_admins_reach_discovery(client, hub, verb, path):
    seen, _ = hub
    assert getattr(client, verb)(path, headers=KID).status_code == 403
    assert getattr(client, verb)(path).status_code == 401
    assert seen == []


def test_listing_and_new_only(client, hub):
    seen, _ = hub
    assert client.get("/api/homehub/discovered", headers=ADMIN).json()["devices"][0]["id"] == FINDING["id"]
    client.get("/api/homehub/discovered?new=true", headers=ADMIN)
    assert seen[0][2] is None and seen[1][2] == {"new": "1"}


def test_adopt_passes_input_and_the_needs_input_answer(client, hub):
    seen, answers = hub
    answers["/hub/discovered/esphome:aabbccddee01/adopt"] = (
        200, {"status": "needs_input", "message": "enter its key", "needs": [{"key": "noise_psk"}]})
    body = {"name": "Porch", "input": {"noise_psk": "made-up"}}
    resp = client.post("/api/homehub/discovered/esphome:aabbccddee01/adopt", headers=ADMIN, json=body)
    assert resp.status_code == 200 and resp.json()["status"] == "needs_input"
    assert seen[-1] == ("POST", "/hub/discovered/esphome:aabbccddee01/adopt", None, body)


def test_hub_refusals_keep_their_status_and_words(client, hub):
    _, answers = hub
    answers["/hub/discovered/samsungtv:x1/adopt"] = (501, {"status": "unsupported",
                                                           "message": "Needs accepting on the TV"})
    resp = client.post("/api/homehub/discovered/samsungtv:x1/adopt", headers=ADMIN, json={})
    assert resp.status_code == 501 and resp.json()["detail"] == "Needs accepting on the TV"
    answers["/hub/discovered/scan"] = (500, None)
    assert client.post("/api/homehub/discovered/scan", headers=ADMIN).status_code == 502


def test_ignore_and_commission(client, hub):
    seen, _ = hub
    client.post("/api/homehub/discovered/esphome:aabbccddee01/ignore", headers=ADMIN, json={"ignored": False})
    assert seen[-1][3] == {"ignored": False}
    assert client.post("/api/homehub/matter/commission", headers=ADMIN, json={"code": " "}).status_code == 400
    client.post("/api/homehub/matter/commission", headers=ADMIN, json={"code": "34970112332", "name": "Lamp"})
    assert seen[-1] == ("POST", "/hub/matter/commission", None, {"code": "34970112332", "name": "Lamp"})


def test_odd_ids_never_reach_the_hub(client, hub):
    seen, _ = hub
    assert client.post("/api/homehub/discovered/BAD%20ID/adopt", headers=ADMIN, json={}).status_code == 404
    assert seen == []


async def test_the_hub_is_called_with_the_internal_secret(monkeypatch):
    sent = {}

    class Resp:
        status = 200

        async def json(self, content_type=None):
            return {"devices": []}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Client:
        def request(self, method, url, **kw):
            sent.update(method=method, url=url, **kw)
            return Resp()

    class Shared:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: Shared())
    monkeypatch.setattr(discovery_routes, "INTERNAL_SECRET", "made-up-secret")
    status, body = await discovery_routes._hub("GET", "/hub/discovered")
    assert status == 200 and body == {"devices": []}
    assert sent["url"] == f"{discovery_routes.HOMEHUB_URL}/hub/discovered"
    assert sent["headers"] == {"Authorization": "Bearer made-up-secret"}


@pytest.mark.parametrize("verb,path", [
    ("get", "/api/homehub/entries"), ("patch", "/api/homehub/entries/e1"), ("delete", "/api/homehub/entries/e1"),
    ("patch", "/api/homehub/devices/d1"), ("delete", "/api/homehub/devices/d1"), ("get", "/api/homehub/flows"),
    ("post", "/api/homehub/flows/samsungtv"), ("get", "/api/homehub/esphome-builder"),
])
def test_only_admins_manage_integrations(client, hub, verb, path):
    seen, _ = hub
    assert getattr(client, verb)(path, headers=KID).status_code == 403
    assert seen == []


def test_integration_management_is_passed_through(client, hub):
    seen, answers = hub
    answers["/hub/entries/e1"] = (502, {"status": "error", "message": "Could not unpair it"})
    resp = client.delete("/api/homehub/entries/e1", headers=ADMIN)
    assert resp.status_code == 502 and resp.json()["detail"] == "Could not unpair it"
    answers["/hub/entries/e1"] = (200, {"status": "removed"})
    client.delete("/api/homehub/entries/e1?force=true", headers=ADMIN)
    assert seen[-1] == ("DELETE", "/hub/entries/e1", {"force": "1"}, None)
    client.patch("/api/homehub/devices/d1", headers=ADMIN, json={"name_by_user": "Den TV", "area_id": "den",
                                                                  "evil": 1})
    assert seen[-1] == ("PATCH", "/hub/devices/d1", None, {"name_by_user": "Den TV", "area_id": "den"})
    client.patch("/api/homehub/entries/e1", headers=ADMIN, json={"data": {"host": "192.0.2.5"}})
    assert seen[-1][3] == {"data": {"host": "192.0.2.5"}}
    assert client.delete("/api/homehub/devices/bad%20id", headers=ADMIN).status_code == 404


def test_pairing_sessions_pass_through(client, hub):
    seen, answers = hub
    answers["/hub/flows/samsungtv"] = (200, {"status": "pending", "session": "s1", "message": "Accept on the TV",
                                             "step": "accept_on_tv", "poll_ms": 2000})
    resp = client.post("/api/homehub/flows/samsungtv", headers=ADMIN,
                       json={"host": "192.0.2.70", "input": {"session": "s1"}})
    assert resp.json()["status"] == "pending"
    assert seen[-1] == ("POST", "/hub/flows/samsungtv", None, {"host": "192.0.2.70", "input": {"session": "s1"}})
    answers["/hub/flows/samsungtv"] = (410, {"status": "error", "message": "That pairing has expired"})
    assert client.post("/api/homehub/flows/samsungtv", headers=ADMIN, json={}).status_code == 410
    assert client.post("/api/homehub/flows/Bad-Domain", headers=ADMIN, json={}).status_code == 404


def test_the_builder_link_never_carries_its_login(client, hub):
    _, answers = hub
    answers["/hub/esphome-builder"] = (200, {"configured": True, "url": "http://192.0.2.5:6052/", "state": "loaded",
                                             "password": "leak"})
    body = client.get("/api/homehub/esphome-builder", headers=ADMIN).json()
    assert body == {"configured": True, "url": "http://192.0.2.5:6052/", "state": "loaded"}
