import os

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

os.environ["INTERNAL_SECRET"] = "test-secret"

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
    import services.workspace_runtime.main as main
    from services.workspace_runtime.main import app
    original_engine = main.engine
    main.engine = session.bind
    client = TestClient(app)
    yield client
    main.engine = original_engine

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_http_post_async_returns_json_with_provided_session():
    import services.workspace_runtime.main as main
    from services.config import IDENTITY_SVC_URL

    class Response:
        status = 200

        async def json(self):
            return {"user": "default"}

    class Session:
        async def post(self, *args, **kwargs):
            return Response()

    result = await main._http_post_async(
        f"{IDENTITY_SVC_URL}/api/resolve",
        json={"rag_user": "default"},
        session=Session(),
    )

    assert result == {"user": "default"}


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
    resp = client.post("/workspaces", json=ws_data, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["workspace"]["id"] == "test_ws"

    # 2. Read (List)
    resp = client.get("/workspaces", headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert any(ws["id"] == "test_ws" for ws in data["workspaces"])

    # 3. Update
    resp = client.patch("/workspaces/test_ws", json={"display_name": "Updated Name"}, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["workspace"]["display_name"] == "Updated Name"

    # 4. Delete
    resp = client.delete("/workspaces/test_ws", headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200

    # 5. Verify Deleted
    resp = client.get("/workspaces", headers={"X-Internal-Secret": "test-secret"})
    data = resp.json()
    assert not any(ws["id"] == "test_ws" for ws in data["workspaces"])


def test_delete_workspace_tears_down_sandbox(client: TestClient):
    """Deleting a workspace must also tear down its sandbox container + network
    so the wsbox-* container is not leaked (which previously exhausted Docker's
    predefined address pools)."""
    from unittest.mock import patch

    ws_data = {
        "id": "teardown_ws",
        "display_name": "Teardown Workspace",
        "local_path": "/tmp/teardown_ws",
        "sync_mode": "git",
        "scope": "user",
        "capabilities": ["read", "write"],
    }
    resp = client.post("/workspaces", json=ws_data, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200

    with patch("services.workspace_sandbox.remove_workspace_container") as rm:
        resp = client.delete("/workspaces/teardown_ws", headers={"X-Internal-Secret": "test-secret"})
        assert resp.status_code == 200
        rm.assert_called_once_with("teardown_ws")


def test_create_workspace_rejects_empty_id(client: TestClient):
    """An empty/whitespace id must be rejected, not stored as an undeletable row."""
    headers = {"X-Internal-Secret": "test-secret"}
    for bad_id in ["", "   ", None]:
        ws_data = {
            "id": bad_id,
            "display_name": "Broken",
            "local_path": "/tmp/broken_ws",
            "scope": "user",
        }
        resp = client.post("/workspaces", json=ws_data, headers=headers)
        assert resp.status_code == 400, f"empty id {bad_id!r} should be rejected"
        # Confirm nothing was persisted with an empty id.
        listing = client.get("/workspaces", headers=headers).json()
        assert not any((ws.get("id") or "") == "" for ws in listing["workspaces"])

