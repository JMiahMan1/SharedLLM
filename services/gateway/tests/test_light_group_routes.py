"""Devices > Light groups: parents and admins only, the hub reached with the internal secret."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import discovery_routes
from services.gateway import main as gateway_main
from services.gateway.main import app

PEOPLE = {
    "Bearer admin-key": {"user": "alex", "is_admin": True, "family_role": None},
    "Bearer mom-key": {"user": "sam", "is_admin": False, "family_role": "mother"},
    "Bearer kid-key": {"user": "robin", "is_admin": False, "family_role": "child"},
}


@pytest.fixture
def hub(monkeypatch):
    seen = []

    async def acting(request):
        return PEOPLE.get(request.headers.get("Authorization") or "")

    async def call(method, path, *, params=None, json=None, timeout=None):
        seen.append((method, path, json))
        if method == "POST" and not (json or {}).get("entities"):
            return 400, {"status": "error", "message": "Choose at least two lights"}
        return 200, {"groups": [], "lights": [], "areas": []}

    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    monkeypatch.setattr(discovery_routes, "_hub", call)
    return seen


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("verb,path", [
    ("get", "/api/homehub/light_groups"), ("post", "/api/homehub/light_groups"),
    ("patch", "/api/homehub/light_groups/01ABC"), ("delete", "/api/homehub/light_groups/01ABC"),
])
def test_only_parents_and_admins_group_lights(client, hub, verb, path):
    assert getattr(client, verb)(path).status_code == 401
    assert getattr(client, verb)(path, headers={"Authorization": "Bearer kid-key"}).status_code == 403
    assert hub == []


def test_a_parent_lists_makes_changes_and_removes(client, hub):
    mom = {"Authorization": "Bearer mom-key"}
    assert client.get("/api/homehub/light_groups", headers=mom).status_code == 200
    made = client.post("/api/homehub/light_groups", headers=mom,
                       json={"name": "Pantry", "entities": ["light.a", "light.b"], "area_id": "pantry", "x": 1})
    assert made.status_code == 200
    assert hub[-1] == ("POST", "/hub/light_groups",
                       {"name": "Pantry", "entities": ["light.a", "light.b"], "area_id": "pantry"})
    client.patch("/api/homehub/light_groups/01ABC", headers=mom, json={"name": "Snacks"})
    assert hub[-1] == ("PATCH", "/hub/light_groups/01ABC", {"name": "Snacks"})
    client.delete("/api/homehub/light_groups/01ABC", headers={"Authorization": "Bearer admin-key"})
    assert hub[-1][:2] == ("DELETE", "/hub/light_groups/01ABC")


def test_the_hubs_reason_is_passed_on(client, hub):
    out = client.post("/api/homehub/light_groups", headers={"Authorization": "Bearer mom-key"}, json={"name": "x"})
    assert out.status_code == 400 and out.json()["detail"] == "Choose at least two lights"


def test_a_strange_group_id_never_reaches_the_hub(client, hub):
    assert client.delete("/api/homehub/light_groups/..%2Fentries", headers={"Authorization": "Bearer mom-key"}
                         ).status_code == 404
    assert hub == []
