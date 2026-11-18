"""Light groups: making, changing and listing the ``group`` entries ``drivers/group.py`` drives.

A light group is a config entry (domain ``group``, HA's options shape:
``group_type: light``, ``entities``, ``hide_members``, ``all``) with one
``light`` entity whose unique id is the entry id, as HA's group helper keeps
it. The entity has no device; its name and room are the entity's own
(``original_name``, ``area_id``), so it shows in that room like any light and
schedules, automations and voice can name it.

| Function | What |
|---|---|
| ``list_groups`` | every light group with its members (name, state, missing, hidden) and the lights that can join |
| ``create_group`` | ``{"name", "entities": [light ids], "area_id"?}`` |
| ``update_group`` | the same keys, any of them; the group keeps its entity id |
| ``delete_group`` | removes the entry and its entity (the lights themselves are untouched) |

Only lights join (never another group, so a command cannot loop). A member
that is later deleted stays listed as missing until the group is edited; one
hidden as a duplicate copy stays a member and is still switched.
"""
from __future__ import annotations

import logging
from typing import Any

from services.homehub.core import UNAVAILABLE, ConfigEntry, HubError
from services.homehub.drivers.group import members_of

log = logging.getLogger("homehub.light_groups")

DOMAIN = "group"
MAX_NAME = 80


class GroupError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def is_light_group(entry: ConfigEntry) -> bool:
    return entry.domain == DOMAIN and str({**entry.data, **entry.options}.get("group_type") or "light") == "light"


def _groups(hub: Any) -> list[ConfigEntry]:
    return [e for e in hub.config_entries.values() if is_light_group(e)]


def _group_entity_ids(hub: Any) -> set[str]:
    ids = {e.entry_id for e in _groups(hub)}
    return {eid for eid, ent in hub.entities.items() if ent.config_entry_id in ids}


def _entity_of(hub: Any, entry: ConfigEntry) -> Any:
    return next((e for e in hub.entities.values()
                 if e.config_entry_id == entry.entry_id and e.domain == "light"), None)


def _member_view(hub: Any, eid: str) -> dict[str, Any]:
    ent = hub.entities.get(eid)
    st = hub.states.get(eid)
    if ent is None:
        return {"entity_id": eid, "name": eid.split(".", 1)[1].replace("_", " "), "state": None,
                "missing": True, "hidden": False, "area": None}
    return {"entity_id": eid, "name": hub.friendly_name(ent), "state": st.state if st else None,
            "missing": False, "hidden": bool(ent.hidden_by), "area": hub.area_name(eid)}


def group_view(hub: Any, entry: ConfigEntry) -> dict[str, Any]:
    ent = _entity_of(hub, entry)
    opts = {**entry.data, **entry.options}
    members = members_of(opts)
    st = hub.states.get(ent.entity_id) if ent else None
    area_id = hub.entity_area_id(ent.entity_id) if ent else None
    return {
        "entry_id": entry.entry_id,
        "entity_id": ent.entity_id if ent else None,
        "name": hub.friendly_name(ent) if ent else entry.title,
        "area_id": area_id,
        "area": hub.areas[area_id].name if area_id in hub.areas else None,
        "state": st.state if st else None,
        "members": [_member_view(hub, eid) for eid in members],
        "missing": sum(1 for eid in members if eid not in hub.entities),
    }


def light_choices(hub: Any) -> list[dict[str, Any]]:
    """The lights that can join a group: every light but a group, hidden duplicate copies marked."""
    groups = _group_entity_ids(hub)
    out = []
    for eid in sorted(hub.entities):
        ent = hub.entities[eid]
        if ent.domain != "light" or eid in groups or ent.disabled_by:
            continue
        st = hub.states.get(eid)
        out.append({"entity_id": eid, "name": hub.friendly_name(ent), "area": hub.area_name(eid),
                    "hidden": bool(ent.hidden_by), "available": bool(st and st.state != UNAVAILABLE)})
    out.sort(key=lambda c: ((c["area"] or "~").lower(), c["name"].lower()))
    return out


def list_groups(runtime: Any) -> dict[str, Any]:
    hub = runtime.hub
    groups = [group_view(hub, e) for e in _groups(hub)]
    groups.sort(key=lambda g: str(g["name"]).lower())
    return {"groups": groups, "lights": light_choices(hub),
            "areas": [{"area_id": a.area_id, "name": a.name} for a in sorted(hub.areas.values(), key=lambda a: a.name)]}


