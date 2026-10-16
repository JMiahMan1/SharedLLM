"""Phone push routes (docs/PUSH.md): stream proxy, UnifiedPush registration,
notification actions and preference switches."""
import json

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import push_callbacks, push_routes
from services.gateway.main import app

KEY = {"Authorization": "Bearer michele-key", "X-Device-Id": "pixel-1"}


@pytest.fixture
def identity(monkeypatch):
    """Strict key lookup plus a fake identity behind ``push_routes._identity``."""
    calls = []
    state = {"registrations": [
        {"device_id": "pixel-1", "app": "com.nextcloud.talk2", "token": "t1", "topic": "upAAA", "created_at": "c"},
        {"device_id": "tablet", "app": "org.matrix", "token": "t2", "topic": "upBBB", "created_at": "c"},
    ]}

    async def strict(key):
        return {"user": "michele", "is_admin": False} if key == "michele-key" else None

    async def fake(method, path, *, params=None, json=None, headers=None):
        calls.append((method, path, params, json, headers))
        if path == "/api/internal/push/topic":
            return {"topic": "jarvis-michele-secret"}
        if path == "/api/internal/push/up":
            regs = state["registrations"]
            if params.get("device_id"):
                regs = [r for r in regs if r["device_id"] == params["device_id"]]
            return {"registrations": regs}
        if path == "/api/internal/push/up/register":
            return {"topic": "upNEW"}
        if path == "/api/internal/push/up/unregister":
            return {"removed": 1}
        if path == "/api/users/me/notification-preferences":
            return {"status": "SUCCESS", "preferences": {"chat": True, **(json or {})}}
        raise AssertionError(path)

    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", strict)
    monkeypatch.setattr(push_routes, "_identity", fake)
    monkeypatch.setattr(push_routes, "JARVIS_HOST", "https://jarvis.example.com")
    return calls


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class _FakeContent:
    def __init__(self, lines):
        self._lines = lines

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for line in self._lines:
            yield line


class _FakeResp:
    def __init__(self, lines):
        self.content = _FakeContent(lines)
        self.released = False

    def release(self):
        self.released = True


class _FakeSession:
    closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def ntfy(monkeypatch):
    """A stand-in for ntfy's JSON stream that records what was asked."""
    lines = [
        b'{"id":"o1","event":"open","topic":"jarvis-michele-secret,upAAA"}\n',
        b'{"id":"m1","event":"message","topic":"upAAA","message":"AAEC","encoding":"base64"}\n',
        b'{"id":"k1","event":"keepalive","topic":"jarvis-michele-secret,upAAA"}\n',
    ]
    state = {"session": _FakeSession(), "resp": _FakeResp(lines)}

    async def open_stream(topics, since):
        state["topics"], state["since"] = topics, since
        return state["session"], state["resp"]

    monkeypatch.setattr(push_routes, "_open_ntfy_stream", open_stream)
    monkeypatch.setattr(push_routes, "NTFY_TOKEN", "tk_test")
    return state


@pytest.mark.parametrize("verb,path", [
    ("GET", "/api/push/stream"), ("GET", "/api/push/up"), ("POST", "/api/push/up/register"),
    ("POST", "/api/push/up/unregister"), ("POST", "/api/push/action"),
    ("GET", "/api/users/me/notification-preferences"), ("PUT", "/api/users/me/notification-preferences"),
])
def test_every_push_route_needs_a_real_key(client, identity, verb, path):
    assert client.request(verb, path, json={}).status_code == 401
    bad = {"Authorization": "Bearer junk", "X-Device-Id": "pixel-1"}
    assert client.request(verb, path, json={}, headers=bad).status_code == 401


def test_stream_relays_ntfy_for_the_person_and_this_device(client, identity, ntfy):
    resp = client.get("/api/push/stream", params={"since": "m0"}, headers=KEY)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in resp.text.splitlines()]
    assert [e["event"] for e in events] == ["open", "message", "keepalive"]
    assert events[1]["topic"] == "upAAA" and events[1]["encoding"] == "base64"
    assert ntfy["topics"] == ["jarvis-michele-secret", "upAAA"]  # not the tablet's upBBB
    assert ntfy["since"] == "m0"
    assert ntfy["resp"].released and ntfy["session"].closed


def test_stream_accepts_the_key_as_x_api_key(client, identity, ntfy):
    resp = client.get("/api/push/stream", headers={"X-Api-Key": "michele-key", "X-Device-Id": "pixel-1"})
    assert resp.status_code == 200


def test_stream_needs_a_device_id(client, identity, ntfy):
    assert client.get("/api/push/stream", headers={"Authorization": "Bearer michele-key"}).status_code == 400


def test_stream_rejects_a_malformed_since(client, identity, ntfy):
    assert client.get("/api/push/stream", params={"since": "1;drop"}, headers=KEY).status_code == 422


def test_stream_without_a_server_token_is_503(client, identity, monkeypatch):
    monkeypatch.setattr(push_routes, "NTFY_TOKEN", "")
    assert client.get("/api/push/stream", headers=KEY).status_code == 503


async def test_relay_ends_quietly_when_ntfy_goes_silent():
    import asyncio

    class _Silent:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise asyncio.TimeoutError

    resp, session = _FakeResp([]), _FakeSession()
    resp.content = _Silent()
    assert [line async for line in push_routes._relay(session, resp)] == []
    assert resp.released and session.closed


