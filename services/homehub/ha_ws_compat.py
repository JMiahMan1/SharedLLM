"""HA WebSocket commands other HA clients (Music Assistant's HA plugin) ask for.

Shapes follow Home Assistant's own answers. Registered into api.WS_COMMANDS.
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


COMMANDS = {
    "person/list": person_list,
    "network/url": network_url,
    "config/entity_registry/list_for_display": entity_registry_list_for_display,
    "config/auth/list": auth_list,
}
