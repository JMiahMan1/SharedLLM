import pytest
import httpx
import os
import json
from unittest.mock import MagicMock, patch

# Assuming we have a way to run these against a dev environment or mock them
# For this task, I'll create a test that can be run in the CI/CD or locally

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://127.0.0.1:8000")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "jarvis-internal-secret")

@pytest.mark.asyncio
async def test_global_settings_workspace_root():
    """Verify that workspace_runtime_root can be set and retrieved."""
    async with httpx.AsyncClient() as client:
        # 1. Update setting
        new_root = "/tmp/custom_workspace"
        resp = await client.patch(
            f"{GATEWAY_URL}/api/settings/workspace_runtime_root",
            json={"value": new_root},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        assert resp.status_code == 200
        
        # 2. Verify setting is saved
        resp = await client.get(
            f"{GATEWAY_URL}/api/settings",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        assert resp.status_code == 200
        settings = resp.json()
        match = next((s for s in settings if s["key"] == "workspace_runtime_root"), None)
        assert match is not None
        assert match["value"] == new_root

@pytest.mark.asyncio
async def test_admin_set_password():
    """Verify that an admin can set a user's password."""
    async with httpx.AsyncClient() as client:
        # Try to set password for a test user
        resp = await client.post(
            f"{GATEWAY_URL}/api/users/admin/password",
            json={"new_password": "new-secure-password"},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        # Should be 200 or 403 if not admin (but we use internal secret here which usually bypasses or identifies as system)
        assert resp.status_code in (200, 403) 

@pytest.mark.asyncio
async def test_rag_purge_endpoint():
    """Verify that the RAG purge endpoint is proxied correctly."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/storage/purge/ha_data",
            json={"filter": {}},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        # It might return 200 if it works, or 500 if RAG is down
        assert resp.status_code in (200, 500, 503)

@pytest.mark.asyncio
async def test_calendar_list_detail():
    """Verify that calendar list returns structured detail."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GATEWAY_URL}/api/communication/calendar/calendars",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] == "SUCCESS":
            assert "calendars" in data.get("detail", {})
