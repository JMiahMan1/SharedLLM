# services/geo/main.py
"""SharedLLM `geo` service — Life360-style family location (wraps Home Assistant).

Backend: Home Assistant (https://ha.sumemail.com:8095). Reads `person` /
`device_tracker` / `zone` entity states via the HA REST API (token resolved at
boot by services.config.resolve_runtime_config into HA_URL / HA_TOKEN), exposes
them as GeoJSON for a MapLibre client, and accepts location pushes via the HA
`device_tracker.see` service.

Design + rationale: see S26-Setup/geo-service/README.md and
docs/GEO_SERVICE.md. OSM = data, MapLibre = renderer. Traccar is the documented
upgrade path if HA's sharing/geofence UX proves insufficient.
"""
import json
import logging
import math
import os
import re
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

_STATIC = Path(__file__).resolve().parent / "static"

from services.common.http import get_client_insecure
from services.config import HA_TOKEN, HA_URL, INTERNAL_SECRET, REDIS_URL
from services.shared.info_endpoint import info_router

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None


def _verify_internal_secret(header_secret: str | None, query_secret: str | None = None) -> bool:
    if not INTERNAL_SECRET:
        return True
    return header_secret == INTERNAL_SECRET or query_secret == INTERNAL_SECRET

log = logging.getLogger(__name__)

_START_TIME = time.time()
_redis = None


async def get_redis():
    global _redis
    if _redis is None and REDIS_URL and aioredis is not None:
        try:
            _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            await _redis.ping()
            log.info("[Geo] Redis connected: %s", REDIS_URL)
        except Exception as e:
            log.warning(f"[Geo] Redis connection failed: {e}")
            _redis = None
    return _redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    from services.config import resolve_runtime_config
    await resolve_runtime_config()
    log.info("[Geo] runtime config resolved (HA_URL=%s)", bool(HA_URL))
    await get_redis()
    yield
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass


app = FastAPI(title="SOA Geo Service", lifespan=lifespan)
app.include_router(info_router)


@app.get("/")
def index():
    """Serve the MapLibre family-location web client."""
    return FileResponse(_STATIC / "index.html")


def _ha_headers() -> dict:
    if not HA_TOKEN:
        raise HTTPException(status_code=500, detail="HA_TOKEN not resolved from Identity")
    return {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def _entity_to_feature(entity_id: str, state: dict) -> dict | None:
    """Convert an HA entity state dict into a GeoJSON Feature (or None)."""
    attrs = state.get("attributes", {})
    lat = attrs.get("latitude")
    lon = attrs.get("longitude")
    if lat is None or lon is None:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "entity_id": entity_id,
            "state": state.get("state"),
            "friendly_name": attrs.get("friendly_name", entity_id),
            "radius": attrs.get("radius", 100),
            "gps_accuracy": attrs.get("gps_accuracy"),
            "speed": attrs.get("speed"),
            "course": attrs.get("course") or attrs.get("bearing"),
            "battery": attrs.get("battery_level") or attrs.get("battery"),
            "source_type": attrs.get("source_type"),
            "in_zones": attrs.get("in_zones"),
            "last_updated": state.get("last_updated") or state.get("last_changed"),
        },
    }


async def _ha_get_states() -> list:
    if not HA_URL:
        raise HTTPException(status_code=500, detail="HA_URL not resolved from Identity")
    async with get_client_insecure() as client, client.get(f"{HA_URL}/api/states", headers=_ha_headers()) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=502, detail=f"HA returned {resp.status}")
        return await resp.json()


def _filter_entities(states: list, domain: str) -> list:
    return [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "geo",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": _START_TIME,
        "ha_configured": bool(HA_URL and HA_TOKEN),
    }


@app.get("/people")
async def get_people():
    """All person + device_tracker entities as a GeoJSON FeatureCollection."""
    states = await _ha_get_states()
    features = []
    for s in _filter_entities(states, "person") + _filter_entities(states, "device_tracker"):
        f = _entity_to_feature(s["entity_id"], s)
        if f:
            features.append(f)
    return {"type": "FeatureCollection", "features": features}


@app.get("/zones")
async def get_zones():
    """HA zones (geofences) as a GeoJSON FeatureCollection."""
    states = await _ha_get_states()
    features = []
    for s in _filter_entities(states, "zone"):
        f = _entity_to_feature(s["entity_id"], s)
        if f:
            features.append(f)
    return {"type": "FeatureCollection", "features": features}


@app.get("/people/{entity_id:path}/history")
async def get_history(entity_id: str, samples: int = Query(200, ge=1, le=2000)):
    """Location history for a person/device_tracker from HA Recorder.

    HA returns a list of [state, attributes, last_changed] rows; we extract the
    lat/lon points for trip replay.
    """
    if not HA_URL:
        raise HTTPException(status_code=500, detail="HA_URL not resolved from Identity")
    url = f"{HA_URL}/api/history/period"
    params = {"filter_entity_id": entity_id, "significant_changes_only": "true", "minimal_response": "true"}
    async with get_client_insecure() as client, client.get(url, headers=_ha_headers(), params=params) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=502, detail=f"HA returned {resp.status}")
        data = await resp.json()
    points = []
    for row in (data[0] if isinstance(data, list) and data else []):
        attrs = row.get("a", {})  # minimal_response puts attributes under "a"
        lat = attrs.get("latitude")
        lon = attrs.get("longitude")
        if lat is not None and lon is not None:
            points.append({"t": row.get("lu") or row.get("last_updated"), "lat": lat, "lon": lon})
        if len(points) >= samples:
            break
    return {"entity_id": entity_id, "points": points}


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in meters."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def _format_duration(seconds: float) -> str:
    secs = int(max(0, seconds))
    if secs < 60:
        return "less than a minute"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min" if mins > 1 else "1 min"
    hours = mins // 60
    rem_mins = mins % 60
    if rem_mins == 0:
        return f"{hours} hr" if hours == 1 else f"{hours} hrs"
    return f"{hours} hr {rem_mins} min" if hours == 1 else f"{hours} hrs {rem_mins} min"


async def record_point(
    entity_id: str,
    lat: float,
    lon: float,
    accuracy: float | None = None,
    speed: float | None = None,
    bearing: float | None = None,
    battery: int | None = None,
    timestamp: float | None = None,
):
    r = await get_redis()
    if not r:
        return
    ts = timestamp or time.time()
    clean_id = entity_id.split(".")[-1].lower()
    point = {
        "t": ts,
        "lat": lat,
        "lon": lon,
        "acc": accuracy or 0,
        "spd": speed if speed is not None else 0.0,
        "brg": bearing if bearing is not None else 0.0,
        "bat": battery,
    }
    encoded = json.dumps(point)
    for k in {f"geo:history:{entity_id.lower()}", f"geo:history:{clean_id}"}:
        try:
            await r.zadd(k, {encoded: ts})
            await r.zremrangebyscore(k, 0, ts - 30 * 86400)
        except Exception as e:
            log.warning(f"[Geo] Failed to record point to Redis ({k}): {e}")


