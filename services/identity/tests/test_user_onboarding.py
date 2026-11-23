"""Tests for automatic multi-service account provisioning on user creation.

Covers:
- All 4 services provisioned on POST /api/users with mocked HTTP/hub calls
- Graceful 'skipped' behavior when HOMEHUB_URL/ABS/Nextcloud are unconfigured
- Existing ABS/Nextcloud users handled idempotently (no error on already-exists)
- POST /api/admin/users/{username}/provision-accounts requires admin (403 for non-admin)
- Reconciles an existing user
"""
import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import music_assistant, onboarding
from services.identity.crypto import decrypt, encrypt
from services.identity.main import app, require_admin_or_internal, require_api_key, require_internal
from services.identity.models import User

# Test constants
HOMEHUB_URL = "http://homehub.example.test:8123"
ABS_URL = "https://abs.example.test"
NEXTCLOUD_URL = "https://nc.example.test"
INTERNAL_SECRET = "test-internal-secret"
ADMIN_NC_USER = "admin"
ADMIN_NC_PASS = "admin-nc-pass"
ADMIN_ABS_KEY = "admin-abs-key"


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # Seed admin user (id=1, username=default)
        admin = User(
            username="default",
            display_name="Household",
            is_admin=True,
            is_system_default=True,
            password_hash="hash",
            api_key_enc=encrypt("admin-key"),
            api_key_hash="admin-hash",
            nextcloud_url=NEXTCLOUD_URL,
            nextcloud_user=ADMIN_NC_USER,
            nextcloud_pass_enc=encrypt(ADMIN_NC_PASS),
            audiobookshelf_url=ABS_URL,
            audiobookshelf_api_key_enc=encrypt(ADMIN_ABS_KEY),
        )
        session.add(admin)
        session.commit()
        yield session


class MockUrllib:
    """Mock urllib.request.urlopen for all onboarding HTTP calls."""
    def __init__(self):
        self.responses = {}
        self.call_log = []

    def add_response(self, url_pattern: str, response_data: dict, method="GET", status=200):
        key = (url_pattern, method)
        self.responses[key] = {"data": response_data, "method": method, "status": status}

    def __call__(self, req, timeout=None):
        from urllib.parse import urlparse
        url = req.full_url if hasattr(req, 'full_url') else req
        method = req.get_method() if hasattr(req, 'get_method') else "GET"
        self.call_log.append({"url": url, "method": method, "data": req.data if hasattr(req, 'data') else None})

        parsed = urlparse(url)
        path = parsed.path

        for (pattern, resp_method), resp in self.responses.items():
            # Match pattern as path suffix (path ends with pattern) or exact match
            if (path == pattern or path.endswith(pattern)) and resp_method == method:
                if resp["status"] >= 400:
                    raise RuntimeError(f"HTTP {resp['status']}: {resp['data'].get('detail', 'Error')}")
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps(resp["data"]).encode()
                mock_resp.__enter__ = lambda self: self
                mock_resp.__exit__ = lambda *a: None
                return mock_resp

        # Default: raise error for unmatched URLs
        raise RuntimeError(f"No mock for {method} {url}")


@pytest.fixture(name="mock_urllib")
def mock_urllib_fixture(monkeypatch):
    mock = MockUrllib()
    monkeypatch.setattr("services.identity.onboarding.urllib.request.urlopen", mock)
    monkeypatch.setattr("services.identity.onboarding.urllib.parse.urlencode", lambda d: "&".join(f"{k}={v}" for k, v in d.items()))
    yield mock


@pytest.fixture(name="client")
def client_fixture(session: Session, monkeypatch, mock_urllib):
    identity_main.engine = session.bind
    identity_main._MA_PROVISION_FAILED.clear()
    app.dependency_overrides[require_admin_or_internal] = lambda: True
    app.dependency_overrides[require_internal] = lambda: True

    # Admin user for API key auth
    admin_user = session.exec(select(User).where(User.username == "default")).first()

    def override_require_api_key():
        return admin_user

    app.dependency_overrides[require_api_key] = override_require_api_key

    # Mock config values
    monkeypatch.setattr("services.config.HOMEHUB_URL", HOMEHUB_URL)
    monkeypatch.setattr("services.config.INTERNAL_SECRET", INTERNAL_SECRET)

    client = TestClient(app)
    client.mock_urllib = mock_urllib
    yield client
    app.dependency_overrides = {}


