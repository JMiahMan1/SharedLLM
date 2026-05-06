import os
import json
import pytest
from fastapi.testclient import TestClient
from contextlib import asynccontextmanager

# Set environment variables for testing
os.environ["INTERNAL_SECRET"] = "test-secret"

import workspace_runtime.main as runtime
from workspace_runtime.main import app

@pytest.fixture
def client(monkeypatch, tmp_path):
    # Setup a mock workspace root
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    demo_dir = workspace_root / "demo"
    demo_dir.mkdir()
    
    # Initialize a git repo in demo_dir
    import subprocess
    subprocess.run(["git", "init", "-b", "main"], cwd=demo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=demo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=demo_dir, check=True, capture_output=True)
    (demo_dir / ".gitignore").write_text("__pycache__/\n")
    subprocess.run(["git", "add", "."], cwd=demo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=demo_dir, check=True, capture_output=True)

    # Setup mock registry
    registry_path = tmp_path / "workspaces.json"
    registry_path.write_text(json.dumps({
        "workspaces": [{
            "id": "demo",
            "display_name": "Demo Workspace",
            "local_path": "demo",
            "access_policy": "authenticated"
        }]
    }))
    
    monkeypatch.setattr(runtime, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(runtime, "WORKSPACE_REGISTRY_PATH", str(registry_path))
    monkeypatch.setattr(runtime, "INTERNAL_SECRET", "test-secret")
    
    # Mock identity resolution
    def mock_resolve(ref):
        return {
            "user": ref.rag_user or "admin",
            "is_admin": True,
            "nextcloud_url": "http://cloud",
            "nextcloud_user": "admin",
            "nextcloud_pass": "pass"
        }
    monkeypatch.setattr(runtime, "_resolve_identity_context", mock_resolve)
    
    # Mock nextcloud provider calls
    class MockResp:
        def __init__(self): self.status_code = 200
        def json(self): return {"status": "SUCCESS", "result": {"verified": True}}
    
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: MockResp())

    with TestClient(app) as test_client:
        yield test_client

def test_workspace_file_operations(client):
    """
    Test 3.1: File Write & Read
    """
    # Write a dummy file
    write_resp = client.post(
        "/files/write",
        json={
            "workspace_id": "demo",
            "rag_user": "admin",
            "relative_path": "temp_test.py",
            "content": "print('hello')"
        },
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert write_resp.status_code == 200
    
    # Read it back
    read_resp = client.post(
        "/files/read",
        json={
            "workspace_id": "demo",
            "rag_user": "admin",
            "relative_path": "temp_test.py"
        },
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert read_resp.status_code == 200
    assert read_resp.json()["content"] == "print('hello')"

def test_autonomous_fix_it_loop(client):
    """
    Test 3.2: The Autonomous 'Fix-It' Loop
    """
    # Pass a payload with a syntax error and a pytest target
    payload = {
        "workspace_id": "demo",
        "rag_user": "admin",
        "relative_path": "broken.py",
        "content": "def broken_func() return True",  # Missing colon
        "commit_message": "test commit",
        "pytest_targets": ["broken.py"],
        "sync_to_provider": False
    }
    
    resp = client.post(
        "/workflow/write-sync-commit",
        json=payload,
        headers={"X-Internal-Secret": "test-secret"}
    )
    
    # The Assertion: Should be 400 because pytest fails on syntax error
    assert resp.status_code == 400
    assert "Pytest failed" in resp.json()["detail"]

def test_path_traversal_protection(client):
    """
    Test 3.3: Path Traversal Protection
    """
    # Try to read /etc/passwd
    resp = client.post(
        "/files/read",
        json={
            "workspace_id": "demo",
            "rag_user": "admin",
            "relative_path": "../../../etc/passwd"
        },
        headers={"X-Internal-Secret": "test-secret"}
    )
    
    # The Assertion: Should return 400 or 403
    assert resp.status_code in [400, 403]
    
    # Try to write outside
    resp = client.post(
        "/files/write",
        json={
            "workspace_id": "demo",
            "rag_user": "admin",
            "relative_path": "../hack.txt",
            "content": "pwned"
        },
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert resp.status_code in [400, 403]

    print("\nSUCCESS: Workspace sandbox security and autonomous loop verified.")
