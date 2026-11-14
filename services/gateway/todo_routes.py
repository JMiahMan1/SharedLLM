"""The Lists page: homehub's to-do lists (HA's ``todo`` services) for every household member.

Reads and writes go to the hub at the caller's own Home Assistant URL with
their own token, as the Rooms and History pages do (dashboard_routes.py); the
token never reaches the browser. The calls are HA's own: ``todo.get_items``,
``add_item``, ``update_item``, ``remove_item``, ``remove_completed_items``,
and the hub's ``todo.move_item`` (HA's WebSocket ``todo/item/move``).

Who may use a list: a to-do list is household data, like the whole-house
energy totals, so every signed-in member reads and writes every list without a
device assignment. Protection wins, as everywhere: a list an admin has locked
is only for its permit list and answers 404 to anyone else, as if it did not
exist. The rule is ``list_visible`` in the assistant's own list tool
(services/execution/handlers/todo_list.py), so the page and Jarvis agree.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request

from services.execution.handlers.todo_list import COMPLETED, NEEDS_ACTION, list_visible, lists_from_states
from services.gateway import dashboard_routes as dash

log = logging.getLogger("gateway.lists")

router = APIRouter(prefix="/api/lists")

_TODO_ID = re.compile(r"^todo\.[a-z0-9_]+$")
MAX_SUMMARY = 255
MAX_DESCRIPTION = 2000


async def _hub_post(creds: dict[str, Any], path: str, body: dict[str, Any], *, response: bool = False) -> Any:
    """A service call to the hub with the caller's own HA credentials; the hub's refusal passes through."""
    from services.gateway.main import shared_http_client

    base = str(creds.get("ha_url") or "").rstrip("/")
    token = str(creds.get("ha_token") or "")
    if not base or not token:
        raise HTTPException(status_code=503, detail="Home Assistant is not set up for your account")
    try:
        async with shared_http_client() as client, client.post(
            f"{base}{path}", params={"return_response": ""} if response else None, json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=dash._TIMEOUT,
        ) as resp:
            status = resp.status
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                payload = None
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[lists] hub %s: %s", path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The home hub did not answer") from exc
    if status == 200:
        return payload
    if status in (400, 404):
        message = str(payload.get("message") or payload.get("detail") or "") if isinstance(payload, dict) else ""
        raise HTTPException(status_code=400, detail=message or "The home hub refused that")
    log.warning("[lists] hub %s answered HTTP %s", path, status)
    raise HTTPException(status_code=502, detail="The home hub did not answer")


async def _visible_lists(creds: dict[str, Any]) -> list[dict[str, Any]]:
    states = await dash._hub_get(creds, "/api/states")
    return lists_from_states(states, await dash._permissions(creds))


async def _items(creds: dict[str, Any], entity_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not entity_ids:
        return {}
    data = await _hub_post(creds, "/api/services/todo/get_items", {"entity_id": entity_ids}, response=True)
    body = data.get("service_response") if isinstance(data, dict) else None
    out = {}
    for eid in entity_ids:
        items = ((body or {}).get(eid) or {}).get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=502, detail="The home hub sent no items for a list")
        out[eid] = [_item(i) for i in items if isinstance(i, dict)]
    return out


def _item(raw: dict[str, Any]) -> dict[str, Any]:
    return {"uid": str(raw.get("uid") or ""), "summary": str(raw.get("summary") or ""),
            "status": COMPLETED if raw.get("status") == COMPLETED else NEEDS_ACTION,
            "due": raw.get("due"), "description": raw.get("description")}


async def _one(creds: dict[str, Any], entity_id: str) -> dict[str, Any]:
    """The list, with its items, when the caller may use it; 404 otherwise."""
    if not _TODO_ID.match(entity_id or ""):
        raise HTTPException(status_code=404, detail="List not found")
    perms = await dash._permissions(creds)
    if not list_visible(perms, entity_id):
        raise HTTPException(status_code=404, detail="List not found")
    found = next((x for x in await _visible_lists(creds) if x["entity_id"] == entity_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="List not found")
    items = (await _items(creds, [entity_id]))[entity_id] if found["available"] else []
    return {**found, "items": items}


async def _run(creds: dict[str, Any], entity_id: str, service: str, data: dict[str, Any]) -> dict[str, Any]:
    """Run a todo service on a list the caller was already checked for, and answer with the list as it now is."""
    await _hub_post(creds, f"/api/services/todo/{service}", {"entity_id": entity_id, **data})
    return await _one(creds, entity_id)


def _text(body: dict[str, Any], key: str, limit: int, *, required: bool = False) -> str | None:
    if key not in body or body[key] is None:
        if required:
            raise HTTPException(status_code=400, detail=f"{key} is required")
        return None
    value = body[key]
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be text")
    value = value.strip()
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{key} cannot be empty")
    if len(value) > limit:
        raise HTTPException(status_code=400, detail=f"{key} is longer than {limit} characters")
    return value


def _due_fields(body: dict[str, Any], features: dict[str, bool]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, feature in (("due_date", "due_date"), ("due_datetime", "due_datetime")):
        if key in body:
            if not features.get(feature):
                raise HTTPException(status_code=400, detail="This list has no due dates")
            value = body[key]
            if value is not None and not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"{key} must be an ISO date")
            out[key] = value or None
    if "description" in body:
        if not features.get("description"):
            raise HTTPException(status_code=400, detail="This list has no descriptions")
        out["description"] = _text(body, "description", MAX_DESCRIPTION) or None
    return out


@router.get("")
async def all_lists(request: Request):
    """Every list the caller may use, with its items: ``{lists: [{entity_id, name, open, features, items}]}``."""
    creds = await dash._caller(request)
    lists = await _visible_lists(creds)
    items = await _items(creds, [x["entity_id"] for x in lists if x["available"]])
    return {"lists": [{**x, "items": items.get(x["entity_id"], [])} for x in lists]}


@router.get("/{entity_id}")
async def one_list(entity_id: str, request: Request):
    return await _one(await dash._caller(request), entity_id)


@router.post("/{entity_id}/items")
async def add_item(entity_id: str, request: Request):
    creds = await dash._caller(request)
    body = await dash._json(request)
    summary = _text(body, "summary", MAX_SUMMARY, required=True)
    current = await _one(creds, entity_id)
    return await _run(creds, entity_id, "add_item", {"item": summary, **_due_fields(body, current["features"])})


@router.patch("/{entity_id}/items/{uid}")
async def update_item(entity_id: str, uid: str, request: Request):
    """Rename, tick off or back on, or change the due date or notes of one item."""
    creds = await dash._caller(request)
    body = await dash._json(request)
    current = await _one(creds, entity_id)
    data: dict[str, Any] = {}
    if "summary" in body:
        data["rename"] = _text(body, "summary", MAX_SUMMARY, required=True)
    if "status" in body:
        if body["status"] not in (NEEDS_ACTION, COMPLETED):
            raise HTTPException(status_code=400, detail="status is needs_action or completed")
        data["status"] = body["status"]
    data.update(_due_fields(body, current["features"]))
    if not data:
        raise HTTPException(status_code=400, detail="Nothing to change")
    _known(current, uid)
    return await _run(creds, entity_id, "update_item", {"item": uid, **data})


@router.delete("/{entity_id}/items/{uid}")
async def remove_item(entity_id: str, uid: str, request: Request):
    creds = await dash._caller(request)
    _known(await _one(creds, entity_id), uid)
    return await _run(creds, entity_id, "remove_item", {"item": uid})


@router.post("/{entity_id}/items/{uid}/move")
async def move_item(entity_id: str, uid: str, request: Request):
    """Put an item right after ``previous_uid``, or at the top when it is null (HA's ``todo/item/move``)."""
    creds = await dash._caller(request)
    body = await dash._json(request)
    current = await _one(creds, entity_id)
    if not current["features"].get("move"):
        raise HTTPException(status_code=400, detail="This list cannot be reordered")
    previous = body.get("previous_uid")
    if previous is not None and not isinstance(previous, str):
        raise HTTPException(status_code=400, detail="previous_uid must be an item id or null")
    _known(current, uid)
    if previous:
        _known(current, previous)
    return await _run(creds, entity_id, "move_item", {"item": uid, "previous_uid": previous or None})


@router.post("/{entity_id}/clear_completed")
async def clear_completed(entity_id: str, request: Request):
    creds = await dash._caller(request)
    await _one(creds, entity_id)
    return await _run(creds, entity_id, "remove_completed_items", {})


def _known(current: dict[str, Any], uid: str) -> None:
    """The hub also finds items by their words; the page only ever names one by its id."""
    if not any(i["uid"] == uid for i in current["items"]):
        raise HTTPException(status_code=404, detail="That item is no longer on the list")

