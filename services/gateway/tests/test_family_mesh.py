"""The offline chat syncing to its rooms: each message once, only by its author, only into a room
the author is in, and room keys that only members get (rotated when the members change)."""
import base64
import json
import os
from contextlib import asynccontextmanager

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.gateway import family_mesh, notices, private_chat
from services.gateway import main as gateway_main
from services.gateway.main import app

KEY = base64.b64encode(bytes(range(32))).decode()
ROOM = "abcd1234"
GROUP = "grp98765"


def seal(payload: dict, key_b64: str = KEY) -> dict:
    iv = os.urandom(12)
    ct = AESGCM(base64.b64decode(key_b64)).encrypt(iv, json.dumps(payload).encode(), None)
    return {"kind": "sealed", "v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def item(msg_id: str, author: str = "pat", **payload) -> dict:
    """A message held since before room keys: sealed with the household chat key, no room beside it."""
    body = {"t": "text", "from": author, "room": ROOM, "text": "Running late", "sent_at": 1_800_000_000, **payload}
    return {"id": msg_id, "env": seal(body)}


def room_item(msg_id: str, key: str, room: str, author: str = "pat", **payload) -> dict:
    """A message sealed with its room's key, sent with its room (what the new app sends)."""
    body = {"t": "text", "from": author, "room": room, "text": "Running late", "sent_at": 1_800_000_000, **payload}
    return {"id": msg_id, "room": room, "env": seal(body, key)}


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.data:
            return None
        self.data[k] = v
        return True

    async def get(self, k):
        return self.data.get(k)

    async def delete(self, k):
        self.data.pop(k, None)

    async def mget(self, keys):
        return [self.data.get(k) for k in keys]


@pytest.fixture
def env(monkeypatch):
    store = FakeRedis()
    posted: list[dict] = []
    state = {
        "status": 200,
        # Talk: a one-to-one room and a group with someone from outside the family.
        "rooms": [{"token": ROOM, "type": 1, "display_name": "Alex"},
                  {"token": GROUP, "type": 2, "display_name": "Hiking trip"},
                  {"token": "chlog123", "type": 4, "display_name": "Talk updates"},
                  {"token": "priv1234", "type": 1, "display_name": "Private"}],
        "members": {ROOM: ["pat", "alex"], GROUP: ["pat", "alex", "sam", "visitor"],
                    "chlog123": ["pat"], "priv1234": ["pat", "alex"]},
    }

    @asynccontextmanager
    async def _redis(r=None):
        yield store

    async def _who(request, body=None):
        return {"user": request.headers.get("X-Test-User", "")} if request.headers.get("X-Test-User") else None

    async def _config(method, path, **kw):
        return {"chat_key": KEY}

    async def _post_as(creds, endpoint, payload):
        action = payload.get("action")
        if action == "list":
            mine = [r for r in state["rooms"] if creds["user"] in state["members"].get(r["token"], [])]
            return JSONResponse(status_code=200, content={"detail": {"conversations": mine}})
        if action == "participants":
            people = state["members"].get(payload["token"], [])
            return JSONResponse(status_code=200, content={"detail": {"participants": [
                {"actor_type": "users", "actor_id": u} for u in people]}})
        posted.append({"as": creds["user"], **payload})
        return JSONResponse(status_code=state["status"], content={})

    async def _auth(request):
        if not request.headers.get("X-Test-User"):
            from fastapi import HTTPException
            raise HTTPException(status_code=401)
        return request.headers["X-Test-User"]

    monkeypatch.setattr(notices, "_redis", _redis)
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    monkeypatch.setattr(gateway_main, "_geo_proximity", _config)
    monkeypatch.setattr(gateway_main, "_execution_post_as", _post_as)
    monkeypatch.setattr(gateway_main, "_require_authenticated", _auth)
    monkeypatch.setattr(private_chat, "is_private", lambda token: token == "priv1234")
    return store, posted, state


def client(user="pat"):
    return TestClient(app, headers={"X-Test-User": user} if user else {})


ID1 = "0" * 31 + "1"
ID2 = "0" * 31 + "2"


def test_a_message_is_posted_once_as_its_author(env):
    _, posted, _ = env
    r = client().post("/api/family-mesh/sync", json={"items": [item(ID1)]})
    assert r.json() == {"results": {ID1: "posted"}}
    assert posted == [{"as": "pat", "action": "send", "token": ROOM, "message": posted[0]["message"]}]
    assert posted[0]["message"].endswith("Running late")
    assert "sent offline at" in posted[0]["message"]
    # Sent again (a retry, a second device): no duplicate.
    r = client().post("/api/family-mesh/sync", json={"items": [item(ID1)]})
    assert r.json() == {"results": {ID1: "posted"}}
    assert len(posted) == 1


def test_a_relay_never_posts_someone_elses_message(env):
    _, posted, _ = env
    r = client("alex").post("/api/family-mesh/sync", json={"items": [item(ID1, author="pat")]})
    assert r.json() == {"results": {ID1: "not_yours"}}
    assert posted == []


def test_a_message_under_another_key_is_unreadable(env):
    _, posted, _ = env
    other = base64.b64encode(bytes(32)).decode()
    bad = {"id": ID1, "env": seal({"t": "text", "from": "pat", "room": ROOM, "text": "x"}, other)}
    assert client().post("/api/family-mesh/sync", json={"items": [bad]}).json() == {"results": {ID1: "unreadable"}}
    assert posted == []


def test_a_failed_post_can_be_tried_again(env):
    store, posted, state = env
    state["status"] = 502
    assert client().post("/api/family-mesh/sync", json={"items": [item(ID1)]}).json()["results"][ID1] == "failed"
    assert family_mesh.claim_key(ID1) not in store.data
    state["status"] = 200
    assert client().post("/api/family-mesh/sync", json={"items": [item(ID1)]}).json()["results"][ID1] == "posted"
    assert len(posted) == 2


def test_pins_and_thumbnails_become_chat_lines():
    pin = family_mesh.talk_text({"t": "pin", "lat": 39.7392, "lon": -104.9903, "label": "Trailhead", "sent_at": 0})
    assert "Location: Trailhead https://www.openstreetmap.org/?mlat=39.73920&mlon=-104.99030" in pin
    assert family_mesh.talk_text({"t": "pin", "lat": 99, "lon": 0}) is None
    assert "A photo" in family_mesh.talk_text({"t": "thumb", "caption": "View", "sent_at": 0})
    assert family_mesh.talk_text({"t": "text", "text": "  "}) is None


def test_synced_says_which_ids_are_in_the_family_chat(env):
    store, _, _ = env
    store.data[family_mesh.claim_key(ID1)] = "posted"
    store.data[family_mesh.claim_key(ID2)] = "posting"
    r = client("alex").post("/api/family-mesh/synced", json={"ids": [ID1, ID2, "junk"]})
    assert r.json() == {"synced": [ID1]}
    assert client(None).post("/api/family-mesh/synced", json={"ids": [ID1]}).status_code == 401


# -- room keys ---------------------------------------------------------------------------


def rooms_of(user: str) -> dict:
    r = client(user).get("/api/family-mesh/rooms")
    assert r.status_code == 200
    return {room["room"]: room for room in r.json()["rooms"]}


def test_every_room_a_person_is_in_gets_a_key_and_members_share_it(env):
    pat = rooms_of("pat")
    # One-to-one and group rooms; not the changelog, not the parents' end-to-end private chat.
    assert sorted(pat) == sorted([GROUP, ROOM])
    assert pat[ROOM]["name"] == "Alex" and pat[GROUP]["type"] == 2
    assert len(base64.b64decode(pat[ROOM]["key"])) == 32
    assert pat[ROOM]["key"] != pat[GROUP]["key"]
    alex = rooms_of("alex")
    assert alex[ROOM]["key"] == pat[ROOM]["key"]
    # Sam is in the group only: no key for the one-to-one room.
    sam = rooms_of("sam")
    assert sorted(sam) == [GROUP]
    assert sam[GROUP]["key"] == pat[GROUP]["key"]


def test_a_member_list_change_rotates_the_key_and_only_stayers_keep_the_old_one(env):
    store, _, state = env
    before = rooms_of("pat")[GROUP]["key"]
    rooms_of("sam")
    # Sam leaves, Robin joins (the app drops the cached member list).
    state["members"][GROUP] = ["pat", "alex", "visitor", "robin"]
    store.data.pop(family_mesh.members_key(GROUP), None)
    pat = rooms_of("pat")[GROUP]
    assert pat["key"] != before
    assert pat["previous"] == [before]
    robin = rooms_of("robin")[GROUP]
    assert robin["key"] == pat["key"] and robin["previous"] == []
    assert GROUP not in rooms_of("sam")


def test_advance_and_keys_for_rules():
    rec, changed = family_mesh.advance(None, "fp1", "pat", 1000.0)
    assert changed and rec["issued"] == ["pat"]
    same, changed = family_mesh.advance(rec, "fp1", "pat", 1001.0)
    assert not changed and same["key"] == rec["key"]
    rotated, changed = family_mesh.advance(same, "fp2", "alex", 1002.0)
    assert changed and rotated["key"] != rec["key"]
    assert family_mesh.keys_for(rotated, "pat", 1003.0) == [rotated["key"], rec["key"]]
    assert family_mesh.keys_for(rotated, "alex", 1003.0) == [rotated["key"]]
    # A retired key is forgotten once nothing sealed with it can still be travelling.
    late = 1002.0 + family_mesh.PREVIOUS_KEEP_S + 1
    assert family_mesh.keys_for(rotated, "pat", late) == [rotated["key"]]
    pruned, changed = family_mesh.advance(rotated, "fp2", "pat", late)
    assert changed and pruned["previous"] == []


def test_the_routing_tag_matches_the_phone_and_hides_the_room():
    # The same vector is pinned in MeshRulesTest (Java), so phone and server agree on the format.
    tag = family_mesh.route_tag(KEY, 100, "0" * 32)
    assert tag == family_mesh.route_tag(KEY, 100, "0" * 32)
    assert len(tag) == 32
    assert tag != family_mesh.route_tag(KEY, 101, "0" * 32)
    assert tag != family_mesh.route_tag(KEY, 100, "0" * 31 + "1")
    other = base64.b64encode(bytes(32)).decode()
    assert tag != family_mesh.route_tag(other, 100, "0" * 32)


def test_a_room_message_is_posted_into_its_own_room(env):
    _, posted, _ = env
    key = rooms_of("pat")[GROUP]["key"]
    r = client().post("/api/family-mesh/sync", json={"items": [room_item(ID1, key, GROUP)]})
    assert r.json() == {"results": {ID1: "posted"}}
    assert posted[0]["token"] == GROUP and posted[0]["as"] == "pat"
    assert "sent offline at" in posted[0]["message"]


def test_a_user_with_no_default_room_syncs_every_room(env):
    # Nothing about "the chat that opens on launch" is asked for anywhere.
    _, posted, _ = env
    keys = rooms_of("pat")
    items = [room_item(ID1, keys[ROOM]["key"], ROOM), room_item(ID2, keys[GROUP]["key"], GROUP)]
    assert client().post("/api/family-mesh/sync", json={"items": items}).json() == {
        "results": {ID1: "posted", ID2: "posted"}}
    assert sorted(p["token"] for p in posted) == sorted([ROOM, GROUP])


def test_only_a_member_of_the_room_can_post_into_it(env):
    _, posted, state = env
    key = rooms_of("pat")[GROUP]["key"]
    # Pat is no longer in the group (and the key they had cannot be used for it).
    state["members"][GROUP] = ["alex", "sam"]
    r = client().post("/api/family-mesh/sync", json={"items": [room_item(ID1, key, GROUP)]})
    assert r.json()["results"][ID1] in ("not_member", "unreadable")
    assert posted == []


def test_a_member_check_applies_to_old_messages_too(env):
    _, posted, _ = env
    # Sealed with the household key, naming a room the author is not in.
    r = client().post("/api/family-mesh/sync", json={"items": [item(ID1, room="zzzz9999")]})
    assert r.json() == {"results": {ID1: "not_member"}}
    assert posted == []


def test_a_room_message_must_name_the_room_it_is_sent_for(env):
    _, posted, _ = env
    keys = rooms_of("pat")
    # Sealed for the one-to-one room, sent as if for the group.
    body = {"t": "text", "from": "pat", "room": ROOM, "text": "x", "sent_at": 1}
    bad = {"id": ID1, "room": GROUP, "env": seal(body, keys[GROUP]["key"])}
    assert client().post("/api/family-mesh/sync", json={"items": [bad]}).json() == {"results": {ID1: "unreadable"}}
    # Sealed with another room's key.
    wrong_key = room_item(ID2, keys[ROOM]["key"], GROUP)
    assert client().post("/api/family-mesh/sync", json={"items": [wrong_key]}).json() == {"results": {ID2: "unreadable"}}
    assert posted == []


def test_a_relay_cannot_post_a_room_message_it_carries(env):
    _, posted, _ = env
    key = rooms_of("pat")[GROUP]["key"]
    rooms_of("alex")
    r = client("alex").post("/api/family-mesh/sync", json={"items": [room_item(ID1, key, GROUP, author="pat")]})
    assert r.json() == {"results": {ID1: "not_yours"}}
    assert posted == []


def test_a_room_message_is_posted_once(env):
    _, posted, _ = env
    key = rooms_of("pat")[ROOM]["key"]
    for _ in range(2):
        assert client().post("/api/family-mesh/sync", json={"items": [room_item(ID1, key, ROOM)]}).json() == {
            "results": {ID1: "posted"}}
    assert len(posted) == 1


def test_the_private_chat_cannot_be_posted_into_offline(env):
    _, posted, _ = env
    r = client().post("/api/family-mesh/sync", json={"items": [item(ID1, room="priv1234")]})
    assert r.json() == {"results": {ID1: "not_member"}}
    assert posted == []
