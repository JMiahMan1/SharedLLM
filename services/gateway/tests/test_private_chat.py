"""The parents' private chat: who may be in it, what the server keeps, and what it never says.

The server holds only public key-derivation parameters, a verifier and
encrypted pictures; these tests check it enforces membership itself (not only
the app), refuses minors and other relatives, serves pictures to members only,
keeps every Talk write in the room encrypted, rate-limits nudges, and puts no
content in any notice.
"""
import base64
import json

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.common import notify
from services.gateway import chat_notify, private_chat, push_callbacks
from services.gateway import main as gateway_main

PEOPLE = {
    "alex": {"username": "alex", "display_name": "Alex", "family_role": "father", "is_minor": False, "nextcloud_user": "alex"},
    "sam": {"username": "sam", "display_name": "Sam", "family_role": "mother", "is_minor": False, "nextcloud_user": "sam"},
    "kid": {"username": "kid", "display_name": "Kid", "family_role": "child", "is_minor": True, "nextcloud_user": "kid"},
    "teen": {"username": "teen", "display_name": "Teen", "family_role": "teen", "is_minor": True, "nextcloud_user": "teen"},
    "gran": {"username": "gran", "display_name": "Gran", "family_role": "other", "is_minor": False, "nextcloud_user": "gran"},
    "uncle": {"username": "uncle", "display_name": "Uncle", "family_role": "adult_child", "is_minor": False, "nextcloud_user": "uncle"},
    "nochat": {"username": "nochat", "display_name": "No chat", "family_role": "mother", "is_minor": False},
}

SEALED = '\U0001f512 Encrypted message\n\n```jarvis-envelope\n{"kind":"sealed","title":"Encrypted message","v":1,"iv":"AAAA","ct":"BBBB"}\n```'


def _b64(n: int) -> str:
    return base64.b64encode(bytes(range(n))).decode()


def _params(**over):
    body = {"salt": _b64(16), "iterations": 600_000, "verifier": {"iv": _b64(12), "ct": _b64(40)}}
    body.update(over)
    return body


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def ttl(self, key):
        return 240 if key in self.kv else -2

    def delete(self, key):
        self.kv.pop(key, None)


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls: list[dict] = []
    notices: list[dict] = []
    state = {"conversation": {"last_message_id": 7, "last_common_read": 5, "last_message_actor_id": "alex"},
             "deliver": True}

    async def identity(request, body=None):
        return {"user": request.headers.get("X-Test-User", "alex")}

    async def people():
        return dict(PEOPLE)

    async def talk(request, payload):
        calls.append(payload)
        action = payload.get("action")
        if action == "create_group":
            return 200, {"status": "SUCCESS", "detail": {"conversation": {"token": "room123abc"}, "not_added": []}}
        if action == "open":
            return 200, {"status": "SUCCESS", "detail": {"conversation": state["conversation"]}}
        if action == "delete_conversation":
            return 200, {"status": "SUCCESS", "message": "Conversation deleted.", "detail": {"deleted": True}}
        return 200, {"status": "SUCCESS", "detail": {}}

    async def send_many(users, make, **kw):
        if not state["deliver"]:
            return 0
        for user in users:
            notices.append(make(user))
        return len(users)

    redis = _Redis()
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", identity)
    monkeypatch.setattr(private_chat, "_people", people)
    monkeypatch.setattr(private_chat, "_talk", talk)
    monkeypatch.setattr(private_chat, "_store", private_chat.Store(tmp_path))
    monkeypatch.setattr(notify, "send_many", send_many)
    import services.gateway.cache as gateway_cache

    monkeypatch.setattr(gateway_cache, "get_redis", lambda: redis)
    client = TestClient(gateway_main.app, raise_server_exceptions=False)
    return {"client": client, "calls": calls, "notices": notices, "state": state, "redis": redis, "root": tmp_path}


def _as(user: str) -> dict:
    return {"X-Test-User": user}


def _create(env, user="alex", partner="sam", **over):
    return env["client"].post("/api/private-chat/chats", json={"partner": partner, **_params(**over)}, headers=_as(user))


# --- Who may be in one --------------------------------------------------------


