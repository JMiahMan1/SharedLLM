import os
# Set environment variables BEFORE any imports that might use them
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool, select

from services.identity.main import app, require_api_key, require_internal, resolve_identity
from services.identity.models import User, DeviceAssignment
from services.identity.crypto import encrypt
from services.identity.schemas import ResolveRequest, UserCreate
from services.identity.seed import seed_from_env
from services.identity import main as identity_main

# Module-level test engine - created once
_test_engine = None

def _get_test_engine():
    global _test_engine
    if _test_engine is None:
        _test_engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(_test_engine)
    return _test_engine

# Setup in-memory database for testing
@pytest.fixture(name="session")
def session_fixture():
    test_engine = _get_test_engine()
    identity_main.engine = test_engine
    with Session(test_engine) as session:
        seed_from_env(session, force=True)
        
        admin_user = session.exec(select(User).where(User.username == "default")).first()
        admin_user.is_admin = True
        session.add(admin_user)
        session.commit()
        
        yield session

@pytest.fixture(name="test_client")
def test_client_fixture(session: Session):
    app.dependency_overrides[require_api_key] = lambda: session.exec(select(User).where(User.username == "default")).first()
    app.dependency_overrides[require_internal] = lambda: True

    with TestClient(app) as client:
        yield client

def test_health_check(test_client: TestClient):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_identity_resolve(test_client: TestClient):
    payload = {"rag_user": "default"}
    resp = test_client.post("/api/resolve", json=payload, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["user"] == "default"

def test_resolve_voice_id(session: Session):
    alice = User(username="alice", ha_url="http://ha.local", ha_token_enc=encrypt("alice-ha-token"))
    session.add(alice)
    session.commit()
    
    data = resolve_identity(ResolveRequest(voice_id="alice"), session)
    assert data.user == "alice"
    assert data.ha_token == "alice-ha-token"

def test_create_user_stores_git_provider_credentials(test_client: TestClient, session: Session):
    # Use the actual API endpoint
    resp = test_client.post("/api/users", json={
        "username": "bob",
        "github_url": "https://github.com",
        "github_user": "bob-gh",
        "github_token": "bob-gh-token",
        "is_admin": False
    })
    assert resp.status_code == 200
    assert resp.json()["username"] == "bob"
    
    # Verify via resolution
    data = resolve_identity(ResolveRequest(rag_user="bob"), session)
    assert data.github_token == "bob-gh-token"
