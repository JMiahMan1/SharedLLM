"""Switching several lights from one spoken phrase, without the model.

"Turn off the kitchen lights" with seven bulbs called "Kitchen Light 1" to
"Kitchen Light 7" used to tie in ``IntentEngine.resolve_device`` and go to the
model, which took 16 to 22 seconds, switched one bulb or none, and said the
others "should be off now". Here:

* a phrase naming a light group, or one that resolves to the group holding the
  lights it names, switches the group's members;
* a plural or room phrase ("the kitchen lights", "all the lights in the
  kitchen", "the kitchen") that ties among several lights with no group
  switches all of them (``IntentEngine.resolve_device_set``);

always only lights and switches this person can see, through execution's
``/execute/lights``: every light is checked like a single one, and the answer
is one sentence saying what really happened ("6 of 7 kitchen lights are off;
Kitchen Light 4 isn't responding."). Nothing is claimed that was not seen.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("gateway.fast_lights")


class LightSet(tuple):
    """``(entity_ids, label, names)``: the lights to switch together, what to call them, and each one's name."""

    def __new__(cls, entity_ids: list[str], label: str, names: dict[str, str] | None = None):
        return super().__new__(cls, (list(entity_ids), label, dict(names or {})))

    @property
    def names(self) -> dict[str, str]:
        return self[2]

    @property
    def entity_ids(self) -> list[str]:
        return self[0]

    @property
    def label(self) -> str:
        return self[1]


def _visible(entities: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {str(e.get("entity_id")): e for e in entities or [] if isinstance(e, dict) and e.get("entity_id")}


def _names(ids: list[str], seen: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Each light's friendly name, for execution to say (it names a generic "Light" from its id)."""
    out = {}
    for eid in ids:
        name = str(((seen.get(eid) or {}).get("attributes") or {}).get("friendly_name") or "").strip()
        if name:
            out[eid] = name
    return out


def group_members(entity_id: str | None, entities: list[dict[str, Any]] | None) -> LightSet | None:
    """A light group's members, when ``entity_id`` is a group and this person can see every one of them.

    A group with a member this person cannot see is left to switch as itself (``None``).
    """
    seen = _visible(entities)
    group = seen.get(entity_id or "")
    if group is None or not str(entity_id).startswith("light."):
        return None
    attrs = group.get("attributes") or {}
    members = attrs.get("entity_id")
    if not isinstance(members, list) or not members:
        return None
    members = [str(m) for m in members]
    if any(m not in seen for m in members):
        log.info(f"[FastPath] {entity_id}: not every member is visible; the group switches as itself")
        return None
    return LightSet(members, str(attrs.get("friendly_name") or str(entity_id).split(".", 1)[1].replace("_", " ")),
                    _names(members, seen))


def light_set_for(engine: Any, query: str, intent: str, entities: list[dict[str, Any]] | None,
                  voice_context: Any = None) -> LightSet | None:
    """The lights a plural or room phrase names together, limited to those this person can see."""
    found = engine.extract_device_set(query, intent)
    if found is not None:
        if found.group:
            return group_members(found.group, entities)
        seen = _visible(entities)
        ids = [e for e in found.entity_ids if e in seen]
        if not ids:
            return None
        return LightSet(ids, found.label, _names(ids, seen))

    # No room named in the query: fall back to the satellite's area when available.
    seen = _visible(entities)
    area_id = None
    area_name = None
    if isinstance(voice_context, dict):
        area_id = voice_context.get("area_id")
        area_name = voice_context.get("area")
    if not area_id and isinstance(voice_context, dict):
        area_id = voice_context.get("area")
    if area_id:
        ids = []
        for eid, e in seen.items():
            if not str(eid).startswith(("light.", "switch.")):
                continue
            attrs = e.get("attributes") or {}
            if str(attrs.get("area_id") or "") == str(area_id):
                ids.append(eid)
        if ids:
            return LightSet(ids, area_name or str(area_id), _names(ids, seen))
    return None


def choose(engine: Any, query: str, intent: str, resolved: str | None,
           entities: list[dict[str, Any]] | None, voice_context: Any = None) -> LightSet | None:
    """What a turn on/off command switches together, or None for the usual single device.

    A resolved light group switches as its members (so the answer names any that did not
    change). A plural or room phrase switches its set when no single device was found, or
    when the one found is just one of the set ("the porch lights" scoring Porch Light Right
    a hair ahead of Porch Light Left).
    """
    if resolved:
        members = group_members(resolved, entities)
        if members is not None:
            return members
    found = light_set_for(engine, query, intent, entities, voice_context)
    if found is None:
        return None
    if resolved is None or (len(found.entity_ids) > 1 and resolved in found.entity_ids):
        return found
    return None


async def switch(light_set: LightSet, intent: str, creds: dict[str, Any], post: Any) -> str:
    """Switch the set through execution's ``/execute/lights``; its one-sentence answer.

    ``post(path, payload)`` returns execution's JSON answer.
    """
    payload = {"user_context": creds, "entity_ids": light_set.entity_ids,
               "action": "turn_on" if intent == "turn_on" else "turn_off", "label": light_set.label,
               "names": light_set.names}
    try:
        answer = await post("/execute/lights", payload)
    except Exception as exc:  # the execution service did not answer: nothing can be claimed
        log.warning(f"[FastPath] /execute/lights for '{light_set.label}' failed: {type(exc).__name__}")
        return f"I couldn't reach the {light_set.label} just now, so I don't know if they changed."
    message = str((answer or {}).get("message") or "").strip()
    return message or f"I couldn't tell whether the {light_set.label} changed."
