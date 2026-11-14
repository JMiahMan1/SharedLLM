"""The hub's small registries: floors, labels, categories, zones, persons and tags.

Each is kept in the store's key-value table in Home Assistant's own shape (the import fills them from
``.storage``), so an imported house and a hub set up from scratch read the same:

| kv key | Shape |
|---|---|
| ``floors`` | ``{floor_id: {floor_id, name, level, icon, aliases}}`` |
| ``labels`` | ``{label_id: {label_id, name, color, icon, description}}`` |
| ``categories`` | ``{scope: [{category_id, name, icon}]}`` (HA's automation/script/scene folders; read only) |
| ``zones`` | ``[{id, name, latitude, longitude, radius, passive, icon}]`` (the zones a person draws) |
| ``geo_zones`` | the same shape, mirrored from the family's saved places in Jarvis (read only here) |
| ``persons`` | ``[{id, name, user_id, jarvis_user, device_trackers}]`` |
| ``tags`` | ``[{id, name, last_scanned}]`` (NFC/QR tags; names only, the tag trigger reads them) |

Zones and persons are live: a change republishes ``zone.*`` (what zone triggers and conditions read) and
restarts the person manager (``drivers/mobile_app.py``). Each zone and person gets an entity registry row
(platform ``zone``/``person``, unique id = its id), so renaming one never changes its entity id and the
automations that name it keep working.

Saved places: the family's shared places (geo ``/zones``, ``scope: family``) are mirrored as read-only zones
with ``source: geo``. A person's private places stay private: they never become household zones. The mirror
is kept in the store, so automations that name one still load while geo is down.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from typing import Any

import aiohttp

from services.homehub.core import EntityEntry, HubError

log = logging.getLogger("homehub.registries")

GEO_TIMEOUT_S = 10.0
_JARVIS_USER = re.compile(r"^[a-z0-9_.-]{1,64}$")
_TRACKER = re.compile(r"^device_tracker\.[a-z0-9_]+$")
_COLOR = re.compile(r"^(#[0-9a-fA-F]{6}|[a-z_]{1,32})$")


class RegistryError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def slugify(text: str) -> str:
    from services.homehub.drivers.mobile_app import slugify as _slug

    return _slug(text)


def _text(body: dict[str, Any], key: str, what: str, *, required: bool = False, limit: int = 100) -> str | None:
    value = body.get(key)
    if value is None:
        if required:
            raise RegistryError(f"{what} needs a {key}")
        return None
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        raise RegistryError(f"{key} must be text")
    out = str(value).strip()
    if required and not out:
        raise RegistryError(f"{what} needs a {key}")
    if len(out) > limit:
        raise RegistryError(f"{key} is longer than {limit} characters")
    return out or None


def _unknown(body: dict[str, Any], allowed: set[str], what: str) -> None:
    extra = sorted(set(body) - allowed)
    if extra:
        raise RegistryError(f"{what} cannot have {', '.join(extra)}")


def _aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise RegistryError("aliases is a list of names")
    return [a.strip() for a in value if a.strip()]


def _new_id(base: str, taken: Any) -> str:
    key, n = base or "item", 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


# -- floors -------------------------------------------------------------------------------------------

def _floors(runtime: Any) -> dict[str, dict[str, Any]]:
    hub = runtime.hub
    if hub.floors is None:
        hub.floors = {}
    return hub.floors


def _floor_view(fid: str, f: dict[str, Any]) -> dict[str, Any]:
    return {"floor_id": fid, "name": f.get("name") or fid, "level": f.get("level"), "icon": f.get("icon"),
            "aliases": list(f.get("aliases") or [])}


def list_floors(runtime: Any) -> dict[str, Any]:
    views = [_floor_view(k, v or {}) for k, v in _floors(runtime).items()]
    return {"floors": sorted(views, key=lambda f: (f["level"] is None, f["level"] or 0, f["name"].lower()))}


def _floor_fields(body: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    _unknown(body, {"name", "level", "icon", "aliases"}, "A floor")
    out: dict[str, Any] = {}
    if creating or "name" in body:
        out["name"] = _text(body, "name", "A floor", required=True)
    if "level" in body:
        level = body["level"]
        if level is not None and (isinstance(level, bool) or not isinstance(level, int)):
            raise RegistryError("level must be a whole number (0 is the ground floor)")
        out["level"] = level
    if "icon" in body:
        out["icon"] = _text(body, "icon", "A floor")
    if "aliases" in body:
        out["aliases"] = _aliases(body["aliases"])
    return out


def _floor_name_taken(floors: dict[str, dict[str, Any]], name: str, except_id: str | None = None) -> bool:
    return any(str(f.get("name") or "").lower() == name.lower() and k != except_id for k, f in floors.items())


def _save_floors(runtime: Any) -> None:
    runtime.store.set_kv("floors", _floors(runtime))
    runtime.hub.bus.fire("floor_registry_updated", {})


def create_floor(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    floors = _floors(runtime)
    fields = _floor_fields(body, creating=True)
    if _floor_name_taken(floors, fields["name"]):
        raise RegistryError(f"There is already a floor named {fields['name']!r}", 409)
    fid = _new_id(slugify(fields["name"]), floors)
    floors[fid] = {"floor_id": fid, "level": None, "icon": None, "aliases": [], **fields}
    _save_floors(runtime)
    return _floor_view(fid, floors[fid])


def update_floor(runtime: Any, floor_id: str, body: dict[str, Any]) -> dict[str, Any]:
    floors = _floors(runtime)
    if floor_id not in floors:
        raise RegistryError("No such floor", 404)
    fields = _floor_fields(body, creating=False)
    if "name" in fields and _floor_name_taken(floors, fields["name"], floor_id):
        raise RegistryError(f"There is already a floor named {fields['name']!r}", 409)
    floors[floor_id] = {**(floors[floor_id] or {}), "floor_id": floor_id, **fields}
    _save_floors(runtime)
    return _floor_view(floor_id, floors[floor_id])


def delete_floor(runtime: Any, floor_id: str) -> dict[str, Any]:
    """Remove a floor; its areas stay, on no floor."""
    hub, floors = runtime.hub, _floors(runtime)
    if floor_id not in floors:
        raise RegistryError("No such floor", 404)
    moved = []
    for area in hub.areas.values():
        if area.floor_id == floor_id:
            area.floor_id = None
            runtime.store.save_area(area)
            moved.append(area.area_id)
    floors.pop(floor_id)
    _save_floors(runtime)
    if moved:
        hub.bus.fire("area_registry_updated", {"action": "update"})
    return {"status": "removed", "floor_id": floor_id, "areas": moved}


def check_floor(runtime: Any, floor_id: str | None) -> None:
    """An area's floor must be one the hub has."""
    if floor_id is not None and floor_id not in _floors(runtime):
        raise RegistryError(f"There is no floor {floor_id!r}")


