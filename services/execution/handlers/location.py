# services/execution/handlers/location.py
"""
Handler for Location and Telemetry Queries (Life360 / HA Geo integration).
Answers queries like "Where is Alex?", "How fast is Alex driving?",
"What was my top speed today?", and "Where do I spend the most time?".
"""
import logging
import re
from typing import Any

import aiohttp
from pydantic import BaseModel, model_validator

try:
    from schemas import ExecutionResult, UserContext
except ImportError:
    from ..schemas import ExecutionResult, UserContext

log = logging.getLogger("execution.location")


class LocationRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def map_travel_aliases(cls, data: Any) -> Any:
        """Keep a named origin however the model named it (``from``, ``start``...)."""
        if isinstance(data, dict):
            data = dict(data)
            if not data.get("origin"):
                for alias in ("from", "from_place", "start", "start_place", "origin_place"):
                    if isinstance(data.get(alias), str) and data[alias].strip():
                        data["origin"] = data[alias]
                        break
            if not data.get("destination"):
                for alias in ("destination", "to_place", "end", "end_place"):
                    if isinstance(data.get(alias), str) and data[alias].strip():
                        data["destination"] = data[alias]
                        break
            if not data.get("mode") and isinstance(data.get("travel_mode"), str):
                data["mode"] = data["travel_mode"]
        return data

    user_context: UserContext | None = None
    user: str | None = None
    person: str | None = None
    target: str | None = None
    detail: str | None = None  # "summary", "speed", "dwell", "frequented", "cost", "vehicle", "eta", "closest"
    to: str | None = None  # for "eta": a Home Assistant zone ("home", "work") or "lat,lon"
    destination: str | None = None  # alias for "to"
    origin: str | None = None  # where the trip starts; omitted = the person's latest fix
    mode: str | None = None  # "driving" (default) or "walking"
    hours: float = 24.0



def _clean_target_name(target_str: str | None, default_user: str | None = None) -> str:
    if not target_str:
        return (default_user or "").strip().lower()
    t = target_str.strip().lower()
    # Strip question phrasing
    t = re.sub(r"^(?:where\s+is|where's|where\s+am\s+i|where|who\s+is|how\s+fast\s+is|what\s+is)\s+", "", t)
    t = re.sub(r"[\?\.\,\!]+$", "", t).strip()
    if t in ("me", "myself", "i", ""):
        return (default_user or "").strip().lower()
    return t


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    return f"{m // 60} h {m % 60:02d} min"


def _arrival_clock(epoch: Any) -> str:
    """When they arrive, as the household reads a clock ("4:15 pm"), or "".

    The geo service answers with an epoch, which is no answer to a person; the
    household's zone lives in ``household_tz``, and a box with no zone set is a
    reason to say nothing rather than to say the wrong hour.
    """
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return ""
    try:
        import datetime

        from services.common import household_tz

        when = datetime.datetime.fromtimestamp(float(epoch), household_tz.zoneinfo())
    except Exception:
        return ""
    hour = when.hour % 12 or 12
    return f"{hour}:{when.minute:02d} {'am' if when.hour < 12 else 'pm'}"


async def _eta(target_name: str, to: str | None, origin: str | None, caller: str, is_admin: bool,
               mode: str | None = None) -> ExecutionResult:
    """ "When will X be home?" / "how long from A to B?": travel time by road or on foot.

    A named origin asks the geo service to route place to place (``/eta``); with
    none, it routes from that person's latest fix as before.
    """
    from services.common.http import get_client_insecure
    from services.config import GEO_SVC_URL, INTERNAL_SECRET

    name = target_name.title() if target_name else ""
    params = {"to": to or "home", "viewer": caller, "is_admin": "true" if is_admin else "false"}
    if mode:
        params["mode"] = mode
    base = f"{GEO_SVC_URL.rstrip('/')}"
    if origin:
        params["origin"] = origin
        url = f"{base}/eta"
    else:
        url = f"{base}/people/{target_name}/eta"
    try:
        async with get_client_insecure() as client, client.get(
            url, params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=10.0),
        ) as resp:
            data = await resp.json(content_type=None)
            status = resp.status
    except Exception as e:
        log.warning(f"[location] ETA lookup failed: {e}")
        return ExecutionResult(status="FAILURE", message="I can't work out travel times right now.", service="location")
    if status == 404 and origin is None and "shared" in str(data.get("detail", "")):
        return ExecutionResult(status="SUCCESS", message=f"I don't have a current location for {name} right now.",
                               service="location")
    if status != 200:
        return ExecutionResult(status="FAILURE", message=str(data.get("detail") or "No travel time available."),
                               service="location", detail=data)
    place = data.get("to") or (to or "there")
    walking = str(data.get("mode") or mode or "driving").lower() == "walking"
    road = "on foot" if walking else "by road"
    miles = data.get("distance_m", 0) * 0.000621371
    clock = _arrival_clock(data.get("eta"))
    if origin is not None:
        start = data.get("from") or origin
        if data.get("arrived"):
            msg = f"{start} and {place} are the same place."
        else:
            msg = f"It is about {_minutes(data['duration_s'])} from {start} to {place} {road} ({miles:.1f} miles)"
            msg += f", arriving around {clock}." if clock else "."
    else:
        if data.get("arrived"):
            msg = f"{name} is at {place} now."
        else:
            msg = f"{name} is about {_minutes(data['duration_s'])} from {place} {road} ({miles:.1f} miles)"
            msg += f", arriving around {clock}" if clock else ""
            msg += "."
            if not data.get("moving"):
                msg += " They don't seem to be driving at the moment."
            if (data.get("fix_age_s") or 0) > 900:
                msg += f" (Their last location is {_minutes(data['fix_age_s'])} old.)"
    return ExecutionResult(status="SUCCESS", message=msg, service="location", detail=data)
