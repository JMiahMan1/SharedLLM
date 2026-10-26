"""The account list has to say which accounts cannot sign in.

Observed on the live household: two members imported from Nextcloud had no
password at all, so they were listed, appeared on the family pages, and could
never sign in - the login just answered "invalid username or password". The API
now reports whether an account has a password, and the Admin page shows it.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity.main import (
    app,
    get_session,
    require_admin_or_internal,
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
            User(username="alice", api_key="key-alice", password_hash=hash_password("a-password"))
        )
        # Imported from Nextcloud, never given a password.
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


def test_the_account_list_says_who_has_no_password(client):
    listed = {row["username"]: row["has_password"] for row in client.get("/api/users").json()}
    assert listed["alice"] is True
    assert listed["kaleb"] is False
