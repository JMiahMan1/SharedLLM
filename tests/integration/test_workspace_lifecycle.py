import httpx
import pytest
import os
import time

# Configuration from environment or defaults
SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = f"http://{SERVER_IP}:8080"
WORKSPACE_RUNTIME_URL = f"http://{SERVER_IP}:8007"
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

@pytest.fixture
def api_client():
    return httpx.Client(headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=30.0)

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
    print(f"   - Created successfully.")

    # 2. Trigger Git Pull (Webhook)
    # We use the internal secret since we didn't set a webhook_token
    print(f"[2/3] Triggering git pull for {workspace_id}")
    resp = api_client.post(
        f"{WORKSPACE_RUNTIME_URL}/api/webhook/git-pull/{workspace_id}",
        params={"token": INTERNAL_SECRET}
    )
    assert resp.status_code == 200, f"Git pull failed: {resp.text}"
    assert resp.json()["status"] == "SUCCESS"
    print(f"   - Pull triggered successfully.")

    # 3. Verify Directory Creation (Via Workspace API)
    # We'll list files to see if it was cloned
    print(f"[3/3] Verifying file presence in {workspace_id}")
    list_req = {
        "workspace_id": workspace_id,
        "relative_path": ".",
        "max_entries": 10
    }
    resp = api_client.post(f"{WORKSPACE_RUNTIME_URL}/files/list", json=list_req)
    assert resp.status_code == 200, f"Failed to list files: {resp.text}"
    entries = resp.json().get("entries", [])
    assert len(entries) > 0, "No files found in workspace after pull"
    print(f"   - Verified: found {len(entries)} entries.")

    # Cleanup (Optional: Delete workspace)
    print(f"Cleanup: Deleting test workspace {workspace_id}")
    api_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")

if __name__ == "__main__":
    # Allow running directly for quick check
    with httpx.Client(headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=30.0) as client:
        test_workspace_lifecycle(client)
