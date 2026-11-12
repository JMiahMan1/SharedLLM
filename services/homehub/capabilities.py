"""What every device and entity can do: the capability index.

Every answer is derived at the moment it is asked, from what the hub holds:

1. **The entity's own state attributes**, which drivers publish with HA's
   meaning: ``supported_features`` (decoded with HA's feature flags per
   domain), ``supported_color_modes``, ``hvac_modes``, ``min_temp`` /
   ``max_temp``, ``fan_modes``, ``preset_modes``, ``source_list``,
   ``device_class``...
2. **The registry**: the entity's domain and platform (the integration), its
   registry ``capabilities`` (HA's ``min``/``max``/``options``...), and the
   protocol detail its unique_id carries (an ESPHome entity's platform, a
   HomeKit characteristic's accessory/instance ids, a Matter node endpoint).
3. **The integration's protocol**: what its driver can do beyond HA's flags,
   read from the driver itself (camera stream sources, an MJPEG relay) and from
   ``PLATFORM_ABILITIES`` (a Cast device can show a web page; a Roku launches
   apps). A driver may also define ``entity_abilities(entity) -> list[str]``,
   which is added as is.

Nothing here names a device, an entity id or a room. A device added through
discovery is in the index the moment its entities are registered and publish a
state, with no restart: the index is computed from the live registry.

An **ability** is a short word (``ABILITIES``). ``services`` lists the
``domain.service`` calls an entity accepts, so an automation editor offers
only those. ``details`` carries the values that go with an ability (the
colour modes, the temperature range, the inputs, the stream types).

The API (``capabilities_api``): ``GET /api/homehub/capabilities`` with
``ability`` (repeatable: all must hold), ``area``, ``domain``, ``device_id``,
``platform``; and ``GET /api/homehub/capabilities/{entity_id}``.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services.homehub.core import UNAVAILABLE

log = logging.getLogger("homehub.capabilities")

#: Every ability the index can report, with what it means (the API lists these).
ABILITIES: dict[str, str] = {
    "power": "turns on and off", "toggle": "toggles",
    "dim": "sets a brightness", "color": "sets a colour", "color_temp": "sets a white temperature",
    "effect": "runs light effects", "flash": "flashes", "transition": "fades",
    "play_media": "plays a media URL or item", "play": "plays", "pause": "pauses", "stop": "stops",
    "seek": "seeks", "next_track": "skips tracks", "volume": "sets the volume", "volume_step": "steps the volume",
    "mute": "mutes", "select_source": "switches input or app", "sound_mode": "switches the sound mode",
    "browse_media": "browses its media", "search_media": "searches media", "enqueue": "queues media",
    "shuffle": "shuffles", "repeat": "repeats", "group": "joins speaker groups",
    "announce": "speaks an announcement over what it plays", "tts": "speaks text",
    "cast": "casts media to it", "show_url": "shows a web page", "launch_app": "launches apps",
    "set_temperature": "sets a target temperature", "set_temperature_range": "sets a low/high range",
    "set_humidity": "sets a target humidity", "hvac_mode": "switches heat/cool modes",
    "fan_mode": "sets a fan mode", "preset_mode": "sets presets", "swing_mode": "sets swing",
    "open": "opens", "close": "closes", "set_position": "sets a position", "tilt": "tilts",
    "lock": "locks and unlocks", "open_door": "opens the latch",
    "set_speed": "sets a fan speed", "oscillate": "oscillates", "direction": "reverses direction",
    "camera_snapshot": "gives a picture", "camera_stream": "streams live video", "camera_recordings": "has recordings",
    "vacuum_start": "starts cleaning", "vacuum_return": "returns to its dock", "locate": "beeps to be found",
    "clean_spot": "cleans a spot", "fan_speed": "sets suction",
    "siren": "sounds", "tones": "plays tones",
    "press": "is pressed", "set_value": "takes a number", "select_option": "takes an option", "set_text": "takes text",
    "remote_command": "sends remote commands", "learn_command": "learns remote commands", "activity": "runs activities",
    "notify": "takes notifications", "todo": "keeps a to-do list", "weather": "forecasts weather",
    "sense": "reports a reading", "detect": "reports on/off events", "track": "reports a location",
    "event": "fires events", "update": "installs firmware",
}

# HA's feature flags, per domain (homeassistant.components.<domain>.const).
MEDIA = {"pause": 1, "seek": 2, "volume": 4, "mute": 8, "previous_track": 16, "next_track": 32, "turn_on": 128,
         "turn_off": 256, "play_media": 512, "volume_step": 1024, "select_source": 2048, "stop": 4096,
         "clear_playlist": 8192, "play": 16384, "shuffle": 32768, "sound_mode": 65536, "browse_media": 131072,
         "repeat": 262144, "group": 524288, "announce": 1048576, "enqueue": 2097152, "search_media": 4194304}
LIGHT = {"effect": 4, "flash": 8, "transition": 32}
CLIMATE = {"set_temperature": 1, "set_temperature_range": 2, "set_humidity": 4, "fan_mode": 8, "preset_mode": 16,
           "swing_mode": 32, "turn_off": 128, "turn_on": 256}
COVER = {"open": 1, "close": 2, "set_position": 4, "stop": 8, "open_tilt": 16, "close_tilt": 32,
         "set_tilt_position": 128}
VALVE = {"open": 1, "close": 2, "set_position": 4, "stop": 8}
FAN = {"set_speed": 1, "oscillate": 2, "direction": 4, "preset_mode": 8, "turn_off": 16, "turn_on": 32}
LOCK = {"open_door": 1}
VACUUM = {"pause": 4, "stop": 8, "return_home": 16, "fan_speed": 32, "locate": 512, "clean_spot": 1024,
          "start": 8192}
SIREN = {"turn_on": 1, "turn_off": 2, "tones": 4, "volume": 8, "duration": 16}
REMOTE = {"learn_command": 1, "activity": 4}
CAMERA = {"on_off": 1, "stream": 2}
TODO = {"create": 1, "delete": 2, "update": 4, "move": 8}

#: What an integration's protocol does beyond HA's flags (integration-level, like HA's manifests).
PLATFORM_ABILITIES: dict[str, dict[str, list[str]]] = {
    "cast": {"media_player": ["cast", "show_url", "tts"]},
    "webostv": {"media_player": ["show_url", "launch_app"]},
    "samsungtv": {"media_player": ["show_url", "launch_app"]},
    "androidtv_remote": {"media_player": ["launch_app"]},
    "roku": {"media_player": ["launch_app"]},
    "music_assistant": {"media_player": ["tts", "enqueue"]},
    "esphome": {"media_player": ["tts"]},
    "dlna_dmr": {"media_player": ["tts"]},
}

#: The HA services each ability (or the bare domain) unlocks, for editors.
_ALWAYS: dict[str, list[str]] = {
    "light": ["turn_on", "turn_off", "toggle"], "switch": ["turn_on", "turn_off", "toggle"],
    "input_boolean": ["turn_on", "turn_off", "toggle"], "lock": ["lock", "unlock"],
    "button": ["press"], "input_button": ["press"], "number": ["set_value"], "input_number": ["set_value"],
    "select": ["select_option"], "input_select": ["select_option"], "text": ["set_value"],
    "camera": ["snapshot"], "remote": ["send_command", "turn_on", "turn_off", "toggle"],
    "scene": ["turn_on"], "script": ["turn_on", "turn_off", "toggle"],
    "weather": ["get_forecasts"], "notify": ["send_message"], "tts": ["speak"], "todo": ["get_items"],
    "update": ["install", "skip"], "climate": ["set_hvac_mode"],
}
_ABILITY_SERVICES: dict[str, dict[str, list[str]]] = {
    "media_player": {"power": ["turn_on", "turn_off", "toggle"], "play_media": ["play_media"],
                     "play": ["media_play"], "pause": ["media_pause", "media_play_pause"], "stop": ["media_stop"],
                     "seek": ["media_seek"], "next_track": ["media_next_track", "media_previous_track"],
                     "volume": ["volume_set"], "volume_step": ["volume_up", "volume_down"], "mute": ["volume_mute"],
                     "select_source": ["select_source"], "sound_mode": ["select_sound_mode"],
                     "shuffle": ["shuffle_set"], "repeat": ["repeat_set"], "group": ["join", "unjoin"],
                     "browse_media": ["browse_media"], "search_media": ["search_media"]},
    "climate": {"set_temperature": ["set_temperature"], "set_temperature_range": ["set_temperature"],
                "set_humidity": ["set_humidity"], "fan_mode": ["set_fan_mode"], "preset_mode": ["set_preset_mode"],
                "swing_mode": ["set_swing_mode"], "power": ["turn_on", "turn_off"]},
    "cover": {"open": ["open_cover"], "close": ["close_cover"], "set_position": ["set_cover_position"],
              "stop": ["stop_cover"], "tilt": ["open_cover_tilt", "close_cover_tilt", "set_cover_tilt_position"]},
    "valve": {"open": ["open_valve"], "close": ["close_valve"], "set_position": ["set_valve_position"],
              "stop": ["stop_valve"]},
    "fan": {"power": ["turn_on", "turn_off", "toggle"], "set_speed": ["set_percentage", "increase_speed",
                                                                      "decrease_speed"],
            "oscillate": ["oscillate"], "direction": ["set_direction"], "preset_mode": ["set_preset_mode"]},
    "lock": {"open_door": ["open"]},
    "vacuum": {"vacuum_start": ["start"], "pause": ["pause"], "stop": ["stop"], "vacuum_return": ["return_to_base"],
               "locate": ["locate"], "clean_spot": ["clean_spot"], "fan_speed": ["set_fan_speed"],
               "remote_command": ["send_command"]},
    "siren": {"power": ["turn_on", "turn_off", "toggle"]},
    "todo": {"todo": ["add_item", "update_item", "remove_item", "remove_completed_items"]},
}


def _flags(features: int, table: dict[str, int]) -> set[str]:
    return {name for name, bit in table.items() if features & bit}


def _features(attrs: dict[str, Any], ent: Any) -> int:
    raw = attrs.get("supported_features")
    if raw is None and ent is not None:
        raw = getattr(ent, "supported_features", 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _protocol(ent: Any, entry: Any) -> dict[str, Any]:
    """What the unique_id says about the device's own protocol."""
    if ent is None:
        return {}
    uid = str(ent.unique_id or "")
    out: dict[str, Any] = {}
    if ent.platform == "esphome":
        parts = uid.split("/")
        if len(parts) >= 4:
            out["esphome_entity_type"] = parts[2]
    elif ent.platform == "homekit_controller":
        m = re.match(r"^(.+)_(\d+)_(\d+)(?:_(\d+))?$", uid)
        if m:
            out["homekit"] = {"aid": int(m.group(2)), "iid": int(m.group(3))}
    elif ent.platform == "matter":
        m = re.search(r"-([0-9A-Fa-f]+)-MatterNodeDevice-(\d+)-(.+)$", uid)
        if m:
            out["matter"] = {"node_id": int(m.group(1), 16), "endpoint": int(m.group(2)), "key": m.group(3)}
    if ent.capabilities:
        out["registry"] = ent.capabilities
    return out


