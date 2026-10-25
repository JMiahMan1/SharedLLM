"""`POST /api/auth/test-connection` for Music Assistant and API-key Audiobookshelf.

MA was the one configured service the UI could not verify ("not testable yet"),
and the Audiobookshelf branch demanded a username/password, so an API-key-only
user could never test. MA 2.10.4 validates the Bearer token (401 for a bad one)
on its JSON-RPC endpoint, and ABS accepts the API key on `GET /api/me`; both
verified live 2026-09-28.
"""
import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity import main as identity_main
from services.identity.main import app
from services.identity.models import User
from services.identity.seed import seed_from_env


@pytest.fixture(name="test_client")
def client_fixture():
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(test_engine)
    identity_main.engine = test_engine
    with Session(test_engine) as session:
        seed_from_env(session, force=True)
        user = session.exec(select(User).where(User.username == "default")).first()
        session.add(user)
        session.commit()

    def _override():
        with Session(test_engine) as session:
            yield session.exec(select(User).where(User.username == "default")).first()

    app.dependency_overrides[identity_main.require_api_key] = _override
    app.dependency_overrides[identity_main.get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(identity_main.require_api_key, None)
        app.dependency_overrides.pop(identity_main.get_session, None)


def _resp(status: int, payload=None, body: str = ""):
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body or json.dumps(payload or {}))
    resp.json = AsyncMock(return_value=payload if payload is not None else {})
    return resp


