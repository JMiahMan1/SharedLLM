"""Nothing internal is ever said aloud: an entity id in a spoken reply becomes the device's name.

"Playing 'worship' (radio) on media_player.office_jarvis_echo_media_player" was read out by
a satellite. A spoken reply must say what a person would: "the Office Echo". ``speakable``
replaces every entity id (``<domain>.<object_id>`` for the hub's domains) with the device's
friendly name when the caller knows it, else with a name made from the id itself ("Office
Jarvis Echo"), and drops what is left of quoting around it. Text with no id comes back as is.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

#: The hub's entity domains, as an id in text starts with one of them.
DOMAINS = (
    "media_player", "light", "switch", "sensor", "binary_sensor", "climate", "cover", "fan", "lock", "camera",
    "vacuum", "scene", "script", "automation", "assist_satellite", "remote", "button", "select", "number",
    "person", "device_tracker", "zone", "alarm_control_panel", "water_heater", "humidifier", "siren", "update",
    "event", "todo", "calendar", "weather", "notify", "tts", "stt", "conversation", "group", "valve",
    "lawn_mower", "image", "text", "date", "time", "datetime", "input_boolean", "input_number", "input_select",
    "input_text", "input_button", "input_datetime", "timer", "counter",
)
ENTITY_ID = re.compile(r"\b(?:" + "|".join(sorted(DOMAINS, key=len, reverse=True)) + r")\.[a-z0-9_]+\b")
#: Words at the end of an id that say what kind of device it is, not which one.
_KIND_TAIL = re.compile(r"(?:\s+(?:media\s+player|assist\s+satellite|speaker|player|entity|device))+$", re.IGNORECASE)


def humanize(entity_id: str) -> str:
    """"Office Jarvis Echo" from "media_player.office_jarvis_echo_media_player"."""
    _domain, _, obj = entity_id.partition(".")
    words = " ".join(obj.replace("_", " ").split())
    words = _KIND_TAIL.sub("", words).strip() or words
    return " ".join(w if any(c.isdigit() for c in w) else w.capitalize() for w in words.split())


def speakable(text: str | None, names: Mapping[str, str] | None = None) -> str:
    """``text`` with every entity id replaced by its friendly name (``names``) or a name made from it."""
    if not text or "." not in text:
        return text or ""

    def name_for(m: re.Match) -> str:
        eid = m.group(0)
        friendly = str((names or {}).get(eid) or "").strip()
        return friendly if friendly and not ENTITY_ID.fullmatch(friendly) else humanize(eid)

    return ENTITY_ID.sub(name_for, text)


def has_entity_id(text: str | None) -> bool:
    return bool(text) and ENTITY_ID.search(text or "") is not None
