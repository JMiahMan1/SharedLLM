"""The gateway's phone notices: chat, jobs, Raven, app updates and the family
safety buttons (docs/PUSH.md, Senders)."""
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.common import notify
from services.gateway import chat_notify, family_safety, notices, push_callbacks, raven_approval


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


class _Redis:
    def __init__(self):
        self.h, self.s = {}, {}

    async def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value

    async def hdel(self, key, field):
        self.h.get(key, {}).pop(field, None)

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def sadd(self, key, value):
        members = self.s.setdefault(key, set())
        if value in members:
            return 0
        members.add(value)
        return 1


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def send(payload, **kw):
        out.append(payload)
        return True

    async def family(**kw):
        return ["jeremiah", "leo", "michele"]

    monkeypatch.setattr(notify, "send", send)
    monkeypatch.setattr(notify, "family", family)
    return out


# --------------------------------------------------------------------- chat


def test_a_chat_notice_has_its_category_tag_and_buttons():
    conv = {"token": "fam", "display_name": "Summers Family", "type": 2, "last_message_id": 7,
            "last_message": "Dinner at 6", "last_message_actor": "Michele", "last_message_actor_id": "michele",
            "unread_messages": 1}
    notice = chat_notify.notice_for(conv, "jeremiah", "jeremiah")
    assert notice["user"] == "jeremiah" and notice["category"] == "chat" and notice["kind"] == "chat"
    assert notice["tag"] == "chat:fam" and notice["once"] == "chat:fam:7"
    assert notice["actions"] == [
        {"label": "Reply", "type": "reply", "callback": "chat_reply", "params": {"token": "fam"}},
        {"label": "Open", "type": "view", "url": "/family?chat=fam"},
    ]


# -------------------------------------------------------------------- songs


async def test_a_finished_song_is_announced_once_and_forgotten(sent, monkeypatch):
    monkeypatch.setattr(notices, "ALPACA_AUDIO_URL", "http://audio")
    r = _Redis()
    await notices.song_started("aaa111", "leo", "a song about dogs", r)
    await notices.song_started("bbb222", "leo", "still going", r)
    await notices.song_started("ccc333", "leo", "broken", r)
    states = {"aaa111": "done", "bbb222": "running", "ccc333": "error"}

    class Client:
        def get(self, url, **kw):
            return _Resp(200, {"id": url.rsplit("/", 1)[-1], "status": states[url.rsplit("/", 1)[-1]]})

    assert await notices.check_songs_once(r, Client()) == 1
    notice = sent[0]
    assert notice["user"] == "leo" and notice["category"] == "jobs"
    assert "expires in 30 minutes" in notice["body"]
    assert notice["data"]["url"] == "/family?tab=create" and notice["once"] == "song:aaa111"
    assert list(r.h[notices.PENDING_SONGS_KEY]) == ["bbb222"], "done and failed songs are dropped"


async def test_a_song_waits_while_telemetry_or_the_audio_server_is_down(monkeypatch):
    monkeypatch.setattr(notices, "ALPACA_AUDIO_URL", "http://audio")
    r = _Redis()
    await notices.song_started("aaa111", "leo", "dogs", r)

    async def refused(payload, **kw):
        return False

    monkeypatch.setattr(notify, "send", refused)

    class Done:
        def get(self, url, **kw):
            return _Resp(200, {"status": "done"})

    class Down:
        def get(self, url, **kw):
            return _Resp(502, None)

    assert await notices.check_songs_once(r, Done()) == 0
    assert await notices.check_songs_once(r, Down()) == 0
    assert "aaa111" in r.h[notices.PENDING_SONGS_KEY]


# ----------------------------------------------------------------- pictures


async def test_an_improved_picture_tells_who_asked(sent):
    assert await notices.picture_ready("leo")
    assert sent[0]["user"] == "leo" and sent[0]["category"] == "jobs"
    assert sent[0]["actions"] == [{"label": "Open", "type": "view", "url": "/family?tab=create"}]


# -------------------------------------------------------------- app updates


async def test_a_new_apk_is_announced_to_everyone_once_per_build(sent):
    r = _Redis()
    assert await notices.app_update_published(412, "2.4.1", "Faster maps", r) == 3
    assert await notices.app_update_published(412, "2.4.1", "Faster maps", r) == 0
    assert {n["user"] for n in sent} == {"jeremiah", "leo", "michele"}
    assert sent[0]["category"] == "app_updates" and sent[0]["title"] == "Jarvis app 2.4.1 is available"
    assert sent[0]["body"] == "Faster maps" and sent[0]["data"]["url"] == "/settings"


