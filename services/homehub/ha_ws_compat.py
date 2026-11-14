"""HA WebSocket commands other HA clients (Music Assistant's HA plugin, HA's own frontend) ask for.

Shapes follow Home Assistant's own answers. Registered into api.WS_COMMANDS.

Assist exposure (``homeassistant/expose_entity``, ``.../list``, ``homeassistant/expose_new_entities/get|set``)
is kept by exposure.py. The hub has one assistant, Home Assistant's own ``conversation`` agent (Jarvis's voice
and chat); a command naming another (``cloud.alexa``, ``cloud.google_assistant``) is refused, not ignored.
"""
from __future__ import annotations

import os
from typing import Any

#: HA numbers entity categories in ``list_for_display``.
_CATEGORIES = {"config": 0, "diagnostic": 1}


async def person_list(conn: Any, mid: int, _msg: dict[str, Any]) -> None:
    store = conn.runtime.store
    persons = store.get_kv("persons", []) or []
    await conn.result(mid, {"storage": [{
        "id": p.get("id"), "name": p.get("name"), "user_id": p.get("user_id"),
        "device_trackers": p.get("device_trackers") or [], "picture": p.get("picture"),
    } for p in persons], "config": []})


async def network_url(conn: Any, mid: int, _msg: dict[str, Any]) -> None:
    loc = conn.hub.location or {}
    internal = os.getenv("HOMEHUB_LOCAL_URL") or loc.get("internal_url") \
        or f"http://127.0.0.1:{os.getenv('HOMEHUB_PORT', '8123')}"
    await conn.result(mid, {"internal": internal, "external": loc.get("external_url"), "cloud": None})


async def entity_registry_list_for_display(conn: Any, mid: int, _msg: dict[str, Any]) -> None:
    out = []
    for e in conn.hub.entities.values():
        if e.disabled_by:
            continue
        row: dict[str, Any] = {"ei": e.entity_id, "pl": e.platform}
        if e.device_id:
            row["di"] = e.device_id
        if e.area_id:
            row["ai"] = e.area_id
        if e.name:
            row["en"] = e.name
        if e.entity_category in _CATEGORIES:
            row["ec"] = _CATEGORIES[e.entity_category]
        if e.hidden_by:
            row["hb"] = True
        if e.has_entity_name:
            row["hn"] = True
        out.append(row)
    await conn.result(mid, {"entity_categories": {str(v): k for k, v in _CATEGORIES.items()}, "entities": out})


async def auth_list(conn: Any, mid: int, _msg: dict[str, Any]) -> None:
    """The hub's users without credentials (HA answers this to admins; the names are not secret)."""
    users = conn.runtime.store.get_kv("users", []) or []
    await conn.result(mid, [{
        "id": u.get("id"), "name": u.get("name"), "username": None, "is_owner": bool(u.get("is_owner")),
        "is_active": u.get("is_active", True), "local_only": False,
        "system_generated": bool(u.get("system_generated")), "group_ids": u.get("group_ids") or [],
        "credentials": [],
    } for u in users])


async def entity_registry_get_entries(conn: Any, mid: int, msg: dict[str, Any]) -> None:
    """HA's batch lookup: ``{entity_id: entry or None}`` (Music Assistant's player provider)."""
    from services.homehub.mobile_app_webhook import registry_view

    out: dict[str, Any] = {}
    for eid in msg.get("entity_ids") or []:
        ent = conn.hub.entities.get(str(eid))
        out[str(eid)] = registry_view(ent, conn.principal) if ent is not None else None
    await conn.result(mid, out)


def _admin_only(conn: Any) -> bool:
    return conn.principal is not None and conn.principal.is_admin


def _assistants(msg: dict[str, Any], key: str = "assistants") -> list[str] | None:
    raw = msg.get(key)
    names = [raw] if isinstance(raw, str) else raw
    if not isinstance(names, list) or not names or not all(isinstance(a, str) for a in names):
        return None
    return names


async def expose_entity(conn: Any, mid: int, msg: dict[str, Any]) -> None:
    """``{assistants, entity_ids, should_expose}``: record the choice for each entity (HA's ws_expose_entity)."""
    from services.homehub import exposure

    if not _admin_only(conn):
        await conn.error(mid, "unauthorized", "Only an admin can change what voice may control")
        return
    assistants = _assistants(msg)
    entity_ids = msg.get("entity_ids")
    should = msg.get("should_expose")
    if assistants is None or not isinstance(entity_ids, list) or not entity_ids or not isinstance(should, bool):
        await conn.error(mid, "invalid_format",
                         "expose_entity needs assistants, entity_ids (lists) and should_expose (true or false)")
        return
    other = sorted(set(assistants) - {exposure.ASSISTANT})
    if other:
        await conn.error(mid, "not_supported",
                         f"This hub only keeps exposure for {exposure.ASSISTANT}, not {', '.join(other)}")
        return
    hub = conn.hub
    unknown = [str(e) for e in entity_ids if hub.entities.get(str(e)) is None and hub.states.get(str(e)) is None]
    if unknown:
        await conn.error(mid, "not_found", f"Unknown entity: {', '.join(unknown)}")
        return
    for eid in entity_ids:
        exposure.set_exposed(hub, str(eid), should)
    await conn.result(mid)


async def expose_entity_list(conn: Any, mid: int, _msg: dict[str, Any]) -> None:
    """``{"exposed_entities": {entity_id: {"conversation": bool}}}`` for every entity the hub knows."""
    from services.homehub import exposure

    hub = conn.hub
    ids = sorted(set(hub.entities) | set(hub.states.entity_ids()))
    await conn.result(mid, {"exposed_entities": {eid: {exposure.ASSISTANT: exposure.is_exposed(hub, eid)}
                                                 for eid in ids}})


async def expose_new_entities_get(conn: Any, mid: int, msg: dict[str, Any]) -> None:
    from services.homehub import exposure

    if msg.get("assistant") != exposure.ASSISTANT:
        await conn.error(mid, "not_supported", f"This hub only keeps exposure for {exposure.ASSISTANT}")
        return
    await conn.result(mid, {"expose_new": exposure.expose_new(conn.hub)})


async def expose_new_entities_set(conn: Any, mid: int, msg: dict[str, Any]) -> None:
    from services.homehub import exposure

    if not _admin_only(conn):
        await conn.error(mid, "unauthorized", "Only an admin can change what voice may control")
        return
    if msg.get("assistant") != exposure.ASSISTANT:
        await conn.error(mid, "not_supported", f"This hub only keeps exposure for {exposure.ASSISTANT}")
        return
    if not isinstance(msg.get("expose_new"), bool):
        await conn.error(mid, "invalid_format", "expose_new must be true or false")
        return
    exposure.set_expose_new(conn.hub, msg["expose_new"])
    await conn.result(mid)


COMMANDS = {
    "homeassistant/expose_entity": expose_entity,
    "homeassistant/expose_entity/list": expose_entity_list,
    "homeassistant/expose_new_entities/get": expose_new_entities_get,
    "homeassistant/expose_new_entities/set": expose_new_entities_set,
    "config/entity_registry/get_entries": entity_registry_get_entries,
    "person/list": person_list,
    "network/url": network_url,
    "config/entity_registry/list_for_display": entity_registry_list_for_display,
    "config/auth/list": auth_list,
}
