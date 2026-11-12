"""Statistics and energy: HA's recorder WebSocket commands plus REST routes for Jarvis's dashboards.

WebSocket (``WS_COMMANDS``, dispatched by api.py's handler):

| Command | What |
|---|---|
| ``recorder/statistics_during_period`` | ``start_time``, ``end_time``?, ``statistic_ids``, ``period``, ``types``?, ``units``? |
| ``recorder/list_statistic_ids`` | ``statistic_type``? (``mean``/``sum``) |
| ``recorder/get_statistics_metadata`` | ``statistic_ids``? |
| ``energy/get_prefs`` / ``energy/save_prefs`` | HA's energy preferences (saving is admin only) |

REST, any hub token:

| Route | What |
|---|---|
| ``GET /api/homehub/statistics`` | ``statistic_ids``, ``start``, ``end``?, ``period``?, ``types``?, ``units`` (``energy:kWh,power:W``) |
| ``GET /api/homehub/statistics/ids`` | ``statistic_type``? |
| ``GET /api/homehub/energy/prefs`` | the energy configuration |
| ``GET /api/homehub/energy/summary`` | ``range`` (day/week/month/year) + ``date``, or ``start``/``end``/``period`` |
| ``GET /api/homehub/energy/candidates`` | statistics an admin can choose as sources |

Admin (``/hub/*``):

| Route | What |
|---|---|
| ``GET /hub/statistics/status`` | row counts, time span, compiler state, last import |
| ``POST /hub/statistics/import`` | ``{"path"?, "energy"?: true}`` HA's recorder statistics (+ ``.storage/energy``), repeatable |
| ``PUT /hub/energy/prefs`` | change the energy configuration (partial updates allowed) |
| ``POST /hub/energy/import`` | ``{"path"?}`` HA's ``.storage/energy`` only |
| ``POST /hub/energy/tariff/suggest`` | ``{"on_peak_stat"?, "total_stat"?, "days"?}`` time-of-use periods read off a utility's data |
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from os.path import commonpath
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub import energy
from services.homehub.auth import Auth, Principal, bearer
from services.homehub.core import HubError
from services.homehub.recorder_statistics import PERIODS, Statistics, parse_dt

log = logging.getLogger("homehub.statistics_api")

IMPORT_DIR = os.getenv("HOMEHUB_IMPORT_DIR", "/import")


def _stats(runtime: Any) -> Statistics:
    stats = getattr(runtime, "statistics", None)
    if stats is None:
        raise HubError("statistics are not running")
    return stats


def _ids(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [str(x) for x in raw]


def _units(raw: Any) -> dict[str, str] | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out = {}
    for part in str(raw).split(","):
        k, _, v = part.partition(":")
        if k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def _import_path(path: str | None) -> str:
    src = path or os.path.join(IMPORT_DIR, "homeassistant")
    if not os.path.realpath(src).startswith(os.path.realpath(IMPORT_DIR)):
        raise HTTPException(400, detail=f"imports are read from {IMPORT_DIR} only")
    return src


def related_power(hub: Any, statistic_ids: list[str]) -> dict[str, str | None]:
    """For each energy sensor, a power sensor on the same device (what is drawing now), if there is one."""
    out: dict[str, str | None] = {}
    for sid in statistic_ids:
        ent = hub.entities.get(sid)
        found = None
        if ent is not None and ent.device_id:
            candidates = []
            for other in hub.entities.values():
                if other.device_id != ent.device_id or other.entity_id == sid or other.disabled_by:
                    continue
                st = hub.states.get(other.entity_id)
                dc = (st.attributes.get("device_class") if st else None) or other.device_class \
                    or other.trusted("original_device_class")
                if other.domain == "sensor" and dc == "power":
                    candidates.append(other.entity_id)
            # A meter with several circuits on one device: the power sensor named most like the energy one.
            found = max(candidates, key=lambda e: (len(commonpath([e, sid])), e), default=None)
        out[sid] = found
    return out


def power_now(hub: Any, entity_ids: list[str | None]) -> dict[str, float | None]:
    """Current draw in W for each power sensor (converted from kW and friends)."""
    from services.homehub.recorder_statistics import convert, convertible

    out: dict[str, float | None] = {}
    for eid in entity_ids:
        if not eid:
            continue
        st = hub.states.get(eid)
        try:
            v = float(st.state) if st else None
        except (TypeError, ValueError):
            v = None
        unit = st.attributes.get("unit_of_measurement") if st else None
        out[eid] = round(convert(v, unit, "W"), 1) if v is not None and convertible(unit, "W") else v
    return out


def energy_summary(runtime: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The energy page's numbers for ``range``+``date`` or ``start``/``end``/``period`` (blocking)."""
    hub = runtime.hub
    stats = _stats(runtime)
    prefs = energy.load_prefs(runtime.store)
    if params.get("start"):
        start = parse_dt(params["start"])
        end = parse_dt(params.get("end")) or datetime.now(UTC)
        period = params.get("period") or "day"
        if start is None or period not in PERIODS:
            raise energy.PrefsError("start must be an ISO time and period one of " + ", ".join(PERIODS))
    else:
        start, end, period = energy.range_for(params.get("range") or "day", energy.parse_when(params.get("date")),
                                              stats.tz)
    dev_ids = [d["stat_consumption"] for d in prefs.get("device_consumption") or []]
    rel = related_power(hub, dev_ids)
    out = energy.summary(stats, prefs, start, end, period, related_power=rel,
                         power_now=power_now(hub, [d.get("stat_rate") or rel.get(d["stat_consumption"])
                                                   for d in prefs.get("device_consumption") or []]))
    for d in out["devices"]:
        st = hub.states.get(d["stat"])
        if d["name"] == d["stat"] and st is not None:
            d["name"] = st.name
    grid_power = []
    for sid in out["grid"]["stats"]:
        grid_power.extend(v for v in related_power(hub, [sid]).values() if v)
    now = power_now(hub, grid_power)
    out["grid"]["power_entities"] = grid_power
    out["grid"]["power_w"] = round(sum(v for v in now.values() if v is not None), 1) if now else None
    out["range"] = params.get("range")
    out["time_zone"] = str(stats.tz)
    return out


