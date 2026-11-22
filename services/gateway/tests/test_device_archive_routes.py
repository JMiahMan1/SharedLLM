"""Archived devices through the gateway: the hub's (admins, internal secret) and identity's (the caller's own token).

Every key and id here is made up.
"""
import json
from contextlib import asynccontextmanager

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

    async def admin(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "parent"
        raise HTTPException(status_code=403 if auth else 401, detail="Admin only")

    async def call(method, path, *, params=None, json=None, timeout=None):
        seen.append((method, path, json))
        return 200, {"items": [], "settings": {}}

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(discovery_routes, "_hub", call)
    return seen


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("verb,path", [
    ("get", "/api/homehub/archive"), ("get", "/api/homehub/archive/preview"),
    ("put", "/api/homehub/archive/settings"), ("post", "/api/homehub/archive/restore"),
    ("post", "/api/homehub/archive/delete"),
])
def test_only_admins_reach_the_hub_archive(client, hub, verb, path):
    kw = {"json": {"kind": "entry", "id": "e1"}} if verb in ("put", "post") else {}
    assert getattr(client, verb)(path, headers=KID, **kw).status_code == 403
    assert hub == []


def test_hub_archive_calls_pass_only_their_fields(client, hub):
    assert client.get("/api/homehub/archive", headers=ADMIN).status_code == 200
    client.put("/api/homehub/archive/settings", headers=ADMIN,
               json={"archive_after_days": 120, "sneaky": 1})
    client.post("/api/homehub/archive/restore", headers=ADMIN, json={"kind": "entry", "id": "e1", "extra": 1})
    client.post("/api/homehub/archive/delete", headers=ADMIN, json={"kind": "device", "id": "d1"})
    assert hub[1:] == [("PUT", "/hub/archive/settings", {"archive_after_days": 120}),
                       ("POST", "/hub/archive/restore", {"kind": "entry", "id": "e1"}),
                       ("POST", "/hub/archive/delete", {"kind": "device", "id": "d1"})]
    assert client.post("/api/homehub/archive/delete", headers=ADMIN, json={"kind": "x", "id": "d1"}).status_code == 400
    assert client.post("/api/homehub/archive/delete", headers=ADMIN,
                       json={"kind": "entry", "id": "../x"}).status_code == 400


def _identity(monkeypatch, status=200, payload=None):
    calls = []
    identity_svc = str(gateway_main.IDENTITY_SVC)

    class Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload or {})

        async def json(self):
            return payload or {}

        async def read(self):
            return json.dumps(payload or {}).encode()

    class Client:
        def _record(self, verb, url, **kw):
            if url.startswith(identity_svc):
                calls.append((verb, url[len(identity_svc):], (kw.get("headers") or {}).get("Authorization")))
            return Resp()

        async def get(self, url, **kw):
            return self._record("get", url, **kw)

        async def put(self, url, **kw):
            return self._record("put", url, **kw)

        async def post(self, url, **kw):
            return self._record("post", url, **kw)

        async def delete(self, url, **kw):
            return self._record("delete", url, **kw)

    @asynccontextmanager
    async def fake():
        yield Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return calls


def test_identity_archive_routes_forward_the_callers_token(client, monkeypatch):
    calls = _identity(monkeypatch, payload={"items": []})
    tok = {"Authorization": "Bearer alex-token"}
    assert client.get("/api/user-panel/devices/archive", headers=tok).status_code == 200
    client.get("/api/user-panel/devices/archive/preview", headers=tok)
    client.put("/api/user-panel/devices/archive/settings", headers=tok, json={"enabled": False})
    client.post("/api/user-panel/devices/archive/phone-1/restore", headers=tok)
    client.delete("/api/user-panel/devices/archive/phone-1", headers=tok)
    assert calls == [
        ("get", "/api/user-panel/devices/archive", "Bearer alex-token"),
        ("get", "/api/user-panel/devices/archive/preview", "Bearer alex-token"),
        ("put", "/api/user-panel/devices/archive/settings", "Bearer alex-token"),
        ("post", "/api/user-panel/devices/archive/phone-1/restore", "Bearer alex-token"),
        ("delete", "/api/user-panel/devices/archive/phone-1", "Bearer alex-token"),
    ]


def test_identity_refusals_are_relayed(client, monkeypatch):
    _identity(monkeypatch, status=403, payload={"detail": "Only a device admin can see archived devices"})
    assert client.get("/api/user-panel/devices/archive").status_code == 403