async def _closest(
    to: str | None, caller: str, is_admin: bool, mode: str | None = None) -> ExecutionResult:
    """ "Who's closest to the school?": drive time by road for everyone the
    asker may see (geo /closest), nearest first."""
    from services.common.http import get_client_insecure
    from services.config import GEO_SVC_URL, INTERNAL_SECRET

    if not to:
        return ExecutionResult(status="FAILURE", message="Closest to where?", service="location")
    params = {"to": to, "viewer": caller, "is_admin": "true" if is_admin else "false"}
    if mode:
        params["mode"] = mode

    try:
        async with get_client_insecure() as client, client.get(
            f"{GEO_SVC_URL.rstrip('/')}/closest", params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=15.0),
        ) as resp:
            data = await resp.json(content_type=None)
            status = resp.status
    except Exception as e:
        log.warning(f"[location] closest lookup failed: {e}")
        return ExecutionResult(status="FAILURE", message="I can't work out drive times right now.", service="location")
    if status != 200:
        return ExecutionResult(status="FAILURE", message=str(data.get("detail") or "No drive times available."),
                               service="location", detail=data)
    people = data.get("people") or []
    place = data.get("to") or to
    if not people:
        return ExecutionResult(status="SUCCESS", message=f"I don't know where anyone is right now to compare against {place}.",
                               service="location", detail=data)
    first = people[0]
    msg = f"{first['user_id'].title()} is closest to {place}, about {_minutes(first['duration_s'])} away by road."
    if len(people) > 1:
        rest = ", ".join(f"{p['user_id'].title()} {_minutes(p['duration_s'])}" for p in people[1:4])
        msg += f" Then {rest}."
    return ExecutionResult(status="SUCCESS", message=msg, service="location", detail=data)


async def _room_answer(target_name: str, caller: str, ctx: UserContext | None) -> tuple[ExecutionResult | None, str]:
    """``(answer, note)``: the room answer when room detection knows it, else a note saying
    why it could not answer (empty when it is not set up, or the asker may not see it)."""
    from services.execution import hardware_router, room_presence

    # The caller's own hub login, else the hub's: who may see whose room is
    # Jarvis's rule (may_see_room), not the login's.
    ha_url, ha_token = hardware_router.hub_pair(ctx) if ctx else ("", "")
    if not ha_url or not ha_token:
        return None, ""
    if not await room_presence.may_see_room(caller, target_name):
        # Not theirs to see: no room, and no hint that there is one.
        return None, ""
    try:
        p = await room_presence.person_room(target_name, ha_url, ha_token)
    except Exception as e:
        log.warning(f"[location] the hub's room detection could not be read: {type(e).__name__}")
        return None, "The hub's room detection could not be read."
    if p.status == "known" and p.room:
        name = target_name.title()
        msg = (f"{name} is {room_presence.in_room(p.room)} (Bluetooth room detection, heard "
               f"{room_presence.ago(p.age_s)}).")
        return ExecutionResult(status="SUCCESS", message=msg, service="location",
                               detail={"answered_by": "room", "room": p.as_detail()}), ""
    if p.status in ("not_set_up", "no_person"):
        return None, ""
    reason = room_presence.why_not(p)
    return None, f"{reason[0].upper()}{reason[1:]}."