def _camera(hub: Any, ent: Any, attrs: dict[str, Any], features: int) -> tuple[set[str], dict[str, Any]]:
    abilities = {"camera_snapshot"}
    streams = ["snapshot"]
    driver = hub.drivers.get(ent.config_entry_id or "") if ent is not None else None
    if driver is not None:
        if hasattr(driver, "async_open_mjpeg_stream"):
            streams.append("mjpeg")
        if hasattr(driver, "async_camera_stream_sources") or features & CAMERA["stream"]:
            streams.append("rtsp")
        if hasattr(driver, "async_open_recording"):
            abilities.add("camera_recordings")
    try:
        from services.homehub import live_video

        if ent is not None and live_video.camera_summary(hub, ent.entity_id).get("realtime"):
            streams.append("webrtc")
    except Exception:  # live video is optional; a camera without it still streams snapshots
        log.exception("live video could not describe %s", ent.entity_id if ent is not None else "a camera")
    abilities.add("camera_stream")
    return abilities, {"stream_types": streams}


def entity_capabilities(hub: Any, entity_id: str) -> dict[str, Any]:
    """``{entity_id, domain, platform, abilities, services, details, protocol, available}`` for one entity."""
    st = hub.states.get(entity_id)
    ent = hub.entities.get(entity_id)
    attrs = dict(st.attributes) if st is not None else {}
    domain = entity_id.split(".", 1)[0]
    platform = ent.platform if ent is not None else None
    entry = hub.config_entries.get(ent.config_entry_id or "") if ent is not None else None
    features = _features(attrs, ent)
    abilities: set[str] = set()
    details: dict[str, Any] = {}
    device_class = attrs.get("device_class") or (ent.device_class or ent.original_device_class if ent else None)
    if device_class:
        details["device_class"] = device_class

    if domain == "light":
        abilities |= {"power", "toggle"}
        modes = [str(m) for m in attrs.get("supported_color_modes") or []]
        if modes:
            details["color_modes"] = modes
        if any(m != "onoff" for m in modes) or attrs.get("brightness") is not None:
            abilities.add("dim")
        if set(modes) & {"hs", "xy", "rgb", "rgbw", "rgbww"}:
            abilities.add("color")
        if "color_temp" in modes or attrs.get("min_color_temp_kelvin"):
            abilities.add("color_temp")
            for key in ("min_color_temp_kelvin", "max_color_temp_kelvin"):
                if attrs.get(key) is not None:
                    details[key] = attrs[key]
        abilities |= _flags(features, LIGHT)
        if attrs.get("effect_list"):
            abilities.add("effect")
            details["effects"] = list(attrs["effect_list"])
    elif domain in ("switch", "input_boolean"):
        abilities |= {"power", "toggle"}
    elif domain == "media_player":
        f = _flags(features, MEDIA)
        if f & {"turn_on", "turn_off"}:
            abilities |= {"power", "toggle"}
        if "previous_track" in f:
            f.add("next_track")
        abilities |= f - {"turn_on", "turn_off", "previous_track", "clear_playlist"}
        if attrs.get("source_list"):
            details["sources"] = list(attrs["source_list"])
        if attrs.get("sound_mode_list"):
            details["sound_modes"] = list(attrs["sound_mode_list"])
        if "announce" in abilities:
            abilities.add("tts")
    elif domain == "climate":
        f = _flags(features, CLIMATE)
        abilities |= f - {"turn_on", "turn_off"}
        if f & {"turn_on", "turn_off"}:
            abilities.add("power")
        if attrs.get("hvac_modes"):
            abilities.add("hvac_mode")
            details["hvac_modes"] = list(attrs["hvac_modes"])
        for key in ("fan_modes", "preset_modes", "swing_modes"):
            if attrs.get(key):
                details[key] = list(attrs[key])
                abilities.add(key[:-1])
        for key in ("min_temp", "max_temp", "target_temp_step", "min_humidity", "max_humidity"):
            if attrs.get(key) is not None:
                details[key] = attrs[key]
        if attrs.get("temperature_unit") or attrs.get("unit_of_measurement"):
            details["temperature_unit"] = attrs.get("temperature_unit") or attrs.get("unit_of_measurement")
    elif domain in ("cover", "valve"):
        f = _flags(features, COVER if domain == "cover" else VALVE)
        if f & {"open_tilt", "close_tilt", "set_tilt_position"}:
            abilities.add("tilt")
        abilities |= f & {"open", "close", "set_position", "stop"}
    elif domain == "fan":
        f = _flags(features, FAN)
        abilities |= (f - {"turn_on", "turn_off"}) | {"power", "toggle"}
        if attrs.get("preset_modes"):
            details["preset_modes"] = list(attrs["preset_modes"])
    elif domain == "lock":
        abilities.add("lock")
        abilities |= _flags(features, LOCK)
    elif domain == "camera":
        cam, extra = _camera(hub, ent, attrs, features)
        abilities |= cam
        details.update(extra)
    elif domain == "vacuum":
        f = _flags(features, VACUUM)
        abilities |= {"vacuum_start" if x == "start" else "vacuum_return" if x == "return_home" else x for x in f}
        if attrs.get("fan_speed_list"):
            details["fan_speeds"] = list(attrs["fan_speed_list"])
    elif domain == "siren":
        f = _flags(features, SIREN)
        abilities |= {"siren", "power"} | (f & {"tones", "volume"})
        if attrs.get("available_tones"):
            details["tones"] = attrs["available_tones"]
    elif domain == "remote":
        abilities |= {"remote_command", "power", "toggle"} | _flags(features, REMOTE)
        if attrs.get("activity_list"):
            details["activities"] = list(attrs["activity_list"])
    elif domain in ("button", "input_button"):
        abilities.add("press")
    elif domain in ("number", "input_number"):
        abilities.add("set_value")
        for key in ("min", "max", "step", "unit_of_measurement", "mode"):
            value = attrs.get(key, (ent.capabilities or {}).get(key) if ent is not None else None)
            if value is not None:
                details[key] = value
    elif domain in ("select", "input_select"):
        abilities.add("select_option")
        options = attrs.get("options") or ((ent.capabilities or {}).get("options") if ent is not None else None)
        if options:
            details["options"] = list(options)
    elif domain == "text":
        abilities.add("set_text")
    elif domain == "sensor":
        abilities.add("sense")
        if attrs.get("unit_of_measurement"):
            details["unit"] = attrs["unit_of_measurement"]
    elif domain == "binary_sensor":
        abilities.add("detect")
    elif domain in ("device_tracker", "person"):
        abilities.add("track")
    elif domain == "notify":
        abilities.add("notify")
    elif domain == "tts":
        abilities.add("tts")
    elif domain == "todo":
        abilities.add("todo")
    elif domain == "weather":
        abilities.add("weather")
    elif domain == "event":
        abilities.add("event")
        if attrs.get("event_types"):
            details["event_types"] = list(attrs["event_types"])
    elif domain == "update":
        abilities.add("update")

    abilities |= set((PLATFORM_ABILITIES.get(platform or "") or {}).get(domain, []))
    driver = hub.drivers.get(ent.config_entry_id or "") if ent is not None else None
    hook = getattr(driver, "entity_abilities", None) if driver is not None else None
    if callable(hook):
        try:
            abilities |= {str(a) for a in hook(ent) or []}
        except Exception:  # a driver's own extras must never hide the rest, but are reported
            log.exception("%s.entity_abilities(%s) failed", type(driver).__name__, entity_id)
    services = sorted({f"{domain}.{s}" for s in _ALWAYS.get(domain, [])}
                      | {f"{domain}.{s}" for a in abilities for s in _ABILITY_SERVICES.get(domain, {}).get(a, [])})
    return {
        "entity_id": entity_id, "domain": domain, "platform": platform,
        "integration": entry.domain if entry is not None else platform,
        "device_id": ent.device_id if ent is not None else None,
        "area_id": hub.entity_area_id(entity_id),
        "name": st.name if st is not None else (hub.friendly_name(ent) if ent is not None else entity_id),
        "available": st is not None and st.state != UNAVAILABLE,
        "abilities": sorted(abilities), "services": services, "details": details,
        "protocol": _protocol(ent, entry),
    }


