import pytest
import subprocess
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool
from pathlib import Path
from unittest.mock import patch
from workspace_runtime.models import Workspace
import workspace_runtime.main as main

@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

@pytest.fixture(name="client")
def client_fixture(session: Session, tmp_path):
    from main import app
    original_engine = main.engine
    main.engine = session.bind
    client = TestClient(app)
    yield client
    main.engine = original_engine

def test_git_revert_logic(client: TestClient, session: Session, tmp_path: Path):
    # 1. Setup a real Git repository
    ws_id = "test_revert_ws"
    ws_dir = tmp_path / ws_id
    ws_dir.mkdir(parents=True)
    
    subprocess.run(["git", "init"], cwd=ws_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=ws_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=ws_dir, check=True)
    
    file_path = ws_dir / "test.txt"
    file_path.write_text("v1")
    subprocess.run(["git", "add", "test.txt"], cwd=ws_dir, check=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=ws_dir, check=True)
    v1_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ws_dir).decode().strip()
    
    file_path.write_text("v2")
    subprocess.run(["git", "add", "test.txt"], cwd=ws_dir, check=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=ws_dir, check=True)
    
    # 2. Register workspace
    ws = Workspace(
        id=ws_id,
        display_name="Test Revert",
        local_path=ws_id,
        quarantined=True,
        sync_mode="local_git_authoritative"
    )
    session.add(ws)
    session.commit()
    
    # 3. Call revert
    # We patch resolve_safe_path to return our REAL temp directory path
    with patch("main.resolve_safe_path", return_value=ws_dir), \
         patch("main._resolve_identity_context", return_value={"user": "admin", "is_admin": True}):
        
        resp = client.post(
            "/git/revert", 
            json={"workspace_id": ws_id},
            headers={"X-Internal-Secret": "change-me-in-production"}
        )
        
        assert resp.status_code == 200
        
    # 4. Verify result
    assert file_path.read_text() == "v1"
    current_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ws_dir).decode().strip()
    assert current_hash == v1_hash
    
    session.expire_all()
    assert session.get(Workspace, ws_id).quarantined is False

def test_git_revert_failure_state(client: TestClient, session: Session, tmp_path: Path):
    ws_id = "fail_ws"
    ws_dir = tmp_path / ws_id
    ws_dir.mkdir() # Not a git repo
    
    ws = Workspace(id=ws_id, display_name="Fail WS", local_path=ws_id, quarantined=True)
    session.add(ws)
    session.commit()
    
    with patch("main.resolve_safe_path", return_value=ws_dir), \
         patch("main._resolve_identity_context", return_value={"user": "admin", "is_admin": True}):
        
        resp = client.post(
            "/git/revert", 
            json={"workspace_id": ws_id},
            headers={"X-Internal-Secret": "change-me-in-production"}
        )
        
        assert resp.status_code == 400
        
    session.expire_all()
    assert session.get(Workspace, ws_id).quarantined is True
