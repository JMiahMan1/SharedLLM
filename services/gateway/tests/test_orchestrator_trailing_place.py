"""The place a request ends on is taken as said, with no list of room words in code.

Execution matches it against the hub's areas (names and aliases) and then its
device names, so any household's rooms work. All rooms here are made up.
"""
import pytest

from services.gateway import orchestrator


@pytest.mark.parametrize(("query", "place"), [
    ("announce dinner is ready in the west wing", "west wing"),
    ("play some jazz in the sunroom.", "sunroom"),
    ("say good night on the den speaker", "den speaker"),
    ("Announce lunch to the Gallery!", "Gallery"),
])
def test_the_place_a_request_ends_on(query, place):
    assert orchestrator._trailing_place(query) == place


@pytest.mark.parametrize("query", [
    "announce dinner in five minutes",  # no "the": not a place
    "play music on repeat",
    "announce that the oven is hot",
    "",
])
def test_no_place_when_none_is_named(query):
    assert orchestrator._trailing_place(query) is None