async def get_points_in_window(entity_id: str, hours: float = 24.0) -> list[dict]:
    r = await get_redis()
    if not r:
        return []
    clean_id = entity_id.split(".")[-1].lower()
    now = time.time()
    start_time = now - (hours * 3600)
    for k in (f"geo:history:{clean_id}", f"geo:history:{entity_id.lower()}"):
        try:
            raw_points = await r.zrangebyscore(k, start_time, "+inf")
            if raw_points:
                pts = []
                for p_str in raw_points:
                    try:
                        pts.append(json.loads(p_str))
                    except Exception:
                        pass
                pts.sort(key=lambda x: x.get("t", 0))
                return pts
        except Exception as e:
            log.warning(f"[Geo] Error reading history from Redis ({k}): {e}")
    return []


class LocationUpdate(BaseModel):
    latitude: float
    longitude: float
    gps_accuracy: int | None = None
    battery: int | None = None
    source_type: str = "gps"
    speed: float | None = None
    bearing: float | None = None
    timestamp: float | None = None


@app.post("/people/{entity_id:path}/see")
async def post_see(
    entity_id: str,
    update: LocationUpdate,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Push a location into HA via the device_tracker.see service and record telemetry breadcrumb."""
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not HA_URL:
        raise HTTPException(status_code=500, detail="HA_URL not resolved from Identity")

    await record_point(
        entity_id=entity_id,
        lat=update.latitude,
        lon=update.longitude,
        accuracy=update.gps_accuracy,
        speed=update.speed,
        bearing=update.bearing,
        battery=update.battery,
        timestamp=update.timestamp,
    )

    dev_id = entity_id.split(".")[-1]
    payload = {
        "type": "device_tracker.see",
        "dev_id": dev_id,
        "gps": [update.latitude, update.longitude],
        "gps_accuracy": update.gps_accuracy or 0,
        "source_type": update.source_type,
    }
    if update.battery is not None:
        payload["battery"] = update.battery
    if update.speed is not None:
        payload["speed"] = update.speed
    if update.bearing is not None:
        payload["course"] = update.bearing

    async with get_client_insecure() as client:
        async with client.post(f"{HA_URL}/api/services/device_tracker/see", headers=_ha_headers(), json=payload) as resp:
            if resp.status >= 300:
                body = await resp.text()
                raise HTTPException(status_code=502, detail=f"HA error {resp.status}: {body}")
            return {"status": "ok", "entity_id": entity_id}


@app.post("/people/{entity_id:path}/record")
async def post_record(
    entity_id: str,
    update: LocationUpdate,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Directly record a telemetry breadcrumb without requiring HA."""
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    await record_point(
        entity_id=entity_id,
        lat=update.latitude,
        lon=update.longitude,
        accuracy=update.gps_accuracy,
        speed=update.speed,
        bearing=update.bearing,
        battery=update.battery,
        timestamp=update.timestamp,
    )
    return {"status": "ok", "entity_id": entity_id}


async def calculate_telemetry(entity_id: str, hours: float = 24.0) -> dict:
    clean_id = entity_id.split(".")[-1].lower()

    # 1. Fetch HA states for entities and zones
    ha_available = bool(HA_URL and HA_TOKEN)
    states = []
    if ha_available:
        try:
            states = await _ha_get_states()
        except Exception as e:
            log.warning(f"[Geo] Failed to fetch HA states for telemetry: {e}")

    # Extract all zones
    zones = []
    for s in _filter_entities(states, "zone"):
        attrs = s.get("attributes", {})
        zlat = attrs.get("latitude")
        zlon = attrs.get("longitude")
        if zlat is not None and zlon is not None:
            zones.append({
                "entity_id": s.get("entity_id"),
                "name": attrs.get("friendly_name") or s.get("entity_id", "").replace("zone.", "").replace("_", " ").title(),
                "latitude": float(zlat),
                "longitude": float(zlon),
                "radius": float(attrs.get("radius", 100)),
            })

    # Find target entity state from HA
    target_state = None
    for s in _filter_entities(states, "person") + _filter_entities(states, "device_tracker"):
        eid = s.get("entity_id", "").lower()
        fname = s.get("attributes", {}).get("friendly_name", "").lower()
        if eid == f"person.{clean_id}" or eid == f"device_tracker.{clean_id}" or clean_id in eid or clean_id in fname:
            target_state = s
            break

    # 2. Fetch history points from Redis
    points = await get_points_in_window(entity_id, hours=hours)

    cur_lat = None
    cur_lon = None
    cur_acc = None
    cur_bat = None
    cur_speed_mps = 0.0
    cur_bearing = 0.0
    latest_t = time.time()

    if points:
        latest = points[-1]
        cur_lat = latest.get("lat")
        cur_lon = latest.get("lon")
        cur_acc = latest.get("acc")
        cur_bat = latest.get("bat")
        cur_speed_mps = latest.get("spd", 0.0)
        cur_bearing = latest.get("brg", 0.0)
        latest_t = latest.get("t", latest_t)
    elif target_state:
        attrs = target_state.get("attributes", {})
        cur_lat = attrs.get("latitude")
        cur_lon = attrs.get("longitude")
        cur_acc = attrs.get("gps_accuracy")
        cur_bat = attrs.get("battery_level") or attrs.get("battery")
        cur_speed_mps = attrs.get("speed") or 0.0

    if cur_lat is None or cur_lon is None:
        return {
            "entity_id": entity_id,
            "friendly_name": target_state.get("attributes", {}).get("friendly_name", clean_id.title()) if target_state else clean_id.title(),
            "status": "unknown",
            "message": f"No recent location data available for {clean_id.title()}.",
        }

    friendly_name = (
        target_state.get("attributes", {}).get("friendly_name")
        if target_state
        else clean_id.title()
    )

    # 3. Derive speed if GPS speed was 0 but points moved recently
    if cur_speed_mps == 0.0 and len(points) >= 2:
        dt = points[-1].get("t", 0) - points[-2].get("t", 0)
        if 2 < dt < 600:
            dist_m = _haversine_distance(points[-2]["lat"], points[-2]["lon"], points[-1]["lat"], points[-1]["lon"])
            derived = dist_m / dt
            if dist_m > 10 and derived < 45.0:
                cur_speed_mps = derived

    cur_speed_mph = round(cur_speed_mps * 2.23694, 1)
    is_moving = cur_speed_mph >= 3.0

    # 4. Find matching & closest zones
    matched_zone = None
    closest_zone = None
    closest_dist_m = float("inf")
    home_dist_m = None

    for z in zones:
        dist_m = _haversine_distance(cur_lat, cur_lon, z["latitude"], z["longitude"])
        if z["entity_id"] == "zone.home":
            home_dist_m = dist_m
        if dist_m <= z["radius"] and (matched_zone is None or dist_m < _haversine_distance(cur_lat, cur_lon, matched_zone["latitude"], matched_zone["longitude"])):
            matched_zone = z
        if dist_m < closest_dist_m:
            closest_dist_m = dist_m
            closest_zone = z

    # 5. Top speed in window
    max_mps = cur_speed_mps
    for p in points:
        spd = p.get("spd", 0.0)
        if spd and spd < 50.0:
            if spd > max_mps:
                max_mps = spd
    top_speed_mph = round(max_mps * 2.23694, 1)

    # 6. Dwell time / stationary duration
    dwell_start_t = latest_t
    if matched_zone:
        zlat, zlon = matched_zone["latitude"], matched_zone["longitude"]
        zr = max(matched_zone["radius"], 120.0)
        for p in reversed(points):
            if _haversine_distance(p["lat"], p["lon"], zlat, zlon) <= zr:
                dwell_start_t = p["t"]
            else:
                break
    else:
        for p in reversed(points):
            if _haversine_distance(p["lat"], p["lon"], cur_lat, cur_lon) <= 100.0 and p.get("spd", 0.0) * 2.23694 < 3.0:
                dwell_start_t = p["t"]
            else:
                break

    dwell_seconds = max(0.0, time.time() - dwell_start_t)
    dwell_formatted = _format_duration(dwell_seconds)

    # 7. Total distance traveled in window
    total_dist_meters = 0.0
    for i in range(1, len(points)):
        p1 = points[i - 1]
        p2 = points[i]
        dt = p2.get("t", 0) - p1.get("t", 0)
        if dt > 0:
            step_m = _haversine_distance(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
            implied_spd = step_m / dt
            if step_m > 15 and 1.2 <= implied_spd <= 55.0:
                total_dist_meters += step_m

    distance_traveled_miles = round(total_dist_meters * 0.000621371, 1)

    # 8. Frequented locations
    zone_dwells: dict[str, dict] = {}
    if points:
        for i in range(len(points)):
            p = points[i]
            dt = 60.0
            if i < len(points) - 1:
                dt = max(10.0, min(1800.0, points[i + 1].get("t", 0) - p.get("t", 0)))
            z_hit = None
            for z in zones:
                if _haversine_distance(p["lat"], p["lon"], z["latitude"], z["longitude"]) <= z["radius"]:
                    z_hit = z["name"]
                    break
            if z_hit:
                if z_hit not in zone_dwells:
                    zone_dwells[z_hit] = {"name": z_hit, "seconds": 0.0, "visits": 0}
                zone_dwells[z_hit]["seconds"] += dt

    frequented_locations = sorted(
        [
            {
                "name": v["name"],
                "dwell_seconds": int(v["seconds"]),
                "dwell_formatted": _format_duration(v["seconds"]),
            }
            for v in zone_dwells.values()
        ],
        key=lambda x: x["dwell_seconds"],
        reverse=True,
    )

    # 9. Vehicle & travel cost (computed only if an actual vehicle has been assigned)
    r = await get_redis()
    vehicle_info = None
    if r:
        try:
            assigned_id = await r.get(f"geo:user_vehicle:{clean_id}")
            if assigned_id and assigned_id != "none":
                raw_veh = await r.hget("geo:vehicles", assigned_id)
                if raw_veh:
                    veh_dict = json.loads(raw_veh)
                    mpg = float(veh_dict.get("mpg", 0.0))
                    cost_per_unit = float(veh_dict.get("cost_per_gallon", 0.0))
                    gallons = (distance_traveled_miles / mpg) if mpg > 0 else 0.0
                    cost = round(gallons * cost_per_unit, 2)
                    vehicle_info = {
                        **veh_dict,
                        "gallons_used": round(gallons, 2),
                        "estimated_cost_usd": cost,
                    }
        except Exception as e:
            log.warning(f"[Geo] Redis vehicle read error: {e}")

    # 10. Natural speech human summary
    if is_moving:
        dir_txt = f" (heading {int(cur_bearing)}°)" if cur_bearing else ""
        if home_dist_m is not None:
            home_miles = round(home_dist_m * 0.000621371, 1)
            dist_txt = f", about {home_miles} miles from Home"
        elif closest_zone:
            c_miles = round(closest_dist_m * 0.000621371, 1)
            dist_txt = f", {c_miles} miles from {closest_zone['name']}"
        else:
            dist_txt = ""
        speech = f"{friendly_name} is currently traveling at {cur_speed_mph} mph{dir_txt}{dist_txt}. Top speed today was {top_speed_mph} mph."
    elif matched_zone:
        zname = matched_zone["name"]
        speech = f"{friendly_name} is at {zname}. Dwell time: {dwell_formatted}."
    else:
        if closest_zone:
            c_miles = round(closest_dist_m * 0.000621371, 1)
            loc_ref = f"{c_miles} miles from {closest_zone['name']}"
        else:
            loc_ref = f"coordinates ({round(cur_lat, 4)}, {round(cur_lon, 4)})"
        speech = f"{friendly_name} is currently still near {loc_ref} (still for {dwell_formatted})."

    if cur_bat is not None:
        speech += f" Phone battery is at {cur_bat}%."

    return {
        "status": "ok",
        "entity_id": entity_id,
        "friendly_name": friendly_name,
        "latitude": cur_lat,
        "longitude": cur_lon,
        "accuracy": cur_acc,
        "battery": cur_bat,
        "is_moving": is_moving,
        "current_speed_mph": cur_speed_mph,
        "top_speed_mph": top_speed_mph,
        "current_zone": matched_zone["name"] if matched_zone else None,
        "closest_zone": closest_zone["name"] if closest_zone else None,
        "closest_zone_distance_miles": round(closest_dist_m * 0.000621371, 1) if closest_zone else None,
        "distance_to_home_miles": round(home_dist_m * 0.000621371, 1) if home_dist_m is not None else None,
        "dwell_time_seconds": int(dwell_seconds),
        "dwell_time_formatted": dwell_formatted,
        "distance_traveled_miles": distance_traveled_miles,
        "frequented_locations": frequented_locations,
        "vehicle": vehicle_info,
        "speech": speech,
    }


@app.get("/people/{entity_id:path}/telemetry")
async def get_telemetry_endpoint(entity_id: str, hours: float = Query(24.0, ge=0.1, le=168.0)):
    """Calculate rich Life360-style telemetry: speed, dwell time, zones, frequented places, vehicle cost."""
    return await calculate_telemetry(entity_id, hours=hours)


@app.get("/telemetry/{entity_id:path}")
async def get_telemetry_alias(entity_id: str, hours: float = Query(24.0, ge=0.1, le=168.0)):
    return await calculate_telemetry(entity_id, hours=hours)


class VehiclePayload(BaseModel):
    id: str
    name: str
    mpg: float
    cost_per_gallon: float
    fuel_type: str = "regular"


class VehicleAssignPayload(BaseModel):
    user_id: str
    vehicle_id: str | None = None


@app.get("/vehicles")
async def get_vehicles():
    """List all user-configured vehicles (no mock data)."""
    r = await get_redis()
    if not r:
        return {"vehicles": []}
    try:
        data = await r.hgetall("geo:vehicles")
        if not data:
            return {"vehicles": []}
        return {"vehicles": [json.loads(v) for v in data.values()]}
    except Exception as e:
        log.warning(f"[Geo] Error listing vehicles: {e}")
        return {"vehicles": []}


@app.post("/vehicles")
async def save_vehicle(
    vehicle: VehiclePayload,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    await r.hset("geo:vehicles", vehicle.id, json.dumps(vehicle.model_dump()))
    return {"status": "ok", "vehicle": vehicle.model_dump()}


@app.delete("/vehicles/{vehicle_id}")
async def delete_vehicle(
    vehicle_id: str,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    await r.hdel("geo:vehicles", vehicle_id)
    return {"status": "ok", "deleted": vehicle_id}


@app.get("/vehicles/assigned/{user_id}")
async def get_assigned_vehicle(user_id: str):
    r = await get_redis()
    clean_user = user_id.split(".")[-1].lower()
    if not r:
        return {"user_id": clean_user, "vehicle_id": None, "vehicle": None}
    vehicle_id = await r.get(f"geo:user_vehicle:{clean_user}")
    vehicle = None
    if vehicle_id and vehicle_id != "none":
        raw_veh = await r.hget("geo:vehicles", vehicle_id)
        if raw_veh:
            try:
                vehicle = json.loads(raw_veh)
            except Exception:
                vehicle = None
    return {"user_id": clean_user, "vehicle_id": vehicle_id, "vehicle": vehicle}


@app.post("/vehicles/assign")
async def assign_vehicle(
    assign: VehicleAssignPayload,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    clean_user = assign.user_id.split(".")[-1].lower()
    if assign.vehicle_id and assign.vehicle_id != "none":
        await r.set(f"geo:user_vehicle:{clean_user}", assign.vehicle_id)
    else:
        await r.delete(f"geo:user_vehicle:{clean_user}")
    return {"status": "ok", "user_id": clean_user, "vehicle_id": assign.vehicle_id}


# ---------------------------------------------------------------------------
# Fuel Price Lookup & Vehicle MPG Lookup
# ---------------------------------------------------------------------------

_US_STATE_MAP = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

_ABBR_TO_NAME = {v: k.title() for k, v in _US_STATE_MAP.items()}
_FUEL_PRICE_CACHE_TTL = 43200   # 12 hours
_VEHICLE_CACHE_TTL = 86400      # 24 hours
_FUELECONOMY_BASE = "https://www.fueleconomy.gov/ws/rest"


async def _nominatim_resolve(location: str) -> dict:
    """Resolve a location string to city + state via Nominatim."""
    location = location.strip()
    if re.match(r"^\d{5}$", location):
        url = f"https://nominatim.openstreetmap.org/search?postalcode={location}&country=us&format=json"
    elif re.match(r"^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$", location):
        lat, lon = [p.strip() for p in location.split(",")]
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
    else:
        q = urllib.parse.quote(location)
        url = f"https://nominatim.openstreetmap.org/search?q={q}&countrycodes=us&format=json"

    client = get_client_insecure()
    async with client.get(url, headers={"User-Agent": "SharedLLM/1.0"},
                          timeout=aiohttp.ClientTimeout(total=6)) as resp:
        data = await resp.json(content_type=None)

    if isinstance(data, list):
        if not data:
            return {}
        data = data[0]

    display_name = data.get("display_name", "")
    parts = [p.strip() for p in display_name.split(",")]

    state_code = None
    city = None
    for p in parts:
        pl = p.lower()
        if pl in _US_STATE_MAP:
            state_code = _US_STATE_MAP[pl]
            break
        if len(p) == 2 and p.upper() in _ABBR_TO_NAME:
            state_code = p.upper()
            break

    for p in parts:
        if (not re.match(r"^\d+$", p) and "county" not in p.lower()
                and p.lower() not in _US_STATE_MAP and p != "United States"):
            city = p
            break

    return {"display_name": display_name, "city": city, "state_code": state_code}


def _clean_price(val: str) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", val)
    return float(cleaned) if cleaned else None


async def _fetch_aaa_prices(state_code: str) -> dict:
    """Scrape AAA state page for state + metro fuel price averages."""
    url = f"https://gasprices.aaa.com/?state={state_code}"
    client = get_client_insecure()
    async with client.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        html = await resp.text()

    # State-level: first table
    state_prices: dict = {}
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL)
    if tables:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tables[0], re.DOTALL)
        for r in rows:
            cols = [re.sub(r"<[^>]+>", "", c).strip()
                    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.DOTALL)]
            if cols and "Current Avg." in cols[0] and len(cols) >= 5:
                state_prices = {
                    "regular": _clean_price(cols[1]),
                    "midgrade": _clean_price(cols[2]),
                    "premium": _clean_price(cols[3]),
                    "diesel": _clean_price(cols[4]),
                }
                break

    # Metro-level
    metros: dict[str, dict] = {}
    # Match <h3>Metro Name</h3> ... <table>...</table>  (skipping "highest recorded")
    h3_pattern = re.compile(
        r"<h3[^>]*>(.*?)</h3>.*?<table[^>]*>(.*?)</table>", re.DOTALL
    )
    for m in h3_pattern.finditer(html):
        metro_name = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if not metro_name or "highest" in metro_name.lower():
            continue
        tbl = m.group(2)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.DOTALL)
        for r in rows:
            cols = [re.sub(r"<[^>]+>", "", c).strip()
                    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.DOTALL)]
            if cols and "Current Avg." in cols[0] and len(cols) >= 5:
                metros[metro_name] = {
                    "regular": _clean_price(cols[1]),
                    "midgrade": _clean_price(cols[2]),
                    "premium": _clean_price(cols[3]),
                    "diesel": _clean_price(cols[4]),
                }
                break

    return {"state_prices": state_prices, "metros": metros}


