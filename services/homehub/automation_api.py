"""Automations, scripts, scenes, the scheduler and thermostat schedules over HTTP.

Any signed-in principal may read (and hold or resume a thermostat, which is no
more than setting it); writes are for admins (the stack's ``INTERNAL_SECRET``
or an HA owner/admin token). The engine is ``runtime.automations``; main.py
calls :func:`prepare` before it loads (so the remap and tracing apply from the
first run) and :func:`start` once it runs.

| Route | Who | What |
|---|---|---|
| ``GET /api/homehub/automations`` | read | every automation, script and scene with its status |
| ``GET|PUT|DELETE /api/homehub/automations/{kind}/{id}``, ``POST /api/homehub/automations/{kind}`` | read / admin | one item's config (HA's own shape) |
| ``POST /api/homehub/automations/validate`` | admin | schema errors, or the reason it would load disabled |
| ``POST .../automation/{id}/enabled``, ``POST .../{kind}/{id}/run`` | admin | switch on/off, run now |
| ``GET .../{kind}/{id}/traces`` | read | the last runs |
| ``GET .../export``, ``POST .../import`` | admin | YAML in HA's file shapes |
| ``GET .../catalog`` | read | triggers, conditions, actions, services and entities for an editor |
| ``GET|PUT .../remap``, ``POST .../remap/preview``, ``POST .../remap/suggest`` | admin | renamed-entity repairs |
| ``GET|POST /api/homehub/schedules``, ``GET|PUT|DELETE .../{id}``, ``POST .../{id}/enabled`` | read / admin | the nielsfaber scheduler |
| ``GET /api/homehub/climate``, ``.../programs``, ``.../hold``, ``.../resume``, ``.../settings`` | read / admin | thermostat schedules |
| ``GET|POST|DELETE /api/config/{automation,script,scene}/config/{id}`` | read / admin | HA's own config API |
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub.auth import Auth, Principal, bearer
from services.homehub.automation_editor import KINDS, Editor, EditorError, catalog, suggest_remap

log = logging.getLogger("homehub.automation_api")

IMPORT_DIR = os.getenv("HOMEHUB_IMPORT_DIR", "/import")


def prepare(engine: Any, store: Any) -> Editor:
    """Attach the editor (remap, tracing) and the thermostat layer to a new engine, before it loads."""
    from services.homehub.climate_schedule import ClimateManager

    editor = Editor(engine, store)
    engine.editor = editor
    engine.climate = ClimateManager(engine, editor, store)
    return editor


async def start(runtime: Any) -> None:
    engine = getattr(runtime, "automations", None)
    climate = getattr(engine, "climate", None)
    if climate is not None:
        await climate.start()


async def stop(engine: Any) -> None:
    climate = getattr(engine, "climate", None)
    if climate is not None:
        await climate.stop()


def recorder_path() -> str:
    return os.path.join(IMPORT_DIR, "homeassistant", "home-assistant_v2.db")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def who(request: Request) -> Principal:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def admin(request: Request) -> Principal:
        p = who(request)
        if not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def editor() -> Editor:
        eng = getattr(runtime, "automations", None)
        ed = getattr(eng, "editor", None)
        if ed is None:
            raise HTTPException(503, detail="Automations are still starting")
        return ed

    def climate() -> Any:
        cl = getattr(getattr(runtime, "automations", None), "climate", None)
        if cl is None:
            raise HTTPException(503, detail="Automations are still starting")
        return cl

    def kind_of(kind: str) -> str:
        if kind not in KINDS:
            raise HTTPException(404, detail="Unknown kind")
        return kind

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def fail(exc: EditorError) -> JSONResponse:
        return JSONResponse({"message": str(exc), "errors": exc.errors}, status_code=exc.status)

    # -- automations, scripts, scenes ----------------------------------------------------
    @router.get("/api/homehub/automations")
    async def list_items(request: Request) -> dict[str, Any]:
        p = who(request)
        return {"items": editor().items(), "can_edit": p.is_admin}

    @router.get("/api/homehub/automations/catalog")
    async def get_catalog(request: Request) -> dict[str, Any]:
        who(request)
        return catalog(runtime.hub)

    @router.get("/api/homehub/automations/export")
    async def export(request: Request, kind: str = "automation", id: str | None = None) -> Any:
        admin(request)
        try:
            return editor().export(kind_of(kind), id)
        except EditorError as exc:
            return fail(exc)

    @router.post("/api/homehub/automations/import")
    async def import_yaml(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await editor().import_yaml(kind_of(str(body.get("kind") or "automation")),
                                              str(body.get("yaml") or ""), bool(body.get("overwrite")))
        except EditorError as exc:
            return fail(exc)

    @router.post("/api/homehub/automations/validate")
    async def validate(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return editor().validate(kind_of(str(body.get("kind") or "automation")), body.get("config"))
        except EditorError as exc:
            return fail(exc)

    @router.get("/api/homehub/automations/remap")
    async def get_remap(request: Request) -> dict[str, Any]:
        admin(request)
        ed = editor()
        return {"remap": ed.remap, "report": ed.remap_report()}

    @router.put("/api/homehub/automations/remap")
    async def put_remap(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        ed = editor()
        try:
            report = await ed.set_remap(body.get("remap"))
        except EditorError as exc:
            return fail(exc)
        return {"remap": ed.remap, "report": report}

    @router.post("/api/homehub/automations/remap/preview")
    async def preview_remap(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        from services.homehub.automation_editor import normalise_remap

        try:
            return {"report": editor().remap_report(normalise_remap(body.get("remap")))}
        except EditorError as exc:
            return fail(exc)

    @router.post("/api/homehub/automations/remap/suggest")
    async def suggest(request: Request) -> Any:
        admin(request)
        import asyncio

        return await asyncio.to_thread(suggest_remap, editor(), recorder_path())

    @router.post("/api/homehub/automations/{kind}")
    async def create_item(kind: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            res = await editor().save(kind_of(kind), None, body.get("config"), create=True)
        except EditorError as exc:
            return fail(exc)
        return {"item": res["item"], "config": res["config"]}

    @router.get("/api/homehub/automations/{kind}/{item_id}")
    async def get_item(kind: str, item_id: str, request: Request) -> Any:
        who(request)
        try:
            return editor().get(kind_of(kind), item_id)
        except EditorError as exc:
            return fail(exc)

    @router.put("/api/homehub/automations/{kind}/{item_id}")
    async def put_item(kind: str, item_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            res = await editor().save(kind_of(kind), item_id, body.get("config"))
        except EditorError as exc:
            return fail(exc)
        return {"item": res["item"], "config": res["config"]}

    @router.delete("/api/homehub/automations/{kind}/{item_id}")
    async def delete_item(kind: str, item_id: str, request: Request) -> Any:
        admin(request)
        try:
            await editor().delete(kind_of(kind), item_id)
        except EditorError as exc:
            return fail(exc)
        return {"result": "ok"}

    @router.post("/api/homehub/automations/automation/{item_id}/enabled")
    async def set_enabled(item_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        if "enabled" not in body:
            raise HTTPException(400, detail="Give enabled: true or false")
        try:
            return {"item": await editor().set_enabled(item_id, bool(body["enabled"]))}
        except EditorError as exc:
            return fail(exc)

    @router.post("/api/homehub/automations/{kind}/{item_id}/run")
    async def run_item(kind: str, item_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        variables = body.get("variables") or {}
        if not isinstance(variables, dict):
            raise HTTPException(400, detail="variables must be an object")
        try:
            await editor().run(kind_of(kind), item_id, variables, bool(body.get("skip_condition", True)))
        except EditorError as exc:
            return fail(exc)
        return {"result": "ok"}

    @router.get("/api/homehub/automations/{kind}/{item_id}/traces")
    async def traces(kind: str, item_id: str, request: Request) -> dict[str, Any]:
        who(request)
        return {"traces": editor().tracer.list(kind_of(kind), item_id)}

    # -- HA's own config API (the automation editor's routes) ------------------------------
    @router.get("/api/config/{kind}/config/{item_id}")
    async def ha_get(kind: str, item_id: str, request: Request) -> Any:
        who(request)
        try:
            return editor().get(kind_of(kind), item_id)["config"]
        except EditorError as exc:
            return JSONResponse({"message": "Resource not found"} if exc.status == 404 else {"message": str(exc)},
                                status_code=exc.status)

    @router.post("/api/config/{kind}/config/{item_id}")
    async def ha_post(kind: str, item_id: str, request: Request) -> Any:
        admin(request)
        conf = await body_of(request)
        ed = editor()
        exists = ed.stored_one(kind_of(kind), item_id) is not None or item_id in (
            ed.engine.scenes if kind == "scene" else ed._pool(kind))
        try:
            await ed.save(kind, item_id, conf, create=not exists)
        except EditorError as exc:
            return JSONResponse({"message": f"Message malformed: {exc}"}, status_code=400)
        return {"result": "ok"}

    @router.delete("/api/config/{kind}/config/{item_id}")
    async def ha_delete(kind: str, item_id: str, request: Request) -> Any:
        admin(request)
        try:
            await editor().delete(kind_of(kind), item_id)
        except EditorError as exc:
            return JSONResponse({"message": "Resource not found"}, status_code=exc.status)
        return {"result": "ok"}

    # -- the scheduler ----------------------------------------------------------------------
    def schedule_out(s: Any) -> dict[str, Any]:
        ed = editor()
        view = ed.schedule_view(s)
        cl = getattr(ed.engine, "climate", None)
        view["climate"] = bool(cl is not None and cl.is_program(s))
        return view

    @router.get("/api/homehub/schedules")
    async def list_schedules(request: Request) -> dict[str, Any]:
        p = who(request)
        return {"schedules": [schedule_out(s) for s in editor().schedules()], "can_edit": p.is_admin}

    @router.post("/api/homehub/schedules")
    async def create_schedule(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return schedule_out(await editor().save_schedule(None, body))
        except EditorError as exc:
            return fail(exc)

    @router.get("/api/homehub/schedules/{sid}")
    async def get_schedule(sid: str, request: Request) -> Any:
        who(request)
        try:
            return schedule_out(editor().get_schedule(sid))
        except EditorError as exc:
            return fail(exc)

    @router.put("/api/homehub/schedules/{sid}")
    async def put_schedule(sid: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            out = schedule_out(await editor().save_schedule(sid, body))
        except EditorError as exc:
            return fail(exc)
        cl = getattr(editor().engine, "climate", None)
        if cl is not None:
            cl.publish_all()
        return out

    @router.delete("/api/homehub/schedules/{sid}")
    async def delete_schedule(sid: str, request: Request) -> Any:
        admin(request)
        try:
            await editor().delete_schedule(sid)
        except EditorError as exc:
            return fail(exc)
        return {"result": "ok"}

    @router.post("/api/homehub/schedules/{sid}/enabled")
    async def schedule_enabled(sid: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        if "enabled" not in body:
            raise HTTPException(400, detail="Give enabled: true or false")
        try:
            out = schedule_out(await editor().set_schedule_enabled(sid, bool(body["enabled"])))
        except EditorError as exc:
            return fail(exc)
        cl = getattr(editor().engine, "climate", None)
        if cl is not None:
            cl.publish_all()
        return out

    # -- thermostats ------------------------------------------------------------------------
    @router.get("/api/homehub/climate")
    async def climate_overview(request: Request) -> dict[str, Any]:
        p = who(request)
        return {**climate().overview(), "can_edit": p.is_admin}

    @router.get("/api/homehub/climate/settings")
    async def climate_settings(request: Request) -> dict[str, Any]:
        who(request)
        return climate().settings

    @router.put("/api/homehub/climate/settings")
    async def put_climate_settings(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await climate().save_settings(body)
        except EditorError as exc:
            return fail(exc)

    @router.post("/api/homehub/climate/programs")
    async def create_program(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await climate().save_program(None, body)
        except EditorError as exc:
            return fail(exc)

    @router.put("/api/homehub/climate/programs/{pid}")
    async def put_program(pid: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await climate().save_program(pid, body)
        except EditorError as exc:
            return fail(exc)

    @router.delete("/api/homehub/climate/programs/{pid}")
    async def delete_program(pid: str, request: Request) -> Any:
        admin(request)
        try:
            await climate().delete_program(pid)
        except EditorError as exc:
            return fail(exc)
        return {"result": "ok"}

    @router.post("/api/homehub/climate/hold")
    async def hold(request: Request) -> Any:
        p = who(request)
        body = await body_of(request)
        try:
            return await climate().set_hold(str(body.get("entity_id") or ""), body.get("setpoint"),
                                            body.get("until", "next_slot"), by=p.name or None)
        except EditorError as exc:
            return fail(exc)

    @router.post("/api/homehub/climate/resume")
    async def resume(request: Request) -> Any:
        who(request)
        body = await body_of(request)
        try:
            return await climate().resume(str(body.get("entity_id") or ""))
        except EditorError as exc:
            return fail(exc)

    return router
