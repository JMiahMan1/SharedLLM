import os
import httpx
import pytest

# Configuration
BASE_URL = os.getenv("LIVE_TEST_URL", "http://localhost:8080")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}

# Service URLs (Internal routing via Gateway)
GATEWAY_URL = f"{BASE_URL}/api"

@pytest.mark.asyncio
async def test_capability_read_success():
    """Verify that a workspace with 'read' capability allowed can list and read files."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Use sharedllm_system which has 'read'
        resp = await client.post(f"{GATEWAY_URL}/execute/workspace_search", json={
            "workspace_id": "sharedllm_system",
            "path": ".",
            "query": "SharedLLM",
            "user_context": {"user": "test_user", "is_admin": False}
        }, headers=HEADERS)  # noqa: F841
        
        # Capability check happens at workspace_runtime level, but routed through execution -> gateway
        assert resp.status_code == 200
        assert resp.json()["status"] == "SUCCESS"

@pytest.mark.asyncio
async def test_capability_write_failure():
    """Verify that a workspace WITHOUT 'write' capability rejects file writes."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # sharedllm_system does NOT have 'write'
        payload = {
            "workspace_id": "sharedllm_system",
            "path": "test_forbidden.txt",
            "content": "This should fail",
            "user_context": {"user": "test_user", "is_admin": False}
        }
        resp = await client.post(f"{GATEWAY_URL}/execute/workspace_file_write", json=payload, headers=HEADERS)
        
        # Should return 403 Forbidden from workspace_runtime
        assert resp.status_code == 403
        assert "does not allow capability 'write'" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_capability_git_status_success():
    """Verify git_status is allowed on sharedllm_system."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = {
            "action": "status",
            "workspace_id": "sharedllm_system",
            "user_context": {"user": "test_user", "is_admin": False}
        }
        resp = await client.post(f"{GATEWAY_URL}/execute/git", json=payload, headers=HEADERS)
        
        assert resp.status_code == 200
        assert resp.json()["status"] == "SUCCESS"

@pytest.mark.asyncio
async def test_capability_git_write_failure():
    """Verify git_write (commit/add) is rejected on sharedllm_system."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = {
            "action": "add",
            "path": ".",
            "workspace_id": "sharedllm_system",
            "user_context": {"user": "test_user", "is_admin": False}
        }
        resp = await client.post(f"{GATEWAY_URL}/execute/git", json=payload, headers=HEADERS)
        
        assert resp.status_code == 403
        assert "does not allow capability 'git_write'" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_capability_pytest_failure():
    """Verify pytest is rejected on sharedllm_system."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = {
            "command": "python3 -m pytest",
            "workspace_id": "sharedllm_system",
            "user_context": {"user": "test_user", "is_admin": False}
        }
        resp = await client.post(f"{GATEWAY_URL}/execute/workspace_shell", json=payload, headers=HEADERS)
        
        assert resp.status_code == 403
        assert "does not allow capability 'pytest'" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_admin_bypass():
    """Verify that an admin user bypasses all capability checks."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Use sharedllm_system which normally forbids 'write'
        payload = {
            "workspace_id": "sharedllm_system",
            "path": "admin_test.txt",
            "content": "Admin bypass works",
            "user_context": {"user": "admin_user", "is_admin": True}
        }
        resp = await client.post(f"{GATEWAY_URL}/execute/workspace_file_write", json=payload, headers=HEADERS)
        
        # Should succeed because is_admin is True
        assert resp.status_code == 200
        assert resp.json()["status"] == "SUCCESS"

if __name__ == "__main__":
    import sys
    pytest.main([__file__] + sys.argv[1:])
