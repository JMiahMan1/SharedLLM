"""The parents' private chat: who may be in it, what the server keeps, and what it never says.

The server holds only public key-derivation parameters, a wrapped key, a
verifier, a hash of the password proof and encrypted pictures; these tests
check it enforces membership itself (not only the app), refuses minors and
other relatives, serves pictures to members only, keeps every Talk write in the
room encrypted, rate-limits nudges and password changes, keeps the old key
records for a rollback, and puts no content in any notice.
"""
import base64
import hashlib
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


def _fill(n: int, byte: int) -> str:
    return base64.b64encode(bytes([byte]) * n).decode()


VERIFIER = {"iv": _b64(12), "ct": _b64(47)}
AUTH_1 = _fill(32, 1)


def _params(**over):
    """A v2 key record as a device makes it."""
    body = {
        "kdf": {"salt": _b64(16), "iterations": 600_000},
        "wrapped_key": {"iv": _fill(12, 7), "ct": _fill(48, 7)},
        "verifier": dict(VERIFIER),
        "auth": AUTH_1,
    }
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
    assert chat["version"] == 2
    assert chat["kdf"] == {"name": "PBKDF2-SHA256", "salt": _b64(16), "iterations": 600_000}
    assert chat["wrapped_key"] == {"iv": _fill(12, 7), "ct": _fill(48, 7)}
    assert "auth_hash" not in chat and "auth" not in chat and "history" not in chat
    create = next(c for c in env["calls"] if c["action"] == "create_group")
    assert create["participants"] == ["sam"]
    saved = json.loads((env["root"] / "chats" / "room123abc.json").read_text())
    assert set(saved) == {"token", "members", "created_by", "created_at", "version", "kdf", "wrapped_key",
                          "verifier", "auth_hash"}
    # Only a hash of the password proof is kept, never the proof itself.
    assert saved["auth_hash"] == hashlib.sha256(bytes([1]) * 32).hexdigest()
    assert AUTH_1 not in json.dumps(saved)
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
    {"kdf": {"salt": _b64(16), "iterations": 100_000}},
    {"kdf": {"salt": _b64(16), "iterations": "600000"}},
    {"kdf": {"salt": _b64(16), "iterations": 20_000_000}},
    {"kdf": {"salt": _b64(8), "iterations": 600_000}},
    {"kdf": {"salt": "not base64!", "iterations": 600_000}},
    {"kdf": None},
    {"wrapped_key": {"iv": _b64(12), "ct": _b64(32)}},
    {"wrapped_key": {"iv": _b64(16), "ct": _b64(48)}},
    {"wrapped_key": None},
    {"verifier": {"iv": _b64(16), "ct": _b64(40)}},
    {"verifier": {"iv": _b64(12), "ct": _b64(8)}},
    {"auth": None},
    {"auth": _b64(16)},
    # A v1 body from an old app: the server only makes v2 chats now.
    {"kdf": None, "wrapped_key": None, "auth": None, "salt": _b64(16), "iterations": 600_000},
])
def test_weak_or_malformed_key_parameters_are_refused(env, over):
    assert _create(env, **over).status_code == 422


def test_status_says_who_may_use_it(env):
    _create(env)
    parent = env["client"].get("/api/private-chat/status", headers=_as("sam")).json()
    assert parent["eligible"] is True
    assert [c["token"] for c in parent["chats"]] == ["room123abc"]
    assert [p["username"] for p in parent["partners"]] == ["alex"]


@pytest.mark.parametrize("caller", ["kid", "teen", "gran", "uncle", "nochat"])
def test_anyone_else_gets_exactly_the_empty_answer(env, caller):
    _create(env)
    resp = env["client"].get("/api/private-chat/status", headers=_as(caller))
    assert resp.status_code == 200
    # Nothing that says the feature exists: no reason, no partners, no parameters.
    assert resp.json() == {"eligible": False, "chats": [], "partners": []}


@pytest.mark.parametrize("caller", ["kid", "teen", "gran", "uncle"])
def test_what_an_outsider_can_reach_names_nothing(env, caller):
    _create(env)
    c = env["client"]
    answers = [
        _create(env, user=caller),
        c.get("/api/private-chat/chats/room123abc", headers=_as(caller)),
        c.delete("/api/private-chat/chats/room123abc", headers=_as(caller)),
        c.post("/api/private-chat/chats/room123abc/nudge", headers=_as(caller)),
        c.post("/api/private-chat/chats/room123abc/password", json={}, headers=_as(caller)),
        c.post("/api/private-chat/chats/room123abc/blobs", content=b"x" * 64, headers=_as(caller)),
        c.get("/api/private-chat/chats/nosuchroom/blobs/" + "a" * 32, headers=_as(caller)),
    ]
    for resp in answers:
        assert resp.status_code in (403, 404), resp.text
        assert resp.json()["detail"] == "Not available.", resp.text
        assert "private" not in resp.text.lower()


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
    assert resp.json()["detail"] == "Not available."
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
    assert {n["user"] for n in env["notices"]} <= {"alex", "sam"}
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


