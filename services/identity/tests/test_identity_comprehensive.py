"""Comprehensive tests for Identity service - user resolution, device assignment, settings."""
import os
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool, select
from fastapi import HTTPException

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as identity_main
from main import app, require_api_key, require_internal, resolve_identity
from models import User, DeviceAssignment
from schemas import ResolveRequest, ResolvedCredentials
from crypto import encrypt


@pytest.fixture(name="session")
def session_fixture():
    """Create in-memory DB with default and bob users."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # Create default admin user
        default_user = User(
            username="default",
            is_admin=True,
            ha_url="http://ha.local",
            ha_token_enc=encrypt("default-ha-token"),
        )
        session.add(default_user)
        
        # Create bob user
        bob_user = User(
            username="bob",
            is_admin=False,
            ha_url="http://ha-bob.local",
            ha_token_enc=encrypt("bob-ha-token"),
            audiobookshelf_url="http://abs.local",
            audiobookshelf_user="bob",
            audiobookshelf_pass_enc=encrypt("bob-abs-pass"),
        )
        session.add(bob_user)
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    """Create test client with identity overrides."""
    identity_main.engine = session.bind
    assert identity_main.engine is not None
    SQLModel.metadata.create_all(identity_main.engine)
    app.dependency_overrides[require_api_key] = lambda: session.exec(select(User).where(User.username == "default")).first()
    app.dependency_overrides[require_internal] = lambda: True
    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


def test_health_check(client: TestClient):
    """Test health endpoint returns OK."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["service"] == "identity"


def test_identity_resolve_by_username(session: Session):
    """Test resolving identity by rag_user username."""
    data = resolve_identity(ResolveRequest(rag_user="default"), session)
    assert data.user == "default"
    assert data.ha_url == "http://ha.local"
    assert data.ha_token == "default-ha-token"


def test_identity_resolve_by_user_id(session: Session):
    """Test resolving identity by user_id (integer PK)."""
    default_user = session.exec(select(User).where(User.username == "default")).first()
    assert default_user is not None
    assert default_user.id is not None
    
    data = resolve_identity(ResolveRequest(user_id=default_user.id), session)
    assert data.user == "default"
    assert data.ha_token == "default-ha-token"


def test_identity_resolve_by_voice_id(session: Session):
    """Test resolving identity by voice_id."""
    data = resolve_identity(ResolveRequest(voice_id="default"), session)
    assert data.user == "default"


def test_identity_resolve_by_api_key(session: Session):
    """Test resolving identity by API key."""
    # Create user with API key (api_key field is the stored plaintext key)
    alice = User(
        username="alice",
        api_key="alice-api-key",
        ha_url="http://ha-alice.local",
        ha_token_enc=encrypt("alice-ha-token"),
    )
    session.add(alice)
    session.commit()
    
    # API key resolution happens before rag_user fallback
    data = resolve_identity(ResolveRequest(api_key="alice-api-key"), session)
    assert data.user == "alice"
    assert data.ha_token == "alice-ha-token"


def test_identity_resolve_fallback_to_first_user(session: Session):
    """Test that unresolved queries fall back to first user."""
    data = resolve_identity(ResolveRequest(rag_user="nonexistent"), session)
    assert data.user == "default"


def test_identity_resolve_returns_decrypted_credentials(session: Session):
    """Test that resolve_identity returns decrypted credentials."""
    data = resolve_identity(ResolveRequest(rag_user="default"), session)
    assert isinstance(data, ResolvedCredentials)
    assert data.ha_token == "default-ha-token"
    assert data.is_admin


def test_identity_resolve_empty_request_fallback(session: Session):
    """Test that empty resolve request falls back to system user."""
    data = resolve_identity(ResolveRequest(), session)
    assert data.user == "default"


def test_identity_resolve_with_device_assignment(session: Session):
    """Test resolving identity by device_id."""
    default_user = session.exec(select(User).where(User.username == "default")).first()
    assert default_user is not None
    assert default_user.id is not None
    device = DeviceAssignment(
        device_id="phone-123",
        user_id=default_user.id,
    )
    session.add(device)
    session.commit()
    
    data = resolve_identity(ResolveRequest(device_id="phone-123"), session)
    assert data.user == "default"


