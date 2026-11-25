"""The offline family chat syncing to the family chat: each message once, only by its author."""
import base64
import json
import os
from contextlib import asynccontextmanager

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.gateway import family_mesh, notices
from services.gateway import main as gateway_main
from services.gateway.main import app

KEY = base64.b64encode(bytes(range(32))).decode()
ROOM = "abcd1234"


def seal(payload: dict, key_b64: str = KEY) -> dict:
    iv = os.urandom(12)
    ct = AESGCM(base64.b64decode(key_b64)).encrypt(iv, json.dumps(payload).encode(), None)
    return {"kind": "sealed", "v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def item(msg_id: str, author: str = "pat", **payload) -> dict:
    body = {"t": "text", "from": author, "room": ROOM, "text": "Running late", "sent_at": 1_800_000_000, **payload}
    return {"id": msg_id, "env": seal(body)}


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
    state = {"status": 200}

    @asynccontextmanager
    async def _redis(r=None):
        yield store

    async def _who(request, body=None):
        return {"user": request.headers.get("X-Test-User", "")} if request.headers.get("X-Test-User") else None

    async def _config(method, path, **kw):
        return {"chat_key": KEY}

    async def _post_as(creds, endpoint, payload):
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
