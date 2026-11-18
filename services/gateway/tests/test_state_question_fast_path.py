"""A yes/no question about a device is answered from its state, whatever the classifier scored."""
import pytest

from services.gateway import main as gateway_main
from services.gateway.intent_engine import IntentEngine

LAMP = {"entity_id": "light.den_lamp", "state": "on", "attributes": {"friendly_name": "Den Lamp"}}
FAN = {"entity_id": "fan.attic_fan", "state": "off", "attributes": {"friendly_name": "Attic Fan"}}


@pytest.fixture
def engine(monkeypatch):
    eng = IntentEngine()
    eng.update_entity_cache([LAMP, FAN])
    monkeypatch.setattr(gateway_main, "engine", eng)
    return eng


@pytest.mark.asyncio
async def test_a_misheard_question_is_answered_from_the_state(engine, monkeypatch):
    async def visible(creds):
        return [LAMP, FAN]

    monkeypatch.setattr(gateway_main, "fetch_ha_entities", visible)
    assert await gateway_main.answer_state_question("is the Den Lamp Ons.", {"user": "sam"}) == "Den Lamp is on."


@pytest.mark.asyncio
async def test_a_device_the_person_cannot_see_is_not_answered(engine, monkeypatch):
    async def visible(creds):
        return [FAN]

    monkeypatch.setattr(gateway_main, "fetch_ha_entities", visible)
    assert await gateway_main.answer_state_question("Is the den lamp on?", {"user": "kid"}) is None


@pytest.mark.asyncio
async def test_a_question_naming_no_device_is_left_to_routing(engine, monkeypatch):
    async def visible(creds):  # must not be called
        raise AssertionError("no device named, so no state is read")

    monkeypatch.setattr(gateway_main, "fetch_ha_entities", visible)
    assert await gateway_main.answer_state_question("Is it going to rain tomorrow?", {"user": "sam"}) is None
    assert await gateway_main.answer_state_question("Is the toaster on?", {"user": "sam"}) is None
