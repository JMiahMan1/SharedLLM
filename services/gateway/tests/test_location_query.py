"""One reading of location questions for both routes to the location tool."""
import pytest

from services.gateway.location_query import parse_location_query


@pytest.mark.parametrize("q,user,detail,to", [
    ("Where is Michele?", "Michele", None, None),
    ("how fast is Jeremiah driving", "Jeremiah", "speed", None),
    ("When will Michele be home?", "Michele", "eta", "home"),
    ("how long until Jeremiah gets to work?", "Jeremiah", "eta", "work"),
    ("How far is Michele from home", "Michele", "eta", "home"),
    ("when will I get there", None, "eta", "home"),
    ("what's my fuel cost today", None, "cost", None),
])
def test_questions(q, user, detail, to):
    got = parse_location_query(q, None)
    assert (got["user"], got["detail"], got["to"]) == (user, detail, to)


def test_the_asker_is_the_default():
    assert parse_location_query("when will I be home", "jeremiah")["user"] == "jeremiah"


@pytest.mark.parametrize("q,to", [
    ("Who is closest to the school?", "school"),
    ("who's nearest to work", "work"),
])
def test_closest(q, to):
    got = parse_location_query(q, "jeremiah")
    assert got["detail"] == "closest" and got["to"] == to
