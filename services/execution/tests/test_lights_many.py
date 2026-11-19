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
    assert res.message == "3 of 4 pantry lights are off; Pantry Bulb 1 isn't yours to switch."
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

    async def silent(*_a, **_k):  # the hub tried the bulb and it did not answer
        return ExecutionResult(status="FAILURE", message="Service call failed: HA returned 503",
                               service="light_control", detail={"route": "ha", "ok": False, "status_code": 503})
    monkeypatch.setattr(hardware_router, "execute_device_command", silent)
    from services.execution.schemas import LightControlRequest
    res = await light.handle_light(LightControlRequest(user_context=CTX, entity_id="light.x", action="turn_off"))
    assert {k: res.detail[k] for k in ("confirmed", "state", "name")} == {
        "confirmed": False, "state": "unavailable", "name": "Pantry Bulb 9"}

    async def off(url, token, eid):
        return {"state": "off", "attributes": {"friendly_name": "Pantry Bulb 9"}}
    monkeypatch.setattr(ha_client, "get_state", off)
    res = await light.handle_light(LightControlRequest(user_context=CTX, entity_id="light.x", action="turn_off"))
    assert res.detail["already"] is True and res.detail["confirmed"] is True


# -- 2026-11-18: a bulb the hub had just marked unavailable stayed on, and was named by its id ------------

def _single(monkeypatch, state: str, sent: dict, after: str | None = None):
    """handle_light against a hub stand-in: ``state`` before, ``sent`` the router's answer, ``after`` once sent."""
    from services.execution import ha_client, hardware_router
    from services.execution.schemas import LightControlRequest

    monkeypatch.setattr(ha_client, "authorize_action", lambda *a: True)
    monkeypatch.setattr(hardware_router, "with_hub_login", lambda ctx: ctx)
    monkeypatch.setattr(light, "CONFIRM_WITHIN_S", 0.05)
    monkeypatch.setattr(light, "CONFIRM_POLL_S", 0.01)
    calls: list[str] = []
    seen = {"state": state}

    async def get_state(url, token, eid):
        return {"state": seen["state"], "attributes": {"friendly_name": "Kitchen Light 1"}}

    async def execute(ctx, domain, action, eid, data=None, service_name=""):
        calls.append(action)
        if after is not None:
            seen["state"] = after
        return ExecutionResult(service="light_control", **sent)

    monkeypatch.setattr(ha_client, "get_state", get_state)
    monkeypatch.setattr(hardware_router, "execute_device_command", execute)
    req = LightControlRequest(user_context=CTX, entity_id="light.wiz_rgbw_tunable_7398b2", action="turn_off")
    return req, calls


async def test_a_light_shown_unavailable_is_still_sent_the_command(monkeypatch):
    req, calls = _single(monkeypatch, "unavailable", {"status": "SUCCESS", "message": "ok", "detail": {"route": "ha"}},
                         after="off")
    res = await light.handle_light(req)
    assert calls == ["turn_off"]
    assert res.status == "SUCCESS" and res.detail["confirmed"] is True and res.message == "Kitchen Light 1 is off."


async def test_a_silent_light_is_named_as_the_person_knows_it(monkeypatch):
    req, calls = _single(monkeypatch, "unavailable", {
        "status": "FAILURE", "message": "Service call failed: HA returned 503",
        "detail": {"route": "ha", "ok": False, "status_code": 503}})
    res = await light.handle_light(req)
    assert calls == ["turn_off"]
    assert res.detail["name"] == "Kitchen Light 1" and res.detail["state"] == "unavailable"
    assert "7398b2" not in light._outcome(req.entity_id, res, "off")["name"]


async def test_a_light_that_fails_mid_command_keeps_its_name(monkeypatch):
    """It was on a moment ago; the hub's 503 says it went quiet. Never 'wiz rgbw tunable 7398b2'."""
    req, _ = _single(monkeypatch, "on", {"status": "FAILURE", "message": "Service call failed: HA returned 503",
                                         "detail": {"route": "ha", "ok": False, "status_code": 503}})
    res = await light.handle_light(req)
    outcome = light._outcome(req.entity_id, res, "off")
    assert outcome == {"entity_id": req.entity_id, "name": "Kitchen Light 1", "result": "silent",
                       "state": "unavailable"}
    message, status = light.summarize("kitchen lights", "off", [
        outcome, {"entity_id": "light.k2", "name": "Kitchen Light 2", "result": "done", "state": "off"}])
    assert message == "1 of 2 kitchen lights are off; Kitchen Light 1 isn't responding." and status == "PARTIAL"


async def test_any_other_failure_keeps_the_name_and_its_message(monkeypatch):
    req, _ = _single(monkeypatch, "on", {"status": "FAILURE", "message": "Service call failed: HA returned 500",
                                         "detail": {"route": "ha", "ok": False, "status_code": 500}})
    res = await light.handle_light(req)
    assert res.detail["name"] == "Kitchen Light 1" and "state" not in res.detail
    assert res.message == "Service call failed: HA returned 500"


# -- voice may not control some or all of them; names, never ids ----------------------------------

DOWN = [f"light.downstairs_bathroom_{n}_light" for n in (1, 2, 3)]


async def test_a_set_voice_may_not_control_is_refused_for_the_set_by_name(bulbs):
    async def voice_off(eid):
        return "voice_off"
    req = LightsControlRequest(user_context=CTX, entity_ids=DOWN, action="turn_off",
                               label="downstairs bathroom lights", names={e: "Light" for e in DOWN})
    res = await light.handle_lights(req, voice_off)
    assert res.status == "FAILURE"
    assert res.message == ("Voice can't control the downstairs bathroom lights. An admin can turn on "
                           "'Voice can control' for them in Devices.")
    assert "light." not in res.message


async def test_some_voice_may_not_control_are_named_from_their_ids(bulbs):
    async def may(eid):
        return "voice_off" if eid in DOWN else True
    req = LightsControlRequest(user_context=CTX, entity_ids=[*BULBS[:3], *DOWN], action="turn_off",
                               label="bathroom lights", names={e: "Light" for e in DOWN})
    res = await light.handle_lights(req, may)
    assert res.message == ("3 of 6 bathroom lights are off; voice can't control Downstairs Bathroom 1 Light, "
                           "Downstairs Bathroom 2 Light and Downstairs Bathroom 3 Light.")


async def test_when_voice_access_cannot_be_checked_it_says_so(bulbs):
    async def unchecked(eid):
        return "unchecked" if eid == BULBS[0] else True
    res = await light.handle_lights(_req(), unchecked)
    assert res.message == ("3 of 4 pantry lights are off; I couldn't check whether voice may control "
                           "Pantry Bulb 1.")


def test_spoken_names():
    from services.execution.spoken_names import spoken_name

    assert spoken_name("light.downstairs_bathroom_1_light", "Light") == "Downstairs Bathroom 1 Light"
    assert spoken_name("light.kitchen_7", "Kitchen Light 7") == "Kitchen Light 7"
    assert spoken_name("switch.den_plug", None) == "Den Plug"
    assert spoken_name("switch.den_plug", "switch.den_plug") == "Den Plug"
