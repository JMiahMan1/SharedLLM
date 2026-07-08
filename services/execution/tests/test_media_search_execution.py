import os

os.environ["INTERNAL_SECRET"] = "test-secret"

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(name="client")
def client_fixture():
    """Setup execution test client."""
    from services.execution.main import app
    return TestClient(app)


@pytest.mark.asyncio
async def test_execution_search_ma_library_only_default_not_found(client):
    """Verify execution search with library_only=True (default) returns empty results for Miles Davis."""
    mock_creds = {
        "ha_url": "http://ha.local:8123",
        "ha_token": "test-ha-token",
        "mass_config_entry_id": "test-mass-id"
    }

    # Mock HA credentials resolution
    with patch("services.execution.main._resolve_mass_ha_creds", new=AsyncMock(return_value=mock_creds)):
        # HA client mock returns empty because it's library_only
        mock_ha_call = AsyncMock(return_value={"results": []})
        with patch("services.execution.handlers.mass_ha_client._call_ha_ma_service", mock_ha_call):
            resp = client.get("/execute/media/music-assistant/search?query=Miles+Davis", headers={"X-Internal-Secret": "test-secret"})

            assert resp.status_code == 200
            assert resp.json() == {"status": "SUCCESS", "results": [], "query": "Miles Davis"}

            # Verify the service call to HA included library_only=True
            called_ha_url, called_ha_token, called_action, called_data = mock_ha_call.call_args[0]
            assert called_action == "search"
            assert called_data["name"] == "Miles Davis"
            assert called_data["library_only"] is True


@pytest.mark.asyncio
async def test_execution_search_ma_not_library_only_found(client):
    """Verify execution search with library_only=False returns Miles Davis results."""
    mock_creds = {
        "ha_url": "http://ha.local:8123",
        "ha_token": "test-ha-token",
        "mass_config_entry_id": "test-mass-id"
    }

    # HA mock returns results when library_only is False
    mock_results = [
        {"name": "Miles Davis - So What", "uri": "spotify://track/miles123", "media_type": "track"}
    ]

    with patch("services.execution.main._resolve_mass_ha_creds", new=AsyncMock(return_value=mock_creds)):
        mock_ha_call = AsyncMock(return_value={"results": mock_results})
        with patch("services.execution.handlers.mass_ha_client._call_ha_ma_service", mock_ha_call):
            resp = client.get("/execute/media/music-assistant/search?query=Miles+Davis&library_only=false", headers={"X-Internal-Secret": "test-secret"})

            assert resp.status_code == 200
            assert resp.json()["status"] == "SUCCESS"
            assert len(resp.json()["results"]) == 1
            assert resp.json()["results"][0]["name"] == "Miles Davis - So What"

            # Verify the service call to HA included library_only=False
            called_ha_url, called_ha_token, called_action, called_data = mock_ha_call.call_args[0]
            assert called_data["library_only"] is False


@pytest.mark.asyncio
async def test_execution_search_ma_failure_propagation(client):
    """Verify execution search endpoint returns status=FAILURE if HA service call fails."""
    mock_creds = {
        "ha_url": "http://ha.local:8123",
        "ha_token": "test-ha-token",
        "mass_config_entry_id": "test-mass-id"
    }

    with patch("services.execution.main._resolve_mass_ha_creds", new=AsyncMock(return_value=mock_creds)):
        # HA service call returns None (meaning it failed/timed out)
        mock_ha_call = AsyncMock(return_value=None)
        with patch("services.execution.handlers.mass_ha_client._call_ha_ma_service", mock_ha_call):
            resp = client.get("/execute/media/music-assistant/search?query=Miles+Davis", headers={"X-Internal-Secret": "test-secret"})

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "FAILURE"
            assert "failed" in data["message"].lower()
            assert data["results"] == []