# -------------------------------------------------------------------- raven


async def test_a_finished_mission_tells_its_owner_with_a_summary(sent):
    await notices.mission_finished("michele", 42, "completed", "Built the site.\n\nAll tests pass.", "ws1")
    notice = sent[0]
    assert notice["category"] == "raven" and notice["title"] == "Mission #42 finished"
    assert notice["body"] == "Built the site. All tests pass."
    assert notice["data"]["url"] == "/workspaces?select=ws1"
    assert not await notices.mission_finished("", 43, "failed", "x", None)


async def test_a_mission_waiting_for_approval_goes_to_admins_with_both_buttons(sent):
    await notices.mission_needs_approval({"id": 9, "target_container": "geo", "error_summary": "Detected 12 errors."})
    notice = sent[0]
    assert notice["user"] == "@admins" and notice["category"] == "raven"
    assert [a.get("callback") for a in notice["actions"][:2]] == ["raven_approve", "raven_reject"]
    assert notice["actions"][0]["params"] == {"mission_id": 9}


def test_mission_notices_skip_the_system_account():
    from services.gateway.background_worker import _mission_owner

    assert _mission_owner({"creds": {"user": "michele"}}) == "michele"
    assert _mission_owner({"creds": {"user": "default"}}) == ""
    assert _mission_owner({}) == ""


@pytest.fixture
def missions(monkeypatch):
    store = {7: {"id": 7, "status": "pending", "user_id": None, "mission_type": "admin_fix"}}
    deleted, dispatched = [], []

    class Client:
        def get(self, url, **kw):
            mission = store.get(int(url.rsplit("/", 1)[-1]))
            return _Resp(200 if mission else 404, mission)

        def delete(self, url, **kw):
            deleted.append(int(url.rsplit("/", 1)[-1]))
            return _Resp(200, {})

    async def dispatch(mission_id, creds):
        dispatched.append((mission_id, creds["user"]))
        return {"status": "SUCCESS", "message": "Mission dispatched."}

    from services.gateway import main

    monkeypatch.setattr(raven_approval, "get_client", lambda: Client())
    monkeypatch.setattr(main, "dispatch_admin_mission", dispatch)
    return store, deleted, dispatched


def test_the_raven_buttons_are_registered():
    assert {"raven_approve", "raven_reject"} <= set(push_callbacks.PUSH_CALLBACKS)


async def test_an_admin_approves_or_rejects_a_waiting_mission(missions):
    _, deleted, dispatched = missions
    admin = {"user": "jeremiah", "id": 1, "is_admin": True}
    assert (await raven_approval.approve(admin, {"mission_id": 7}))["status"] == "SUCCESS"
    assert dispatched == [(7, "jeremiah")]
    assert (await raven_approval.reject(admin, {"mission_id": "7"}))["status"] == "SUCCESS"
    assert deleted == [7]


async def test_only_an_admin_or_the_owner_may_act_and_only_while_it_waits(missions):
    store, deleted, dispatched = missions
    with pytest.raises(HTTPException) as e:
        await raven_approval.approve({"user": "leo", "id": 5, "is_admin": False}, {"mission_id": 7})
    assert e.value.status_code == 403
    store[8] = {"id": 8, "status": "pending", "user_id": 5}
    await raven_approval.reject({"user": "leo", "id": 5, "is_admin": False}, {"mission_id": 8})
    assert deleted == [8]
    store[7]["status"] = "queued"
    with pytest.raises(HTTPException) as e:
        await raven_approval.approve({"user": "jeremiah", "id": 1, "is_admin": True}, {"mission_id": 7})
    assert e.value.status_code == 409
    assert dispatched == []


# ------------------------------------------------------------ family safety


@pytest.fixture
def safety_client(sent, monkeypatch):
    from services.gateway import main

    async def caller(request):
        return "leo"

    answers = {}

    class Client:
        def get(self, url, **kw):
            for part, body in answers.items():
                if part in url:
                    return _Resp(200, body)
            return _Resp(404, None)

        def put(self, url, json=None, **kw):
            answers["put"] = (url, json)
            return _Resp(200, {"zone": "zone.school", **json})

    monkeypatch.setattr(main, "_require_authenticated", caller)
    monkeypatch.setattr(family_safety, "get_client", lambda: Client())
    return TestClient(main.app), answers


