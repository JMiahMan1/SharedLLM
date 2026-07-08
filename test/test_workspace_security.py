import httpx
import pytest

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
            "http://test/files/write",
            json=malicious_payload,
            headers={"X-Internal-Secret": "test-secret"}
        )

        # Should be blocked by resolve_safe_path with 403
        assert resp.status_code == 403
        detail = resp.json().get("detail", "")
        assert "Forbidden" in detail or "traversal" in detail.lower()
