"""Gateway routes for linking a vehicle to Bluetooth hardware.

Linking is open to any signed-in family member (the one in the car with the
paired phone does it); removing a link is for a parent or whoever made it.
The person is always the signed-in one, never a field the client sends.
"""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

ADDR = "00:1A:7D:DA:71:13"
VEHICLES = {"vehicles": [{"id": "van", "name": "Van", "bluetooth": [
    {"address": ADDR, "kind": "classic", "name": "Car Audio", "linked_by": "pat"}]}]}


def _patch_geo(monkeypatch, answers):
    """``answers`` maps (verb, path suffix) to (status, body); calls are recorded."""
    calls: list[dict] = []
    geo = str(gateway_main.GEO_SVC)

    class _Resp:
        def __init__(self, status, body):
            self.status = status
            self._body = body

        async def json(self):
            return self._body

        async def text(self):
            return str(self._body)

    class _Client:
        async def _go(self, verb, url, **kw):
            if not url.startswith(geo):
                return _Resp(200, {})
            path = url[len(geo):]
            calls.append({"verb": verb, "path": path, "json": kw.get("json")})
            status, body = answers.get((verb, path), (200, {"status": "ok"}))
            return _Resp(status, body)

        async def get(self, url, **kw):
            return await self._go("get", url, **kw)

        async def post(self, url, **kw):
            return await self._go("post", url, **kw)

        async def delete(self, url, **kw):
            return await self._go("delete", url, **kw)

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return calls


@pytest.fixture
def as_user(monkeypatch):
    def _make(user, is_admin=False):
        async def _resolve(api_key):
            return {"user": user, "user_id": 3, "is_admin": is_admin}

        monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _resolve)
        return TestClient(app, headers={"Authorization": "Bearer t"})

    return _make


def test_linking_sends_the_signed_in_person_not_a_claimed_one(monkeypatch, as_user):
    calls = _patch_geo(monkeypatch, {})
    r = as_user("alex").post("/api/geo/vehicles/van/bluetooth",
                             json={"address": ADDR, "kind": "classic", "name": "Car Audio", "user": "someone_else"})
    assert r.status_code == 200
    sent = calls[-1]
    assert sent["path"] == "/vehicles/van/bluetooth"
    assert sent["json"]["user"] == "alex"
    assert sent["json"]["address"] == ADDR


def test_linking_needs_a_sign_in(monkeypatch):
    _patch_geo(monkeypatch, {})
    assert TestClient(app).post("/api/geo/vehicles/van/bluetooth", json={"address": ADDR}).status_code == 401


def test_geo_refusals_reach_the_app_with_their_reason(monkeypatch, as_user):
    _patch_geo(monkeypatch, {("post", "/vehicles/van/bluetooth"): (409, {"detail": "That device is already linked to Truck"})})
    r = as_user("alex").post("/api/geo/vehicles/van/bluetooth", json={"address": ADDR})
    assert r.status_code == 409
    assert "Truck" in r.json()["detail"]


def test_only_a_parent_or_the_linker_can_unlink(monkeypatch, as_user):
    calls = _patch_geo(monkeypatch, {("get", "/vehicles"): (200, VEHICLES)})
    assert as_user("alex").delete(f"/api/geo/vehicles/van/bluetooth/{ADDR}").status_code == 403
    assert not [c for c in calls if c["verb"] == "delete"]

    assert as_user("pat").delete(f"/api/geo/vehicles/van/bluetooth/{ADDR}").status_code == 200
    assert as_user("parent", is_admin=True).delete(f"/api/geo/vehicles/van/bluetooth/{ADDR}").status_code == 200
    assert len([c for c in calls if c["verb"] == "delete"]) == 2
