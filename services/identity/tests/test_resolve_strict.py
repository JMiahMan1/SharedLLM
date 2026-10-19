"""Strict resolution at ``POST /api/resolve``.

The old resolver fell through to user 1 (the system default, an administrator)
whenever nothing matched. A made-up API key, an unknown username, or an
unassigned device therefore resolved to the admin account. These tests pin the
replacement contract:

* an identifier or credential that is presented but does not match is a 401;
* a request with no identifier gets no identity unless it explicitly asks to
  be ``anonymous`` (nothing) or ``household`` (shared home services, no admin
  powers and no personal accounts);
* a real credential still resolves to its owner.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.crypto import encrypt
from services.identity.main import app, get_session, require_internal
from services.identity.models import DeviceAssignment, User


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(
                username="default",
                is_admin=True,
                is_system_default=True,
                api_key="key-default",
                ha_url="http://home-assistant.local",
                ha_token_enc=encrypt("household-ha-token"),
                github_user="default-gh",
                github_token_enc=encrypt("default-personal-github-token"),
            )
        )
        session.add(User(username="alice", is_admin=False, api_key="key-alice"))
        session.add(User(username="bob", is_admin=False))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_internal] = lambda: True
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _alice(session: Session) -> User:
    return session.exec(select(User).where(User.username == "alice")).first()


def _bob(session: Session) -> User:
    return session.exec(select(User).where(User.username == "bob")).first()


def _resolve(client: TestClient, body: dict):
    return client.post("/api/resolve", json=body)


def test_a_real_key_resolves_to_its_owner(client: TestClient):
    resp = _resolve(client, {"api_key": "key-alice"})
    assert resp.status_code == 200
    assert resp.json()["user"] == "alice"
    assert resp.json()["is_admin"] is False


def test_an_unknown_api_key_is_rejected_not_upgraded_to_admin(client: TestClient):
    resp = _resolve(client, {"api_key": "totally-bogus-key"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid credentials"}
    assert "default" not in resp.text
    assert "is_admin" not in resp.text


def test_an_empty_presented_api_key_is_rejected(client: TestClient):
    """An empty credential was still presented; it must not mean anonymous."""
    resp = _resolve(client, {"api_key": ""})
    assert resp.status_code == 401


def test_an_unknown_username_is_rejected(client: TestClient):
    resp = _resolve(client, {"rag_user": "nobody"})
    assert resp.status_code == 401
    assert "default" not in resp.text


def test_an_unknown_voice_id_is_rejected(client: TestClient):
    resp = _resolve(client, {"voice_id": "nobody"})
    assert resp.status_code == 401


def test_an_unknown_user_id_is_rejected(client: TestClient):
    resp = _resolve(client, {"user_id": 9999})
    assert resp.status_code == 401


def test_an_unassigned_device_id_is_rejected(client: TestClient):
    resp = _resolve(client, {"device_id": "not-a-device"})
    assert resp.status_code == 401


def test_a_shared_device_still_uses_the_household_account(
    client: TestClient, session: Session
):
    """A real device used by two people cannot name one, so it falls back.

    This is the single compatibility exception: the device exists, it just does
    not identify a speaker. An unknown device (above) is not a credential and
    does not get the fallback.
    """
    session.add(DeviceAssignment(device_id="shared-tablet", user_id=_alice(session).id))
    session.add(DeviceAssignment(device_id="shared-tablet", user_id=_bob(session).id))
    session.commit()

    resp = _resolve(client, {"device_id": "shared-tablet"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "household"
    assert body["is_admin"] is False
    assert body["ha_token"] == "household-ha-token"


def test_a_device_with_a_single_user_resolves_to_that_user(
    client: TestClient, session: Session
):
    session.add(DeviceAssignment(device_id="alice-phone", user_id=_alice(session).id))
    session.commit()

    resp = _resolve(client, {"device_id": "alice-phone"})
    assert resp.status_code == 200
    assert resp.json()["user"] == "alice"


def test_no_identifier_at_all_is_refused_not_upgraded_to_admin(client: TestClient):
    """Trust alone is not an identity; a credential-less caller must say what it wants."""
    resp = _resolve(client, {})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Identity required"}
    assert "default" not in resp.text
    assert "is_admin" not in resp.text


def test_household_gets_shared_services_but_no_admin_or_personal_accounts(
    client: TestClient,
):
    resp = _resolve(client, {"household": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "household"
    assert body["id"] is None
    assert body["is_admin"] is False
    assert body["api_key"] is None
    assert body["ha_url"] == "http://home-assistant.local"
    assert body["ha_token"] == "household-ha-token"
    assert body["github_user"] is None
    assert body["github_token"] is None
    assert body["shared_credential_owner"] == "default"


def test_anonymous_and_household_are_mutually_exclusive(client: TestClient):
    resp = _resolve(client, {"anonymous": True, "household": True})
    assert resp.status_code == 400
    assert "mutually exclusive" in resp.text


def test_resolve_without_the_internal_secret_never_returns_the_admin(session: Session):
    """The endpoint is internal-only; an anonymous HTTP caller gets no identity."""
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            missing = client.post("/api/resolve", json={})
            wrong = client.post(
                "/api/resolve", json={}, headers={"X-Internal-Secret": "wrong-secret"}
            )
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code in (401, 403)
    assert wrong.status_code in (401, 403)
    for resp in (missing, wrong):
        assert "default" not in resp.text
        assert "is_admin" not in resp.text


def test_anonymous_request_returns_a_non_admin_identity(client: TestClient):
    resp = _resolve(client, {"anonymous": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "anonymous"
    assert body["is_admin"] is False
    assert body["api_key"] is None
    assert body["ha_token"] is None


def test_anonymous_flag_does_not_hide_an_unknown_credential(client: TestClient):
    resp = _resolve(client, {"anonymous": True, "api_key": "totally-bogus-key"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid credentials"}