# --- Changing the password ------------------------------------------------------


V1_SALT = _fill(16, 3)


def _seed_v1(env) -> dict:
    """A chat as the first version made it (the one already in use): no wrapped key, no auth."""
    chat = {"token": "room123abc", "members": ["alex", "sam"], "created_by": "alex", "created_at": 1_700_000_000,
            "salt": V1_SALT, "iterations": 600_000, "verifier": dict(VERIFIER)}
    private_chat.get_store().save(chat)
    return chat


def _change(env, user="alex", n=2, auth=None, **over):
    body = {
        "kdf": {"salt": _fill(16, 10 + n), "iterations": 600_000},
        "wrapped_key": {"iv": _fill(12, 10 + n), "ct": _fill(48, 10 + n)},
        "verifier": dict(VERIFIER),
        "new_auth": _fill(32, n),
    }
    if auth is not None:
        body["auth"] = auth
    body.update(over)
    return env["client"].post("/api/private-chat/chats/room123abc/password", json=body, headers=_as(user))


def _saved(env) -> dict:
    return json.loads((env["root"] / "chats" / "room123abc.json").read_text())


def test_a_v1_chat_is_shown_as_v1(env):
    _seed_v1(env)
    chat = env["client"].get("/api/private-chat/chats/room123abc", headers=_as("sam")).json()
    assert chat["version"] == 1
    assert chat["kdf"] == {"name": "PBKDF2-SHA256", "salt": V1_SALT, "iterations": 600_000}
    assert "wrapped_key" not in chat


def test_a_v1_chat_takes_a_change_from_a_member_with_the_same_verifier_and_becomes_v2(env):
    _seed_v1(env)
    resp = _change(env, user="sam")
    assert resp.status_code == 200, resp.text
    chat = resp.json()
    assert chat["version"] == 2 and chat["verifier"] == VERIFIER
    assert chat["kdf"] == {"name": "PBKDF2-SHA256", "salt": _fill(16, 12), "iterations": 600_000}
    assert chat["notified"] == ["alex"]
    saved = _saved(env)
    assert saved["version"] == 2 and "salt" not in saved and "iterations" not in saved
    assert saved["auth_hash"] == hashlib.sha256(bytes([2]) * 32).hexdigest()
    assert saved["password_changed_by"] == "sam"
    (old,) = saved["history"]
    assert old["version"] == 1 and old["salt"] == V1_SALT and old["iterations"] == 600_000
    assert old["verifier"] == VERIFIER and old["replaced_by"] == "sam"
    # Unlocking now asks the other parent's device for the new parameters.
    other = env["client"].get("/api/private-chat/chats/room123abc", headers=_as("alex")).json()
    assert other["version"] == 2 and other["wrapped_key"] == {"iv": _fill(12, 12), "ct": _fill(48, 12)}
    assert "auth_hash" not in other and "history" not in other


def test_a_v1_change_that_would_change_the_key_is_refused(env):
    _seed_v1(env)
    resp = _change(env, verifier={"iv": _fill(12, 9), "ct": _b64(47)})
    assert resp.status_code == 409
    saved = _saved(env)
    assert saved.get("version") is None and saved["salt"] == V1_SALT and "history" not in saved
    assert env["notices"] == []


@pytest.mark.parametrize("outsider,code", [("kid", 404), ("gran", 404), ("uncle", 404), ("teen", 404)])
def test_only_a_member_may_change_it(env, outsider, code):
    _seed_v1(env)
    assert _change(env, user=outsider).status_code == code
    assert _saved(env)["salt"] == V1_SALT and env["notices"] == []


def test_a_member_who_is_no_longer_a_parent_may_not_change_it(env, monkeypatch):
    _seed_v1(env)

    async def changed():
        return {**PEOPLE, "sam": {**PEOPLE["sam"], "family_role": "other"}}

    monkeypatch.setattr(private_chat, "_people", changed)
    assert _change(env, user="sam").status_code == 403
    assert _saved(env)["salt"] == V1_SALT


def test_a_v2_change_needs_the_current_auth(env):
    _create(env)
    assert _change(env).status_code == 403  # no proof
    assert _change(env, auth=_fill(32, 9)).status_code == 403  # the wrong one
    assert _saved(env)["auth_hash"] == hashlib.sha256(bytes([1]) * 32).hexdigest()
    assert env["notices"] == []
    resp = _change(env, auth=AUTH_1)
    assert resp.status_code == 200, resp.text
    assert _saved(env)["auth_hash"] == hashlib.sha256(bytes([2]) * 32).hexdigest()
    # The old proof no longer works; the new one does.
    assert _change(env, n=3, auth=AUTH_1).status_code == 403
    assert _change(env, n=3, auth=_fill(32, 2)).status_code == 200