@app.get("/fuel-prices")
async def get_fuel_prices(location: str = Query(..., min_length=1)):
    """Look up current fuel prices by ZIP code, city/state, or lat,lon."""
    cache_key = f"geo:fuel_prices:{location.strip().lower()}"
    r = await get_redis()

    # Check cache first
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    # 1. Resolve location
    try:
        loc = await _nominatim_resolve(location)
    except Exception as exc:
        log.warning(f"[fuel-prices] Nominatim resolve failed for '{location}': {exc}")
        raise HTTPException(status_code=400, detail=f"Could not resolve location: {location}")

    state_code = loc.get("state_code")
    city = loc.get("city")
    if not state_code:
        raise HTTPException(status_code=400, detail=f"Could not determine US state for: {location}")

    # 2. Fetch AAA prices
    try:
        aaa = await _fetch_aaa_prices(state_code)
    except Exception as exc:
        log.warning(f"[fuel-prices] AAA fetch failed for {state_code}: {exc}")
        raise HTTPException(status_code=502, detail="Unable to fetch fuel prices from upstream source")

    # 3. Match metro
    matched_metro = None
    matched_prices = aaa.get("state_prices", {})
    source_label = f"{_ABBR_TO_NAME.get(state_code, state_code)} state average"

    if city:
        city_lower = city.lower()
        for metro_name, metro_prices in aaa.get("metros", {}).items():
            metro_lower = metro_name.lower()
            if city_lower in metro_lower or metro_lower.split("-")[0].strip() in city_lower:
                matched_metro = metro_name
                matched_prices = metro_prices
                source_label = f"{metro_name} metro daily average"
                break

    result = {
        "location": loc.get("display_name", location),
        "source": source_label,
        "prices": matched_prices,
    }

    # Cache
    if r:
        try:
            await r.set(cache_key, json.dumps(result), ex=_FUEL_PRICE_CACHE_TTL)
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# FuelEconomy.gov vehicle lookup proxies
# ---------------------------------------------------------------------------

