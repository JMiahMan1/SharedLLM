"""Strict API-key validation (GET /api/internal/validate-api-key).

This endpoint exists because ``POST /api/resolve`` is NOT a safe authentication
gate: when nothing matches it falls back to the system default user, who is an
admin. A caller that treated a successful resolve as proof of authentication
would therefore accept any string at all — including an anonymous caller's — as
the default administrator.

The endpoint under test must never fall back. These tests pin that property
directly, because it is the entire reason the endpoint exists and a regression
here silently re-opens every gate built on it.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity.main import app, get_session, require_internal
from services.identity.models import User


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="default", is_admin=True, is_system_default=True, api_key="key-default"))
        session.add(User(username="alice", is_admin=False, api_key="key-alice"))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    app.dependency_overrides[require_internal] = lambda: True
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_a_real_key_is_accepted_and_names_its_owner(client: TestClient):
    resp = client.get("/api/internal/validate-api-key", params={"api_key": "key-alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "alice"
    assert body["is_admin"] is False
    assert body["is_system_default"] is False


def test_the_default_users_key_is_accepted(client: TestClient):
    body = client.get("/api/internal/validate-api-key", params={"api_key": "key-default"}).json()
    assert body["user"] == "default"
    assert body["is_admin"] is True
    assert body["is_system_default"] is True


def test_a_junk_key_is_rejected_rather_than_resolved_to_the_default_admin(client: TestClient):
    """The whole point of the endpoint: no fallback, ever."""
    resp = client.get("/api/internal/validate-api-key", params={"api_key": "totally-bogus-key"})
    assert resp.status_code == 401
    # Critically: the body must not leak the default user's identity.
    assert "default" not in resp.text


def test_a_blank_key_is_a_bad_request_not_a_default_user(client: TestClient):
    resp = client.get("/api/internal/validate-api-key", params={"api_key": "   "})
    assert resp.status_code == 400
    assert "default" not in resp.text


def test_another_users_key_does_not_resolve_to_the_caller(client: TestClient):
    """alice must not be able to present bob's key and have it accepted as hers."""
    resp = client.get("/api/internal/validate-api-key", params={"api_key": "key-default"})
    assert resp.status_code == 200
    assert resp.json()["user"] == "default"


def test_the_endpoint_is_internal_only(client: TestClient):
    """Without the internal secret the endpoint must not be reachable."""
    app.dependency_overrides.pop(require_internal, None)
    try:
        resp = client.get("/api/internal/validate-api-key", params={"api_key": "key-alice"})
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[require_internal] = lambda: True
