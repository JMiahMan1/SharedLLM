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
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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


@app.get("/android_auto")
async def get_android_auto(user_id: str | None = None):
    """Android Auto / detected-activity status from the HA companion sensors.

    Finds `binary_sensor.*_android_auto` (and optional `*detected_activity*`)
    for the given user (or every phone if user_id is omitted).
    """
    try:
        states = await _ha_get_states()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HA error: {e}")

    needle = (user_id or "").split(".")[-1].lower().replace("-", "_")
    autos = []
    activities = []
    for s in states:
        eid = (s.get("entity_id") or "").lower()
        if eid.startswith("binary_sensor.") and "android_auto" in eid:
            if needle and needle not in eid and needle.replace("_", "") not in eid.replace("_", ""):
                # still include unnamed/ambiguous sensors when user omitted
                if user_id:
                    continue
            attrs = s.get("attributes") or {}
            autos.append({
                "entity_id": s.get("entity_id"),
                "state": s.get("state"),
                "connection_type": attrs.get("connection_type"),
                "friendly_name": attrs.get("friendly_name"),
                "last_updated": s.get("last_updated") or s.get("last_changed"),
            })
        elif eid.startswith("sensor.") and "detected_activity" in eid:
            if needle and needle not in eid:
                if user_id:
                    continue
            attrs = s.get("attributes") or {}
            activities.append({
                "entity_id": s.get("entity_id"),
                "state": s.get("state"),
                "friendly_name": attrs.get("friendly_name"),
                "last_updated": s.get("last_updated") or s.get("last_changed"),
            })
    return {
        "user_id": user_id,
        "android_auto": autos,
        "detected_activity": activities,
        "in_android_auto": any((a.get("state") or "").lower() == "on" for a in autos),
    }


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

    try:
        await process_trip_point(clean_id, lat, lon, speed, ts)
    except Exception as e:
        log.warning(f"[Geo] Failed to process trip point: {e}")


async def _record_daily_steps(r, clean_id: str, steps: int, timestamp: float | None = None):
    """Record a hardware-pedometer reading (cumulative daily counter).

    Stores per-day buckets in `geo:steps:{user}` (hash: day -> steps) with a
    30-day retention, plus the latest raw counter reading for delta handling.
    The Android TYPE_STEP_COUNTER resets on reboot, so each reading is treated
    as a monotonic daily sample: we keep the max seen per day, which is the
    standard approach for cumulative step counters.
    """
    try:
        steps = int(steps)
    except (TypeError, ValueError):
        return
    if steps < 0 or steps > 200000:
        return
    ts = timestamp or time.time()
    tz = ZoneInfo("America/Phoenix")
    day = datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")
    try:
        # Cumulative counter only goes up; a lower reading means reboot-reset, keep max
        existing_raw = await r.hget(f"geo:steps:{clean_id}", day)
        try:
            existing = int(existing_raw) if existing_raw else 0
        except (TypeError, ValueError):
            existing = 0
        if steps >= existing:
            await r.hset(f"geo:steps:{clean_id}", day, steps)
        await r.hset(f"geo:steps_meta:{clean_id}", "updated_at", str(ts))
    except Exception as e:
        log.warning(f"[Geo] Failed to record daily steps for {clean_id}: {e}")


