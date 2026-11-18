"""`POST /api/auth/test-connection` for HomeHub (integrations step 2).

HomeHub's address is the household ``homehub_url`` setting, so only an admin may
test one. The test asks the address's ``/health`` and sends no credential: the
stack's own secret must never go to an address typed into a form.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity import main as identity_main
from services.identity.main import app
from services.identity.models import User
from services.identity.seed import seed_from_env

HUB = "http://hub.example.test:8123"


def _client_for(is_admin: bool):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    identity_main.engine = engine
    with Session(engine) as session:
        seed_from_env(session, force=True)
        session.add(User(username="casey", display_name="Casey", is_admin=is_admin))
        session.commit()

    def _caller():
        with Session(engine) as session:
            yield session.exec(select(User).where(User.username == "casey")).one()

    def _session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[identity_main.require_api_key] = _caller
    app.dependency_overrides[identity_main.get_session] = _session
    return TestClient(app)


@pytest.fixture
def admin_client():
    try:
        yield _client_for(True)
    finally:
        app.dependency_overrides.pop(identity_main.require_api_key, None)
        app.dependency_overrides.pop(identity_main.get_session, None)


@pytest.fixture
def member_client():
    try:
        yield _client_for(False)
    finally:
        app.dependency_overrides.pop(identity_main.require_api_key, None)
        app.dependency_overrides.pop(identity_main.get_session, None)


def _patch(monkeypatch, status=200, payload=None):
    calls: list[dict] = []
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=payload)

    class _Client:
        async def get(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return resp

    @asynccontextmanager
    async def fake(*_a, **_k):
        yield _Client()

    monkeypatch.setattr(identity_main, "get_client_insecure", fake)
    monkeypatch.setattr(identity_main, "get_client", fake)
    return calls


def _test(client, config):
    return client.post("/api/auth/test-connection", json={"service": "HomeHub", "config": config})


def test_a_homehub_answers_on_health_with_no_credential_sent(admin_client, monkeypatch):
    calls = _patch(monkeypatch, payload={"status": "ok", "service": "homehub", "entries": 4, "loaded": 3,
                                         "entities": 120})
    r = _test(admin_client, {"homehub_url": HUB + "/"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "SUCCESS" and "Connected to HomeHub" in data["message"]
    assert "3 integrations loaded" in data["message"]
    assert [c["url"] for c in calls] == [f"{HUB}/health"]
    assert "headers" not in calls[0] and "auth" not in calls[0]


def test_something_that_is_not_a_homehub_is_an_error(admin_client, monkeypatch):
    _patch(monkeypatch, payload={"status": "ok", "service": "geo"})
    data = _test(admin_client, {"homehub_url": HUB}).json()
    assert data["status"] == "ERROR" and "not a HomeHub" in data["message"]


def test_a_failing_address_is_an_error(admin_client, monkeypatch):
    _patch(monkeypatch, status=404, payload=None)
    data = _test(admin_client, {"homehub_url": HUB}).json()
    assert data["status"] == "ERROR" and "404" in data["message"]


def test_the_address_is_required_and_must_be_http(admin_client, monkeypatch):
    calls = _patch(monkeypatch, payload={"service": "homehub"})
    assert _test(admin_client, {"homehub_url": ""}).json()["status"] == "ERROR"
    data = _test(admin_client, {"homehub_url": "hub.example.test"}).json()
    assert data["status"] == "ERROR" and "http" in data["message"]
    assert calls == []


def test_only_an_admin_may_test_homehub(member_client, monkeypatch):
    calls = _patch(monkeypatch, payload={"service": "homehub"})
    r = _test(member_client, {"homehub_url": HUB})
    assert r.status_code == 403
    assert calls == []
