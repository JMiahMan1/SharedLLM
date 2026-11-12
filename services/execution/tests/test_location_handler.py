"""
Tests for location handler and telemetry queries.
"""
from unittest.mock import patch

import pytest

from services.execution.handlers.location import LocationRequest, handle_location
from services.execution.schemas import UserContext


@pytest.mark.asyncio
async def test_handle_location_at_home():
    mock_telemetry = {
        "status": "ok",
        "entity_id": "alex",
        "friendly_name": "Alex",
        "current_zone": "Home",
        "current_speed_mph": 0.0,
        "top_speed_mph": 45.0,
        "is_moving": False,
        "dwell_time_formatted": "2 hrs 15 min",
        "battery": 88,
        "speech": "Alex is at Home. Dwell time: 2 hrs 15 min. Phone battery is at 88%.",
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
        req = LocationRequest(user="Alex")
        ctx = UserContext(user="alex", role="admin")
        req.user_context = ctx
        result = await handle_location(req)

        assert result.status == "SUCCESS"
        assert "Alex is at Home" in result.message
        assert "88%" in result.message
        assert result.detail["current_zone"] == "Home"


@pytest.mark.asyncio
async def test_handle_location_speed_query():
    mock_telemetry = {
        "status": "ok",
        "entity_id": "alex",
        "friendly_name": "Alex",
        "current_zone": None,
        "current_speed_mph": 54.2,
        "top_speed_mph": 65.0,
        "is_moving": True,
        "dwell_time_formatted": "1 min",
        "battery": 82,
        "speech": "Alex is currently traveling at 54.2 mph. Top speed today was 65.0 mph.",
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
        req = LocationRequest(user="Alex", detail="speed")
        ctx = UserContext(user="alex", role="admin")
        req.user_context = ctx
        result = await handle_location(req)

        assert result.status == "SUCCESS"
        assert "traveling at 54.2 mph" in result.message
        assert "Top speed today was 65.0 mph" in result.message


def _client_returning(status, payload, seen):
    class Resp:
        def __init__(self):
            self.status = status

        async def json(self, content_type=None):
            return payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    class Client:
        def get(self, url, **kw):
            seen.append((url, kw.get("params")))
            return Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass
    return Client()


@pytest.mark.asyncio
async def test_eta_answers_by_road_time():
    seen = []
    eta = {"to": "Home", "arrived": False, "duration_s": 1080, "distance_m": 19800, "moving": True, "fix_age_s": 20}
    with patch("services.common.http.get_client_insecure", return_value=_client_returning(200, eta, seen)):
        req = LocationRequest(user="Sam", detail="eta", to="home")
        req.user_context = UserContext(user="alex", role="user")
        result = await handle_location(req)
    assert result.message == "Sam is about 18 minutes from Home by road (12.3 miles)."
    url, params = seen[0]
    assert url.endswith("/people/sam/eta") and params["viewer"] == "alex" and params["to"] == "home"


@pytest.mark.asyncio
async def test_where_is_passes_the_asker_as_viewer():
    """Sharing consent: geo needs to know who is asking."""
    seen = []
    with patch("services.common.http.get_client_insecure",
               return_value=_client_returning(404, {"detail": "activity not shared"}, seen)):
        req = LocationRequest(user="Sam")
        req.user_context = UserContext(user="kate", role="user")
        result = await handle_location(req)
    assert seen[0][1]["viewer"] == "kate"
    assert "don't have a current location" in result.message


@pytest.mark.asyncio
async def test_no_target_means_the_asker_not_a_hardcoded_user():
    seen = []
    with patch("services.common.http.get_client_insecure",
               return_value=_client_returning(404, {"detail": "x"}, seen)):
        req = LocationRequest()
        req.user_context = UserContext(user="sam", role="user")
        await handle_location(req)
    assert "/people/sam/" in seen[0][0]


@pytest.mark.asyncio
async def test_closest_names_the_nearest_by_road():
    seen = []
    data = {"to": "School", "people": [{"user_id": "sam", "duration_s": 720, "fix_age_s": 30},
                                       {"user_id": "alex", "duration_s": 1500, "fix_age_s": 60}]}
    with patch("services.common.http.get_client_insecure", return_value=_client_returning(200, data, seen)):
        req = LocationRequest(detail="closest", to="school")
        req.user_context = UserContext(user="alex", role="user")
        result = await handle_location(req)
    assert result.message == "Sam is closest to School, about 12 minutes away by road. Then Alex 25 minutes."
    assert seen[0][0].endswith("/closest") and seen[0][1]["viewer"] == "alex"
