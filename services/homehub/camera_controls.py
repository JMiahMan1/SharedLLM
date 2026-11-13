"""What a camera's device can do, for the camera view (feature discovery).

A camera is rarely only a camera. A Reolink camera has a siren, a speaker
volume, a status light, person/vehicle/animal detection and its own settings;
an ESP32 camera on a garage door board shares it with the door's relay and
sensor. Everything on the camera's device is found here, so a new camera shows
its features in the camera view without anyone setting them up:

``actions``
    What a person does from the camera: an ``opener`` (a door sensor paired with
    the switch or button that moves the door), the ``siren``, lights, locks,
    covers, switches and buttons.
``status``
    What the camera sees and reports: detections, door state, battery.
``settings``
    The device's configuration (``entity_category: config``): sensitivities,
    volume, day/night mode, recording switches. For admins only (the gateway
    leaves them out for everyone else).

Entities that are disabled or hidden are left out, so an admin hides one from
the camera view the way they hide it anywhere (Devices). Each item is the
dashboard's entity summary (``dashboard_api.summarize``) plus ``group`` and
``role``; the gateway filters them by the viewer's permissions.
"""
from __future__ import annotations

from typing import Any

from services.homehub import dashboard_api

#: Domains that are never a control in the camera view.
SKIPPED = {"camera", "update", "event", "image", "tts", "stt", "conversation", "assist_satellite"}
ACTION_DOMAINS = ("siren", "light", "lock", "cover", "valve", "switch", "button", "fan")
SETTING_DOMAINS = {"number", "select", "text"}
#: Door sensors an opener can pair with.
DOOR_CLASSES = {"door", "garage_door", "opening", "gate"}
#: Status order: what a person looks for first.
STATUS_ORDER = ("motion", "person", "vehicle", "animal", "pet", "face", "package", "visitor", "door",
                "garage_door", "opening", "occupancy", "battery")


def _role(domain: str) -> str:
    return domain if domain in ("siren", "light", "lock", "cover", "valve", "fan", "button") else "switch"


def _detection(eid: str, device_class: str | None) -> str:
    """The detection or reading an entity is about, for ordering: its device class, else its name's last word."""
    if device_class:
        return device_class
    return eid.split(".", 1)[1].rsplit("_", 1)[-1]


def discover(hub: Any, camera_id: str) -> dict[str, Any] | None:
    """The camera's controls, grouped; None when it is not a camera."""
    cam = hub.entities.get(camera_id)
    if cam is None or cam.domain != "camera" or cam.disabled_by:
        return None
    siblings = [e for e in hub.entities.values()
                if cam.device_id and e.device_id == cam.device_id and e.entity_id != camera_id
                and not e.disabled_by and not e.hidden_by and e.domain not in SKIPPED]
    items: dict[str, dict[str, Any]] = {}
    for ent in siblings:
        summary = dashboard_api.summarize(hub, ent.entity_id)
        if summary is not None:
            items[ent.entity_id] = summary

    def category(eid: str) -> str | None:
        return hub.entities[eid].entity_category

    actions: list[dict[str, Any]] = []
    status: list[dict[str, Any]] = []
    settings: list[dict[str, Any]] = []

    # An opener: one door sensor and one switch/button (not a setting) on the device, like a garage door
    # board's relay. The pair becomes one control: the door's state, and a press that moves it.
    doors = [i for i in items.values() if i["domain"] == "binary_sensor" and i["device_class"] in DOOR_CLASSES]
    movers = [i for i in items.values() if i["domain"] in ("switch", "button") and category(i["entity_id"]) is None]
    paired: set[str] = set()
    if len(doors) == 1 and len(movers) == 1:
        door, mover = doors[0], movers[0]
        actions.append({**mover, "group": "actions", "role": "opener", "door": door})
        paired = {door["entity_id"], mover["entity_id"]}

    for eid, item in items.items():
        if eid in paired:
            continue
        cat, domain = category(eid), item["domain"]
        if cat == "config" or domain in SETTING_DOMAINS:
            settings.append({**item, "group": "settings", "role": domain})
        elif domain in ACTION_DOMAINS:
            actions.append({**item, "group": "actions", "role": _role(domain)})
        elif domain in ("binary_sensor", "sensor"):
            status.append({**item, "group": "status", "role": _detection(eid, item["device_class"])})

    rank = {d: i for i, d in enumerate(("opener", *ACTION_DOMAINS))}
    actions.sort(key=lambda i: (rank.get(i["role"], 99), i["name"].lower()))
    order = {d: i for i, d in enumerate(STATUS_ORDER)}
    status.sort(key=lambda i: (order.get(i["role"], 99), i["name"].lower()))
    settings.sort(key=lambda i: (i["domain"], i["name"].lower()))
    return {"entity_id": camera_id, "device_id": cam.device_id, "actions": actions, "status": status,
            "settings": settings}
