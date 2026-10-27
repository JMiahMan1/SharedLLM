"""The seeded administrator password has to be visible where a parent looks.

Read live from the household database on 2026-10-26: the `default` account still
signed in with `DEFAULT_ADMIN_PASSWORD`, an eight-character word from the
placeholder list, and the login page is reachable from the public internet. The
startup log said so; a log line nobody reads is not a warning. The account list
now carries `has_default_password` and the Admin page shows a chip.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity.main import (
    _PLACEHOLDER_PASSWORD_CACHE,
    app,
    get_session,
    require_admin_or_internal,
)
from services.identity.models import User
from services.identity.seed import hash_password

SEEDED = "changeme"


@pytest.fixture(name="session")
def session_fixture(monkeypatch):
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", SEEDED)
    _PLACEHOLDER_PASSWORD_CACHE.clear()
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
                password_hash=hash_password(SEEDED),
            )
        )
        session.add(
            User(username="alice", api_key="key-alice", password_hash=hash_password("a-real-one"))
        )
        session.add(User(username="kaleb", api_key="key-kaleb"))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    app.dependency_overrides[require_admin_or_internal] = lambda: True
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_the_account_list_marks_the_seeded_password(client):
    listed = {
        row["username"]: row["has_default_password"] for row in client.get("/api/users").json()
    }
    assert listed["default"] is True
    assert listed["alice"] is False
    # No password at all is a different problem, reported separately.
    assert listed["kaleb"] is False


def test_changing_the_password_clears_the_mark(client, session):
    user = session.exec(select(User).where(User.username == "default")).one()
    user.password_hash = hash_password("a-real-one")
    session.add(user)
    session.commit()

    listed = {
        row["username"]: row["has_default_password"] for row in client.get("/api/users").json()
    }
    assert listed["default"] is False
