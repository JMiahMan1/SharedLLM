import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="
os.environ["DEFAULT_ADMIN_PASSWORD"] = "changeme"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, decrypt, encrypt, require_api_key, require_internal
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
    admin_user.nextcloud_url = "https://cloud.test"
    admin_user.nextcloud_user = "admin"
    admin_user.nextcloud_pass_enc = encrypt("admin-nc-pass")
    session.add(admin_user)
    session.commit()

    app.dependency_overrides[require_api_key] = lambda: admin_user
    app.dependency_overrides[require_internal] = lambda: True

    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


class _Response:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(url, kwargs)


def _install(monkeypatch, handler):
    fake = _FakeClient(handler)
    monkeypatch.setattr(main, "get_client_insecure", lambda: fake)
    return fake


def test_home_assistant_login_flow_stores_token(client, monkeypatch, session: Session):
    session.add(User(username="mom", ha_url="http://ha.test:8123"))
    session.commit()

    def handler(url, kwargs):
        if url.endswith("/auth/login_flow") and kwargs.get("json", {}).get("type") == "credentials":
            return _Response(200, {"result_id": "flow-1"})
        if url.endswith("/auth/login_flow/flow-1"):
            return _Response(200, {"result": "auth-code-1"})
        if url.endswith("/auth/token"):
            assert kwargs["data"]["grant_type"] == "authorization_code"
            return _Response(200, {"access_token": "ha-long-lived-token", "token_type": "Bearer"})
        raise AssertionError(f"unexpected call {url}")

    _install(monkeypatch, handler)

    resp = client.post(
        "/api/users/mom/service-token",
        json={"service": "home_assistant", "password": "typed-once"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    mom = session.exec(select(User).where(User.username == "mom")).first()
    assert decrypt(mom.ha_token_enc) == "ha-long-lived-token"


def test_home_assistant_reports_bad_credentials_without_saving(client, monkeypatch, session: Session):
    session.add(User(username="mom", ha_url="http://ha.test:8123"))
    session.commit()

    _install(monkeypatch, lambda url, kwargs: _Response(400, {"type": "auth_invalid"}))

    resp = client.post(
        "/api/users/mom/service-token",
        json={"service": "home_assistant", "password": "wrong"},
    )
    assert resp.status_code == 400
    assert "did not accept" in resp.json()["detail"] or "rejected" in resp.json()["detail"]

    mom = session.exec(select(User).where(User.username == "mom")).first()
    assert mom.ha_token_enc is None


def test_audiobookshelf_login_stores_token(client, monkeypatch, session: Session):
    session.add(User(username="mom", audiobookshelf_url="https://abs.test"))
    session.commit()

    def handler(url, kwargs):
        assert url.endswith("/login")
        assert kwargs["json"] == {"username": "mom", "password": "typed-once"}
        return _Response(200, {"token": "abs-token"})

    _install(monkeypatch, handler)

    resp = client.post(
        "/api/users/mom/service-token",
        json={"service": "audiobookshelf", "password": "typed-once"},
    )
    assert resp.status_code == 200, resp.text

    mom = session.exec(select(User).where(User.username == "mom")).first()
    assert decrypt(mom.audiobookshelf_api_key_enc) == "abs-token"


def test_nextcloud_app_password_needs_no_user_password(client, monkeypatch, session: Session):
    """Nextcloud is the hands-off case: the admin mints an app password."""
    session.add(User(username="mom"))
    session.commit()

    def handler(url, kwargs):
        assert "/app-passwords" in url and "mom" in url
        return _Response(200, {"ocs": {"meta": {"status": "ok"}, "data": {"password": "nc-app-pw"}}})

    _install(monkeypatch, handler)

    resp = client.post("/api/users/mom/service-token", json={"service": "nextcloud"})
    assert resp.status_code == 200, resp.text

    mom = session.exec(select(User).where(User.username == "mom")).first()
    assert mom.nextcloud_user == "mom"
    assert decrypt(mom.nextcloud_pass_enc) == "nc-app-pw"


def test_nextcloud_requires_configuration_instead_of_guessing(client, session: Session):
    session.add(User(username="mom"))
    session.commit()

    admin_user = session.exec(select(User).where(User.username == "default")).first()
    admin_user.nextcloud_url = None
    admin_user.nextcloud_user = None
    admin_user.nextcloud_pass_enc = None
    session.add(admin_user)
    session.commit()

    resp = client.post("/api/users/mom/service-token", json={"service": "nextcloud"})
    assert resp.status_code == 400
    assert "not configured" in resp.json()["detail"]


def test_unknown_service_is_rejected(client, session: Session):
    session.add(User(username="mom"))
    session.commit()

    resp = client.post("/api/users/mom/service-token", json={"service": "spotify", "password": "x"})
    assert resp.status_code in (400, 422), resp.text
    assert "service must be one of" in resp.json()["detail"]


def test_missing_password_fails_loudly_for_services_that_need_it(client, session: Session):
    session.add(User(username="mom", ha_url="http://ha.test:8123"))
    session.commit()

    resp = client.post("/api/users/mom/service-token", json={"service": "home_assistant"})
    assert resp.status_code in (400, 422), resp.text
    assert "password is required" in resp.json()["detail"]


def test_missing_url_fails_loudly_rather_than_guessing(client, session: Session):
    session.add(User(username="mom"))
    session.commit()

    # The seeded admin carries real service URLs; clear them so an unconfigured
    # service fails here instead of reaching the network.
    admin_user = session.exec(select(User).where(User.username == "default")).first()
    admin_user.audiobookshelf_url = None
    session.add(admin_user)
    session.commit()

    resp = client.post(
        "/api/users/mom/service-token",
        json={"service": "audiobookshelf", "password": "typed-once"},
    )
    assert resp.status_code in (400, 422), resp.text
    assert "not configured" in resp.json()["detail"]


def test_a_user_cannot_set_up_another_users_account(client, session: Session):
    session.add(User(username="mom", is_admin=False))
    session.add(User(username="kiddo", is_admin=False))
    session.commit()

    mom = session.exec(select(User).where(User.username == "mom")).first()
    app.dependency_overrides[require_api_key] = lambda: mom

    resp = client.post(
        "/api/users/kiddo/service-token",
        json={"service": "home_assistant", "password": "x"},
    )
    assert resp.status_code == 403
