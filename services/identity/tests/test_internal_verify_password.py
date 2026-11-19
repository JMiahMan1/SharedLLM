"""``POST /api/internal/verify-password``: HomeHub's check of a Jarvis account's password.

It must answer yes or no and nothing else (no hash, no key, no session), share the sign-in lockout with
``/api/auth/login``, and refuse callers without the internal secret. Every name and password is made up.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity import main as identity
from services.identity.main import app, get_session
from services.identity.models import User
from services.identity.seed import hash_password

SECRET = "test-internal-secret"


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="robin", api_key="key-robin", password_hash=hash_password("right-password")))
        session.add(User(username="nopass", api_key="key-nopass"))
        session.commit()
        monkeypatch.setattr(identity, "INTERNAL_SECRET", SECRET, raising=False)
        identity._LOGIN_FAILURES.clear()
        app.dependency_overrides[get_session] = lambda: session
        with TestClient(app) as client:
            yield client
        app.dependency_overrides.clear()
        identity._LOGIN_FAILURES.clear()


def _check(client, username, password, secret=SECRET):
    return client.post("/api/internal/verify-password", json={"username": username, "password": password},
                       headers={"X-Internal-Secret": secret} if secret else {})


def test_right_password_says_who_and_nothing_secret(client):
    r = _check(client, " Robin ", "right-password")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["username"] == "robin" and isinstance(body["id"], int)
    text = json.dumps(body)
    assert "pbkdf2" not in text and "key-robin" not in text and "right-password" not in text
    assert set(body) == {"ok", "id", "username"}


@pytest.mark.parametrize("username,password", [("robin", "wrong"), ("nobody", "x"), ("nopass", ""), ("", "x")])
def test_wrong_unknown_or_passwordless_are_401(client, username, password):
    assert _check(client, username, password).status_code == 401


def test_needs_the_internal_secret(client):
    assert _check(client, "robin", "right-password", secret=None).status_code in (401, 403)
    assert _check(client, "robin", "right-password", secret="not-it").status_code in (401, 403)


def test_the_lockout_is_shared_with_the_sign_in_page(client):
    for _ in range(identity._LOGIN_MAX_PER_USER):
        assert _check(client, "robin", "wrong").status_code == 401
    # Locked: even the right password is refused, here and on the sign-in page.
    assert _check(client, "robin", "right-password").status_code == 429
    assert client.post("/api/auth/login", json={"username": "robin", "password": "right-password"}).status_code == 429