def test_a_parent_starts_one_with_the_other_parent_and_the_server_keeps_no_key(env):
    resp = _create(env)
    assert resp.status_code == 200, resp.text
    chat = resp.json()
    assert chat["token"] == "room123abc"
    assert chat["members"] == ["alex", "sam"]
    assert chat["kdf"] == {"name": "PBKDF2-SHA256", "salt": _b64(16), "iterations": 600_000}
    create = next(c for c in env["calls"] if c["action"] == "create_group")
    assert create["participants"] == ["sam"]
    saved = json.loads((env["root"] / "chats" / "room123abc.json").read_text())
    assert set(saved) == {"token", "members", "created_by", "created_at", "salt", "iterations", "verifier"}
    # The other parent sees it too, with the same parameters to derive the key.
    other = env["client"].get("/api/private-chat/chats/room123abc", headers=_as("sam")).json()
    assert other["verifier"] == chat["verifier"] and other["partner"] == "alex"


@pytest.mark.parametrize("caller", ["kid", "teen", "gran", "uncle", "nochat"])
def test_nobody_but_a_parent_may_start_one(env, caller):
    resp = _create(env, user=caller)
    assert resp.status_code == 403
    assert not any(c["action"] == "create_group" for c in env["calls"])


@pytest.mark.parametrize("partner", ["kid", "teen", "gran", "uncle", "nochat", "stranger"])
def test_and_only_with_the_other_parent(env, partner):
    resp = _create(env, partner=partner)
    assert resp.status_code == 403
    assert not any(c["action"] == "create_group" for c in env["calls"])


def test_not_with_yourself_and_only_once_per_pair(env):
    assert _create(env, partner="alex").status_code == 422
    assert _create(env).status_code == 200
    again = _create(env, user="sam", partner="alex")
    assert again.status_code == 409 and again.json()["token"] == "room123abc"


@pytest.mark.parametrize("over", [
    {"iterations": 100_000},
    {"iterations": "600000"},
    {"salt": _b64(8)},
    {"salt": "not base64!"},
    {"verifier": {"iv": _b64(16), "ct": _b64(40)}},
    {"verifier": {"iv": _b64(12), "ct": _b64(8)}},
])
def test_weak_or_malformed_key_parameters_are_refused(env, over):
    assert _create(env, **over).status_code == 422


def test_status_says_who_may_use_it(env):
    _create(env)
    parent = env["client"].get("/api/private-chat/status", headers=_as("sam")).json()
    assert parent["eligible"] is True
    assert [c["token"] for c in parent["chats"]] == ["room123abc"]
    assert [p["username"] for p in parent["partners"]] == ["alex"]
    kid = env["client"].get("/api/private-chat/status", headers=_as("kid")).json()
    assert kid["eligible"] is False and kid["chats"] == [] and kid["partners"] == [] and kid["reason"]


def test_a_member_who_stops_being_a_parent_loses_access(env, monkeypatch):
    _create(env)

    async def changed():
        return {**PEOPLE, "sam": {**PEOPLE["sam"], "family_role": "other"}}

    monkeypatch.setattr(private_chat, "_people", changed)
    assert env["client"].get("/api/private-chat/chats/room123abc", headers=_as("sam")).status_code == 403


def test_identity_down_is_an_error_not_a_guess(env, monkeypatch):
    from fastapi import HTTPException

    async def down():
        raise HTTPException(status_code=503, detail="Could not check who is in the family; try again.")

    monkeypatch.setattr(private_chat, "_people", down)
    assert env["client"].get("/api/private-chat/status").status_code == 503


# --- Pictures -----------------------------------------------------------------


def test_pictures_are_kept_and_served_only_to_members(env):
    _create(env)
    blob = bytes(range(256)) * 4
    up = env["client"].post("/api/private-chat/chats/room123abc/blobs", content=blob, headers=_as("alex"))
    assert up.status_code == 200, up.text
    blob_id = up.json()["id"]
    got = env["client"].get(f"/api/private-chat/chats/room123abc/blobs/{blob_id}", headers=_as("sam"))
    assert got.status_code == 200 and got.content == blob
    assert got.headers["cache-control"] == "no-store"
    assert got.headers["content-type"] == "application/octet-stream"
    for outsider in ("kid", "gran", "uncle"):
        assert env["client"].get(f"/api/private-chat/chats/room123abc/blobs/{blob_id}", headers=_as(outsider)).status_code == 404
        assert env["client"].post("/api/private-chat/chats/room123abc/blobs", content=blob, headers=_as(outsider)).status_code == 404
        assert env["client"].delete(f"/api/private-chat/chats/room123abc/blobs/{blob_id}", headers=_as(outsider)).status_code == 404
    assert env["client"].delete(f"/api/private-chat/chats/room123abc/blobs/{blob_id}", headers=_as("sam")).status_code == 200
    assert env["client"].get(f"/api/private-chat/chats/room123abc/blobs/{blob_id}", headers=_as("alex")).status_code == 404