async def _fueleconomy_get(path: str, params: dict | None = None, cache_ttl: int = _VEHICLE_CACHE_TTL) -> dict:
    """Proxy a GET to FuelEconomy.gov with Redis caching."""
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    full_url = f"{_FUELECONOMY_BASE}{path}{query}"

    cache_key = f"geo:fueleconomy:{path}{query}"
    r = await get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    client = get_client_insecure()
    async with client.get(
        full_url,
        headers={"Accept": "application/json", "User-Agent": "SharedLLM/1.0"},
        timeout=aiohttp.ClientTimeout(total=8),
    ) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=502, detail="FuelEconomy.gov lookup failed")
        data = await resp.json(content_type=None)

    if r and data:
        try:
            await r.set(cache_key, json.dumps(data), ex=cache_ttl)
        except Exception:
            pass

    return data


@app.get("/vehicle-lookup/years")
async def vehicle_lookup_years():
    """Available model years from FuelEconomy.gov."""
    return await _fueleconomy_get("/vehicle/menu/year", cache_ttl=86400 * 7)


@app.get("/vehicle-lookup/makes")
async def vehicle_lookup_makes(year: int = Query(...)):
    """Makes available for a given model year."""
    return await _fueleconomy_get("/vehicle/menu/make", {"year": year}, cache_ttl=86400 * 7)


