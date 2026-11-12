"""One reading of location questions for both routes to the location tool."""
import pytest

from services.gateway.location_query import parse_location_query


@pytest.mark.parametrize("q,user,detail,to", [
    ("Where is Sam?", "Sam", None, None),
    ("how fast is Alex driving", "Alex", "speed", None),
    ("When will Sam be home?", "Sam", "eta", "home"),
    ("how long until Alex gets to work?", "Alex", "eta", "work"),
    ("How far is Sam from home", "Sam", "eta", "home"),
    ("when will I get there", None, "eta", "home"),
    ("what's my fuel cost today", None, "cost", None),
])
def test_questions(q, user, detail, to):
    got = parse_location_query(q, None)
    assert (got["user"], got["detail"], got["to"]) == (user, detail, to)


def test_the_asker_is_the_default():
    assert parse_location_query("when will I be home", "alex")["user"] == "alex"


@pytest.mark.parametrize("q,to", [
    ("Who is closest to the school?", "school"),
    ("who's nearest to work", "work"),
])
def test_closest(q, to):
    got = parse_location_query(q, "alex")
    assert got["detail"] == "closest" and got["to"] == to


def test_an_eta_to_a_street_address_keeps_the_address():
    q = parse_location_query("How long until I get to 1234 E Main St, Springfield?", "alex")
    assert q == {"user": "alex", "detail": "eta", "to": "1234 e main st, springfield"}
    assert parse_location_query("when will sam be home?", "alex")["to"] == "home"
