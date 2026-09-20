"""
Tests for location tool routing and single-turn execution in Gateway.
"""
import pytest
from services.gateway.orchestrator import SINGLE_TURN_TOOL_ENDPOINTS, _execute_single_tool
from services.gateway.schemas import ResolvedCredentials


def test_location_endpoint_registered():
    assert "locationrequest" in SINGLE_TURN_TOOL_ENDPOINTS
    assert SINGLE_TURN_TOOL_ENDPOINTS["locationrequest"] == "/execute/location"


@pytest.mark.asyncio
async def test_execute_single_tool_location(monkeypatch):
    from unittest.mock import AsyncMock

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={
        "status": "SUCCESS",
        "message": "Jeremiah is at Home. Dwell time: 1 hr 30 min.",
        "detail": {"current_zone": "Home"}
    })

    class MockClient:
        async def post(self, *args, **kwargs):
            return mock_resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_shared_client():
        yield MockClient()

    import services.gateway.main as gw_main
    import services.gateway.orchestrator as orch
    monkeypatch.setattr(orch, "get_all_settings", AsyncMock(return_value={"execution_svc_url": "http://execution:8003"}))
    monkeypatch.setattr(gw_main, "shared_http_client", fake_shared_client)

    creds = ResolvedCredentials(user="jeremiah", role="admin")
    result = await _execute_single_tool(
        action="LocationRequest",
        tool_data={"tool": "LocationRequest", "user": "Jeremiah"},
        query="where is Jeremiah",
        creds=creds,
    )

    assert "Jeremiah is at Home" in result
