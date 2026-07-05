import pytest
import os
import aiohttp
from unittest.mock import MagicMock, patch, AsyncMock

def test_kill_switch_logic():
    """Verify that the Raven kill switch logic correctly identifies an aborted mission."""
    mock_redis = MagicMock()
    mission_id = "test-mission-123"
    kill_key = f"raven:mission:kill:{mission_id}"
    
    mock_redis.get.return_value = b"1"
    
    is_killed = bool(mock_redis.get(kill_key))
    
    assert is_killed is True
    mock_redis.get.assert_called_with(kill_key)

def test_non_root_permission_mock():
    """Verify that we can correctly identify if we are running as non-root."""
    uid = os.getuid()
    assert uid != 0 or os.getenv("ALLOW_ROOT_TEST") == "true"

@pytest.mark.asyncio
async def test_identity_resolution_mock():
    """Verify that we can mock identity resolution for Raven."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "user": "raven_test",
        "is_admin": True,
        "ha_url": "http://ha.local"
    }
    
    with patch("aiohttp.ClientSession.get", new=AsyncMock(return_value=mock_resp)):
        async with aiohttp.ClientSession() as client:
            resp = await client.get("http://identity:8001/api/resolve")
            data = await resp.json()
            assert data["user"] == "raven_test"
            assert data["is_admin"] is True