def _name(body: dict[str, Any], required: bool) -> str | None:
    if "name" not in body:
        if required:
            raise GroupError("Give the group a name")
        return None
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise GroupError("Give the group a name")
    if len(name) > MAX_NAME:
        raise GroupError(f"Keep the name under {MAX_NAME} letters")
    return name


def _members(hub: Any, body: dict[str, Any], required: bool, keep: list[str] | None = None) -> list[str] | None:
    """The new member list, checked. Members that no longer exist may stay (``keep``), new ones must exist."""
    if "entities" not in body:
        if required:
            raise GroupError("Choose at least two lights")
        return None
    raw = body.get("entities")
    if not isinstance(raw, list):
        raise GroupError("entities must be a list of lights")
    groups = _group_entity_ids(hub)
    out: list[str] = []
    for eid in raw:
        eid = str(eid or "").strip()
        if not eid or eid in out:
            continue
        if not eid.startswith("light."):
            raise GroupError(f"{eid} is not a light")
        if eid in groups:
            raise GroupError("A light group cannot hold another group")
        if eid not in hub.entities and eid not in (keep or []):
            raise GroupError(f"There is no light {eid}")
        out.append(eid)
    if len(out) < 2:
        raise GroupError("Choose at least two lights")
    return out


def _area(hub: Any, body: dict[str, Any]) -> tuple[bool, str | None]:
    if "area_id" not in body:
        return False, None
    area = body.get("area_id")
    if area in (None, ""):
        return True, None
    found = hub.area_id_by_name(str(area))
    if found is None:
        raise GroupError(f"There is no room {area!r}")
    return True, found


def _name_taken(hub: Any, name: str, except_entry: str | None = None) -> bool:
    low = name.lower()
    for e in _groups(hub):
        if e.entry_id == except_entry:
            continue
        ent = _entity_of(hub, e)
        if (hub.friendly_name(ent) if ent else e.title).lower() == low:
            return True
    return False


async def create_group(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    from services.homehub.flows import Plan, _new_entry_id, commit

    hub = runtime.hub
    name = _name(body, True)
    members = _members(hub, body, True)
    _, area_id = _area(hub, body)
    assert name is not None and members is not None
    if _name_taken(hub, name):
        raise GroupError(f"There is already a light group called {name}", 409)
    entry_id = _new_entry_id()
    entry = ConfigEntry(entry_id=entry_id, domain=DOMAIN, title=name, source="user", data={},
                        options={"group_type": "light", "name": name, "entities": members,
                                 "hide_members": False, "all": False})
    plan = Plan(hub, entry)
    ent = plan.add_entity("light", entry_id, None)
    ent.original_name, ent.has_entity_name, ent.area_id = name, False, area_id
    await commit(runtime, plan)
    log.info("light group %s made with %d lights", name, len(members))
    return group_view(hub, entry)


def _entry(hub: Any, entry_id: str) -> ConfigEntry:
    entry = hub.config_entries.get(entry_id)
    if entry is None or not is_light_group(entry):
        raise GroupError("No such light group", 404)
    return entry


async def update_group(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    hub, store = runtime.hub, runtime.store
    entry = _entry(hub, entry_id)
    old_members = members_of({**entry.data, **entry.options})
    name = _name(body, False)
    members = _members(hub, body, False, keep=old_members)
    area_given, area_id = _area(hub, body)
    if name and _name_taken(hub, name, except_entry=entry_id):
        raise GroupError(f"There is already a light group called {name}", 409)
    ent = _entity_of(hub, entry)
    options = dict(entry.options)
    if members is not None:
        options["entities"] = members
    if name:
        options["name"] = name
        entry.title = name
        if ent is not None:
            ent.name = None  # the group's own name is the one people gave it here
            ent.original_name, ent.has_entity_name = name, False
    if area_given and ent is not None:
        ent.area_id = area_id
    entry.options = options
    store.save_config_entry(entry)
    if ent is not None:
        store.save_entity(ent)
        hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": ent.entity_id})
    await runtime.stop_entry(entry_id)
    if not entry.disabled_by:
        runtime.start_entry(entry)
    log.info("light group %s changed", entry.title)
    return group_view(hub, entry)


async def delete_group(runtime: Any, entry_id: str) -> dict[str, Any]:
    from services.homehub import entries

    entry = _entry(runtime.hub, entry_id)
    out = await entries.remove_entry(runtime, entry.entry_id)
    return {"status": "removed", "entry_id": entry_id, "entities": out.get("entities", [])}
