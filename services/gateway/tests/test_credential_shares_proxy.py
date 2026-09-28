"""Gateway proxy for the per-user credential-share endpoints.

The UI reaches Identity through the gateway, so both the read and the
admin-only write need a route. These tests assert the exact upstream method,
path, body and that the caller's Authorization header is forwarded.
"""
import json
from contextlib import asynccontextmanager

from services.gateway import main as gateway_main


def _patch_client(monkeypatch, status: int, payload: dict, captured: dict):
    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload)

        async def json(self):
            return payload

    class _Client:
        async def get(self, url, **kwargs):
            captured["method"] = "get"
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            return _Resp()

        async def put(self, url, **kwargs):
            captured["method"] = "put"
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["json"] = kwargs.get("json")
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake_shared_client():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_shared_client)
    return captured


def test_get_credential_shares_proxies_to_identity(client, monkeypatch):
    captured = _patch_client(
        monkeypatch,
        200,
        {"username": "bob", "services": ["music_assistant"], "shared_owner": "default"},
        {},
    )

    resp = client.get(
        "/api/users/bob/credential-shares",
        headers={"Authorization": "Bearer user-key"},
    )

    assert resp.status_code == 200
    assert resp.json()["services"] == ["music_assistant"]
    assert captured["method"] == "get"
    assert captured["url"].endswith("/api/users/bob/credential-shares")
    assert captured["headers"].get("Authorization") == "Bearer user-key"


def test_put_credential_shares_forwards_body(client, monkeypatch):
    captured = _patch_client(
        monkeypatch,
        200,
        {"username": "casey", "services": ["home_assistant"], "granted_by": "default"},
        {},
    )

    resp = client.put(
        "/api/users/casey/credential-shares",
        json={"services": ["home_assistant"], "note": "guest tablet"},
        headers={"Authorization": "Bearer admin-key"},
    )

    assert resp.status_code == 200
    assert resp.json()["granted_by"] == "default"
    assert captured["method"] == "put"
    assert captured["url"].endswith("/api/users/casey/credential-shares")
    assert captured["json"] == {"services": ["home_assistant"], "note": "guest tablet"}
    assert captured["headers"].get("Authorization") == "Bearer admin-key"


def test_put_credential_shares_passes_identity_error_through(client, monkeypatch):
    _patch_client(monkeypatch, 403, {"detail": "Admin only"}, {})

    resp = client.put(
        "/api/users/bob/credential-shares",
        json={"services": ["audiobookshelf"]},
        headers={"Authorization": "Bearer user-key"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin only"
