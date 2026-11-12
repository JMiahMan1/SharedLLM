"""Services HA provides itself rather than through an integration."""
from __future__ import annotations

import logging
import re
from typing import Any

from services.homehub.core import Hub, HubError, ServiceCall

log = logging.getLogger("homehub.builtin")

_DEV_ID = re.compile(r"^[a-z0-9_]+$")


async def device_tracker_see(hub: Hub, call: ServiceCall) -> None:
    """``device_tracker.see``: Jarvis forwards each phone's GPS fix here (identity's location relay)."""
    d = call.data
    dev_id = str(d.get("dev_id") or "").lower()
    if not _DEV_ID.match(dev_id):
        raise HubError("dev_id must be a lower-case slug")
    entity_id = f"device_tracker.{dev_id}"
    cur = hub.states.get(entity_id)
    attrs: dict[str, Any] = dict(cur.attributes) if cur else {"friendly_name": dev_id.replace("_", " ")}
    attrs["source_type"] = d.get("source_type") or "gps"
    gps = d.get("gps")
    state = d.get("location_name")
    if isinstance(gps, list | tuple) and len(gps) == 2:
        lat, lon = float(gps[0]), float(gps[1])
        attrs["latitude"], attrs["longitude"] = lat, lon
        if state is None:
            state = _zone_for(hub, lat, lon)
    if d.get("gps_accuracy") is not None:
        attrs["gps_accuracy"] = int(d["gps_accuracy"])
    if d.get("battery") is not None:
        attrs["battery_level"] = int(d["battery"])
    if d.get("host_name"):
        attrs["host_name"] = d["host_name"]
    attrs.update(d.get("attributes") or {})
    ent = hub.entities.get(entity_id)
    if ent is not None:
        hub.publish(ent, state or "not_home", attrs)
    else:
        hub.states.set(entity_id, state or "not_home", attrs)


def _zone_for(hub: Hub, lat: float, lon: float) -> str:
    try:
        from services.homehub.drivers.mobile_app import zone_for

        return zone_for(hub, lat, lon)
    except Exception:
        log.debug("zone lookup unavailable", exc_info=True)
        return "not_home"


async def update_entity(hub: Hub, call: ServiceCall) -> None:
    """``homeassistant.update_entity``: poll the drivers behind the targets now."""
    seen: set[str] = set()
    for eid in call.entity_ids:
        ent = hub.entities.get(eid)
        if ent is None or ent.config_entry_id in seen:
            continue
        seen.add(ent.config_entry_id or "")
        driver = hub.drivers.get(ent.config_entry_id or "")
        if driver is not None:
            await driver.async_poll()


async def persistent_notification(hub: Hub, call: ServiceCall) -> None:
    nid = str(call.data.get("notification_id") or "homehub")
    eid = f"persistent_notification.{re.sub(r'[^a-z0-9_]+', '_', nid.lower())}"
    if call.service == "dismiss":
        hub.states.remove(eid)
        return
    hub.states.set(eid, "notifying", {"title": call.data.get("title"), "message": call.data.get("message")})
    log.info("notification %s: %s", call.data.get("title"), call.data.get("message"))


async def logbook_log(hub: Hub, call: ServiceCall) -> None:
    log.info("logbook: %s %s", call.data.get("name"), call.data.get("message"))
    hub.bus.fire("logbook_entry", dict(call.data))


async def system_log_write(hub: Hub, call: ServiceCall) -> None:
    level = getattr(logging, str(call.data.get("level", "error")).upper(), logging.ERROR)
    logging.getLogger(call.data.get("logger") or "homehub.system_log").log(level, "%s", call.data.get("message"))


async def noop(hub: Hub, call: ServiceCall) -> None:
    """Reload services HA has for YAML it re-reads; the hub reloads through /hub/import."""


def register(hub: Hub) -> None:
    hub.register_service("device_tracker", "see", device_tracker_see,
                         {"dev_id": {}, "gps": {}, "gps_accuracy": {}, "battery": {}, "location_name": {}})
    hub.register_service("homeassistant", "update_entity", update_entity, {"entity_id": {}})
    hub.register_service("homeassistant", "reload_core_config", noop)
    hub.register_service("homeassistant", "reload_all", noop)
    hub.register_service("persistent_notification", "create", persistent_notification)
    hub.register_service("persistent_notification", "dismiss", persistent_notification)
    hub.register_service("logbook", "log", logbook_log)
    hub.register_service("system_log", "write", system_log_write)
