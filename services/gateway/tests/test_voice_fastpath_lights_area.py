"""Voice satellite area fallback for lights."""
from services.gateway import fast_lights
from services.gateway.intent_engine import IntentEngine


def _entity(eid, name, area):
    return {"entity_id": eid, "state": "on", "attributes": {"friendly_name": name, "area_id": area}}


DEVICES = [
    _entity("light.den_lamp", "Den Lamp", "den"),
    _entity("light.den_reading", "Reading Lamp", "den"),
    _entity("light.kitchen_main", "Kitchen Main", "kitchen"),
]


def test_light_set_for_voice_context_area():
    vc = {"area": "Den", "area_id": "den"}
    eng = IntentEngine()
    eng.update_entity_cache(DEVICES)
    found = fast_lights.light_set_for(eng, "turn off the lights", "turn_off", DEVICES, vc)
    assert found is not None
    assert sorted(found.entity_ids) == ["light.den_lamp", "light.den_reading"]


def test_query_named_room_keeps_named_room():
    # a room named in the query wins over the satellite area
    import services.gateway.intent_engine as ie
    eng = ie.IntentEngine()
    eng.update_entity_cache(DEVICES)
    found = fast_lights.light_set_for(eng, "turn off the kitchen lights", "turn_off", DEVICES, {"area": "Den", "area_id": "den"})
    assert found is not None
    assert found.entity_ids == ["light.kitchen_main"]
