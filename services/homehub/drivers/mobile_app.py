"""Phones (HA's ``mobile_app``), people and zones, fed by Jarvis's geo service.

The phones cannot report to Home Assistant any more, but every phone already
reports to Jarvis (geo keeps each user's breadcrumbs). So a phone's
``device_tracker`` comes from ``GET {GEO_SVC_URL}/people`` (``X-Internal-Secret``;
no ``viewer`` means a service is asking, which sees everyone), mapped from the
Jarvis user to the HA device:

1. ``entry.options["jarvis_user"]``: the importer's explicit choice; always wins.
2. The HA person whose ``device_trackers`` list this phone's tracker.
3. The HA person whose ``user_id`` is the entry's ``user_id``.
4. ``hub.ha_users`` (optional, ``{ha_user_id: name}`` from ``.storage/auth``).
5. The device name: "Michele's Phone", "Jeremiahs Phone", "kalebs-phone"
   -> michele, jeremiah, kaleb.

A user with several phones gets the same fix on each (geo keeps one track per
user), so the battery sensor is only written for a user with one phone.

The tracker's state is the zone it is in, as HA computes it (``home``, a zone's
name, or ``not_home``), with latitude, longitude, gps_accuracy, source_type
``gps`` and battery_level. A user geo has nothing for keeps the last state.

People and zones are not config entries; the importer calls
``setup_zones(hub, zones, home)`` (HA's ``.storage/zone`` items and the core
config) and ``setup_persons(hub, persons)`` (``.storage/person`` items). Each
``person.*`` follows its trackers with HA's rules; a person with no trackers
follows their Jarvis user directly (``source: jarvis``), which HA could not do.

Notifications: ``notify.mobile_app_<device_name slug>`` (what the automations
call) and the ``notify.*`` entities' ``send_message`` go to the phone's Jarvis
user through telemetry's notices (``services/common/notify.py``, category
``home``); ``notify.notify`` goes to the whole family.

Known gap: once geo's ``HA_URL`` points at the hub, geo's ``/people`` reads
the hub's own ``person``/``device_tracker`` states back and stops returning
Jarvis's fixes (``source`` is then ``home_assistant``). Those echoes are
ignored here; geo needs a way to ask for its own fixes (``?source=jarvis``,
which this driver already sends).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import time
import unicodedata
from typing import Any

import aiohttp

from services.common import notify as jarvis_notify
from services.homehub.core import UNAVAILABLE, UNKNOWN, Driver, EntityEntry, Hub, HubError, ServiceCall, State

log = logging.getLogger("homehub.mobile_app")

GEO_TTL_S = 20.0
GEO_TIMEOUT_S = 10.0
NOTICE_CATEGORY = "home"
#: Messages the HA companion app treats as commands, not text to show.
COMMAND_MESSAGES = {"clear_notification", "remove_channel", "request_location_update", "TTS"}
IGNORED_STATES = {UNKNOWN, UNAVAILABLE, ""}


def slugify(text: str) -> str:
    """HA's ``slugify`` for the names here: "Michele's Phone" -> ``michele_s_phone``."""
    norm = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", norm).strip("_") or "unknown"


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (HA's ``location.distance`` uses Vincenty; this is within a metre here)."""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# -- zones ---------------------------------------------------------------------

def _registry_id(hub: Hub, platform: str, unique_id: str, fallback: str) -> str:
    for ent in hub.entities.values():
        if ent.platform == platform and ent.unique_id == unique_id:
            return ent.entity_id
    return fallback


def setup_zones(hub: Hub, zones: list[dict[str, Any]], home: dict[str, Any]) -> None:
    """Publish ``zone.home`` (core config) and HA's stored zones; remember them for ``zone_for``."""
    table = [{
        "entity_id": "zone.home",
        "name": str(home.get("location_name") or "Home"),
        "latitude": float(home.get("latitude") or 0.0),
        "longitude": float(home.get("longitude") or 0.0),
        "radius": float(home.get("radius") or 100.0),
        "passive": False,
        "icon": "mdi:home",
        "editable": False,
    }]
    for z in zones:
        if z.get("latitude") is None or z.get("longitude") is None:
            continue
        zid = str(z.get("id") or slugify(z.get("name") or "zone"))
        table.append({
            "entity_id": _registry_id(hub, "zone", zid, f"zone.{slugify(z.get('name') or zid)}"),
            "name": str(z.get("name") or zid),
            "latitude": float(z["latitude"]),
            "longitude": float(z["longitude"]),
            "radius": float(z.get("radius") or 100.0),
            "passive": bool(z.get("passive")),
            "icon": z.get("icon") or "mdi:map-marker",
            "editable": True,
        })
    hub.zone_table = table  # type: ignore[attr-defined]
    refresh_zone_states(hub)


def _zones(hub: Hub) -> list[dict[str, Any]]:
    table = getattr(hub, "zone_table", None)
    if table:
        return table
    loc = hub.location or {}
    if loc.get("latitude") is None or loc.get("longitude") is None:
        return []
    return [{"entity_id": "zone.home", "name": "Home", "latitude": float(loc["latitude"]),
             "longitude": float(loc["longitude"]), "radius": float(loc.get("radius") or 100.0), "passive": False}]


def zone_state_name(zone: dict[str, Any]) -> str:
    """What a tracker in ``zone`` shows: ``home`` for zone.home, else the zone's name."""
    return "home" if zone["entity_id"] == "zone.home" else zone["name"]


def active_zone(hub: Hub, lat: float, lon: float, accuracy: float = 0.0) -> dict[str, Any] | None:
    """HA's ``async_active_zone``: the closest non-passive zone the fix is in (smaller radius on a tie)."""
    best, best_dist = None, None
    for z in _zones(hub):
        if z.get("passive"):
            continue
        dist = distance_m(lat, lon, z["latitude"], z["longitude"])
        if dist - accuracy >= z["radius"]:
            continue
        if (best is None or dist < best_dist
                or (dist == best_dist and z["radius"] < best["radius"])):
            best, best_dist = z, dist
    return best


def zone_for(hub: Hub, lat: float, lon: float, accuracy: float = 0.0) -> str:
    """``home``, the zone's name, or ``not_home``, as an HA device_tracker shows it."""
    z = active_zone(hub, lat, lon, accuracy)
    return zone_state_name(z) if z is not None else "not_home"


def refresh_zone_states(hub: Hub) -> None:
    """Each zone's state is the number of persons in it (HA 2023.3+), with their entity ids."""
    persons = hub.states.all("person")
    for z in _zones(hub):
        inside = sorted(p.entity_id for p in persons if p.state == zone_state_name(z))
        hub.publish(z["entity_id"], len(inside), {
            "latitude": z["latitude"], "longitude": z["longitude"], "radius": z["radius"],
            "passive": z.get("passive", False), "persons": inside, "editable": z.get("editable", True),
            "icon": z.get("icon"), "friendly_name": z["name"],
        })


# -- persons ---------------------------------------------------------------------

class PersonManager:
    """Keeps ``person.*`` following their device trackers, as HA's person integration does."""

    def __init__(self, hub: Hub, persons: list[dict[str, Any]]) -> None:
        self.hub = hub
        self.persons: list[dict[str, Any]] = []
        for p in persons:
            pid = str(p.get("id") or slugify(p.get("name") or "person"))
            self.persons.append({
                "id": pid,
                "name": str(p.get("name") or pid),
                "user_id": p.get("user_id"),
                "device_trackers": list(p.get("device_trackers") or []),
                "entity_id": _registry_id(hub, "person", pid, f"person.{slugify(p.get('name') or pid)}"),
            })
        self._unsub = hub.bus.subscribe(self._on_state, "state_changed")

    def close(self) -> None:
        self._unsub()

    def person_for_tracker(self, tracker: str) -> dict[str, Any] | None:
        return next((p for p in self.persons if tracker in p["device_trackers"]), None)

    def person_for_user_id(self, user_id: str | None) -> dict[str, Any] | None:
        if not user_id:
            return None
        return next((p for p in self.persons if p.get("user_id") == user_id), None)

    def known_names(self) -> set[str]:
        return {p["id"].lower() for p in self.persons} | {slugify(p["name"]) for p in self.persons}

    def _on_state(self, _event: str, data: dict[str, Any]) -> None:
        eid = data.get("entity_id") or ""
        if not eid.startswith("device_tracker."):
            return
        for p in self.persons:
            if eid in p["device_trackers"]:
                self.update(p)

    def update_all(self) -> None:
        for p in self.persons:
            self.update(p)

    def update(self, p: dict[str, Any]) -> None:
        latest_home = latest_not_home = latest_gps = None

        def newer(a: State | None, b: State) -> State:
            return b if a is None or b.last_updated > a.last_updated else a

        for tracker in p["device_trackers"]:
            st = self.hub.states.get(tracker)
            if st is None or st.state in IGNORED_STATES:
                continue
            if st.attributes.get("source_type") == "gps":
                latest_gps = newer(latest_gps, st)
            elif st.state == "home":
                latest_home = newer(latest_home, st)
            elif st.state == "not_home":
                latest_not_home = newer(latest_not_home, st)
        latest = latest_home or latest_gps or latest_not_home
        if latest is None and not p["device_trackers"]:
            return  # left to update_from_fix (or the restored state)
        attrs = self._base_attrs(p)
        if latest is not None:
            for key in ("latitude", "longitude", "gps_accuracy"):
                if latest.attributes.get(key) is not None:
                    attrs[key] = latest.attributes[key]
            attrs["source"] = latest.entity_id
        self._publish(p, latest.state if latest is not None else UNKNOWN, attrs)

    def update_from_fix(self, p: dict[str, Any], fix: dict[str, Any]) -> None:
        """A person with no trackers follows their Jarvis user's latest fix."""
        if p["device_trackers"]:
            return
        attrs = self._base_attrs(p)
        attrs.update({"latitude": fix["latitude"], "longitude": fix["longitude"],
                      "gps_accuracy": fix.get("gps_accuracy"), "source": "jarvis"})
        self._publish(p, zone_for(self.hub, fix["latitude"], fix["longitude"], fix.get("gps_accuracy") or 0.0),
                      attrs)

    def _base_attrs(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"editable": True, "id": p["id"], "device_trackers": list(p["device_trackers"]),
                "user_id": p.get("user_id"), "friendly_name": p["name"]}

    def _publish(self, p: dict[str, Any], state: str, attrs: dict[str, Any]) -> None:
        old = self.hub.states.get(p["entity_id"])
        self.hub.publish(p["entity_id"], state, attrs)
        if old is None or old.state != state:
            refresh_zone_states(self.hub)


async def setup_persons(hub: Hub, persons: list[dict[str, Any]]) -> PersonManager:
    """Start (or restart) the person manager with HA's ``.storage/person`` items."""
    old = getattr(hub, "person_manager", None)
    if old is not None:
        old.close()
    mgr = PersonManager(hub, persons)
    hub.person_manager = mgr  # type: ignore[attr-defined]
    mgr.update_all()
    return mgr


# -- geo ----------------------------------------------------------------------------

def _geo_settings() -> tuple[str, str]:
    from services import config

    return str(config.GEO_SVC_URL or ""), str(config.INTERNAL_SECRET or "")


async def _fetch_geo(base: str, secret: str) -> dict[str, Any]:
    from services.common.http import get_client

    try:
        async with get_client().get(f"{base.rstrip('/')}/people", params={"source": "jarvis"},
                                    headers={"X-Internal-Secret": secret},
                                    timeout=aiohttp.ClientTimeout(total=GEO_TIMEOUT_S)) as resp:
            if resp.status != 200:
                raise HubError(f"geo /people answered HTTP {resp.status}")
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise HubError(f"geo /people: {type(exc).__name__}: {exc}") from exc


def parse_people(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Jarvis users' latest fixes from geo's FeatureCollection (only Jarvis's own fixes)."""
    out: dict[str, dict[str, Any]] = {}
    for f in (payload or {}).get("features") or []:
        props = f.get("properties") or {}
        if props.get("source_type") != "jarvis":
            continue  # an HA state geo read back (possibly our own): not a new fix
        eid = str(props.get("entity_id") or "")
        user = eid.split(".", 1)[-1].lower()
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if not user or len(coords) < 2:
            continue
        out[user] = {
            "latitude": float(coords[1]), "longitude": float(coords[0]),
            "gps_accuracy": props.get("gps_accuracy"), "battery_level": props.get("battery"),
            "speed": props.get("speed"), "course": props.get("course"),
            "last_updated": props.get("last_updated"), "zone": props.get("state"),
        }
    return out


async def people_fixes(hub: Hub) -> dict[str, dict[str, Any]]:
    """Every user's fix, fetched at most once per ``GEO_TTL_S`` for all phones together."""
    cache = getattr(hub, "_geo_people_cache", None)
    if cache is None:
        cache = {"at": 0.0, "fixes": {}, "lock": asyncio.Lock(), "warned": False}
        hub._geo_people_cache = cache  # type: ignore[attr-defined]
    async with cache["lock"]:
        if time.monotonic() - cache["at"] < GEO_TTL_S:
            return cache["fixes"]
        base, secret = _geo_settings()
        if not base:
            raise HubError("GEO_SVC_URL is not set")
        payload = await _fetch_geo(base, secret)
        if payload.get("source") not in (None, "jarvis") and not cache["warned"]:
            cache["warned"] = True
            log.warning("geo /people answered from %s, not Jarvis's own fixes; phone locations will not move "
                        "until geo can serve its breadcrumbs to the hub", payload.get("source"))
        cache["fixes"] = parse_people(payload)
        cache["at"] = time.monotonic()
        return cache["fixes"]


# -- notifications ------------------------------------------------------------------

def _notice_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    extra = data.get("data") if isinstance(data.get("data"), dict) else {}
    kw: dict[str, Any] = {}
    if extra.get("tag"):
        kw["tag"] = str(extra["tag"])
    if str(extra.get("priority") or "").lower() == "high" or extra.get("ttl") == 0:
        kw["priority"] = 4
    if extra.get("channel") == "alarm_stream":
        kw["priority"] = 5
    return kw


async def _send(users: list[str], data: dict[str, Any]) -> int:
    message = str(data.get("message") or "")
    if not message or message in COMMAND_MESSAGES or message.startswith("command_"):
        log.info("notify: dropped an HA companion-app command (%s)", message or "empty")
        return 0
    title = str(data.get("title") or "Home")
    kw = _notice_kwargs(data)
    sent = 0
    for user in users:
        sent += int(await jarvis_notify.send(jarvis_notify.notice(user, NOTICE_CATEGORY, title, message, **kw)))
    return sent


def _notify_targets(hub: Hub) -> dict[str, list[MobileAppDriver]]:
    table = getattr(hub, "mobile_app_notify", None)
    if table is None:
        table = {}
        hub.mobile_app_notify = table  # type: ignore[attr-defined]
    return table


async def _notify_phone(hub: Hub, call: ServiceCall) -> dict[str, Any]:
    slug = call.service[len("mobile_app_"):]
    users = sorted({u for d in _notify_targets(hub).get(slug, []) if (u := d.jarvis_user())})
    if not users:
        raise HubError(f"notify.{call.service}: no Jarvis user is mapped to this phone")
    return {"sent": await _send(users, call.data)}


async def _notify_family(hub: Hub, call: ServiceCall) -> dict[str, Any]:
    users = await jarvis_notify.family()
    if not users:
        raise HubError("notify.notify: the family list is unavailable")
    return {"sent": await _send(users, call.data)}


# -- the driver -----------------------------------------------------------------------

class MobileAppDriver(Driver):
    platform = "mobile_app"
    poll_interval = 30.0

    @property
    def device_name(self) -> str:
        return str(self.entry.data.get("device_name") or self.entry.title or "")

    @property
    def notify_slug(self) -> str:
        return slugify(self.device_name)

    def tracker(self) -> EntityEntry | None:
        return next((e for e in self.entities if e.domain == "device_tracker"), None)

    def jarvis_user(self) -> str | None:
        explicit = self.entry.options.get("jarvis_user")
        if explicit:
            return str(explicit).strip().lower()
        mgr = getattr(self.hub, "person_manager", None)
        tracker = self.tracker()
        if mgr is not None:
            p = (mgr.person_for_tracker(tracker.entity_id) if tracker else None) \
                or mgr.person_for_user_id(self.entry.data.get("user_id"))
            if p is not None:
                return p["id"].lower()
        ha_users = getattr(self.hub, "ha_users", None) or {}
        name = ha_users.get(self.entry.data.get("user_id") or "")
        if name:
            return slugify(str(name).split()[0])
        m = re.match(r"^([a-z]+?)(?:'s|\u2019s|s)[\s_-]+(?:phone|tablet|ipad|pixel)\b", self.device_name.lower())
        if m:
            return m.group(1)
        return None

    async def async_setup(self) -> None:
        hub = self.hub
        targets = _notify_targets(hub)
        targets.setdefault(self.notify_slug, [])
        targets[self.notify_slug] = [d for d in targets[self.notify_slug]
                                     if d.entry.entry_id != self.entry.entry_id] + [self]
        hub.register_service("notify", f"mobile_app_{self.notify_slug}", _notify_phone,
                             {"message": {"required": True}, "title": {}, "data": {}})
        if ("notify", "notify") not in hub.services:
            hub.register_service("notify", "notify", _notify_family,
                                 {"message": {"required": True}, "title": {}, "data": {}})
        for ent in self.entities:
            # A notify entity's state is when it last sent (HA); keep any restored one.
            if ent.domain == "notify" and self.hub.states.get(ent.entity_id) is None:
                self.publish(ent, UNKNOWN, {"supported_features": 1})
        with contextlib.suppress(HubError):
            # Geo being away at startup only delays the first fix.
            await self.async_poll()

    async def async_unload(self) -> None:
        targets = _notify_targets(self.hub)
        remaining = [d for d in targets.get(self.notify_slug, []) if d is not self]
        if remaining:
            targets[self.notify_slug] = remaining
        else:
            targets.pop(self.notify_slug, None)
            self.hub.services.pop(("notify", f"mobile_app_{self.notify_slug}"), None)
            self.hub.service_schemas.pop(("notify", f"mobile_app_{self.notify_slug}"), None)

    async def async_poll(self) -> None:
        user = self.jarvis_user()
        fixes = await people_fixes(self.hub)
        mgr = getattr(self.hub, "person_manager", None)
        if mgr is not None:
            for p in mgr.persons:
                if not p["device_trackers"] and p["id"].lower() in fixes:
                    mgr.update_from_fix(p, fixes[p["id"].lower()])
        if not user or user not in fixes:
            return
        fix = fixes[user]
        tracker = self.tracker()
        if tracker is not None:
            acc = float(fix.get("gps_accuracy") or 0.0)
            attrs = {"source_type": "gps", "latitude": fix["latitude"], "longitude": fix["longitude"],
                     "gps_accuracy": acc}
            if fix.get("battery_level") is not None:
                attrs["battery_level"] = fix["battery_level"]
            for key in ("speed", "course"):
                if fix.get(key) is not None:
                    attrs[key] = fix[key]
            self.publish(tracker, zone_for(self.hub, fix["latitude"], fix["longitude"], acc), attrs)
        if fix.get("battery_level") is not None and self._only_phone_of(user):
            for ent in self.entities:
                if ent.domain == "sensor" and ent.unique_id.endswith("_battery_level"):
                    self.publish(ent, int(fix["battery_level"]), {"unit_of_measurement": "%",
                                                                  "device_class": "battery",
                                                                  "state_class": "measurement"})

    def _only_phone_of(self, user: str) -> bool:
        phones = [d for d in self.hub.drivers.values() if isinstance(d, MobileAppDriver)]
        if self not in phones:
            phones.append(self)
        return sum(1 for d in phones if d.jarvis_user() == user) == 1

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain == "notify" and service == "send_message":
            user = self.jarvis_user()
            if not user:
                raise HubError(f"{entity.entity_id}: no Jarvis user is mapped to this phone")
            return await _send([user], data)
        return await super().async_call(entity, domain, service, data)