# ---------------------------------------------------------------------------
# Heavy-Duty & Diesel Truck Specifications Database (Class 2b/3, GVWR >= 8500 lbs)
# Real-world verified fuel economy averages for vehicles exempt from EPA testing
# ---------------------------------------------------------------------------

_HEAVY_DUTY_VEHICLES: dict[str, dict[str, list[dict]]] = {
    "Ford": {
        "F-250 Super Duty": [
            {
                "years": (2020, 2026),
                "option_id": "hd-ford-f250-67-diesel-gen3",
                "text": "6.7L Power Stroke V8 Turbo Diesel (10-spd Auto) - 15.0 MPG",
                "comb08": 15.0, "city08": 13.0, "highway08": 18.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.7", "trany": "Automatic 10-spd"
            },
            {
                "years": (2011, 2019),
                "option_id": "hd-ford-f250-67-diesel-gen1",
                "text": "6.7L Power Stroke V8 Turbo Diesel (6-spd Auto) - 15.0 MPG",
                "comb08": 15.0, "city08": 13.0, "highway08": 17.5,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.7", "trany": "Automatic 6-spd"
            },
            {
                "years": (2008, 2010),
                "option_id": "hd-ford-f250-64-diesel",
                "text": "6.4L Power Stroke V8 Twin-Turbo Diesel - 13.5 MPG",
                "comb08": 13.5, "city08": 11.0, "highway08": 16.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.4", "trany": "Automatic 5-spd"
            },
            {
                "years": (2003, 2007),
                "option_id": "hd-ford-f250-60-diesel",
                "text": "6.0L Power Stroke V8 Turbo Diesel - 14.5 MPG",
                "comb08": 14.5, "city08": 12.0, "highway08": 17.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.0", "trany": "Automatic 5-spd"
            },
            {
                "years": (1999, 2003),
                "option_id": "hd-ford-f250-73-diesel",
                "text": "7.3L Power Stroke V8 Turbo Diesel - 15.5 MPG",
                "comb08": 15.5, "city08": 13.0, "highway08": 18.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "7.3", "trany": "Automatic 4-spd"
            },
            {
                "years": (2020, 2026),
                "option_id": "hd-ford-f250-73-gas",
                "text": "7.3L Godzilla V8 Gasoline (10-spd Auto) - 13.0 MPG",
                "comb08": 13.0, "city08": 11.0, "highway08": 15.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "7.3", "trany": "Automatic 10-spd"
            },
            {
                "years": (2011, 2022),
                "option_id": "hd-ford-f250-62-gas",
                "text": "6.2L Boss V8 Gasoline (6-spd Auto) - 12.0 MPG",
                "comb08": 12.0, "city08": 10.0, "highway08": 14.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "6.2", "trany": "Automatic 6-spd"
            },
            {
                "years": (1999, 2010),
                "option_id": "hd-ford-f250-68-gas",
                "text": "6.8L Triton V10 Gasoline - 10.5 MPG",
                "comb08": 10.5, "city08": 9.0, "highway08": 12.0,
                "fuelType1": "Regular Gasoline", "cylinders": "10", "displ": "6.8", "trany": "Automatic"
            },
            {
                "years": (1999, 2010),
                "option_id": "hd-ford-f250-54-gas",
                "text": "5.4L Triton V8 Gasoline - 12.0 MPG",
                "comb08": 12.0, "city08": 10.0, "highway08": 14.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "5.4", "trany": "Automatic"
            },
        ],
        "F-350 Super Duty": [
            {
                "years": (2020, 2026),
                "option_id": "hd-ford-f350-67-diesel-gen3",
                "text": "6.7L Power Stroke V8 Turbo Diesel (10-spd Auto) - 14.5 MPG",
                "comb08": 14.5, "city08": 12.5, "highway08": 17.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.7", "trany": "Automatic 10-spd"
            },
            {
                "years": (2011, 2019),
                "option_id": "hd-ford-f350-67-diesel-gen1",
                "text": "6.7L Power Stroke V8 Turbo Diesel (6-spd Auto) - 14.5 MPG",
                "comb08": 14.5, "city08": 12.5, "highway08": 17.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.7", "trany": "Automatic 6-spd"
            },
            {
                "years": (2008, 2010),
                "option_id": "hd-ford-f350-64-diesel",
                "text": "6.4L Power Stroke V8 Turbo Diesel - 13.0 MPG",
                "comb08": 13.0, "city08": 10.5, "highway08": 15.5,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.4", "trany": "Automatic 5-spd"
            },
            {
                "years": (2003, 2007),
                "option_id": "hd-ford-f350-60-diesel",
                "text": "6.0L Power Stroke V8 Turbo Diesel - 14.0 MPG",
                "comb08": 14.0, "city08": 11.5, "highway08": 16.5,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.0", "trany": "Automatic 5-spd"
            },
            {
                "years": (1999, 2003),
                "option_id": "hd-ford-f350-73-diesel",
                "text": "7.3L Power Stroke V8 Turbo Diesel - 15.0 MPG",
                "comb08": 15.0, "city08": 12.5, "highway08": 17.5,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "7.3", "trany": "Automatic 4-spd"
            },
            {
                "years": (2011, 2022),
                "option_id": "hd-ford-f350-62-gas",
                "text": "6.2L Boss V8 Gasoline - 11.5 MPG",
                "comb08": 11.5, "city08": 9.5, "highway08": 13.5,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "6.2", "trany": "Automatic 6-spd"
            },
        ],
        "F-450 Super Duty": [
            {
                "years": (2011, 2026),
                "option_id": "hd-ford-f450-67-diesel",
                "text": "6.7L Power Stroke V8 Turbo Diesel (Commercial/Dually) - 12.5 MPG",
                "comb08": 12.5, "city08": 10.5, "highway08": 15.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.7", "trany": "Automatic"
            },
        ],
        "Excursion": [
            {
                "years": (2000, 2003),
                "option_id": "hd-ford-excursion-73-diesel",
                "text": "7.3L Power Stroke V8 Turbo Diesel - 15.5 MPG",
                "comb08": 15.5, "city08": 13.0, "highway08": 18.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "7.3", "trany": "Automatic 4-spd"
            },
            {
                "years": (2003, 2005),
                "option_id": "hd-ford-excursion-60-diesel",
                "text": "6.0L Power Stroke V8 Turbo Diesel - 14.5 MPG",
                "comb08": 14.5, "city08": 12.0, "highway08": 17.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.0", "trany": "Automatic 5-spd"
            },
            {
                "years": (2000, 2005),
                "option_id": "hd-ford-excursion-68-gas",
                "text": "6.8L Triton V10 Gasoline - 11.0 MPG",
                "comb08": 11.0, "city08": 9.0, "highway08": 13.0,
                "fuelType1": "Regular Gasoline", "cylinders": "10", "displ": "6.8", "trany": "Automatic 4-spd"
            },
        ],
    },
    "Chevrolet": {
        "Silverado 2500HD": [
            {
                "years": (2020, 2026),
                "option_id": "hd-chevy-2500-66-duramax-l5p",
                "text": "6.6L Duramax V8 Turbo Diesel (10-spd Allison) - 16.0 MPG",
                "comb08": 16.0, "city08": 13.5, "highway08": 19.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic 10-spd"
            },
            {
                "years": (2011, 2019),
                "option_id": "hd-chevy-2500-66-duramax-lml",
                "text": "6.6L Duramax V8 Turbo Diesel (6-spd Allison) - 15.5 MPG",
                "comb08": 15.5, "city08": 13.0, "highway08": 18.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic 6-spd"
            },
            {
                "years": (2001, 2010),
                "option_id": "hd-chevy-2500-66-duramax-classic",
                "text": "6.6L Duramax V8 Turbo Diesel (LB7/LLY/LBZ/LMM) - 16.5 MPG",
                "comb08": 16.5, "city08": 14.0, "highway08": 19.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic"
            },
            {
                "years": (2020, 2026),
                "option_id": "hd-chevy-2500-66-gas",
                "text": "6.6L V8 Gasoline - 12.5 MPG",
                "comb08": 12.5, "city08": 10.5, "highway08": 14.5,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "6.6", "trany": "Automatic"
            },
            {
                "years": (1999, 2019),
                "option_id": "hd-chevy-2500-60-gas",
                "text": "6.0L Vortec V8 Gasoline - 12.0 MPG",
                "comb08": 12.0, "city08": 10.0, "highway08": 14.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "6.0", "trany": "Automatic"
            },
        ],
        "Silverado 3500HD": [
            {
                "years": (2011, 2026),
                "option_id": "hd-chevy-3500-66-duramax",
                "text": "6.6L Duramax V8 Turbo Diesel (Allison) - 14.5 MPG",
                "comb08": 14.5, "city08": 12.5, "highway08": 17.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic"
            },
        ],
    },
    "GMC": {
        "Sierra 2500HD": [
            {
                "years": (2020, 2026),
                "option_id": "hd-gmc-2500-66-duramax-l5p",
                "text": "6.6L Duramax V8 Turbo Diesel (10-spd Allison) - 16.0 MPG",
                "comb08": 16.0, "city08": 13.5, "highway08": 19.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic 10-spd"
            },
            {
                "years": (2011, 2019),
                "option_id": "hd-gmc-2500-66-duramax-lml",
                "text": "6.6L Duramax V8 Turbo Diesel (6-spd Allison) - 15.5 MPG",
                "comb08": 15.5, "city08": 13.0, "highway08": 18.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic 6-spd"
            },
            {
                "years": (2001, 2010),
                "option_id": "hd-gmc-2500-66-duramax-classic",
                "text": "6.6L Duramax V8 Turbo Diesel (LB7/LLY/LBZ/LMM) - 16.5 MPG",
                "comb08": 16.5, "city08": 14.0, "highway08": 19.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic"
            },
            {
                "years": (1999, 2019),
                "option_id": "hd-gmc-2500-60-gas",
                "text": "6.0L Vortec V8 Gasoline - 12.0 MPG",
                "comb08": 12.0, "city08": 10.0, "highway08": 14.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "6.0", "trany": "Automatic"
            },
        ],
        "Sierra 3500HD": [
            {
                "years": (2011, 2026),
                "option_id": "hd-gmc-3500-66-duramax",
                "text": "6.6L Duramax V8 Turbo Diesel (Allison) - 14.5 MPG",
                "comb08": 14.5, "city08": 12.5, "highway08": 17.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "6.6", "trany": "Automatic"
            },
        ],
    },
    "RAM": {
        "2500": [
            {
                "years": (2013, 2026),
                "option_id": "hd-ram-2500-67-cummins-gen4",
                "text": "6.7L Cummins I6 Turbo Diesel (6-spd Auto) - 16.5 MPG",
                "comb08": 16.5, "city08": 14.0, "highway08": 19.5,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "6.7", "trany": "Automatic 6-spd"
            },
            {
                "years": (2007, 2012),
                "option_id": "hd-ram-2500-67-cummins-gen3",
                "text": "6.7L Cummins I6 Turbo Diesel (6-spd Auto/Man) - 16.0 MPG",
                "comb08": 16.0, "city08": 13.5, "highway08": 19.0,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "6.7", "trany": "Automatic 6-spd"
            },
            {
                "years": (1998, 2007),
                "option_id": "hd-ram-2500-59-cummins-24v",
                "text": "5.9L Cummins 24V I6 Turbo Diesel - 18.0 MPG",
                "comb08": 18.0, "city08": 15.5, "highway08": 21.0,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "5.9", "trany": "Automatic/Manual"
            },
            {
                "years": (2014, 2026),
                "option_id": "hd-ram-2500-64-hemi",
                "text": "6.4L Heavy Duty HEMI V8 Gasoline - 12.5 MPG",
                "comb08": 12.5, "city08": 10.5, "highway08": 15.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "6.4", "trany": "Automatic"
            },
            {
                "years": (2003, 2018),
                "option_id": "hd-ram-2500-57-hemi",
                "text": "5.7L HEMI V8 Gasoline - 12.0 MPG",
                "comb08": 12.0, "city08": 10.0, "highway08": 14.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "5.7", "trany": "Automatic"
            },
        ],
        "3500": [
            {
                "years": (2013, 2026),
                "option_id": "hd-ram-3500-67-cummins-ho",
                "text": "6.7L High Output Cummins I6 Turbo Diesel (Aisin 6-spd) - 15.0 MPG",
                "comb08": 15.0, "city08": 12.5, "highway08": 17.5,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "6.7", "trany": "Automatic 6-spd"
            },
            {
                "years": (2007, 2012),
                "option_id": "hd-ram-3500-67-cummins",
                "text": "6.7L Cummins I6 Turbo Diesel - 15.0 MPG",
                "comb08": 15.0, "city08": 12.5, "highway08": 17.5,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "6.7", "trany": "Automatic"
            },
            {
                "years": (1998, 2007),
                "option_id": "hd-ram-3500-59-cummins",
                "text": "5.9L Cummins 24V I6 Turbo Diesel - 17.0 MPG",
                "comb08": 17.0, "city08": 14.5, "highway08": 19.5,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "5.9", "trany": "Automatic/Manual"
            },
        ],
    },
    "Dodge": {
        "Ram 2500": [
            {
                "years": (1998, 2010),
                "option_id": "hd-dodge-2500-cummins",
                "text": "5.9L / 6.7L Cummins Turbo Diesel - 17.0 MPG",
                "comb08": 17.0, "city08": 14.5, "highway08": 20.0,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "5.9", "trany": "Automatic/Manual"
            },
            {
                "years": (2003, 2010),
                "option_id": "hd-dodge-2500-57-hemi",
                "text": "5.7L HEMI V8 Gasoline - 12.0 MPG",
                "comb08": 12.0, "city08": 10.0, "highway08": 14.0,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "5.7", "trany": "Automatic"
            },
        ],
        "Ram 3500": [
            {
                "years": (1998, 2010),
                "option_id": "hd-dodge-3500-cummins",
                "text": "5.9L / 6.7L Cummins Turbo Diesel - 15.5 MPG",
                "comb08": 15.5, "city08": 13.0, "highway08": 18.0,
                "fuelType1": "Diesel", "cylinders": "6", "displ": "5.9", "trany": "Automatic/Manual"
            },
        ],
    },
    "Nissan": {
        "Titan XD": [
            {
                "years": (2016, 2019),
                "option_id": "hd-nissan-titan-xd-50-cummins",
                "text": "5.0L Cummins V8 Turbo Diesel (6-spd Aisin) - 15.5 MPG",
                "comb08": 15.5, "city08": 13.5, "highway08": 18.0,
                "fuelType1": "Diesel", "cylinders": "8", "displ": "5.0", "trany": "Automatic 6-spd"
            },
            {
                "years": (2016, 2024),
                "option_id": "hd-nissan-titan-xd-56-gas",
                "text": "5.6L Endurance V8 Gasoline - 14.0 MPG",
                "comb08": 14.0, "city08": 12.0, "highway08": 16.5,
                "fuelType1": "Regular Gasoline", "cylinders": "8", "displ": "5.6", "trany": "Automatic"
            },
        ],
    },
}


