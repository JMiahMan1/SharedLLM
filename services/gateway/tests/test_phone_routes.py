"""Devices > Phones: admins only, the hub reached with the internal secret, its answers passed through."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import phone_routes
from services.gateway.main import app

ADMIN = {"Authorization": "Bearer admin-key"}
KID = {"Authorization": "Bearer kid-key"}
PHONE = {"entry_id": "01ABCDEFGHJKMNPQRSTVWXYZ01", "name": "Robin's Phone", "kind": "companion", "sensors": []}


@pytest.fixture
def hub(monkeypatch):
    seen = []
    answers = {}

    async def admin(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "parent"
        raise HTTPException(status_code=403 if auth else 401, detail="Admin only")

    async def call(method, path, *, json=None):
        seen.append((method, path, json))
        return answers.get((method, path), (200, {"phones": [PHONE], "settings": {}}))

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(phone_routes, "_hub", call)
    return seen, answers


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("verb,path", [
    ("get", "/api/homehub/phones"), ("post", "/api/homehub/phones"),
    ("get", "/api/homehub/phones/settings"), ("put", "/api/homehub/phones/settings"),
    ("patch", f"/api/homehub/phones/{PHONE['entry_id']}"), ("delete", f"/api/homehub/phones/{PHONE['entry_id']}"),
])
def test_only_admins_reach_phones(client, hub, verb, path):
    seen, _ = hub
    assert client.request(verb.upper(), path, headers=KID).status_code == 403
    assert client.request(verb.upper(), path).status_code == 401
    assert seen == []


def test_listing_and_changes_reach_the_hub(client, hub):
    seen, answers = hub
    assert client.get("/api/homehub/phones", headers=ADMIN).json()["phones"][0]["name"] == "Robin's Phone"
    client.patch(f"/api/homehub/phones/{PHONE['entry_id']}", headers=ADMIN,
                 json={"jarvis_user": "robin", "jarvis_fallback": False, "webhook_id": "x"})
    assert seen[-1] == ("PATCH", f"/hub/mobile_app/phones/{PHONE['entry_id']}",
                        {"jarvis_user": "robin", "jarvis_fallback": False})
    client.put("/api/homehub/phones/settings", headers=ADMIN,
               json={"external_url": "https://hub.example.invalid", "secret": "no"})
    assert seen[-1] == ("PUT", "/hub/mobile_app/settings", {"external_url": "https://hub.example.invalid"})
    answers[("POST", "/hub/mobile_app/phones")] = (400, {"message": "robin already has a Jarvis phone"})
    r = client.post("/api/homehub/phones", headers=ADMIN, json={"jarvis_user": "robin"})
    assert r.status_code == 400 and r.json()["detail"] == "robin already has a Jarvis phone"
    assert client.post("/api/homehub/phones", headers=ADMIN, json={}).status_code == 400
    assert client.delete("/api/homehub/phones/..%2Fsettings", headers=ADMIN).status_code == 404


def test_the_hub_being_away_is_a_502(client, hub):
    _, answers = hub
    answers[("GET", "/hub/mobile_app/phones")] = (500, None)
    assert client.get("/api/homehub/phones", headers=ADMIN).status_code == 502