async def handle_location(req: LocationRequest) -> ExecutionResult:
    ctx = req.user_context
    caller_user = (ctx.user if ctx else "") or ""
    is_admin = bool(ctx and ctx.is_admin)
    raw_target = req.user or req.person or req.target or caller_user
    detail = (req.detail or "").lower()
    to = (req.to or req.destination or "").strip() or None
    origin = (req.origin or "").strip() or None
    if not detail and (to or origin):
        # "How long to get to X" and "from A to B" are travel questions even when
        # the model leaves `detail` off; answering them as "where is X" is how a
        # question about a street address came back as someone's whereabouts.
        detail = "eta"
    if detail == "closest":
        return await _closest(to, caller_user, is_admin, req.mode)
    if detail == "eta" and origin:
        # Place to place needs no person; only tidy a target the model also sent.
        tidy = _clean_target_name(req.user or req.person or req.target) if (req.user or req.person or req.target) else ""
        return await _eta(tidy, to, origin, caller_user, is_admin, req.mode)
    target_name = _clean_target_name(raw_target, caller_user)
    if not target_name:
        return ExecutionResult(status="FAILURE", message="Whose location do you mean?", service="location")

    log.info(f"[location] target={target_name} detail={req.detail} caller={caller_user}")
    if detail == "eta":
        return await _eta(target_name, to, None, caller_user, is_admin, req.mode)

    # A plain "where is X?" answers with the room first when the hub's Bluetooth room
    # detection knows it (and the asker may see it); otherwise geo answers, and says so.
    room_note = ""
    if (req.detail or "summary").lower() == "summary":
        room, room_note = await _room_answer(target_name, caller_user, ctx)
        if room is not None:
            return room

    from services.common.http import get_client_insecure
    from services.config import GEO_SVC_URL, INTERNAL_SECRET

    telemetry_data: dict[str, Any] | None = None

    if GEO_SVC_URL:
        try:
            geo_url = GEO_SVC_URL.rstrip("/")
            # The asker as viewer: "where is X?" respects X's sharing choice.
            async with get_client_insecure() as client, client.get(
                f"{geo_url}/people/{target_name}/telemetry",
                params={"hours": req.hours, "viewer": caller_user, "is_admin": "true" if is_admin else "false"},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as resp:
                if resp.status == 200:
                    telemetry_data = await resp.json()
        except Exception as e:
            log.warning(f"[location] Failed to query Geo service: {e}")

    # Fallback to BLE / Redis PresenceTracker if Geo service has no location data
    if not telemetry_data or telemetry_data.get("status") != "ok":
        try:
            from services.execution.presence import get_presence_tracker
            from services.execution.room_presence import may_see_room
            tracker = get_presence_tracker()
            presence = await tracker.get_user_presence(target_name)
            # A room is the same private fact whichever detector found it.
            if presence and await may_see_room(caller_user, target_name):
                room = presence.get("room", "home")
                name_cap = target_name.title()
                if room in ("home", "not_home", "away"):
                    msg = f"{name_cap} is currently {room.replace('_', ' ')}."
                else:
                    msg = f"{name_cap} is in the {room.replace('_', ' ')}."
                return ExecutionResult(
                    status="SUCCESS",
                    message=msg,
                    service="location",
                    detail={"presence": presence, "source": "ble"},
                )
        except Exception as pe:
            log.warning(f"[location] Presence fallback check failed: {pe}")

        none_msg = f"I don't have a current location for {target_name.title()} right now."
        if room_note:
            none_msg = f"{none_msg} {room_note}"
        return ExecutionResult(
            status="SUCCESS",
            message=none_msg,
            service="location",
            detail=telemetry_data,
        )

    # We have rich telemetry data from Geo service!
    name = telemetry_data.get("friendly_name", target_name.title())
    cur_speed = telemetry_data.get("current_speed_mph", 0.0)
    top_speed = telemetry_data.get("top_speed_mph", cur_speed)
    is_moving = telemetry_data.get("is_moving", False)
    dwell_formatted = telemetry_data.get("dwell_time_formatted", "a short while")
    current_zone = telemetry_data.get("current_zone")
    frequented = telemetry_data.get("frequented_locations", [])
    vehicle = telemetry_data.get("vehicle", {})
    dist_miles = telemetry_data.get("distance_traveled_miles", 0.0)
    default_speech = telemetry_data.get("speech", "")

    detail_type = (req.detail or "").lower()

    if detail_type == "speed":
        if is_moving:
            msg = f"{name} is traveling at {cur_speed} mph. Top speed today was {top_speed} mph."
        else:
            msg = f"{name} is currently still (0 mph). Top speed today was {top_speed} mph."
    elif detail_type in ("dwell", "still"):
        loc = f"at {current_zone}" if current_zone else "at the current location"
        msg = f"{name} has been still {loc} for {dwell_formatted}."
    elif detail_type == "frequented":
        if frequented:
            loc_summaries = [f"{item['name']} ({item['dwell_formatted']})" for item in frequented[:3]]
            msg = f"{name}'s most frequented locations today are: {', '.join(loc_summaries)}."
        else:
            msg = f"{name} has spent the day {f'at {current_zone}' if current_zone else 'at their current location'}."
    elif detail_type in ("cost", "vehicle"):
        if vehicle:
            veh_name = vehicle.get("name", "designated vehicle")
            veh_mpg = vehicle.get("mpg", 0.0)
            cost = vehicle.get("estimated_cost_usd", 0.0)
            msg = f"{name} has traveled {dist_miles} miles today in {veh_name} ({veh_mpg} MPG). Estimated fuel cost is ${cost:.2f}."
        else:
            msg = f"{name} has traveled {dist_miles} miles today. No vehicle is currently designated. You can add and assign your vehicle in Settings > Location & Vehicles to calculate MPG and travel costs."
    else:
        # Default full query response ("Where is Alex?")
        msg = default_speech
        telemetry_data = {**telemetry_data, "answered_by": "geo"}
        if room_note and msg:
            # Room detection is set up for them but could not answer: say which source did.
            msg = f"{msg} (That is from their phone's location; {room_note[0].lower()}{room_note[1:]})"

    return ExecutionResult(
        status="SUCCESS",
        message=msg,
        service="location",
        detail=telemetry_data,
    )
