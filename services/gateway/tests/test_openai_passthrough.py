"""The OpenAI client-tool passthrough gate and proxy behaviour."""
from __future__ import annotations

import json

import pytest

from services.gateway import openai_passthrough as pt


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class _Request:
    def __init__(self, headers=None):
        self.headers = _Headers({k.lower(): v for k, v in (headers or {}).items()})


def _body(**over):
    body = {
        "model": "qwen3-6-35b",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
    }
    body.update(over)
    return body


def test_safe_model_name_rejects_virtual_aliases_and_paths():
    assert pt.safe_model_name("qwen3-6-35b")
    assert pt.safe_model_name("kwaipilot-kat-coder-v2-5-dev-iq4-nl")
    assert not pt.safe_model_name("jarvis")
    assert not pt.safe_model_name("Assistant")
    assert not pt.safe_model_name("coding")
    assert not pt.safe_model_name("../../etc/passwd")
    assert not pt.safe_model_name("")
    assert not pt.safe_model_name("http://evil.test/model")


def test_gate_requires_a_verified_key_tools_or_opt_in_and_a_concrete_model():
    request = _Request()
    assert pt.should_passthrough(
        request=request, body=_body(), presented_key="key", selected_model="qwen3-6-35b",
        explicit_model="qwen3-6-35b", is_openai=True,
    )
    # No key: a keyless household/kiosk caller must never be proxied.
    assert not pt.should_passthrough(
        request=request, body=_body(), presented_key="", selected_model="qwen3-6-35b",
        is_openai=True,
    )
    # No tools and no opt-in header: Jarvis's own chat path handles it.
    assert not pt.should_passthrough(
        request=request, body=_body(tools=[]), presented_key="key", selected_model="qwen3-6-35b",
        is_openai=True,
    )
    # Explicit opt-in works without tools.
    assert pt.should_passthrough(
        request=_Request({"x-jarvis-passthrough": "1"}), body=_body(tools=[]),
        presented_key="key", selected_model="qwen3-6-35b", is_openai=True,
    )
    assert pt.should_passthrough(
        request=_Request({"x-request-source": "opencode"}), body=_body(tools=[]),
        presented_key="key", selected_model="qwen3-6-35b", is_openai=True,
    )


def test_gate_rejects_agentic_modes_virtual_models_and_voice():
    assert not pt.should_passthrough(
        request=_Request(), body=_body(agentic=True), presented_key="key",
        selected_model="qwen3-6-35b", is_openai=True,
    )
    assert not pt.should_passthrough(
        request=_Request(), body=_body(sharedllm_tools=True), presented_key="key",
        selected_model="qwen3-6-35b", is_openai=True,
    )
    assert not pt.should_passthrough(
        request=_Request(), body=_body(), presented_key="key", selected_model="qwen3-6-35b",
        explicit_model="jarvis", is_openai=True,
    )
    assert not pt.should_passthrough(
        request=_Request(), body=_body(), presented_key="key", selected_model="qwen3-6-35b",
        is_openai=True, voice_context={"satellite": "kitchen"},
    )
    # /api/chat is not the OpenAI route.
    assert not pt.should_passthrough(
        request=_Request(), body=_body(), presented_key="key", selected_model="qwen3-6-35b",
        is_openai=False,
    )


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://llm:11434", "http://llm:11434/v1/chat/completions"),
        ("http://llm:11434/", "http://llm:11434/v1/chat/completions"),
        ("http://llm:11434/v1", "http://llm:11434/v1/chat/completions"),
        ("http://llm/v1/chat/completions", "http://llm/v1/chat/completions"),
        ("", ""),
    ],
)
def test_completions_url(base, expected):
    assert pt.completions_url(base) == expected


def test_outbound_payload_drops_jarvis_identity_and_internal_modes():
    payload = pt.outbound_payload(
        {
            "model": "qwen3-6-35b",
            "messages": [],
            "api_key": "jarvis-secret",
            "user_id": "alice",
            "agentic": True,
            "sharedllm_tools": True,
            "tools": [],
        },
        selected_model="qwen3-6-35b",
    )
    assert payload == {"model": "qwen3-6-35b", "messages": [], "tools": []}
    assert pt.outbound_payload({"messages": []}, selected_model="qwen3-6-35b")["model"] == "qwen3-6-35b"


class _Content:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_any(self):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, status=200, *, content_type="text/event-stream", chunks=(), data=None, text=""):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.content = _Content(chunks)
        self._data = data
        self._text = text
        self.released = False

    async def json(self, content_type=None):
        return self._data

    async def text(self):
        return self._text

    def release(self):
        self.released = True


