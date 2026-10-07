"""The gateway must proxy every /api/user-panel route.

The phones only ever talk to the gateway. Identity serving these routes is
therefore not enough: without a gateway route the register call 404s, the
``Device`` table stays empty, and the server cannot answer "which build is this
device on?".

That is not hypothetical. This exact gap shipped once -- identity had all six
routes, the gateway had none, and 33 identity-side tests all passed while
self-registration was dead in production. It is the same mistake as the missing
``/api/users/sharing-recipients`` route, which also only surfaced under a live
non-admin probe. Hence the emphasis here: the tests assert the *gateway* has
each route, not that identity has one.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _patch_identity(monkeypatch, status=200, payload=None):
    """Capture what the gateway forwards to identity.

    Only identity-bound calls are recorded: ``shared_http_client`` is also used
    by the request-logging POST, which shares the client and would otherwise
    overwrite the capture.
    """
    captured = {"calls": []}
    identity_svc = str(gateway_main.IDENTITY_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload if payload is not None else {})

        async def json(self):
            return payload if payload is not None else {}

        async def read(self):
            return json.dumps(payload if payload is not None else {}).encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(identity_svc):
                captured["calls"].append(
                    {
                        "verb": verb,
                        "url": url,
                        "headers": kwargs.get("headers") or {},
                        "json": kwargs.get("json"),
                    }
                )
            return _Resp()

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def patch(self, url, **kw):
            return await self._record("patch", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


@pytest.fixture
def client():
    return TestClient(app)


DEVICE = {
    "device_key": "abc12345-1234-5678-9012-abcdefabcdef",
    "kind": "phone",
    "registered_by": "self",
    "owner_username": "jeremiah",
    "app_build": "25",
}


class TestRegisterRoute:
    def test_registering_a_phone_is_not_a_404(self, client, monkeypatch):
        """The regression: this answered 404 while identity served it fine."""
        _patch_identity(monkeypatch, payload=DEVICE)
        r = client.post(
            "/api/user-panel/devices/register",
            json={"device_key": DEVICE["device_key"], "app_build": "25"},
        )
        assert r.status_code == 200
        assert r.json() == DEVICE

    def test_forwards_the_body_to_identity(self, client, monkeypatch):
        cap = _patch_identity(monkeypatch, payload=DEVICE)
        client.post(
            "/api/user-panel/devices/register",
            json={"device_key": DEVICE["device_key"], "model": "Pixel 7", "os_version": "14"},
        )
        assert cap["calls"][0]["url"].endswith("/api/user-panel/devices/register")
        assert cap["calls"][0]["json"]["model"] == "Pixel 7"
        assert cap["calls"][0]["json"]["os_version"] == "14"

    def test_forwards_the_callers_own_token(self, client, monkeypatch):
        cap = _patch_identity(monkeypatch, payload=DEVICE)
        client.post(
            "/api/user-panel/devices/register",
            headers={"Authorization": "Bearer jeremiah-token"},
            json={"device_key": DEVICE["device_key"]},
        )
        assert cap["calls"][0]["headers"].get("Authorization") == "Bearer jeremiah-token"

    def test_never_invents_an_identity(self, client, monkeypatch):
        """Inventing one would register the device against the default admin."""
        cap = _patch_identity(monkeypatch, payload=DEVICE)
        client.post(
            "/api/user-panel/devices/register",
            json={"device_key": DEVICE["device_key"]},
        )
        assert "Authorization" not in cap["calls"][0]["headers"]


class TestListingAndAdminRoutes:
    def test_lists_devices(self, client, monkeypatch):
        _patch_identity(monkeypatch, payload=[DEVICE])
        r = client.get("/api/user-panel/devices")
        assert r.status_code == 200
        assert r.json() == [DEVICE]

    def test_creates_a_device(self, client, monkeypatch):
        cap = _patch_identity(monkeypatch, payload={**DEVICE, "kind": "assistant"})
        r = client.post("/api/user-panel/devices", json={"device_key": "k", "kind": "assistant"})
        assert r.status_code == 200
        assert cap["calls"][0]["verb"] == "post"

    def test_assigns_a_device(self, client, monkeypatch):
        cap = _patch_identity(monkeypatch, payload=DEVICE)
        r = client.patch("/api/user-panel/devices/abc", json={"owner_username": "michele"})
        assert r.status_code == 200
        assert cap["calls"][0]["verb"] == "patch"
        assert cap["calls"][0]["url"].endswith("/api/user-panel/devices/abc")

    def test_reports_capabilities(self, client, monkeypatch):
        cap = _patch_identity(monkeypatch, payload=DEVICE)
        r = client.post(
            "/api/user-panel/devices/abc/capabilities",
            json={"capabilities": {"climate": True}},
        )
        assert r.status_code == 200
        assert cap["calls"][0]["url"].endswith("/api/user-panel/devices/abc/capabilities")


class TestTelemetryRoute:
    def test_ingests_telemetry(self, client, monkeypatch):
        cap = _patch_identity(monkeypatch, payload={"written": 2, "rejected": []})
        r = client.post(
            "/api/user-panel/devices/telemetry",
            json={"device_key": "abc", "events": [{"event": "app_open"}]},
        )
        assert r.status_code == 200
        assert r.json()["written"] == 2
        assert cap["calls"][0]["url"].endswith("/api/user-panel/devices/telemetry")

    # The {device_key} PATCH would otherwise swallow this: a POST to
    # /devices/telemetry must not be read as a request about a device whose
    # key is literally "telemetry".
    def test_telemetry_is_not_captured_as_a_device_key(self, client, monkeypatch):
        cap = _patch_identity(monkeypatch, payload={"written": 0, "rejected": []})
        client.post(
            "/api/user-panel/devices/telemetry",
            json={"device_key": "abc", "events": []},
        )
        forwarded = cap["calls"]
        assert len(forwarded) == 1
        assert forwarded[0]["url"].endswith("/devices/telemetry")
        assert "/devices/telemetry/capabilities" not in forwarded[0]["url"]


class TestRefusalsAreRelayed:
    def test_relays_a_403_from_identity(self, client, monkeypatch):
        """An admin-only refusal must not become a 200, or the UI would think an
        unauthorised registration succeeded."""
        _patch_identity(monkeypatch, status=403, payload={"detail": "Admin only"})
        r = client.post("/api/user-panel/devices", json={"device_key": "k", "kind": "light"})
        assert r.status_code == 403

    def test_relays_a_409_from_identity(self, client, monkeypatch):
        _patch_identity(monkeypatch, status=409, payload={"detail": "already registered"})
        r = client.post(
            "/api/user-panel/devices/register", json={"device_key": DEVICE["device_key"]}
        )
        assert r.status_code == 409


def test_battery_history_is_proxied_with_the_callers_token(client, monkeypatch):
    cap = _patch_identity(monkeypatch, payload=[{"at": "2026-10-06T10:00:00", "pct": 80.0}])
    r = client.get("/api/user-panel/devices/esphome:744dbd2c9728/battery?hours=48",
                   headers={"Authorization": "Bearer jeremiah-token"})
    assert r.status_code == 200
    assert cap["calls"][0]["url"].endswith("/api/user-panel/devices/esphome:744dbd2c9728/battery")
    assert cap["calls"][0]["headers"].get("Authorization") == "Bearer jeremiah-token"