def _find_heavy_duty_option(option_id: str) -> dict | None:
    """Find a heavy-duty vehicle option by ID across all makes and models."""
    for make_name, models in _HEAVY_DUTY_VEHICLES.items():
        for model_name, options in models.items():
            for opt in options:
                if opt.get("option_id") == option_id:
                    res = dict(opt)
                    res["make"] = make_name
                    res["model"] = model_name
                    return res
    return None


@app.get("/vehicle-lookup/models")
async def vehicle_lookup_models(year: int = Query(...), make: str = Query(...)):
    """Models for a given year + make, merging EPA models with heavy-duty diesel/gas trucks."""
    data = await _fueleconomy_get("/vehicle/menu/model", {"year": year, "make": make})
    raw_items = data.get("menuItem", [])
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    items = list(raw_items)

    # Check heavy duty vehicles
    hd_make = None
    for k in _HEAVY_DUTY_VEHICLES:
        if k.lower() == make.lower():
            hd_make = k
            break

    existing_models = {i.get("value", "").lower() for i in items}
    if hd_make:
        for model_name, options in _HEAVY_DUTY_VEHICLES[hd_make].items():
            # Check if this model is active in the requested year
            has_year = any(opt["years"][0] <= year <= opt["years"][1] for opt in options)
            if has_year and model_name.lower() not in existing_models:
                items.append({"text": model_name, "value": model_name})
                existing_models.add(model_name.lower())

    items.sort(key=lambda x: x.get("text", "").lower())
    return {"menuItem": items}