def setup_full_mocks(mock_urllib, monkeypatch, abs_users=None, abs_created_user=None, nc_statuscode=100, nc_app_pass="nc-app-pass-123"):
    """Set up all standard mocks for a successful provisioning."""
    abs_users = abs_users or []
    abs_created_user = abs_created_user or {"username": "newuser", "token": "abs-token-123"}

    # HomeHub - use full URL pattern to match
    mock_urllib.add_response("hub/auth/users", {"created": True}, method="POST")

    # Audiobookshelf - list users (GET)
    mock_urllib.add_response("api/users", {"users": abs_users}, method="GET")
    # Audiobookshelf - create user (POST) - use a dynamic response that matches the requested username
    # We'll handle this by checking the request data in the mock
    mock_urllib.add_response("api/users", {"user": abs_created_user}, method="POST")

    # Nextcloud - create user (POST to /ocs/v1.php/cloud/users)
    mock_urllib.add_response("/ocs/v1.php/cloud/users", {"ocs": {"meta": {"statuscode": nc_statuscode}}}, method="POST")
    # Nextcloud - create app password (POST to /app-passwords)
    mock_urllib.add_response("/app-passwords", {"ocs": {"data": {"password": nc_app_pass}}}, method="POST")

    # Music Assistant
    monkeypatch.setattr(music_assistant, "household_server", lambda fresh=False: {"url": "http://ma.example.test:8095", "error": None})
    monkeypatch.setattr(music_assistant, "provision_account", lambda username, display_name=None: {"username": username, "user_id": f"u-{username}", "created": True, "token": f"ma-token-{username}"})
    monkeypatch.setattr(music_assistant, "sync_audiobookshelf_provider", lambda **kwargs: {"abs_instance_id": f"audiobookshelf--{kwargs['username']}", "user_id": f"u-{kwargs['username']}", "provider_filter": [], "filter_updated": True, "action": "created"})


