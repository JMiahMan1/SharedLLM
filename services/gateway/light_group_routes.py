"""Devices > Light groups: grouping lights so they switch as one (homehub ``light_groups_api.py``).

| Route | What |
|---|---|
| ``GET /api/homehub/light_groups`` | the groups with their members, the lights that can join, the rooms |
| ``POST /api/homehub/light_groups`` | ``{"name", "entities", "area_id"?}`` |
| ``PATCH /api/homehub/light_groups/{entry_id}`` | any of those keys |
| ``DELETE /api/homehub/light_groups/{entry_id}`` | removes the group, never its lights |

For parents (father, mother) and admins: a household's rooms are theirs to
arrange, and the rest of the hub's setup stays with admins. The hub is reached
with the stack's internal secret (``discovery_routes._hub``), and its answers
("choose at least two lights", "already a group called ...") are passed through.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.gateway import discovery_routes as hub_api

router = APIRouter(prefix="/api/homehub")

_ENTRY_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_KEYS = ("name", "entities", "area_id")


async def _parent_or_admin(request: Request) -> str:
    from services.gateway import main as gateway
    from services.gateway.calls import is_parent

    ident = await gateway._acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not is_parent(ident):
        raise HTTPException(status_code=403, detail="Only parents can group lights")
    return str(ident["user"])


def _entry(entry_id: str) -> str:
    if not _ENTRY_ID.match(entry_id or ""):
        raise HTTPException(status_code=404, detail="No such light group")
    return entry_id


def _body(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: raw[k] for k in _KEYS if k in raw}


@router.get("/light_groups")
async def list_light_groups(request: Request):
    await _parent_or_admin(request)
    return hub_api._answer(*await hub_api._hub("GET", "/hub/light_groups"))


@router.post("/light_groups")
async def create_light_group(request: Request):
    await _parent_or_admin(request)
    body = _body(await hub_api._json(request))
    return hub_api._answer(*await hub_api._hub("POST", "/hub/light_groups", json=body))


@router.patch("/light_groups/{entry_id}")
async def update_light_group(entry_id: str, request: Request):
    await _parent_or_admin(request)
    body = _body(await hub_api._json(request))
    return hub_api._answer(*await hub_api._hub("PATCH", f"/hub/light_groups/{_entry(entry_id)}", json=body))


@router.delete("/light_groups/{entry_id}")
async def delete_light_group(entry_id: str, request: Request):
    await _parent_or_admin(request)
    return hub_api._answer(*await hub_api._hub("DELETE", f"/hub/light_groups/{_entry(entry_id)}"))