def test_a_picture_that_cannot_be_ciphertext_or_is_too_big_is_refused(env, monkeypatch):
    _create(env)
    assert env["client"].post("/api/private-chat/chats/room123abc/blobs", content=b"x" * 20, headers=_as("alex")).status_code == 422
    monkeypatch.setattr(private_chat, "MAX_BLOB_BYTES", 100)
    assert env["client"].post("/api/private-chat/chats/room123abc/blobs", content=b"x" * 101, headers=_as("alex")).status_code == 413


def test_bad_ids_never_reach_the_file_system(env):
    _create(env)
    assert env["client"].get("/api/private-chat/chats/room123abc/blobs/..%2F..%2Fchats", headers=_as("alex")).status_code == 404
    assert env["client"].get("/api/private-chat/chats/..%2Fx/blobs/" + "a" * 32, headers=_as("alex")).status_code == 404


def test_deleting_the_chat_takes_its_pictures(env):
    _create(env)
    blob_id = env["client"].post("/api/private-chat/chats/room123abc/blobs", content=b"y" * 64, headers=_as("alex")).json()["id"]
    assert env["client"].delete("/api/private-chat/chats/room123abc", headers=_as("kid")).status_code == 404
    assert env["client"].delete("/api/private-chat/chats/room123abc", headers=_as("sam")).status_code == 200
    assert not (env["root"] / "chats" / "room123abc.json").exists()
    assert not (env["root"] / "blobs" / "room123abc" / blob_id).exists()


# --- Talk writes in a private room ---------------------------------------------


@pytest.fixture
def talk_routes(env, monkeypatch):
    sent: list[dict] = []

    async def proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        sent.append(payload or {})
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {"deleted": True}})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    _create(env)
    env["sent"] = sent
    return env


@pytest.mark.parametrize("path,body", [
    ("/api/communication/talk/messages", {"message": "meet at 6"}),
    ("/api/communication/talk/messages/edit", {"message": "meet at 7", "message_id": 3}),
    ("/api/communication/talk/file", {"file_base64": "AAAA", "mime_type": "image/png", "file_name": "a.png"}),
    ("/api/communication/talk/voice", {"audio_base64": "AAAA"}),
    ("/api/communication/talk/react", {"message_id": 3, "reaction": "x"}),
    ("/api/communication/talk/game", {"game_command": "start"}),
    ("/api/communication/talk/card", {"card_kind": "activity", "card_title": "x"}),
    ("/api/communication/talk/polls/create", {"question": "q", "options": ["a", "b"]}),
    ("/api/communication/talk/polls/vote", {"poll_id": 1, "option_id": 0}),
    ("/api/communication/talk/participants", {"user": "kid"}),
])
def test_nothing_readable_goes_into_a_private_room(talk_routes, path, body):
    resp = talk_routes["client"].post(path, json={"token": "room123abc", **body})
    assert resp.status_code == 403, resp.text
    assert talk_routes["sent"] == []


def test_removing_a_member_is_refused_too(talk_routes):
    resp = talk_routes["client"].delete("/api/communication/talk/participants?token=room123abc&attendee_id=4")
    assert resp.status_code == 403 and talk_routes["sent"] == []


def test_an_encrypted_message_goes_through_and_other_rooms_are_untouched(talk_routes):
    c = talk_routes["client"]
    assert c.post("/api/communication/talk/messages", json={"token": "room123abc", "message": SEALED}).status_code == 200
    assert c.post("/api/communication/talk/messages", json={"token": "family1", "message": "meet at 6"}).status_code == 200
    assert c.post("/api/communication/talk/file", json={"token": "family1", "file_base64": "AAAA"}).status_code == 200
    assert [p["token"] for p in talk_routes["sent"]] == ["room123abc", "family1", "family1"]