class _Client:
    """Records the requests it receives and answers with one canned response."""

    def __init__(self, calls: list[dict], status: int, payload=None, body: str = ""):
        self._calls = calls
        self._status = status
        self._payload = payload
        self._body = body

    async def get(self, url, **kwargs):
        self._calls.append({"method": "get", "url": url, **kwargs})
        return _resp(self._status, self._payload, self._body)

    async def post(self, url, **kwargs):
        self._calls.append({"method": "post", "url": url, **kwargs})
        return _resp(self._status, self._payload, self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_client(monkeypatch, status: int, payload=None, body: str = ""):
    """Replace the aiohttp client factories; capture the calls they receive."""
    calls: list[dict] = []

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield _Client(calls, status, payload, body)

    # GitHub and GitLab use the verifying client; the rest use the insecure one.
    monkeypatch.setattr(identity_main, "get_client_insecure", fake_client)
    monkeypatch.setattr(identity_main, "get_client", fake_client)
    return calls


def _test(client, service, config):
    return client.post(
        "/api/auth/test-connection",
        json={"service": service, "config": config},
    ).json()


# Music Assistant

def test_ma_requires_url_and_token(test_client):
    resp = test_client.post(
        "/api/auth/test-connection",
        json={"service": "Music Assistant", "config": {"mass_url": "http://ma:8095"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ERROR"
    assert "mass_token" in data["message"]


def test_ma_success_calls_jsonrpc_with_bearer(test_client, monkeypatch):
    calls = _patch_client(monkeypatch, 200, payload=[{"item_id": "6"}])

    data = _test(
        test_client,
        "Music Assistant",
        {"mass_url": "http://ma:8095/", "mass_token": "ma-jwt"},
    )

    assert data["status"] == "SUCCESS"
    assert "Music Assistant" in data["message"]
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "post"
    assert call["url"] == "http://ma:8095/api"
    assert call["headers"]["Authorization"] == "Bearer ma-jwt"
    assert call["json"]["command"]
    assert call["json"]["message_id"]


def test_ma_rejected_token_is_an_error(test_client, monkeypatch):
    _patch_client(monkeypatch, 401, body="Authentication required")

    data = _test(
        test_client,
        "Music Assistant",
        {"mass_url": "http://ma:8095", "mass_token": "stale"},
    )

    assert data["status"] == "ERROR"
    assert "401" in data["message"]


# Audiobookshelf with an API key

def test_abs_api_key_is_enough_to_test(test_client, monkeypatch):
    calls = _patch_client(monkeypatch, 200, payload={"username": "jeremiah"})

    data = _test(
        test_client,
        "Audiobookshelf",
        {"audiobookshelf_url": "https://abs.example.com", "audiobookshelf_api_key": "abs-key"},
    )

    assert data["status"] == "SUCCESS"
    assert "jeremiah" in data["message"]
    assert calls[0]["method"] == "get"
    assert calls[0]["url"] == "https://abs.example.com/api/me"
    assert calls[0]["headers"]["Authorization"] == "Bearer abs-key"


def test_abs_rejected_api_key_is_an_error(test_client, monkeypatch):
    _patch_client(monkeypatch, 401, body="Unauthorized")

    data = _test(
        test_client,
        "Audiobookshelf",
        {"audiobookshelf_url": "https://abs.example.com", "audiobookshelf_api_key": "stale"},
    )

    assert data["status"] == "ERROR"
    assert "401" in data["message"]


def test_abs_without_any_credential_fails_loudly(test_client):
    data = _test(test_client, "Audiobookshelf", {"audiobookshelf_url": "https://abs.example.com"})
    assert data["status"] == "ERROR"
    assert "api key" in data["message"].lower()


# GitHub (the branch referenced undefined names and always raised)

def test_github_token_test_does_not_crash(test_client, monkeypatch):
    _patch_client(monkeypatch, 200, payload={"login": "jeremiah"})

    data = _test(test_client, "GitHub", {"github_token": "gh-token"})

    assert data["status"] == "SUCCESS"
    assert "jeremiah" in data["message"]


def test_github_requires_token(test_client):
    data = _test(test_client, "GitHub", {})
    assert data["status"] == "ERROR"
    assert "token" in data["message"].lower()


# Testing an already-saved integration

def test_blank_secret_is_tested_with_the_saved_one(test_client, monkeypatch):
    """Saved secrets never reach the browser, so Manage -> Test sends the token
    blank; the test must use the caller's saved token, not fail."""
    from services.identity.crypto import encrypt

    with Session(identity_main.engine) as session:
        user = session.exec(select(User).where(User.username == "default")).first()
        user.ha_token_enc = encrypt("saved-ha-token")
        session.add(user)
        session.commit()
    calls = _patch_client(monkeypatch, 200, payload={})

    data = _test(test_client, "Home Assistant", {"ha_url": "http://ha:8123", "ha_token": ""})

    assert data["status"] == "SUCCESS"
    assert calls[0]["headers"]["Authorization"] == "Bearer saved-ha-token"


# Audiobookshelf username/password logs in at the root /login

def test_abs_username_password_posts_to_the_root_login(test_client, monkeypatch):
    """ABS logs in at /login; /api/login answers 401 (see abs_client.py)."""
    calls = _patch_client(monkeypatch, 200, payload={"user": {"username": "jeremiah"}})

    data = _test(
        test_client,
        "Audiobookshelf",
        {
            "audiobookshelf_url": "https://abs.example.com/",
            "audiobookshelf_user": "jeremiah",
            "audiobookshelf_pass": "pw",
        },
    )

    assert data["status"] == "SUCCESS"
    assert "jeremiah" in data["message"]
    assert calls[0]["method"] == "post"
    assert calls[0]["url"] == "https://abs.example.com/login"


# Nextcloud reads the caller's own account and checks the OCS status code

def test_nextcloud_reads_the_users_own_ocs_status(test_client, monkeypatch):
    calls = _patch_client(
        monkeypatch, 200, payload={"ocs": {"meta": {"statuscode": 200, "message": "OK"}}}
    )

    data = _test(
        test_client,
        "Nextcloud",
        {"nextcloud_url": "https://cloud.example.com/", "nextcloud_user": "jeremiah", "nextcloud_pass": "pw"},
    )

    assert data["status"] == "SUCCESS"
    assert calls[0]["url"] == "https://cloud.example.com/ocs/v2.php/cloud/user?format=json"
    assert calls[0]["headers"]["OCS-APIRequest"] == "true"


def test_nextcloud_permission_error_is_reported_not_read_as_success(test_client, monkeypatch):
    """OCS reports a permission failure as HTTP 200 with statuscode 997."""
    _patch_client(monkeypatch, 200, payload={"ocs": {"meta": {"statuscode": 997, "message": "Unauthorised"}}})

    data = _test(
        test_client,
        "Nextcloud",
        {"nextcloud_url": "https://cloud.example.com", "nextcloud_user": "jeremiah", "nextcloud_pass": "pw"},
    )

    assert data["status"] == "ERROR"
    assert "997" in data["message"]


# A URL with no scheme is explained, not echoed

def _patch_client_raising(monkeypatch, exc: Exception) -> None:
    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        class _Raising:
            async def get(self, url, **kwargs):
                raise exc

            async def post(self, url, **kwargs):
                raise exc

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

        yield _Raising()

    monkeypatch.setattr(identity_main, "get_client_insecure", fake_client)
    monkeypatch.setattr(identity_main, "get_client", fake_client)


def test_a_url_without_a_scheme_is_explained(test_client, monkeypatch):
    _patch_client_raising(monkeypatch, aiohttp.InvalidURL("example.com"))

    data = _test(test_client, "Home Assistant", {"ha_url": "example.com", "ha_token": "t"})

    assert data["status"] == "ERROR"
    assert data["message"] == "URL must start with http:// or https://"


def test_an_unexpected_failure_does_not_echo_the_exception(test_client, monkeypatch):
    _patch_client_raising(monkeypatch, RuntimeError("dial tcp 10.0.0.5:8123: refused"))

    data = _test(test_client, "Home Assistant", {"ha_url": "http://ha:8123", "ha_token": "t"})

    assert data["status"] == "ERROR"
    assert "10.0.0.5" not in data["message"]
    assert "refused" not in data["message"]


# TLS: public git hosts verify, self-hosted private hosts do not

def test_github_and_gitlab_use_the_verifying_client(test_client, monkeypatch):
    used = {"verify": 0, "insecure": 0}

    def _factory(kind: str):
        @asynccontextmanager
        async def _cm(*args, **kwargs):
            used[kind] += 1
            yield _Client([], 200, {"login": "jeremiah", "username": "jeremiah"})

        return _cm

    monkeypatch.setattr(identity_main, "get_client", _factory("verify"))
    monkeypatch.setattr(identity_main, "get_client_insecure", _factory("insecure"))

    _test(test_client, "GitHub", {"github_token": "gh"})
    _test(test_client, "GitLab", {"gitlab_token": "gl"})
    assert used == {"verify": 2, "insecure": 0}

    _test(test_client, "Home Assistant", {"ha_url": "http://ha:8123", "ha_token": "t"})
    _test(test_client, "Nextcloud", {"nextcloud_url": "http://nc", "nextcloud_user": "u", "nextcloud_pass": "p"})
    assert used == {"verify": 2, "insecure": 2}


# Secrets never reach the log

def test_secret_values_are_never_logged(test_client, monkeypatch, caplog):
    from services.identity.crypto import encrypt

    with Session(identity_main.engine) as session:
        user = session.exec(select(User).where(User.username == "default")).first()
        user.mailcow_api_key_enc = encrypt("mailcow-secret-value")
        session.add(user)
        session.commit()

    _patch_client(monkeypatch, 200, payload={"login": "x"})

    with caplog.at_level(logging.INFO, logger="identity"):
        _test(test_client, "GitHub", {"github_user": "u", "github_token": "ghp_plaintext_token"})
        _test(test_client, "Audiobookshelf", {"audiobookshelf_url": "http://abs", "audiobookshelf_api_key": "abs-plaintext-key"})
        _test(test_client, "GitHub", {"api_key": "generic-plaintext-key"})
        # Filled from a saved *_enc column, so it must be masked even though the
        # request carried it blank.
        _test(test_client, "GitHub", {"mailcow_api_key": ""})

    assert "ghp_plaintext_token" not in caplog.text
    assert "abs-plaintext-key" not in caplog.text
    assert "generic-plaintext-key" not in caplog.text
    assert "mailcow-secret-value" not in caplog.text
