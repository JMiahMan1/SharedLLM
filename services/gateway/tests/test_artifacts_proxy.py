"""Artifact routes exist at the gateway and carry the caller's identity.

Storage is internal-secret protected, so the gateway is the only place a
browser's identity becomes ``X-User``. A missing proxy would leave artifacts
working in storage tests yet unreachable in the app.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Content:
    def __init__(self, content: bytes):
        self._content = content

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._content), size):
            yield self._content[start:start + size]


class _Resp:
    def __init__(self, status=200, payload=None, headers=None, content=b""):
        self.status = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {"Content-Type": "application/json"}
        self._content = content
        self.content = _Content(content)
        self.released = False

    async def json(self):
        return self._payload

    async def read(self):
        return self._content

    async def text(self):
        return json.dumps(self._payload)

    def release(self):
        self.released = True


class _Client:
    def __init__(self):
        self.calls: list[dict] = []
        self.responses: list[_Resp] = []

    async def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self.responses.pop(0) if self.responses else _Resp()

    async def get(self, url, **kw):
        self.calls.append({"method": "GET", "url": url, **kw})
        return self.responses.pop(0) if self.responses else _Resp()

    async def delete(self, url, **kw):
        self.calls.append({"method": "DELETE", "url": url, **kw})
        return self.responses.pop(0) if self.responses else _Resp()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def upstream(monkeypatch):
    fake = _Client()

    @asynccontextmanager
    async def factory():
        yield fake

    async def identity(_request):
        return {"user": "jeremiah", "is_admin": True}

    monkeypatch.setattr(gateway_main, "shared_http_client", factory)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: fake)
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", identity)
    return fake


def test_creating_an_artifact_forwards_the_owner_and_base64(client, upstream):
    response = client.post(
        "/api/artifacts",
        json={"name": "note.txt", "kind": "document", "content_b64": "aGVsbG8="},
    )
    assert response.status_code == 200
    call = upstream.calls[0]
    assert call["url"].endswith(":8005/artifacts")
    assert call["headers"]["X-User"] == "jeremiah"
    assert call["headers"]["X-Admin"] == "true"
    assert call["json"]["content_b64"] == "aGVsbG8="


def test_download_rewrites_the_storage_url_to_the_gateway(client, upstream):
    upstream.responses.append(
        _Resp(payload={"url": "/artifacts/abc/stream?token=xyz", "expires_in": 900})
    )
    response = client.get("/api/artifacts/abc/download")
    assert response.status_code == 200
    assert response.json()["url"] == "/api/artifacts/abc/stream?token=xyz"


def test_stream_uses_the_signed_token_without_a_gateway_login(client, upstream):
    upstream.responses.append(
        _Resp(
            status=206,
            headers={
                "Content-Type": "audio/wav",
                "Content-Range": "bytes 0-3/10",
                "Accept-Ranges": "bytes",
            },
            content=b"RIFF",
        )
    )
    response = client.get(
        "/api/artifacts/abc/stream?token=xyz", headers={"Range": "bytes=0-3"}
    )
    assert response.status_code == 206
    assert response.content == b"RIFF"
    call = upstream.calls[0]
    assert call["params"] == {"token": "xyz"}
    assert call["headers"]["Range"] == "bytes=0-3"


def test_only_an_admin_can_read_system_storage_usage(client, upstream, monkeypatch):
    async def non_admin(_request):
        return {"user": "michele", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", non_admin)
    assert client.get("/api/storage/usage/system").status_code == 403
    assert upstream.calls == []
