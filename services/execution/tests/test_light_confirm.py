"""A light is confirmed by what it shows, including the brightness asked for.

An already-on light that is asked for a new brightness shows "on" at once, with its old level, so "on" alone
is not a confirmation: the answer waits for the level to come within a couple of points, and otherwise says
the level it is really at. The hub is a fake that changes the light's state over successive reads. Device
names are made up.
"""
import pytest

from services.execution.handlers import light
from services.execution.schemas import ExecutionResult, LightControlRequest, UserContext

CTX = UserContext(user="alex", is_admin=True, ha_url="http://hub", ha_token="t")
LAMP = "light.reading_lamp"
SENT = ExecutionResult(status="SUCCESS", message="sent", service="light_control", detail={"route": "ha"})


def _state(state: str, brightness: int | None = None) -> dict:
    attrs = {"friendly_name": "Reading Lamp"}
    if brightness is not None:
        attrs["brightness"] = brightness
    return {"state": state, "attributes": attrs}


@pytest.fixture
def hub(monkeypatch):
    """The light's state on each read: the last one repeats."""
    reads: list[dict] = []

    async def get_state(_url, _token, entity_id):
        assert entity_id == LAMP
        return reads.pop(0) if len(reads) > 1 else reads[0]

    monkeypatch.setattr(light.ha_client, "get_state", get_state)
    monkeypatch.setattr(light, "CONFIRM_WITHIN_S", 0.3)
    monkeypatch.setattr(light, "CONFIRM_POLL_S", 0.01)
    return reads


def _req(brightness=None, action="turn_on"):
    return LightControlRequest(user_context=CTX, entity_id=LAMP, action=action, brightness_pct=brightness)


async def test_an_on_light_is_confirmed_only_once_it_reaches_the_new_level(hub):
    hub += [_state("on", 204), _state("on", 204), _state("on", 102)]  # 80%, 80%, then 40%
    res = await light._confirmed(CTX, LAMP, "Reading Lamp", _req(40), SENT)
    assert res.status == "SUCCESS" and res.detail["confirmed"] is True
    assert res.message == "Reading Lamp is on at 40%."
    assert res.detail["brightness_pct"] == 40


async def test_a_level_within_two_points_counts(hub):
    hub.append(_state("on", 99))  # 39%
    res = await light._confirmed(CTX, LAMP, "Reading Lamp", _req(40), SENT)
    assert res.status == "SUCCESS" and res.message == "Reading Lamp is on at 39%."


async def test_a_light_that_keeps_its_old_level_is_partial_and_says_the_real_one(hub):
    hub.append(_state("on", 204))
    res = await light._confirmed(CTX, LAMP, "Reading Lamp", _req(40), SENT)
    assert res.status == "PARTIAL" and res.detail["confirmed"] is False
    assert res.message == "I asked Reading Lamp to go to 40%, but it is at 80%."
    assert res.detail["brightness_pct"] == 80


async def test_a_light_that_shows_no_brightness_is_not_claimed_to_be_at_the_level(hub):
    hub.append(_state("on"))
    res = await light._confirmed(CTX, LAMP, "Reading Lamp", _req(40), SENT)
    assert res.status == "PARTIAL"
    assert "could not read its brightness" in res.message


async def test_plain_on_and_off_still_confirm_on_state_alone(hub):
    hub.append(_state("on", 204))
    assert (await light._confirmed(CTX, LAMP, "Reading Lamp", _req(), SENT)).message == "Reading Lamp is on."
    hub[:] = [_state("off")]
    res = await light._confirmed(CTX, LAMP, "Reading Lamp", _req(action="turn_off"), SENT)
    assert res.status == "SUCCESS" and res.message == "Reading Lamp is off."
