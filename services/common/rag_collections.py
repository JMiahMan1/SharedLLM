"""Names of RAG collections that more than one service uses.

The home's devices are indexed in ``home_devices``, whichever home hub they
come from (HomeHub, Home Assistant, or both). The collection used to be called
``ha_entities``. That old name is still accepted wherever a caller names a
collection (a lesson or prompt written before the rename, a service still
running the previous build during a deploy) and means ``home_devices``. The
RAG service moves rows stored under the old name once, at startup.
"""
from __future__ import annotations

#: The index of the home's devices, one row per entity per user per hub.
HOME_DEVICES = "home_devices"

#: The collection's name before the rename. Only read, never written.
LEGACY_HOME_DEVICES = "ha_entities"

#: The home hubs a device row can come from (the row's ``source``).
SOURCE_HOMEHUB = "homehub"
SOURCE_HOME_ASSISTANT = "home_assistant"
DEVICE_SOURCES = (SOURCE_HOMEHUB, SOURCE_HOME_ASSISTANT)

_ALIASES = {LEGACY_HOME_DEVICES: HOME_DEVICES}


def canonical_collection(name: str | None) -> str | None:
    """``name`` with an old collection name replaced by the current one."""
    if name is None:
        return None
    return _ALIASES.get(name.strip(), name)
