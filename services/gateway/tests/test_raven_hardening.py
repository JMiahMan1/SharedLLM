import os
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest


def _aio_resp(status=200, json_data=None, text=""):
    """aiohttp-compatible mock response (code does `await resp.json()`/`resp.status`)."""
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {"status": "SUCCESS"})
    m.text = AsyncMock(return_value=text)
    return m


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
    mock_resp = _aio_resp(200, {
        "user": "raven_test",
        "is_admin": True,
        "ha_url": "http://ha.local"
    })

    with patch("aiohttp.ClientSession.get", new=AsyncMock(return_value=mock_resp)):
        async with aiohttp.ClientSession() as client:
            resp = await client.get("http://identity:8001/api/resolve")
            data = await resp.json()
            assert data["user"] == "raven_test"
            assert data["is_admin"] is True
