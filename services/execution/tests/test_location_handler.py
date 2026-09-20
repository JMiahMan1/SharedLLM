"""
Tests for location handler and telemetry queries.
"""
from unittest.mock import AsyncMock, patch

import pytest
from services.execution.handlers.location import LocationRequest, handle_location
from services.execution.schemas import UserContext


@pytest.mark.asyncio
async def test_handle_location_at_home():
    mock_telemetry = {
        "status": "ok",
        "entity_id": "jeremiah",
        "friendly_name": "Jeremiah",
        "current_zone": "Home",
        "current_speed_mph": 0.0,
        "top_speed_mph": 45.0,
        "is_moving": False,
        "dwell_time_formatted": "2 hrs 15 min",
        "battery": 88,
        "speech": "Jeremiah is at Home. Dwell time: 2 hrs 15 min. Phone battery is at 88%.",
    }

    class MockResponse:
        status = 200

        async def json(self):
            return mock_telemetry

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class MockClient:
        def get(self, *args, **kwargs):
            return MockResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    with patch("services.common.http.get_client_insecure", return_value=MockClient()):
        req = LocationRequest(user="Jeremiah")
        ctx = UserContext(user="jeremiah", role="admin")
        req.user_context = ctx
        result = await handle_location(req)

        assert result.status == "SUCCESS"
        assert "Jeremiah is at Home" in result.message
        assert "88%" in result.message
        assert result.detail["current_zone"] == "Home"


@pytest.mark.asyncio
async def test_handle_location_speed_query():
    mock_telemetry = {
        "status": "ok",
        "entity_id": "jeremiah",
        "friendly_name": "Jeremiah",
        "current_zone": None,
        "current_speed_mph": 54.2,
        "top_speed_mph": 65.0,
        "is_moving": True,
        "dwell_time_formatted": "1 min",
        "battery": 82,
        "speech": "Jeremiah is currently traveling at 54.2 mph. Top speed today was 65.0 mph.",
    }

    class MockResponse:
        status = 200

        async def json(self):
            return mock_telemetry

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class MockClient:
        def get(self, *args, **kwargs):
            return MockResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    with patch("services.common.http.get_client_insecure", return_value=MockClient()):
        req = LocationRequest(user="Jeremiah", detail="speed")
        ctx = UserContext(user="jeremiah", role="admin")
        req.user_context = ctx
        result = await handle_location(req)

        assert result.status == "SUCCESS"
        assert "traveling at 54.2 mph" in result.message
        assert "Top speed today was 65.0 mph" in result.message
