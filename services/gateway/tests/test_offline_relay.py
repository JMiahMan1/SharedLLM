"""Jarvis requests relayed by another family phone: verified as the sender's, acted on as the sender, once."""
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import notices, offline_relay
from services.gateway.main import app

# Made-up keys of two family members' phones.
KEYS = {"pat": "pat-api-key-0123456789", "alex": "alex-api-key-9876543210"}
NOW = time.time()


def request(sender="pat", kind="ask", text="Is the garage door shut?", key=None, req_id="a" * 32,
            issued=None, life=600, **change):
    sign_key, seal_key = offline_relay.derive_keys(key or KEYS[sender])
    issued = int(NOW if issued is None else issued)
    req = {"id": req_id, "from": sender, "kind": kind, "issued_at": issued, "expires_at": issued + life,
           "env": offline_relay.seal(seal_key, {"text": text})}
    req.update(change)
    req["sig"] = offline_relay.sign(sign_key, req)
    return req


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.data:
            return None
        self.data[k] = v
        return True

    async def get(self, k):
        return self.data.get(k)

    async def mget(self, keys):
        return [self.data.get(k) for k in keys]


@pytest.fixture
def env(monkeypatch):
    store = FakeRedis()
    acted: list[tuple] = []

    @asynccontextmanager
    async def _redis(r=None):
        yield store

    async def _resolve(body):
        user = body.get("rag_user")
        return {"user": user, "api_key": KEYS.get(user)} if user in KEYS else {"user": "default"}

    async def _act(api_key, sender, kind, payload):
        acted.append((api_key, sender, kind, payload))
        return {"ok": True, "text": f"Yes, {sender}."}

    async def _auth(request):
        user = request.headers.get("X-Test-User")
        if not user:
            from fastapi import HTTPException
            raise HTTPException(status_code=401)
        return user

    monkeypatch.setattr(notices, "_redis", _redis)
    monkeypatch.setattr(gateway_main, "resolve_identity", _resolve)
    monkeypatch.setattr(gateway_main, "_require_authenticated", _auth)
    monkeypatch.setattr(offline_relay, "act_as_sender", _act)
    monkeypatch.setattr(offline_relay.time, "time", lambda: NOW)
    return store, acted


def submit(relay, *reqs):
    r = TestClient(app, headers={"X-Test-User": relay}).post("/api/offline-relay/submit", json={"requests": list(reqs)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_a_relayed_request_is_acted_on_as_the_sender_never_the_relay(env):
    _, acted = env
    out = submit("alex", request("pat"))["a" * 32]
    assert out["status"] == "answered"
    assert acted == [(KEYS["pat"], "pat", "ask", {"text": "Is the garage door shut?"})]
    # The answer is sealed for the sender: only Pat's key opens it.
    _, pat_seal = offline_relay.derive_keys(KEYS["pat"])
    _, alex_seal = offline_relay.derive_keys(KEYS["alex"])
    assert offline_relay.open_sealed(pat_seal, out["answer"])["text"] == "Yes, pat."
    assert offline_relay.open_sealed(alex_seal, out["answer"]) is None
    assert out["to"] == "pat"


def test_a_relay_cannot_speak_for_someone_else(env):
    _, acted = env
    # Alex signs with Alex's key but claims to be Pat.
    forged = request("pat", key=KEYS["alex"])
    assert submit("alex", forged)["a" * 32]["status"] == "bad_signature"
    # Or changes a word of Pat's request on the way.
    tampered = request("pat")
    tampered["kind"] = "command"
    assert submit("alex", tampered)["a" * 32]["status"] == "bad_signature"
    tampered = request("pat")
    tampered["env"] = request("pat", text="Unlock the front door")["env"]
    assert submit("alex", tampered)["a" * 32]["status"] == "bad_signature"
    assert acted == []


def test_a_request_is_acted_on_once_and_a_second_relay_gets_the_same_answer(env):
    _, acted = env
    first = submit("alex", request("pat"))["a" * 32]
    again = submit("robin", request("pat"))["a" * 32]
    assert len(acted) == 1
    assert again["status"] == "answered" and again["answer"] == first["answer"]


def test_expired_future_and_overlong_requests_are_refused(env):
    _, acted = env
    assert submit("alex", request(issued=NOW - 700, life=600))["a" * 32]["status"] == "expired"
    assert submit("alex", request(issued=NOW + 3600))["a" * 32]["status"] == "from_the_future"
    assert submit("alex", request(kind="command", life=3600))["a" * 32]["status"] == "lifetime_too_long"
    assert submit("alex", request(kind="ask", life=3601))["a" * 32]["status"] == "lifetime_too_long"
    assert submit("alex", request(kind="drive_the_car"))["a" * 32]["status"] == "unknown_kind"
    assert acted == []


def test_an_unknown_sender_or_junk_is_refused(env):
    assert submit("alex", request("pat", **{"from": "nobody"}))["a" * 32]["status"] in ("unknown_sender", "bad_signature")
    assert submit("alex", {"id": "b" * 32})["b" * 32]["status"] == "malformed"
    assert TestClient(app).post("/api/offline-relay/submit", json={"requests": []}).status_code == 401


def test_only_the_sender_reads_its_answers_online(env):
    submit("alex", request("pat"))
    pat = TestClient(app, headers={"X-Test-User": "pat"}).post("/api/offline-relay/answers", json={"ids": ["a" * 32]})
    alex = TestClient(app, headers={"X-Test-User": "alex"}).post("/api/offline-relay/answers", json={"ids": ["a" * 32]})
    assert list(pat.json()["answers"]) == ["a" * 32]
    assert alex.json()["answers"] == {}


def test_the_signature_covers_every_field():
    sign_key, _ = offline_relay.derive_keys(KEYS["pat"])
    req = request("pat")
    assert offline_relay.verify(sign_key, req)
    for field, value in (("id", "c" * 32), ("from", "alex"), ("issued_at", req["issued_at"] - 1),
                         ("expires_at", req["expires_at"] + 1)):
        changed = dict(req, **{field: value})
        assert not offline_relay.verify(sign_key, changed), field


def test_reply_text_reads_every_answer_shape():
    assert offline_relay.reply_text({"response": " ok "}) == "ok"
    assert offline_relay.reply_text({"message": {"content": "yes"}}) == "yes"
    assert offline_relay.reply_text({"choices": [{"message": {"content": "sure"}}]}) == "sure"
    assert offline_relay.reply_text({"status": "ERROR"}) is None


def test_edits_are_applied_in_the_order_they_were_made(env):
    _, acted = env
    later = request("pat", kind="shopping_add", text="x", req_id="b" * 32, issued=NOW - 10, life=3600)
    earlier = request("pat", kind="shopping_add", text="y", req_id="c" * 32, issued=NOW - 20, life=3600)
    submit("alex", later, earlier)
    assert [a[3]["text"] for a in acted] == ["y", "x"]
