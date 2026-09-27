import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="
os.environ["DEFAULT_ADMIN_PASSWORD"] = "changeme"

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


@pytest.fixture(name="client")
def client_fixture(session: Session):
    main.engine = session.bind
    assert main.engine is not None
    SQLModel.metadata.create_all(main.engine)

    from services.identity.seed import seed_from_env

    seed_from_env(session, force=True)

    admin_user = session.exec(select(User).where(User.username == "default")).first()
    assert admin_user is not None
    admin_user.is_admin = True
    session.add(admin_user)
    session.commit()

    app.dependency_overrides[require_api_key] = lambda: admin_user
    app.dependency_overrides[require_internal] = lambda: True

    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload


class _FakeClient:
    """Answers the four onboarding scans by URL shape."""

    def __init__(self, routes):
        self.routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        for fragment, (status, payload) in self.routes.items():
            if fragment in url:
                return _FakeResponse(status, payload)
        return _FakeResponse(404, {})

    post = get


def _install(monkeypatch, routes):
    monkeypatch.setattr(main, "get_client", lambda: _FakeClient(routes))


def _configure_mailcow(session: Session):
    """Mailcow is configuration, not a built-in default: the test must set it."""
    from services.identity.main import encrypt

    user = session.exec(select(User).where(User.username == "default")).first()
    user.mailcow_url = "https://mail.sumemail.com"
    user.mailcow_api_key_enc = encrypt("mailcow-test-key")
    session.add(user)
    session.commit()


HA_STATES = [
    {
        "entity_id": "person.mom",
        "attributes": {"friendly_name": "Mom"},
    },
    {
        "entity_id": "person.kiddo",
        "attributes": {"friendly_name": "Kiddo"},
    },
]

NC_USERS = {"ocs": {"meta": {"status": "ok"}, "data": {"users": ["mom", "kiddo"]}}}
NC_MOM = {"ocs": {"data": {"display-name": "Mom", "email": "mom@sumemail.com"}}}

ABS_USERS = {
    "users": [
        {"username": "mom", "name": "Mom"},
        {"username": "kiddo", "name": "Kiddo"},
    ]
}

MAILBOXES = {
    "items": [
        {"local_part": "mom", "email": "mom@sumemail.com", "name": "Mom", "active": True},
        {"local_part": "kiddo", "email": "kiddo@sumemail.com", "name": "Kiddo", "active": True},
        {"local_part": "admin", "email": "admin@sumemail.com", "name": "Admin", "active": True},
    ]
}


def test_discovery_merges_all_four_sources(client, monkeypatch, session: Session):
    _configure_mailcow(session)
    _install(
        monkeypatch,
        {
            "/api/states": (200, HA_STATES),
            "cloud/users?": (200, NC_USERS),
            "cloud/users/mom": (200, NC_MOM),
            "/api/users": (200, ABS_USERS),
            "/api/v1/mailbox": (200, MAILBOXES),
        },
    )

    resp = client.get("/api/auth/discover")
    assert resp.status_code == 200
    users = {u["username"]: u for u in resp.json()["users"]}

    # The admin account already exists in Jarvis, so it is never offered.
    assert "admin" not in users
    assert set(users) == {"mom", "kiddo"}

    mom = users["mom"]
    assert mom["source"] == "Home Assistant + Nextcloud + Audiobookshelf + Mailcow"
    assert mom["ha_person_id"] == "person.mom"
    assert mom["nc_username"] == "mom"
    assert mom["abs_username"] == "mom"
    assert mom["mail_address"] == "mom@sumemail.com"
    # A real display name beats the login handle, and the email comes from
    # Nextcloud when it is there.
    assert mom["display_name"] == "Mom"
    assert mom["email"] == "mom@sumemail.com"


def test_discovery_uses_mailcow_address_when_nextcloud_has_no_email(client, monkeypatch, session: Session):
    _configure_mailcow(session)
    _install(
        monkeypatch,
        {
            "/api/v1/mailbox": (200, MAILBOXES),
        },
    )

    resp = client.get("/api/auth/discover")
    users = {u["username"]: u for u in resp.json()["users"]}

    assert users["mom"]["source"] == "Mailcow"
    assert users["mom"]["email"] == "mom@sumemail.com"
    assert users["mom"]["display_name"] == "Mom"


def test_discovery_reports_source_failures_without_failing(client, monkeypatch, session: Session):
    _configure_mailcow(session)
    _install(
        monkeypatch,
        {
            "/api/states": (200, HA_STATES),
            "/api/v1/mailbox": (500, {}),
        },
    )

    resp = client.get("/api/auth/discover")
    assert resp.status_code == 200
    body = resp.json()
    assert any("Mailcow" in w for w in body["warnings"])
    # Home Assistant still produced results despite Mailcow failing.
    assert {u["username"] for u in body["users"]} == {"mom", "kiddo"}


def test_discovery_skips_existing_jarvis_users(client, monkeypatch, session: Session):
    _configure_mailcow(session)
    session.add(User(username="mom", is_admin=False))
    session.commit()

    _install(
        monkeypatch,
        {
            "/api/states": (200, HA_STATES),
            "/api/v1/mailbox": (200, MAILBOXES),
        },
    )

    resp = client.get("/api/auth/discover")
    assert {u["username"] for u in resp.json()["users"]} == {"kiddo"}


def test_mailcow_fields_round_trip_through_creation(client):
    resp = client.post(
        "/api/users",
        json={
            "username": "sarah",
            "password": "hunter2hunter2",
            "mailcow_url": "https://mail.sumemail.com",
            "mailcow_api_key": "mc-key-123",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    created = resp.json()
    assert created.get("username") == "sarah"

    # The API key is stored encrypted and never returned in plaintext.
    assert "mc-key-123" not in resp.text


def test_discovery_warns_instead_of_guessing_when_mailcow_is_unset(client, monkeypatch):
    """No hardcoded Mailcow host: unconfigured means a visible warning, not a
    silent scan of a guessed address."""
    _install(monkeypatch, {"/api/states": (200, HA_STATES)})

    body = client.get("/api/auth/discover").json()

    assert any("mailcow_url" in w for w in body["warnings"])
    assert {u["username"] for u in body["users"]} == {"mom", "kiddo"}
    assert all("Mailcow" not in u["source"] for u in body["users"])