class _Session:
    last: _Session | None = None

    def __init__(self, *args, **kwargs):
        self.closed = False
        self.sent: dict = {}
        self.response = self.planned
        _Session.last = self

    planned = _Response()

    async def post(self, url, json=None, headers=None):
        self.sent = {"url": url, "json": json, "headers": headers}
        return self.response

    async def close(self):
        self.closed = True


async def test_streaming_passthrough_preserves_sse_chunks_and_forwards_source(monkeypatch):
    chunks = [b'data: {"choices":[{"delta":{"tool_calls":[]}}]}\n\n', b"data: [DONE]\n\n"]
    _Session.planned = _Response(chunks=chunks)
    monkeypatch.setattr(pt.aiohttp, "ClientSession", _Session)

    response = await pt.forward_chat_completion(
        _Request({"x-request-source": "opencode"}),
        _body(stream=True, api_key="secret"),
        base_url="http://llm:11434",
        selected_model="qwen3-6-35b",
    )
    assert response.media_type == "text/event-stream"
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"".join(chunks)
    sent = _Session.last.sent
    assert sent["url"] == "http://llm:11434/v1/chat/completions"
    assert sent["headers"]["X-Request-Source"] == "opencode"
    assert "api_key" not in sent["json"]
    assert _Session.last.closed is True


async def test_non_streaming_passthrough_returns_the_backend_json(monkeypatch):
    _Session.planned = _Response(
        content_type="application/json",
        data={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
    )
    monkeypatch.setattr(pt.aiohttp, "ClientSession", _Session)
    response = await pt.forward_chat_completion(
        _Request(), _body(), base_url="http://llm:11434", selected_model="qwen3-6-35b"
    )
    assert response.status_code == 200
    assert json.loads(response.body)["choices"][0]["message"]["content"] == "ok"


async def test_a_backend_4xx_is_returned_as_the_same_status(monkeypatch):
    _Session.planned = _Response(
        status=422, content_type="application/json",
        text='{"error": {"message": "bad tool arguments", "type": "invalid_request_error"}}',
    )
    monkeypatch.setattr(pt.aiohttp, "ClientSession", _Session)
    response = await pt.forward_chat_completion(
        _Request(), _body(), base_url="http://llm:11434", selected_model="qwen3-6-35b"
    )
    assert response.status_code == 422
    assert json.loads(response.body)["error"]["message"] == "bad tool arguments"


async def test_a_dead_backend_is_a_502_not_a_crash(monkeypatch):
    class _Dead(_Session):
        async def post(self, url, json=None, headers=None):
            raise OSError("connection refused")

    monkeypatch.setattr(pt.aiohttp, "ClientSession", _Dead)
    response = await pt.forward_chat_completion(
        _Request(), _body(), base_url="http://llm:11434", selected_model="qwen3-6-35b"
    )
    assert response.status_code == 502
    assert "did not answer" in json.loads(response.body)["error"]["message"]


def test_invalid_key_on_the_openai_route_is_a_real_http_401(monkeypatch):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from services.gateway import main as gateway_main

    async def reject(*args, **kwargs):
        raise HTTPException(status_code=401, detail="invalid key")

    monkeypatch.setattr(gateway_main, "resolve_identity", reject)
    client = TestClient(gateway_main.app, client=("127.0.0.1", 50000))
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3-6-35b", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer definitely-invalid"},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


def test_a_valid_key_with_client_tools_takes_the_passthrough_path(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from services.gateway import main as gateway_main

    forwarded: dict = {}

    async def fake_forward(request, body, **kwargs):
        forwarded.update(body=body, kwargs=kwargs)
        return JSONResponse({"object": "chat.completion", "choices": []})

    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": "alice"}))
    monkeypatch.setattr(
        gateway_main, "get_all_settings",
        AsyncMock(return_value={"llm_local_url": "http://llm:11434"}),
    )
    monkeypatch.setattr(pt, "forward_chat_completion", fake_forward)
    client = TestClient(gateway_main.app, client=("127.0.0.1", 50000))
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3-6-35b",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
        },
        headers={"Authorization": "Bearer valid-key", "x-request-source": "opencode"},
    )

    assert resp.status_code == 200
    assert resp.json()["object"] == "chat.completion"
    assert forwarded["kwargs"]["selected_model"] == "qwen3-6-35b"
    assert forwarded["body"]["api_key"] == "valid-key"  # the proxy strips it, not the gate

