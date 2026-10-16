"""POST /api/telemetry/notify per docs/PUSH.md: categories, preferences,
actions, @admins fan-out and publishing to ntfy."""
import json
import logging

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi.testclient import TestClient

from services.telemetry import notices, ntfy
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

from fakes import FakeRedis

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}


@pytest.fixture
def rc(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    return fake


@pytest.fixture
def client():
    # No ``with``: the lifespan (scheduler and worker loops) is not started.
    return TestClient(app)


@pytest.fixture
def sent(monkeypatch):
    """Records web push and ntfy deliveries instead of making them."""
    out = {"webpush": [], "ntfy": []}

    async def send(user, title, body, data=None):
        out["webpush"].append(user)
        return {"webpush": 1, "fcm": False, "pruned": 0}

    async def publish(session, topic, notice):
        out["ntfy"].append((topic, notice))
        return True

    monkeypatch.setattr("services.telemetry.main.push.send_to_user", send)
    monkeypatch.setattr(notices.ntfy, "publish", publish)
    return out


@pytest.fixture
def identity(monkeypatch):
    """A fake identity: preferences per user, admins, topics."""
    state = {"prefs": {}, "admins": ["jeremiah"], "calls": []}

    async def get(session, path, params=None):
        state["calls"].append(path)
        user = (params or {}).get("username")
        if path == "/api/internal/push/preferences":
            return {"preferences": state["prefs"].get(user, {})}
        if path == "/api/internal/push/topic":
            return {"topic": f"jarvis-{user}-secret"}
        if path == "/api/internal/push/admins":
            return {"admins": state["admins"]}
        raise AssertionError(path)

    monkeypatch.setattr(notices, "_identity_get", get)
    return state


def _outbox(client, user):
    return client.get(f"/api/telemetry/notifications/{user}", headers=HEADERS).json()["notifications"]


def test_a_full_notice_reaches_the_outbox_web_push_and_ntfy(client, rc, sent, identity):
    body = {
        "user": "michele", "category": "chat", "kind": "chat", "title": "Jeremiah", "body": "On my way",
        "priority": 4, "tag": "chat:abc123", "data": {"url": "/family?chat=abc123"},
        "actions": [
            {"label": "Reply", "type": "reply", "callback": "chat_reply", "params": {"token": "abc123"}},
            {"label": "Open", "type": "view", "url": "/family?chat=abc123"},
        ],
    }
    resp = client.post("/api/telemetry/notify", headers=HEADERS, json=body)
    assert resp.status_code == 200 and resp.json()["ntfy"] is True
    stored = _outbox(client, "michele")[-1]
    assert stored["category"] == "chat" and stored["priority"] == 4 and stored["tag"] == "chat:abc123"
    assert sent["webpush"] == ["michele"]
    topic, notice = sent["ntfy"][0]
    assert topic == "jarvis-michele-secret"
    assert notice["id"] == stored["id"] and notice["created_at"] == stored["created_at"]
    assert notice["actions"][0] == {"label": "Reply", "type": "reply", "callback": "chat_reply",
                                    "params": {"token": "abc123"}}
    assert "read" not in notice


def test_category_defaults_to_general(client, rc, sent):
    client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": "Hi"})
    assert _outbox(client, "michele")[-1]["category"] == "general"
    assert _outbox(client, "michele")[-1]["priority"] == 3


@pytest.mark.parametrize("bad", [
    {"priority": 0}, {"priority": 6}, {"priority": "3"}, {"priority": True},
    {"category": "Not A Category"},
    {"actions": "nope"},
    {"actions": [{"label": "x", "type": "view", "url": "/"}] * 4},
    {"actions": [{"label": "x", "type": "http", "url": "/"}]},
    {"actions": [{"label": "x", "type": "view"}]},
    {"actions": [{"label": "", "type": "view", "url": "/"}]},
    {"actions": [{"label": "x", "type": "callback"}]},
    {"actions": [{"label": "x", "type": "callback", "callback": "Bad Name"}]},
    {"actions": [{"label": "x", "type": "reply", "callback": "chat_reply", "params": "token"}]},
])
def test_invalid_notices_are_refused(client, rc, sent, bad):
    resp = client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": "x", **bad})
    assert resp.status_code == 422
    assert sent["webpush"] == [] and sent["ntfy"] == []


def test_a_category_turned_off_is_dropped_and_recorded_nowhere(client, rc, sent, identity):
    identity["prefs"]["michele"] = {"bible": False}
    body = {"user": "michele", "category": "bible", "title": "Read Psalm 23", "once": "bible:1"}
    resp = client.post("/api/telemetry/notify", headers=HEADERS, json=body).json()
    assert resp["dropped"] == "preference"
    assert _outbox(client, "michele") == []
    assert sent == {"webpush": [], "ntfy": []}
    assert not any(k.startswith("telemetry:notify_once") for k in rc.strings)


def test_sos_is_delivered_even_with_family_safety_off(client, rc, sent, identity):
    identity["prefs"]["michele"] = {"family_safety": False}
    sos = {"user": "michele", "category": "family_safety", "priority": 5, "title": "SOS from Jeremiah"}
    assert "dropped" not in client.post("/api/telemetry/notify", headers=HEADERS, json=sos).json()
    calm = {**sos, "priority": 3, "title": "Battery low"}
    assert client.post("/api/telemetry/notify", headers=HEADERS, json=calm).json()["dropped"] == "preference"
    assert [n["title"] for n in _outbox(client, "michele")] == ["SOS from Jeremiah"]


def test_preferences_are_cached_briefly(client, rc, sent, identity):
    for _ in range(3):
        client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": "x"})
    assert identity["calls"].count("/api/internal/push/preferences") == 1
    assert identity["calls"].count("/api/internal/push/topic") == 1


def test_admins_fan_out_to_every_admin(client, rc, sent, identity):
    identity["admins"] = ["jeremiah", "kate"]
    body = {"user": "@admins", "category": "admin", "title": "Disk nearly full", "once": "disk:root"}
    resp = client.post("/api/telemetry/notify", headers=HEADERS, json=body).json()
    assert set(resp["recipients"]) == {"jeremiah", "kate"}
    assert sent["webpush"] == ["jeremiah", "kate"]
    assert [t for t, _ in sent["ntfy"]] == ["jarvis-jeremiah-secret", "jarvis-kate-secret"]
    again = client.post("/api/telemetry/notify", headers=HEADERS, json=body).json()
    assert all(r == {"duplicate": True} for r in again["recipients"].values())


def test_admins_unresolvable_is_503(client, rc, sent):
    resp = client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "@admins", "title": "x"})
    assert resp.status_code == 503


