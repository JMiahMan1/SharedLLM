"""Which device a spoken on/off command means, misheard names included.

The device names are made up, but shaped like a real hub's: separators in the
name ("Den-Lamp", "reading_lamp"), the device repeated in the entity
("Porch-Light Porch Light"), a brand and model after the room, and other kinds of
entity (an automation, a camera, a firmware update) whose names are just as close.
"""
import pytest

from services.gateway.intent_engine import IntentEngine


def _entity(entity_id, name, state="on"):
    return {"entity_id": entity_id, "state": state, "attributes": {"friendly_name": name}}


DEVICES = [
    _entity("light.den_lamp", "Den-Lamp"),
    _entity("light.bulb_a19", "reading_lamp"),
    _entity("switch.porch_light_porch_light", "Porch-Light Porch Light"),
    _entity("light.porch_light_state", "Porch-Light Porch Light State"),
    _entity("camera.porch_light_camera", "Porch Light Camera"),
    _entity("automation.patio_lights_timer", "Patio Lights Timer"),
    _entity("switch.patio_lights", "Patio Lights"),
    _entity("update.bedroom_lamp_left", "bedroom lamp left"),
    _entity("light.bedroom_lamp_left", "Bedroom Light Left"),
    _entity("light.bedroom_lamp_right", "Bedroom Light Right"),
    _entity("light.bath_light_2", "Bath Light 2"),
    _entity("light.bath_light_4", "Bath Light 4"),
    _entity("light.old_bath_light_3", "Bath Light 3", state="unavailable"),
    _entity("light.new_bath_light_3", "Bath Light 3"),
    _entity("light.twin_a", "Hallway Light 1"),
    _entity("light.twin_b", "Hallway Light 1"),
    _entity("switch.garage_door", "Garage Door"),
    _entity("sensor.den_lamp_power", "Den-Lamp Power"),
]


@pytest.fixture(scope="module")
def engine():
    eng = object.__new__(IntentEngine)
    eng.update_entity_cache(DEVICES)
    return eng


@pytest.mark.parametrize("query, intent, expected", [
    # Said right, and said with the separators and filler a person uses.
    ("turn on the den lamp", "turn_on", "light.den_lamp"),
    ("turn on the reading lamp", "turn_on", "light.bulb_a19"),
    ("turn the den lamp off", "turn_off", "light.den_lamp"),
    # Misheard by speech-to-text.
    ("turn on the den amp", "turn_on", "light.den_lamp"),
    ("turn off the din lamp", "turn_off", "light.den_lamp"),
    ("turn on the reading lamb", "turn_on", "light.bulb_a19"),
    ("turn off the porch lite", "turn_off", "switch.porch_light_porch_light"),
    ("turn on bath light to", "turn_on", "light.bath_light_2"),
    ("turn off the bath light for", "turn_off", "light.bath_light_4"),
    ("turn on bath light two", "turn_on", "light.bath_light_2"),
    # The entity id's own words count too ("lamp" where the name says "Light").
    ("turn on the bedroom lamp right", "turn_on", "light.bedroom_lamp_right"),
    # Never an automation, a camera, an update or a sensor, however close the name.
    ("turn on the patio lights", "turn_on", "switch.patio_lights"),
    ("turn on the bedroom lamp left", "turn_on", "light.bedroom_lamp_left"),
    # Of two devices with one name, the one that answers.
    ("turn on bath light three", "turn_on", "light.new_bath_light_3"),
])
def test_a_spoken_name_finds_its_device(engine, query, intent, expected):
    assert engine.extract_entity(query, intent) == expected


@pytest.mark.parametrize("query", [
    "turn on the piano lamp",      # no such device: never the closest lamp
    "turn on the garage",          # one word of "Garage Door" is not enough to open it
    "turn on hallway light 1",     # two live devices share the name: the model asks
    "turn on bath light 5",        # numbers must match exactly
])
def test_no_clear_device_means_no_guess(engine, query):
    assert engine.extract_entity(query, "turn_on") is None


def test_a_state_question_reaches_the_same_device(engine):
    assert engine.extract_entity("is the den lamp on", "turn_on") == "light.den_lamp"
