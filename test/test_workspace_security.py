import pytest
import httpx
from fastapi import status

# Phase 4.2: Test Workspace Security
# This test attempts a path traversal attack and asserts a 403 Forbidden.

@pytest.mark.asyncio
async def test_workspace_path_traversal_blocked():
    from services.workspace_runtime.main import app
    
    # We attempt to write to a path outside the workspace
    malicious_payload = {
        "workspace_id": "main",
        "relative_path": "../../../main.py",
        "content": "print('hacked')"
    }
    
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/files/write", 
            json=malicious_payload,
            headers={"X-Internal-Secret": "change-me-in-production"}
        )
        
        if resp.status_code == 404:
            print(f"Routes: {[r.path for r in app.routes]}")
            
        # Should be blocked by resolve_safe_path
        assert resp.status_code == 403
        assert "Path traversal detected" in resp.json()["detail"]