@app.get("/vehicle-lookup/options")
async def vehicle_lookup_options(
    year: int = Query(...), make: str = Query(...), model: str = Query(...)
):
    """Trim / engine options for a year + make + model."""
    # Check heavy duty vehicles first
    hd_make = None
    for k in _HEAVY_DUTY_VEHICLES:
        if k.lower() == make.lower():
            hd_make = k
            break

    if hd_make:
        for m_name, options in _HEAVY_DUTY_VEHICLES[hd_make].items():
            if m_name.lower() == model.lower():
                matching = []
                for opt in options:
                    if opt["years"][0] <= year <= opt["years"][1]:
                        matching.append({"text": opt["text"], "value": opt["option_id"]})
                if matching:
                    return {"menuItem": matching}

    # Otherwise proxy to FuelEconomy.gov
    return await _fueleconomy_get("/vehicle/menu/options", {"year": year, "make": make, "model": model})


@app.get("/vehicle-lookup/{vehicle_id}")
async def vehicle_lookup_detail(vehicle_id: str):
    """Full vehicle specs from FuelEconomy.gov or Heavy-Duty database by ID."""
    if vehicle_id.startswith("hd-"):
        opt = _find_heavy_duty_option(vehicle_id)
        if opt:
            return {
                "year": opt.get("years", (2014, 2014))[0],
                "make": opt.get("make"),
                "model": opt.get("model"),
                "comb08": opt.get("comb08"),
                "city08": opt.get("city08"),
                "highway08": opt.get("highway08"),
                "fuelType1": opt.get("fuelType1"),
                "fuelType2": "",
                "cylinders": opt.get("cylinders"),
                "displ": opt.get("displ"),
                "trany": opt.get("trany"),
                "drive": "4WD",
            }
        raise HTTPException(status_code=404, detail="Heavy-duty vehicle option not found")

    data = await _fueleconomy_get(f"/vehicle/{vehicle_id}")
    return {
        "year": data.get("year"),
        "make": data.get("make"),
        "model": data.get("model"),
        "comb08": data.get("comb08"),
        "city08": data.get("city08"),
        "highway08": data.get("highway08"),
        "fuelType1": data.get("fuelType1"),
        "fuelType2": data.get("fuelType2"),
        "cylinders": data.get("cylinders"),
        "displ": data.get("displ"),
        "trany": data.get("trany"),
        "drive": data.get("drive"),
    }