async def test_open_stream_reports_an_unreachable_ntfy(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(push_routes, "NTFY_URL", "http://127.0.0.1:9")
    with pytest.raises(HTTPException) as err:
        await push_routes._open_ntfy_stream(["t"], None)
    assert err.value.status_code == 502


def test_stream_is_a_routine_poll():
    assert gateway_main._is_routine_poll("/api/push/stream")


def test_register_returns_the_public_endpoint(client, identity):
    resp = client.post("/api/push/up/register", headers=KEY, json={"app": "com.nextcloud.talk2", "token": "t9"})
    assert resp.json() == {"endpoint": "https://jarvis.example.com/push/upNEW", "topic": "upNEW"}
    _, path, _, sent, _ = identity[-1]
    assert path == "/api/internal/push/up/register"
    assert sent == {"username": "michele", "device_id": "pixel-1", "app": "com.nextcloud.talk2", "token": "t9"}


def test_register_needs_a_device_id(client, identity):
    resp = client.post("/api/push/up/register", headers={"Authorization": "Bearer michele-key"},
                       json={"app": "a", "token": "t"})
    assert resp.status_code == 400


def test_unregister_from_the_web_needs_no_device(client, identity):
    resp = client.post("/api/push/up/unregister", headers={"Authorization": "Bearer michele-key"},
                       json={"app": "org.matrix", "token": "t2"})
    assert resp.json() == {"status": "SUCCESS", "removed": 1}
    assert identity[-1][3]["device_id"] == ""


def test_list_marks_this_device_and_adds_endpoints(client, identity):
    regs = client.get("/api/push/up", headers=KEY).json()["registrations"]
    assert [(r["app"], r["this_device"]) for r in regs] == [("com.nextcloud.talk2", True), ("org.matrix", False)]
    assert regs[0]["endpoint"] == "https://jarvis.example.com/push/upAAA"
    assert identity[-1][2] == {"username": "michele"}


def test_action_runs_a_registered_callback_as_the_caller(client, identity, monkeypatch):
    seen = {}

    async def fake(request, params, reply):
        seen.update(auth=request.headers.get("Authorization"), params=params, reply=reply)
        return {"status": "SUCCESS"}

    monkeypatch.setitem(push_callbacks.PUSH_CALLBACKS, "test_cb", fake)
    resp = client.post("/api/push/action", headers=KEY, json={"callback": "test_cb", "params": {"a": 1}, "reply": "hi"})
    assert resp.json() == {"status": "SUCCESS"}
    assert seen == {"auth": "Bearer michele-key", "params": {"a": 1}, "reply": "hi"}


def test_action_with_x_api_key_reaches_callbacks_as_a_bearer(client, identity, monkeypatch):
    seen = {}

    async def fake(request, params, reply):
        seen["auth"] = request.headers.get("Authorization")
        return {"status": "SUCCESS"}

    monkeypatch.setitem(push_callbacks.PUSH_CALLBACKS, "test_cb", fake)
    client.post("/api/push/action", headers={"X-Api-Key": "michele-key"}, json={"callback": "test_cb"})
    assert seen["auth"] == "Bearer michele-key"


def test_unknown_callbacks_and_bad_params_are_refused(client, identity):
    assert client.post("/api/push/action", headers=KEY, json={"callback": "rm_rf"}).status_code == 400
    assert client.post("/api/push/action", headers=KEY, json={"callback": "chat_reply", "params": "x"}).status_code == 422


def test_chat_reply_posts_into_talk_as_the_caller(client, identity, monkeypatch):
    sent = []

    async def proxy(request, path, payload, as_user=None):
        sent.append((request.headers.get("Authorization"), path, payload))
        return JSONResponse({"status": "SUCCESS"})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    body = {"callback": "chat_reply", "params": {"token": "abc123"}, "reply": " On my way "}
    assert client.post("/api/push/action", headers=KEY, json=body).json() == {"status": "SUCCESS"}
    assert sent == [("Bearer michele-key", "/execute/talk", {"action": "send", "token": "abc123", "message": "On my way"})]


@pytest.mark.parametrize("body", [{"params": {"token": "abc"}, "reply": "  "}, {"params": {}, "reply": "hi"}])
def test_chat_reply_needs_a_token_and_text(client, identity, body):
    resp = client.post("/api/push/action", headers=KEY, json={"callback": "chat_reply", **body})
    assert resp.status_code == 422


def test_preferences_are_read_and_written_through_identity_as_the_caller(client, identity):
    assert client.get("/api/users/me/notification-preferences", headers=KEY).json()["preferences"] == {"chat": True}
    resp = client.put("/api/users/me/notification-preferences", headers=KEY, json={"bible": False})
    assert resp.json()["preferences"] == {"chat": True, "bible": False}
    method, path, _, body, headers = identity[-1]
    assert (method, body, headers) == ("PUT", {"bible": False}, {"Authorization": "Bearer michele-key"})


async def test_open_stream_asks_ntfy_for_the_topics_with_the_server_token(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    seen = {}

    async def handler(request):
        seen.update(path=request.path, since=request.query.get("since"), auth=request.headers.get("Authorization"))
        resp = web.StreamResponse()
        await resp.prepare(request)
        await resp.write(b'{"id":"o1","event":"open"}\n')
        await resp.write(b'{"id":"m1","event":"message","message":"x"}\n')
        return resp

    server_app = web.Application()
    server_app.router.add_get("/{topics}/json", handler)
    async with TestServer(server_app) as server:
        monkeypatch.setattr(push_routes, "NTFY_URL", str(server.make_url("")).rstrip("/"))
        monkeypatch.setattr(push_routes, "NTFY_TOKEN", "tk_test")
        session, resp = await push_routes._open_ntfy_stream(["jarvis-m-s", "upAAA"], "m0")
        lines = [line async for line in push_routes._relay(session, resp)]
    assert seen == {"path": "/jarvis-m-s,upAAA/json", "since": "m0", "auth": "Bearer tk_test"}
    assert [json.loads(line)["id"] for line in lines] == ["o1", "m1"]
    assert session.closed
