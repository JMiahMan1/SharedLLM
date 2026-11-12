"""Rooms, Energy, History and Climate pages: homehub's dashboard, history and statistics APIs.

Reads go to the hub at the caller's own Home Assistant URL with their own HA
token (as the Cameras page does); neither reaches the browser. What a caller
sees follows the same device permissions as every other HA surface
(``services.execution.entity_access``): an entity they may not control is left
out of rooms, thermostats, history and statistics as if it did not exist.
Whole-house totals (the grid meter, the utility's readings) are household
figures and are shown to every signed-in member; a per-circuit row is shown
only when its sensor is visible to the caller.

Changing the energy or dashboard settings is for admins and goes to the hub's
``/hub/*`` admin API with the stack's internal secret (as the New devices
panel does). Controls are not here: the pages call ``/execute/ha_service``,
which enforces the same permissions.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.execution.entity_access import can_view, load_entity_permissions
from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.dashboards")

router = APIRouter(prefix="/api/dashboard")

HOMEHUB_URL = os.getenv("HOMEHUB_URL", "http://host.docker.internal:8123").rstrip("/")
_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=5)
#: Importing years of HA statistics takes a while.
_IMPORT_TIMEOUT = aiohttp.ClientTimeout(total=900, connect=5)
_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_STATISTIC_ID = re.compile(r"^([a-z_]+\.[a-z0-9_]+|[a-z0-9_]+:[a-z0-9_]+)$")
MAX_HISTORY_ENTITIES = 12
MAX_HISTORY_DAYS = 31
_PASS_THROUGH = {200, 400, 404, 409, 503}


async def _caller(request: Request) -> dict[str, Any]:
    from services.gateway import main as gateway

    creds = gateway._identity_cred_dict(await gateway._resolve_identity_from_request(request))
    if not creds.get("user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return creds


async def _admin(request: Request) -> str:
    from services.gateway import main as gateway

    return await gateway._require_admin(request)


async def _permissions(creds: dict[str, Any]):
    return await load_entity_permissions(str(creds.get("user") or ""), bool(creds.get("is_admin")))


async def _hub_get(creds: dict[str, Any], path: str, params: dict[str, str] | None = None) -> Any:
    """A read from the hub with the caller's own HA credentials."""
    from services.gateway.main import shared_http_client

    base = str(creds.get("ha_url") or "").rstrip("/")
    token = str(creds.get("ha_token") or "")
    if not base or not token:
        raise HTTPException(status_code=503, detail="Home Assistant is not set up for your account")
    try:
        async with shared_http_client() as client, client.get(
            f"{base}{path}", params=params or None, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT
        ) as resp:
            body = await resp.json(content_type=None) if resp.status == 200 or resp.status in (400, 404, 503) else None
            status = resp.status
    except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
        log.warning("[dashboards] hub %s: %s", path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The home hub did not answer") from exc
    if status == 200:
        return body
    if status in (400, 404, 503):
        message = str(body.get("message") or body.get("detail") or "") if isinstance(body, dict) else ""
        raise HTTPException(status_code=status, detail=message or "The home hub refused that")
    log.warning("[dashboards] hub %s answered HTTP %s", path, status)
    raise HTTPException(status_code=502, detail="The home hub did not answer")


async def _hub_admin(method: str, path: str, *, json: Any = None,
                     timeout: aiohttp.ClientTimeout = _TIMEOUT) -> Any:
    """An admin call to the hub with the internal secret; the hub's own words pass through."""
    from services.gateway.main import shared_http_client

    try:
        async with shared_http_client() as client, client.request(
            method, f"{HOMEHUB_URL}{path}", json=json, headers={"Authorization": f"Bearer {INTERNAL_SECRET}"},
            timeout=timeout,
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            status = resp.status
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[dashboards] hub %s %s: %s", method, path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The home hub did not answer") from exc
    if status == 200:
        return body
    if status in _PASS_THROUGH and isinstance(body, dict):
        message = str(body.get("message") or body.get("detail") or "")
        return JSONResponse({**body, "detail": message} if message else body, status_code=status)
    log.warning("[dashboards] hub %s answered HTTP %s", path, status)
    raise HTTPException(status_code=502, detail="The home hub did not answer")


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The body must be a JSON object")
    return body


def _visible(perms: Any, entity: Any) -> bool:
    return isinstance(entity, dict) and can_view(perms, str(entity.get("entity_id") or ""))


def _statistic_visible(perms: Any, statistic_id: str) -> bool:
    """External statistics (``source:id``) are household meters; entity statistics follow the entity."""
    return ":" in statistic_id or can_view(perms, statistic_id)


def _ids(raw: str | None, pattern: re.Pattern[str]) -> list[str]:
    ids = [x.strip() for x in (raw or "").split(",") if x.strip()]
    bad = [x for x in ids if not pattern.match(x)]
    if bad:
        raise HTTPException(status_code=400, detail=f"Not an id: {bad[0][:80]}")
    return list(dict.fromkeys(ids))


def _time(raw: str | None, default: datetime) -> datetime:
    if not raw:
        return default
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Times are ISO 8601") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# -- rooms, climate, entities ---------------------------------------------------------------------
@router.get("/rooms")
async def rooms(request: Request):
    """Every area with the devices in it the caller may use; areas left empty by permissions are dropped."""
    creds = await _caller(request)
    data = await _hub_get(creds, "/api/homehub/dashboard/rooms")
    perms = await _permissions(creds)
    areas = []
    for area in data.get("areas") or []:
        visible = [e for e in area.get("entities") or [] if _visible(perms, e)]
        if visible:
            areas.append({**area, "entities": visible})
    return {"floors": data.get("floors") or [], "areas": areas,
            "unassigned": [e for e in data.get("unassigned") or [] if _visible(perms, e)]}


@router.get("/climate")
async def climate(request: Request):
    """Thermostats with their area's sensors and the power circuits that belong with them."""
    creds = await _caller(request)
    data = await _hub_get(creds, "/api/homehub/dashboard/climate")
    perms = await _permissions(creds)
    out = []
    for t in data.get("thermostats") or []:
        if not _visible(perms, t):
            continue
        out.append({**t, "power": [p for p in t.get("power") or [] if _visible(perms, p)],
                    "sensors": [s for s in t.get("sensors") or [] if _visible(perms, s)]})
    return {"thermostats": out}


@router.get("/entities")
async def entities(request: Request, domains: str | None = None):
    """Entities the caller may see, for the History page's picker."""
    creds = await _caller(request)
    wanted = {d.strip() for d in (domains or "").split(",") if d.strip()}
    states = await _hub_get(creds, "/api/states")
    perms = await _permissions(creds)
    out = []
    for st in states if isinstance(states, list) else []:
        eid = str(st.get("entity_id") or "")
        if (wanted and eid.split(".", 1)[0] not in wanted) or not can_view(perms, eid):
            continue
        attrs = st.get("attributes") or {}
        out.append({"entity_id": eid, "name": attrs.get("friendly_name") or eid, "domain": eid.split(".", 1)[0],
                    "unit": attrs.get("unit_of_measurement"), "device_class": attrs.get("device_class"),
                    "state": st.get("state")})
    out.sort(key=lambda e: (e["domain"], str(e["name"]).lower()))
    return {"entities": out}


@router.get("/history")
async def history(request: Request, entity_ids: str | None = None, start: str | None = None,
                  end: str | None = None, attributes: bool = False):
    """State history of up to 12 entities over at most 31 days: ``{series: {entity_id: [{t, s, a?}]}}``."""
    ids = _ids(entity_ids, _ENTITY_ID)
    if not ids:
        raise HTTPException(status_code=400, detail="Choose at least one entity")
    if len(ids) > MAX_HISTORY_ENTITIES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_HISTORY_ENTITIES} entities at a time")
    now = datetime.now(UTC)
    end_dt = _time(end, now)
    start_dt = _time(start, end_dt - timedelta(days=1))
    if end_dt - start_dt > timedelta(days=MAX_HISTORY_DAYS):
        raise HTTPException(status_code=400, detail=f"At most {MAX_HISTORY_DAYS} days at a time")
    creds = await _caller(request)
    perms = await _permissions(creds)
    ids = [i for i in ids if can_view(perms, i)]
    if not ids:
        return {"series": {}}
    params = {"filter_entity_id": ",".join(ids), "end_time": end_dt.isoformat()}
    if not attributes:
        params["no_attributes"] = "1"
    data = await _hub_get(creds, f"/api/history/period/{start_dt.isoformat()}", params)
    series: dict[str, list[dict[str, Any]]] = {}
    for rows in data if isinstance(data, list) else []:
        if not rows:
            continue
        eid = rows[0].get("entity_id")
        if eid not in ids:
            continue
        series[eid] = [{"t": r.get("last_changed") or r.get("last_updated"), "s": r.get("state"),
                        **({"a": r.get("attributes") or {}} if attributes else {})} for r in rows]
    return {"series": series}


