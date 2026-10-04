"""Gateway contract for the chores proxy: whose chores a caller may see.

The Skylight login is one shared household credential, so it cannot decide
scope. This pins the decision the gateway does make instead, and that a
refusal never quietly turns into somebody else's chores.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _patch_execution(monkeypatch, payload=None, captured=None):
    """Record what the gateway forwards to the execution service."""
    captured = captured if captured is not None else {}
    captured["calls"] = []
    execution_svc = str(gateway_main.EXECUTION_SVC)

    class _Resp:
        status = 200

        def __init__(self):
            self.text = json.dumps(payload if payload is not None else {"status": "SUCCESS", "chores": []})

        async def json(self):
            return payload if payload is not None else {"status": "SUCCESS", "chores": []}

        async def read(self):
            return self.text.encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(execution_svc):
                captured["calls"].append({"verb": verb, "url": url, "params": kwargs.get("params")})
            return _Resp()

        async def request(self, method, url, **kw):
            return await self._record(method.lower(), url, **kw)

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

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
def resolver(monkeypatch):
    """Install a stand-in for the gateway's identity resolution."""

    def _install(user="jeremiah", is_admin=True, skylight_enabled=True, key="test-token"):
        async def _resolve(request, body=None):
            if request.headers.get("Authorization", "") != f"Bearer {key}":
                raise HTTPException(status_code=401, detail="Authentication required")
            return {"user": user, "is_admin": is_admin, "skylight_enabled": skylight_enabled}

        monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _resolve)
        return key

    return _install


@pytest.fixture
def chores_client(resolver):
    def _make(user="jeremiah", is_admin=True, skylight_enabled=True, key="test-token"):
        resolver(user=user, is_admin=is_admin, skylight_enabled=skylight_enabled, key=key)
        return TestClient(app, headers={"Authorization": f"Bearer {key}"}, raise_server_exceptions=False)

    return _make


def _forwarded_user(captured):
    """The `user` param the gateway sent on, or None when nothing was sent."""
    if not captured["calls"]:
        return None
    return captured["calls"][0]["params"]["user"]


# ── who sees what ───────────────────────────────────────────────────────────
def test_admin_without_a_scope_gets_the_whole_frame(chores_client, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resp = chores_client(user="jeremiah", is_admin=True).get("/api/integrations/skylight/chores")
    assert resp.status_code == 200
    assert _forwarded_user(captured) == ""


def test_admin_can_narrow_to_their_own_chores(chores_client, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resp = chores_client(user="jeremiah", is_admin=True).get(
        "/api/integrations/skylight/chores", params={"scope": "me", "date": "today"}
    )
    assert resp.status_code == 200
    assert _forwarded_user(captured) == "jeremiah"
    assert captured["calls"][0]["params"]["date"] == "today"


def test_admin_can_ask_for_one_member_by_login_name(chores_client, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resp = chores_client(user="jeremiah", is_admin=True).get(
        "/api/integrations/skylight/chores", params={"scope": "michele"}
    )
    assert resp.status_code == 200
    assert _forwarded_user(captured) == "michele"


def test_member_always_gets_their_own_chores(chores_client, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resp = chores_client(user="michele", is_admin=False).get(
        "/api/integrations/skylight/chores", params={"scope": "all"}
    )
    assert resp.status_code == 200
    assert _forwarded_user(captured) == "michele"


def test_member_asking_for_someone_else_is_refused_and_forwards_nothing(chores_client, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resp = chores_client(user="michele", is_admin=False).get(
        "/api/integrations/skylight/chores", params={"scope": "jeremiah"}
    )
    assert resp.status_code == 403
    assert "your own chores" in resp.json()["message"]
    assert captured["calls"] == [], "a refused scope still reached the execution service"


def test_a_disabled_account_is_told_so_and_forwards_nothing(chores_client, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resp = chores_client(user="michele", is_admin=False, skylight_enabled=False).get(
        "/api/integrations/skylight/chores"
    )
    assert resp.status_code == 400
    assert "disabled" in resp.json()["message"].lower()
    assert captured["calls"] == []


def test_anonymous_callers_are_rejected(resolver, monkeypatch):
    captured = _patch_execution(monkeypatch)
    resolver()  # identity resolution is live; there is simply no token here
    anon = TestClient(app, raise_server_exceptions=False)
    resp = anon.get("/api/integrations/skylight/chores")
    assert resp.status_code == 401
    assert captured["calls"] == []