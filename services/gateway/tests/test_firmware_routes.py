"""Firmware page routes: admins only, the hub reached with the internal secret, its answers passed through."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import automation_routes
from services.gateway import main as gateway_main
from services.gateway.main import app

ADMIN = {"Authorization": "Bearer admin-key"}
KID = {"Authorization": "Bearer kid-key"}

REPORT = {
    "devices": [
        {
            "id": "update.office_node_firmware",
            "entity_id": "update.office_node_firmware",
            "device_id": "dev1",
            "device": "Office Node",
            "name": "Firmware",
            "integration": "esphome",
            "model": "ESP32",
            "installed_version": "2026.10.17",
            "latest_version": "2026.11.18",
            "available": True,
            "installable": True,
            "in_progress": False,
            "action": "install",
        }
    ],
    "installing": 0,
}


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
        return answers.get(path, (200, REPORT))

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(automation_routes, "_hub", call)
    return seen, answers


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "verb,path",
    [
        ("get", "/api/firmware"),
        ("post", "/api/firmware/install"),
        ("post", "/api/firmware/dev1/upgrade"),
    ],
)
def test_only_admins_reach_firmware(client, hub, verb, path):
    seen, _ = hub
    assert getattr(client, verb)(path, headers=KID).status_code == 403
    assert getattr(client, verb)(path).status_code == 401
    assert seen == []


def test_the_list_comes_from_the_hub(client, hub):
    seen, _ = hub
    body = client.get("/api/firmware", headers=ADMIN).json()
    assert body["devices"][0]["latest_version"] == "2026.11.18"
    assert seen == [("GET", "/hub/firmware", None, None)]


def test_install_passes_the_entity(client, hub):
    seen, _ = hub
    client.post("/api/firmware/install", headers=ADMIN, json={"entity_id": "update.office_node_firmware"})
    assert seen == [("POST", "/hub/firmware/install", None, {"entity_id": "update.office_node_firmware"})]


def test_tasmota_upgrade_names_the_device(client, hub):
    seen, _ = hub
    client.post("/api/firmware/dev1/upgrade", headers=ADMIN)
    assert seen == [("POST", "/hub/firmware/dev1/upgrade", None, None)]


def test_a_hub_error_is_reported_as_a_bad_gateway(client, hub):
    _seen, answers = hub
    answers["/hub/firmware"] = (500, {"detail": "boom"})
    assert client.get("/api/firmware", headers=ADMIN).status_code == 502
