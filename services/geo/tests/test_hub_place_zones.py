"""The hub mirrors the family's saved places as zones (``source: geo``); geo must not count them twice."""
from services.geo import main


def test_mirrored_place_zones_are_not_a_second_zone():
    states = [
        {"entity_id": "zone.home", "attributes": {"latitude": 1.0, "longitude": 2.0}},
        {"entity_id": "zone.grandma_s", "attributes": {"latitude": 1.5, "longitude": 2.5, "source": "geo"}},
        {"entity_id": "zone.practice_field", "attributes": {"latitude": 1.6, "longitude": 2.6}},
        {"entity_id": "person.sam", "attributes": {"source": "geo"}},
    ]
    assert [s["entity_id"] for s in main._filter_entities(states, "zone")] == ["zone.home", "zone.practice_field"]
    assert [s["entity_id"] for s in main._filter_entities(states, "person")] == ["person.sam"]
