"""Travel-time questions: place to place, on foot, and the words a model reaches for.

The live failure this pins down: "how long will it take me to get to <street address>"
was answered as "I have no mapping tool", the model then called LocationRequest twice,
and the second call silently became "where is the asker" because `to` was dropped and
`detail` was never set. Each test below is one of those dropped pieces.
"""
from unittest.mock import patch

import pytest

from services.execution.handlers.location import LocationRequest, handle_location
from services.execution.schemas import LocationRequest as SchemaLocationRequest
from services.execution.schemas import UserContext


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
async def test_a_journey_between_two_places_asks_the_eta_route():
    """No person is involved, so it must not go looking for one."""
    seen = []
    eta = {
        "from": "Work",
        "to": "The airport",
        "arrived": False,
        "duration_s": 1440,
        "distance_m": 28968,
        "mode": "driving",
    }
    with patch("services.common.http.get_client_insecure", return_value=_client_returning(200, eta, seen)):
        req = LocationRequest(detail="eta", origin="work", to="the airport")
        req.user_context = UserContext(user="alex", role="user")
        result = await handle_location(req)

    assert result.message == "It is about 24 minutes from Work to The airport by road (18.0 miles)."
    url, params = seen[0]
    assert url.endswith("/eta")
    assert params["origin"] == "work" and params["to"] == "the airport"
    assert "viewer" in params


@pytest.mark.asyncio
async def test_the_arrival_time_is_spoken_in_the_households_clock():
    seen = []
    eta = {
        "from": "Work",
        "to": "The airport",
        "arrived": False,
        "duration_s": 1440,
        "distance_m": 28968,
        "eta": 1_800_000_000.0,
    }
    with patch("services.common.http.get_client_insecure", return_value=_client_returning(200, eta, seen)), \
            patch("services.execution.handlers.location._arrival_clock", return_value="4:15 pm"):
        req = LocationRequest(detail="eta", origin="work", to="the airport")
        req.user_context = UserContext(user="alex", role="user")
        result = await handle_location(req)

    assert result.message == (
        "It is about 24 minutes from Work to The airport by road (18.0 miles), arriving around 4:15 pm."
    )


@pytest.mark.asyncio
async def test_walking_says_on_foot_rather_than_by_road():
    seen = []
    eta = {
        "from": "Home",
        "to": "The park",
        "arrived": False,
        "duration_s": 720,
        "distance_m": 1600,
        "mode": "walking",
    }
    with patch("services.common.http.get_client_insecure", return_value=_client_returning(200, eta, seen)):
        req = LocationRequest(detail="eta", origin="home", to="the park", mode="walking")
        req.user_context = UserContext(user="alex", role="user")
        result = await handle_location(req)

    assert "on foot" in result.message and "by road" not in result.message
    assert seen[0][1]["mode"] == "walking"


@pytest.mark.asyncio
async def test_a_destination_without_a_detail_is_a_journey_not_a_whereabouts_question():
    """The bug: "how long to <address>" was answered with where the asker is."""
    seen = []
    eta = {"to": "Home", "arrived": False, "duration_s": 600, "distance_m": 4000, "moving": True, "fix_age_s": 5}
    with patch("services.common.http.get_client_insecure", return_value=_client_returning(200, eta, seen)):
        req = LocationRequest(to="home")
        req.user_context = UserContext(user="sam", role="user")
        await handle_location(req)

    url, params = seen[0]
    assert url.endswith("/people/sam/eta")
    assert params["to"] == "home"


@pytest.mark.asyncio
async def test_a_destination_the_model_called_destination_still_arrives():
    seen = []
    eta = {"to": "Home", "arrived": False, "duration_s": 600, "distance_m": 4000, "moving": True, "fix_age_s": 5}
    with patch("services.common.http.get_client_insecure", return_value=_client_returning(200, eta, seen)):
        req = LocationRequest(user="Sam", destination="home")
        req.user_context = UserContext(user="alex", role="user")
        await handle_location(req)

    assert seen[0][0].endswith("/people/sam/eta")
    assert seen[0][1]["to"] == "home"


def test_the_handler_accepts_the_words_a_model_reaches_for():
    req = LocationRequest(user="Sam", detail="eta", **{"from": "work"}, travel_mode="walking")
    assert req.origin == "work"
    assert req.mode == "walking"

    other = LocationRequest(detail="eta", from_place="home", to_place="the airport")
    assert other.origin == "home" and other.destination == "the airport"

    started = LocationRequest(detail="eta", start="work", end="home")
    assert started.origin == "work" and started.destination == "home"


def test_the_published_schema_accepts_the_same_guesses():
    """The tool schema is what the model sees; a guess it drops is a call that never works."""
    req = SchemaLocationRequest(tool="LocationRequest", detail="eta", destination="home", **{"from": "work"})
    assert req.destination == "home"
    assert req.origin == "work"
    assert SchemaLocationRequest(detail="eta", travel_mode="walking").mode == "walking"


def test_a_named_place_is_not_overwritten_by_an_alias():
    req = LocationRequest(detail="eta", to="home", destination="the airport")
    assert req.to == "home"
