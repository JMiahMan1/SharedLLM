import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool
import os

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
    from main import app, engine
    import main
    # Override engine in main with our test engine
    main.engine = session.bind
    
    # Ensure tables are created on the test engine
    SQLModel.metadata.create_all(main.engine)
    
    from seed import seed_from_env
    os.environ["INTERNAL_SECRET"] = "change-me-in-production"
    seed_from_env(session, force=True)
    
    # Mock require_api_key to return the default user
    from models import User
    from sqlmodel import select
    default_user = session.exec(select(User).where(User.username == "default")).first()
    app.dependency_overrides[main.require_api_key] = lambda: default_user

    client = TestClient(app)
    yield client
    app.dependency_overrides = {}

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "OK"

def test_identity_resolve(client: TestClient):
    payload = {"rag_user": "default"}
    resp = client.post("/api/resolve", json=payload, headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert resp.json()["user"] == "default"
    # ResolvedCredentials does not contain api_key in the public schema

def test_get_settings(client: TestClient):
    resp = client.get("/api/settings", headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert len(resp.json()) > 0
