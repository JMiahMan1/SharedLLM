import os
import time

import httpx
import pytest

# Configuration from environment or defaults
SERVER_IP = os.getenv("SERVER_IP", "localhost")
GATEWAY_URL = f"http://{SERVER_IP}:8080"
WORKSPACE_RUNTIME_URL = f"http://{SERVER_IP}:8007"
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

def wait_for_service(url, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with httpx.Client() as client:
                resp = client.get(url.replace("/workspaces", "/")) # Generic health check
                if resp.status_code < 500:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False

@pytest.fixture(scope="session", autouse=True)
def ensure_services():
    if not wait_for_service(WORKSPACE_RUNTIME_URL):
        pytest.fail(f"Service {WORKSPACE_RUNTIME_URL} not available")

@pytest.fixture
def api_client():
    return httpx.Client(headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=60.0)

def test_workspace_lifecycle(api_client):
    """
    Test the full lifecycle of a workspace: Create -> Pull -> Sync.
    """
    workspace_id = f"test_audit_{int(time.time())}"

    # 1. Create Workspace
    print(f"\n[1/3] Creating workspace: {workspace_id}")
    ws_data = {
        "id": workspace_id,
        "display_name": "Audit Test Workspace",
        "local_path": f"tests/{workspace_id}",
        "repo_url": "https://github.com/JMiahMan1/SharedLLM.git", # Use current repo as test
        "default_branch": "microservices",
        "auto_pull_enabled": True,
        "auto_backup_enabled": True,
        "nextcloud_path": f"/Tests/{workspace_id}"
    }

    resp = api_client.post(f"{WORKSPACE_RUNTIME_URL}/workspaces", json=ws_data)
    assert resp.status_code == 200, f"Failed to create workspace: {resp.text}"
    print("   - Created successfully.")

    # 2. Trigger Git Pull (Webhook)
    # We use the internal secret since we didn't set a webhook_token
    print(f"[2/3] Triggering git pull for {workspace_id}")
    resp = api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/api/webhook/git-pull/{workspace_id}",
        params={"token": INTERNAL_SECRET}
    )
    assert resp.status_code == 200, f"Git pull failed: {resp.text}"
    assert resp.json()["status"] == "SUCCESS"
    print("   - Pull triggered successfully.")

    # 3. Verify Directory Creation (Via Workspace API)
    # We'll list files to see if it was cloned
    print(f"[3/3] Verifying file presence in {workspace_id}")
    list_req = {
        "workspace_id": workspace_id,
        "relative_path": ".",
        "max_entries": 10,
        "user_context": {"user": "default", "is_admin": True}
    }
    resp = api_client.post(f"{WORKSPACE_RUNTIME_URL}/files/list", json=list_req)
    assert resp.status_code == 200, f"Failed to list files: {resp.text}"
    entries = resp.json().get("entries", [])
    assert len(entries) > 0, "No files found in workspace after pull"
    print(f"   - Verified: found {len(entries)} entries.")

    # Cleanup (Optional: Delete workspace)
    print(f"Cleanup: Deleting test workspace {workspace_id}")
    api_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")

@pytest.mark.integration
def test_write_read_verification(api_client):
    """Write a file then read it back. The write endpoint self-verifies the
    content actually persisted, so the round-trip must match exactly."""
    ws_id = f"test_write_verify_{int(time.time() * 1000)}"
    resp = api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/workspaces",
        json={"id": ws_id, "display_name": "Write Verify", "local_path": f"tests/{ws_id}"},
    )
    assert resp.status_code == 200, f"create failed: {resp.text}"
    payload = {"workspace_id": ws_id, "relative_path": "main.py", "content": "print('hello starfall')\n", "user_context": {"user": "default", "is_admin": True}}
    w = api_client.post(f"{WORKSPACE_RUNTIME_URL}/files/write", json=payload)
    assert w.status_code == 200, f"write failed: {w.text}"
    body = w.json()
    assert body["status"] == "SUCCESS"
    assert body.get("sha256"), "write should return a sha256"
    assert body["bytes_written"] == len(payload["content"])
    r = api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/files/read",
        json={"workspace_id": ws_id, "relative_path": "main.py", "user_context": {"user": "default", "is_admin": True}},
    )
    assert r.status_code == 200, f"read failed: {r.text}"
    assert "print('hello starfall')" in r.json().get("content", "")
    api_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{ws_id}")


@pytest.mark.integration
def test_delete_verification(api_client):
    """Delete a file and confirm it is actually gone (delete self-verifies)."""
    ws_id = f"test_del_verify_{int(time.time() * 1000)}"
    resp = api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/workspaces",
        json={"id": ws_id, "display_name": "Delete Verify", "local_path": f"tests/{ws_id}"},
    )
    assert resp.status_code == 200, f"create failed: {resp.text}"
    api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/files/write",
        json={"workspace_id": ws_id, "relative_path": "to_del.txt", "content": "bye", "user_context": {"user": "default", "is_admin": True}},
    )
    d = api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/files/delete",
        json={"workspace_id": ws_id, "relative_path": "to_del.txt", "user_context": {"user": "default", "is_admin": True}},
    )
    assert d.status_code == 200, f"delete failed: {d.text}"
    r = api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/files/read",
        json={"workspace_id": ws_id, "relative_path": "to_del.txt", "user_context": {"user": "default", "is_admin": True}},
    )
    assert r.status_code == 404, f"deleted file should be gone, got {r.status_code}: {r.text}"
    api_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{ws_id}")


if __name__ == "__main__":
    # Allow running directly for quick check
    with httpx.Client(headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=30.0) as client:
        test_workspace_lifecycle(client)
