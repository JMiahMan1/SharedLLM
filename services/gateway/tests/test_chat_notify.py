"""Family chat messages become phone notifications.

The notifier reads each family member's own conversation list and announces a
conversation's last message once, when it is new, unread and someone else's.
The phone collects those notices through a long poll on the gateway.
"""
import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import chat_notify
from services.gateway import main as gateway_main


class _Redis:
    def __init__(self):
        self.h: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value):
        self.kv[key] = value

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    """Identity, execution and telemetry, as far as the notifier needs them."""

    def __init__(self, conversations):
        self.conversations = conversations  # username -> list of summaries
        self.notified: list[dict] = []

    def get(self, url, **kw):
        assert url.endswith("/api/users")
        return _Resp(200, [
            {"username": "default", "nextcloud_user": "jarvis", "is_system_default": True},
            {"username": "jeremiah", "nextcloud_user": "jeremiah"},
            {"username": "leo", "nextcloud_user": None},
        ])

    def post(self, url, json=None, **kw):
        if url.endswith("/api/resolve"):
            return _Resp(200, {"user": json["rag_user"], "nextcloud_user": json["rag_user"]})
        if url.endswith("/execute/talk"):
            user = json["user_context"]["user"]
            return _Resp(200, {"status": "SUCCESS", "detail": {"conversations": self.conversations.get(user, [])}})
        if url.endswith("/api/telemetry/notify"):
            self.notified.append(json)
            return _Resp(200, {"status": "SUCCESS"})
        raise AssertionError(url)


def _conv(msg_id, *, actor="michele", unread=1, kind=2, text="Dinner at 6", system=False):
    return {
        "token": "fam", "display_name": "Summers Family", "type": kind,
        "last_message_id": msg_id, "last_message": text, "last_message_actor": actor.capitalize(),
        "last_message_actor_id": actor, "unread_messages": unread, "last_message_system": system,
    }


def _run(r, client):
    return asyncio.run(chat_notify.check_once(
        r, client, identity="http://id", execution="http://ex", telemetry="http://tel", secret="s"))


def test_history_is_remembered_not_replayed_then_new_messages_notify():
    r, client = _Redis(), _Client({"jeremiah": [_conv(10)]})
    assert _run(r, client) == 0 and client.notified == []
    client.conversations["jeremiah"] = [_conv(11)]
    assert _run(r, client) == 1
    n = client.notified[0]
    assert n["user"] == "jeremiah" and n["kind"] == "chat"
    assert n["title"] == "Summers Family" and n["body"] == "Michele: Dinner at 6"
    assert n["data"]["token"] == "fam" and n["data"]["url"] == "/family?chat=fam"
    assert n["once"] == "chat:fam:11"
    assert _run(r, client) == 0, "the same message is announced once"


def test_a_brand_new_conversation_announces_its_first_message():
    r, client = _Redis(), _Client({"jeremiah": [_conv(10)]})
    _run(r, client)
    dm = {**_conv(3, kind=1, text="Hi Dad"), "token": "newdm"}
    client.conversations["jeremiah"] = [_conv(10), dm]
    assert _run(r, client) == 1
    assert client.notified[0]["data"]["token"] == "newdm" and client.notified[0]["body"] == "Hi Dad"


@pytest.mark.parametrize("conv", [
    _conv(11, actor="jeremiah"),  # my own message
    _conv(11, unread=0),  # already read on another device
    _conv(11, system=True),  # "Michele joined"
])
def test_nothing_to_announce(conv):
    r, client = _Redis(), _Client({"jeremiah": [_conv(10)]})
    _run(r, client)
    client.conversations["jeremiah"] = [conv]
    assert _run(r, client) == 0


def test_a_direct_message_is_titled_with_the_sender():
    notice = chat_notify.notice_for(_conv(5, kind=1, text="Picking up milk"), "jeremiah")
    assert notice["title"] == "Michele" and notice["body"] == "Picking up milk"


def test_previews_drop_envelopes_and_placeholders():
    fenced = "Leo scored 9/10\n\n```jarvis-envelope\n{\"kind\": \"game\"}\n```"
    assert chat_notify.preview(fenced) == "Leo scored 9/10"
    assert chat_notify.preview("{file}") == "Sent an attachment"


def test_one_unreachable_person_does_not_stop_the_rest(monkeypatch):
    r, client = _Redis(), _Client({"jeremiah": [_conv(10)]})
    calls = []

    async def flaky(r_, client_, username, **kw):
        calls.append(username)
        raise ConnectionError("execution restarting")

    monkeypatch.setattr(chat_notify, "_check_user", flaky)
    assert _run(r, client) == 0 and calls == ["jeremiah"]


# ---------------------------------------------------------------- long poll


@pytest.fixture
def waiting(monkeypatch):
    outbox: list[dict] = []

    async def user(_request):
        return "jeremiah"

    async def telemetry(method, path, **kw):
        assert path == "/api/telemetry/notifications/jeremiah"
        return {"notifications": list(outbox)}

    monkeypatch.setattr(gateway_main, "_user_id_from_request", user)
    monkeypatch.setattr(gateway_main, "_telemetry_call", telemetry)
    monkeypatch.setattr(gateway_main, "NOTIFY_WAIT_POLL_S", 0.01)
    return outbox


def test_a_first_wait_only_sets_the_cursor(waiting):
    waiting.append({"id": "a", "created_at": "2026-10-14T10:00:00+00:00"})
    body = TestClient(gateway_main.app).get("/api/telemetry/notifications/wait").json()
    assert body == {"notifications": [], "cursor": "2026-10-14T10:00:00+00:00"}


def test_new_notices_after_the_cursor_are_returned(waiting):
    waiting.extend([
        {"id": "a", "created_at": "2026-10-14T10:00:00+00:00"},
        {"id": "b", "created_at": "2026-10-14T10:05:00+00:00"},
    ])
    body = TestClient(gateway_main.app).get(
        "/api/telemetry/notifications/wait", params={"since": "2026-10-14T10:00:00+00:00"}).json()
    assert [n["id"] for n in body["notifications"]] == ["b"]
    assert body["cursor"] == "2026-10-14T10:05:00+00:00"


def test_nothing_new_returns_empty_when_the_wait_ends(waiting):
    body = TestClient(gateway_main.app).get(
        "/api/telemetry/notifications/wait", params={"since": "2026-10-14T10:00:00+00:00", "timeout": 0.05}).json()
    assert body == {"notifications": [], "cursor": "2026-10-14T10:00:00+00:00"}


def test_the_wait_needs_a_signed_in_user(monkeypatch):
    async def nobody(_request):
        return None

    monkeypatch.setattr(gateway_main, "_user_id_from_request", nobody)
    assert TestClient(gateway_main.app).get("/api/telemetry/notifications/wait").status_code == 401