@router.get("/statistics")
async def statistics(request: Request, statistic_ids: str | None = None, start: str | None = None,
                     end: str | None = None, period: str = "hour", types: str | None = None,
                     units: str | None = None):
    """Long-term statistics (HA's recorder shape) for statistics the caller may see."""
    ids = _ids(statistic_ids, _STATISTIC_ID)
    if not ids:
        raise HTTPException(status_code=400, detail="statistic_ids is required")
    creds = await _caller(request)
    perms = await _permissions(creds)
    ids = [i for i in ids if _statistic_visible(perms, i)]
    if not ids:
        return {}
    params = {"statistic_ids": ",".join(ids), "period": period}
    for key, value in (("start", start), ("end", end), ("types", types), ("units", units)):
        if value:
            params[key] = value
    return await _hub_get(creds, "/api/homehub/statistics", params)


@router.get("/energy/summary")
async def energy_summary(request: Request, range: str | None = None, date: str | None = None,
                         start: str | None = None, end: str | None = None, period: str | None = None):
    """The Energy page: usage, per-circuit breakdown, time of use, cost and the utility's readings."""
    creds = await _caller(request)
    params = {k: v for k, v in (("range", range), ("date", date), ("start", start), ("end", end),
                                ("period", period)) if v}
    data = await _hub_get(creds, "/api/homehub/energy/summary", params)
    perms = await _permissions(creds)
    if isinstance(data, dict):
        devices = [d for d in data.get("devices") or [] if _statistic_visible(perms, str(d.get("stat") or ""))]
        for d in devices:
            if d.get("power_entity") and not can_view(perms, d["power_entity"]):
                d["power_entity"], d["power_w"] = None, None
        data["devices"] = devices
    return data


