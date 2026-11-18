# services/execution/handlers/light.py
import asyncio
import logging
import time

try:
    import ha_client
    from schemas import ExecutionResult, LightControlRequest, LightsControlRequest
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult, LightControlRequest, LightsControlRequest

try:
    import hardware_router
except ImportError:
    from .. import hardware_router  # type: ignore[attr-defined]

log = logging.getLogger("execution.light")

#: How long to watch a light after a command before saying what it did. A hub
#: answers a service call before a bulb has changed (a Zigbee or Wi-Fi bulb takes
#: a moment), so "executed" was said before the light came on, or when it never
#: did. Short enough that a voice answer still feels immediate.
CONFIRM_WITHIN_S = 4.0
CONFIRM_POLL_S = 0.4

ACTIVE_STATES = {"on", "playing", "idle", "standby", "home", "cooling", "heating", "drying", "cleaning"}
INACTIVE_STATES = {"off", "unavailable", "unknown", "not_home"}

async def handle_light(req: LightControlRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(f"[light] user={ctx.user} entity={req.entity_id} action={req.action}")

    # Resolve and sanitize entity_id
    full_entity_id = ha_client.sanitize_entity_id("light", req.entity_id)
    domain = full_entity_id.split(".")[0]

    log.info(f"[light] user={ctx.user} (admin={ctx.is_admin}) entity={full_entity_id} action={req.action} (original={req.entity_id})")

    # 1. AUTHORIZATION CHECK
    if not ha_client.authorize_action(ctx.model_dump(), domain, req.action):
        return ExecutionResult(
            status="FAILURE",
            message=f"Access Denied: You are not authorized to perform '{req.action}' on {full_entity_id}. Admin privileges required.",
            service="light_control"
        )

    # 2. HUB LOGIN & STATE CHECK
    #    The caller's own login, else the hub's (never another person's token).
    #    The route checked the caller may control this entity before we got here.
    hub_ctx = hardware_router.with_hub_login(ctx)
    if hub_ctx is None:
        return ExecutionResult(status="FAILURE", message=hardware_router.NO_HUB_MESSAGE, service="light_control")
    ctx = hub_ctx

    current = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
    friendly = _spoken_name(full_entity_id)
    was_unavailable = False
    if current:
        current_state = current.get("state", "").lower()
        friendly = (current.get("attributes") or {}).get("friendly_name") or friendly

        if current_state == "unavailable":
            # Still send it: the hub tries the device anyway (a bulb switched back on at the wall, or one the
            # hub missed for a moment, answers), and says so if it does not. Refusing here left bulbs on
            # in a room that had been asked to turn off (2026-11-18).
            was_unavailable = True
        elif current_state == "unknown":
            return ExecutionResult(
                status="FAILURE",
                message=f"The light '{friendly}' ({full_entity_id}) has an unknown state on the home hub. It may be initializing or misconfigured.",
                service="light_control", detail={"confirmed": False, "state": "unknown", "name": friendly},
            )

        has_attributes = (
            req.brightness_pct is not None
            or req.color_temp is not None
            or req.rgb_color is not None
        )
        if req.action in ("turn_on", "turn_off") and not was_unavailable:
            target_state = "on" if req.action == "turn_on" else "off"
            if current_state == target_state and not (req.action == "turn_on" and has_attributes):
                return ExecutionResult(
                    status="SUCCESS",
                    message=f"{friendly} is already {target_state}.",
                    service="light_control",
                    detail={"confirmed": True, "already": True, "state": target_state, "name": friendly},
                )

    service_data: dict = {}
    if req.brightness_pct is not None:
        service_data["brightness_pct"] = req.brightness_pct
    if req.color_temp is not None:
        service_data["color_temp"] = req.color_temp
    if req.rgb_color is not None:
        service_data["rgb_color"] = list(req.rgb_color)

    # Unified hardware routing: HA first, direct ESPHome when HA unreachable.
    result = await hardware_router.execute_device_command(
        ctx, domain, req.action, full_entity_id, service_data or None,
        service_name="light_control",
    )
    if result.status != "SUCCESS":
        return _not_done(result, friendly, full_entity_id, was_unavailable)
    if req.action not in ("turn_on", "turn_off"):
        return result
    target = "on" if req.action == "turn_on" else "off"
    if (result.detail or {}).get("route") != "ha" or current is None:
        # Sent around the hub, or a light whose state cannot be read: say what
        # was done, in words (never the hub's service name).
        return ExecutionResult(status="SUCCESS", message=f"Turned {target} {friendly}.",
                               service=result.service,
                               detail={**(result.detail or {}), "confirmed": False, "name": friendly})
    return await _confirmed(ctx, full_entity_id, friendly, req, result)


def _not_done(result: ExecutionResult, friendly: str, entity_id: str, was_unavailable: bool) -> ExecutionResult:
    """A command the hub could not carry out, named as the person knows the light (never its id)."""
    detail = {**(result.detail or {}), "confirmed": False, "name": friendly}
    silent = was_unavailable or detail.get("status_code") == 503  # 503: the hub's "the device did not answer"
    if silent:
        detail["state"] = "unavailable"
        message = (f"The light '{friendly}' ({entity_id}) is not responding on the home hub. The device may be "
                   "powered off or disconnected from WiFi. Please check the physical device and its network connection.")
    else:
        message = result.message
    return ExecutionResult(status=result.status, message=message, service=result.service, detail=detail)


def _spoken_name(entity_id: str) -> str:
    """What to call a device when the hub gives no friendly name: light.hall_lamp -> hall lamp."""
    return entity_id.split(".", 1)[-1].replace("_", " ")


async def _confirmed(ctx, entity_id: str, friendly: str, req: LightControlRequest, sent: ExecutionResult) -> ExecutionResult:
    """Watch the light until it shows the new state, then say that state (never the command).

    A light that has not changed within ``CONFIRM_WITHIN_S`` is reported as it is,
    so Jarvis never says it turned on a light that is still off.
    """
    target = "on" if req.action == "turn_on" else "off"
    deadline = time.monotonic() + CONFIRM_WITHIN_S
    state = None
    while True:
        current = await ha_client.get_state(ctx.ha_url, ctx.ha_token, entity_id)
        if current:
            state = str(current.get("state", "")).lower()
            friendly = current.get("attributes", {}).get("friendly_name") or friendly
            if state == target:
                level = _brightness_pct(current) if target == "on" else None
                said = f"{friendly} is on at {level}%." if level is not None and req.brightness_pct is not None else f"{friendly} is {target}."
                return ExecutionResult(status="SUCCESS", message=said, service=sent.service,
                                       detail={**(sent.detail or {}), "confirmed": True, "state": state,
                                               "name": friendly})
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(CONFIRM_POLL_S)
    if state == "unavailable":
        said = f"I asked {friendly} to turn {target}, but it is not responding."
    elif state:
        said = f"I asked {friendly} to turn {target}, but it still shows {state}."
    else:
        said = f"I asked {friendly} to turn {target}, but I could not read it back to check."
    log.warning("[light] %s did not show %s within %.0fs (state=%s)", entity_id, target, CONFIRM_WITHIN_S, state)
    return ExecutionResult(status="PARTIAL", message=said, service=sent.service,
                           detail={**(sent.detail or {}), "confirmed": False, "state": state, "name": friendly})


def _brightness_pct(state: dict) -> int | None:
    raw = (state.get("attributes") or {}).get("brightness")
    if not isinstance(raw, (int, float)):
        return None
    return round(raw * 100 / 255)


# -- several lights at once ---------------------------------------------------------------

async def handle_lights(req: LightsControlRequest, may_control=None) -> ExecutionResult:
    """Switch several lights together and say, in one sentence, what really happened.

    Each light goes through :func:`handle_light` (its own state check and
    confirmation), all at once, so seven bulbs take as long as one. ``may_control``
    (an async ``entity_id -> bool``) is the caller's access check; a light it refuses
    is not touched and is named in the answer. Only lights that showed the new state
    are counted as done.
    """
    ids = list(dict.fromkeys(e for e in req.entity_ids if e))
    allowed: list[str] = []
    refused: list[str] = []
    for eid in ids:
        if may_control is None or await may_control(eid):
            allowed.append(eid)
        else:
            refused.append(eid)

    async def one(eid: str) -> ExecutionResult:
        single = LightControlRequest(user_context=req.user_context, entity_id=eid, action=req.action,
                                     brightness_pct=req.brightness_pct)
        try:
            return await handle_light(single)
        except Exception as exc:  # one bulb's failure is reported, never the whole answer lost
            log.warning("[lights] %s failed: %s", eid, type(exc).__name__)
            return ExecutionResult(status="FAILURE", message=str(exc), service="light_control",
                                   detail={"confirmed": False, "name": _spoken_name(eid)})

    results = dict(zip(allowed, await asyncio.gather(*(one(e) for e in allowed)), strict=True))
    target = "on" if req.action == "turn_on" else "off"
    outcomes = [_outcome(eid, results.get(eid), target) for eid in ids if eid in results]
    outcomes += [{"entity_id": eid, "name": _spoken_name(eid), "result": "refused"} for eid in refused]
    message, status = summarize(req.label, target, outcomes)
    log.info("[lights] %s %s: %s", req.label or "lights", target, message)
    return ExecutionResult(status=status, message=message, service="light_control",
                           detail={"outcomes": outcomes, "target": target})


def _outcome(eid: str, res: ExecutionResult | None, target: str) -> dict:
    detail = (res.detail if res else None) or {}
    name = detail.get("name") or _spoken_name(eid)
    if res is None:
        kind = "failed"
    elif res.status == "SUCCESS" and detail.get("confirmed"):
        kind = "already" if detail.get("already") else "done"
    elif res.status == "SUCCESS":
        kind = "sent"  # sent around the hub: done as far as anyone can tell, not seen
    elif detail.get("state") in ("unavailable", "unknown"):
        kind = "silent"
    elif res.status == "PARTIAL" and detail.get("state") and detail["state"] != target:
        kind = "stuck"
    else:
        kind = "failed"
    return {"entity_id": eid, "name": name, "result": kind, "state": detail.get("state")}


def _names(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _reason(kind: str, names: list[str], target: str) -> str:
    one = len(names) == 1
    who = _names(names)
    if kind == "silent":
        return f"{who} {'isn' if one else 'aren'}'t responding"
    if kind == "stuck":
        return f"{who} still {'shows' if one else 'show'} {'on' if target == 'off' else 'off'}"
    if kind == "refused":
        return f"{who} {'isn' if one else 'aren'}'t yours to switch"
    if kind == "sent":
        return f"I couldn't check {who}"
    return f"{who} didn't answer"


def summarize(label: str, target: str, outcomes: list[dict]) -> tuple[str, str]:
    """(one spoken sentence, status) for a set of light outcomes. Never claims a light it did not see change.

    All showed the new state: "The kitchen lights are off." Some did not:
    "6 of 7 kitchen lights are off; Kitchen Light 4 isn't responding." None did:
    "None of the kitchen lights turned off; ...". A light sent its command around the
    hub (no state to read) is not counted as done, and is named as not checked.
    """
    what = (label or "lights").strip()
    total = len(outcomes)
    good = [o for o in outcomes if o["result"] in ("done", "already")]
    if total == 1:
        o = outcomes[0]
        if o["result"] == "already":
            return f"{o['name']} is already {target}.", "SUCCESS"
        if o["result"] == "done":
            return f"{o['name']} is {target}.", "SUCCESS"
        return f"{o['name']} is not {target}: {_reason(o['result'], [o['name']], target)}.", "PARTIAL"
    problems: dict[str, list[str]] = {}
    for o in outcomes:
        if o["result"] not in ("done", "already"):
            problems.setdefault(o["result"], []).append(o["name"])
    why = "; ".join(_reason(kind, names, target) for kind, names in problems.items())
    if len(good) == total:
        already = all(o["result"] == "already" for o in outcomes)
        return f"The {what} are {'already ' if already else ''}{target}.", "SUCCESS"
    if not good:
        return f"None of the {what} turned {target}; {why}.", "FAILURE"
    return f"{len(good)} of {total} {what} are {target}; {why}.", "PARTIAL"
