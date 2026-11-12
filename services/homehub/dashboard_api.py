"""What Jarvis's Home, Climate and Energy pages are built from, read off the registry at request time.

Nothing here names a room, a device or an entity: rooms are the registry's
areas, what goes on a room card is decided by domain and device class, and the
power circuits shown beside a thermostat are the power sensors in the
thermostat's own area (an admin can override that per thermostat).

| Route | What |
|---|---|
| ``GET /api/homehub/dashboard/rooms`` | ``{floors, areas: [{area_id, name, floor_id, icon, entities: [...]}], unassigned}`` |
| ``GET /api/homehub/dashboard/climate`` | every thermostat with its area, related power sensors and the area's temperature/humidity sensors |
| ``GET /api/homehub/dashboard/prefs`` | the dashboard settings |
| ``PUT /hub/dashboard/prefs`` | admin: ``{"climate_power": {climate_id: [power sensor ids]}, "hidden": [entity ids]}`` |

An entity summary is ``{entity_id, domain, name, state, attributes, device_class,
unit, icon, area_id, device_id, last_changed}``; attributes that grant access
(a camera's ``access_token``, ``entity_picture`` URLs carrying it) are removed.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub.auth import Auth, Principal, bearer

PREFS_KEY = "dashboard_prefs"
#: Domains a room card can show or control. Everything else (updates, buttons, numbers, automations) is setup, not use.
ROOM_DOMAINS = ("light", "switch", "fan", "cover", "lock", "climate", "media_player", "camera", "vacuum",
                "humidifier", "water_heater", "valve", "siren", "sensor", "binary_sensor", "remote", "scene")
#: Sensor device classes worth a glance on a room card.
ROOM_SENSOR_CLASSES = {"temperature", "humidity", "power", "battery", "illuminance", "pm25", "carbon_dioxide",
                       "moisture", "pressure", "aqi", "volatile_organic_compounds"}
ROOM_BINARY_CLASSES = {"door", "garage_door", "window", "opening", "motion", "occupancy", "presence", "moisture",
                       "smoke", "gas", "carbon_monoxide", "lock", "problem", "safety", "tamper", "vibration"}
_SECRET_ATTRS = ("access_token", "entity_picture")
#: Words that say what a thing is rather than where or which; they never tie a sensor to a thermostat.
_GENERIC_WORDS = {"thermostat", "climate", "temperature", "humidity", "power", "energy", "current", "daily",
                  "monthly", "consumption", "sensor", "the", "and", "of", "hvac", "tstat"}


def _clean_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (attrs or {}).items() if k not in _SECRET_ATTRS}


def load_prefs(store: Any) -> dict[str, Any]:
    saved = store.get_kv(PREFS_KEY, None) if store is not None else None
    prefs = {"climate_power": {}, "hidden": []}
    if isinstance(saved, dict):
        prefs.update({k: v for k, v in saved.items() if k in prefs and v is not None})
    return prefs


def validate_prefs(body: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    prefs = dict(current)
    if "climate_power" in body:
        cp = body["climate_power"] or {}
        if not isinstance(cp, dict) or not all(isinstance(v, list) for v in cp.values()):
            raise ValueError("climate_power maps a climate entity to a list of power sensors")
        prefs["climate_power"] = {str(k): [str(x) for x in v] for k, v in cp.items() if k.startswith("climate.")}
    if "hidden" in body:
        hidden = body["hidden"] or []
        if not isinstance(hidden, list):
            raise ValueError("hidden is a list of entity ids")
        prefs["hidden"] = sorted({str(x) for x in hidden})
    return prefs


def _device_class(hub: Any, entity_id: str) -> str | None:
    st = hub.states.get(entity_id)
    ent = hub.entities.get(entity_id)
    return (st.attributes.get("device_class") if st else None) or (
        (ent.device_class or ent.original_device_class) if ent else None)


def summarize(hub: Any, entity_id: str) -> dict[str, Any] | None:
    st = hub.states.get(entity_id)
    ent = hub.entities.get(entity_id)
    if st is None:
        return None
    attrs = st.attributes or {}
    return {
        "entity_id": entity_id, "domain": st.domain, "name": st.name, "state": st.state,
        "attributes": _clean_attrs(attrs), "device_class": _device_class(hub, entity_id),
        "unit": attrs.get("unit_of_measurement"), "icon": attrs.get("icon"),
        "area_id": hub.entity_area_id(entity_id), "device_id": ent.device_id if ent else None,
        "last_changed": st.last_changed.isoformat(),
    }


def on_room_card(hub: Any, entity_id: str, hidden: set[str]) -> bool:
    ent = hub.entities.get(entity_id)
    if ent is None or ent.disabled_by or ent.hidden_by or ent.entity_category or entity_id in hidden:
        return False
    domain = ent.domain
    if domain not in ROOM_DOMAINS:
        return False
    if domain == "sensor":
        return _device_class(hub, entity_id) in ROOM_SENSOR_CLASSES
    if domain == "binary_sensor":
        return _device_class(hub, entity_id) in ROOM_BINARY_CLASSES
    return True


def rooms(hub: Any, prefs: dict[str, Any]) -> dict[str, Any]:
    hidden = set(prefs.get("hidden") or [])
    by_area: dict[str | None, list[dict[str, Any]]] = {}
    for eid in sorted(hub.entities):
        if not on_room_card(hub, eid, hidden):
            continue
        item = summarize(hub, eid)
        if item is None:
            continue
        by_area.setdefault(item["area_id"] if item["area_id"] in hub.areas else None, []).append(item)
    areas = []
    for a in sorted(hub.areas.values(), key=lambda a: (a.floor_id or "", a.name.lower())):
        areas.append({"area_id": a.area_id, "name": a.name, "floor_id": a.floor_id, "icon": a.icon,
                      "entities": by_area.get(a.area_id, [])})
    floors = [{"floor_id": k, **{kk: vv for kk, vv in (v or {}).items() if kk in ("name", "level", "icon")}}
              for k, v in (hub.floors or {}).items()]
    return {"floors": floors, "areas": areas, "unassigned": by_area.get(None, [])}


def _words(text: str | None) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _GENERIC_WORDS}


def climate_power(hub: Any, climate_id: str, override: dict[str, list[str]] | None = None) -> list[str]:
    """Power sensors that belong with a thermostat: an admin's list, else those in its area, else by name."""
    if override and climate_id in override:
        return [e for e in override[climate_id] if e in hub.entities]
    powers = [e.entity_id for e in hub.entities.values()
              if e.domain == "sensor" and not e.disabled_by and _device_class(hub, e.entity_id) == "power"]
    area = hub.entity_area_id(climate_id)
    same_area = [p for p in powers if area and hub.entity_area_id(p) == area]
    if same_area:
        return sorted(same_area)
    st = hub.states.get(climate_id)
    words = _words(st.name if st else None) | _words(hub.area_name(climate_id))
    if not words:
        return []
    return sorted(p for p in powers if words & _words(hub.states.get(p).name if hub.states.get(p) else p))