# -- WebSocket commands ------------------------------------------------------------------
async def _ws_during_period(conn: Any, msg: dict[str, Any]) -> Any:
    stats = _stats(conn.runtime)
    start = parse_dt(msg.get("start_time"))
    if start is None:
        raise HubError("start_time is required")
    period = msg.get("period") or "hour"
    if period not in PERIODS:
        raise HubError(f"period must be one of {', '.join(PERIODS)}")
    return await asyncio.to_thread(stats.during_period, start, parse_dt(msg.get("end_time")),
                                   _ids(msg.get("statistic_ids")), period, msg.get("types"), _units(msg.get("units")))


async def _ws_list_ids(conn: Any, msg: dict[str, Any]) -> Any:
    return await asyncio.to_thread(_stats(conn.runtime).list_statistic_ids, msg.get("statistic_type"))


async def _ws_metadata(conn: Any, msg: dict[str, Any]) -> Any:
    return await asyncio.to_thread(_stats(conn.runtime).list_statistic_ids, None, _ids(msg.get("statistic_ids")))


async def _ws_get_prefs(conn: Any, msg: dict[str, Any]) -> Any:
    return energy.ha_shape(energy.load_prefs(conn.runtime.store))


async def _ws_save_prefs(conn: Any, msg: dict[str, Any]) -> Any:
    if conn.principal is None or not conn.principal.is_admin:
        raise HubError("Unauthorized")
    body = {k: v for k, v in msg.items() if k not in ("id", "type")}
    try:
        return energy.save_prefs(conn.runtime.store, body)
    except energy.PrefsError as exc:
        raise HubError(str(exc)) from exc


WS_COMMANDS: dict[str, Callable[[Any, dict[str, Any]], Awaitable[Any]]] = {
    "recorder/statistics_during_period": _ws_during_period,
    "recorder/list_statistic_ids": _ws_list_ids,
    "recorder/get_statistics_metadata": _ws_metadata,
    "energy/get_prefs": _ws_get_prefs,
    "energy/save_prefs": _ws_save_prefs,
}


def register_ws_commands() -> None:
    """Add the commands to api.py's WebSocket registry (``async fn(conn, id, msg)``, the handler answers)."""
    from services.homehub import api

    def wrap(fn: Callable[[Any, dict[str, Any]], Awaitable[Any]]) -> Callable[[Any, Any, dict[str, Any]], Awaitable[None]]:
        async def handler(conn: Any, mid: Any, msg: dict[str, Any]) -> None:
            await conn.result(mid, await fn(conn, msg))
        return handler

    for kind, fn in WS_COMMANDS.items():
        api.WS_COMMANDS[kind] = wrap(fn)