@app.get("/vehicle-lookup/vin/{vin}")
async def vehicle_lookup_vin(vin: str):
    """Decode a 17-digit VIN using NHTSA vPIC with automated MPG matching."""
    clean_vin = vin.strip().upper()
    if len(clean_vin) != 17:
        raise HTTPException(status_code=400, detail="A valid 17-character VIN is required")

    cache_key = f"geo:vin:{clean_vin}"
    r = await get_redis()
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    client = get_client_insecure()
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{clean_vin}?format=json"
    async with client.get(
        url,
        headers={"User-Agent": "SharedLLM/1.0"},
        timeout=aiohttp.ClientTimeout(total=8),
    ) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=502, detail="NHTSA VIN service unavailable")
        data = await resp.json(content_type=None)

    results = data.get("Results", [])
    if not results:
        raise HTTPException(status_code=404, detail="VIN could not be decoded")
    res = results[0]

    make = res.get("Make", "").title()
    model = res.get("Model", "")
    year_str = res.get("ModelYear", "")
    try:
        year = int(year_str)
    except (ValueError, TypeError):
        year = 0

    fuel_raw = res.get("FuelTypePrimary", "").title()
    displ_str = res.get("DisplacementL", "")
    cylinders = res.get("EngineCylinders", "")

    # Normalize fuel type
    if "Diesel" in fuel_raw:
        fuel_type = "Diesel"
    elif "Electric" in fuel_raw:
        fuel_type = "Electric"
    elif "Hybrid" in fuel_raw or "Flexible" in fuel_raw:
        fuel_type = "Hybrid"
    else:
        fuel_type = "Regular Gasoline"

    comb_mpg = None
    city_mpg = None
    hwy_mpg = None

    # Check heavy-duty database first
    for hd_make, models in _HEAVY_DUTY_VEHICLES.items():
        if hd_make.lower() == make.lower():
            for m_name, options in models.items():
                if m_name.lower() in model.lower() or model.lower() in m_name.lower():
                    for opt in options:
                        if opt["years"][0] <= year <= opt["years"][1]:
                            if fuel_type == "Diesel" and "diesel" in opt.get("fuelType1", "").lower():
                                comb_mpg = opt.get("comb08")
                                city_mpg = opt.get("city08")
                                hwy_mpg = opt.get("highway08")
                                break
                            elif fuel_type != "Diesel" and "diesel" not in opt.get("fuelType1", "").lower():
                                comb_mpg = opt.get("comb08")
                                city_mpg = opt.get("city08")
                                hwy_mpg = opt.get("highway08")
                                break
                    if comb_mpg:
                        break

    # If still not found, try to find matching EPA vehicle
    if not comb_mpg and year and make and model:
        try:
            epa_models = await _fueleconomy_get("/vehicle/menu/model", {"year": year, "make": make})
            items = epa_models.get("menuItem", [])
            if isinstance(items, dict):
                items = [items]
            matched_model = None
            for it in items:
                if it.get("value", "").lower() in model.lower() or model.lower() in it.get("value", "").lower():
                    matched_model = it.get("value")
                    break
            if matched_model:
                opts = await _fueleconomy_get("/vehicle/menu/options", {"year": year, "make": make, "model": matched_model})
                opt_items = opts.get("menuItem", [])
                if isinstance(opt_items, dict):
                    opt_items = [opt_items]
                if opt_items:
                    v_id = opt_items[0].get("value")
                    detail = await _fueleconomy_get(f"/vehicle/{v_id}")
                    comb_mpg = detail.get("comb08")
                    city_mpg = detail.get("city08")
                    hwy_mpg = detail.get("highway08")
                    fuel_type = detail.get("fuelType1") or fuel_type
        except Exception:
            pass

    out = {
        "vin": clean_vin,
        "year": year,
        "make": make,
        "model": model,
        "trim": res.get("Trim", "") or res.get("Series", ""),
        "comb08": comb_mpg,
        "city08": city_mpg,
        "highway08": hwy_mpg,
        "fuelType1": fuel_type,
        "displ": displ_str,
        "cylinders": cylinders,
        "drive": res.get("DriveType", ""),
    }

    if r:
        try:
            await r.set(cache_key, json.dumps(out), ex=86400 * 30)
        except Exception:
            pass

    return out
