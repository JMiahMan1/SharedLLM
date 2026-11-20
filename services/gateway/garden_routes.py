"""Garden proxy for the Gateway.

Mirrors ``services/gateway/weather_routes.py``: every call carries
``X-Internal-Secret`` and is forwarded to the Garden service
(``services/garden``, port 8012, container `sharedllm_garden`).  The Gateway
resolves the authenticated user/role exclusively from ``gateway._acting_identity``
(Bearer token), never from client-supplied headers, so clients cannot spoof
admin or role claims.  User/role/permission headers are forwarded so the Garden
service can enforce Phase 1 permissions ("open care, guarded structure").
"""
from __future__ import annotations

import json
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request, Response

from services.gateway.config import GARDEN_SVC, INTERNAL_SECRET

router = APIRouter(prefix="/api/garden")

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_TIMEOUT = aiohttp.ClientTimeout(total=25)


async def _call(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    headers: dict | None = None,
) -> Any:
    from services.gateway.main import shared_http_client

    merged = dict(_HEADERS)
    if headers:
        merged.update(headers)
    async with shared_http_client() as client, client.request(
        method,
        f"{GARDEN_SVC.rstrip('/')}{path}",
        params=params,
        json=json,
        headers=merged,
        timeout=_TIMEOUT,
    ) as resp:
        # Any 2xx is a success: return a Response with the original status
        # code so 201/204/etc. pass through instead of being collapsed to 200.
        if 200 <= resp.status < 300:
            if resp.status == 204:
                return Response(status_code=204)
            body = await resp.json(content_type=None)
            return Response(
                content=json.dumps(body),
                status_code=resp.status,
                media_type="application/json",
            )
        # Pass through client-error and server-error status codes instead of
        # collapsing everything to 502.
        detail = "The garden service did not answer"
        with suppress(Exception):
            detail = (await resp.json(content_type=None)).get("detail") or detail
        return Response(
            content=json.dumps({"detail": detail}),
            status_code=resp.status,
            media_type="application/json",
        )


from contextlib import suppress  # noqa: E402


def _forward_headers(request: Request) -> dict:
    """Not used directly: identity is resolved asynchronously via
    _verified_headers().  Kept as a no-op placeholder for signature
    compatibility; callers must use _verified_headers instead."""
    return {}


async def _verified_headers(request: Request) -> dict:
    """Resolve identity asynchronously and return forwardable headers.
    Raises 401 if the caller is not authenticated."""
    from services.gateway import main as gateway

    ident = await gateway._acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    out: dict[str, str] = {}
    out["X-User-Id"] = ident["user"]
    if ident.get("is_admin"):
        out["X-Is-Admin"] = "true"
    family_role = ident.get("family_role")
    if family_role:
        out["X-User-Role"] = family_role
        if family_role in ("mother", "father"):
            out["X-Family-Manage"] = "true"
    return out


@router.get("/health")
async def garden_health():
    return await _call("GET", "/health")


@router.get("/species/search")
async def garden_species_search(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/species/search",
        params={
            k: v
            for k, v in request.query_params.items()
            if k in ("q", "category", "usda_zone", "limit")
        },
        headers=headers,
    )


@router.get("/species/{key}")
async def garden_species_get(key: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET", f"/species/{key}", headers=headers
    )


@router.get("/species")
async def garden_species_list(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/species",
        params={
            k: v
            for k, v in request.query_params.items()
            if k in ("category", "usda_zone", "limit")
        },
        headers=headers,
    )