# -- REST ----------------------------------------------------------------------------------
def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    register_ws_commands()

    def principal(request: Request) -> Principal:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def admin(request: Request) -> Principal:
        p = principal(request)
        if not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def stats() -> Statistics:
        s = getattr(runtime, "statistics", None)
        if s is None:
            raise HTTPException(503, detail="Statistics are not running")
        return s

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    @router.get("/api/homehub/statistics")
    async def statistics(request: Request) -> Any:
        principal(request)
        q = request.query_params
        start = parse_dt(q.get("start")) or datetime.now(UTC) - timedelta(days=1)
        period = q.get("period") or "hour"
        if period not in PERIODS:
            raise HTTPException(400, detail=f"period must be one of {', '.join(PERIODS)}")
        ids = _ids(q.get("statistic_ids"))
        if not ids:
            raise HTTPException(400, detail="statistic_ids is required")
        types = _ids(q.get("types"))
        return await asyncio.to_thread(stats().during_period, start, parse_dt(q.get("end")), ids, period, types,
                                       _units(q.get("units")))

    @router.get("/api/homehub/statistics/ids")
    async def statistic_ids(request: Request, statistic_type: str | None = None) -> Any:
        principal(request)
        return await asyncio.to_thread(stats().list_statistic_ids, statistic_type)

    @router.get("/api/homehub/energy/prefs")
    async def get_prefs(request: Request) -> Any:
        principal(request)
        return energy.load_prefs(runtime.store)

    @router.get("/api/homehub/energy/summary")
    async def summary(request: Request) -> Any:
        principal(request)
        stats()
        try:
            return await asyncio.to_thread(energy_summary, runtime, dict(request.query_params))
        except energy.PrefsError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @router.get("/api/homehub/energy/candidates")
    async def candidates(request: Request) -> Any:
        principal(request)
        found = await asyncio.to_thread(energy.utility_candidates, stats())
        for c in found:
            st = runtime.hub.states.get(c["stat_energy"])
            if st is not None and not c.get("name"):
                c["name"] = st.name
        return found

    @router.put("/hub/energy/prefs")
    async def put_prefs(request: Request) -> Any:
        admin(request)
        try:
            return energy.save_prefs(runtime.store, await body_of(request))
        except energy.PrefsError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @router.post("/hub/energy/import")
    async def import_energy(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        src = _import_path(body.get("path"))
        try:
            return await asyncio.to_thread(energy.import_ha_prefs, runtime.store, src,
                                           getattr(runtime, "statistics", None))
        except FileNotFoundError as exc:
            raise HTTPException(404, detail=f"No energy preferences at {exc}") from exc

    @router.post("/hub/energy/tariff/suggest")
    async def suggest(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        prefs = energy.load_prefs(runtime.store)
        on_peak, total = body.get("on_peak_stat"), body.get("total_stat")
        if not on_peak or not total:
            for u in prefs.get("utility_sources") or []:
                periods = u.get("stat_periods") or {}
                key = next((k for k in periods if "on" in k and "peak" in k and "off" not in k), None)
                if key:
                    on_peak, total = periods[key], u["stat_energy"]
                    break
        if not on_peak or not total:
            raise HTTPException(400, detail="No utility source with on-peak readings; pass on_peak_stat and total_stat")
        result = await asyncio.to_thread(energy.suggest_tou, stats(), on_peak, total,
                                         days=int(body.get("days") or 365))
        return {**result, "on_peak_stat": on_peak, "total_stat": total}

    @router.get("/hub/statistics/status")
    async def status(request: Request) -> Any:
        admin(request)
        return await asyncio.to_thread(stats().summary)

    @router.post("/hub/statistics/import")
    async def import_statistics(request: Request) -> Any:
        """HA's long-term statistics from ``home-assistant_v2.db`` (read-only), then its energy settings."""
        admin(request)
        body = await body_of(request)
        src = _import_path(body.get("path"))
        db = os.path.join(src, "home-assistant_v2.db")
        if not os.path.exists(db):
            raise HTTPException(404, detail=f"No recorder database at {db}")
        report = await asyncio.to_thread(stats().import_ha, db)
        if body.get("energy", True) and os.path.exists(os.path.join(src, ".storage", "energy")):
            report["energy"] = await asyncio.to_thread(energy.import_ha_prefs, runtime.store, src, stats())
        return report

    return router
