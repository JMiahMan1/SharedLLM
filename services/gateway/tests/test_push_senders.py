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

    # Strings with a TTL, enough for SET NX EX cooldowns.
    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.s:
            return None
        self.s[key] = (value, ex)
        return True

    async def ttl(self, key):
        return self.s[key][1] if key in self.s else -2

    async def delete(self, key):
        self.s.pop(key, None)


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
    assert sent[0]["category"] == "app_updates" and sent[0]["title"] == "Jarvis app 2.4.1 (build 412) is available"
    assert sent[0]["body"] == "Faster maps" and sent[0]["data"]["url"] == "/settings"


async def test_ci_placeholder_notes_are_not_shown_as_the_update_body(sent):
    await notices.app_update_published(413, "2.4.1", "Android build 31ccc366", _Redis())
    assert sent[0]["body"] == "Open Settings to install it."


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
    """A fake identity for missions: GET, the conditional transition, DELETE."""
    store = {7: {"id": 7, "status": "pending", "user_id": None, "mission_type": "admin_fix"}}
    deleted, dispatched = [], []

    class Client:
        def get(self, url, **kw):
            mission = store.get(int(url.rsplit("/", 1)[-1]))
            return _Resp(200 if mission else 404, mission)

        def post(self, url, json=None, **kw):
            mission = store.get(int(url.rsplit("/", 2)[-2]))
            if mission is None:
                return _Resp(404, {"detail": "Mission not found"})
            if mission["status"] != json["from_status"]:
                return _Resp(409, {"detail": f"Mission #{mission['id']} is already {mission['status']}"})
            mission["status"] = json["to_status"]
            return _Resp(200, {"status": "SUCCESS"})

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


ADMIN = {"user": "jeremiah", "id": 1, "is_admin": True}


def test_the_raven_buttons_are_registered():
    assert {"raven_approve", "raven_reject"} <= set(push_callbacks.PUSH_CALLBACKS)


async def test_an_admin_approves_or_rejects_a_waiting_mission(missions):
    store, deleted, dispatched = missions
    assert (await raven_approval.approve(ADMIN, {"mission_id": 7}))["status"] == "SUCCESS"
    assert dispatched == [(7, "jeremiah")] and store[7]["status"] == "queued"
    store[8] = {"id": 8, "status": "pending", "user_id": 5}
    assert (await raven_approval.reject(ADMIN, {"mission_id": "8"}))["status"] == "SUCCESS"
    assert deleted == [8]


async def test_only_an_admin_may_act_even_on_their_own_mission(missions):
    """As Execute in Raven Ops: the owner of a mission is not enough."""
    store, deleted, dispatched = missions
    store[8] = {"id": 8, "status": "pending", "user_id": 5, "mission_type": "user"}
    leo = {"user": "leo", "id": 5, "is_admin": False}
    for act in (raven_approval.approve, raven_approval.reject):
        with pytest.raises(HTTPException) as e:
            await act(leo, {"mission_id": 8})
        assert e.value.status_code == 403
    assert deleted == [] and dispatched == [] and store[8]["status"] == "pending"


async def test_only_while_it_waits(missions):
    store, _, dispatched = missions
    store[7]["status"] = "queued"
    with pytest.raises(HTTPException) as e:
        await raven_approval.approve(ADMIN, {"mission_id": 7})
    assert e.value.status_code == 409
    assert dispatched == []


async def test_a_mission_waits_for_the_one_it_depends_on(missions):
    store, _, dispatched = missions
    store[7]["depends_on_mission_id"] = 6
    store[6] = {"id": 6, "status": "executing"}
    with pytest.raises(HTTPException) as e:
        await raven_approval.approve(ADMIN, {"mission_id": 7})
    assert e.value.status_code == 409 and "#6" in e.value.detail
    del store[6]
    with pytest.raises(HTTPException):
        await raven_approval.approve(ADMIN, {"mission_id": 7})
    assert dispatched == [] and store[7]["status"] == "pending"
    store[6] = {"id": 6, "status": "completed"}
    await raven_approval.approve(ADMIN, {"mission_id": 7})
    assert dispatched == [(7, "jeremiah")]


