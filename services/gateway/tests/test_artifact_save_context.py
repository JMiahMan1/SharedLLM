"""Regression tests for workspace artifact saves from the agent loop.

Root cause (mission 44): the TTSRequest / image-generation interceptors called
workspace_runtime /files/write WITHOUT user_context. Non-system workspaces
reject such writes with 400 'User context is required for this workspace', so
every TTS save silently failed while the interceptor still reported SUCCESS —
the model looped on TTSRequest and the workspace stayed empty.
"""

import asyncio
import json

import pytest

from services.gateway import agent_loop


class _FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload
        self.text_val = payload if isinstance(payload, (str, Exception)) else json.dumps(payload)

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def text(self):
        return self.text_val


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _ClientCtx:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


def _patch_client(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(
        agent_loop, "shared_http_client", lambda: _ClientCtx(client)
    )
    return client


def test_save_b64_artifact_sends_user_context(monkeypatch):
    """The /files/write payload MUST carry user_context; non-system workspaces
    hard-reject writes without it (the mission-44 killer)."""
    client = _patch_client(monkeypatch, [_FakeResponse(200, {"status": "SUCCESS"})])
    monkeypatch.setattr(agent_loop, "WORKSPACE_RUNTIME_SVC", "http://ws:8007")
    monkeypatch.setattr(agent_loop, "INTERNAL_SECRET", "sekret")

    status, data = asyncio.run(
        agent_loop._save_b64_artifact("ws-1", "a.wav", "QUJD", {"user": "default", "is_admin": False})
    )

    assert status == 200
    assert data == {"status": "SUCCESS"}
    url, kwargs = client.calls[0]
    assert url == "http://ws:8007/files/write"
    body = kwargs["json"]
    assert body["user_context"] == {"user": "default", "is_admin": False}
    assert body["workspace_id"] == "ws-1"
    assert body["relative_path"] == "a.wav"
    assert body["content_base64"] == "QUJD"
    assert body["create_parents"] is True
    assert kwargs["headers"]["X-Internal-Secret"] == "sekret"
    assert kwargs["timeout"].total == 30.0


def test_save_b64_artifact_success_returns_parsed_json(monkeypatch):
    client = _patch_client(monkeypatch, [_FakeResponse(200, {"status": "SUCCESS", "size": 4})])
    monkeypatch.setattr(agent_loop, "WORKSPACE_RUNTIME_SVC", "http://ws:8007")

    status, data = asyncio.run(agent_loop._save_b64_artifact("ws-1", "b.mp3", "AA==", {"user": "default"}))

    assert status == 200
    assert data == {"status": "SUCCESS", "size": 4}
    assert len(client.calls) == 1


def test_save_b64_artifact_non_200_returns_error_detail(monkeypatch):
    client = _patch_client(
        monkeypatch,
        [_FakeResponse(400, "User context is required for this workspace")],
    )
    monkeypatch.setattr(agent_loop, "WORKSPACE_RUNTIME_SVC", "http://ws:8007")

    status, data = asyncio.run(agent_loop._save_b64_artifact("ws-1", "c.wav", "AA==", {"user": "default"}))

    assert status == 400
    assert data["status"] == "ERROR"
    assert "User context is required" in data["detail"]


def test_save_b64_artifact_500_json_fallback(monkeypatch):
    client = _patch_client(
        monkeypatch,
        [_FakeResponse(500, {"status": "ERROR", "detail": "boom"})],
    )
    monkeypatch.setattr(agent_loop, "WORKSPACE_RUNTIME_SVC", "http://ws:8007")

    status, data = asyncio.run(agent_loop._save_b64_artifact("ws-1", "d.mp4", "AA==", {"user": "default"}))

    assert status == 500
    assert data == {"status": "ERROR", "detail": '{"status": "ERROR", "detail": "boom"}'}


def test_save_b64_artifact_bad_json_body_returns_empty_dict(monkeypatch):
    client = _patch_client(monkeypatch, [_FakeResponse(200, Exception("not json"))])
    monkeypatch.setattr(agent_loop, "WORKSPACE_RUNTIME_SVC", "http://ws:8007")

    status, data = asyncio.run(agent_loop._save_b64_artifact("ws-1", "e.wav", "AA==", {"user": "default"}))

    assert status == 200
    assert data == {}
