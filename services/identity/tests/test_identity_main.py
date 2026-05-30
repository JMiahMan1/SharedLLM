import os
# Set environment variables BEFORE any imports that might use them
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool, select

from services.identity.main import app, require_api_key, require_internal, resolve_identity
from services.identity.models import User
from services.identity.crypto import encrypt
from services.identity.schemas import ResolveRequest
import services.identity.main as main

# Setup in-memory database for testing
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
    
    # Mock admin user
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

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "OK"

def test_identity_resolve(client: TestClient):
    payload = {"rag_user": "default"}
    resp = client.post("/api/resolve", json=payload, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["user"] == "default"

def test_resolve_voice_id(session: Session):
    alice = User(username="alice", ha_url="http://ha.local", ha_token_enc=encrypt("alice-ha-token"))
    session.add(alice)
    session.commit()
    
    data = resolve_identity(ResolveRequest(voice_id="alice"), session)
    assert data.user == "alice"
    assert data.ha_token == "alice-ha-token"

def test_create_user_stores_git_provider_credentials(client: TestClient, session: Session):
    # Use the actual API endpoint
    resp = client.post("/api/users", json={
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
