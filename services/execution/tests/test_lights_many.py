"""Several lights at once ("turn off the kitchen lights"): each confirmed, one honest sentence.

Device names are made up.
"""
import pytest

from services.execution.handlers import light
from services.execution.schemas import ExecutionResult, LightsControlRequest, UserContext

CTX = UserContext(user="alex", is_admin=True, ha_url="http://hub", ha_token="t")
BULBS = [f"light.pantry_bulb_{n}" for n in range(1, 5)]


def _req(ids=BULBS, action="turn_off", label="pantry lights"):
    return LightsControlRequest(user_context=CTX, entity_ids=ids, action=action, label=label)


@pytest.fixture
def bulbs(monkeypatch):
    """What each bulb does when asked: its handle_light result, by entity id."""
    plan: dict[str, ExecutionResult] = {}
    asked: list[str] = []

    async def handle(req):
        asked.append(req.entity_id)
        n = req.entity_id.rsplit("_", 1)[-1]
        return plan.get(req.entity_id) or ExecutionResult(
            status="SUCCESS", message="ok", service="light_control",
            detail={"confirmed": True, "state": "off", "name": f"Pantry Bulb {n}"})

    monkeypatch.setattr(light, "handle_light", handle)
    return plan, asked


async def test_all_confirmed_is_one_short_sentence(bulbs):
    res = await light.handle_lights(_req())
    assert res.status == "SUCCESS" and res.message == "The pantry lights are off."
    assert [o["result"] for o in res.detail["outcomes"]] == ["done"] * 4


async def test_one_that_does_not_answer_is_named(bulbs):
    plan, _ = bulbs
    plan[BULBS[3]] = ExecutionResult(status="FAILURE", message="unavailable", service="light_control",
                                     detail={"confirmed": False, "state": "unavailable", "name": "Pantry Bulb 4"})
    res = await light.handle_lights(_req())
    assert res.status == "PARTIAL"
    assert res.message == "3 of 4 pantry lights are off; Pantry Bulb 4 isn't responding."


async def test_never_claims_what_was_not_seen(bulbs):
    plan, _ = bulbs
    plan[BULBS[0]] = ExecutionResult(status="PARTIAL", message="still on", service="light_control",
                                     detail={"confirmed": False, "state": "on", "name": "Pantry Bulb 1"})
    plan[BULBS[1]] = ExecutionResult(status="SUCCESS", message="Turned off", service="light_control",
                                     detail={"confirmed": False, "name": "Pantry Bulb 2"})
    plan[BULBS[2]] = ExecutionResult(status="FAILURE", message="unavailable", service="light_control",
                                     detail={"confirmed": False, "state": "unavailable", "name": "Pantry Bulb 3"})
    res = await light.handle_lights(_req())
    assert res.message == ("1 of 4 pantry lights are off; Pantry Bulb 1 still shows on; "
                           "I couldn't check Pantry Bulb 2; Pantry Bulb 3 isn't responding.")


async def test_none_and_already(bulbs):
    plan, _ = bulbs
    for i, eid in enumerate(BULBS, 1):
        plan[eid] = ExecutionResult(status="FAILURE", message="x", service="light_control",
                                    detail={"state": "unavailable", "name": f"Pantry Bulb {i}"})
    res = await light.handle_lights(_req())
    assert res.status == "FAILURE"
    assert res.message.startswith("None of the pantry lights turned off; Pantry Bulb 1, Pantry Bulb 2, Pantry Bulb 3 "
                                  "and Pantry Bulb 4 aren't responding")
    for i, eid in enumerate(BULBS, 1):
        plan[eid] = ExecutionResult(status="SUCCESS", message="x", service="light_control",
                                    detail={"confirmed": True, "already": True, "state": "off",
                                            "name": f"Pantry Bulb {i}"})
    assert (await light.handle_lights(_req())).message == "The pantry lights are already off."


async def test_a_light_the_person_may_not_switch_is_left_alone(bulbs):
    _, asked = bulbs

    async def may(eid):
        return eid != BULBS[0]
    res = await light.handle_lights(_req(), may)
    assert BULBS[0] not in asked
    assert res.message == "3 of 4 pantry lights are off; pantry bulb 1 isn't yours to switch."
    assert res.status == "PARTIAL"


async def test_the_bulbs_are_asked_together(bulbs, monkeypatch):
    import asyncio
    import time

    async def slow(req):
        await asyncio.sleep(0.2)
        return ExecutionResult(status="SUCCESS", message="ok", service="light_control",
                               detail={"confirmed": True, "state": "on", "name": req.entity_id})
    monkeypatch.setattr(light, "handle_light", slow)
    t = time.monotonic()
    res = await light.handle_lights(_req(action="turn_on"))
    assert time.monotonic() - t < 0.6 and res.message == "The pantry lights are on."


async def test_the_existing_confirmation_reports_its_state(monkeypatch):
    """handle_light's answers carry what handle_lights reads: confirmed, state and name."""
    from services.execution import ha_client, hardware_router

    monkeypatch.setattr(ha_client, "authorize_action", lambda *a: True)
    monkeypatch.setattr(hardware_router, "with_hub_login", lambda ctx: ctx)

    async def unavailable(url, token, eid):
        return {"state": "unavailable", "attributes": {"friendly_name": "Pantry Bulb 9"}}
    monkeypatch.setattr(ha_client, "get_state", unavailable)
    from services.execution.schemas import LightControlRequest
    res = await light.handle_light(LightControlRequest(user_context=CTX, entity_id="light.x", action="turn_off"))
    assert res.detail == {"confirmed": False, "state": "unavailable", "name": "Pantry Bulb 9"}

    async def off(url, token, eid):
        return {"state": "off", "attributes": {"friendly_name": "Pantry Bulb 9"}}
    monkeypatch.setattr(ha_client, "get_state", off)
    res = await light.handle_light(LightControlRequest(user_context=CTX, entity_id="light.x", action="turn_off"))
    assert res.detail["already"] is True and res.detail["confirmed"] is True
