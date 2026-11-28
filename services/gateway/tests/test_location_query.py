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
    assert got["origin"] is None and got["mode"] is None


def test_an_eta_to_a_street_address_keeps_the_address():
    q = parse_location_query("How long until I get to 1234 E Main St, Springfield?", "alex")
    assert q == {"user": "alex", "detail": "eta", "to": "1234 e main st, springfield",
                 "origin": None, "mode": None}
    assert parse_location_query("when will sam be home?", "alex")["to"] == "home"


@pytest.mark.parametrize("q,origin,to,mode", [
    # The sentence a person actually says, which used to be read as "how long
    # has he been still" and then ran as a whereabouts question.
    ("How long will it take me to get to Nellis auction?", None, "nellis auction", None),
    ("how long will it take me to get to 4720 E Van Buren St, Phoenix, AZ 85008?",
     None, "4720 e van buren st, phoenix, az 85008", None),
    ("eta to the office", None, "office", None),
    # Both ends named: no person is needed for this one at all.
    ("how long to drive from work to the school", "work", "school", "driving"),
    ("how long does it take to walk from home to the park", "home", "park", "walking"),
    ("how long will it take to get home", None, "home", None),
])
def test_journeys_carry_their_ends_and_mode(q, origin, to, mode):
    got = parse_location_query(q, "alex")
    assert (got["origin"], got["to"], got["mode"]) == (origin, to, mode)
    assert got["detail"] == "eta"


def test_a_journey_names_the_asker_only_when_it_has_to():
    # "how long to drive from work to the school" is about nobody in
    # particular; the ends carry the question.
    assert parse_location_query("how long to drive from work to the school", "alex")["user"] == "alex"
    assert parse_location_query("how long will it take me to get to the airport", "alex")["user"] == "alex"


def test_mode_is_not_invented_for_a_speed_question():
    got = parse_location_query("how fast is Alex driving", "alex")
    assert got["detail"] == "speed" and got["mode"] is None


def test_frequency_and_cost_questions_are_unchanged():
    assert parse_location_query("locations I frequent the most", "alex")["detail"] == "frequented"
    assert parse_location_query("what's my fuel cost today", "alex")["detail"] == "cost"
