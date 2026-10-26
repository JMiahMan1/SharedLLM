"""An admin password reset has to sign the old phone out.

Observed: a parent resetting a child's password left the child's device fully
signed in - it kept its key, kept streaming push, and kept uploading location -
so the reset did not do the one thing it is for. The route now rotates the
session key exactly like changing your own password does.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity.main import (
    app,
    get_session,
    require_admin_or_internal,
    require_internal,
)
from services.identity.models import User
from services.identity.seed import hash_password


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(username="default", is_admin=True, is_system_default=True, api_key="key-default")
        )
        session.add(
            User(
                username="alice",
                is_admin=False,
                api_key="key-alice",
                password_hash=hash_password("old-password"),
            )
        )
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    app.dependency_overrides[require_internal] = lambda: True
    app.dependency_overrides[require_admin_or_internal] = lambda: True
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_resetting_a_password_rotates_the_session_key(client, session):
    assert (
        client.get("/api/internal/validate-api-key", headers={"X-Api-Key": "key-alice"}).status_code
        == 200
    )

    resp = client.post("/api/users/alice/password", json={"new_password": "new-password"})
    assert resp.status_code == 200

    # The device that held the old key is out...
    assert (
        client.get("/api/internal/validate-api-key", headers={"X-Api-Key": "key-alice"}).status_code
        == 401
    )

    # ...and the new password signs in, with a key that is not the old one.
    login = client.post("/api/auth/login", json={"username": "alice", "password": "new-password"})
    assert login.status_code == 200
    issued = login.json()["api_key"]
    assert issued and issued != "key-alice"
