"""`POST /api/auth/test-connection` for Music Assistant and API-key Audiobookshelf.

MA was the one configured service the UI could not verify ("not testable yet"),
and the Audiobookshelf branch demanded a username/password, so an API-key-only
user could never test. MA 2.10.4 validates the Bearer token (401 for a bad one)
on its JSON-RPC endpoint, and ABS accepts the API key on `GET /api/me` — both
verified live 2026-09-28.
"""
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

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


def _patch_client(monkeypatch, status: int, payload=None, body: str = ""):
    """Replace the aiohttp client factory; capture the calls it receives."""
    calls: list[dict] = []

    def _resp():
        resp = MagicMock()
        resp.status = status
        resp.text = AsyncMock(return_value=body or json.dumps(payload or {}))
        resp.json = AsyncMock(return_value=payload if payload is not None else {})
        return resp

    class _Client:
        async def get(self, url, **kwargs):
            calls.append({"method": "get", "url": url, **kwargs})
            return _resp()

        async def post(self, url, **kwargs):
            calls.append({"method": "post", "url": url, **kwargs})
            return _resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield _Client()

    monkeypatch.setattr(identity_main, "get_client_insecure", fake_client)
    return calls


def _test(client, service, config):
    return client.post(
        "/api/auth/test-connection",
        json={"service": service, "config": config},
    ).json()


# ── Music Assistant ──────────────────────────────────────────────────────────

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


# ── Audiobookshelf with an API key ───────────────────────────────────────────

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


# ── GitHub (the branch referenced undefined names and always raised) ─────────

def test_github_token_test_does_not_crash(test_client, monkeypatch):
    _patch_client(monkeypatch, 200, payload={"login": "jeremiah"})

    data = _test(test_client, "GitHub", {"github_token": "gh-token"})

    assert data["status"] == "SUCCESS"
    assert "jeremiah" in data["message"]


def test_github_requires_token(test_client):
    data = _test(test_client, "GitHub", {})
    assert data["status"] == "ERROR"
    assert "token" in data["message"].lower()
