"""The private chat's backup key: members store it once, nobody overwrites it, and it is never shown in full.

Backups are encrypted to this public key (scripts/private_chat_backup.py); the
private key is sealed on a phone with the chat's own key, so the server checks
only the shape of what it keeps.
"""
import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from fastapi import HTTPException

from services.gateway import main as gateway_main
from services.gateway.tests.test_private_chat import _as, _create, env  # noqa: F401  (env is a fixture)

TOKEN = "room123abc"
URL = f"/api/private-chat/chats/{TOKEN}/backup-key"


@pytest.fixture
def chat(env):  # noqa: F811  (the imported fixture)
    return env


def _x25519() -> str:
    raw = x25519.X25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _p256() -> str:
    raw = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.b64encode(raw).decode()


def _sealed(ct_bytes: int = 120) -> str:
    env_ = {"kind": "sealed", "title": "Encrypted message", "v": 1,
            "iv": base64.b64encode(b"\x01" * 12).decode(), "ct": base64.b64encode(b"\x02" * ct_bytes).decode()}
    return f"\U0001f512 Encrypted message\n\n```jarvis-envelope\n{json.dumps(env_)}\n```"


def _body(**over):
    body = {"alg": "X25519", "public_key": _x25519(), "sealed_private_key": _sealed(), "created_at": 1}
    body.update(over)
    return body


def _record(chat):
    return json.loads((chat["root"] / "chats" / f"{TOKEN}.json").read_text())


def test_a_new_chat_has_no_backup_key_and_says_so(chat):
    assert _create(chat).json()["backup_key"] is None
    resp = chat["client"].get(URL, headers=_as("sam"))
    assert resp.json() == {"token": TOKEN, "has_backup_key": False, "backup_key": None}


@pytest.mark.parametrize("alg,key", [("X25519", _x25519), ("P-256", _p256)])
def test_a_member_stores_it_and_only_the_public_part_is_shown(chat, alg, key):
    _create(chat)
    public = key()
    resp = chat["client"].post(URL, json=_body(alg=alg, public_key=public), headers=_as("sam"))
    assert resp.status_code == 200, resp.text
    shown = resp.json()["backup_key"]
    assert shown["alg"] == alg and shown["public_key"] == public and len(shown["key_id"]) == 32
    assert "sealed_private_key" not in json.dumps(resp.json())
    stored = _record(chat)["backup_key"]
    assert stored["sealed_private_key"].startswith("\U0001f512") and stored["created_by"] == "sam"
    # A bogus client time is replaced by the server's.
    assert stored["created_at"] > 1_000_000_000
    status = chat["client"].get(URL, headers=_as("alex")).json()
    assert status["has_backup_key"] is True and "sealed_private_key" not in json.dumps(status)
    assert chat["client"].get(f"/api/private-chat/chats/{TOKEN}", headers=_as("alex")).json()["backup_key"] == shown


def test_it_is_never_overwritten_without_rotate(chat):
    _create(chat)
    first = chat["client"].post(URL, json=_body(), headers=_as("alex")).json()["backup_key"]
    again = chat["client"].post(URL, json=_body(), headers=_as("sam"))
    assert again.status_code == 409
    assert again.json()["backup_key"] == first
    assert _record(chat)["backup_key"]["key_id"] == first["key_id"]


def test_rotation_keeps_the_old_key_sealed(chat):
    _create(chat)
    first = chat["client"].post(URL, json=_body(), headers=_as("alex")).json()["backup_key"]
    old_sealed = _record(chat)["backup_key"]["sealed_private_key"]
    resp = chat["client"].post(URL, json=_body(rotate=True), headers=_as("sam"))
    assert resp.status_code == 200
    record = _record(chat)
    assert record["backup_key"]["key_id"] != first["key_id"]
    (retired,) = record["backup_keys_retired"]
    assert retired["key_id"] == first["key_id"] and retired["sealed_private_key"] == old_sealed


def test_a_password_change_leaves_it_alone(chat):
    from services.gateway.tests.test_private_chat import AUTH_1, _fill, _params

    _create(chat)
    chat["client"].post(URL, json=_body(), headers=_as("alex"))
    before = _record(chat)["backup_key"]
    body = _params(kdf={"salt": _fill(16, 3), "iterations": 600_000}, wrapped_key={"iv": _fill(12, 9), "ct": _fill(48, 9)})
    body.pop("auth")
    body.update({"auth": AUTH_1, "new_auth": _fill(32, 5)})
    resp = chat["client"].post(f"/api/private-chat/chats/{TOKEN}/password", json=body, headers=_as("alex"))
    assert resp.status_code == 200, resp.text
    assert _record(chat)["backup_key"] == before


@pytest.mark.parametrize("user,status", [("kid", 404), ("gran", 404), ("teen", 404)])
def test_only_members_may_read_or_set_it(chat, user, status):
    _create(chat)
    assert chat["client"].post(URL, json=_body(), headers=_as(user)).status_code == status
    assert chat["client"].get(URL, headers=_as(user)).status_code == status
    assert "backup_key" not in _record(chat)


@pytest.mark.parametrize("over", [
    {"alg": "RSA"},
    {"alg": "P-256"},  # an X25519 key under the wrong name
    {"public_key": base64.b64encode(b"\0" * 32).decode()},
    {"public_key": "not base64!"},
    {"sealed_private_key": "plain text private key"},
    {"sealed_private_key": _sealed(ct_bytes=8)},
    {"sealed_private_key": _sealed(ct_bytes=4000)},
    {"sealed_private_key": None},
])
def test_shapes_are_checked(chat, over):
    _create(chat)
    resp = chat["client"].post(URL, json=_body(**over), headers=_as("alex"))
    assert resp.status_code in (413, 422), resp.text
    assert "backup_key" not in _record(chat)


def test_the_admin_list_shows_which_chats_have_a_key_and_nothing_else(chat, monkeypatch):
    _create(chat)
    chat["client"].post(URL, json=_body(), headers=_as("alex"))

    async def admin(request):
        if request.headers.get("X-Test-User") != "root":
            raise HTTPException(status_code=403, detail="Admin only")
        return "root"

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    assert chat["client"].get("/api/private-chat/backup-keys", headers=_as("alex")).status_code == 403
    resp = chat["client"].get("/api/private-chat/backup-keys", headers=_as("root"))
    (chat,) = resp.json()["chats"]
    assert chat["token"] == TOKEN and chat["has_backup_key"] is True and chat["alg"] == "X25519"
    text = resp.text
    for secret in ("alex", "sam", "sealed", "public_key", "wrapped_key", "verifier"):
        assert secret not in text
