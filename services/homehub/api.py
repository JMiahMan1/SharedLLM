"""Home Assistant's REST and WebSocket API, served from the hub.

Only what Jarvis (and HA-speaking tools) actually use is implemented, with
HA's request and response shapes, status codes and error bodies, so a client
written for HA cannot tell the difference.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from services.common.secrets import secret_matches
from services.homehub import discovery_api
from services.homehub import live_video, live_video_api
from services.homehub import ha_auth_api, mobile_app_api  # HA companion apps: login, webhooks, pushes
from services.homehub.mobile_app_webhook import registry_view  # hides phones' webhook ids from non-admins
from services.homehub.auth import Auth, Principal, bearer
from services.homehub.core import DeviceUnavailable, HubError, State
from services.homehub.runtime import Runtime

log = logging.getLogger("homehub.api")

HA_VERSION = "2026.11.0"
#: The longest span one recordings request may cover.
MAX_RECORDING_RANGE = timedelta(days=7)
_BOUNDARY = "frame"
_NO_STORE = {"Cache-Control": "no-store"}
#: A snapshot stream gives up after this many failed frames in a row (a camera gone to sleep for good).
SNAPSHOT_STREAM_MAX_FAILURES = 5
#: WebSocket commands other modules add (``async fn(conn, id, msg)``), e.g. mobile_app's push channel.
WS_COMMANDS: dict[str, Any] = {}


def jsonable(value: Any) -> Any:
    if isinstance(value, State):
        return value.as_dict()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    return value


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "+"))
    except ValueError:
        raise HTTPException(400, detail="Invalid datetime") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def _frame(img: bytes) -> bytes:
    return (f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(img)}\r\n\r\n").encode() \
        + img + b"\r\n"


async def _snapshot_frames(request: Request, driver: Any, ent: Any, first: bytes, interval: float):
    """``multipart/x-mixed-replace`` from repeated snapshots, the way HA serves a camera with no MJPEG."""
    loop = asyncio.get_running_loop()
    yield _frame(first)
    failures = 0
    while failures < SNAPSHOT_STREAM_MAX_FAILURES:
        started = loop.time()
        if await request.is_disconnected():
            return  # every frame may wake a battery camera: stop as soon as nobody watches
        try:
            img = await driver.async_camera_image(ent)
        except Exception:
            img = b""
        if img:
            failures = 0
            yield _frame(img)
        else:
            failures += 1
        await asyncio.sleep(max(0.05, interval - (loop.time() - started)))


def config_payload(runtime: Runtime) -> dict[str, Any]:
    hub = runtime.hub
    loc = hub.location or {}
    us = (loc.get("unit_system") or "us_customary") != "metric"
    components = sorted({e.domain for e in hub.config_entries.values()} | {d for d, _ in hub.services}
                        | {"homeassistant", "api", "websocket_api", "history", "logbook", "template"})
    return {
        "latitude": loc.get("latitude"), "longitude": loc.get("longitude"), "elevation": loc.get("elevation", 0),
        "radius": loc.get("radius", 100),
        "unit_system": {
            "length": "mi" if us else "km", "accumulated_precipitation": "in" if us else "mm",
            "area": "ft²" if us else "m²", "mass": "lb" if us else "g", "pressure": "psi" if us else "Pa",
            "temperature": "°F" if us else "°C", "volume": "gal" if us else "L", "wind_speed": "mph" if us else "m/s",
        },
        "location_name": loc.get("location_name", "Home"), "time_zone": hub.time_zone,
        "components": components, "config_dir": "/config", "allowlist_external_dirs": [],
        "allowlist_external_urls": [], "version": HA_VERSION, "config_source": "storage", "recovery_mode": False,
        "state": "RUNNING", "external_url": loc.get("external_url"), "internal_url": loc.get("internal_url"),
        "currency": loc.get("currency", "USD"), "country": loc.get("country"), "language": loc.get("language", "en"),
        "safe_mode": False, "debug": False, "homehub": True,
    }


def build_app(runtime: Runtime, auth: Auth, *, admin_hooks: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI(title="homehub", docs_url=None, redoc_url=None, openapi_url=None)
    hub = runtime.hub
    hooks = admin_hooks or {}
    api = APIRouter()

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

    @app.exception_handler(HTTPException)
    async def _http_exc(_request: Request, exc: HTTPException) -> Response:
        if exc.status_code == 401:
            return PlainTextResponse("401: Unauthorized", status_code=401)
        return _error(exc.status_code, str(exc.detail))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        entries = runtime.entry_status()
        return {"status": "ok", "service": "homehub", "entries": len(entries),
                "loaded": sum(1 for e in entries if e["state"] == "loaded"),
                "entities": len(hub.states.all()), "started_at": hub.started_at.isoformat()}

    @api.get("/api/")
    async def root(request: Request) -> dict[str, str]:
        principal(request)
        return {"message": "API running."}

    @api.get("/api/config")
    async def get_config(request: Request) -> dict[str, Any]:
        principal(request)
        return config_payload(runtime)

    @api.get("/api/states")
    async def get_states(request: Request) -> list[dict[str, Any]]:
        principal(request)
        return [s.as_dict() for s in hub.states.all()]

    @api.get("/api/states/{entity_id}")
    async def get_state(entity_id: str, request: Request) -> Response:
        principal(request)
        st = hub.states.get(entity_id)
        if st is None:
            return _error(404, "Entity not found.")
        return JSONResponse(st.as_dict())

    @api.post("/api/states/{entity_id}")
    async def set_state(entity_id: str, request: Request) -> Response:
        admin(request)
        body = await request.json()
        if not isinstance(body, dict) or "state" not in body:
            return _error(400, "No state specified.")
        existed = hub.states.get(entity_id) is not None
        st = hub.states.set(entity_id, body["state"], body.get("attributes") or {},
                            force=bool(body.get("force_update")))
        return JSONResponse(st.as_dict(), status_code=200 if existed else 201,
                            headers={"Location": f"/api/states/{entity_id}"})

    @api.get("/api/services")
    async def get_services(request: Request) -> list[dict[str, Any]]:
        principal(request)
        return hub.list_services()

    @api.post("/api/services/{domain}/{service}")
    async def call_service(domain: str, service: str, request: Request) -> Response:
        principal(request)
        raw = await request.body()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return _error(400, "Data should be valid JSON.")
        if not isinstance(data, dict):
            return _error(400, "Data should be a JSON object.")
        want_response = "return_response" in request.query_params
        try:
            result = await hub.call_service(domain, service, data, return_response=want_response)
        except DeviceUnavailable as exc:
            return _error(500, str(exc))
        except HubError as exc:
            return _error(exc.status, str(exc))
        except Exception as exc:
            log.exception("%s.%s failed", domain, service)
            return _error(500, f"{type(exc).__name__}: {exc}")
        changed = [s.as_dict() for s in result["changed_states"]]
        if want_response:
            return JSONResponse({"changed_states": changed, "service_response": jsonable(result["response"])})
        return JSONResponse(changed)

    @api.get("/api/events")
    async def list_events(request: Request) -> list[dict[str, Any]]:
        principal(request)
        return [{"event": "state_changed", "listener_count": 1}, {"event": "call_service", "listener_count": 1}]

    @api.post("/api/events/{event_type}")
    async def fire_event(event_type: str, request: Request) -> dict[str, str]:
        admin(request)
        raw = await request.body()
        data = json.loads(raw) if raw else {}
        hub.bus.fire(event_type, data if isinstance(data, dict) else {})
        return {"message": f"Event {event_type} fired."}

    @api.get("/api/config/config_entries/entry")
    async def config_entries(request: Request, domain: str | None = None) -> list[dict[str, Any]]:
        principal(request)
        return [e.public() for e in hub.config_entries.values() if not domain or e.domain == domain]

    @api.post("/api/template")
    async def template(request: Request) -> Response:
        admin(request)
        body = await request.json()
        from services.homehub import templating

        try:
            out = templating.render(hub, body.get("template", ""), body.get("variables") or {})
        except Exception as exc:
            return _error(400, f"Error rendering template: {exc}")
        return PlainTextResponse(out)

    @api.get("/api/history/period")
    @api.get("/api/history/period/{start}")
    async def history(request: Request, start: str | None = None) -> list[list[dict[str, Any]]]:
        principal(request)
        q = request.query_params
        start_dt = parse_time(start) or datetime.now(UTC) - timedelta(days=1)
        end_dt = parse_time(q.get("end_time"))
        ids = [x.strip() for x in (q.get("filter_entity_id") or "").split(",") if x.strip()]
        minimal = "minimal_response" in q
        no_attrs = "no_attributes" in q
        rows = await asyncio.to_thread(runtime.store.history, ids or None, start_dt, end_dt,
                                       changes_only="significant_changes_only" in q)
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_entity.setdefault(r["entity_id"], []).append(r)
        for eid in ids:
            if eid not in by_entity and (st := hub.states.get(eid)) is not None:
                by_entity[eid] = [st.as_dict()]
        out = []
        for _eid, items in by_entity.items():
            series = []
            for i, r in enumerate(items):
                if no_attrs:
                    r = {**r, "attributes": {}}
                if minimal and i > 0:
                    r = {"state": r["state"], "last_changed": r["last_changed"]}
                series.append(r)
            out.append(series)
        return out

    @api.get("/api/logbook")
    @api.get("/api/logbook/{start}")
    async def logbook(request: Request, start: str | None = None) -> list[dict[str, Any]]:
        principal(request)
        q = request.query_params
        start_dt = parse_time(start) or datetime.now(UTC) - timedelta(days=1)
        end_dt = parse_time(q.get("end_time"))
        ids = [q["entity"]] if q.get("entity") else None
        rows = await asyncio.to_thread(runtime.store.history, ids, start_dt, end_dt, changes_only=True)
        return [{"when": r["last_changed"], "state": r["state"], "entity_id": r["entity_id"],
                 "name": r["attributes"].get("friendly_name") or r["entity_id"]} for r in rows]

    async def _camera_driver(entity_id: str) -> tuple[Any, Any]:
        ent = hub.entities.get(entity_id)
        if ent is None or ent.domain != "camera":
            raise HTTPException(404, detail="Entity not found.")
        driver = hub.drivers.get(ent.config_entry_id or "")
        if driver is None or not hasattr(driver, "async_camera_image"):
            raise HTTPException(503, detail="Camera is not available")
        return driver, ent

    def _camera_auth(request: Request, entity_id: str) -> None:
        token = request.query_params.get("token")
        if token:
            ent = hub.entities.get(entity_id)
            driver = hub.drivers.get(ent.config_entry_id or "") if ent is not None else None
            valid = set(driver.access_tokens(entity_id)) if driver is not None and hasattr(driver, "access_tokens") \
                else set()
            st = hub.states.get(entity_id)
            if st is not None and st.attributes.get("access_token"):
                valid.add(st.attributes["access_token"])
            if any(secret_matches(token, v) for v in valid):
                return
        principal(request)

    @api.get("/api/tts_proxy/{name}")
    async def tts_proxy(name: str) -> Response:
        """Spoken audio for a speaker; the unguessable token is the credential, as in HA."""
        from services.homehub import tts

        audio = tts.clip(name.rsplit(".", 1)[0])
        if audio is None:
            return _error(404, "Not found")
        return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @api.get("/api/camera_proxy/{entity_id}")
    async def camera_proxy(entity_id: str, request: Request) -> Response:
        _camera_auth(request, entity_id)
        driver, ent = await _camera_driver(entity_id)
        try:
            image = await driver.async_camera_image(ent)
        except Exception as exc:
            return _error(500, f"Error getting image: {exc}")
        if not image:
            return _error(500, "Camera returned no image")
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @api.get("/api/camera_proxy_stream/{entity_id}")
    async def camera_stream(entity_id: str, request: Request) -> Response:
        _camera_auth(request, entity_id)
        driver, ent = await _camera_driver(entity_id)
        if hasattr(driver, "async_open_mjpeg_stream"):
            # The camera's own MJPEG, relayed: its full frame rate, not one snapshot a second.
            try:
                ctype, chunks = await driver.async_open_mjpeg_stream(ent)
            except Exception as exc:
                log.info("%s: relaying the camera's stream failed, sending snapshots instead: %s", entity_id, exc)
            else:
                return StreamingResponse(chunks, media_type=ctype, headers=_NO_STORE)
        try:
            first = await driver.async_camera_image(ent)
        except Exception as exc:
            return _error(503, f"Camera is not available: {exc}")
        if not first:
            return _error(503, "Camera returned no image")
        interval = float(getattr(driver, "snapshot_interval", 1.0) or 1.0)
        return StreamingResponse(_snapshot_frames(request, driver, ent, first, interval),
                                 media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}", headers=_NO_STORE)

    # -- the hub's own camera API (Jarvis's Cameras page, through the gateway) --------
    def _camera_entities() -> list[Any]:
        return sorted((e for e in hub.entities.values() if e.domain == "camera" and not e.disabled_by),
                      key=lambda e: e.entity_id)

    def _supports_recordings(driver: Any, ent: Any) -> bool:
        if driver is None or not hasattr(driver, "async_list_recordings") \
                or not hasattr(driver, "async_open_recording"):
            return False
        check = getattr(driver, "supports_recordings", None)
        return bool(check(ent)) if callable(check) else True

    def _camera_summary(ent: Any) -> dict[str, Any]:
        driver = hub.drivers.get(ent.config_entry_id or "")
        st = hub.states.get(ent.entity_id)
        live: list[str] = []
        if driver is not None and hasattr(driver, "async_camera_image"):
            live = ["snapshot", "mjpeg"]
        out: dict[str, Any] = {
            "entity_id": ent.entity_id,
            "name": (st.attributes.get("friendly_name") if st is not None else None) or hub.friendly_name(ent),
            "area": hub.area_name(ent.entity_id),
            "platform": ent.platform,
            "state": st.state if st is not None else "unavailable",
            "recordings": _supports_recordings(driver, ent),
            "live": live,
            # "camera" when the stream is the camera's own video, "snapshots" when it is built from stills.
            "stream": "camera" if driver is not None and hasattr(driver, "async_open_mjpeg_stream") else "snapshots",
            "snapshot_interval": float(getattr(driver, "snapshot_interval", 1.0) or 1.0) if driver else None,
        }
        out.update(live_video.camera_summary(hub, ent.entity_id))  # "realtime" when go2rtc can play it
        info = getattr(driver, "camera_info", None)
        if callable(info):
            with contextlib.suppress(Exception):
                out.update({k: v for k, v in (info(ent) or {}).items() if k not in out})
        return out

    @api.get("/api/homehub/cameras")
    async def homehub_cameras(request: Request) -> list[dict[str, Any]]:
        principal(request)
        return [_camera_summary(e) for e in _camera_entities()]

    def _recording_driver(entity_id: str) -> tuple[Any, Any]:
        ent = hub.entities.get(entity_id)
        if ent is None or ent.domain != "camera" or ent.disabled_by:
            raise HTTPException(404, detail="Entity not found.")
        driver = hub.drivers.get(ent.config_entry_id or "")
        if driver is None:
            raise HTTPException(503, detail="Camera is not available")
        if not _supports_recordings(driver, ent):
            raise HTTPException(404, detail="This camera has no recordings")
        return driver, ent

    @api.get("/api/homehub/cameras/{entity_id}/recordings")
    async def homehub_recordings(entity_id: str, request: Request) -> Response:
        principal(request)
        driver, ent = _recording_driver(entity_id)
        q = request.query_params
        end = parse_time(q.get("end")) or datetime.now(UTC)
        start = parse_time(q.get("start")) or end - timedelta(days=1)
        if start >= end:
            return _error(400, "start must be before end")
        if end - start > MAX_RECORDING_RANGE:
            return _error(400, f"The range is longer than {MAX_RECORDING_RANGE.days} days")
        try:
            items = await driver.async_list_recordings(ent, start, end)
        except HubError as exc:
            return _error(exc.status, str(exc))
        except Exception as exc:
            log.exception("listing recordings of %s failed", entity_id)
            return _error(500, f"{type(exc).__name__}: {exc}")
        items = sorted(items, key=lambda r: str(r.get("start") or ""), reverse=True)
        return JSONResponse({"entity_id": entity_id, "start": start.isoformat(), "end": end.isoformat(),
                             "recordings": jsonable(items)})

    @api.get("/api/homehub/cameras/{entity_id}/recordings/play")
    async def homehub_recording_play(entity_id: str, request: Request) -> Response:
        principal(request)
        driver, ent = _recording_driver(entity_id)
        file = request.query_params.get("file") or ""
        if not file:
            return _error(400, "file is required")
        try:
            rec = await driver.async_open_recording(ent, file, range_header=request.headers.get("range"))
        except HubError as exc:
            return _error(exc.status, str(exc))
        except Exception as exc:
            log.exception("opening recording %s of %s failed", file, entity_id)
            return _error(500, f"{type(exc).__name__}: {exc}")
        headers = {**rec.headers, "Cache-Control": "private, no-store"}
        headers.setdefault("Accept-Ranges", "bytes" if rec.status == 206 or "Content-Range" in headers else "none")
        return StreamingResponse(rec.body, status_code=rec.status, media_type=rec.content_type, headers=headers)

    # -- hub administration (internal secret / HA admin only) -----------------------
    @app.get("/hub/status")
    async def hub_status(request: Request) -> dict[str, Any]:
        admin(request)
        engine = runtime.automations
        return {
            "entries": runtime.entry_status(),
            "automations": engine.status() if engine is not None and hasattr(engine, "status") else [],
            "import": runtime.store.get_kv("import_report"),
            "counts": {t: runtime.store.count(t) for t in ("config_entries", "devices", "entities", "areas",
                                                            "history", "automations", "refresh_tokens")},
        }

    @app.post("/hub/import")
    async def hub_import(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        hook = hooks.get("import")
        if hook is None:
            raise HTTPException(503, detail="import is not available")
        return await hook(path=body.get("path"), history_days=int(body.get("history_days") or 0))

    @app.post("/hub/entries/{entry_id}/reload")
    async def hub_reload(entry_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        entry = hub.config_entries.get(entry_id)
        if entry is None:
            raise HTTPException(404, detail="Config entry not found")
        await runtime.stop_entry(entry_id)
        runtime.start_entry(entry)
        return {"entry_id": entry_id, "state": "reloading"}

    @app.patch("/hub/entries/{entry_id}/options")
    async def hub_options(entry_id: str, request: Request) -> dict[str, Any]:
        """Change an entry's options (e.g. a device's new IP) and reload it."""
        admin(request)
        entry = hub.config_entries.get(entry_id)
        if entry is None:
            raise HTTPException(404, detail="Config entry not found")
        patch = await request.json()
        if not isinstance(patch, dict):
            raise HTTPException(400, detail="options must be an object")
        for k, v in patch.items():
            if v is None:
                entry.options.pop(k, None)
            else:
                entry.options[k] = v
        runtime.store.save_config_entry(entry)
        await runtime.stop_entry(entry_id)
        runtime.start_entry(entry)
        return {"entry_id": entry_id, "options": entry.options}

    app.include_router(api)
    app.include_router(discovery_api.build_router(runtime, auth))
    app.include_router(live_video_api.build_router(runtime, auth))  # real-time camera video (go2rtc)
    app.include_router(ha_auth_api.build_router(runtime, auth))
    app.include_router(mobile_app_api.build_router(runtime, auth))

    @app.websocket("/api/websocket")
    async def websocket(ws: WebSocket) -> None:
        await WSConnection(ws, runtime, auth).run()

    return app


class WSConnection:
    """One HA WebSocket API client."""

    def __init__(self, ws: WebSocket, runtime: Runtime, auth: Auth) -> None:
        self.ws = ws
        self.runtime = runtime
        self.hub = runtime.hub
        self.auth = auth
        self.subs: dict[int, Any] = {}
        self.principal: Principal | None = None
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def send(self, msg: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(jsonable(msg), default=str))

    async def result(self, mid: int, result: Any = None) -> None:
        await self.send({"id": mid, "type": "result", "success": True, "result": result})

    async def error(self, mid: int, code: str, message: str) -> None:
        await self.send({"id": mid, "type": "result", "success": False, "error": {"code": code, "message": message}})

    async def run(self) -> None:
        await self.ws.accept()
        try:
            await self.send({"type": "auth_required", "ha_version": HA_VERSION})
            try:
                first = json.loads(await asyncio.wait_for(self.ws.receive_text(), timeout=10))
            except (TimeoutError, ValueError):
                return
            self.principal = self.auth.authenticate(first.get("access_token") or first.get("api_password"))
            if first.get("type") != "auth" or self.principal is None:
                await self.send({"type": "auth_invalid", "message": "Invalid access token or password"})
                return
            await self.send({"type": "auth_ok", "ha_version": HA_VERSION})
            while True:
                raw = await self.ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                for m in msg if isinstance(msg, list) else [msg]:
                    task = asyncio.create_task(self.handle(m))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            for unsub in self.subs.values():
                with contextlib.suppress(Exception):
                    unsub()
            self.subs.clear()

    async def handle(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id")
        kind = msg.get("type")
        hub = self.hub
        try:
            if kind == "ping":
                await self.send({"id": mid, "type": "pong"})
            elif kind == "supported_features":
                await self.result(mid)
            elif kind == "get_states":
                await self.result(mid, [s.as_dict() for s in hub.states.all()])
            elif kind == "get_config":
                await self.result(mid, config_payload(self.runtime))
            elif kind == "get_services":
                await self.result(mid, {d["domain"]: d["services"] for d in hub.list_services()})
            elif kind == "call_service":
                data = dict(msg.get("service_data") or {})
                if msg.get("target"):
                    data["target"] = msg["target"]
                res = await hub.call_service(msg["domain"], msg["service"], data,
                                             return_response=bool(msg.get("return_response")))
                await self.result(mid, {"context": {"id": mid, "parent_id": None, "user_id": None},
                                        "response": jsonable(res["response"])})
            elif kind == "subscribe_events":
                event_type = msg.get("event_type")

                async def forward(etype: str, data: dict[str, Any], _mid: int = mid) -> None:
                    with contextlib.suppress(Exception):
                        await self.send({"id": _mid, "type": "event", "event": {
                            "event_type": etype, "data": data, "origin": "LOCAL",
                            "time_fired": datetime.now(UTC).isoformat(),
                            "context": {"id": "", "parent_id": None, "user_id": None}}})

                self.subs[mid] = hub.bus.subscribe(forward, event_type)
                await self.result(mid)
            elif kind == "subscribe_entities":
                await self._subscribe_entities(mid, msg.get("entity_ids"))
            elif kind == "unsubscribe_events":
                unsub = self.subs.pop(msg.get("subscription"), None)
                if unsub is None:
                    await self.error(mid, "not_found", "Subscription not found.")
                else:
                    unsub()
                    await self.result(mid)
            elif kind == "fire_event":
                hub.bus.fire(msg.get("event_type", ""), msg.get("event_data") or {})
                await self.result(mid, {"context": {"id": ""}})
            elif kind == "render_template":
                from services.homehub import templating

                out = templating.render(hub, msg.get("template", ""), msg.get("variables") or {})
                await self.result(mid)
                await self.send({"id": mid, "type": "event", "event": {"result": out, "listeners": {
                    "all": False, "entities": [], "domains": [], "time": False}}})
            elif kind == "config_entries/get":
                domain = msg.get("domain")
                await self.result(mid, [e.public() for e in hub.config_entries.values()
                                        if not domain or e.domain == domain])
            elif kind == "config/area_registry/list":
                await self.result(mid, [{"area_id": a.area_id, "name": a.name, "floor_id": a.floor_id,
                                         "aliases": a.aliases, "icon": a.icon, "labels": a.labels, "picture": None}
                                        for a in hub.areas.values()])
            elif kind == "config/floor_registry/list":
                await self.result(mid, list(hub.floors.values()))
            elif kind == "config/device_registry/list":
                await self.result(mid, [d.public() for d in hub.devices.values()])
            elif kind == "config/entity_registry/list":
                await self.result(mid, [registry_view(e, self.principal) for e in hub.entities.values()])
            elif kind == "config/entity_registry/get":
                ent = hub.entities.get(msg.get("entity_id", ""))
                if ent is None:
                    await self.error(mid, "not_found", "Entity not found")
                else:
                    await self.result(mid, registry_view(ent, self.principal))
            elif kind == "history/history_during_period":
                start = parse_time(msg.get("start_time")) or datetime.now(UTC) - timedelta(days=1)
                rows = await asyncio.to_thread(self.runtime.store.history, msg.get("entity_ids"), start,
                                               parse_time(msg.get("end_time")))
                out: dict[str, list[dict[str, Any]]] = {}
                for r in rows:
                    out.setdefault(r["entity_id"], []).append({
                        "s": r["state"], "a": {} if msg.get("no_attributes") else r["attributes"],
                        "lu": datetime.fromisoformat(r["last_updated"]).timestamp()})
                await self.result(mid, out)
            elif kind in WS_COMMANDS:
                await WS_COMMANDS[kind](self, mid, msg)
            else:
                await self.error(mid, "unknown_command", f"Unknown command: {kind}")
        except HubError as exc:
            await self.error(mid, "home_assistant_error", str(exc))
        except KeyError as exc:
            await self.error(mid, "invalid_format", f"missing {exc}")
        except Exception as exc:
            log.exception("ws command %s failed", kind)
            await self.error(mid, "unknown_error", str(exc))

    async def _subscribe_entities(self, mid: int, entity_ids: list[str] | None) -> None:
        """The compressed state feed newer HA clients use (``a`` added, ``c`` changed, ``r`` removed)."""
        wanted = set(entity_ids or [])

        def compress(s: State) -> dict[str, Any]:
            return {"s": s.state, "a": s.attributes, "c": s.context_id,
                    "lc": s.last_changed.timestamp(), "lu": s.last_updated.timestamp()}

        async def forward(_etype: str, data: dict[str, Any]) -> None:
            eid = data.get("entity_id")
            if wanted and eid not in wanted:
                return
            new = data.get("new_state")
            payload = {"r": [eid]} if new is None else {"a": {eid: compress(new)}}
            with contextlib.suppress(Exception):
                await self.send({"id": mid, "type": "event", "event": payload})

        self.subs[mid] = self.hub.bus.subscribe(forward, "state_changed")
        await self.result(mid)
        initial = {s.entity_id: compress(s) for s in self.hub.states.all() if not wanted or s.entity_id in wanted}
        await self.send({"id": mid, "type": "event", "event": {"a": initial}})