def abilities_of(hub: Any, entity_id: str) -> list[str]:
    return entity_capabilities(hub, entity_id)["abilities"]


def _candidates(hub: Any) -> list[str]:
    ids = set(hub.states.entity_ids())
    ids |= {e.entity_id for e in hub.entities.values() if not e.disabled_by}
    return sorted(eid for eid in ids if not (hub.entities.get(eid) and hub.entities[eid].disabled_by))


def find(hub: Any, abilities: list[str] | None = None, *, area: str | None = None, domain: str | None = None,
         device_id: str | None = None, platform: str | None = None,
         available_only: bool = False) -> list[dict[str, Any]]:
    """Entities with every ability asked for, optionally in one area (id or name), domain, device or platform."""
    want = set(abilities or [])
    unknown = sorted(want - set(ABILITIES))
    if unknown:
        raise ValueError(f"unknown abilities: {', '.join(unknown)} (known: {', '.join(sorted(ABILITIES))})")
    area_id = None
    if area:
        area_id = hub.area_id_by_name(area)
        if area_id is None:
            raise LookupError(f"There is no area {area!r}")
    out = []
    for eid in _candidates(hub):
        if domain and not eid.startswith(domain + "."):
            continue
        ent = hub.entities.get(eid)
        if device_id and (ent is None or ent.device_id != device_id):
            continue
        if platform and (ent is None or ent.platform != platform):
            continue
        if area_id and hub.entity_area_id(eid) != area_id:
            continue
        cap = entity_capabilities(hub, eid)
        if want - set(cap["abilities"]):
            continue
        if available_only and not cap["available"]:
            continue
        out.append(cap)
    return out


def device_capabilities(hub: Any, device_id: str) -> dict[str, Any]:
    """A device's abilities: the union of its enabled entities', each with the entities that provide it."""
    by_ability: dict[str, list[str]] = {}
    for eid in hub.device_entities(device_id):
        ent = hub.entities.get(eid)
        if ent is None or ent.disabled_by:
            continue
        for a in abilities_of(hub, eid):
            by_ability.setdefault(a, []).append(eid)
    return {"abilities": sorted(by_ability), "by_ability": by_ability}


def summary(hub: Any) -> dict[str, int]:
    """How many entities have each ability (the index at a glance)."""
    counts: dict[str, int] = {}
    for eid in _candidates(hub):
        for a in abilities_of(hub, eid):
            counts[a] = counts.get(a, 0) + 1
    return dict(sorted(counts.items()))
