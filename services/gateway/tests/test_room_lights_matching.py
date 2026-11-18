"""Room and plural phrases name the right room's lights, and only those.

Seen by voice on 2026-11-18: "Turn off the upstairs bathroom lights" matched six lights, the downstairs
bathroom's too, because "upstairs" and "downstairs" look alike to a word similarity. The house below is
made up but shaped like the real one: two bathrooms whose bulbs are in the hub twice (a WiZ copy with an
area and a model name, a Matter copy named after the room, or just "Light" with the room only in its id),
a master bedroom, and pairs of words that tell places apart.
"""
from __future__ import annotations

import pytest

from services.gateway.intent_engine import IntentEngine


def _e(eid, name, area="", state="on", **attrs):
    a = {"friendly_name": name, **attrs}
    if area:
        a["area_id"] = area
    return {"entity_id": eid, "state": state, "attributes": a}


UP_MATTER = [f"light.upstairs_bathroom_{n}" for n in (1, 2, 3)]
DOWN_MATTER = [f"light.downstairs_bathroom_{n}_light" for n in (1, 2, 3)]
UP_WIZ = ["light.wiz_tunable_white_0000a1", "light.wiz_tunable_white_0000a2", "light.wiz_tunable_white_0000a3"]
DOWN_WIZ = ["light.wiz_tunable_white_0000b1", "light.wiz_tunable_white_0000b2", "light.wiz_tunable_white_0000b3"]

HOUSE = [
    *[_e(eid, f"Upstairs Bathroom {n}") for n, eid in enumerate(UP_MATTER, 1)],
    *[_e(eid, "Light") for eid in DOWN_MATTER],
    *[_e(eid, f"WiZ Tunable White 0000A{n}", area="Upstairs Bathroom") for n, eid in enumerate(UP_WIZ, 1)],
    *[_e(eid, f"WiZ Tunable White 0000B{n}", area="Downstairs Bathroom") for n, eid in enumerate(DOWN_WIZ, 1)],
    _e("light.master_bedroom_lamp_left", "Master Bedroom Light Left", area="Master Bedroom"),
    _e("light.master_bedroom_lamp_right", "Master Bedroom Light Right", area="Master Bedroom"),
    _e("light.guest_room_lamp", "Guest Bedroom Light", area="Guest Bedroom"),
    _e("light.front_porch", "Front Porch Light 1"),
    _e("light.front_porch_2", "Front Porch Light 2"),
    _e("light.back_porch", "Back Porch Light 1"),
    _e("light.back_porch_2", "Back Porch Light 2"),
    _e("light.robin_room_1", "Robin Room Light 1"),
    _e("light.robin_room_2", "Robin Room Light 2"),
    _e("light.robbie_room_1", "Robbie Room Light 1"),
    _e("light.robbie_room_2", "Robbie Room Light 2"),
]


@pytest.fixture
def engine():
    eng = object.__new__(IntentEngine)
    eng.update_entity_cache(HOUSE)
    return eng


def _set(engine, query):
    found = engine.extract_device_set(query, "turn_off")
    return sorted(found.entity_ids) if found else None


def test_upstairs_never_takes_the_downstairs_bulbs(engine):
    """By name (the Matter copies) and by room (the WiZ copies), never the other bathroom."""
    assert _set(engine, "turn off the upstairs bathroom lights") == sorted(UP_MATTER + UP_WIZ)


def test_a_bulb_named_just_light_is_found_by_the_room_in_its_id(engine):
    assert _set(engine, "turn off the downstairs bathroom lights") == sorted(DOWN_MATTER + DOWN_WIZ)
    assert _set(engine, "turn off all the lights in the downstairs bathroom") == sorted(DOWN_MATTER + DOWN_WIZ)


def test_a_room_the_house_does_not_have_switches_nothing(engine):
    """No master bathroom yet: never the Master Bedroom's lamps."""
    assert _set(engine, "turn off the master bathroom lights") is None
    assert _set(engine, "turn off the master bedroom lights") == ["light.master_bedroom_lamp_left",
                                                                 "light.master_bedroom_lamp_right"]


@pytest.mark.parametrize("query, expected", [
    ("turn off the front porch lights", ["light.front_porch", "light.front_porch_2"]),
    ("turn off the back porch lights", ["light.back_porch", "light.back_porch_2"]),
    ("turn off the robin room lights", ["light.robin_room_1", "light.robin_room_2"]),
    ("turn off the robbie room lights", ["light.robbie_room_1", "light.robbie_room_2"]),
    ("turn off the guest bedroom lights", ["light.guest_room_lamp"]),
])
def test_words_that_tell_places_or_people_apart_must_be_the_same_word(engine, query, expected):
    assert _set(engine, query) == expected


def test_a_misheard_word_no_device_has_still_counts(engine):
    """"bathrom" is no device's word: taken for "bathroom"; "upstair" (said without its s) is still upstairs."""
    assert _set(engine, "turn off the upstair bathrom lights") == sorted(UP_MATTER + UP_WIZ)


@pytest.mark.parametrize("query, expected", [
    ("turn off upstairs bathroom 2", "light.upstairs_bathroom_2"),
    ("turn off the downstairs bathroom 2 light", "light.downstairs_bathroom_2_light"),
    ("turn off the master bedroom light left", "light.master_bedroom_lamp_left"),
    ("turn off the master bedroom light right", "light.master_bedroom_lamp_right"),
    ("turn off back porch light 1", "light.back_porch"),
    ("turn off robbie room light 1", "light.robbie_room_1"),
])
def test_one_device_is_never_its_opposite(engine, query, expected):
    assert engine.extract_entity(query, "turn_off") == expected


@pytest.mark.parametrize("query", [
    "turn off downstairs bathroom 4",       # no such bulb downstairs: never the upstairs one
    "turn off the master bathroom light left",  # a bathroom the hub does not have: never the bedroom lamp
])
def test_one_device_that_is_not_there_is_not_guessed(engine, query):
    assert engine.extract_entity(query, "turn_off") is None


def test_a_copy_the_hub_hides_as_a_duplicate_is_never_switched():
    """The hub hides one copy of a bulb seen twice (``_hidden``): a room command uses the visible one only."""
    house = [
        _e("light.upstairs_bathroom_1", "Upstairs Bathroom 1", area="Upstairs Bathroom", _hidden=True),
        _e("light.wiz_a1", "WiZ Tunable White 0000A1", area="Upstairs Bathroom"),
        _e("light.wiz_a2", "WiZ Tunable White 0000A2", area="Upstairs Bathroom"),
    ]
    eng = object.__new__(IntentEngine)
    eng.update_entity_cache(house)
    found = eng.extract_device_set("turn off the upstairs bathroom lights", "turn_off")
    assert sorted(found.entity_ids) == ["light.wiz_a1", "light.wiz_a2"]