def test_a_v2_change_with_a_different_verifier_is_refused(env):
    _create(env)
    resp = _change(env, auth=AUTH_1, verifier={"iv": _fill(12, 9), "ct": _b64(47)})
    assert resp.status_code == 409


@pytest.mark.parametrize("over", [
    {"new_auth": _b64(16)},
    {"new_auth": None},
    {"kdf": {"salt": _b64(16), "iterations": 1000}},
    {"wrapped_key": {"iv": _b64(12), "ct": _b64(47)}},
    {"verifier": None},
])
def test_a_malformed_change_is_refused(env, over):
    _seed_v1(env)
    assert _change(env, **over).status_code == 422
    assert _saved(env)["salt"] == V1_SALT


def test_history_keeps_the_last_five_records(env, monkeypatch):
    monkeypatch.setattr(private_chat, "PASSWORD_CHANGE_LIMIT", 100)
    _seed_v1(env)
    assert _change(env, n=2).status_code == 200
    for n in range(3, 9):
        assert _change(env, n=n, auth=_fill(32, n - 1)).status_code == 200
    history = _saved(env)["history"]
    assert len(history) == private_chat.PASSWORD_HISTORY_KEEP == 5
    # Newest last: the v1 record and the first v2 one have aged out.
    assert [h["kdf"]["salt"] for h in history] == [_fill(16, 10 + n) for n in range(3, 8)]
    assert all("history" not in h for h in history)


def test_password_changes_are_rate_limited_per_chat(env):
    _create(env)
    assert private_chat.PASSWORD_CHANGE_LIMIT == 5
    for n in range(2, 7):
        assert _change(env, n=n, auth=_fill(32, n - 1) if n > 2 else AUTH_1).status_code == 200
    resp = _change(env, n=7, auth=_fill(32, 6))
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0 and resp.json()["retry_after"] > 0
    assert _saved(env)["auth_hash"] == hashlib.sha256(bytes([6]) * 32).hexdigest()


def test_refused_attempts_count_toward_the_limit(env):
    _create(env)
    for _ in range(private_chat.PASSWORD_CHANGE_LIMIT):
        assert _change(env, auth=_fill(32, 9)).status_code == 403
    assert _change(env, auth=AUTH_1).status_code == 429


def test_the_other_parent_gets_a_notice_with_no_content(env):
    _seed_v1(env)
    assert _change(env, user="alex").status_code == 200
    (notice,) = env["notices"]
    assert notice["user"] == "sam"
    assert {n["user"] for n in env["notices"]} <= set(_saved(env)["members"])
    assert notice["title"] == "Private chat"
    assert notice["body"] == "Alex changed the private chat password"
    assert notice["data"]["url"] == "/family?chat=room123abc"
    text = json.dumps(notice)
    for secret in (_fill(16, 12), _fill(48, 12), _fill(32, 2), VERIFIER["ct"]):
        assert secret not in text
    assert not any(a.get("type") == "reply" for a in notice.get("actions", []))


def test_an_undelivered_notice_does_not_undo_the_change(env):
    _seed_v1(env)
    env["state"]["deliver"] = False
    resp = _change(env)
    assert resp.status_code == 200 and resp.json()["notified"] == []
    assert _saved(env)["version"] == 2


# --- Message notices go to the two members only ---------------------------------


class _Resp:
    def __init__(self, body):
        self.status = 200
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, conversations):
        self.conversations = conversations

    def post(self, url, json=None, headers=None, timeout=None):
        if url.endswith("/api/resolve"):
            return _Resp({"nextcloud_user": json["rag_user"]})
        return _Resp({"status": "SUCCESS", "detail": {"conversations": self.conversations}})


class _AsyncRedis:
    def __init__(self):
        self.seen = {"room123abc": "1"}

    async def hgetall(self, key):
        return dict(self.seen)

    async def get(self, key):
        return "1"

    async def hset(self, key, field, value):
        self.seen[field] = value

    async def set(self, key, value):
        return True


@pytest.mark.parametrize("user,expected", [("sam", 1), ("kid", 0), ("gran", 0)])
def test_a_private_message_notice_reaches_only_a_member(env, monkeypatch, user, expected):
    import asyncio

    _create(env)
    sent: list[dict] = []

    async def send(notice, **kw):
        sent.append(notice)
        return True

    monkeypatch.setattr(notify, "send", send)
    conv = _conv(last_message_actor_id="alex", last_message_actor="Alex")
    count = asyncio.run(chat_notify._check_user(
        _AsyncRedis(), _Client([conv]), user, identity="i", execution="e", telemetry="t",
        headers={"X-Internal-Secret": "s"}, timeout=None))
    assert count == expected and len(sent) == expected
    assert all(n["user"] in ("alex", "sam") for n in sent)
    # The notice carries the message id (for a phone that shows previews) and never content.
    for n in sent:
        assert n["data"]["message_id"] == 9
        assert "BBBB" not in json.dumps(n)
