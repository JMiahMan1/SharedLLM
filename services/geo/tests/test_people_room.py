"""A person's room (the hub's Bluetooth room detection) rides along on /people.

Geo only passes ``room`` and ``room_area_id`` through for ``person.*``; who may
see them is the gateway's decision (services/gateway/room_presence.py).
"""
import services.geo.main as geo


def _state(entity_id: str, **attrs):
    return {"entity_id": entity_id, "state": "home", "last_updated": "2026-11-12T10:00:00+00:00",
            "attributes": {"latitude": 33.0, "longitude": -111.0, "friendly_name": "Alex", **attrs}}


def test_a_persons_room_is_passed_through():
    f = geo._entity_to_feature("person.alex", _state("person.alex", room="Den", room_area_id="den"))
    assert f["properties"]["room"] == "Den"
    assert f["properties"]["room_area_id"] == "den"


def test_no_room_attribute_adds_no_key():
    f = geo._entity_to_feature("person.alex", _state("person.alex"))
    assert "room" not in f["properties"] and "room_area_id" not in f["properties"]


def test_only_people_carry_a_room():
    f = geo._entity_to_feature("device_tracker.alex_pixel", _state("device_tracker.alex_pixel", room="Den"))
    assert "room" not in f["properties"]
