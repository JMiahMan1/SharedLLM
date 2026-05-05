import httpx
import os

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
BASE_URL = "http://localhost:8007"

def test_health():
    resp = httpx.get(f"{BASE_URL}/health")
    print(f"Health check: {resp.status_code} - {resp.json()}")
    assert resp.status_code == 200

def test_workspaces():
    params = {"rag_user": "jeremiah"}
    resp = httpx.get(f"{BASE_URL}/workspaces", params=params, headers={"X-Internal-Secret": INTERNAL_SECRET})
    print(f"Workspaces: {resp.status_code} - {resp.json()}")
    assert resp.status_code == 200
    return resp.json().get("workspaces", [])

def test_git_status(ws_id):
    payload = {"workspace_id": ws_id, "rag_user": "jeremiah"}
    resp = httpx.post(f"{BASE_URL}/git/status", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
    print(f"Git status ({ws_id}): {resp.status_code} - {resp.json()}")
    assert resp.status_code == 200

if __name__ == "__main__":
    try:
        test_health()
        workspaces = test_workspaces()
        if workspaces:
            test_git_status(workspaces[0]["id"])
        print("\nSMOKE TEST SUCCESSFUL")
    except Exception as e:
        print(f"\nSMOKE TEST FAILED: {e}")
        exit(1)