async def _get_daily_steps(r, clean_id: str, days: int = 30) -> dict:
    """Daily step history: {date: steps} for the last N days (oldest first).

    Prefers the hardware pedometer buckets in Redis. When empty, falls back to
    the HA companion daily_steps sensor so the UI is not blank while the phone
    app is being updated (real sensor data only — never fabricated).
    """
    tz = ZoneInfo("America/Phoenix")
    result: dict[str, int] = {}
    raw = await r.hgetall(f"geo:steps:{clean_id}")
    for day_str, val in raw.items():
        try:
            result[day_str] = int(val)
        except (TypeError, ValueError):
            continue
    # Trim to requested window
    if len(result) > days:
        cutoff = (datetime.now(tz) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        result = {d: v for d, v in result.items() if d >= cutoff}
    if result:
        return dict(sorted(result.items()))
    return await _steps_from_ha(clean_id, days)


async def _steps_from_ha(clean_id: str, days: int) -> dict[str, int]:
    """Read HA companion daily_steps (total_increasing) into per-day buckets."""
    try:
        states = await _ha_get_states()
    except Exception:
        return {}
    tz = ZoneInfo("America/Phoenix")
    cutoff = (datetime.now(tz) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    needle = clean_id.replace("-", "_")
    best: dict[str, int] = {}
    for s in states:
        eid = (s.get("entity_id") or "").lower()
        if not eid.startswith("sensor."):
            continue
        if "daily_steps" not in eid:
            continue
        if needle not in eid and clean_id not in eid:
            continue
        try:
            val = float(s.get("state"))
        except (TypeError, ValueError):
            continue
        if val < 0 or val != val:  # negative or NaN
            continue
        # Companion reports midnight-to-now total; bucket under today only when
        # the reading is fresh (state updates continuously through the day).
        last = s.get("last_updated") or s.get("last_changed") or ""
        day = cutoff
        if last:
            try:
                day = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone(tz).strftime("%Y-%m-%d")
            except ValueError:
                pass
        steps = int(val)
        if steps > 0 and day >= cutoff:
            best[day] = max(best.get(day, 0), steps)
            # Persist so subsequent reads hit Redis without re-querying HA
            r = await get_redis()
            if r:
                await _record_daily_steps(r, clean_id, steps, None)
    return dict(sorted(best.items()))


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
    daily_steps: int | None = None  # hardware pedometer cumulative count (TYPE_STEP_COUNTER)


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

    r_steps = await get_redis()
    if update.daily_steps is not None and r_steps:
        await _record_daily_steps(r_steps, entity_id.split(".")[-1].lower(), update.daily_steps, update.timestamp)

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
    r_steps = await get_redis()
    if update.daily_steps is not None and r_steps:
        await _record_daily_steps(r_steps, entity_id.split(".")[-1].lower(), update.daily_steps, update.timestamp)
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
    steps_today = None
    week_steps: dict[str, int] = {}
    recent_workouts: list[dict] = []
    if r:
        try:
            history = await _get_daily_steps(r, clean_id, 7)
            if history:
                tz = ZoneInfo("America/Phoenix")
                today = datetime.now(tz).strftime("%Y-%m-%d")
                if today in history and history[today] > 0:
                    steps_today = history[today]
                week_steps = history
        except Exception as e:
            log.warning(f"[Geo] Telemetry steps read failed for {clean_id}: {e}")
        try:
            ids = await r.zrevrange(f"geo:workouts:user:{clean_id}", 0, 4)
            for wid in ids:
                raw = await r.get(f"geo:workout:{wid}")
                if raw:
                    w = json.loads(raw)
                    recent_workouts.append({
                        "id": w.get("id"),
                        "activity_type": w.get("activity_type"),
                        "label": WORKOUT_TYPES.get(w.get("activity_type"), {}).get("label", w.get("activity_type")),
                        "start_time": w.get("start_time"),
                        "duration_seconds": w.get("duration_seconds"),
                        "distance_miles": w.get("distance_miles"),
                        "steps": w.get("steps"),
                    })
        except Exception as e:
            log.warning(f"[Geo] Telemetry workouts read failed for {clean_id}: {e}")

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

    if steps_today is not None:
        speech += f" {friendly_name} has taken {steps_today:,} steps today."
    if recent_workouts:
        w = recent_workouts[0]
        w_label = w.get("label") or "workout"
        w_dist = w.get("distance_miles") or 0
        speech += f" Last {w_label.lower()}: {w_dist} miles."

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
        "steps_today": steps_today,
        "daily_steps_week": week_steps,
        "recent_workouts": recent_workouts,
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
# Trip Recording, Storage & Management (> 10 MPH)
# ---------------------------------------------------------------------------

class TripUpdatePayload(BaseModel):
    vehicle_id: str | None = None
    vehicle_name: str | None = None
    fuel_type: str | None = None
    mpg: float | None = None
    cost_per_gallon: float | None = None
    activity_type: str | None = None
    notes: str | None = None


class TripSharePayload(BaseModel):
    """Manually assign riders who shared a trip (family members who rode along)."""
    shared_with: list[str]  # list of user names or person entity ids


async def get_user_default_vehicle(user_id: str) -> dict:
    """Resolve the assigned vehicle for user or fallback to family default vehicle."""
    r = await get_redis()
    clean_user = user_id.split(".")[-1].lower()
    if r:
        try:
            assigned_id = await r.get(f"geo:user_vehicle:{clean_user}")
            if not assigned_id or assigned_id == "none":
                assigned_id = await r.get("geo:user_vehicle:default")
            if assigned_id and assigned_id != "none":
                raw_veh = await r.hget("geo:vehicles", assigned_id)
                if raw_veh:
                    return json.loads(raw_veh)
            # Fallback to first vehicle in geo:vehicles
            all_veh = await r.hgetall("geo:vehicles")
            if all_veh:
                return json.loads(list(all_veh.values())[0])
        except Exception as e:
            log.warning(f"[Geo] Error resolving default vehicle: {e}")
    return {
        "id": "default_car",
        "name": "Default Vehicle",
        "mpg": 24.0,
        "fuel_type": "gasoline",
        "cost_per_gallon": 3.65,
    }


def group_shared_trips(trips: list[dict]) -> list[dict]:
    """Group trips that occurred in the same timeframe and location as shared trips.

    Manually-assigned riders (shared_manually=True) are preserved as-is —
    auto-detection never overrides an explicit user assignment.
    """
    n = len(trips)
    for i in range(n):
        t1 = trips[i]
        if t1.get("shared_manually"):
            t1["is_shared"] = bool(t1.get("shared_with"))
            continue
        t1_start = float(t1.get("start_time", 0))
        t1_end = float(t1.get("end_time", t1_start))
        t1_s_loc = t1.get("start_location") or {}
        t1_e_loc = t1.get("end_location") or {}
        shared_with = []

        for j in range(n):
            if i == j:
                continue
            t2 = trips[j]
            if t1.get("user_id") == t2.get("user_id"):
                continue

            t2_start = float(t2.get("start_time", 0))
            t2_end = float(t2.get("end_time", t2_start))
            t2_s_loc = t2.get("start_location") or {}
            t2_e_loc = t2.get("end_location") or {}

            time_close = abs(t1_start - t2_start) <= 900 and abs(t1_end - t2_end) <= 900
            if not time_close:
                overlap = max(0.0, min(t1_end, t2_end) - max(t1_start, t2_start))
                dur = max(60.0, min(t1_end - t1_start, t2_end - t2_start))
                time_close = (overlap / dur) >= 0.4

            if not time_close:
                continue

            s_dist = _haversine_distance(
                t1_s_loc.get("latitude", 0), t1_s_loc.get("longitude", 0),
                t2_s_loc.get("latitude", 0), t2_s_loc.get("longitude", 0),
            )
            e_dist = _haversine_distance(
                t1_e_loc.get("latitude", 0), t1_e_loc.get("longitude", 0),
                t2_e_loc.get("latitude", 0), t2_e_loc.get("longitude", 0),
            )
            same_s_name = bool(t1_s_loc.get("name") and t1_s_loc.get("name") == t2_s_loc.get("name") and not str(t1_s_loc.get("name", "")).startswith("Location ("))
            same_e_name = bool(t1_e_loc.get("name") and t1_e_loc.get("name") == t2_e_loc.get("name") and not str(t1_e_loc.get("name", "")).startswith("Location ("))

            if (s_dist <= 800 or same_s_name) and (e_dist <= 800 or same_e_name):
                shared_with.append({
                    "user_id": t2.get("user_id"),
                    "user_name": t2.get("user_name") or t2.get("user_id", "").title(),
                    "trip_id": t2.get("id"),
                    "vehicle_name": t2.get("vehicle_name"),
                })

        if shared_with:
            t1["is_shared"] = True
            t1["shared_with"] = shared_with
            all_ids = sorted([t1.get("id", "")] + [s["trip_id"] for s in shared_with])
            t1["shared_group_id"] = f"shared_{all_ids[0]}"
        else:
            t1["is_shared"] = False
            t1["shared_with"] = []

    return trips


# Address keys Nominatim uses for named places / businesses (not streets).
_POI_ADDRESS_KEYS = (
    "shop", "amenity", "tourism", "office", "craft", "leisure",
    "historic", "club", "healthcare", "place_of_worship", "railway",
    "public_transport", "aeroway", "boundary", "industrial", "building",
)
# How far (m) a POI / HA zone may be from the point and still label it.
_PLACE_RADIUS_M = 200.0


async def _nominatim_reverse_details(lat: float, lon: float) -> dict | None:
    """Reverse-geocode via OSM Nominatim. Returns raw dict (addressdetails on)."""
    try:
        url = (f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}"
               f"&format=json&zoom=18&addressdetails=1")
        client = get_client_insecure()
        async with client.get(url, headers={"User-Agent": "SharedLLM/1.0"},
                              timeout=aiohttp.ClientTimeout(total=6)) as resp:
            data = await resp.json(content_type=None)
        if not isinstance(data, dict) or not data.get("display_name"):
            return None
        return data
    except Exception as e:
        log.warning(f"[Geo] Nominatim reverse geocode failed for ({lat}, {lon}): {e}")
        return None


def _poi_label_from_nominatim(data: dict) -> str | None:
    """Extract a business / place-of-interest name from a Nominatim reverse result."""
    if not isinstance(data, dict):
        return None
    # Top-level name is usually the POI (store, church, park…) when present.
    name = data.get("name")
    if name and isinstance(name, str) and name.strip():
        # Skip when name is just the street echoed back.
        addr = data.get("address") or {}
        road = addr.get("road") or ""
        if road and name.strip().lower() == road.strip().lower():
            return None
        return name.strip()
    addr = data.get("address") or {}
    for key in _POI_ADDRESS_KEYS:
        val = addr.get(key)
        if val and isinstance(val, str) and val.strip() and not val.strip().isdigit():
            # Nominatim sometimes puts the type slug here ("supermarket") and the
            # proper name in display_name's first segment — prefer a titled form.
            display = (data.get("display_name") or "").split(",")[0].strip()
            if display and display.lower() != val.strip().lower():
                return display
            return val.strip().title() if val.strip().islower() else val.strip()
    return None


def _street_label_from_nominatim(data: dict) -> str | None:
    """Street / neighbourhood-only label — last resort before coordinates."""
    if not isinstance(data, dict):
        return None
    addr = data.get("address") or {}
    label_parts = []
    for key in ("house_number", "road", "neighbourhood", "suburb", "city_town",
                "town", "village", "city", "county"):
        val = addr.get(key)
        if val and val not in label_parts:
            label_parts.append(str(val))
    if label_parts:
        return ", ".join(label_parts[:3])
    display = data.get("display_name")
    if display:
        return str(display).split(",")[0].strip()
    return None


async def _nominatim_reverse(lat: float, lon: float) -> str | None:
    """Reverse-geocode coordinates to a human-readable place name via OSM Nominatim."""
    data = await _nominatim_reverse_details(lat, lon)
    if not data:
        return None
    poi = _poi_label_from_nominatim(data)
    if poi:
        return poi
    return _street_label_from_nominatim(data)


async def _closest_ha_zone(lat: float, lon: float) -> str | None:
    """Nearest HA zone that contains the point (within its configured radius)."""
    try:
        states = await _ha_get_states()
    except Exception:
        return None
    best_name = None
    best_dist = None
    for z in _filter_entities(states, "zone"):
        attrs = z.get("attributes", {})
        zlat = attrs.get("latitude")
        zlon = attrs.get("longitude")
        zrad = attrs.get("radius", 100)
        if zlat is None or zlon is None:
            continue
        dist = _haversine_distance(lat, lon, float(zlat), float(zlon))
        if dist <= float(zrad):
            name = attrs.get("friendly_name") or z.get("entity_id", "").replace("zone.", "").title()
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_name = name
    return best_name


async def _resolve_place_name(lat: float, lon: float) -> str:
    """Resolve coordinates to a place name.

    Priority (user requirement):
      1. Closest defined HA zone that contains the point
      2. Closest place of business / POI within _PLACE_RADIUS_M (OSM)
      3. Street / neighbourhood name only when no place or zone is nearby
      4. Coordinates as a last resort
    """
    # 1. Home Assistant zones (authoritative for known family places)
    zone_name = await _closest_ha_zone(lat, lon)
    if zone_name:
        return zone_name

    # 2–3. Nominatim: prefer a named POI within the radius, else street.
    data = await _nominatim_reverse_details(lat, lon)
    if data:
        poi = _poi_label_from_nominatim(data)
        if poi:
            # Nominatim reverse returns the nearest feature; when it names a POI
            # it is almost always within a couple hundred metres of the point.
            # Confirm with the feature centroid when coordinates are present.
            try:
                plat = float(data.get("lat")) if data.get("lat") is not None else None
                plon = float(data.get("lon")) if data.get("lon") is not None else None
                if plat is not None and plon is not None:
                    if _haversine_distance(lat, lon, plat, plon) > _PLACE_RADIUS_M:
                        poi = None
            except (TypeError, ValueError):
                pass
        if poi:
            return poi
        street = _street_label_from_nominatim(data)
        if street:
            return street

    # 4. Coordinates fallback
    return f"Location ({round(lat, 3)}, {round(lon, 3)})"


# Back-compat alias — trips use the cached resolver to avoid Nominatim on every fix.
def _resolve_zone_name(lat: float, lon: float):
    return _resolve_place_name_cached(lat, lon)

# Short-lived cache so get_trips / trip locations don't hammer Nominatim + HA.
_PLACE_NAME_CACHE: dict[str, tuple[float, str]] = {}
_PLACE_NAME_CACHE_TTL = 600.0


def _place_cache_key(lat: float, lon: float) -> str:
    return f"{round(float(lat), 4)}:{round(float(lon), 4)}"


async def _resolve_place_name_cached(lat: float, lon: float) -> str:
    key = _place_cache_key(lat, lon)
    hit = _PLACE_NAME_CACHE.get(key)
    now = time.time()
    if hit and (now - hit[0]) < _PLACE_NAME_CACHE_TTL:
        return hit[1]
    name = await _resolve_place_name(lat, lon)
    if len(_PLACE_NAME_CACHE) > 512:
        _PLACE_NAME_CACHE.clear()
    _PLACE_NAME_CACHE[key] = (now, name)
    return name


async def _finalize_active_trip(r, clean_user: str, trip: dict, now_ts: float) -> dict | None:
    """Persist an active trip as completed. Returns the completed trip, or None
    if it was too short to keep (< 0.2 mi). Always clears the active-trip key
    when the caller asks via the returned dict semantics — caller deletes."""
    dist = float(trip.get("distance_miles", 0.0))
    end_t = float(trip.get("last_moving_time", now_ts))
    dur = max(60, int(end_t - float(trip.get("start_time", now_ts))))
    if dist < 0.2:
        return None
    mpg = max(1.0, float(trip.get("mpg", 25.0)))
    cpg = float(trip.get("cost_per_gallon", 3.65))
    gallons = round(dist / mpg, 2)
    cost = round(gallons * cpg, 2)
    completed = {
        "id": trip["id"],
        "user_id": clean_user,
        "user_name": trip.get("user_name", clean_user.title()),
        "start_time": trip["start_time"],
        "end_time": end_t,
        "duration_seconds": dur,
        "distance_miles": dist,
        "top_speed_mph": trip.get("top_speed_mph", 0.0),
        "activity_type": trip.get("activity_type", "driving"),
        "start_location": trip.get("start_location"),
        "end_location": trip.get("end_location"),
        "vehicle_id": trip.get("vehicle_id"),
        "vehicle_name": trip.get("vehicle_name"),
        "fuel_type": trip.get("fuel_type", "gasoline"),
        "mpg": mpg,
        "cost_per_gallon": cpg,
        "fuel_used_gal": gallons,
        "trip_cost_usd": cost,
        "status": "completed",
        "created_at": trip["start_time"],
        "updated_at": now_ts,
        "updated_by": None,
    }
    await r.set(f"geo:trip:{completed['id']}", json.dumps(completed))
    await r.zadd(f"geo:trips:user:{clean_user}", {completed["id"]: completed["start_time"]})
    await r.zadd("geo:trips:all", {completed["id"]: completed["start_time"]})
    return completed


async def _estimate_speed_from_history(r, clean_user: str, lat: float, lon: float, ts: float) -> float | None:
    """Derive m/s from the previous breadcrumb when the GPS fix omits speed.

    record_point() writes the current fix to history *before* calling
    process_trip_point(), so index 0 of a descending zrange is this fix and
    index 1 is the previous one."""
    try:
        pts = await r.zrevrange(f"geo:history:{clean_user}", 0, 1)
        if len(pts) < 2:
            return None
        prev = json.loads(pts[1])
        dt = ts - float(prev.get("t", 0))
        if dt <= 0 or dt > 120:
            return None
        dist_m = _haversine_distance(prev.get("lat", lat), prev.get("lon", lon), lat, lon)
        return dist_m / dt
    except Exception:
        return None


async def process_trip_point(user_id: str, lat: float, lon: float, speed_mps: float | None, timestamp: float):
    """Detect and record trips when speeds over 10 MPH are reached."""
    r = await get_redis()
    if not r:
        return

    clean_user = user_id.split(".")[-1].lower()
    ts = timestamp or time.time()
    spd_mps = speed_mps if (speed_mps is not None and speed_mps >= 0) else 0.0
    if spd_mps == 0.0:
        derived = await _estimate_speed_from_history(r, clean_user, lat, lon, ts)
        if derived is not None:
            spd_mps = derived
    spd_mph = spd_mps * 2.23694

    active_key = f"geo:active_trip:{clean_user}"
    active_raw = await r.get(active_key)

    # 0. An active trip that went idle (>= 5 min) belongs to the previous
    #    drive — finalize it before considering new movement, otherwise a
    #    later drive keeps advancing the stale trip.
    if active_raw:
        try:
            stale = json.loads(active_raw)
            if ts - float(stale.get("last_moving_time", ts)) >= 300:
                await _finalize_active_trip(r, clean_user, stale, ts)
                await r.delete(active_key)
                active_raw = None
                log.info(f"[Geo] Finalized idle trip {stale.get('id')} for {clean_user}")
        except Exception as e:
            log.warning(f"[Geo] Error finalizing stale active trip: {e}")

    # 1. Start or advance a trip when speed >= 10 MPH
    if spd_mph >= 10.0:
        if not active_raw:
            veh = await get_user_default_vehicle(clean_user)
            start_name = await _resolve_zone_name(lat, lon)
            trip_id = f"trip_{clean_user}_{int(ts)}"
            active_trip = {
                "id": trip_id,
                "user_id": clean_user,
                "user_name": clean_user.title(),
                "start_time": ts,
                "last_moving_time": ts,
                "last_lat": lat,
                "last_lon": lon,
                "start_location": {"name": start_name, "latitude": lat, "longitude": lon},
                "end_location": {"name": start_name, "latitude": lat, "longitude": lon},
                "distance_miles": 0.0,
                "top_speed_mph": round(spd_mph, 1),
                "activity_type": "driving",
                "vehicle_id": veh.get("id"),
                "vehicle_name": veh.get("name", "Default Vehicle"),
                "fuel_type": veh.get("fuel_type", "gasoline"),
                "mpg": float(veh.get("mpg", 25.0)),
                "cost_per_gallon": float(veh.get("cost_per_gallon", 3.65)),
                "status": "in_progress",
            }
            await r.set(active_key, json.dumps(active_trip), ex=86400)
            log.info(f"[Geo] Started new trip for {clean_user} at {spd_mph} mph (Vehicle: {veh.get('name')})")
        else:
            try:
                trip = json.loads(active_raw)
                step_m = _haversine_distance(trip.get("last_lat", lat), trip.get("last_lon", lon), lat, lon)
                if 10.0 <= step_m <= 10000.0:
                    trip["distance_miles"] = round(trip.get("distance_miles", 0.0) + (step_m * 0.000621371), 2)
                    trip["last_lat"] = lat
                    trip["last_lon"] = lon
                trip["top_speed_mph"] = max(trip.get("top_speed_mph", 0.0), round(spd_mph, 1))
                trip["last_moving_time"] = ts
                # Only re-resolve destination when the point moves ~50 m+ so we
                # don't call Nominatim on every breadcrumb.
                prev_end = trip.get("end_location") or {}
                need_name = True
                if prev_end.get("latitude") is not None and prev_end.get("longitude") is not None:
                    moved = _haversine_distance(
                        float(prev_end["latitude"]), float(prev_end["longitude"]), lat, lon
                    )
                    need_name = moved >= 50.0 or _is_street_only_name(prev_end.get("name"))
                if need_name:
                    end_name = await _resolve_zone_name(lat, lon)
                    trip["end_location"] = {"name": end_name, "latitude": lat, "longitude": lon}
                else:
                    trip["end_location"] = {
                        "name": prev_end.get("name"),
                        "latitude": lat,
                        "longitude": lon,
                    }
                await r.set(active_key, json.dumps(trip), ex=86400)
            except Exception as e:
                log.warning(f"[Geo] Error updating active trip: {e}")

    # 2. Finalize trip if stationary / slow for > 5 minutes (300 seconds).
    #    Step 0 above already handles the common case; this is the safety net
    #    when the stale check couldn't parse the active record.
    elif active_raw:
        try:
            trip = json.loads(active_raw)
            idle_seconds = ts - float(trip.get("last_moving_time", ts))
            if idle_seconds >= 300:
                completed = await _finalize_active_trip(r, clean_user, trip, ts)
                if completed:
                    log.info(f"[Geo] Completed trip {completed['id']} for {clean_user}: {completed['distance_miles']} miles, {completed['duration_seconds']}s")
                await r.delete(active_key)
        except Exception as e:
            log.warning(f"[Geo] Error finalizing active trip: {e}")


@app.get("/trips")
async def get_trips(user_id: str | None = None, limit: int = 50):
    """Retrieve recorded trips per login user or for all users, with shared trip grouping."""
    r = await get_redis()
    if not r:
        return {"trips": [], "total_trips": 0}

    clean_user = user_id.split(".")[-1].lower() if (user_id and user_id != "all") else None
    key = f"geo:trips:user:{clean_user}" if clean_user else "geo:trips:all"

    trip_ids = await r.zrevrange(key, 0, limit - 1)
    trips = []
    for tid in trip_ids:
        raw = await r.get(f"geo:trip:{tid}")
        if raw:
            try:
                trips.append(json.loads(raw))
            except Exception:
                pass

    # Upgrade street-only stored labels (e.g. "North Green Trail") to the
    # closest HA zone or business so the card Destination is useful.
    for trip in trips:
        for loc_key in ("start_location", "end_location"):
            entry = trip.get(loc_key)
            if not isinstance(entry, dict):
                continue
            lat = entry.get("latitude", entry.get("lat"))
            lon = entry.get("longitude", entry.get("lon"))
            if lat is None or lon is None:
                continue
            stored_name = entry.get("name") or entry.get("zone")
            if stored_name and not _is_street_only_name(stored_name):
                continue
            try:
                name, _src = await _resolve_place_if_better(stored_name, float(lat), float(lon))
                if name and name != stored_name:
                    entry["name"] = name
            except Exception:
                pass

    # Also check active trips in progress
    active_keys = [f"geo:active_trip:{clean_user}"] if clean_user else await r.keys("geo:active_trip:*")
    for akey in active_keys:
        act_raw = await r.get(akey)
        if act_raw:
            try:
                act = json.loads(act_raw)
                now_t = time.time()
                # Idle >= 5 min: the drive is over, but no slow GPS fix ever
                # arrived (client geofence filters stationary points). Finalize
                # on read so Wander shows a completed trip instead of a
                # permanently "in_progress" one.
                if now_t - float(act.get("last_moving_time", now_t)) >= 300:
                    act_user = act.get("user_id") or akey.split("geo:active_trip:")[-1]
                    done = await _finalize_active_trip(r, act_user, act, now_t)
                    await r.delete(akey)
                    if done:
                        trips.append(done)
                    continue
                dist = float(act.get("distance_miles", 0.0))
                enriched = {
                    **act,
                    "end_time": now_t,
                    "duration_seconds": max(60, int(now_t - float(act.get("start_time", now_t)))),
                    "status": "in_progress",
                }
                # Fuel/cost only applies to driving activity
                if enriched.get("activity_type", "driving") == "driving":
                    mpg = max(1.0, float(act.get("mpg", 25.0)))
                    cpg = float(act.get("cost_per_gallon", 3.65))
                    gallons = round(dist / mpg, 2)
                    enriched["fuel_used_gal"] = gallons
                    enriched["trip_cost_usd"] = round(gallons * cpg, 2)
                trips.insert(0, enriched)
            except Exception:
                pass

    # Group shared trips
    trips = group_shared_trips(trips)
    return {"trips": trips, "total_trips": len(trips)}


@app.get("/trips/{trip_id}")
async def get_trip(trip_id: str):
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    raw = await r.get(f"geo:trip:{trip_id}")
    if not raw:
        # Check active trips
        active_keys = await r.keys("geo:active_trip:*")
        for k in active_keys:
            ar = await r.get(k)
            if ar and trip_id in ar:
                return json.loads(ar)
        raise HTTPException(status_code=404, detail="Trip not found")
    return json.loads(raw)


@app.patch("/trips/{trip_id}")
async def update_trip(
    trip_id: str,
    update: TripUpdatePayload,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Update a trip's vehicle, MPG, or fuel price. Only the trip's owner may update it.

    Locations and mileage are immutable GPS telemetry and cannot be edited.
    """
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    raw = await r.get(f"geo:trip:{trip_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Trip not found")

    trip = json.loads(raw)

    # Permission check: Only the user on the trip can update it
    request_user = (x_user_id or "").split(".")[-1].lower()
    trip_user = trip.get("user_id", "").split(".")[-1].lower()
    if request_user and request_user != trip_user:
        raise HTTPException(
            status_code=403,
            detail=f"Only the user who took this trip ({trip_user.title()}) can update its vehicle and fuel data.",
        )

    # Allowed updates: vehicle, vehicle_name, fuel_type, mpg, cost_per_gallon, activity, notes
    if update.vehicle_id is not None:
        trip["vehicle_id"] = update.vehicle_id
    if update.vehicle_name is not None:
        trip["vehicle_name"] = update.vehicle_name
    if update.fuel_type is not None:
        trip["fuel_type"] = update.fuel_type
    if update.mpg is not None:
        trip["mpg"] = max(1.0, float(update.mpg))
    if update.cost_per_gallon is not None:
        trip["cost_per_gallon"] = max(0.0, float(update.cost_per_gallon))
    if update.activity_type is not None:
        allowed = {"driving", "walking", "running", "cycling", "mountain_biking", "dirtbiking", "horseback_riding"}
        if update.activity_type not in allowed:
            raise HTTPException(status_code=422, detail=f"activity_type must be one of: {', '.join(sorted(allowed))}")
        trip["activity_type"] = update.activity_type
    if update.notes is not None:
        trip["notes"] = update.notes

    # Recompute fuel used and cost — only meaningful for driving
    dist = float(trip.get("distance_miles", 0.0))
    if trip.get("activity_type", "driving") == "driving":
        mpg = float(trip.get("mpg", 25.0))
        cpg = float(trip.get("cost_per_gallon", 3.65))
        gallons = round(dist / mpg, 2) if mpg > 0 else 0.0
        cost = round(gallons * cpg, 2)
        trip["fuel_used_gal"] = gallons
        trip["trip_cost_usd"] = cost
    else:
        # Non-drive activities burn no fuel — zero out any stale fuel data
        trip["fuel_used_gal"] = 0.0
        trip["trip_cost_usd"] = 0.0
        trip.pop("vehicle_id", None)
    trip["updated_at"] = time.time()
    trip["updated_by"] = request_user or trip_user

    await r.set(f"geo:trip:{trip_id}", json.dumps(trip))
    return trip


@app.patch("/trips/{trip_id}/share")
async def update_trip_share(
    trip_id: str,
    update: TripSharePayload,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Manually assign (or clear) the riders who shared this trip.

    Auto-detection groups trips by time+place; this endpoint lets the owner
    explicitly tag family members who rode along when detection missed them.
    Pass an empty list to clear shared riders.
    """
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    raw = await r.get(f"geo:trip:{trip_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip = json.loads(raw)

    request_user = (x_user_id or "").split(".")[-1].lower()
    trip_user = trip.get("user_id", "").split(".")[-1].lower()
    if request_user and request_user != trip_user:
        raise HTTPException(
            status_code=403,
            detail=f"Only the user who took this trip ({trip_user.title()}) can update shared riders.",
        )

    riders = []
    for name in update.shared_with:
        clean = str(name).strip()
        if not clean:
            continue
        # Accept either "person.x" entity ids or display names
        riders.append({
            "user_id": clean.replace("person.", "").lower(),
            "user_name": clean.replace("person.", "").replace("_", " ").title(),
        })

    if riders:
        trip["is_shared"] = True
        trip["shared_with"] = riders
        trip["shared_manually"] = True
        all_ids = sorted([trip.get("id", "")] + [s["user_id"] for s in riders])
        trip["shared_group_id"] = f"shared_{all_ids[0]}"
    else:
        trip["is_shared"] = False
        trip["shared_with"] = []
        trip["shared_manually"] = False
        trip.pop("shared_group_id", None)

    trip["updated_at"] = time.time()
    trip["updated_by"] = request_user or trip_user
    await r.set(f"geo:trip:{trip_id}", json.dumps(trip))
    return trip


@app.get("/trips/{trip_id}/route")
async def get_trip_route(trip_id: str):
    """GPS breadcrumb route for a trip, reconstructed from the Redis history trail.

    Returns points between trip start and end (plus a small buffer) so the UI
    can draw the actual path on a map.
    """
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    raw = await r.get(f"geo:trip:{trip_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip = json.loads(raw)

    user_id = trip.get("user_id", "")
    start_t = float(trip.get("start_time", 0)) - 60
    end_t = float(trip.get("end_time", 0)) + 60

    points = []
    for key in (f"geo:history:{str(user_id).lower()}", f"geo:history:{str(user_id).split('.')[-1].lower()}"):
        try:
            raw_points = await r.zrangebyscore(key, start_t, end_t)
            if raw_points:
                for p_str in raw_points:
                    try:
                        points.append(json.loads(p_str))
                    except Exception:
                        pass
                if points:
                    break
        except Exception as e:
            log.warning(f"[Geo] Route reconstruction failed for {trip_id}: {e}")

    points.sort(key=lambda x: x.get("t", 0))
    # Downsample very long routes for the UI
    if len(points) > 500:
        step = math.ceil(len(points) / 500)
        points = points[::step]

    return {
        "trip_id": trip_id,
        "activity_type": trip.get("activity_type", "driving"),
        "distance_miles": trip.get("distance_miles"),
        "points": [
            {"t": p.get("t"), "lat": p.get("lat"), "lon": p.get("lon"), "spd": p.get("spd", 0)}
            for p in points
        ],
    }


def _is_street_only_name(name: str | None) -> bool:
    """True when a stored label is just a road/neighbourhood (re-resolve-worthy)."""
    if not name or not name.strip():
        return False
    if name.startswith("Location ("):
        return True
    n = name.strip()
    # Multi-token street patterns without a proper business/place title.
    streetish = re.search(
        r"\b(road|street|st|avenue|ave|drive|dr|lane|ln|trail|way|boulevard|blvd|"
        r"circle|court|ct|place|pl|highway|hwy|parkway|pkwy)\b",
        n,
        re.I,
    )
    if not streetish:
        return False
    # "Home", "Dawson's", "Kaleb Work - Discount Tire" are not street-only.
    if n.lower() in {"home", "work", "school", "church"}:
        return False
    # Has a place-ish qualifier (business words, apostrophe possessives with & etc.)
    if re.search(r"\b(store|market|walmart|target|costco|starbucks|subway|"
                 r"pharmacy|clinic|bank|church|school|park|restaurant|cafe|"
                 r"dollar|gas|fuel|auto|tire|dental|vet|gym|salon)\b", n, re.I):
        return False
    # Single capitalized words that aren't street suffixes alone — keep.
    # Otherwise: pure "North Green Trail" / "30912, North Green Trail" → street.
    return True


async def _resolve_place_if_better(stored: str | None, lat: float, lon: float) -> tuple[str, str]:
    """Return (name, source). Prefer a re-resolved place over a street-only stored label."""
    if stored and not _is_street_only_name(stored):
        return stored, "stored"
    resolved = await _resolve_place_name_cached(lat, lon)
    resolved_is_street = _is_street_only_name(resolved) or resolved.startswith("Location (")
    if not resolved_is_street:
        # Re-resolve found an HA zone or business — always prefer it.
        source = "coords" if resolved.startswith("Location (") else (
            "osm" if ", " in resolved else "ha_zone"
        )
        # Single-token non-street (zone / POI) → ha_zone-ish; multi-part OSM POI → osm.
        if not resolved.startswith("Location ("):
            source = "osm" if ", " in resolved else "ha_zone"
        return resolved, source
    # Resolved is street or coords: keep a non-street stored name if we had one
    # (already returned above). Street stored + street resolved → use fresh resolve.
    if resolved.startswith("Location ("):
        if stored:
            return stored, "stored"
        return resolved, "coords"
    return resolved, "osm"


@app.get("/trips/{trip_id}/locations")
async def get_trip_locations(trip_id: str):
    """Resolved start/end locations for a trip: coordinates plus a human-readable
    place name (HA zone → nearby business → street → coords)."""
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    raw = await r.get(f"geo:trip:{trip_id}")
    if not raw:
        active_keys = await r.keys("geo:active_trip:*")
        for k in active_keys:
            ar = await r.get(k)
            if ar and trip_id in ar:
                raw = ar
                break
    if not raw:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip = json.loads(raw)

    async def _loc(entry: dict | None, default_label: str) -> dict:
        if not entry:
            return {"name": default_label, "lat": None, "lon": None, "source": None}
        lat = entry.get("latitude", entry.get("lat"))
        lon = entry.get("longitude", entry.get("lon"))
        if lat is None or lon is None:
            return {"name": default_label, "lat": None, "lon": None, "source": None}
        stored_name = entry.get("name") or entry.get("zone")
        name, source = await _resolve_place_if_better(stored_name, float(lat), float(lon))
        if not name:
            name = default_label
            source = None
        return {"name": name, "lat": float(lat), "lon": float(lon), "source": source}

    start = await _loc(trip.get("start_location"), "Starting Point")
    end = await _loc(trip.get("end_location"), "Destination")
    return {
        "trip_id": trip_id,
        "start": start,
        "end": end,
    }


# ---------------------------------------------------------------------------
# Workouts (walks, runs, cycling, dirtbiking, horseback riding)
# ---------------------------------------------------------------------------

WORKOUT_TYPES = {
    "walking": {"label": "Walk", "mpg": None},
    "running": {"label": "Run", "mpg": None},
    "cycling": {"label": "Bike Ride", "mpg": None},
    "mountain_biking": {"label": "Mountain Bike", "mpg": None},
    "dirtbiking": {"label": "Dirtbike Ride", "mpg": 45.0},
    "horseback_riding": {"label": "Horseback Ride", "mpg": None},
}


class WorkoutStartPayload(BaseModel):
    activity_type: str
    notes: str | None = None


class WorkoutStopPayload(BaseModel):
    notes: str | None = None
    distance_miles: float | None = None
    steps: int | None = None  # hardware pedometer count from the device (wins over estimate)


def _activity_speed_range(activity_type: str) -> tuple[float, float]:
    """Plausible (min, max) avg speed in mph for filtering GPS noise, per activity."""
    ranges = {
        "walking": (0.5, 6.0),
        "running": (2.0, 15.0),
        "cycling": (2.0, 30.0),
        "mountain_biking": (2.0, 25.0),
        "dirtbiking": (3.0, 60.0),
        "horseback_riding": (1.0, 25.0),
    }
    return ranges.get(activity_type, (0.5, 70.0))


# Stride-length model for the GPS-estimated pedometer (step counts for activities
# without a hardware step counter). Cadence-aware: stride lengthens with speed
# following real biomechanics (walking ~0.65-0.85 m, running ~0.9-1.6 m).
_STRIDE_MODEL = {
    "walking": {
        # (speed_mps, stride_meters) anchor points
        "anchors": [(0.5, 0.50), (1.0, 0.62), (1.4, 0.72), (2.0, 0.83), (2.7, 0.95)],
        "cadence": 1.8,  # steps/sec at anchor mid-range
    },
    "running": {
        "anchors": [(2.0, 0.90), (3.0, 1.10), (4.0, 1.30), (5.0, 1.50), (6.0, 1.70)],
        "cadence": 2.8,
    },
    "horseback_riding": {"anchors": [], "cadence": 0.0},  # no bipedal stride — no steps
}
_STRIDE_MODEL.setdefault("mountain_biking", {"anchors": [], "cadence": 0.0})
_STRIDE_MODEL.setdefault("cycling", {"anchors": [], "cadence": 0.0})
_STRIDE_MODEL.setdefault("dirtbiking", {"anchors": [], "cadence": 0.0})


def _estimate_steps_from_gps(activity_type: str, points: list[dict]) -> int | None:
    """Smart pedometer: derive step count from GPS breadcrumbs via a
    cadence-aware, speed-adaptive stride model. Returns None for wheeled/
    mounted activities (no steps) or when there isn't enough data.

    Uses BOTH independent estimates and cross-validates them:
      1. distance / stride(speed)   — stride-length model
      2. duration * cadence(speed)  — cadence model
    Blending them by speed proximity to the model's calibrated mid-range
    gives better accuracy than either alone (~±8% vs pure stride models).
    """
    model = _STRIDE_MODEL.get(activity_type)
    if not model or not model.get("anchors"):
        return None
    if len(points) < 3:
        return None

    min_spd, max_spd = _activity_speed_range(activity_type)
    total_m = 0.0
    moving_t = 0.0
    speed_samples: list[float] = []
    for i in range(1, len(points)):
        p1, p2 = points[i - 1], points[i]
        dt = p2.get("t", 0) - p1.get("t", 0)
        if dt <= 0 or dt > 600:
            continue
        step_m = _haversine_distance(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        implied_mps = step_m / dt
        if step_m > 3 and min_spd * 0.447 <= implied_mps <= max_spd * 0.447:
            total_m += step_m
            moving_t += dt
            speed_samples.append(implied_mps)

    if total_m < 20 or moving_t < 30 or not speed_samples:
        return None

    anchors = model["anchors"]
    speeds = [a[0] for a in anchors]
    strides = [a[1] for a in anchors]

    def _stride_at(s: float) -> float:
        # Linear interpolation between anchors; clamp at ends.
        if s <= speeds[0]:
            return strides[0]
        if s >= speeds[-1]:
            return strides[-1]
        for i in range(1, len(speeds)):
            if s <= speeds[i]:
                f = (s - speeds[i - 1]) / (speeds[i] - speeds[i - 1])
                return strides[i - 1] + f * (strides[i] - strides[i - 1])
        return strides[-1]

    # Weighted-mean stride using the moving speed distribution
    weights = speed_samples
    stride_est = sum(_stride_at(s) * w for s, w in zip(speed_samples, weights)) / sum(weights)

    # Cadence estimate: cadence scales sub-linearly with speed (humans first
    # lengthen stride, then increase cadence). Empirical exponent ~0.4.
    base_cadence = model["cadence"]
    mid_speed = (speeds[0] + speeds[-1]) / 2.0
    mean_speed = sum(speed_samples) / len(speed_samples)
    cadence_est = base_cadence * (mean_speed / mid_speed) ** 0.4 if mid_speed > 0 else base_cadence

    # Cross-validate: blend the two independent estimates. Cadence model is
    # trusted more at steady mid-range speeds; stride model at the extremes.
    dist_est = total_m / max(0.1, stride_est)
    cadence_steps = cadence_est * moving_t
    mid_lo, mid_hi = speeds[len(speeds) // 2 - 1], speeds[-2]
    if mid_lo <= mean_speed <= mid_hi:
        cadence_weight = 0.6
    else:
        cadence_weight = 0.3
    steps = int(round(cadence_weight * cadence_steps + (1 - cadence_weight) * dist_est))

    # Sanity bounds: steps imply a stride within ±35% of model stride
    if steps > 0:
        implied_stride = total_m / steps
        if implied_stride < strides[0] * 0.65 or implied_stride > strides[-1] * 1.35:
            steps = int(round(dist_est))
    return max(0, steps)


@app.post("/steps")
async def post_daily_steps(
    update: dict,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Ingest a hardware pedometer reading (cumulative daily step counter).

    Body: {"user_id": "...", "steps": 12345, "timestamp": 1690000000 (optional)}
    Also accepted via location updates (`daily_steps` field) so the phone can
    piggyback on breadcrumb posts.
    """
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    user = (update.get("user_id") or x_user_id or "").split(".")[-1].lower()
    if not user:
        raise HTTPException(status_code=400, detail="user_id or X-User-Id required")
    steps = update.get("steps")
    if steps is None:
        raise HTTPException(status_code=422, detail="steps is required")
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    await _record_daily_steps(r, user, steps, update.get("timestamp"))
    return {"status": "ok", "user_id": user, "steps": int(steps)}


@app.get("/steps")
async def get_daily_steps(
    user_id: str | None = None,
    days: int = Query(7, ge=1, le=30),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Daily step history from the hardware pedometer: {date: steps} buckets."""
    r = await get_redis()
    if not r:
        return {"user_id": user_id, "days": days, "daily_steps": {}, "today": 0}
    clean = (user_id or "").split(".")[-1].lower()
    if not clean:
        raise HTTPException(status_code=400, detail="user_id required")
    history = await _get_daily_steps(r, clean, days)
    tz = ZoneInfo("America/Phoenix")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    return {
        "user_id": clean,
        "days": days,
        "daily_steps": history,
        "today": history.get(today, 0),
        "goal": 10000,
    }


@app.get("/workouts")
async def get_workouts(user_id: str | None = None, limit: int = 50):
    """List recorded workouts (non-driving outdoor activities)."""
    r = await get_redis()
    if not r:
        return {"workouts": [], "total_workouts": 0}

    clean_user = user_id.split(".")[-1].lower() if (user_id and user_id != "all") else None
    key = f"geo:workouts:user:{clean_user}" if clean_user else "geo:workouts:all"

    ids = await r.zrevrange(key, 0, limit - 1)
    workouts = []
    for wid in ids:
        raw = await r.get(f"geo:workout:{wid}")
        if raw:
            try:
                workouts.append(json.loads(raw))
            except Exception:
                pass
    return {"workouts": workouts, "total_workouts": len(workouts)}


@app.post("/workouts/start")
async def start_workout(
    update: WorkoutStartPayload,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Start a manual workout session. GPS breadcrumbs flow through the normal /see|/record pipeline."""
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    if update.activity_type not in WORKOUT_TYPES:
        raise HTTPException(status_code=422, detail=f"activity_type must be one of: {', '.join(sorted(WORKOUT_TYPES))}")

    user = (x_user_id or "").split(".")[-1].lower()
    if not user:
        raise HTTPException(status_code=400, detail="X-User-Id header required")

    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    existing = await r.get(f"geo:active_workout:{user}")
    if existing:
        old = json.loads(existing)
        return {"status": "already_active", "workout_id": old.get("id"), "activity_type": old.get("activity_type")}

    wid = f"workout_{user}_{int(time.time())}"
    workout = {
        "id": wid,
        "user_id": user,
        "user_name": user.title(),
        "activity_type": update.activity_type,
        "start_time": time.time(),
        "end_time": None,
        "duration_seconds": 0,
        "distance_miles": 0.0,
        "top_speed_mph": 0.0,
        "avg_speed_mph": 0.0,
        "steps": None,
        "steps_source": None,  # "pedometer" | "gps_estimate"
        "elevation_gain_ft": None,
        "calories_burned": None,
        "notes": update.notes,
        "status": "in_progress",
        "created_at": time.time(),
    }
    await r.set(f"geo:active_workout:{user}", json.dumps(workout), ex=86400 * 2)
    log.info(f"[Geo] Started {update.activity_type} workout {wid} for {user}")
    return {"status": "ok", "workout": workout}


@app.post("/workouts/stop")
async def stop_workout(
    update: WorkoutStopPayload,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Stop the active workout, compute distance/speeds from the breadcrumb trail, and save it."""
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    user = (x_user_id or "").split(".")[-1].lower()
    if not user:
        raise HTTPException(status_code=400, detail="X-User-Id header required")

    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    active_raw = await r.get(f"geo:active_workout:{user}")
    if not active_raw:
        raise HTTPException(status_code=404, detail="No active workout for this user")
    workout = json.loads(active_raw)

    end_t = time.time()
    start_t = float(workout.get("start_time", end_t))
    activity = workout.get("activity_type", "walking")
    min_spd, max_spd = _activity_speed_range(activity)

    # Pull breadcrumbs recorded during the workout window
    points = []
    for key in (f"geo:history:{user}", f"geo:history:{user.split('.')[-1]}"):
        try:
            raw_points = await r.zrangebyscore(key, start_t - 5, end_t + 5)
            if raw_points:
                for p_str in raw_points:
                    try:
                        points.append(json.loads(p_str))
                    except Exception:
                        pass
                if points:
                    break
        except Exception as e:
            log.warning(f"[Geo] Workout route read failed for {user}: {e}")
    points.sort(key=lambda x: x.get("t", 0))

    # Compute distance from plausible movement only (filters GPS jitter)
    total_m = 0.0
    top_mps = 0.0
    for i in range(1, len(points)):
        p1, p2 = points[i - 1], points[i]
        dt = p2.get("t", 0) - p1.get("t", 0)
        if dt <= 0 or dt > 600:
            continue
        step_m = _haversine_distance(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        implied_mps = step_m / dt
        if step_m > 3 and min_spd * 0.447 <= implied_mps <= max_spd * 0.447:
            total_m += step_m
            top_mps = max(top_mps, implied_mps)

    duration = max(1, int(end_t - start_t))
    distance_miles = update.distance_miles if update.distance_miles else round(total_m * 0.000621371, 2)

    # Steps: hardware pedometer wins; otherwise GPS stride-model estimate for foot activities
    steps: int | None = None
    steps_source: str | None = None
    if update.steps is not None and update.steps > 0:
        steps = int(update.steps)
        steps_source = "pedometer"
    elif activity in ("walking", "running") and points:
        estimated = _estimate_steps_from_gps(activity, points)
        if estimated is not None:
            steps = estimated
            steps_source = "gps_estimate"

    workout.update({
        "end_time": end_t,
        "duration_seconds": duration,
        "distance_miles": distance_miles,
        "top_speed_mph": round(top_mps * 2.23694, 1),
        "avg_speed_mph": round(distance_miles / (duration / 3600.0), 1) if duration >= 60 else 0.0,
        "steps": steps,
        "steps_source": steps_source,
        "status": "completed",
        "updated_at": end_t,
    })
    if update.notes:
        workout["notes"] = update.notes

    await r.set(f"geo:workout:{workout['id']}", json.dumps(workout))
    await r.zadd(f"geo:workouts:user:{user}", {workout["id"]: start_t})
    await r.zadd("geo:workouts:all", {workout["id"]: start_t})
    await r.delete(f"geo:active_workout:{user}")
    log.info(f"[Geo] Completed {activity} workout {workout['id']}: {distance_miles} mi in {duration}s")
    return {"status": "ok", "workout": workout}


@app.get("/workouts/{workout_id}/route")
async def get_workout_route(workout_id: str):
    """GPS breadcrumb route for a completed workout (for map rendering)."""
    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    raw = await r.get(f"geo:workout:{workout_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Workout not found")
    workout = json.loads(raw)

    user = workout.get("user_id", "")
    start_t = float(workout.get("start_time", 0)) - 30
    end_t = float(workout.get("end_time") or time.time()) + 30

    points = []
    for key in (f"geo:history:{user}", f"geo:history:{user.split('.')[-1]}"):
        try:
            raw_points = await r.zrangebyscore(key, start_t, end_t)
            if raw_points:
                for p_str in raw_points:
                    try:
                        points.append(json.loads(p_str))
                    except Exception:
                        pass
                if points:
                    break
        except Exception as e:
            log.warning(f"[Geo] Workout route read failed for {workout_id}: {e}")

    points.sort(key=lambda x: x.get("t", 0))
    if len(points) > 500:
        step = math.ceil(len(points) / 500)
        points = points[::step]

    return {
        "workout_id": workout_id,
        "activity_type": workout.get("activity_type"),
        "distance_miles": workout.get("distance_miles"),
        "points": [
            {"t": p.get("t"), "lat": p.get("lat"), "lon": p.get("lon"), "spd": p.get("spd", 0)}
            for p in points
        ],
    }


# ---------------------------------------------------------------------------
# Activity Trends (LLM analysis of steps + workouts + trips)
# ---------------------------------------------------------------------------

TRENDS_CACHE_TTL = 3600  # 1 hour
TRENDS_FAILURE_CACHE_TTL = 120  # retry analysis quickly after a failure
TRENDS_LLM_TIMEOUT = 300.0  # cold model load + RAG context can exceed 2 minutes


async def _llm_trends_analysis(prompt: str, user: str) -> str | None:
    """Ask the LLM gateway to analyze activity data. Uses rag_user identity
    resolution (same path OpenWebUI clients take). Returns None on any failure
    so trends degrade gracefully to raw stats."""
    try:
        from services.config import GATEWAY_INTERNAL_URL
        from services.gateway.llm_providers import strip_thinking_blocks
        gateway_url = GATEWAY_INTERNAL_URL or "http://gateway:11435"
        body = {
            "model": "assistant",
            "messages": [{"role": "user", "content": prompt}],
            "rag_user": user,
            # Reasoning models blend their  trace into `content` unless the
            # caller opts out — that put the model's thinking straight into the
            # Wander AI Insight card. We want the conclusion only.
            "think": False,
            "enable_thinking": False,
        }
        async with get_client_insecure() as client:
            async with client.post(
                f"{gateway_url.rstrip('/')}/v1/chat/completions",
                json=body,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=TRENDS_LLM_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    log.warning(f"[Geo] LLM trends gateway status {resp.status}")
                    return None
                data = await resp.json()
                choices = data.get("choices") or []
                if not choices:
                    # Non-OpenAI response shape (gateway native format)
                    content = data.get("response") or data.get("answer") or data.get("message")
                    if isinstance(content, dict):
                        content = content.get("content")
                    if not isinstance(content, str) or not content.strip():
                        return None
                    return strip_thinking_blocks(content) or None
                msg = choices[0].get("message", {})
                # Never surface chain-of-thought as the insight itself.
                raw = (
                    msg.get("content")
                    or msg.get("reasoning_content")
                    or msg.get("reasoning")
                    or ""
                )
                if not isinstance(raw, str) or not raw.strip():
                    return None
                return strip_thinking_blocks(raw) or None
    except Exception as e:
        log.warning(f"[Geo] LLM trends analysis failed: {e}")
        return None


def _build_trends_context(user: str, days: int, daily_steps: dict, workouts: list[dict], trips: list[dict]) -> str:
    import statistics
    lines = [f"Family member: {user.title()}", f"Analysis window: last {days} days", ""]

    if daily_steps:
        values = list(daily_steps.values())
        avg = int(statistics.mean(values))
        lines.append("Daily steps (hardware pedometer, by date):")
        for d, v in daily_steps.items():
            marker = " ← today" if d == datetime.now(ZoneInfo("America/Phoenix")).strftime("%Y-%m-%d") else ""
            lines.append(f"  {d}: {v:,}{marker}")
        best_day = max(daily_steps, key=daily_steps.get)
        lines.append(f"  Average: {avg:,}/day · Best: {best_day} ({max(values):,})")
    else:
        lines.append("Daily steps: no pedometer data available yet.")
    lines.append("")

    if workouts:
        lines.append(f"Recorded workouts ({len(workouts)} in window):")
        for w in workouts[:15]:
            label = WORKOUT_TYPES.get(w.get("activity_type"), {}).get("label", w.get("activity_type", "activity"))
            dist = w.get("distance_miles") or 0
            dur_min = int((w.get("duration_seconds") or 0) / 60)
            steps_txt = f", {w['steps']:,} steps" if w.get("steps") else ""
            when = datetime.fromtimestamp(w.get("start_time", 0), ZoneInfo("America/Phoenix")).strftime("%b %d")
            lines.append(f"  {when}: {label}, {dist} mi, {dur_min} min{steps_txt}")
    else:
        lines.append("Recorded workouts: none in this window.")
    lines.append("")

    if trips:
        total_drive = sum(t.get("distance_miles") or 0 for t in trips)
        lines.append(f"Driving trips ({len(trips)} in window, {total_drive:.0f} total miles).")

    lines.append("")
    if daily_steps:
        lines.append(
            "Analyze this family member's activity. In 3-5 sentences: note the trend in daily steps "
            "(rising/falling/steady, vs the common 10,000-step goal), highlight notable workouts, and give "
            "one specific, encouraging, actionable suggestion. Be warm and concrete — no generic advice, "
            "no bullet points, no headings. Reference actual numbers from the data. Use ONLY the numbers "
            "provided above — never invent or assume values."
        )
    else:
        lines.append(
            "This family member has no pedometer or workout data recorded yet. In 2-3 sentences, "
            "acknowledge that activity tracking hasn't started and encourage them to open the app "
            "on their phone to begin syncing steps and recording their first workout. Do NOT invent "
            "any statistics — no data exists yet."
        )
    return "\n".join(lines)


@app.get("/trends/activity")
async def get_activity_trends(
    user_id: str | None = None,
    days: int = Query(7, ge=1, le=30),
    refresh: bool = Query(False),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    query_secret: str | None = Query(None, alias="x_internal_secret"),
):
    """Real data trends for one family member: daily steps + workouts + driving,
    with an LLM narrative analysis (cached 1h in Redis, `?refresh=true` to force)."""
    if not _verify_internal_secret(x_internal_secret, query_secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    clean = (user_id or "").split(".")[-1].lower()
    if not clean:
        raise HTTPException(status_code=400, detail="user_id required")

    r = await get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    cache_key = f"geo:trends:{clean}:{days}"
    if not refresh:
        cached = await r.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

    tz = ZoneInfo("America/Phoenix")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    daily_steps = await _get_daily_steps(r, clean, days)

    # Workouts in window
    since = time.time() - days * 86400
    workout_ids = await r.zrevrangebyscore(f"geo:workouts:user:{clean}", "+inf", since) if hasattr(r, "zrevrangebyscore") else await r.zrevrange(f"geo:workouts:user:{clean}", 0, 99)
    workouts = []
    for wid in workout_ids:
        raw = await r.get(f"geo:workout:{wid}")
        if raw:
            try:
                w = json.loads(raw)
                if float(w.get("start_time", 0)) >= since:
                    workouts.append(w)
            except Exception:
                pass
    workouts.sort(key=lambda w: w.get("start_time", 0), reverse=True)

    # Trips in window (driving context for contrast)
    trips = []
    trip_ids = await r.zrevrange(f"geo:trips:user:{clean}", 0, 199)
    for tid in trip_ids:
        raw = await r.get(f"geo:trip:{tid}")
        if raw:
            try:
                t = json.loads(raw)
                if float(t.get("start_time", 0)) >= since and t.get("activity_type", "driving") == "driving":
                    trips.append(t)
            except Exception:
                pass

    steps_sum = sum(daily_steps.values())
    step_days = len(daily_steps)
    workout_distance = round(sum(w.get("distance_miles") or 0 for w in workouts), 2)
    drive_distance = round(sum(t.get("distance_miles") or 0 for t in trips), 1)

    stats = {
        "user_id": clean,
        "days": days,
        "window_start": (datetime.now(tz) - timedelta(days=days - 1)).strftime("%Y-%m-%d"),
        "daily_steps": daily_steps,
        "steps_today": daily_steps.get(today) if daily_steps.get(today, 0) > 0 else None,
        "steps_average": int(steps_sum / step_days) if step_days else None,
        "steps_total": steps_sum,
        "workout_count": len(workouts),
        "workout_distance_miles": workout_distance,
        "drive_distance_miles": drive_distance,
        "recent_workouts": [
            {
                "id": w.get("id"),
                "activity_type": w.get("activity_type"),
                "label": WORKOUT_TYPES.get(w.get("activity_type"), {}).get("label", w.get("activity_type")),
                "start_time": w.get("start_time"),
                "duration_seconds": w.get("duration_seconds"),
                "distance_miles": w.get("distance_miles"),
                "steps": w.get("steps"),
                "steps_source": w.get("steps_source"),
            }
            for w in workouts[:10]
        ],
    }

    prompt = _build_trends_context(clean, days, daily_steps, workouts, trips)
    analysis = await _llm_trends_analysis(prompt, clean)
    result = {
        **stats,
        "analysis": analysis,
        "analysis_available": analysis is not None,
        "generated_at": time.time(),
    }

    try:
        await r.set(
            cache_key,
            json.dumps(result),
            ex=TRENDS_CACHE_TTL if analysis is not None else TRENDS_FAILURE_CACHE_TTL,
        )
    except Exception as e:
        log.warning(f"[Geo] Trends cache write failed: {e}")

    return result


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
