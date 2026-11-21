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

import json as _json
import logging
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request, Response

from services.gateway.config import EXECUTION_SVC, GARDEN_SVC, IDENTITY_SVC, INTERNAL_SECRET

log = logging.getLogger("gateway.garden_routes")

router = APIRouter(prefix="/api/garden")

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_TIMEOUT = aiohttp.ClientTimeout(total=25)


async def _call(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: Any = None,
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
                content=_json.dumps(body),
                status_code=resp.status,
                media_type="application/json",
            )
        # Pass through client-error and server-error status codes instead of
        # collapsing everything to 502.
        detail = "The garden service did not answer"
        with suppress(Exception):
            detail = (await resp.json(content_type=None)).get("detail") or detail
        return Response(
            content=_json.dumps({"detail": detail}),
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
    """A weather event re-evaluates care: for a trusted service or a parent.

    Anyone could POST this and make the garden re-run its frost/rain pass, so it
    takes the internal secret (weather service, other containers) or an
    authenticated parent/admin -- never an anonymous caller.
    """
    from services.common.secrets import secret_matches

    if secret_matches(request.headers.get("X-Internal-Secret"), INTERNAL_SECRET):
        headers = dict(_HEADERS)
        return await _call("POST", "/hooks/weather", headers=headers)

    ident = await _verified_headers(request)  # 401 for anyone not signed in
    role = str(ident.get("X-User-Role") or "").lower()
    if role not in ("mother", "father", "admin") and ident.get("X-Is-Admin") != "true":
        raise HTTPException(
            status_code=403, detail="Only parents and admins may re-run the garden weather check"
        )
    return await _call("POST", "/hooks/weather", headers={**ident, **dict(_HEADERS)})


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


@router.get("/plants/{plant_id}")
async def garden_get_plant(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", f"/plants/{plant_id}", headers=headers)


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
    # ``mine``/``assigned_to`` are the caller's own filter; dropping them made
    # "Mine" fetch everything and filter in the browser.
    return await _call(
        "GET",
        "/today",
        params={k: v for k, v in request.query_params.items() if k in ("mine", "assigned_to")},
        headers=headers,
    )


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
async def garden_water_all(
    loc_id: str,
    request: Request,
    due_only: bool | None = None,
    source: str | None = None,
):
    """Water a room, forwarding which plants to take and where it came from."""
    headers = await _verified_headers(request)
    params: dict[str, str] = {}
    if due_only is not None:
        params["due_only"] = "true" if due_only else "false"
    if source:
        params["source"] = source
    return await _call(
        "POST", f"/locations/{loc_id}/water-all", params=params or None, headers=headers
    )


@router.get("/plants/{plant_id}/logs")
async def garden_plant_logs(plant_id: str, request: Request, limit: int | None = None):
    """A plant's care history, newest first (``limit`` forwarded when asked)."""
    headers = await _verified_headers(request)
    return await _call(
        "GET", f"/plants/{plant_id}/logs", params={"limit": limit} if limit else None, headers=headers
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


# --------------------------------------------------------------------------- garden planner proxy (Phase 5)


@router.post("/profile/detect-zone")
async def garden_detect_zone(request: Request):
    headers = await _verified_headers(request)
    return await _call("POST", "/profile/detect-zone", headers=headers)


@router.get("/pantry-goals")
async def garden_list_pantry_goals(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/pantry-goals",
        params={k: v for k, v in request.query_params.items() if k in ("season_year",)},
        headers=headers,
    )


@router.post("/pantry-goals")
async def garden_create_pantry_goal(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("POST", "/pantry-goals", json=body, headers=headers)


@router.patch("/pantry-goals/{goal_id}")
async def garden_patch_pantry_goal(goal_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return await _call("PATCH", f"/pantry-goals/{goal_id}", json=body, headers=headers)


@router.delete("/pantry-goals/{goal_id}")
async def garden_delete_pantry_goal(goal_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("DELETE", f"/pantry-goals/{goal_id}", headers=headers)


@router.get("/pantry-goals/calculate")
async def garden_calculate_pantry_goal(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/pantry-goals/calculate",
        params={k: v for k, v in request.query_params.items() if k in ("item", "crop_refs", "target_qty", "unit", "method")},
        headers=headers,
    )


@router.get("/plans")
async def garden_list_plans(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/plans",
        params={k: v for k, v in request.query_params.items() if k in ("status", "season")},
        headers=headers,
    )


@router.post("/plans")
async def garden_create_plan(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("POST", "/plans", json=body, headers=headers)


@router.get("/plans/{plan_id}")
async def garden_get_plan(plan_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", f"/plans/{plan_id}", headers=headers)


@router.patch("/plans/{plan_id}")
async def garden_patch_plan(plan_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return await _call("PATCH", f"/plans/{plan_id}", json=body, headers=headers)


@router.delete("/plans/{plan_id}")
async def garden_delete_plan(plan_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("DELETE", f"/plans/{plan_id}", headers=headers)


@router.get("/plans/{plan_id}/beds/{bed_id}/cells")
async def garden_list_plan_cells(plan_id: str, bed_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", f"/plans/{plan_id}/beds/{bed_id}/cells", headers=headers)


@router.put("/plans/{plan_id}/beds/{bed_id}/cells")
async def garden_upsert_plan_cells(plan_id: str, bed_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("PUT", f"/plans/{plan_id}/beds/{bed_id}/cells", json=body, headers=headers)


@router.post("/plans/{plan_id}/beds/{bed_id}/auto-arrange")
async def garden_auto_arrange(plan_id: str, bed_id: str, request: Request):
    headers = await _verified_headers(request)
    # The body carries the arrangement the person is looking at
    # ({species_refs, apply}); dropping it arranged only what was last saved.
    body = await _json_body(request)
    return await _call(
        "POST",
        f"/plans/{plan_id}/beds/{bed_id}/auto-arrange",
        json=body,
        headers=headers,
    )


@router.get("/plans/{plan_id}/checks")
async def garden_plan_checks(plan_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", f"/plans/{plan_id}/checks", headers=headers)


@router.get("/plans/{plan_id}/timeline")
async def garden_plan_timeline(plan_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", f"/plans/{plan_id}/timeline", headers=headers)


@router.get("/planner/sizing")
async def garden_planner_sizing(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/planner/sizing",
        params={k: v for k, v in request.query_params.items() if k in ("mode", "family_size", "species_refs")},
        headers=headers,
    )


# --------------------------------------------------------------------------- Skylight plant chores
#
# A plant's watering can live on the family's Skylight chore chart. The chore is
# created through Execution (which owns the Skylight session) and linked on the
# plant in Garden; ticking it off there logs the watering back in Garden (see
# ``services/garden/main.py`` POST /hooks/skylight-chore-completed).
#
# Who may do it: parents/admins, or an ``adult_child`` the plant is assigned to
# (they look after it). An adult child may only put the chore on themselves or a
# child/teen -- never on a parent or another adult, and never with admin reach.

_SKYLIGHT_EXCLUDED = frozenset({"default", "anonymous", "household"})
_PARENT_ROLES = frozenset({"mother", "father", "admin"})
_CHILD_ROLES = frozenset({"child", "teen"})
_SKYLIGHT_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _decode(response) -> dict:
    """A proxied Garden answer as a dict (an empty one when there is no body)."""
    try:
        body = _json.loads(getattr(response, "body", b"{}") or b"{}")
    except Exception:
        return {}
    return body if isinstance(body, dict) else {"locations": body} if isinstance(body, list) else {}


async def _garden_json(
    method: str,
    path: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json: Any = None,
) -> tuple[int, Any]:
    """Call Garden and return ``(status, parsed body)`` for logic that needs it.

    Through ``_call`` like every other Garden route, so the timeout, the merged
    headers and any patched client apply here too -- this used to open its own
    session, which meant a test (or a proxy tweak) covered every route but these.
    """
    response = await _call(method, path, params=params, json=json, headers=headers)
    return int(getattr(response, "status_code", 200) or 200), _decode(response)


async def _plant_or_404(plant_id: str, headers: dict) -> dict:
    status, plant = await _garden_json("GET", f"/plants/{plant_id}", headers=headers)
    if status == 404:
        raise HTTPException(status_code=404, detail="Plant not found")
    if status != 200 or not isinstance(plant, dict):
        raise HTTPException(status_code=502, detail="The garden service did not answer")
    return plant


def _same_person(a: str, b: str) -> bool:
    """The chore chart's matching rule: case-insensitive, either name inside the other."""
    x, y = a.strip().lower(), b.strip().lower()
    return bool(x and y) and (x == y or x in y or y in x)


def _may_link_skylight(ident: dict, plant: dict) -> bool:
    role = str(ident.get("family_role") or "").lower()
    if ident.get("is_admin") or role in _PARENT_ROLES:
        return True
    return role == "adult_child" and str(plant.get("assigned_to") or "").lower() == str(ident["user"]).lower()


def _assignees_for(ident: dict, members: list[dict]) -> list[dict]:
    """Who this caller may give the chore to, from the real household roster."""
    role = str(ident.get("family_role") or "").lower()
    parent = bool(ident.get("is_admin")) or role in _PARENT_ROLES
    people = [m for m in members if str(m.get("username") or "").lower() not in _SKYLIGHT_EXCLUDED]
    if parent:
        return people
    me = str(ident["user"]).lower()
    return [
        m
        for m in people
        if str(m.get("username") or "").lower() == me
        or str(m.get("family_role") or "").lower() in _CHILD_ROLES
    ]


def _skylight_member_name(member: dict, skylight_members: list[dict]) -> str | None:
    """The chore chart's label for a household member, if the chart has them."""
    names = [str(member.get("display_name") or ""), str(member.get("username") or "")]
    for candidate in names:
        if not candidate:
            continue
        for chart in skylight_members:
            if _same_person(candidate, str(chart.get("label") or "")):
                return str(chart.get("label"))
    return None



async def _household(request: Request) -> list[dict]:
    """The real household roster, from Identity (the caller's own credentials).

    Failure is loud: a chore assigned to a name we invented would be a chore
    nobody can tick off.
    """
    from services.gateway.main import shared_http_client

    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else {}
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC.rstrip('/')}/api/users/family-directory",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=8.0),
            )
            if resp.status != 200:
                raise ValueError(f"Identity answered {resp.status}")
            members = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed into a default
        raise HTTPException(
            status_code=502,
            detail=f"Could not read the household from Identity ({exc}).",
        ) from exc
    if not isinstance(members, list):
        raise HTTPException(status_code=502, detail="Identity returned no household roster.")
    return [m for m in members if isinstance(m, dict) and m.get("username")]


async def _skylight_chart() -> list[dict] | None:
    """The Skylight chore chart's members, or ``None`` when Skylight is not set up."""
    from services.gateway.main import shared_http_client

    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC.rstrip('/')}/api/integrations/skylight/members",
                headers=_HEADERS,
                timeout=_SKYLIGHT_TIMEOUT,
            )
            data = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001 - a missing chart is a normal, reported state
        return None
    if not isinstance(data, dict) or data.get("status") != "SUCCESS":
        return None
    members = data.get("members")
    return [m for m in members if isinstance(m, dict)] if isinstance(members, list) else None


async def _skylight_state(request: Request, plant_id: str) -> tuple[dict, dict, list[dict], list[dict] | None]:
    """Shared gate for the three routes: (ident, plant, household, chart)."""
    from services.gateway import main as gateway

    ident = await gateway._acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    plant = await _plant_or_404(plant_id, await _verified_headers(request))
    if not _may_link_skylight(ident, plant):
        raise HTTPException(
            status_code=403,
            detail="Only parents, admins, or the adult this plant is assigned to may link its Skylight chore.",
        )
    return ident, plant, await _household(request), await _skylight_chart()


@router.get("/plants/{plant_id}/skylight-options")
async def garden_skylight_options(plant_id: str, request: Request):
    """Who this plant's Skylight chore may be assigned to, and what is linked now."""
    ident, plant, household, chart = await _skylight_state(request, plant_id)
    chart = chart or []
    eligible = []
    for member in _assignees_for(ident, household):
        eligible.append({
            "username": str(member["username"]),
            "display_name": str(member.get("display_name") or member["username"]),
            "family_role": member.get("family_role") or None,
            "skylight_name": _skylight_member_name(member, chart),
            "is_self": str(member["username"]).lower() == str(ident["user"]).lower(),
        })
    return {
        "can_assign_skylight": True,
        "skylight_configured": bool(chart),
        "eligible_assignees": eligible,
        "current_chore_id": plant.get("skylight_chore_id"),
        "current_assignee": plant.get("skylight_assignee"),
        "skylight_sync_enabled": bool(plant.get("skylight_sync_enabled")),
    }



def _watering_repeat(plant: dict) -> str:
    """The chore's repeat, from the plant's watering cadence ("weekly" when 7 days)."""
    species = plant.get("species") or {}
    try:
        days = float(species.get("watering_interval_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return "weekly"
    rounded = max(1, round(days))
    return "weekly" if rounded == 7 else f"FREQ=DAILY;INTERVAL={rounded}"


def _chore_defaults(plant: dict, body: dict) -> dict:
    """The chore body: the person's choices, else the plant's own cadence/cue."""
    title = str(body.get("title") or "").strip()
    if not title:
        name = plant.get("nickname") or (plant.get("species") or {}).get("common_name") or "plant"
        title = f"Water {name}"
    species = plant.get("species") or {}
    out: dict[str, Any] = {
        "title": title[:120],
        "emoji": "🪴",
        "repeat": str(body.get("repeat") or "").strip() or _watering_repeat(plant),
        "note": str(body.get("note") or "").strip() or str(species.get("dryness_cue") or "").replace("_", " "),
    }
    if body.get("time"):
        out["time"] = str(body["time"])
    if body.get("stars") not in (None, ""):
        out["stars"] = str(body["stars"])
    return out


@router.post("/plants/{plant_id}/skylight-chore")
async def garden_create_skylight_chore(plant_id: str, request: Request):
    """Put this plant's watering on the Skylight chore chart, for one person.

    The same eligibility rules as the options route are enforced here (the
    browser is not the authority): an adult child looking after the plant may
    pick themselves or a child/teen, and nothing else.
    """
    from services.gateway.main import shared_http_client

    ident, plant, household, chart = await _skylight_state(request, plant_id)
    body = await _json_body(request)
    assignee = str(body.get("assignee_username") or "").strip().lower()
    if not assignee:
        raise HTTPException(status_code=422, detail="assignee_username is required")

    allowed = {str(m["username"]).lower(): m for m in _assignees_for(ident, household)}
    if assignee not in allowed:
        raise HTTPException(status_code=403, detail="That person cannot be given this chore.")
    if not chart:
        raise HTTPException(
            status_code=400,
            detail="Skylight is not connected, so no chore can be added. Connect it on Devices > Integrations first.",
        )
    member = allowed[assignee]
    skylight_name = _skylight_member_name(member, chart)
    if not skylight_name:
        raise HTTPException(
            status_code=400,
            detail=f"{member.get('display_name') or assignee} is not on the Skylight chore chart; add them there first.",
        )

    spec = {**_chore_defaults(plant, body), "assignees": [skylight_name]}
    try:
        async with shared_http_client() as client:
            resp = await client.post(
                f"{EXECUTION_SVC.rstrip('/')}/api/integrations/skylight/chores",
                json=spec,
                headers=_HEADERS,
                timeout=_SKYLIGHT_TIMEOUT,
            )
            created = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 - reported, never a fake success
        raise HTTPException(status_code=502, detail=f"Skylight did not answer ({exc}).") from exc
    if not isinstance(created, dict) or created.get("status") != "SUCCESS":
        message = (created or {}).get("message") if isinstance(created, dict) else None
        raise HTTPException(status_code=400, detail=message or "Skylight did not accept the chore.")
    chore_ids = created.get("chore_ids") or []
    if not chore_ids:
        raise HTTPException(
            status_code=502,
            detail="Skylight added the chore but returned no id, so it cannot be linked to the plant.",
        )

    status, updated = await _garden_json(
        "PATCH",
        f"/plants/{plant_id}/assign",
        headers=await _verified_headers(request),
        json={
            "skylight_sync_enabled": True,
            "skylight_chore_id": str(chore_ids[0]),
            "skylight_assignee": assignee,
        },
    )
    if status != 200 or not isinstance(updated, dict):
        raise HTTPException(
            status_code=502,
            detail="The Skylight chore was added but could not be saved on the plant; remove it in Skylight if you try again.",
        )
    return {
        "plant": updated,
        "chore": {
            "id": str(chore_ids[0]),
            "assignee": assignee,
            "skylight_name": skylight_name,
            "repeat": spec["repeat"],
            "title": spec["title"],
        },
    }



@router.delete("/plants/{plant_id}/skylight-chore")
async def garden_delete_skylight_chore(plant_id: str, request: Request):
    """Unlink this plant's Skylight chore, removing it there best-effort."""
    from services.gateway.main import shared_http_client

    _, plant, _members, _chart = await _skylight_state(request, plant_id)
    chore_id = plant.get("skylight_chore_id")
    if chore_id:
        try:
            async with shared_http_client() as client:
                await client.delete(
                    f"{EXECUTION_SVC.rstrip('/')}/api/integrations/skylight/chores/{chore_id}",
                    params={"apply_to": "all"},
                    headers=_HEADERS,
                    timeout=_SKYLIGHT_TIMEOUT,
                )
        except Exception:  # noqa: BLE001 - the link is cleared either way
            pass  # the plant must still be unlinked when Skylight is away
    status, updated = await _garden_json(
        "PATCH",
        f"/plants/{plant_id}/assign",
        headers=await _verified_headers(request),
        json={"skylight_sync_enabled": False, "skylight_chore_id": None, "skylight_assignee": None},
    )
    if status != 200 or not isinstance(updated, dict):
        raise HTTPException(status_code=502, detail="Could not unlink the Skylight chore from the plant.")
    return {"plant": updated, "removed_chore_id": chore_id}



# --------------------------------------------------------------------------- harvest, preservation & pantry proxy (Phase 6)


@router.get("/harvest/forecast")
async def garden_harvest_forecast(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/harvest/forecast",
        params={k: v for k, v in request.query_params.items() if k in ("days",)},
        headers=headers,
    )


@router.get("/preservation/advice")
async def garden_preservation_advice(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/preservation/advice",
        params={k: v for k, v in request.query_params.items() if k in ("days",)},
        headers=headers,
    )


@router.get("/pantry-batches")
async def garden_list_pantry_batches(request: Request):
    headers = await _verified_headers(request)
    return await _call(
        "GET",
        "/pantry-batches",
        params={k: v for k, v in request.query_params.items() if k in ("goal_id", "include_empty")},
        headers=headers,
    )


@router.post("/pantry-batches")
async def garden_create_pantry_batch(request: Request):
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call("POST", "/pantry-batches", json=body, headers=headers)


@router.patch("/pantry-batches/{batch_id}")
async def garden_patch_pantry_batch(batch_id: str, request: Request):
    headers = await _verified_headers(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return await _call("PATCH", f"/pantry-batches/{batch_id}", json=body, headers=headers)


@router.post("/pantry-batches/{batch_id}/use")
async def garden_use_pantry_batch(batch_id: str, request: Request):
    # Every family role may take something out of the pantry, children included.
    headers = await _verified_headers(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return await _call("POST", f"/pantry-batches/{batch_id}/use", json=body, headers=headers)


@router.delete("/pantry-batches/{batch_id}")
async def garden_delete_pantry_batch(batch_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("DELETE", f"/pantry-batches/{batch_id}", headers=headers)


# --------------------------------------------------------------------------- assistant: one answer any surface can use (Phase 7)

#: Roles that may add plants from a photo (the same rule as adding one in the UI).
_ADULT_ROLES = frozenset({"adult_child", "mother", "father", "admin"})


async def _acting(request: Request) -> dict:
    """The verified caller, with the credentials their own uploads will use.

    The photo store needs the caller's Nextcloud account (``nextcloud_url``,
    ``nextcloud_user``, ``nextcloud_pass``), so this is the full resolved
    identity -- the same one the recipe photo import uses -- not the trimmed
    ``{user, is_admin, family_role}``. Anyone not signed in is a 401.
    """
    from services.gateway import main as gateway

    await _verified_headers(request)  # 401 for anyone not signed in
    # `_acting_identity` is the authority on *who* (and their role); the resolved
    # credentials add the Nextcloud account the photo is stored in.
    trimmed = dict(await gateway._acting_identity(request) or {})
    try:
        full = gateway._identity_cred_dict(await gateway._resolve_acting_identity(request))
    except Exception as exc:  # an identity service hiccup must not lose the caller
        log.info("[garden] full identity unavailable: %s", exc)
        full = {}
    return {**full, **trimmed}


@router.get("/assistant/summary")
async def garden_assistant_summary(request: Request):
    """Today's care, harvest-ready plants, pantry progress -- one grounded answer."""
    from services.gateway import garden_assist

    creds = await _acting(request)
    return await garden_assist.summarize(creds)


@router.post("/assistant/log-care")
async def garden_assistant_log_care(request: Request):
    """Log care for whatever the body names (a plant, a species, or a room)."""
    from services.gateway import garden_assist

    creds = await _acting(request)
    body = await request.json()
    target = str(body.get("target") or "").strip()
    if not target:
        raise HTTPException(status_code=422, detail="Name a plant, a species or a room.")
    return await garden_assist.log_care(
        creds,
        target,
        action=str(body.get("action") or "water"),
        still_damp=bool(body.get("still_damp")),
        source=str(body.get("source") or "voice"),
        postpone_days=body.get("postpone_days"),
    )


@router.post("/assistant/log-harvest")
async def garden_assistant_log_harvest(request: Request):
    """Log a harvest against whatever the body names."""
    from services.gateway import garden_assist

    creds = await _acting(request)
    body = await request.json()
    target = str(body.get("target") or "").strip()
    try:
        quantity = float(body.get("quantity"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="A harvest needs a quantity.") from None
    if not target:
        raise HTTPException(status_code=422, detail="Name the plant you picked.")
    return await garden_assist.log_harvest(
        creds,
        target,
        quantity=quantity,
        unit=str(body.get("unit") or "lb"),
        disposition=str(body.get("disposition") or "fresh"),
        notes=body.get("notes"),
    )


@router.get("/assistant/plan-context")
async def garden_assistant_plan_context(request: Request):
    """The real yard and family the planner should reason from."""
    headers = await _verified_headers(request)
    profile = await _call("GET", "/profile", headers=headers)
    locations = await _call("GET", "/locations", headers=headers)
    goals = await _call("GET", "/pantry-goals", headers=headers)
    plans = await _call("GET", "/plans", headers=headers)

    def _json_body(response, *, key: str | None = None):
        """A Garden answer, whether it is the target shape or wrapped in ``key``.

        ``GET /locations`` answers a bare list; the other three answer objects
        that carry their own key. Reading only one of those was a 500.
        """
        try:
            body = _json.loads(getattr(response, "body", b"{}") or b"{}")
        except Exception:
            return [] if key else {}
        if isinstance(body, list):
            return body if key else {"profile": {}}
        if not isinstance(body, dict):
            return [] if key else {}
        if key is None:
            return body
        value = body.get(key)
        return value if isinstance(value, list) else []

    beds = [
        loc for loc in _json_body(locations, key="locations")
        if isinstance(loc, dict) and loc.get("kind") not in ("indoor_room",)
    ]
    return {
        "profile": _json_body(profile),
        "beds": beds,
        "pantry_goals": _json_body(goals, key="goals"),
        "plans": _json_body(plans, key="plans"),
    }


# --------------------------------------------------------------------------- Alpaca Vision: Plant ID, Plant Doctor, journal


async def _garden_json_body(
    method: str, path: str, *, headers: dict, params: dict | None = None, json: Any = None
) -> dict:
    """A Garden answer as just its body, for routes that need nothing else."""
    _, body = await _garden_json(method, path, headers=headers, params=params, json=json)
    return body


def _detail_of(body: dict, fallback: str) -> str:
    """A Garden refusal's message, so the caller hears the real reason."""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict) and first.get("msg"):
            return str(first["msg"])
    return fallback


async def _read_upload(request: Request, field: str = "image") -> bytes:
    """The uploaded photo, refusing the two cases that cannot be vision'd.

    Duck-typed on purpose: Starlette's form parser returns its own ``UploadFile``,
    which is not an instance of ``fastapi.UploadFile`` on every version.
    """
    form = await request.form()
    upload = form.get(field)
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=422, detail=f"Attach the photo as '{field}'")
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=422, detail="The photo is empty")
    return raw


@router.post("/vision/identify")
async def garden_vision_identify(request: Request):
    """Snap-to-add: identify a plant (or seed packet) and offer catalog matches."""
    from services.gateway import garden_vision

    ident = await _acting(request)
    role = str(ident.get("family_role") or "").lower()
    if not (ident.get("is_admin") or role in _ADULT_ROLES):
        raise HTTPException(status_code=403, detail="Adults and admins only: add a plant from a photo.")
    raw = await _read_upload(request)
    headers = await _verified_headers(request)

    async def search(query: str, category: str | None) -> dict:
        params: dict = {"q": query, "limit": 5}
        if category:
            params["category"] = category
        return await _garden_json_body("GET", "/species/search", headers=headers, params=params)

    try:
        return await garden_vision.identify(raw, creds=ident, search=search)
    except garden_vision.VisionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except garden_vision.VisionFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/plants/{plant_id}/diagnose")
async def garden_diagnose_plant(plant_id: str, request: Request):
    """Plant Doctor: a diagnosis from a leaf photo plus this plant's own history.

    The diagnosis is saved as a ``diagnosis`` photo on the plant's timeline, so
    the answer is still there next week (with the photo, when Nextcloud is set).
    """
    from services.gateway import garden_vision

    creds = await _acting(request)
    raw = await _read_upload(request)
    headers = await _verified_headers(request)
    form = await request.form()
    symptoms = form.get("symptoms")

    plant_status, plant = await _garden_json("GET", f"/plants/{plant_id}", headers=headers)
    if plant_status == 404 or not plant or plant.get("detail"):
        raise HTTPException(status_code=404, detail="Plant not found")
    _, logs = await _garden_json("GET", f"/plants/{plant_id}/logs", headers=headers, params={"limit": 10})
    _, today = await _garden_json("GET", "/today", headers=headers)

    try:
        diagnosis = await garden_vision.diagnose(
            raw,
            creds=creds,
            plant=plant,
            logs=logs.get("logs") or [],
            weather=today.get("weather"),
            symptoms=str(symptoms) if symptoms else None,
        )
    except garden_vision.VisionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except garden_vision.VisionFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    stored = await garden_vision.upload_photo(raw, creds=creds, plant_id=plant_id)
    photo_status, photo = await _garden_json(
        "POST",
        f"/plants/{plant_id}/photos",
        headers=headers,
        json={
            "kind": "diagnosis",
            "notes": str(symptoms) if symptoms else None,
            "nextcloud_path": stored.get("nextcloud_path"),
            "thumb_base64": stored.get("thumb_base64"),
            "ai_summary_json": garden_vision.as_summary_json(diagnosis),
        },
    )
    if photo_status >= 400:
        raise HTTPException(status_code=photo_status, detail=_detail_of(photo, "The diagnosis could not be saved"))
    return {
        "diagnosis": diagnosis,
        "photo": photo,
        "nextcloud_warning": stored.get("warning"),
    }


@router.post("/plants/{plant_id}/photos", status_code=201)
async def garden_add_plant_photo(plant_id: str, request: Request):
    """Add a growth-journal photo (every family role; it is the family's journal)."""
    from services.gateway import garden_vision

    creds = await _acting(request)
    raw = await _read_upload(request)
    headers = await _verified_headers(request)
    form = await request.form()
    kind = str(form.get("kind") or "progress")

    # A photo for a plant that does not exist must not reach Nextcloud: the
    # upload happens first otherwise, and the file is orphaned.
    plant_status, _plant = await _garden_json("GET", f"/plants/{plant_id}", headers=headers)
    if plant_status == 404:
        raise HTTPException(status_code=404, detail="Plant not found")
    notes = form.get("notes")

    stored = await garden_vision.upload_photo(raw, creds=creds, plant_id=plant_id)
    photo_status, photo = await _garden_json(
        "POST",
        f"/plants/{plant_id}/photos",
        headers=headers,
        json={
            "kind": kind,
            "notes": str(notes) if notes else None,
            "nextcloud_path": stored.get("nextcloud_path"),
            "thumb_base64": stored.get("thumb_base64"),
        },
    )
    if photo_status >= 400:
        raise HTTPException(status_code=photo_status, detail=_detail_of(photo, "The photo could not be saved"))
    return {"photo": photo, "nextcloud_warning": stored.get("warning")}


@router.get("/plants/{plant_id}/photos")
async def garden_list_plant_photos(plant_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("GET", f"/plants/{plant_id}/photos", headers=headers)


@router.get("/plants/{plant_id}/photos/{photo_id}/thumb")
async def garden_plant_photo_thumb(plant_id: str, photo_id: str, request: Request):
    """The local thumbnail, streamed straight through (the UI loads these fast).

    A browser ``<img>`` cannot send a Bearer header, so this also accepts the
    signed media token the rest of the app uses for images (``?user=&mt=``) --
    and, like those, a bad token is a 403 rather than a silent fallback.
    """
    from services.gateway import main as gateway
    from services.gateway.config import GARDEN_SVC
    from services.gateway.main import shared_http_client

    media_ident = await gateway._resolve_identity_from_media_token(request)
    headers = await _verified_headers(request) if media_ident is None else {
        "X-User-Id": str(media_ident.get("user") if isinstance(media_ident, dict) else ""),
    }
    merged = dict(_HEADERS)
    merged.update(headers)
    async with shared_http_client() as client, client.get(
        f"{GARDEN_SVC.rstrip('/')}/plants/{plant_id}/photos/{photo_id}/thumb",
        headers=merged,
        timeout=_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=404, detail="No thumbnail for this photo")
        return Response(
            content=await resp.read(),
            media_type=resp.headers.get("Content-Type") or "image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )


@router.delete("/plants/{plant_id}/photos/{photo_id}")
async def garden_delete_plant_photo(plant_id: str, photo_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("DELETE", f"/plants/{plant_id}/photos/{photo_id}", headers=headers)


# --------------------------------------------------------------------------- Home Hub hardware (Phase 8)


@router.get("/hardware/discover")
async def garden_hardware_discover(request: Request, fresh: bool = False):
    """What garden hardware Home Hub has (drives whether the UI shows any)."""
    headers = await _verified_headers(request)
    return await _call(
        "GET", "/hardware/discover", params={"fresh": "true" if fresh else "false"}, headers=headers
    )


@router.get("/bindings")
async def garden_list_bindings(
    request: Request,
    plant_id: str | None = None,
    location_id: str | None = None,
):
    """Bindings for a plant, a bed, or the whole house, with live readings."""
    headers = await _verified_headers(request)
    params = {key: value for key, value in (("plant_id", plant_id), ("location_id", location_id)) if value}
    return await _call("GET", "/bindings", params=params, headers=headers)


@router.put("/bindings/{target_kind}/{target_id}")
async def garden_upsert_binding(target_kind: str, target_id: str, request: Request):
    """Create or update a plant's or bed's hardware binding (parents/admins)."""
    headers = await _verified_headers(request)
    body = await request.json()
    return await _call(
        "PUT", f"/bindings/{target_kind}/{target_id}", json=body, headers=headers
    )


@router.delete("/bindings/{target_kind}/{target_id}")
async def garden_delete_binding(target_kind: str, target_id: str, request: Request):
    headers = await _verified_headers(request)
    return await _call("DELETE", f"/bindings/{target_kind}/{target_id}", headers=headers)


@router.post("/bindings/{binding_id}/valve/run")
async def garden_run_valve(binding_id: str, request: Request):
    """Open a guarded irrigation valve (parents/admins)."""
    headers = await _verified_headers(request)
    body = await request.json() if await request.body() else {}
    return await _call("POST", f"/bindings/{binding_id}/valve/run", json=body, headers=headers)


@router.post("/bindings/{binding_id}/valve/stop")
async def garden_stop_valve(binding_id: str, request: Request):
    """Close an irrigation valve now (parents/admins)."""
    headers = await _verified_headers(request)
    return await _call("POST", f"/bindings/{binding_id}/valve/stop", headers=headers)


@router.get("/tags/{tag_id}")
async def garden_scan_tag(tag_id: str, request: Request):
    """Scan an NFC/QR tag: which plant or bed, and what is owed (every role)."""
    headers = await _verified_headers(request)
    return await _call("GET", f"/tags/{tag_id}", headers=headers)
