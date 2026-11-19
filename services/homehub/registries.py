"""The hub's small registries: floors, labels, categories, zones, persons and tags.

Each is kept in the store's key-value table in Home Assistant's own shape (the import fills them from
``.storage``), so an imported house and a hub set up from scratch read the same:

| kv key | Shape |
|---|---|
| ``floors`` | ``{floor_id: {floor_id, name, level, icon, aliases}}`` |
| ``labels`` | ``{label_id: {label_id, name, color, icon, description}}`` |
| ``categories`` | ``{scope: [{category_id, name, icon}]}`` (HA's automation/script/scene folders; read only) |
| ``zones`` | ``[{id, name, latitude, longitude, radius, passive, icon, owner, owner_name, visibility, important, notify, pin, created_at}]`` (every place but Home; ``owner`` is a hub user id) |
| ``home_place`` | ``{important, notify, pin}`` for Home (its location is ``core_config``) |
| ``persons`` | ``[{id, name, user_id, jarvis_user, device_trackers, source?, provider_key?, avatar?}]`` |
| ``tags`` | ``[{id, name, last_scanned}]`` (NFC/QR tags; names only, the tag trigger reads them) |

Zones and persons are live: a change republishes ``zone.*`` (what zone triggers and conditions read) and
restarts the person manager (``drivers/mobile_app.py``). Each zone and person gets an entity registry row
(platform ``zone``/``person``, unique id = its id), so renaming one never changes its entity id and the
automations that name it keep working.

A person with ``source`` (a login provider such as ``jarvis_identity``) is made and kept by that provider's
sync (``services/homehub/auth_providers/sync.py``): it is read-only here and is edited where its user is (in
Jarvis). Once the provider no longer has the user, the person is the hub's own again and can be edited.

Places: the hub is the only store of the family's places. Home, the zones drawn on Devices > Rooms & places
(or imported from Home Assistant) and the places people save from Wander are all zones here; a saved place has
an ``owner`` and a ``visibility`` (``family``, or ``private`` to its owner). A private place never becomes a
``zone.*`` state. Geo reads every place from ``GET /hub/zones?all=1`` for arrivals, departures and reminders.
"""
from __future__ import annotations

import logging
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from services.homehub.core import EntityEntry, HubError

log = logging.getLogger("homehub.registries")

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
#
# Every place the family has is a zone here: Home (the hub's core config), the zones drawn on Devices >
# Rooms & places or imported from Home Assistant, and the places people save from Wander (an owner, and
# either shared with the family or private to that owner). Geo, the gateway and the UI all read them from
# here; nothing else stores places.
#
# Owners are the hub's own users: ``owner`` is a hub user id, so a standalone hub (no Jarvis) has places
# owned by the users who sign in to it. When Jarvis's gateway acts for a person it names them by their Jarvis
# username (``viewer``/``owner``); the hub matches that to its own user (``hub_user_for``: a hub user synced
# from Jarvis carries ``jarvis_user``, or a person links the username to a hub user) and also keeps the name
# as ``owner_name``, so a place made before that person's hub user existed is still theirs. The hub never
# asks Jarvis anything for this.
#
# A private place is the owner's alone. It is never published as a ``zone.*`` state and has no entity
# registry row, so HA-compatible readers (``/api/states``, the websocket, templates, automations, the
# HA frontend) never see it; geo knows it as ``place.<id>`` for the owner's own arrivals and reminders.
# Only the stack's internal secret may list every place (``everything``); anyone else sees the family's
# places plus, when the caller is the gateway acting for a person, that person's own.

#: The icons a place's pin can wear on the family map ("" is automatic, from the name).
PIN_ICONS = ("home", "school", "work", "church", "store", "gym", "park", "friends", "food", "hospital", "place")
VISIBILITIES = ("family", "private")
#: How a private place is known to geo (it has no ``zone.*`` entity).
PRIVATE_PREFIX = "place."
#: Home's place settings (important, who hears, its pin); its location is the core config.
HOME_PLACE_KEY = "home_place"
MAX_ZONES = 500
MAX_PRIVATE_PER_OWNER = 200
_HEX_ID = re.compile(r"^[0-9a-f]{32}$")
#: Core-config keys a location change may carry, besides the place fields.
_HOME_LOCATION_KEYS = ("latitude", "longitude", "radius", "elevation")