def climate(hub: Any, prefs: dict[str, Any]) -> dict[str, Any]:
    override = prefs.get("climate_power") or {}
    out = []
    for ent in sorted(hub.entities.values(), key=lambda e: e.entity_id):
        if ent.domain != "climate" or ent.disabled_by:
            continue
        item = summarize(hub, ent.entity_id)
        if item is None:
            continue
        area = item["area_id"]
        item["area_name"] = hub.areas[area].name if area in hub.areas else None
        item["power"] = [p for p in (summarize(hub, e) for e in climate_power(hub, ent.entity_id, override)) if p]
        item["power_source"] = "settings" if ent.entity_id in override else "area_or_name"
        item["sensors"] = [s for s in (summarize(hub, e) for e in sorted(hub.entities)
                                       if area and hub.entity_area_id(e) == area and e.startswith("sensor.")
                                       and _device_class(hub, e) in ("temperature", "humidity")) if s]
        out.append(item)
    return {"thermostats": out}


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    hub = runtime.hub

    def principal(request: Request) -> Principal:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None:
            raise HTTPException(401, detail="Unauthorized")
        return p

    @router.get("/api/homehub/dashboard/rooms")
    async def get_rooms(request: Request) -> Any:
        principal(request)
        return rooms(hub, load_prefs(runtime.store))

    @router.get("/api/homehub/dashboard/climate")
    async def get_climate(request: Request) -> Any:
        principal(request)
        return climate(hub, load_prefs(runtime.store))

    @router.get("/api/homehub/dashboard/prefs")
    async def get_prefs(request: Request) -> Any:
        principal(request)
        return load_prefs(runtime.store)

    @router.put("/hub/dashboard/prefs")
    async def put_prefs(request: Request) -> Any:
        if not principal(request).is_admin:
            raise HTTPException(401, detail="Unauthorized")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        try:
            prefs = validate_prefs(body, load_prefs(runtime.store))
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        runtime.store.set_kv(PREFS_KEY, prefs)
        return prefs

    return router