@router.get("/prefs")
async def dashboard_prefs(request: Request):
    creds = await _caller(request)
    return await _hub_get(creds, "/api/homehub/dashboard/prefs")


# -- admin -------------------------------------------------------------------------------------------
@router.put("/prefs")
async def put_dashboard_prefs(request: Request):
    """Thermostat-to-circuit pairings and hidden entities: ``{"climate_power"?, "hidden"?}``."""
    await _admin(request)
    return await _hub_admin("PUT", "/hub/dashboard/prefs", json=await _json(request))


@router.get("/energy/prefs")
async def energy_prefs(request: Request):
    await _admin(request)
    return await _hub_admin("GET", "/api/homehub/energy/prefs")


@router.put("/energy/prefs")
async def put_energy_prefs(request: Request):
    """Grid and solar sources, devices, utility meters and the tariff (partial updates allowed)."""
    await _admin(request)
    return await _hub_admin("PUT", "/hub/energy/prefs", json=await _json(request))


@router.get("/energy/candidates")
async def energy_candidates(request: Request):
    await _admin(request)
    return await _hub_admin("GET", "/api/homehub/energy/candidates")


@router.post("/energy/tariff/suggest")
async def suggest_tariff(request: Request):
    """Time-of-use periods read off the utility's own on-peak readings, for the admin to review."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("on_peak_stat", "total_stat", "days") if body.get(k)}
    return await _hub_admin("POST", "/hub/energy/tariff/suggest", json=payload)


@router.post("/energy/import")
async def import_energy(request: Request):
    """Home Assistant's energy settings (``.storage/energy``) from the hub's import folder."""
    await _admin(request)
    return await _hub_admin("POST", "/hub/energy/import", json={})


@router.post("/statistics/import")
async def import_statistics(request: Request):
    """Home Assistant's long-term statistics and energy settings (repeatable; takes minutes)."""
    await _admin(request)
    return await _hub_admin("POST", "/hub/statistics/import", json={}, timeout=_IMPORT_TIMEOUT)


@router.get("/statistics/status")
async def statistics_status(request: Request):
    await _admin(request)
    return await _hub_admin("GET", "/hub/statistics/status")
