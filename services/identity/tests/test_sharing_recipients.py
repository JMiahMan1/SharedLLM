"""Tests for `GET /api/users/sharing-recipients`.

Written around the non-admin caller, because that is the whole point of the
endpoint: `GET /api/users` is admin-only, so before this existed the sharing
picker rendered zero people for every normal user and "Everyone" was the only
selectable audience -- precisely inverting the consent model.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_user(session: Session, username: str, *, is_admin: bool = False) -> User:
    user = User(username=username, display_name=username.title(), is_admin=is_admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(name="client")
def client_fixture(session: Session):
    """A client whose authenticated caller is a NON-ADMIN, by default.

    Every test here runs as a normal user on purpose. The bug this endpoint
    fixes was only ever visible to a non-admin, so a suite that defaults to an
    admin would have kept the regression alive.
    """
    main.engine = session.bind
    assert main.engine is not None
    SQLModel.metadata.create_all(main.engine)

    jeremiah = _make_user(session, "jeremiah", is_admin=True)
    _make_user(session, "michele")
    session.commit()

    app.dependency_overrides[require_api_key] = lambda: session.exec(
        select(User).where(User.username == "michele")
    ).first()
    app.dependency_overrides[require_internal] = lambda: True

    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


def test_a_normal_user_can_list_recipients(client: TestClient):
    """The regression: this used to 403 for anyone who was not an admin."""
    resp = client.get("/api/users/sharing-recipients")
    assert resp.status_code == 200
    names = {row["username"] for row in resp.json()}
    assert "jeremiah" in names


def test_the_caller_is_excluded(client: TestClient):
    """You cannot grant yourself anything, so you are not a recipient."""
    rows = client.get("/api/users/sharing-recipients").json()
    assert "michele" not in {row["username"] for row in rows}


def test_only_identity_fields_are_returned(client: TestClient):
    """Least privilege: username + display name, and nothing else.

    `UserRead` carries integration URLs, credential fields, the voice
    fingerprint and the raw API key. None of that belongs in a share picker.
    """
    rows = client.get("/api/users/sharing-recipients").json()
    assert rows
    for row in rows:
        assert set(row) == {"username", "display_name"}


def test_admin_can_also_list_them(client: TestClient, session: Session):
    """An admin is an ordinary recipient too -- the carve-out is about reading
    others' *data*, not about this directory."""
    admin = session.exec(select(User).where(User.username == "jeremiah")).first()
    app.dependency_overrides[require_api_key] = lambda: admin
    resp = client.get("/api/users/sharing-recipients")
    assert resp.status_code == 200
    names = {row["username"] for row in resp.json()}
    assert "michele" in names
    assert "jeremiah" not in names


def test_unauthenticated_is_rejected(client: TestClient):
    app.dependency_overrides.pop(require_api_key, None)
    assert client.get("/api/users/sharing-recipients").status_code == 401


def test_display_name_falls_back_to_username(session: Session):
    """A blank display name must not render as an empty chip."""
    main.engine = session.bind
    SQLModel.metadata.create_all(main.engine)
    _make_user(session, "michele")
    blank = User(username="anon", display_name="")
    session.add(blank)
    session.commit()

    caller = session.exec(select(User).where(User.username == "michele")).first()
    app.dependency_overrides[require_api_key] = lambda: caller
    app.dependency_overrides[require_internal] = lambda: True
    try:
        rows = TestClient(app).get("/api/users/sharing-recipients").json()
        anon = next(r for r in rows if r["username"] == "anon")
        assert anon["display_name"] == "anon"
    finally:
        app.dependency_overrides = {}


def test_it_does_not_leak_the_admin_user_directory(client: TestClient):
    """Guard against widening this into `GET /api/users` by accident.

    A non-admin must not be able to read the full user list through this
    route -- that is the thing it exists to avoid needing.
    """
    resp = client.get("/api/users/sharing-recipients")
    assert resp.status_code == 200
    # The admin-only route still refuses the same caller. It answers 401 rather
    # than 403 because `require_admin_or_internal` authenticates from the
    # Authorization header itself, independently of the `require_api_key`
    # override this fixture installs -- so the invariant to assert is simply
    # "not readable", not a specific status.
    assert client.get("/api/users").status_code != 200
    # And nothing in the narrow response carries an admin flag or credential.
    assert all("is_admin" not in row for row in resp.json())