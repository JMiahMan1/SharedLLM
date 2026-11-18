"""Which entities voice and chat may control: Home Assistant's Assist exposure, kept by the hub.

Home Assistant only let its voice assistant act on the entities "exposed" to it. The hub keeps the same
setting with the same storage, so an imported household keeps its choices and a fresh hub starts from
Home Assistant's own defaults:

* A registered entity's choice lives in its registry options, ``options["conversation"]["should_expose"]``
  (where Home Assistant keeps it, so the import carries it across untouched).
* An entity with no registry row (a state set over the API) keeps its choice in the store's
  ``voice_exposure`` key, ``{"expose_new": bool, "entities": {entity_id: bool}}``, read from Home
  Assistant's ``.storage/homeassistant.exposed_entities`` on import.
* With no choice made, Home Assistant's default applies (``default_exposed``): the domains it exposes
  by default, and sensors and binary sensors of the device classes it exposes, never a config or
  diagnostic entity or a hidden one. ``expose_new: false`` (Home Assistant's "expose new entities"
  switch turned off) exposes nothing by default.

Only control is gated: reading a state stays open. Jarvis's execution service asks
``GET /api/homehub/exposed_entities`` before an assistant (voice or chat) controls an entity.
"""
from __future__ import annotations

from typing import Any

#: Home Assistant's key for its own conversation agent, in registry options and in its storage file.
ASSISTANT = "conversation"
#: The store key for entities without a registry row, and the "expose new entities" switch.
KV_KEY = "voice_exposure"

#: Home Assistant's DEFAULT_EXPOSED_DOMAINS (homeassistant/components/homeassistant/exposed_entities.py).
DEFAULT_EXPOSED_DOMAINS = frozenset({
    "climate", "cover", "fan", "humidifier", "light", "media_player", "scene", "switch", "todo", "vacuum",
    "water_heater",
})
DEFAULT_EXPOSED_BINARY_SENSOR_DEVICE_CLASSES = frozenset({
    "door", "garage_door", "lock", "motion", "opening", "presence", "window",
})
DEFAULT_EXPOSED_SENSOR_DEVICE_CLASSES = frozenset({
    "aqi", "co", "co2", "humidity", "pm10", "pm25", "temperature", "volatile_organic_compounds",
})


def _settings(hub: Any) -> dict[str, Any]:
    store = getattr(hub, "store", None)
    raw = store.get_kv(KV_KEY, {}) if store is not None else {}
    return raw if isinstance(raw, dict) else {}


def _device_class(hub: Any, entity_id: str) -> str | None:
    ent = hub.entities.get(entity_id)
    if ent is not None and (ent.device_class or ent.original_device_class):
        return ent.device_class or ent.original_device_class
    st = hub.states.get(entity_id)
    return st.attributes.get("device_class") if st is not None else None


def default_exposed(hub: Any, entity_id: str) -> bool:
    """Home Assistant's default for an entity nobody chose for (``_is_default_exposed``)."""
    if _settings(hub).get("expose_new") is False:
        return False
    ent = hub.entities.get(entity_id)
    if ent is not None and (ent.entity_category or ent.hidden_by):
        return False
    domain = entity_id.split(".", 1)[0]
    if domain in DEFAULT_EXPOSED_DOMAINS:
        return True
    if domain == "binary_sensor":
        return _device_class(hub, entity_id) in DEFAULT_EXPOSED_BINARY_SENSOR_DEVICE_CLASSES
    if domain == "sensor":
        return _device_class(hub, entity_id) in DEFAULT_EXPOSED_SENSOR_DEVICE_CLASSES
    return False


def chosen(hub: Any, entity_id: str) -> bool | None:
    """The choice made for this entity (a person's or the import's), or None for the default."""
    ent = hub.entities.get(entity_id)
    if ent is not None:
        value = ((ent.options or {}).get(ASSISTANT) or {}).get("should_expose")
        return value if isinstance(value, bool) else None
    value = (_settings(hub).get("entities") or {}).get(entity_id)
    return value if isinstance(value, bool) else None


def is_exposed(hub: Any, entity_id: str) -> bool:
    """May voice and chat control this entity?"""
    choice = chosen(hub, entity_id)
    return default_exposed(hub, entity_id) if choice is None else choice


def view(hub: Any, entity_id: str) -> dict[str, Any]:
    """``{"exposed", "default", "chosen"}`` for the device sheet."""
    return {"exposed": is_exposed(hub, entity_id), "default": default_exposed(hub, entity_id),
            "chosen": chosen(hub, entity_id) is not None}


def set_exposed(hub: Any, entity_id: str, exposed: bool | None) -> None:
    """Record a choice (``None`` clears it, back to the default). Saves through the hub's store."""
    if exposed is not None and not isinstance(exposed, bool):
        raise ValueError("exposed must be true, false or null")
    ent = hub.entities.get(entity_id)
    if ent is not None:
        options = dict(ent.options or {})
        conv = {k: v for k, v in (options.get(ASSISTANT) or {}).items() if k != "should_expose"}
        if exposed is not None:
            conv["should_expose"] = exposed
        if conv:
            options[ASSISTANT] = conv
        else:
            options.pop(ASSISTANT, None)
        ent.options = options
        if hub.store is not None:
            hub.store.save_entity(ent)
        return
    if hub.store is None:
        raise ValueError(f"{entity_id} has no registry entry and the hub has no store to keep the choice")
    settings = dict(_settings(hub))
    entities = dict(settings.get("entities") or {})
    if exposed is None:
        entities.pop(entity_id, None)
    else:
        entities[entity_id] = exposed
    settings["entities"] = entities
    hub.store.set_kv(KV_KEY, settings)


def expose_new(hub: Any) -> bool:
    """Home Assistant's "expose new entities" switch (on unless turned off)."""
    return _settings(hub).get("expose_new") is not False


def set_expose_new(hub: Any, value: bool) -> None:
    if not isinstance(value, bool):
        raise ValueError("expose_new must be true or false")
    if hub.store is None:
        raise ValueError("the hub has no store to keep the expose new entities switch")
    hub.store.set_kv(KV_KEY, {**_settings(hub), "expose_new": value})


def exposed_entities(hub: Any) -> list[str]:
    """Every entity with a state that voice and chat may control, sorted.

    A hidden one (a duplicate device's copy, duplicates.py) is left out of the list; asked about by its id
    (``is_exposed``) it answers as before, so a command naming it still runs.
    """
    hidden = getattr(hub, "is_hidden", None)
    return sorted(eid for eid in hub.states.entity_ids()
                  if is_exposed(hub, eid) and not (callable(hidden) and hidden(eid)))


def from_ha_storage(data: Any) -> tuple[dict[str, Any], dict[str, bool]]:
    """Split Home Assistant's ``homeassistant.exposed_entities`` data.

    Returns (the ``voice_exposure`` setting with ``expose_new`` when Home Assistant had one,
    {entity_id: should_expose} for every entity it recorded a choice for).
    """
    settings: dict[str, Any] = {}
    choices: dict[str, bool] = {}
    if not isinstance(data, dict):
        return settings, choices
    prefs = (data.get("assistants") or {}).get(ASSISTANT)
    if isinstance(prefs, dict) and isinstance(prefs.get("expose_new"), bool):
        settings["expose_new"] = prefs["expose_new"]
    for eid, per in (data.get("exposed_entities") or {}).items():
        value = ((per or {}).get("assistants", per) or {}).get(ASSISTANT) if isinstance(per, dict) else None
        if isinstance(value, dict) and isinstance(value.get("should_expose"), bool):
            choices[str(eid)] = value["should_expose"]
    return settings, choices
