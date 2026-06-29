import os
import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

# Set environment variables for testing (respect CI overrides)
if "INTERNAL_SECRET" not in os.environ:
    os.environ["INTERNAL_SECRET"] = "test-secret"
if "FERNET_KEY" not in os.environ:
    os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="
if "INIT_DB" not in os.environ:
    os.environ["INIT_DB"] = "false"
if "DEFAULT_ADMIN_PASSWORD" not in os.environ:
    os.environ["DEFAULT_ADMIN_PASSWORD"] = "test-admin-password"

import services.identity.main as identity_main
from services.identity.main import app, get_session, _store_user_api_key
from services.identity.models import User, APIKey
from services.identity.crypto import digest_secret

# Use an in-memory SQLite database for testing
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

@pytest.fixture(name="session")
def session_fixture(monkeypatch):
    # Override the engine in the main module BEFORE creating tables
    monkeypatch.setattr(identity_main, "engine", test_engine)
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)

@pytest.fixture(name="client")
def client_fixture(session, monkeypatch):
    def get_session_override():
        return session
    app.dependency_overrides[get_session] = get_session_override
    # Also monkeypatch engine here just in case
    monkeypatch.setattr(identity_main, "engine", test_engine)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

def test_api_key_generation_and_resolution(client, session):
    """
    Test 1: Key Generation and Test 2: Resolution Routing
    """
    # 1. Create a dummy user (Dad)
    dad = session.exec(select(User).where(User.username == "dad")).first()
    if not dad:
        dad = User(username="dad", is_admin=True)
        session.add(dad)
    
    # Also find or update the 'default' user for fallback
    default_user = session.exec(select(User).where(User.username == "default")).first()
    if not default_user:
        default_user = User(username="default", is_system_default=True)
        session.add(default_user)
    
    default_user.ha_url = "http://default-ha"
    session.add(default_user)
    session.commit()
    
    # Headers for admin requests (using internal secret for some, or just login normally)
    # The generate key endpoint uses require_api_key, so we need a way to call it.
    # For testing, we'll manually set an api_key for Dad first.
    _store_user_api_key(dad, "dad-session-token")
    session.add(dad)
    session.commit()
    
    auth_headers = {"Authorization": "Bearer dad-session-token"}
    
    # 2. Call the generate API key endpoint
    resp = client.post("/api/users/me/keys", json={"label": "OpenWebUI Key"}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    generated_key = data["key"]
    assert generated_key.startswith("sk-")
    
    # Verify it's in the DB
    db_key = session.exec(select(APIKey).where(APIKey.key_hash == digest_secret(generated_key))).first()
    assert db_key is not None
    assert db_key.user_id == dad.id
    assert db_key.key_value == digest_secret(generated_key)
    
    # 3. Test Resolution Routing
    
    # A. Resolve by rag_user (UI context)
    resolve_resp = client.post(
        "/api/resolve", 
        json={"rag_user": "dad"},
        headers={"Authorization": f"Bearer {os.environ['INTERNAL_SECRET']}"}
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["user"] == "dad"
    
    # B. Resolve by api_key (OpenWebUI context)
    resolve_resp = client.post(
        "/api/resolve", 
        json={"api_key": generated_key},
        headers={"Authorization": f"Bearer {os.environ['INTERNAL_SECRET']}"}
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["user"] == "dad"
    
    # C. Resolve with no identifiers (Fallback to default)
    resolve_resp = client.post(
        "/api/resolve", 
        json={},
        headers={"Authorization": f"Bearer {os.environ['INTERNAL_SECRET']}"}
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["user"] == "default"
    assert resolve_resp.json()["ha_url"] == "http://default-ha"

    print("\nSUCCESS: Identity resolution routing verified for UI, API Key, and Fallback.")