async def test_two_taps_enqueue_once(missions, monkeypatch):
    """Both taps read ``pending``; only the first claims it in identity."""
    import asyncio

    _, _, dispatched = missions
    real = raven_approval._waiting_mission

    async def both_see_it_waiting(creds, params):
        mission = dict(await real(creds, params))
        await asyncio.sleep(0)
        return mission

    monkeypatch.setattr(raven_approval, "_waiting_mission", both_see_it_waiting)
    results = await asyncio.gather(raven_approval.approve(ADMIN, {"mission_id": 7}),
                                   raven_approval.approve(ADMIN, {"mission_id": 7}),
                                   return_exceptions=True)

    assert dispatched == [(7, "jeremiah")]
    assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1


async def test_a_failed_dispatch_puts_the_mission_back(missions, monkeypatch):
    store, _, _ = missions
    from services.gateway import main

    async def broken(mission_id, creds):
        raise RuntimeError("queue down")

    monkeypatch.setattr(main, "dispatch_admin_mission", broken)
    with pytest.raises(RuntimeError):
        await raven_approval.approve(ADMIN, {"mission_id": 7})
    assert store[7]["status"] == "pending"


# ------------------------------------------------------------ family safety


@pytest.fixture
def safety_client(sent, monkeypatch):
    from contextlib import asynccontextmanager

    from services.gateway import main

    async def caller(request):
        return "leo"

    who = {"creds": {"user": "jeremiah", "id": 1, "is_admin": True}}

    async def identity(request, body=None):
        return who["creds"]

    redis = _Redis()

    @asynccontextmanager
    async def shared_redis(r=None):
        yield redis

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
    monkeypatch.setattr(main, "_resolve_identity_from_request", identity)
    monkeypatch.setattr(notices, "_redis", shared_redis)
    monkeypatch.setattr(family_safety, "get_client", lambda: Client())
    answers["who"] = who
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


def test_sos_and_on_my_way_wait_between_sends(safety_client, sent):
    """One SOS a minute and one On my way every five minutes per person, with
    a 429 the app shows as is."""
    client, _ = safety_client
    assert client.post("/api/family/sos", json={"confirm": True}).status_code == 200
    again = client.post("/api/family/sos", json={"confirm": True})
    assert again.status_code == 429 and again.headers["Retry-After"] == "60"
    assert again.json()["detail"] == "Your SOS went out less than a minute ago. You can send it again in 1 min."
    assert client.post("/api/family/on-my-way").status_code == 200
    way = client.post("/api/family/on-my-way")
    assert way.status_code == 429
    assert way.json()["detail"] == ("Your family was already told you are on the way. "
                                    "You can send it again in 5 min.")
    assert len(sent) == 4  # two people each, once
    assert family_safety._wait_text(45) == "45 s" and family_safety._wait_text(61) == "2 min"


def test_a_notice_nobody_got_can_be_sent_again_at_once(safety_client, monkeypatch):
    client, _ = safety_client

    async def refused(payload, **kw):
        return False

    monkeypatch.setattr(notify, "send", refused)
    assert client.post("/api/family/sos", json={"confirm": True}).json()["sent"] == 0
    assert client.post("/api/family/sos", json={"confirm": True}).status_code == 200


def test_an_sos_still_goes_out_when_redis_is_down(safety_client, sent, monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def down(r=None):
        raise ConnectionError("redis down")
        yield

    monkeypatch.setattr(notices, "_redis", down)
    client, _ = safety_client
    for _ in range(2):
        assert client.post("/api/family/sos", json={"confirm": True}).status_code == 200
    assert len(sent) == 4


def test_marking_a_place_important_goes_to_geo(safety_client):
    client, answers = safety_client
    resp = client.put("/api/geo/places/zone.school", json={"important": True, "notify": ["Michele", "leo", "leo"]})
    assert resp.status_code == 200
    url, payload = answers["put"]
    assert url.endswith("/place-settings/zone.school")
    assert payload == {"important": True, "notify": ["michele", "leo"]}


@pytest.mark.parametrize("body", [
    [True],
    {"notify": []},
    {"important": "yes"},
    {"important": 1},
    {"important": True, "notify": "michele"},
    {"important": True, "notify": ["michele", 3]},
    {"important": True, "notify": ["stranger"]},
])
def test_place_settings_are_validated(safety_client, body):
    client, answers = safety_client
    assert client.put("/api/geo/places/zone.school", json=body).status_code == 422
    assert "put" not in answers


def test_only_an_admin_changes_a_place(safety_client):
    client, answers = safety_client
    answers["who"]["creds"] = {"user": "leo", "id": 5, "is_admin": False}
    resp = client.put("/api/geo/places/zone.school", json={"important": True})
    assert resp.status_code == 403
    assert "put" not in answers


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
