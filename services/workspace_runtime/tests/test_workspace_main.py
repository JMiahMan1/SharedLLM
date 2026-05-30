import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool

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
    from services.workspace_runtime.main import app
    # Override engine in main with our test engine
    from services.workspace_runtime import main
    original_engine = main.engine
    main.engine = session.bind
    client = TestClient(app)
    yield client
    main.engine = original_engine

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_workspace_crud(client: TestClient):
    # 1. Create
    ws_data = {
        "id": "test_ws",
        "display_name": "Test Workspace",
        "local_path": "/tmp/test_ws",
        "sync_mode": "git",
        "scope": "user",
        "capabilities": ["read", "write"]
    }
    resp = client.post("/workspaces", json=ws_data, headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert resp.json()["workspace"]["id"] == "test_ws"

    # 2. Read (List)
    resp = client.get("/workspaces", headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    data = resp.json()
    assert any(ws["id"] == "test_ws" for ws in data["workspaces"])

    # 3. Update
    resp = client.patch("/workspaces/test_ws", json={"display_name": "Updated Name"}, headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert resp.json()["workspace"]["display_name"] == "Updated Name"

    # 4. Delete
    resp = client.delete("/workspaces/test_ws", headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    
    # 5. Verify Deleted
    resp = client.get("/workspaces", headers={"X-Internal-Secret": "change-me-in-production"})
    data = resp.json()
    assert not any(ws["id"] == "test_ws" for ws in data["workspaces"])