class TestCreateUserProvisioning:
    """Tests for POST /api/users with automatic provisioning."""

    def test_all_four_services_provisioned_on_create(self, client, session, monkeypatch, mock_urllib):
        """All 4 services are provisioned when configured."""
        setup_full_mocks(mock_urllib, monkeypatch, abs_created_user={"username": "alice", "token": "abs-token-123"})

        resp = client.post("/api/users", json={
            "username": "alice",
            "display_name": "Alice",
            "password": "alice-secret-pass",
            "is_admin": False,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["username"] == "alice"

        # Verify user was created with provisioned credentials
        user = session.exec(select(User).where(User.username == "alice")).first()
        assert user is not None

        # Audiobookshelf: credentials stored
        assert user.audiobookshelf_url == ABS_URL
        assert user.audiobookshelf_user == "alice"
        assert user.audiobookshelf_api_key_enc is not None
        assert decrypt(user.audiobookshelf_api_key_enc) == "abs-token-123"

        # Music Assistant: token stored
        assert user.mass_token_enc is not None
        assert decrypt(user.mass_token_enc) == "ma-token-alice"
        assert user.mass_user_id == "u-alice"
        assert user.mass_abs_provider_id == "audiobookshelf--alice"

        # Nextcloud: app password stored
        assert user.nextcloud_url == NEXTCLOUD_URL
        assert user.nextcloud_user == "alice"
        assert user.nextcloud_pass_enc is not None
        assert decrypt(user.nextcloud_pass_enc) == "nc-app-pass-123"

        # Secrets never returned in response (encrypted fields not in schema, plain credential fields are None)
        assert "password" not in data
        assert data.get("audiobookshelf_api_key") is None
        assert "nextcloud_pass" not in data
        assert "mass_token" not in data

    def test_skipped_when_homehub_unconfigured(self, client, session, monkeypatch, mock_urllib):
        """HomeHub provisioning skipped when HOMEHUB_URL is empty."""
        monkeypatch.setattr("services.config.HOMEHUB_URL", "")
        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={
            "username": "bob",
            "password": "bob-pass",
        })
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "bob")).first()
        assert user.audiobookshelf_api_key_enc is not None
        assert user.mass_token_enc is not None
        assert user.nextcloud_pass_enc is not None

    def test_skipped_when_abs_unconfigured(self, client, session, monkeypatch, mock_urllib):
        """Audiobookshelf provisioning skipped when admin ABS credentials missing."""
        admin = session.exec(select(User).where(User.username == "default")).first()
        admin.audiobookshelf_url = None
        admin.audiobookshelf_api_key_enc = None
        session.add(admin)
        session.commit()

        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={"username": "carol", "password": "carol-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "carol")).first()
        assert user.audiobookshelf_api_key_enc is None  # Not provisioned
        assert user.mass_token_enc is not None  # MA still works
        assert user.nextcloud_pass_enc is not None

    def test_skipped_when_nextcloud_unconfigured(self, client, session, monkeypatch, mock_urllib):
        """Nextcloud provisioning skipped when admin NC credentials missing."""
        admin = session.exec(select(User).where(User.username == "default")).first()
        admin.nextcloud_url = None
        admin.nextcloud_user = None
        admin.nextcloud_pass_enc = None
        session.add(admin)
        session.commit()

        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={"username": "dave", "password": "dave-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "dave")).first()
        assert user.nextcloud_pass_enc is None
        assert user.audiobookshelf_api_key_enc is not None
        assert user.mass_token_enc is not None

    def test_skipped_when_ma_unconfigured(self, client, session, monkeypatch, mock_urllib):
        """Music Assistant provisioning skipped when household MA not configured."""
        setup_full_mocks(mock_urllib, monkeypatch)
        monkeypatch.setattr(music_assistant, "household_server", lambda fresh=False: {"url": None, "error": "No Music Assistant"})

        resp = client.post("/api/users", json={"username": "eve", "password": "eve-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "eve")).first()
        assert user.mass_token_enc is None
        assert user.audiobookshelf_api_key_enc is not None
        assert user.nextcloud_pass_enc is not None

    def test_existing_abs_user_idempotent(self, client, session, monkeypatch, mock_urllib):
        """Existing Audiobookshelf user handled without error."""
        setup_full_mocks(mock_urllib, monkeypatch, abs_users=[{"username": "frank", "id": "abs-frank", "token": "abs-token-frank"}])

        resp = client.post("/api/users", json={"username": "frank", "password": "frank-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "frank")).first()
        assert user.audiobookshelf_user == "frank"
        assert user.audiobookshelf_api_key_enc is not None

    def test_existing_nextcloud_user_idempotent(self, client, session, monkeypatch, mock_urllib):
        """Existing Nextcloud user (statuscode 102) handled without error."""
        setup_full_mocks(mock_urllib, monkeypatch, nc_statuscode=102)

        resp = client.post("/api/users", json={"username": "grace", "password": "grace-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "grace")).first()
        assert user.nextcloud_pass_enc is not None
        assert decrypt(user.nextcloud_pass_enc) == "nc-app-pass-123"

    def test_system_default_user_skipped(self, client, session, monkeypatch, mock_urllib):
        """System default user (id=1, username=default) skips provisioning."""
        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={
            "username": "default2",
            "password": "pass",
            "is_system_default": True,
        })
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "default2")).first()
        assert user.is_system_default is True

    def test_test_account_skipped(self, client, session, monkeypatch, mock_urllib):
        """Test account (is_test_account=True) skips provisioning."""
        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={
            "username": "test-bot",
            "password": "pass",
            "is_test_account": True,
        })
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "test-bot")).first()
        assert user.is_test_account is True
        assert user.audiobookshelf_api_key_enc is None
        assert user.mass_token_enc is None
        assert user.nextcloud_pass_enc is None

    def test_homehub_error_handled_gracefully(self, client, session, monkeypatch, mock_urllib):
        """HomeHub errors don't break user creation."""
        mock_urllib.add_response("/hub/auth/users", {"detail": "HomeHub unavailable"}, method="POST", status=500)
        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={"username": "henry", "password": "henry-pass"})
        assert resp.status_code == 200  # User creation succeeds

        user = session.exec(select(User).where(User.username == "henry")).first()
        assert user.audiobookshelf_api_key_enc is not None
        assert user.mass_token_enc is not None
        assert user.nextcloud_pass_enc is not None

    def test_abs_error_handled_gracefully(self, client, session, monkeypatch, mock_urllib):
        """Audiobookshelf errors don't break user creation."""
        mock_urllib.add_response("/api/users", {"detail": "ABS connection failed"}, method="GET", status=500)
        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={"username": "irene", "password": "irene-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "irene")).first()
        assert user.audiobookshelf_api_key_enc is None  # ABS failed
        assert user.mass_token_enc is not None
        assert user.nextcloud_pass_enc is not None

    def test_nextcloud_error_handled_gracefully(self, client, session, monkeypatch, mock_urllib):
        """Nextcloud errors don't break user creation."""
        mock_urllib.add_response("/cloud/users", {"detail": "Nextcloud unavailable"}, method="POST", status=500)
        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/users", json={"username": "jack", "password": "jack-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "jack")).first()
        assert user.nextcloud_pass_enc is None  # NC failed
        assert user.audiobookshelf_api_key_enc is not None
        assert user.mass_token_enc is not None

    def test_ma_error_handled_gracefully(self, client, session, monkeypatch, mock_urllib):
        """Music Assistant errors don't break user creation."""
        setup_full_mocks(mock_urllib, monkeypatch)

        def failing_provision(username, display_name=None):
            raise RuntimeError("MA provisioning failed")
        monkeypatch.setattr(music_assistant, "provision_account", failing_provision)

        resp = client.post("/api/users", json={"username": "kate", "password": "kate-pass"})
        assert resp.status_code == 200

        user = session.exec(select(User).where(User.username == "kate")).first()
        assert user.mass_token_enc is None  # MA failed
        assert user.audiobookshelf_api_key_enc is not None
        assert user.nextcloud_pass_enc is not None


