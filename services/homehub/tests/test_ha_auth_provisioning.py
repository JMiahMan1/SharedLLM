"""Person account provisioning via HAAuth.ensure_person_user and POST /hub/auth/users."""
from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Hub
from services.homehub.ha_auth import AuthError, HAAuth
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"


@pytest.fixture
def parts(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    auth = Auth(store, SECRET)
    ha = HAAuth(store, auth)
    return store, auth, ha


def test_create_person_user_succeeds_and_verifies_password(parts):
    _store, _auth, ha = parts
    result = ha.ensure_person_user("alice", display_name="Alice Smith", password="correct horse")
    assert result["created"] is True
    assert result["username"] == "alice"
    assert result["display_name"] == "Alice Smith"
    assert result["is_admin"] is False
    assert result["user_id"] and len(result["user_id"]) == 32
    # Password check works.
    cred = ha.check_password("alice", "correct horse")
    assert cred is not None and cred["user_id"] == result["user_id"]
    # Wrong password fails.
    assert ha.check_password("alice", "wrong") is None


def test_create_admin_person_user_sets_group_ids(parts):
    _store, _auth, ha = parts
    result = ha.ensure_person_user("bob", is_admin=True)
    assert result["is_admin"] is True
    users = _store.get_kv("users", []) or []
    user = next(u for u in users if u["id"] == result["user_id"])
    assert user["group_ids"] == ["system-admin"]
    assert user["is_owner"] is False
    assert user["service"] is False
    assert user["system_generated"] is False
    assert user["is_active"] is True


def test_create_regular_person_user_sets_group_ids(parts):
    _store, _auth, ha = parts
    result = ha.ensure_person_user("carol")
    assert result["is_admin"] is False
    users = _store.get_kv("users", []) or []
    user = next(u for u in users if u["id"] == result["user_id"])
    assert user["group_ids"] == ["system-users"]


def test_ensure_person_user_is_idempotent(parts):
    store, _auth, ha = parts
    r1 = ha.ensure_person_user("dave", password="password123")
    r2 = ha.ensure_person_user("dave", password="password123")
    assert r1["created"] is True
    assert r2["created"] is False
    assert r1["user_id"] == r2["user_id"]
    assert r1["username"] == r2["username"]
    # No duplicate user or credential.
    users = store.get_kv("users", []) or []
    assert sum(1 for u in users if not u.get("service") and not u.get("system_generated")) == 1
    blob = ha._blob()
    matching = [c for c in blob["credentials"]
                if c.get("user_id") == r1["user_id"] and c.get("username") == "dave"]
    assert len(matching) == 1


def test_ensure_person_user_short_username_raises(parts):
    ha = HAAuth(parts[0], parts[1])
    with pytest.raises(AuthError) as exc_info:
        ha.ensure_person_user("a")
    assert exc_info.value.status == 400
    assert "username must be at least 2 characters" in exc_info.value.description


def test_ensure_person_user_without_password_gets_unguessable_hash(parts):
    _store, _auth, ha = parts
    result = ha.ensure_person_user("eve")
    blob = ha._blob()
    cred = next(c for c in blob["credentials"]
                if c.get("user_id") == result["user_id"] and c.get("username") == "eve")
    assert cred["password"]
    raw = base64.b64decode(cred["password"])
    assert raw.startswith(b"$2b$")
    assert ha.check_password("eve", "") is None
    assert ha.check_password("eve", "guess-password") is None


def test_ensure_person_user_updates_password_when_provided(parts):
    _store, _auth, ha = parts
    ha.ensure_person_user("frank", password="oldpass")
    assert ha.check_password("frank", "oldpass") is not None
    assert ha.check_password("frank", "newpass") is None
    # Update password.
    ha.ensure_person_user("frank", password="newpass")
    assert ha.check_password("frank", "newpass") is not None
    assert ha.check_password("frank", "oldpass") is None


def test_ensure_person_user_normalizes_username(parts):
    _store, _auth, ha = parts
    r1 = ha.ensure_person_user("  Grace  ")
    r2 = ha.ensure_person_user("grace")
    assert r1["username"] == "grace"
    assert r2["username"] == "grace"
    assert r1["user_id"] == r2["user_id"]
    assert r2["created"] is False


def test_post_hub_auth_users_rejects_missing_auth(parts):
    store, auth, _ha = parts
    app = build_app(Runtime(Hub(), store), auth)
    with TestClient(app) as client:
        r = client.post("/hub/auth/users", json={"username": "hank"})
        assert r.status_code == 401


def test_post_hub_auth_users_rejects_non_internal_auth(parts):
    store, auth, _ha = parts
    app = build_app(Runtime(Hub(), store), auth)
    with TestClient(app) as client:
        r = client.post("/hub/auth/users", json={"username": "hank"},
                        headers={"Authorization": "Bearer some-token"})
        assert r.status_code == 401


def test_post_hub_auth_users_provisions_with_internal_secret(parts):
    store, auth, ha = parts
    app = build_app(Runtime(Hub(), store), auth)
    with TestClient(app) as client:
        r = client.post("/hub/auth/users",
                        json={"username": "iris", "display_name": "Iris Jones",
                              "password": "secret123", "is_admin": True},
                        headers={"X-Internal-Secret": SECRET})
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "iris"
        assert body["display_name"] == "Iris Jones"
        assert body["is_admin"] is True
        assert body["created"] is True
        # Password is NOT in the response.
        for key in body:
            assert key not in ("password", "hash")
        # The credential is checkable.
        cred = ha.check_password("iris", "secret123")
        assert cred is not None


def test_post_hub_auth_users_does_not_leak_password_in_response(parts):
    store, auth, _ha = parts
    app = build_app(Runtime(Hub(), store), auth)
    with TestClient(app) as client:
        r = client.post("/hub/auth/users",
                        json={"username": "jack", "password": "mysecret"},
                        headers={"X-Internal-Secret": SECRET})
        assert r.status_code == 200
        text = r.text
        assert "mysecret" not in text


def test_post_hub_auth_users_returns_400_for_short_username(parts):
    store, auth, _ha = parts
    app = build_app(Runtime(Hub(), store), auth)
    with TestClient(app) as client:
        r = client.post("/hub/auth/users",
                        json={"username": "x"},
                        headers={"X-Internal-Secret": SECRET})
        assert r.status_code == 400


def test_ensure_person_user_skips_service_and_system_users(parts):
    store, _auth, ha = parts
    # Pre-seed a service user and a system user.
    store.set_kv("users", [
        {"id": "svc1", "name": "Music Assistant", "is_owner": False, "is_active": True,
         "system_generated": False, "group_ids": ["system-users"], "service": True},
        {"id": "sys1", "name": "Supervisor", "is_owner": False, "is_active": True,
         "system_generated": True, "group_ids": ["system-admin"], "service": False},
    ])
    # Put a credential on the system user so it looks like it has a login.
    ha._save({"credentials": [
        {"id": "c1", "user_id": "sys1", "username": "supervisor", "password": "x"}
    ]})
    # "supervisor" is the system user's login: a person may not get a second credential
    # with the same username (logins look the credential up by username).
    with pytest.raises(AuthError) as exc:
        ha.ensure_person_user("supervisor", password="pass123")
    assert exc.value.status == 409
    # A fresh name still makes a person user, never reusing the service or system ones.
    r = ha.ensure_person_user("newperson", password="pass123")
    assert r["created"] is True
    users = store.get_kv("users", []) or []
    person_users = [u for u in users if not u.get("service") and not u.get("system_generated")]
    assert len(person_users) == 1
    assert person_users[0]["id"] not in ("svc1", "sys1")