def _stored_zones(runtime: Any, key: str = "zones") -> list[dict[str, Any]]:
    zones = runtime.store.get_kv(key, []) or []
    return [z for z in zones if isinstance(z, dict)] if isinstance(zones, list) else []


def _zone_entity_ids(runtime: Any) -> dict[str, str]:
    return {e.unique_id: e.entity_id for e in runtime.hub.entities.values() if e.platform == "zone"}


def is_private(zone: dict[str, Any]) -> bool:
    return zone.get("visibility") == "private"


def _clean_viewer(viewer: Any) -> str | None:
    who = str(viewer or "").strip().lower()
    return who if _JARVIS_USER.match(who) else None


@dataclass(frozen=True)
class Who:
    """Who a request acts for: a hub user (``user_id``), and/or the Jarvis username the gateway named."""

    user_id: str | None = None
    name: str | None = None


def hub_user_for(runtime: Any, jarvis_user: str | None) -> str | None:
    """The hub user a Jarvis username belongs to, from the hub's own records only: a hub user that carries
    it (``jarvis_user``, set when users are synced from Jarvis), else a person linking it to a hub user."""
    name = _clean_viewer(jarvis_user)
    if name is None:
        return None
    for u in runtime.store.get_kv("users", []) or []:
        if isinstance(u, dict) and str(u.get("jarvis_user") or "").lower() == name and u.get("id"):
            return str(u["id"])
    for p in _stored_persons(runtime):
        if str(p.get("jarvis_user") or "").lower() == name and p.get("user_id"):
            return str(p["user_id"])
    return None


def who_for(runtime: Any, *, user_id: str | None = None, jarvis_user: str | None = None) -> Who:
    name = _clean_viewer(jarvis_user)
    return Who(user_id=user_id or hub_user_for(runtime, name), name=name)


def owns(zone: dict[str, Any], who: Who | None) -> bool:
    if who is None:
        return False
    if who.user_id and zone.get("owner") == who.user_id:
        return True
    return bool(who.name) and str(zone.get("owner_name") or "").lower() == who.name


def can_see(zone: dict[str, Any], who: Who | None, everything: bool = False) -> bool:
    """A family zone is everyone's; a private one only its owner's (and the stack's, for presence)."""
    return everything or not is_private(zone) or owns(zone, who)


