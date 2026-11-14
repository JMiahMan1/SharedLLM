"""Helpers (toggles, numbers, texts, dropdowns, dates, buttons, counters, timers) on the Automations page.

The hub keeps HA's helpers (``services/homehub/helpers.py``) and serves them
at ``/hub/helpers``. Everything here is for admins: listing, creating,
changing, deleting and running a helper's services all go to ``HOMEHUB_URL``
with the stack's internal secret, through the same client and error handling
as the automation routes (``automation_routes._hub``), so the hub's own words
("max must be greater than min") reach the page.

| Gateway route | Hub route |
|---|---|
| ``GET /api/homehub/helpers`` | ``GET /hub/helpers`` |
| ``POST /api/homehub/helpers/{domain}`` | ``POST /hub/helpers/{domain}`` |
| ``PUT|DELETE /api/homehub/helpers/{domain}/{id}`` | ``PUT|DELETE /hub/helpers/{domain}/{id}`` |
| ``POST /api/homehub/helpers/{domain}/{id}/call`` | ``POST /hub/helpers/{domain}/{id}/call`` |
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request

from services.gateway import automation_routes

router = APIRouter(prefix="/api/homehub")

#: The helper types the hub keeps; anything else is not forwarded.
_DOMAIN = re.compile(r"^(input_boolean|input_number|input_text|input_select|input_datetime|input_button|counter|timer)$")
_HELPER_ID = re.compile(r"^[a-z0-9_]{1,120}$")


def _domain(domain: str) -> str:
    if not _DOMAIN.match(domain or ""):
        raise HTTPException(status_code=404, detail="Unknown helper type")
    return domain


def _helper_id(helper_id: str) -> str:
    if not _HELPER_ID.match(helper_id or ""):
        raise HTTPException(status_code=404, detail="No such helper")
    return helper_id


async def _forward(method: str, path: str, request: Request | None = None):
    body = await automation_routes._json(request) if request is not None else None
    return automation_routes._answer(*await automation_routes._hub(method, path, json=body))


@router.get("/helpers")
async def list_helpers(request: Request):
    await automation_routes._admin(request)
    return await _forward("GET", "/hub/helpers")


@router.post("/helpers/{domain}")
async def create_helper(domain: str, request: Request):
    await automation_routes._admin(request)
    return await _forward("POST", f"/hub/helpers/{_domain(domain)}", request)


@router.put("/helpers/{domain}/{helper_id}")
async def update_helper(domain: str, helper_id: str, request: Request):
    await automation_routes._admin(request)
    return await _forward("PUT", f"/hub/helpers/{_domain(domain)}/{_helper_id(helper_id)}", request)


@router.delete("/helpers/{domain}/{helper_id}")
async def delete_helper(domain: str, helper_id: str, request: Request):
    await automation_routes._admin(request)
    return await _forward("DELETE", f"/hub/helpers/{_domain(domain)}/{_helper_id(helper_id)}")


@router.post("/helpers/{domain}/{helper_id}/call")
async def call_helper(domain: str, helper_id: str, request: Request):
    await automation_routes._admin(request)
    return await _forward("POST", f"/hub/helpers/{_domain(domain)}/{_helper_id(helper_id)}/call", request)
