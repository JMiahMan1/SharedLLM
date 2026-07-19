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
import logging
import os
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from services.config import HA_TOKEN, HA_URL, INTERNAL_SECRET
from services.common.http import get_client_insecure
from services.shared.info_endpoint import info_router

log = logging.getLogger(__name__)

_START_TIME = __import__("time").time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from services.config import resolve_runtime_config
    await resolve_runtime_config()
    log.info("[Geo] runtime config resolved (HA_URL=%s)", bool(HA_URL))
    yield


app = FastAPI(title="SOA Geo Service", lifespan=lifespan)
app.include_router(info_router)


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
            "gps_accuracy": attrs.get("gps_accuracy"),
            "battery": attrs.get("battery_level") or attrs.get("battery"),
            "source_type": attrs.get("source_type"),
            "in_zones": attrs.get("in_zones"),
            "last_updated": state.get("last_updated") or state.get("last_changed"),
        },
    }


async def _ha_get_states() -> list:
    if not HA_URL:
        raise HTTPException(status_code=500, detail="HA_URL not resolved from Identity")
    async with get_client_insecure() as client:
        async with client.get(f"{HA_URL}/api/states", headers=_ha_headers()) as resp:
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
    async with get_client_insecure() as client:
        async with client.get(url, headers=_ha_headers(), params=params) as resp:
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


class LocationUpdate(BaseModel):
    latitude: float
    longitude: float
    gps_accuracy: int | None = None
    battery: int | None = None
    source_type: str = "gps"


@app.post("/people/{entity_id:path}/see")
async def post_see(entity_id: str, update: LocationUpdate, x_internal_secret: str | None = None):
    """Push a location into HA via the device_tracker.see service.

    Used by the SharedLLM Android client (or any trusted source) to report its
    own position when not using the HA companion app.
    """
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not HA_URL:
        raise HTTPException(status_code=500, detail="HA_URL not resolved from Identity")
    payload = {
        "type": "device_tracker.see",
        "dev_id": entity_id.split(".")[-1],
        "gps": [update.latitude, update.longitude],
        "gps_accuracy": update.gps_accuracy or 0,
        "source_type": update.source_type,
    }
    if update.battery is not None:
        payload["battery"] = update.battery
    async with get_client_insecure() as client:
        async with client.post(f"{HA_URL}/api/services/device_tracker/see", headers=_ha_headers(), json=payload) as resp:
            if resp.status >= 300:
                body = await resp.text()
                raise HTTPException(status_code=502, detail=f"HA error {resp.status}: {body}")
            return {"status": "ok", "entity_id": entity_id}


# NOTE: live push (websocket subscribe_entities) is the client's responsibility;
# the Android client subscribes to person/device_tracker states directly via the
# HA websocket. A /stream proxy can be added here later if the Gateway needs it.