def _same_owner(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (bool(a.get("owner")) and a.get("owner") == b.get("owner")) or (
        bool(a.get("owner_name")) and a.get("owner_name") == b.get("owner_name"))


def apply_zones(runtime: Any) -> None:
    """Publish ``zone.home`` and every family zone; drop the states of removed or now private ones."""
    from services.homehub.drivers import mobile_app

    hub = runtime.hub
    before = {z["entity_id"] for z in getattr(hub, "zone_table", None) or []}
    home = {"name": (hub.location or {}).get("location_name", "Home"), **(hub.location or {})}
    mobile_app.setup_zones(hub, [z for z in _stored_zones(runtime) if not is_private(z)], home)
    after = {z["entity_id"] for z in hub.zone_table}
    for gone in before - after:
        hub.states.remove(gone)


def _home_place(runtime: Any) -> dict[str, Any]:
    raw = runtime.store.get_kv(HOME_PLACE_KEY, None)
    return raw if isinstance(raw, dict) else {}


def _home_view(runtime: Any) -> dict[str, Any]:
    loc = runtime.hub.location or {}
    place = _home_place(runtime)
    return {"id": "home", "entity_id": "zone.home", "name": loc.get("location_name") or "Home",
            "latitude": loc.get("latitude"), "longitude": loc.get("longitude"), "radius": loc.get("radius") or 100,
            "passive": False, "icon": "mdi:home", "source": "home", "editable": True, "removable": False,
            "owner": None, "owner_name": None, "visibility": "family", "important": bool(place.get("important")),
            "notify": list(place.get("notify") or []), "pin": str(place.get("pin") or ""), "created_at": None}


def _zone_view(z: dict[str, Any], entity_ids: dict[str, str]) -> dict[str, Any]:
    zid = str(z.get("id"))
    private = is_private(z)
    return {"id": z.get("id"), "entity_id": f"{PRIVATE_PREFIX}{zid}" if private else entity_ids.get(zid),
            "name": z.get("name"), "latitude": z.get("latitude"), "longitude": z.get("longitude"),
            "radius": z.get("radius"), "passive": bool(z.get("passive")), "icon": z.get("icon"), "source": "hub",
            "editable": True, "removable": True, "owner": z.get("owner") or None,
            "owner_name": z.get("owner_name") or None,
            "visibility": "private" if private else "family", "important": bool(z.get("important")),
            "notify": list(z.get("notify") or []), "pin": str(z.get("pin") or ""), "created_at": z.get("created_at")}


def list_zones(runtime: Any, who: Who | None = None, everything: bool = False) -> dict[str, Any]:
    """Home first, then the zones the viewer may see, by name."""
    ids = _zone_entity_ids(runtime)
    zones = [_zone_view(z, ids) for z in _stored_zones(runtime) if can_see(z, who, everything)]
    return {"zones": [_home_view(runtime), *sorted(zones, key=lambda z: str(z["name"] or "").lower())]}


def get_zone(runtime: Any, zone_id: str, who: Who | None = None, everything: bool = False) -> dict[str, Any]:
    if zone_id == "home":
        return _home_view(runtime)
    _zones, zone = _visible_zone(runtime, zone_id, who, everything)
    return _zone_view(zone, _zone_entity_ids(runtime))


def _number(body: dict[str, Any], key: str, low: float, high: float) -> float:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise RegistryError(f"{key} must be a number")
    if not low <= float(value) <= high:
        raise RegistryError(f"{key} must be between {low:g} and {high:g}")
    return float(value)


def _notify(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(u, str) for u in value):
        raise RegistryError("notify is a list of Jarvis usernames")
    names = sorted({u.strip().lower() for u in value if u.strip()})
    bad = [u for u in names if not _JARVIS_USER.match(u)]
    if bad:
        raise RegistryError(f"notify names someone who is not a Jarvis username: {', '.join(bad)}")
    return names


def _place_fields(body: dict[str, Any]) -> dict[str, Any]:
    """The place settings any zone may carry: important, who hears about it, its pin on the map."""
    out: dict[str, Any] = {}
    if "important" in body:
        if not isinstance(body["important"], bool):
            raise RegistryError("important must be true or false")
        out["important"] = body["important"]
    if "notify" in body:
        out["notify"] = _notify(body["notify"])
    if "pin" in body:
        pin = "" if body["pin"] is None else str(body["pin"]).strip().lower()
        if pin and pin not in PIN_ICONS:
            raise RegistryError(f"pin must be one of: {', '.join(PIN_ICONS)} (or empty for automatic)")
        out["pin"] = pin
    return out


def _zone_fields(runtime: Any, body: dict[str, Any], *, creating: bool, trusted: bool) -> dict[str, Any]:
    """``trusted`` (the stack's internal secret) may also say whose a place is (a Jarvis username); anyone
    else's places are their own (``create_zone``)."""
    allowed = {"name", "latitude", "longitude", "radius", "passive", "icon", "important", "notify", "pin",
               "visibility"}
    if trusted:
        allowed |= {"owner"}
    _unknown(body, allowed, "A zone")
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
    out.update(_place_fields(body))
    if "owner" in body:
        name = _clean_viewer(body["owner"]) if body["owner"] is not None else None
        if body["owner"] is not None and name is None:
            raise RegistryError("owner must be a Jarvis username")
        out["owner"] = hub_user_for(runtime, name)
        out["owner_name"] = name
    if "visibility" in body:
        if body["visibility"] not in VISIBILITIES:
            raise RegistryError("visibility must be family or private")
        out["visibility"] = body["visibility"]
    return out


def _zone_name_taken(runtime: Any, name: str, *, zone: dict[str, Any], except_id: str | None = None) -> bool:
    """A family zone's name is unique among the family's zones and Home; a private one also among its
    owner's own. Someone else's private place never blocks a name (that would tell you it exists)."""
    home = str((runtime.hub.location or {}).get("location_name") or "Home")
    if name.lower() == home.lower():
        return True
    for z in _stored_zones(runtime):
        if z.get("id") == except_id or str(z.get("name") or "").lower() != name.lower():
            continue
        if not is_private(z) or (is_private(zone) and _same_owner(z, zone)):
            return True
    return False


def _check_limits(runtime: Any, zone: dict[str, Any]) -> None:
    zones = _stored_zones(runtime)
    if len(zones) >= MAX_ZONES:
        raise RegistryError(f"The hub already has {MAX_ZONES} zones", 409)
    if is_private(zone) and sum(1 for z in zones if is_private(z) and _same_owner(z, zone)) \
            >= MAX_PRIVATE_PER_OWNER:
        raise RegistryError(f"You already have {MAX_PRIVATE_PER_OWNER} private places", 409)


def _check_private_owner(zone: dict[str, Any]) -> None:
    if is_private(zone) and not (zone.get("owner") or zone.get("owner_name")):
        raise RegistryError("A private place needs an owner")


def create_zone(runtime: Any, body: dict[str, Any], *, trusted: bool = False,
                creator: str | None = None) -> dict[str, Any]:
    """``creator`` is the hub user making it (a hub login, not Jarvis's gateway): the place is theirs."""
    fields = _zone_fields(runtime, body, creating=True, trusted=trusted)
    zone = {"id": uuid.uuid4().hex, "passive": False, "icon": "mdi:map-marker", "owner": creator,
            "owner_name": None, "visibility": "family", "important": False, "notify": [], "pin": "",
            "created_at": time.time(), **fields}
    _check_private_owner(zone)
    if _zone_name_taken(runtime, zone["name"], zone=zone):
        raise RegistryError(f"There is already a place named {zone['name']!r}", 409)
    _check_limits(runtime, zone)
    runtime.store.set_kv("zones", [*_stored_zones(runtime), zone])
    if not is_private(zone):
        _registry_entity(runtime, "zone", zone["id"], zone["name"])
    apply_zones(runtime)
    return _zone_view(zone, _zone_entity_ids(runtime))


def _visible_zone(runtime: Any, zone_id: str, who: Who | None,
                  everything: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    zones = _stored_zones(runtime)
    zone = next((z for z in zones if str(z.get("id")) == zone_id), None)
    if zone is None or not can_see(zone, who, everything):
        raise RegistryError("No such zone", 404)  # someone else's private place is not there at all
    return zones, zone


def update_zone(runtime: Any, zone_id: str, body: dict[str, Any], *, who: Who | None = None,
                everything: bool = False, trusted: bool = False) -> dict[str, Any]:
    if zone_id == "home":
        return update_home(runtime, body)
    zones, zone = _visible_zone(runtime, zone_id, who, everything)
    fields = _zone_fields(runtime, body, creating=False, trusted=trusted)
    merged = {**zone, **fields}
    if is_private(merged) and not (merged.get("owner") or merged.get("owner_name")) and who is not None:
        merged["owner"], merged["owner_name"] = who.user_id, who.name  # made private by: theirs now
        fields["owner"], fields["owner_name"] = who.user_id, who.name
    _check_private_owner(merged)
    if ("name" in fields or "visibility" in fields) and _zone_name_taken(
            runtime, merged["name"], zone=merged, except_id=zone_id):
        raise RegistryError(f"There is already a place named {merged['name']!r}", 409)
    was_private = is_private(zone)
    zone.update(fields)
    runtime.store.set_kv("zones", zones)
    if is_private(zone):
        if not was_private:
            _drop_registry_entity(runtime, "zone", zone_id)  # private now: no household entity
    else:
        _registry_entity(runtime, "zone", zone_id, zone["name"])
    apply_zones(runtime)
    return _zone_view(zone, _zone_entity_ids(runtime))


def delete_zone(runtime: Any, zone_id: str, *, who: Who | None = None, everything: bool = False) -> dict[str, Any]:
    if zone_id == "home":
        raise RegistryError("Home cannot be removed: it is the hub's own location. Move it instead.", 409)
    zones, zone = _visible_zone(runtime, zone_id, who, everything)
    runtime.store.set_kv("zones", [z for z in zones if str(z.get("id")) != zone_id])
    entity_id = None if is_private(zone) else _drop_registry_entity(runtime, "zone", zone_id)
    apply_zones(runtime)
    return {"status": "removed", "id": zone_id, "entity_id": entity_id}


# -- Home ---------------------------------------------------------------------------------------------

def update_home(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Move Home, resize it, rename it or change its place settings.

    Home's location is the hub's core config (HA's ``core.config``: latitude, longitude, radius,
    location_name), so ``zone.home``, ``/api/config``, the sun, the weather integration and Jarvis's forecast
    all follow it. It is saved at once and survives a restart.
    """
    _unknown(body, {"name", "latitude", "longitude", "radius", "elevation", "important", "notify", "pin"}, "Home")
    loc: dict[str, Any] = {}
    if "name" in body:
        loc["location_name"] = _text(body, "name", "Home", required=True, limit=80)
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180), ("radius", 1, 100000),
                           ("elevation", -500, 9000)):
        if key in body:
            loc[key] = _number(body, key, low, high)
    current = runtime.hub.location or {}
    lat = loc.get("latitude", current.get("latitude"))
    lon = loc.get("longitude", current.get("longitude"))
    if ("latitude" in loc or "longitude" in loc) and (lat is None or lon is None):
        raise RegistryError("Home needs both a latitude and a longitude")
    if "location_name" in loc and any(
            not is_private(z) and str(z.get("name") or "").lower() == loc["location_name"].lower()
            for z in _stored_zones(runtime)):
        raise RegistryError(f"There is already a place named {loc['location_name']!r}", 409)
    place = _place_fields(body)
    if loc:
        save_core_config(runtime, loc)
    if place:
        runtime.store.set_kv(HOME_PLACE_KEY, {**_home_place(runtime), **place})
    apply_zones(runtime)
    return _home_view(runtime)


def save_core_config(runtime: Any, changes: dict[str, Any]) -> dict[str, Any]:
    """Merge ``changes`` into the stored core config and the live location; HA's ``core_config_updated``."""
    hub = runtime.hub
    cfg = runtime.store.get_kv("core_config", {}) or {}
    cfg = {**(cfg if isinstance(cfg, dict) else {}), **changes}
    runtime.store.set_kv("core_config", cfg)
    live = hub.location if isinstance(hub.location, dict) else {}
    live.update(changes)
    hub.location = live
    hub.bus.fire("core_config_updated", {})
    return cfg


# -- moving the places geo used to keep ------------------------------------------------------------------

def adopt_mirrored_places(runtime: Any) -> int:
    """Once: the read-only mirror of the family's saved places (``geo_zones``, ids ``geo_<place id>``)
    becomes ordinary family zones with the place's own id. Each keeps its ``zone.*`` entity id, so the
    automations that name one keep working; geo's import then adds the owner and the place settings."""
    mirror = _stored_zones(runtime, "geo_zones")
    if not mirror:
        return 0
    zones = _stored_zones(runtime)
    have = {str(z.get("id")) for z in zones}
    adopted = 0
    for m in mirror:
        pid = str(m.get("id") or "").removeprefix("geo_")
        if not _HEX_ID.match(pid):
            continue
        if pid not in have:
            zones.append({"id": pid, "name": m.get("name") or "Saved place", "latitude": m.get("latitude"),
                          "longitude": m.get("longitude"), "radius": m.get("radius") or 150.0, "passive": False,
                          "icon": m.get("icon") or "mdi:map-marker-star", "owner": None, "visibility": "family"})
            have.add(pid)
            adopted += 1
        ent = next((e for e in runtime.hub.entities.values()
                    if e.platform == "zone" and e.unique_id == f"geo_{pid}"), None)
        if ent is not None:
            ent.unique_id = pid
            runtime.store.save_entity(ent)
    runtime.store.set_kv("zones", zones)
    runtime.store.set_kv("geo_zones", [])
    log.info("places: %d mirrored saved place(s) became zones", adopted)
    return adopted


def _place_from_geo(runtime: Any, p: dict[str, Any]) -> dict[str, Any]:
    family = p.get("scope") == "family"
    name = _clean_viewer(p.get("owner"))
    return {"name": str(p.get("name") or "Saved place")[:80], "latitude": float(p["lat"]),
            "longitude": float(p["lon"]), "radius": float(p.get("radius") or 150.0), "passive": False,
            "icon": "mdi:map-marker-star", "owner": hub_user_for(runtime, name), "owner_name": name,
            "visibility": "family" if family else "private"}


def _settings_from_geo(s: dict[str, Any]) -> dict[str, Any]:
    pin = str(s.get("icon") or "").strip().lower()
    return {"important": bool(s.get("important")),
            "notify": sorted({str(u).strip().lower() for u in s.get("notify") or [] if _clean_viewer(u)}),
            "pin": pin if pin in PIN_ICONS else ""}


def import_places(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Geo's one-time move (idempotent): its saved places and the important/notify/icon it kept per zone.

    ``places`` are geo's records (``id``, ``name``, ``lat``, ``lon``, ``radius``, ``scope``, ``owner``,
    ``important``, ``notify``, ``icon``, ``created_at``); ``settings`` maps a zone entity id to
    ``{important, notify, icon}``. A place keeps its id. One already here is not replaced: only what it is
    missing (its owner, its settings) is filled in, so running it twice changes nothing. ``entity_ids`` says
    what each ``place.<id>`` is known as now, so geo can carry its presence state and reminders over.
    """
    places = body.get("places") or []
    settings = body.get("settings") or {}
    if not isinstance(places, list) or not isinstance(settings, dict):
        raise RegistryError("places is a list and settings an object")
    zones = _stored_zones(runtime)
    by_id = {str(z.get("id")): z for z in zones}
    counts = {"created": 0, "kept": 0, "skipped": 0, "settings": 0, "settings_kept": 0}
    unmatched: list[str] = []
    for p in places:
        pid = str((p or {}).get("id") or "") if isinstance(p, dict) else ""
        try:
            fields = _place_from_geo(runtime, p)
        except (KeyError, TypeError, ValueError):
            fields = None
        if not _HEX_ID.match(pid) or fields is None:
            counts["skipped"] += 1
            continue
        extra = _settings_from_geo(p)
        existing = by_id.get(pid)
        if existing is None:
            zone = {"id": pid, **fields, **extra, "created_at": p.get("created_at") or time.time()}
            zones.append(zone)
            by_id[pid] = zone
            counts["created"] += 1
        else:
            for key, value in {**fields, **extra, "created_at": p.get("created_at")}.items():
                if existing.get(key) in (None, "") and value not in (None, ""):
                    existing[key] = value
            existing.setdefault("visibility", fields["visibility"])
            counts["kept"] += 1
    runtime.store.set_kv("zones", zones)
    moved = {str(p.get("id")) for p in places if isinstance(p, dict)}
    for z in zones:
        if not is_private(z) and str(z.get("id")) in moved:
            _registry_entity(runtime, "zone", str(z["id"]), str(z.get("name") or z["id"]))
    entity_ids = _zone_entity_ids(runtime)
    by_entity = {entity_ids.get(str(z.get("id"))): z for z in zones if not is_private(z)}
    for eid, raw in settings.items():
        if not isinstance(raw, dict):
            continue
        values = _settings_from_geo(raw)
        if eid == "zone.home":
            if isinstance(runtime.store.get_kv(HOME_PLACE_KEY, None), dict):
                counts["settings_kept"] += 1
            else:
                runtime.store.set_kv(HOME_PLACE_KEY, values)
                counts["settings"] += 1
            continue
        zone = by_entity.get(eid)
        if zone is None:
            unmatched.append(str(eid))
        elif "important" in zone:
            counts["settings_kept"] += 1
        else:
            zone.update(values)
            counts["settings"] += 1
    runtime.store.set_kv("zones", zones)
    apply_zones(runtime)
    mapping = {}
    for pid in moved:
        z = by_id.get(pid)
        if z is not None:
            mapping[f"{PRIVATE_PREFIX}{pid}"] = f"{PRIVATE_PREFIX}{pid}" if is_private(z) else entity_ids.get(pid)
    log.info("places: imported from geo: %s; unmatched settings: %s", counts, unmatched or "none")
    return {**counts, "unmatched": unmatched, "entity_ids": mapping}


# -- persons --------------------------------------------------------------------------------------------

def _stored_persons(runtime: Any) -> list[dict[str, Any]]:
    persons = runtime.store.get_kv("persons", []) or []
    return [p for p in persons if isinstance(p, dict)] if isinstance(persons, list) else []


def person_source(runtime: Any, p: dict[str, Any]) -> str | None:
    """The login provider that keeps this person (read-only here), or None for the hub's own person."""
    source = p.get("source")
    if not source:
        return None
    users = {u.get("id"): u for u in runtime.store.get_kv("users", []) or [] if isinstance(u, dict)}
    link = (users.get(p.get("user_id")) or {}).get("provider")
    if isinstance(link, dict) and link.get("type") == source and not link.get("removed"):
        return str(source)
    return None


def _person_view(runtime: Any, p: dict[str, Any], entity_ids: dict[str, str]) -> dict[str, Any]:
    eid = entity_ids.get(str(p.get("id")))
    st = runtime.hub.states.get(eid) if eid else None
    source = person_source(runtime, p)
    return {"id": p.get("id"), "entity_id": eid, "name": p.get("name"), "user_id": p.get("user_id"),
            "jarvis_user": p.get("jarvis_user"), "device_trackers": list(p.get("device_trackers") or []),
            "state": st.state if st is not None else None, "source": source or "hub", "editable": source is None,
            "avatar": p.get("avatar") if source else None}


def _refuse_synced(runtime: Any, p: dict[str, Any]) -> None:
    source = person_source(runtime, p)
    if source:
        where = "Jarvis" if source == "jarvis_identity" else source
        raise RegistryError(f"{p.get('name')} comes from {where}; change them there", 409)


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
    _refuse_synced(runtime, person)
    person.update(_person_fields(runtime, body, creating=False, person_id=person_id))
    runtime.store.set_kv("persons", persons)
    eid = _registry_entity(runtime, "person", person_id, str(person.get("name") or person_id))
    await _restart_persons(runtime)
    return _person_view(runtime, person, {person_id: eid})


async def delete_person(runtime: Any, person_id: str) -> dict[str, Any]:
    persons = _stored_persons(runtime)
    found = next((p for p in persons if str(p.get("id")) == person_id), None)
    if found is None:
        raise RegistryError("No such person", 404)
    _refuse_synced(runtime, found)
    runtime.store.set_kv("persons", [p for p in persons if str(p.get("id")) != person_id])
    entity_id = _drop_registry_entity(runtime, "person", person_id)
    await _restart_persons(runtime)
    if entity_id:
        from services.homehub.drivers.mobile_app import refresh_zone_states

        refresh_zone_states(runtime.hub)  # the zones stop counting a person who is gone
    return {"status": "removed", "id": person_id, "entity_id": entity_id}