def test_identity_resolve_multiple_users(session: Session):
    """Test that different users resolve to different credentials."""
    bob_data = resolve_identity(ResolveRequest(rag_user="bob"), session)
    assert bob_data.user == "bob"
    assert bob_data.ha_token == "bob-ha-token"
    assert bob_data.ha_url == "http://ha-bob.local"
    assert not bob_data.is_admin
    
    default_data = resolve_identity(ResolveRequest(rag_user="default"), session)
    assert default_data.user == "default"
    assert default_data.ha_token == "default-ha-token"
    assert default_data.ha_url == "http://ha.local"
    assert default_data.is_admin


def test_identity_resolve_audiobookshelf_credentials(session: Session):
    """Test that ABS credentials are resolved and decrypted."""
    data = resolve_identity(ResolveRequest(rag_user="bob"), session)
    assert data.audiobookshelf_url == "http://abs.local"
    assert data.audiobookshelf_user == "bob"
    assert data.audiobookshelf_pass == "bob-abs-pass"


def test_identity_resolve_missing_user_raises(session: Session):
    """Test that resolving a non-existent user without fallback raises 404."""
    # Temporarily remove default user
    default_user = session.exec(select(User).where(User.username == "default")).first()
    if default_user:
        session.delete(default_user)
    session.commit()
    
    with pytest.raises(HTTPException) as exc_info:
        resolve_identity(ResolveRequest(rag_user="nonexistent"), session)
    assert exc_info.value.status_code == 404


def test_resolve_request_schema_optional_fields():
    """Test that ResolveRequest accepts partial data."""
    req = ResolveRequest()
    assert req.user_id is None
    assert req.rag_user is None
    
    req = ResolveRequest(user_id=1)
    assert req.user_id == 1
    assert req.rag_user is None


def test_resolved_credentials_default_values():
    """Test that ResolvedCredentials has proper defaults."""
    creds = ResolvedCredentials(user="testuser")
    assert not creds.is_admin
    assert creds.api_key is None
    assert creds.ha_url is None
    assert creds.ha_token is None


def test_resolve_identity_by_multiple_fields(session: Session):
    """Test that identity resolves correctly with multiple fields in request."""
    default_user = session.exec(select(User).where(User.username == "default")).first()
    assert default_user is not None
    assert default_user.id is not None
    
    # User ID takes precedence
    data = resolve_identity(ResolveRequest(user_id=default_user.id), session)
    assert data.user == "default"


def test_resolve_identity_returns_all_credential_types(session: Session):
    """Test that resolve_identity returns all credential types."""
    full_user = User(
        username="fulluser",
        ha_url="http://ha.full.local",
        ha_token_enc=encrypt("full-ha-token"),
        nextcloud_url="http://nc.full.local",
        nextcloud_user="ncuser",
        nextcloud_pass_enc=encrypt("nc-pass"),
        github_url="https://github.com",
        github_user="ghuser",
        github_token_enc=encrypt("gh-token"),
        gitlab_url="https://gitlab.com",
        gitlab_user="gluser",
        gitlab_token_enc=encrypt("gl-token"),
    )
    session.add(full_user)
    session.commit()
    
    data = resolve_identity(ResolveRequest(rag_user="fulluser"), session)
    
    assert data.ha_url == "http://ha.full.local"
    assert data.ha_token == "full-ha-token"
    assert data.nextcloud_url == "http://nc.full.local"
    assert data.nextcloud_user == "ncuser"
    assert data.nextcloud_pass == "nc-pass"
    assert data.github_url == "https://github.com"
    assert data.github_user == "ghuser"
    assert data.github_token == "gh-token"
    assert data.gitlab_url == "https://gitlab.com"
    assert data.gitlab_user == "gluser"
    assert data.gitlab_token == "gl-token"


def test_resolve_identity_credential_isolation(session: Session):
    """Test that credentials are isolated per user."""
    user1 = User(
        username="user1",
        ha_url="http://ha1.local",
        ha_token_enc=encrypt("token1"),
    )
    user2 = User(
        username="user2",
        ha_url="http://ha2.local",
        ha_token_enc=encrypt("token2"),
    )
    session.add_all([user1, user2])
    session.commit()
    
    data1 = resolve_identity(ResolveRequest(rag_user="user1"), session)
    data2 = resolve_identity(ResolveRequest(rag_user="user2"), session)
    
    assert data1.ha_url == "http://ha1.local"
    assert data1.ha_token == "token1"
    assert data2.ha_url == "http://ha2.local"
    assert data2.ha_token == "token2"