@router.post("/species/lookup")
async def garden_species_lookup(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("POST", "/species/lookup", json=body, headers=headers)


@router.post("/species/custom")
async def garden_species_custom(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("POST", "/species/custom", json=body, headers=headers)


@router.delete("/species/custom/{key}")
async def garden_species_custom_delete(key: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "DELETE", f"/species/custom/{key}", headers=headers
    )


@router.post("/hooks/weather")
async def garden_hooks_weather(request: Request):
    headers = dict(_HEADERS)
    return await _call("POST", "/hooks/weather", headers=headers)


@router.get("/profile")
async def garden_profile(request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", "/profile", headers=headers)


@router.put("/profile")
async def garden_put_profile(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("PUT", "/profile", json=body, headers=headers)


@router.get("/locations")
async def garden_list_locations(request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", "/locations", headers=headers)


@router.post("/locations")
async def garden_create_location(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("POST", "/locations", json=body, headers=headers)


@router.patch("/locations/{loc_id}")
async def garden_patch_location(loc_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "PATCH", f"/locations/{loc_id}", json=body, headers=headers
    )


@router.delete("/locations/{loc_id}")
async def garden_delete_location(loc_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "DELETE", f"/locations/{loc_id}", headers=headers
    )


@router.get("/plants")
async def garden_list_plants(request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", "/plants", headers=headers)


@router.post("/plants")
async def garden_create_plant(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("POST", "/plants", json=body, headers=headers)


@router.patch("/plants/{plant_id}")
async def garden_patch_plant(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "PATCH", f"/plants/{plant_id}", json=body, headers=headers
    )


@router.delete("/plants/{plant_id}")
async def garden_delete_plant(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "DELETE", f"/plants/{plant_id}", headers=headers
    )


@router.get("/today")
async def garden_today(request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", "/today", headers=headers)


@router.post("/plants/{plant_id}/care")
async def garden_log_care(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "POST", f"/plants/{plant_id}/care", json=body, headers=headers
    )


@router.post("/plants/{plant_id}/snooze")
async def garden_snooze(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return await _call(
        "POST",
        f"/plants/{plant_id}/snooze",
        params={
            k: v
            for k, v in request.query_params.items()
            if k in ("still_damp", "postpone_days")
        },
        json=body,
        headers=headers,
    )


@router.post("/plants/{plant_id}/harvest")
async def garden_harvest(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "POST", f"/plants/{plant_id}/harvest", json=body, headers=headers
    )


@router.get("/harvests")
async def garden_harvests(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/harvests",
        params={
            k: v
            for k, v in request.query_params.items()
            if k in ("plant_id", "days", "disposition")
        },
        headers=headers,
    )


@router.delete("/harvests/{harvest_id}")
async def garden_delete_harvest(harvest_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "DELETE", f"/harvests/{harvest_id}", headers=headers
    )


@router.get("/harvest/ready")
async def garden_harvest_ready(request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", "/harvest/ready", headers=headers)


@router.post("/locations/{loc_id}/water-all")
async def garden_water_all(loc_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "POST", f"/locations/{loc_id}/water-all", headers=headers
    )


@router.get("/plants/{plant_id}/logs")
async def garden_plant_logs(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET", f"/plants/{plant_id}/logs", headers=headers
    )


@router.get("/plants/{plant_id}/care-rules")
async def garden_list_care_rules(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET", f"/plants/{plant_id}/care-rules", headers=headers
    )


@router.post("/plants/{plant_id}/care-rules")
async def garden_create_care_rule(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "POST",
        f"/plants/{plant_id}/care-rules",
        json=body,
        headers=headers,
    )


@router.patch("/plants/{plant_id}/care-rules/{rule_id}")
async def garden_patch_care_rule(plant_id: str, rule_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "PATCH",
        f"/plants/{plant_id}/care-rules/{rule_id}",
        json=body,
        headers=headers,
    )


@router.patch("/locations/{loc_id}/assign")
async def garden_assign_location(loc_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "PATCH", f"/locations/{loc_id}/assign", json=body, headers=headers
    )


@router.patch("/plants/{plant_id}/assign")
async def garden_assign_plant(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "PATCH", f"/plants/{plant_id}/assign", json=body, headers=headers
    )


@router.get("/streaks")
async def garden_streaks(request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", "/streaks", headers=headers)


@router.post("/notices/test")
async def garden_notices_test(request: Request):
    headers = await _verified_headers(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return await _call("POST", "/notices/test", json=body, headers=headers)


@router.delete("/plants/{plant_id}/care-rules/{rule_id}")
async def garden_delete_care_rule(plant_id: str, rule_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "DELETE",
        f"/plants/{plant_id}/care-rules/{rule_id}",
        headers=headers,
    )


# --------------------------------------------------------------------------- recipes bridge (Phase 4)


@router.get("/recipes/matches")
async def garden_recipes_matches(request: Request):
    """Match harvest-ready garden produce against the family cookbook."""
    from services.gateway.garden_recipes import recipe_matches
    from services.gateway.main import _cookbook_caller, _cookbook_run

    caller = await _cookbook_caller(request)
    return await recipe_matches(_cookbook_run, caller)


@router.post("/recipes/draft")
async def garden_recipes_draft(request: Request):
    """Draft a new family-sized recipe from surplus garden produce."""
    from services.gateway.garden_recipes import recipe_draft
    from services.gateway.main import _cookbook_caller, _cookbook_run

    caller = await _cookbook_caller(request)
    body = await request.json()
    return await recipe_draft(_cookbook_run, caller, body, request)