def test_on_my_way_tells_everyone_else_with_the_drive_time(safety_client, sent):
    client, answers = safety_client
    answers["/eta"] = {"arrived": False, "duration_s": 1260, "eta": 1}
    body = client.post("/api/family/on-my-way").json()
    assert body["sent"] == 2 and body["eta_s"] == 1260
    assert {n["user"] for n in sent} == {"jeremiah", "michele"}
    assert sent[0]["category"] == "family_safety" and sent[0]["body"] == "Home in about 21 min by road."


def test_on_my_way_without_a_position_still_goes_out(safety_client, sent):
    client, _ = safety_client
    assert client.post("/api/family/on-my-way").json()["sent"] == 2
    assert sent[0]["title"] == "Leo is on the way home" and sent[0]["body"] == "No drive time yet."


def test_sos_must_be_confirmed(safety_client, sent):
    client, _ = safety_client
    assert client.post("/api/family/sos", json={}).status_code == 422
    assert client.post("/api/family/sos", json={"confirm": "yes"}).status_code == 422
    assert sent == []


def test_sos_is_urgent_and_carries_the_last_position(safety_client, sent):
    import time

    client, answers = safety_client
    answers["/location"] = {"latitude": 33.4484, "longitude": -112.074, "timestamp": time.time() - 120}
    body = client.post("/api/family/sos", json={"confirm": True}).json()
    assert body == {"status": "SUCCESS", "sent": 2, "family": 2, "located": True}
    notice = sent[0]
    assert notice["priority"] == 5 and notice["category"] == "family_safety"
    assert notice["title"] == "SOS from Leo"
    assert "Last seen 2 min ago at 33.44840, -112.07400" in notice["body"]
    assert notice["actions"][1]["url"].startswith("https://www.openstreetmap.org/?mlat=33.44840")


def test_sos_text_without_a_position_or_with_an_old_one():
    assert family_safety.sos_text("Leo", None, 1000.0) == ("SOS from Leo", "Leo needs help. No position is known.", None)
    _, body, _ = family_safety.sos_text("Leo", {"latitude": 1, "longitude": 2, "timestamp": 0}, 7200.0)
    assert "2 h ago (may be out of date)" in body


def test_marking_a_place_important_goes_to_geo(safety_client):
    client, answers = safety_client
    resp = client.put("/api/geo/places/zone.school", json={"important": True, "notify": ["michele", 3]})
    assert resp.status_code == 200
    url, payload = answers["put"]
    assert url.endswith("/place-settings/zone.school")
    assert payload == {"important": True, "notify": ["michele"]}


def test_notice_builder_caps_actions_and_priority():
    n = notify.notice("leo", "jobs", "t", priority=9, actions=[{}] * 5)
    assert n["priority"] == 5 and len(n["actions"]) == 3
    assert json.dumps(n)


# ------------------------------------------------------- gateway route hooks


def test_an_improved_drawing_notifies_the_person_who_asked(monkeypatch):
    from contextlib import asynccontextmanager

    from services.gateway import main

    class Client:
        async def post(self, url, **kw):
            body = {"status": "SUCCESS", "detail": {"b64_json": "AAAA", "model": "m"}} if "image_edit_inline" in url else {}
            return type("R", (), {"status": 200, "json": staticmethod(lambda: _async(body))})()

    async def _async(value):
        return value

    @asynccontextmanager
    async def shared():
        yield Client()

    told = []

    async def user(request):
        return "leo"

    async def picture_ready(name):
        told.append(name)
        return True

    monkeypatch.setattr(main, "shared_http_client", shared)
    monkeypatch.setattr(main, "_sd_request_authorized", lambda request: _async(True))
    monkeypatch.setattr(main, "_user_id_from_request", user)
    monkeypatch.setattr(notices, "picture_ready", picture_ready)
    resp = TestClient(main.app).post("/api/images/edit", json={"prompt": "better", "image": "data:image/png;base64,AA=="})
    assert resp.json()["status"] == "SUCCESS"
    assert told == ["leo"]
