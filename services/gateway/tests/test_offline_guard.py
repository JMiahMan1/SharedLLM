"""Parent commands carried over Bluetooth: only a parent's registered device can make one, it is applied
once with the existing screen-time routes as that parent, and the child's acknowledgement is checked."""
import base64
import json
import time
from contextlib import asynccontextmanager

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import notices, offline_guard, offline_relay
from services.gateway.main import app
from services.gateway.tests.test_offline_relay import FakeRedis

NOW = time.time()


class Device:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())

    @property
    def pub(self) -> str:
        der = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(der).decode()

    def sign(self, data: bytes) -> str:
        return base64.b64encode(self.key.sign(data, ec.ECDSA(hashes.SHA256()))).decode()


PARENT, CHILD, SIBLING = Device(), Device(), Device()


def command(action="lock", arg=0, seq=1, cid="a" * 32, issued=None, by=PARENT, **change):
    issued = int(NOW if issued is None else issued)
    c = {"id": cid, "parent": "mom", "device": "mom-phone", "child": "kid", "action": action, "arg": arg,
         "issued_at": issued, "expires_at": issued + 1800, "seq": seq, **change}
    c["sig"] = by.sign(offline_guard.command_bytes(c))
    return c


def ack(c, by=CHILD, result="applied"):
    a = {"id": c["id"], "child": "kid", "device": "kid-phone", "applied_at": int(NOW), "result": result}
    a["sig"] = by.sign(offline_guard.ack_bytes(a))
    return a


@pytest.fixture
def env(monkeypatch):
    store = FakeRedis()
    store.data["offline_keys:mom"] = json.dumps({"mom-phone": {"pub": PARENT.pub}})
    store.data["offline_keys:kid"] = json.dumps({"kid-phone": {"pub": CHILD.pub}})
    store.data["offline_keys:sib"] = json.dumps({"sib-phone": {"pub": SIBLING.pub}})
    applied: list[tuple] = []

    async def _delete(k):
        store.data.pop(k, None)

    store.delete = _delete

    @asynccontextmanager
    async def _redis(r=None):
        yield store

    async def _parents(user):
        return (["mom"] if user == "kid" else []), user

    async def _key(user):
        return f"{user}-api-key"

    async def _call(method, path, api_key, body):
        applied.append((api_key, path, body))
        return 200, {}

    async def _auth(request):
        return request.headers.get("X-Test-User") or "relay"

    monkeypatch.setattr(notices, "_redis", _redis)
    monkeypatch.setattr(gateway_main, "_my_parents", _parents)
    monkeypatch.setattr(gateway_main, "_require_authenticated", _auth)
    monkeypatch.setattr(offline_relay, "_sender_key", _key)
    monkeypatch.setattr(offline_relay, "_call_self", _call)
    monkeypatch.setattr(offline_guard.time, "time", lambda: NOW)
    return store, applied


def report(*items, user="sib"):
    r = TestClient(app, headers={"X-Test-User": user}).post("/api/offline-guard/report", json={"commands": list(items)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_a_parents_lock_is_applied_once_with_the_existing_route_as_the_parent(env):
    _, applied = env
    c = command("lock", arg=30)
    out = report({"command": c, "ack": ack(c)})[c["id"]]
    assert out["status"] == "applied"
    assert out["ack"]["result"] == "applied"
    assert len(applied) == 1
    api_key, path, body = applied[0]
    assert (api_key, path) == ("mom-api-key", "/api/screen-time/kid/pause")
    assert "until" in body
    # Handed in again by another phone: not applied twice.
    assert report({"command": c})[c["id"]]["status"] == "applied"
    assert len(applied) == 1


def test_unlock_and_bonus_use_the_same_controls(env):
    _, applied = env
    report({"command": command("unlock", cid="b" * 32, seq=2)}, {"command": command("bonus", arg=15, cid="c" * 32, seq=3)})
    assert [(p, b) for _, p, b in applied] == [("/api/screen-time/kid/pause", {"resume": True}),
                                              ("/api/screen-time/kid/bonus", {"minutes": 15})]


def test_a_child_or_sibling_cannot_forge_a_parents_command(env):
    _, applied = env
    forged = command("unlock", by=CHILD)  # the child's own key, claiming the parent's device
    assert report({"command": forged})[forged["id"]]["status"] == "bad_signature"
    altered = command("lock", arg=30)
    altered["action"] = "unlock"
    assert report({"command": altered})[altered["id"]]["status"] == "bad_signature"
    not_parent = command(parent="sib", device="sib-phone", by=SIBLING)
    assert report({"command": not_parent})[not_parent["id"]]["status"] == "not_a_parent"
    assert applied == []


def test_commands_are_applied_in_the_order_the_parent_made_them(env):
    _, applied = env
    first = command("lock", cid="d" * 32, seq=4, issued=NOW - 60)
    second = command("unlock", cid="e" * 32, seq=5, issued=NOW - 30)
    report({"command": second}, {"command": first})
    assert [b for _, _, b in applied] == [{}, {"resume": True}]


def test_an_acknowledgement_must_be_the_childs(env):
    c = command()
    out = report({"command": c, "ack": ack(c, by=SIBLING)})[c["id"]]
    assert out["ack"] is None


def test_stale_and_future_commands_are_refused(env):
    long = command(expires_at=int(NOW) + 10 * 3600)
    assert report({"command": long})[long["id"]]["status"] == "lifetime_too_long"
    future = command(issued=NOW + 3600, cid="f" * 32)
    assert report({"command": future})[future["id"]]["status"] == "from_the_future"


def test_the_parent_sees_delivery(env):
    c = command()
    report({"command": c, "ack": ack(c)})
    mine = TestClient(app, headers={"X-Test-User": "mom"}).post("/api/offline-guard/status", json={"ids": [c["id"]]})
    other = TestClient(app, headers={"X-Test-User": "sib"}).post("/api/offline-guard/status", json={"ids": [c["id"]]})
    assert mine.json()["commands"][c["id"]]["ack"]["result"] == "applied"
    assert other.json()["commands"] == {}


def test_registering_a_key_accepts_only_p256(monkeypatch, env):
    store, _ = env
    client = TestClient(app, headers={"X-Test-User": "dad"})
    assert client.post("/api/offline-keys", json={"device_id": "dad-phone", "public_key": PARENT.pub}).status_code == 200
    assert json.loads(store.data["offline_keys:dad"])["dad-phone"]["pub"] == PARENT.pub
    assert client.post("/api/offline-keys", json={"device_id": "dad-phone", "public_key": "AAAA"}).status_code == 422


def test_bluetooth_and_airplane_mode_reach_the_parents_in_plain_words():
    assert gateway_main._tamper_sentence("bluetooth_off", "from 3:10 PM to 3:42 PM") == "had Bluetooth off from 3:10 PM to 3:42 PM."
    assert gateway_main._tamper_sentence("airplane_mode", "from 9:00 PM to 9:30 PM") == "was in airplane mode from 9:00 PM to 9:30 PM."
    assert gateway_main._tamper_sentence("offline_parent_command", "lock from mom").startswith("applied a parent's command")
