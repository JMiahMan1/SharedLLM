"""What Jarvis calls a device out loud: its name, never its entity id.

A device's friendly name is used as it is, unless it is only a generic word: a Matter bulb a person never
named reports itself as "Light", and seven of those are seven "Light"s. Then the name comes from the
entity id, which usually carries the room: ``light.downstairs_bathroom_1_light`` -> "Downstairs Bathroom
1 Light". An entity id itself is never said.
"""
from __future__ import annotations

import re

#: Friendly names that say what a thing is, not which one it is.
GENERIC_NAMES = frozenset({
    "light", "lights", "lamp", "bulb", "light bulb", "smart bulb", "smart led bulb", "led", "switch", "plug",
    "smart plug", "outlet", "socket", "fan", "speaker", "tv", "sensor", "device",
})
_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def from_entity_id(entity_id: str) -> str:
    """``light.downstairs_bathroom_1_light`` -> "Downstairs Bathroom 1 Light"."""
    words = [w for w in str(entity_id or "").split(".", 1)[-1].split("_") if w]
    return " ".join(w if w.isdigit() else w.capitalize() for w in words) or "that device"


def spoken_name(entity_id: str, friendly: str | None = None) -> str:
    """The name to say for ``entity_id``: its friendly name unless that is generic, empty or an id."""
    name = " ".join(str(friendly or "").split())
    if name and name.lower() not in GENERIC_NAMES and not _ENTITY_ID.match(name):
        return name
    return from_entity_id(entity_id)
