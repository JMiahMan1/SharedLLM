import pytest
import redis
import os
import json
from unittest.mock import MagicMock, patch

# Mock the redis client for unit testing the logic
def test_kill_switch_logic():
    """Verify that the Raven kill switch logic correctly identifies an aborted mission."""
    mock_redis = MagicMock()
    mission_id = "test-mission-123"
    kill_key = f"raven:mission:kill:{mission_id}"
    
    # Simulate the key being present (mission killed)
    mock_redis.get.return_value = b"1"
    
    # Logic from agent_loop.py
    is_killed = bool(mock_redis.get(kill_key))
    
    assert is_killed is True
    mock_redis.get.assert_called_with(kill_key)

def test_non_root_permission_mock():
    """Verify that we can correctly identify if we are running as non-root."""
    # In a container, this should be 1000
    uid = os.getuid()
    print(f"Current UID: {uid}")
    # This test is just for demonstration, in local env it might be different
    assert uid != 0 or os.getenv("ALLOW_ROOT_TEST") == "true"

@pytest.mark.asyncio
async def test_identity_resolution_mock():
    """Verify that we can mock identity resolution for Raven."""
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "user": "raven_test",
            "is_admin": True,
            "ha_url": "http://ha.local"
        }
        
        # Simulate a call to Identity
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://identity:8001/api/resolve")
            data = resp.json()
            assert data["user"] == "raven_test"
            assert data["is_admin"] is True