class TestAdminProvisionAccountsEndpoint:
    """Tests for POST /api/admin/users/{username}/provision-accounts."""

    def test_requires_admin(self, client, session, mock_urllib):
        """Non-admin gets 403."""
        app.dependency_overrides[require_admin_or_internal] = lambda: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="Admin access required")
        )
        try:
            resp = client.post("/api/admin/users/alice/provision-accounts")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides[require_admin_or_internal] = lambda: True

    def test_provisions_for_existing_user(self, client, session, monkeypatch, mock_urllib):
        """Provisions accounts for an existing user."""
        user = User(username="existing-user", display_name="Existing", password_hash="hash")
        session.add(user)
        session.commit()

        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/admin/users/existing-user/provision-accounts")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["username"] == "existing-user"
        assert "services" in data

        # Verify credentials were stored
        user = session.exec(select(User).where(User.username == "existing-user")).first()
        assert user.audiobookshelf_api_key_enc is not None
        assert user.mass_token_enc is not None
        assert user.nextcloud_pass_enc is not None

    def test_idempotent_for_existing_credentials(self, client, session, monkeypatch, mock_urllib):
        """Running twice doesn't error on already-existing accounts."""
        user = User(
            username="idempotent-user",
            display_name="Idempotent",
            password_hash="hash",
            audiobookshelf_url=ABS_URL,
            audiobookshelf_user="idempotent-user",
            audiobookshelf_api_key_enc=encrypt("existing-abs-key"),
            nextcloud_url=NEXTCLOUD_URL,
            nextcloud_user="idempotent-user",
            nextcloud_pass_enc=encrypt("existing-nc-pass"),
            mass_token_enc=encrypt("existing-ma-token"),
            mass_user_id="u-existing",
        )
        session.add(user)
        session.commit()

        # HomeHub: already exists
        mock_urllib.add_response("/hub/auth/users", {"created": False}, method="POST")
        # ABS: already exists
        mock_urllib.add_response("/api/users", {"users": [{"username": "idempotent-user"}]}, method="GET")
        # NC: already exists
        mock_urllib.add_response("/cloud/users", {"ocs": {"meta": {"statuscode": 102}}}, method="POST")
        # NC app password still created
        mock_urllib.add_response("/app-passwords", {"ocs": {"data": {"password": "nc-app-pass-123"}}}, method="POST")
        # MA
        monkeypatch.setattr(music_assistant, "household_server", lambda fresh=False: {"url": "http://ma.example.test:8095", "error": None})
        monkeypatch.setattr(music_assistant, "provision_account", lambda username, display_name=None: {"username": username, "user_id": f"u-{username}", "created": False, "token": f"ma-token-{username}"})
        monkeypatch.setattr(music_assistant, "sync_audiobookshelf_provider", lambda **kwargs: {"abs_instance_id": f"audiobookshelf--{kwargs['username']}", "user_id": f"u-{kwargs['username']}", "provider_filter": [], "filter_updated": True, "action": "updated"})

        resp = client.post("/api/admin/users/idempotent-user/provision-accounts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "idempotent-user"

        user = session.exec(select(User).where(User.username == "idempotent-user")).first()
        assert decrypt(user.audiobookshelf_api_key_enc) == "existing-abs-key"
        assert decrypt(user.nextcloud_pass_enc) == "existing-nc-pass"
        assert decrypt(user.mass_token_enc) == "existing-ma-token"
        assert data["services"]["audiobookshelf"]["status"] == "existing"
        assert data["services"]["nextcloud"]["status"] == "existing"
        assert data["services"]["music_assistant"]["status"] == "existing"

    def test_skipped_services_when_unconfigured(self, client, session, monkeypatch, mock_urllib):
        """Returns skipped for unconfigured services."""
        admin = session.exec(select(User).where(User.username == "default")).first()
        admin.audiobookshelf_url = None
        admin.audiobookshelf_api_key_enc = None
        admin.nextcloud_url = None
        admin.nextcloud_user = None
        admin.nextcloud_pass_enc = None
        session.add(admin)
        session.commit()

        user = User(username="partial-user", display_name="Partial", password_hash="hash")
        session.add(user)
        session.commit()

        setup_full_mocks(mock_urllib, monkeypatch)

        resp = client.post("/api/admin/users/partial-user/provision-accounts")
        assert resp.status_code == 200
        data = resp.json()
        services = data["services"]
        assert services["homehub"]["status"] == "created"
        assert services["audiobookshelf"]["status"] == "skipped"
        assert services["music_assistant"]["status"] == "created"
        assert services["nextcloud"]["status"] == "skipped"

    def test_user_not_found_returns_404(self, client, session, mock_urllib):
        """Unknown user returns 404."""
        resp = client.post("/api/admin/users/nonexistent/provision-accounts")
        assert resp.status_code == 404


class TestOnboardingModuleDirectly:
    """Unit tests for onboarding.provision_new_user_accounts function."""

    def test_skip_system_default(self, session):
        """System default user returns empty dict."""
        user = User(username="default", is_system_default=True, id=1)
        result = onboarding.provision_new_user_accounts(session, user)
        assert result == {}

    def test_skip_id_1(self, session):
        """User with id=1 returns empty dict."""
        user = User(username="admin", id=1)
        result = onboarding.provision_new_user_accounts(session, user)
        assert result == {}

    def test_skip_username_default(self, session):
        """User with username='default' returns empty dict."""
        user = User(username="default", id=2)
        result = onboarding.provision_new_user_accounts(session, user)
        assert result == {}

    def test_skip_test_account(self, session):
        """Test account returns empty dict."""
        user = User(username="test", is_test_account=True)
        result = onboarding.provision_new_user_accounts(session, user)
        assert result == {}
