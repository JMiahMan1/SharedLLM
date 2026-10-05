"""BUG-29: /execute/groups/* accept real Pydantic body models, resolve their
imports under uvicorn's /app PYTHONPATH, and keep the caller's user_context
(gateway-injected) instead of silently dropping it."""
import os

from fastapi.testclient import TestClient

import services.execution.handlers.groups as groups_module
from services.config import INTERNAL_SECRET
from services.execution.main import app

client = TestClient(app)
HEADERS = {"X-Internal-Secret": os.getenv("INTERNAL_SECRET", INTERNAL_SECRET)}


def _fake_identity(calls):
    async def fake(method, path, json_data=None):
        calls.append((method, path, json_data))
        return {"groups": [], "clusters": [], "patterns": []}
    return fake


def test_media_group_list_uses_body_model(monkeypatch):
    calls = []
    monkeypatch.setattr(groups_module, "_call_identity", _fake_identity(calls))
    resp = client.post("/execute/groups/media", headers=HEADERS, json={
        "action": "list",
        "group_id": "media_all",
        "user_context": {"user": "jeremiah"},
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"
    assert calls == [("GET", "/api/groups/media", None)]


def test_media_group_create_records_acting_user(monkeypatch):
    calls = []
    monkeypatch.setattr(groups_module, "_call_identity", _fake_identity(calls))
    resp = client.post("/execute/groups/media", headers=HEADERS, json={
        "action": "create",
        "group_id": "living_room",
        "member_entity_ids": ["media_player.living_room_tv"],
        "user_context": {"user": "michele"},
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/api/groups/media")
    assert payload["owner_user_id"] == "michele"


def test_media_group_create_without_user_context_has_empty_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(groups_module, "_call_identity", _fake_identity(calls))
    resp = client.post("/execute/groups/media", headers=HEADERS, json={
        "action": "create",
        "group_id": "kitchen",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"
    assert calls[0][2]["owner_user_id"] == ""


def test_media_group_bad_action_is_422():
    resp = client.post("/execute/groups/media", headers=HEADERS, json={
        "action": "explode",
        "group_id": "x",
    })
    assert resp.status_code == 422


def test_light_cluster_create_records_acting_user(monkeypatch):
    calls = []
    monkeypatch.setattr(groups_module, "_call_identity", _fake_identity(calls))
    resp = client.post("/execute/groups/lights", headers=HEADERS, json={
        "action": "create",
        "cluster_id": "porch",
        "user_context": {"user": "jeremiah"},
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/api/groups/lights")
    assert payload["owner_user_id"] == "jeremiah"


def test_light_pattern_list_uses_body_model(monkeypatch):
    calls = []
    monkeypatch.setattr(groups_module, "_call_identity", _fake_identity(calls))
    resp = client.post("/execute/groups/patterns", headers=HEADERS, json={
        "action": "list",
        "pattern_id": "ocean",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"
    assert calls == [("GET", "/api/groups/patterns", None)]
