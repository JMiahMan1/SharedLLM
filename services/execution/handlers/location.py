# services/execution/handlers/location.py
"""
Handler for Location and Telemetry Queries (Life360 / HA Geo integration).
Answers queries like "Where is Jeremiah?", "How fast is Jeremiah driving?",
"What was my top speed today?", and "Where do I spend the most time?".
"""
import logging
import re
from typing import Any

import aiohttp
from pydantic import BaseModel

try:
    from schemas import ExecutionResult, UserContext
except ImportError:
    from ..schemas import ExecutionResult, UserContext

log = logging.getLogger("execution.location")


class LocationRequest(BaseModel):
    user_context: UserContext | None = None
    user: str | None = None
    person: str | None = None
    target: str | None = None
    detail: str | None = None  # "summary", "speed", "dwell", "frequented", "cost", "vehicle"
    hours: float = 24.0


def _clean_target_name(target_str: str | None, default_user: str | None = None) -> str:
    if not target_str:
        return (default_user or "jeremiah").strip().lower()
    t = target_str.strip().lower()
    # Strip question phrasing
    t = re.sub(r"^(?:where\s+is|where's|where\s+am\s+i|where|who\s+is|how\s+fast\s+is|what\s+is)\s+", "", t)
    t = re.sub(r"[\?\.\,\!]+$", "", t).strip()
    if t in ("me", "myself", "i", ""):
        return (default_user or "jeremiah").strip().lower()
    return t


async def handle_location(req: LocationRequest) -> ExecutionResult:
    ctx = req.user_context
    caller_user = ctx.user if ctx else "jeremiah"
    raw_target = req.user or req.person or req.target or caller_user
    target_name = _clean_target_name(raw_target, caller_user)

    log.info(f"[location] target={target_name} detail={req.detail} caller={caller_user}")

    from services.common.http import get_client_insecure
    from services.config import GEO_SVC_URL, INTERNAL_SECRET

    telemetry_data: dict[str, Any] | None = None

    if GEO_SVC_URL:
        try:
            geo_url = GEO_SVC_URL.rstrip("/")
            async with get_client_insecure() as client:
                async with client.get(
                    f"{geo_url}/people/{target_name}/telemetry?hours={req.hours}",
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
            tracker = get_presence_tracker()
            presence = await tracker.get_user_presence(target_name)
            if presence:
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

        return ExecutionResult(
            status="SUCCESS",
            message=f"I don't have a current location for {target_name.title()} right now.",
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
        # Default full query response ("Where is Jeremiah?")
        msg = default_speech

    return ExecutionResult(
        status="SUCCESS",
        message=msg,
        service="location",
        detail=telemetry_data,
    )