def test_a_notification_reply_cannot_post_into_a_private_chat(talk_routes):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as err:
        import asyncio

        asyncio.run(push_callbacks.chat_reply(None, {"token": "room123abc"}, "on my way"))
    assert err.value.status_code == 403 and talk_routes["sent"] == []


def test_the_conversation_list_hides_the_last_message(talk_routes, monkeypatch):
    async def proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {"conversations": [
            {"token": "room123abc", "last_message": SEALED, "description": "x"},
            {"token": "family1", "last_message": "dinner at 6"},
        ]}})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    convs = talk_routes["client"].get("/api/communication/talk/conversations").json()["detail"]["conversations"]
    assert convs[0]["private"] is True and convs[0]["last_message"] == "Encrypted message"
    assert convs[0]["description"] is None
    assert "private" not in convs[1] and convs[1]["last_message"] == "dinner at 6"


# --- Nudges -------------------------------------------------------------------


def test_a_nudge_says_who_and_nothing_else(env):
    _create(env)
    resp = env["client"].post("/api/private-chat/chats/room123abc/nudge", headers=_as("alex"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["nudged"] == ["sam"]
    (notice,) = env["notices"]
    assert notice["user"] == "sam"
    assert notice["title"] == "Private chat"
    assert notice["body"] == "Alex nudged you in your private chat"
    assert notice["data"]["url"] == "/family?chat=room123abc"
    assert not any(a.get("type") == "reply" for a in notice.get("actions", []))


def test_nudges_are_rate_limited_per_chat_per_sender(env):
    _create(env)
    c = env["client"]
    assert c.post("/api/private-chat/chats/room123abc/nudge", headers=_as("alex")).status_code == 200
    again = c.post("/api/private-chat/chats/room123abc/nudge", headers=_as("alex"))
    assert again.status_code == 429
    assert again.headers["retry-after"] == "240" and again.json()["retry_after"] == 240
    assert len(env["notices"]) == 1


def test_a_failed_nudge_does_not_use_up_the_limit(env):
    _create(env)
    env["state"]["deliver"] = False
    assert env["client"].post("/api/private-chat/chats/room123abc/nudge", headers=_as("alex")).status_code == 502
    env["state"]["deliver"] = True
    assert env["client"].post("/api/private-chat/chats/room123abc/nudge", headers=_as("alex")).status_code == 200


@pytest.mark.parametrize("conversation", [
    {"last_message_id": 7, "last_common_read": 7, "last_message_actor_id": "alex"},  # already read
    {"last_message_id": 7, "last_common_read": 5, "last_message_actor_id": "sam"},  # theirs, not mine
    {"last_message_id": 0, "last_common_read": 0, "last_message_actor_id": ""},  # empty chat
])
def test_only_while_my_latest_message_is_unread(env, conversation):
    _create(env)
    env["state"]["conversation"] = conversation
    assert env["client"].post("/api/private-chat/chats/room123abc/nudge", headers=_as("alex")).status_code == 409
    assert env["notices"] == []


@pytest.mark.parametrize("outsider", ["kid", "gran", "uncle"])
def test_only_members_may_nudge(env, outsider):
    _create(env)
    assert env["client"].post("/api/private-chat/chats/room123abc/nudge", headers=_as(outsider)).status_code == 404
    assert env["notices"] == []


# --- Message notices ------------------------------------------------------------


def _conv(**over):
    conv = {
        "token": "room123abc", "display_name": "Private chat", "type": 2,
        "last_message": SEALED, "last_message_id": 9, "last_message_actor": "Sam",
        "last_message_actor_id": "sam", "unread_messages": 1,
    }
    conv.update(over)
    return conv


@pytest.mark.parametrize("private,message", [(True, "meet at 6"), (False, SEALED), (True, SEALED)])
def test_a_private_message_notice_has_no_content_and_no_reply(private, message):
    notice = chat_notify.notice_for(_conv(last_message=message), "alex", "alex", private=private)
    assert notice["title"] == "Private chat"
    assert notice["body"] == "New private message from Sam"
    text = json.dumps(notice)
    assert "meet at 6" not in text and "BBBB" not in text and "jarvis-envelope" not in text
    assert [a["type"] for a in notice["actions"]] == ["view"]


def test_my_own_private_message_is_not_announced_to_me():
    assert chat_notify.notice_for(_conv(last_message_actor_id="alex"), "alex", "alex", private=True) is None