def test_unreachable_identity_still_delivers_to_the_outbox(client, rc, sent):
    resp = client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": "x"}).json()
    assert resp["ntfy"] is False and sent["webpush"] == ["michele"]
    assert len(_outbox(client, "michele")) == 1


def test_ntfy_failure_does_not_fail_the_request_and_logs_once(client, rc, identity, monkeypatch, caplog):
    async def send(user, title, body, data=None):
        return {}

    monkeypatch.setattr("services.telemetry.main.push.send_to_user", send)
    monkeypatch.setattr(ntfy, "NTFY_TOKEN", "tk_test")
    monkeypatch.setattr(ntfy, "NTFY_URL", "http://127.0.0.1:9")
    with caplog.at_level(logging.WARNING, logger="telemetry.ntfy"):
        for title in ("one", "two"):
            resp = client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": title})
            assert resp.status_code == 200 and resp.json()["ntfy"] is False
    assert len([r for r in caplog.records if "ntfy publish failed" in r.getMessage()]) == 1
    assert [n["title"] for n in _outbox(client, "michele")] == ["one", "two"]


async def test_publish_sends_the_notice_as_the_message(monkeypatch):
    received = []

    async def handler(request):
        received.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response({"id": "abc", "event": "message"})

    server_app = web.Application()
    server_app.router.add_post("/", handler)
    async with TestServer(server_app) as server:
        monkeypatch.setattr(ntfy, "NTFY_URL", str(server.make_url("")).rstrip("/"))
        monkeypatch.setattr(ntfy, "NTFY_TOKEN", "tk_test")
        notice = {"id": "n1", "title": "Leak", "category": "home", "priority": 5, "created_at": "t"}
        async with aiohttp.ClientSession() as session:
            assert await ntfy.publish(session, "jarvis-kate-x", notice) is True
    auth, body = received[0]
    assert auth == "Bearer tk_test"
    assert body["topic"] == "jarvis-kate-x" and body["priority"] == 5
    assert body["title"] == "Leak" and body["tags"] == ["home"]
    assert json.loads(body["message"]) == notice


async def test_publish_without_a_token_does_nothing(monkeypatch):
    monkeypatch.setattr(ntfy, "NTFY_TOKEN", "")
    async with aiohttp.ClientSession() as session:
        assert await ntfy.publish(session, "t", {"id": "n"}) is False


async def test_oversized_notices_are_not_published(monkeypatch):
    monkeypatch.setattr(ntfy, "NTFY_TOKEN", "tk_test")
    async with aiohttp.ClientSession() as session:
        assert await ntfy.publish(session, "t", {"id": "n", "data": {"x": "y" * 5000}}) is False


def test_the_topic_is_cached_for_ten_minutes(client, rc, sent, identity, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(notices.time, "monotonic", lambda: now[0])
    client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": "x"})
    now[0] += notices.TOPIC_CACHE_SECONDS - 1
    client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": "x"})
    assert identity["calls"].count("/api/internal/push/topic") == 1
    now[0] += 2
    client.post("/api/telemetry/notify", headers=HEADERS, json={"user": "michele", "title": "x"})
    assert identity["calls"].count("/api/internal/push/topic") == 2


def test_a_failed_outbox_write_does_not_mark_the_notice_sent(rc, sent, identity, monkeypatch):
    """The ``once`` key is released, so the sender's retry is delivered."""
    import asyncio

    real = notices.store.push_notification
    failures = [1]

    async def flaky(rc_, user, notification):
        if failures:
            failures.pop()
            raise ConnectionError("redis went away")
        await real(rc_, user, notification)

    monkeypatch.setattr(notices.store, "push_notification", flaky)
    fields = notices.parse({"user": "michele", "category": "bible", "title": "Read Psalm 23", "once": "bible:1"})

    async def run():
        with pytest.raises(ConnectionError):
            await notices.deliver(rc, None, "michele", fields)
        assert not any(k.startswith("telemetry:notify_once") for k in rc.strings)
        retry = await notices.deliver(rc, None, "michele", fields)
        assert retry.get("duplicate") is None and sent["webpush"] == ["michele"]
        assert await notices.deliver(rc, None, "michele", fields) == {"duplicate": True}

    asyncio.run(run())