# -- labels -------------------------------------------------------------------------------------------

def _labels(runtime: Any) -> dict[str, dict[str, Any]]:
    labels = runtime.store.get_kv("labels", {}) or {}
    return labels if isinstance(labels, dict) else {}


def _label_view(lid: str, label: dict[str, Any]) -> dict[str, Any]:
    return {"label_id": lid, "name": label.get("name") or lid, "color": label.get("color"),
            "icon": label.get("icon"), "description": label.get("description")}


def list_labels(runtime: Any) -> dict[str, Any]:
    views = [_label_view(k, v or {}) for k, v in _labels(runtime).items()]
    return {"labels": sorted(views, key=lambda x: x["name"].lower())}


def _label_fields(body: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    _unknown(body, {"name", "color", "icon", "description"}, "A label")
    out: dict[str, Any] = {}
    if creating or "name" in body:
        out["name"] = _text(body, "name", "A label", required=True, limit=60)
    if "color" in body:
        color = _text(body, "color", "A label", limit=32)
        if color is not None and not _COLOR.match(color):
            raise RegistryError("color is a colour name (like indigo) or #rrggbb")
        out["color"] = color
    for key, limit in (("icon", 100), ("description", 300)):
        if key in body:
            out[key] = _text(body, key, "A label", limit=limit)
    return out


def create_label(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    labels = _labels(runtime)
    fields = _label_fields(body, creating=True)
    if any(str(v.get("name") or "").lower() == fields["name"].lower() for v in labels.values()):
        raise RegistryError(f"There is already a label named {fields['name']!r}", 409)
    lid = _new_id(slugify(fields["name"]), labels)
    labels[lid] = {"label_id": lid, "color": None, "icon": None, "description": None, **fields}
    runtime.store.set_kv("labels", labels)
    runtime.hub.bus.fire("label_registry_updated", {"action": "create", "label_id": lid})
    return _label_view(lid, labels[lid])


def update_label(runtime: Any, label_id: str, body: dict[str, Any]) -> dict[str, Any]:
    labels = _labels(runtime)
    if label_id not in labels:
        raise RegistryError("No such label", 404)
    fields = _label_fields(body, creating=False)
    if "name" in fields and any(str(v.get("name") or "").lower() == fields["name"].lower() and k != label_id
                                for k, v in labels.items()):
        raise RegistryError(f"There is already a label named {fields['name']!r}", 409)
    labels[label_id] = {**labels[label_id], "label_id": label_id, **fields}
    runtime.store.set_kv("labels", labels)
    runtime.hub.bus.fire("label_registry_updated", {"action": "update", "label_id": label_id})
    return _label_view(label_id, labels[label_id])


def delete_label(runtime: Any, label_id: str) -> dict[str, Any]:
    """Remove a label and take it off every area, device and entity that wore it."""
    hub, store = runtime.hub, runtime.store
    labels = _labels(runtime)
    if label_id not in labels:
        raise RegistryError("No such label", 404)
    counts = {"areas": 0, "devices": 0, "entities": 0}
    for area in hub.areas.values():
        if label_id in area.labels:
            area.labels = [x for x in area.labels if x != label_id]
            store.save_area(area)
            counts["areas"] += 1
    for dev in hub.devices.values():
        if label_id in dev.labels:
            dev.labels = [x for x in dev.labels if x != label_id]
            store.save_device(dev)
            counts["devices"] += 1
    for ent in hub.entities.values():
        if label_id in ent.labels:
            ent.labels = [x for x in ent.labels if x != label_id]
            store.save_entity(ent)
            counts["entities"] += 1
    labels.pop(label_id)
    store.set_kv("labels", labels)
    hub.bus.fire("label_registry_updated", {"action": "remove", "label_id": label_id})
    return {"status": "removed", "label_id": label_id, **counts}


def clean_label_ids(runtime: Any, value: Any) -> list[str]:
    """The ``labels`` of a change: label ids the hub has, in order, once each."""
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise RegistryError("labels is a list of label ids")
    known = _labels(runtime)
    missing = sorted({x for x in value if x not in known})
    if missing:
        raise RegistryError(f"There is no label {', '.join(missing)}")
    return list(dict.fromkeys(value))


# -- categories and tags (read only) ------------------------------------------------------------------

def list_categories(runtime: Any) -> dict[str, Any]:
    raw = runtime.store.get_kv("categories", {}) or {}
    return {"categories": {scope: [{"category_id": c.get("category_id"), "name": c.get("name"),
                                    "icon": c.get("icon")} for c in items or [] if isinstance(c, dict)]
                           for scope, items in raw.items()} if isinstance(raw, dict) else {}}


def list_tags(runtime: Any) -> dict[str, Any]:
    raw = runtime.store.get_kv("tags", []) or []
    tags = [{"id": t.get("id"), "name": t.get("name"), "last_scanned": t.get("last_scanned")}
            for t in raw if isinstance(t, dict) and t.get("id")]
    return {"tags": sorted(tags, key=lambda t: str(t["name"] or t["id"]).lower())}


# -- registry rows for zones and persons ----------------------------------------------------------------

def _registry_entity(runtime: Any, platform: str, unique_id: str, name: str) -> str:
    """The entity id of a zone or person, registering one (``<platform>.<slug>``) the first time."""
    hub = runtime.hub
    for ent in hub.entities.values():
        if ent.platform == platform and ent.unique_id == unique_id:
            return ent.entity_id
    base = f"{platform}.{slugify(name)}"
    eid = _new_id(base, set(hub.entities) | set(hub.states.entity_ids()))
    ent = EntityEntry(entity_id=eid, platform=platform, unique_id=unique_id, original_name=name)
    hub.entities[eid] = ent
    runtime.store.save_entity(ent)
    hub.bus.fire("entity_registry_updated", {"action": "create", "entity_id": eid})
    return eid


def _drop_registry_entity(runtime: Any, platform: str, unique_id: str) -> str | None:
    hub = runtime.hub
    ent = next((e for e in hub.entities.values() if e.platform == platform and e.unique_id == unique_id), None)
    if ent is None:
        return None
    hub.entities.pop(ent.entity_id, None)
    runtime.store._exec("DELETE FROM entities WHERE entity_id=?", (ent.entity_id,))
    hub.states.remove(ent.entity_id)
    hub.bus.fire("entity_registry_updated", {"action": "remove", "entity_id": ent.entity_id})
    return ent.entity_id


# -- zones ----------------------------------------------------------------------------------------------

def _stored_zones(runtime: Any, key: str = "zones") -> list[dict[str, Any]]:
    zones = runtime.store.get_kv(key, []) or []
    return [z for z in zones if isinstance(z, dict)] if isinstance(zones, list) else []


def _zone_entity_ids(runtime: Any) -> dict[str, str]:
    return {e.unique_id: e.entity_id for e in runtime.hub.entities.values() if e.platform == "zone"}


def apply_zones(runtime: Any) -> None:
    """Publish ``zone.home``, the drawn zones and the saved-place zones; drop the states of removed ones."""
    from services.homehub.drivers import mobile_app

    hub = runtime.hub
    before = {z["entity_id"] for z in getattr(hub, "zone_table", None) or []}
    home = {"name": (hub.location or {}).get("location_name", "Home"), **(hub.location or {})}
    zones = _stored_zones(runtime) + [{**z, "editable": False} for z in _stored_zones(runtime, "geo_zones")]
    mobile_app.setup_zones(hub, zones, home)
    after = {z["entity_id"] for z in hub.zone_table}
    for gone in before - after:
        hub.states.remove(gone)


def _zone_view(z: dict[str, Any], entity_ids: dict[str, str]) -> dict[str, Any]:
    return {"id": z.get("id"), "entity_id": entity_ids.get(str(z.get("id"))), "name": z.get("name"),
            "latitude": z.get("latitude"), "longitude": z.get("longitude"), "radius": z.get("radius"),
            "passive": bool(z.get("passive")), "icon": z.get("icon"), "source": z.get("source") or "hub",
            "editable": not z.get("source")}


def list_zones(runtime: Any) -> dict[str, Any]:
    ids = _zone_entity_ids(runtime)
    loc = runtime.hub.location or {}
    home = {"id": "home", "entity_id": "zone.home", "name": loc.get("location_name") or "Home",
            "latitude": loc.get("latitude"), "longitude": loc.get("longitude"), "radius": loc.get("radius") or 100,
            "passive": False, "icon": "mdi:home", "source": "home", "editable": False}
    drawn = [_zone_view(z, ids) for z in _stored_zones(runtime)]
    places = [_zone_view(z, ids) for z in _stored_zones(runtime, "geo_zones")]
    return {"zones": [home, *sorted(drawn + places, key=lambda z: str(z["name"] or "").lower())]}


def _number(body: dict[str, Any], key: str, low: float, high: float) -> float:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise RegistryError(f"{key} must be a number")
    if not low <= float(value) <= high:
        raise RegistryError(f"{key} must be between {low:g} and {high:g}")
    return float(value)


def _zone_fields(body: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    _unknown(body, {"name", "latitude", "longitude", "radius", "passive", "icon"}, "A zone")
    out: dict[str, Any] = {}
    if creating or "name" in body:
        out["name"] = _text(body, "name", "A zone", required=True, limit=80)
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180), ("radius", 1, 100000)):
        if key in body or (creating and key != "radius"):
            out[key] = _number(body, key, low, high)
    if creating:
        out.setdefault("radius", 100.0)
    if "passive" in body:
        if not isinstance(body["passive"], bool):
            raise RegistryError("passive must be true or false")
        out["passive"] = body["passive"]
    if "icon" in body:
        out["icon"] = _text(body, "icon", "A zone")
    return out


def _zone_name_taken(runtime: Any, name: str, except_id: str | None = None) -> bool:
    names = [z for z in _stored_zones(runtime) + _stored_zones(runtime, "geo_zones") if z.get("id") != except_id]
    home = str((runtime.hub.location or {}).get("location_name") or "Home")
    return name.lower() == home.lower() or any(str(z.get("name") or "").lower() == name.lower() for z in names)


def create_zone(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    fields = _zone_fields(body, creating=True)
    if _zone_name_taken(runtime, fields["name"]):
        raise RegistryError(f"There is already a zone named {fields['name']!r}", 409)
    zone = {"id": uuid.uuid4().hex, "passive": False, "icon": "mdi:map-marker", **fields}
    zones = [*_stored_zones(runtime), zone]
    runtime.store.set_kv("zones", zones)
    _registry_entity(runtime, "zone", zone["id"], zone["name"])
    apply_zones(runtime)
    return _zone_view(zone, _zone_entity_ids(runtime))


def _editable_zone(runtime: Any, zone_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    zones = _stored_zones(runtime)
    zone = next((z for z in zones if str(z.get("id")) == zone_id), None)
    if zone is None:
        if zone_id == "home" or any(str(z.get("id")) == zone_id for z in _stored_zones(runtime, "geo_zones")):
            raise RegistryError("That zone is not edited here (home is the hub's location; a saved place is "
                                "changed in Jarvis's places)", 409)
        raise RegistryError("No such zone", 404)
    return zones, zone


def update_zone(runtime: Any, zone_id: str, body: dict[str, Any]) -> dict[str, Any]:
    zones, zone = _editable_zone(runtime, zone_id)
    fields = _zone_fields(body, creating=False)
    if "name" in fields and _zone_name_taken(runtime, fields["name"], zone_id):
        raise RegistryError(f"There is already a zone named {fields['name']!r}", 409)
    zone.update(fields)
    runtime.store.set_kv("zones", zones)
    _registry_entity(runtime, "zone", zone_id, zone["name"])
    apply_zones(runtime)
    return _zone_view(zone, _zone_entity_ids(runtime))


def delete_zone(runtime: Any, zone_id: str) -> dict[str, Any]:
    zones, _zone = _editable_zone(runtime, zone_id)
    runtime.store.set_kv("zones", [z for z in zones if str(z.get("id")) != zone_id])
    entity_id = _drop_registry_entity(runtime, "zone", zone_id)
    apply_zones(runtime)
    return {"status": "removed", "id": zone_id, "entity_id": entity_id}


def places_to_zones(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The family's shared saved places from geo's ``/zones``, as hub zones (``id`` = ``geo_<place id>``)."""
    out = []
    for f in (payload or {}).get("features") or []:
        props = f.get("properties") or {}
        if not props.get("custom") or props.get("scope") != "family":
            continue  # a drawn zone (possibly our own mirror) or someone's private place
        place_id = str(props.get("entity_id") or "").removeprefix("place.")
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if not place_id or len(coords) < 2:
            continue
        out.append({"id": f"geo_{place_id}", "name": str(props.get("friendly_name") or "Saved place"),
                    "latitude": float(coords[1]), "longitude": float(coords[0]),
                    "radius": float(props.get("radius") or 150.0), "passive": False,
                    "icon": "mdi:map-marker-star", "source": "geo"})
    return out


async def fetch_places(base: str) -> dict[str, Any]:
    from services.common.http import get_client

    try:
        async with get_client().get(f"{base.rstrip('/')}/zones",
                                    timeout=aiohttp.ClientTimeout(total=GEO_TIMEOUT_S)) as resp:
            if resp.status != 200:
                raise RegistryError(f"geo /zones answered HTTP {resp.status}", 502)
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise RegistryError(f"geo /zones: {type(exc).__name__}: {exc}", 502) from exc


def set_place_zones(runtime: Any, zones: list[dict[str, Any]]) -> dict[str, Any]:
    """Make the saved-place zones exactly ``zones``: register new ones, drop removed ones, republish."""
    old = {str(z.get("id")) for z in _stored_zones(runtime, "geo_zones")}
    new = {str(z["id"]) for z in zones}
    runtime.store.set_kv("geo_zones", zones)
    for z in zones:
        _registry_entity(runtime, "zone", str(z["id"]), z["name"])
    for gone in old - new:
        _drop_registry_entity(runtime, "zone", gone)
    apply_zones(runtime)
    return {"places": len(zones), "added": len(new - old), "removed": len(old - new)}


def geo_url() -> str:
    """Where geo answers (``GEO_SVC_URL``, read once when the stack's config loads)."""
    from services.homehub.drivers.mobile_app import _geo_settings

    return _geo_settings()[0]


async def sync_places(runtime: Any, base: str) -> dict[str, Any]:
    if not base:
        raise RegistryError("GEO_SVC_URL is not set, so saved places cannot be followed", 503)
    return set_place_zones(runtime, places_to_zones(await fetch_places(base)))


async def follow_places(runtime: Any, base: str, every_s: float) -> None:
    """Keep the saved-place zones in step with geo; a failed round keeps the last mirror and says why."""
    last_error: str | None = None
    while True:
        try:
            result = await sync_places(runtime, base)
            if last_error is not None or result["added"] or result["removed"]:
                log.info("saved places: %s", result)
            last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if str(exc) != last_error:
                log.warning("saved places could not be followed (keeping the last %d): %s",
                            len(_stored_zones(runtime, "geo_zones")), exc)
            last_error = str(exc)
        await asyncio.sleep(every_s)


# -- persons --------------------------------------------------------------------------------------------

def _stored_persons(runtime: Any) -> list[dict[str, Any]]:
    persons = runtime.store.get_kv("persons", []) or []
    return [p for p in persons if isinstance(p, dict)] if isinstance(persons, list) else []


def _person_view(runtime: Any, p: dict[str, Any], entity_ids: dict[str, str]) -> dict[str, Any]:
    eid = entity_ids.get(str(p.get("id")))
    st = runtime.hub.states.get(eid) if eid else None
    return {"id": p.get("id"), "entity_id": eid, "name": p.get("name"), "user_id": p.get("user_id"),
            "jarvis_user": p.get("jarvis_user"), "device_trackers": list(p.get("device_trackers") or []),
            "state": st.state if st is not None else None}


def _tracker_choices(runtime: Any) -> list[dict[str, Any]]:
    hub = runtime.hub
    ids = {e for e in hub.entities if e.startswith("device_tracker.")} | set(
        s.entity_id for s in hub.states.all("device_tracker"))
    out = []
    for eid in sorted(ids):
        ent = hub.entities.get(eid)
        st = hub.states.get(eid)
        name = hub.friendly_name(ent) if ent is not None else (st.attributes.get("friendly_name") if st else None)
        out.append({"entity_id": eid, "name": name or eid, "state": st.state if st is not None else None})
    return out


def list_persons(runtime: Any) -> dict[str, Any]:
    ids = {e.unique_id: e.entity_id for e in runtime.hub.entities.values() if e.platform == "person"}
    persons = [_person_view(runtime, p, ids) for p in _stored_persons(runtime)]
    return {"persons": sorted(persons, key=lambda p: str(p["name"] or "").lower()),
            "trackers": _tracker_choices(runtime)}


def _person_fields(runtime: Any, body: dict[str, Any], *, creating: bool, person_id: str | None) -> dict[str, Any]:
    _unknown(body, {"name", "jarvis_user", "device_trackers"}, "A person")
    out: dict[str, Any] = {}
    others = [p for p in _stored_persons(runtime) if p.get("id") != person_id]
    if creating or "name" in body:
        out["name"] = _text(body, "name", "A person", required=True, limit=80)
        if any(str(p.get("name") or "").lower() == out["name"].lower() for p in others):
            raise RegistryError(f"There is already a person named {out['name']!r}", 409)
    if "jarvis_user" in body:
        user = _text(body, "jarvis_user", "A person", limit=64)
        user = user.lower() if user else None
        if user is not None and not _JARVIS_USER.match(user):
            raise RegistryError("jarvis_user must be a Jarvis username")
        if user is not None and any(str(p.get("jarvis_user") or "").lower() == user for p in others):
            raise RegistryError(f"{user} is already linked to another person", 409)
        out["jarvis_user"] = user
    if "device_trackers" in body:
        trackers = body["device_trackers"]
        if not isinstance(trackers, list) or not all(isinstance(t, str) for t in trackers):
            raise RegistryError("device_trackers is a list of device_tracker entity ids")
        known = {t["entity_id"] for t in _tracker_choices(runtime)}
        bad = sorted(t for t in trackers if not _TRACKER.match(t) or t not in known)
        if bad:
            raise RegistryError(f"There is no tracker {', '.join(bad)}")
        taken = sorted(t for t in trackers for p in others if t in (p.get("device_trackers") or []))
        if taken:
            raise RegistryError(f"{', '.join(taken)} already belongs to another person", 409)
        out["device_trackers"] = list(dict.fromkeys(trackers))
    return out


async def _restart_persons(runtime: Any) -> None:
    from services.homehub.drivers import mobile_app

    await mobile_app.setup_persons(runtime.hub, _stored_persons(runtime))


async def create_person(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    fields = _person_fields(runtime, body, creating=True, person_id=None)
    person = {"id": uuid.uuid4().hex, "user_id": None, "jarvis_user": None, "device_trackers": [], **fields}
    runtime.store.set_kv("persons", [*_stored_persons(runtime), person])
    eid = _registry_entity(runtime, "person", person["id"], person["name"])
    await _restart_persons(runtime)
    return _person_view(runtime, person, {person["id"]: eid})


async def update_person(runtime: Any, person_id: str, body: dict[str, Any]) -> dict[str, Any]:
    persons = _stored_persons(runtime)
    person = next((p for p in persons if str(p.get("id")) == person_id), None)
    if person is None:
        raise RegistryError("No such person", 404)
    person.update(_person_fields(runtime, body, creating=False, person_id=person_id))
    runtime.store.set_kv("persons", persons)
    eid = _registry_entity(runtime, "person", person_id, str(person.get("name") or person_id))
    await _restart_persons(runtime)
    return _person_view(runtime, person, {person_id: eid})


async def delete_person(runtime: Any, person_id: str) -> dict[str, Any]:
    persons = _stored_persons(runtime)
    if not any(str(p.get("id")) == person_id for p in persons):
        raise RegistryError("No such person", 404)
    runtime.store.set_kv("persons", [p for p in persons if str(p.get("id")) != person_id])
    entity_id = _drop_registry_entity(runtime, "person", person_id)
    await _restart_persons(runtime)
    if entity_id:
        from services.homehub.drivers.mobile_app import refresh_zone_states

        refresh_zone_states(runtime.hub)  # the zones stop counting a person who is gone
    return {"status": "removed", "id": person_id, "entity_id": entity_id}
