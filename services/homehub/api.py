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
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from services.common.secrets import secret_matches
from services.homehub import (  # HA companion apps: login, webhooks, pushes
    automation_api,  # automation editor, scheduler, thermostat schedules
    bluetooth_api,  # /hub/bluetooth: scanners, devices heard, the room map
    camera_recording,
    camera_recording_api,
    capabilities_api,  # the capability index: which devices can do what
    conversation,  # /hub/conversation/triggers: HA sentence triggers, asked by Jarvis first
    dashboard_api,
    discovery_api,
    entries_api,  # integrations: reconfigure, remove/unpair, add by address
    esphome_restarts,  # ESPHome restarts and drops, for /health
    ha_auth_api,
    ha_ws_compat,
    hardware_api,  # the hardware list: integrations, devices, entities, disable, sync
    helpers_api,  # /hub/helpers: HA's helpers (input_*, counter, timer)
    light_groups_api,  # /hub/light_groups: light groups (the group helper)
    live_video,
    live_video_api,
    matter_ble,  # new Wi-Fi Matter devices over Bluetooth, and their household Wi-Fi
    mobile_app_api,
    prune_api,  # dry-run and apply pruning of stale imported HA data
    registries_api,  # floors, labels, categories, zones, persons, tags
    restarts_api,  # /hub/devices/{id}/restarts: an ESPHome node's restarts and their causes
    service_logins_api,
    statistics_api,
    users_api,  # the hub's users and its login providers (local, Jarvis)
    voice_api,  # /hub/voice: the voice satellites and their runs
)
from services.homehub.auth import Auth, Principal, bearer
from services.homehub.core import DeviceUnavailable, HubError, State, camera_rotation
from services.homehub.mobile_app_webhook import registry_view  # hides phones' webhook ids from non-admins
from services.homehub.runtime import Runtime

log = logging.getLogger("homehub.api")

HA_VERSION = "2026.11.0"
#: The longest span one recordings request may cover.
MAX_RECORDING_RANGE = timedelta(days=7)
_BOUNDARY = "frame"
_NO_STORE = {"Cache-Control": "no-store"}
#: HA's history SIGNIFICANT_DOMAINS: attribute-only changes count as significant for these.
SIGNIFICANT_DOMAINS = frozenset({"climate", "device_tracker", "humidifier", "thermostat", "water_heater"})
#: HA's logbook leaves these domains out entirely: they change all the time and none of it reads as an event.
LOGBOOK_ALWAYS_CONTINUOUS = frozenset({"counter", "proximity"})
#: Set ("true") on a history or logbook answer whose window held more rows than one answer carries: the oldest
#: rows were left out. A header, so HA clients reading the body see HA's shape unchanged.
TRUNCATED_HEADER = "X-HomeHub-History-Truncated"
#: A snapshot stream gives up after this many failed frames in a row (a camera gone to sleep for good).
SNAPSHOT_STREAM_MAX_FAILURES = 5
#: WebSocket commands other modules add (``async fn(conn, id, msg)``), e.g. mobile_app's push channel.
WS_COMMANDS: dict[str, Any] = dict(ha_ws_compat.COMMANDS)  # HA commands other HA clients ask for


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


#: What a user whose login provider limits their entities may call (everything else is the admins').
#: Each of these filters or checks by entity (``Principal.may_view`` / ``may_control``).
LIMITED_PATHS = ("/api/", "/api/config", "/api/states", "/api/services", "/api/events", "/api/history/period",
                 "/api/logbook", "/api/camera_proxy/", "/api/image_proxy/", "/api/camera_proxy_stream/",
                 "/api/config/entity_registry", "/api/mobile_app/registrations", "/api/tts_proxy/", "/api/webhook/",
                 "/api/websocket", "/auth/")
#: WebSocket commands a limited user may send besides the core ones api.py checks itself.
LIMITED_WS = {"auth/current_user", "mobile_app/push_notification_channel", "mobile_app/push_notification_confirm",
              "network/url", "config/entity_registry/list_for_display", "config/entity_registry/get_entries"}


def limited_path_ok(path: str) -> bool:
    for allowed in LIMITED_PATHS:
        if allowed.endswith("/") and allowed != "/api/":
            if path.startswith(allowed):
                return True
        elif path == allowed or path.startswith(allowed + "/"):
            return True
    return False


def call_allowed(hub: Any, p: Principal | None, domain: str, service: str, data: dict[str, Any]) -> str | None:
    """None when this caller may make this call, else why not. Only limited users are checked.

    A limited user's call must name entities, and every one of them must be theirs to control. A script is
    named by its service (``script.<name>``) as often as by an entity.
    """
    if p is None or not p.restricted:
        return None
    targets = hub.resolve_targets(data)
    if not targets and domain == "script" and service not in ("turn_on", "turn_off", "toggle", "reload"):
        targets = [f"script.{service}"]
    if not targets:
        return f"{domain}.{service} needs an entity this account may use"
    denied = [t for t in targets if not p.may_control(t)]
    if denied:
        return f"This account may not use {denied[0]}"
    return None


def _event_visible(p: Principal | None, data: dict[str, Any]) -> bool:
    if p is None or not p.restricted:
        return True
    eid = data.get("entity_id")
    if isinstance(eid, str) and eid and not p.may_view(eid):
        return False
    ids = data.get("entity_ids")
    return not (isinstance(ids, list) and any(not p.may_view(str(i)) for i in ids))


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


def logbook_continuous(entity_id: str, attributes: dict[str, Any] | None) -> bool:
    """HA's logbook filter: counters, proximity, and sensors with a unit or a state class are measurements."""
    domain = entity_id.split(".", 1)[0]
    if domain in LOGBOOK_ALWAYS_CONTINUOUS:
        return True
    attrs = attributes or {}
    return domain == "sensor" and ("unit_of_measurement" in attrs or "state_class" in attrs)


async def history_by_entity(runtime: Runtime, ids: list[str] | None, start: datetime, end: datetime | None, *,
                            changes_only: bool, start_state: bool) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    """Recorded rows per entity (oldest first) and whether older rows were left out.

    With ``changes_only``, attribute-only changes are still kept for HA's SIGNIFICANT_DOMAINS (a tracker moving
    while it stays "not_home" is a change of position). With ``start_state``, each entity's series begins with
    its state at ``start`` (HA's include_start_time_state), timed at ``start``. That state is left out when the
    window was truncated: it would claim to last until the oldest row kept, across the rows left out.
    """
    store = runtime.store
    keep_all = [i for i in ids or [] if i.split(".", 1)[0] in SIGNIFICANT_DOMAINS] if changes_only else []
    rest = [i for i in ids or [] if i not in keep_all]
    rows: list[dict[str, Any]] = []
    truncated = False
    if keep_all:
        got, cut = await asyncio.to_thread(store.history_page, keep_all, start, end, changes_only=False)
        rows += got
        truncated |= cut
    if rest or not ids:
        got, cut = await asyncio.to_thread(store.history_page, rest or None, start, end, changes_only=changes_only)
        rows += got
        truncated |= cut
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_entity.setdefault(r["entity_id"], []).append(r)
    if start_state and not truncated:
        known = [s.entity_id for s in runtime.hub.states.all()]
        wanted = list(ids) if ids else list(dict.fromkeys([*by_entity, *known]))
        at = await asyncio.to_thread(store.states_at, wanted, start)
        when = start.astimezone(UTC).isoformat()
        merged: dict[str, list[dict[str, Any]]] = {}
        for eid in wanted:
            series = by_entity.get(eid, [])
            if eid in at:
                series = [{**at[eid], "last_changed": when, "last_updated": when}, *series]
            if series:
                merged[eid] = series
        by_entity = merged
    return by_entity, truncated


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

    @app.middleware("http")
    async def _limited_users(request: Request, call_next: Any) -> Response:
        """A user with entity limits reaches only the routes that apply them (``LIMITED_PATHS``)."""
        token = bearer(request.headers.get("authorization"))
        if token and not limited_path_ok(request.url.path):
            p = auth.authenticate(token)
            if p is not None and p.restricted:
                return PlainTextResponse("401: Unauthorized", status_code=401)
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _http_exc(_request: Request, exc: HTTPException) -> Response:
        if exc.status_code == 401:
            return PlainTextResponse("401: Unauthorized", status_code=401)
        return _error(exc.status_code, str(exc.detail))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        entries = runtime.entry_status()
        out = {"status": "ok", "service": "homehub", "entries": len(entries),
               "loaded": sum(1 for e in entries if e["state"] == "loaded"),
               "entities": len(hub.states.all()), "started_at": hub.started_at.isoformat()}
        monitor = getattr(runtime, "loop_monitor", None)
        if monitor is not None:
            out["loop"] = monitor.snapshot()  # stalls and thread-pool waits since start (loop_monitor.py)
        with contextlib.suppress(Exception):  # a summary must never fail the health check
            out["esphome_restarts"] = esphome_restarts.health_summary(hub)
        return out

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
        p = principal(request)
        return [s.as_dict() for s in hub.states.all() if p.may_view(s.entity_id)]

    @api.get("/api/states/{entity_id}")
    async def get_state(entity_id: str, request: Request) -> Response:
        p = principal(request)
        st = hub.states.get(entity_id)
        if st is None or not p.may_view(entity_id):
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
        p = principal(request)
        raw = await request.body()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return _error(400, "Data should be valid JSON.")
        if not isinstance(data, dict):
            return _error(400, "Data should be a JSON object.")
        refused = call_allowed(hub, p, domain, service, data)
        if refused:
            return _error(403, refused)
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

    # HA serves its registries over the WebSocket only; Jarvis's older lookups ask for them over REST.
    @api.get("/api/config/device_registry/list")
    async def device_registry_rest(request: Request) -> list[dict[str, Any]]:
        principal(request)
        return [d.public() for d in hub.devices.values()]

    @api.get("/api/config/entity_registry")
    @api.get("/api/config/entity_registry/list")
    async def entity_registry_rest(request: Request) -> list[dict[str, Any]]:
        p = principal(request)
        return [registry_view(e, p) for e in hub.entities.values() if p.may_view(e.entity_id)]

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
    async def history(request: Request, start: str | None = None) -> Response:
        p = principal(request)
        q = request.query_params
        start_dt = parse_time(start) or datetime.now(UTC) - timedelta(days=1)
        end_dt = parse_time(q.get("end_time"))
        ids = [x.strip() for x in (q.get("filter_entity_id") or "").split(",") if x.strip()]
        minimal = "minimal_response" in q
        no_attrs = "no_attributes" in q
        changes_only = "significant_changes_only" in q and q.get("significant_changes_only") not in ("0", "false")
        found, truncated = await history_by_entity(runtime, ids or None, start_dt, end_dt, changes_only=changes_only,
                                                   start_state="skip_initial_state" not in q)
        by_entity = {eid: items for eid, items in found.items() if p.may_view(eid)}
        for eid in ids:
            if eid not in by_entity and p.may_view(eid) and (st := hub.states.get(eid)) is not None:
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
        return JSONResponse(jsonable_encoder(out), headers={TRUNCATED_HEADER: "true"} if truncated else None)

    @api.get("/api/logbook")
    @api.get("/api/logbook/{start}")
    async def logbook(request: Request, start: str | None = None) -> Response:
        p = principal(request)
        q = request.query_params
        start_dt = parse_time(start) or datetime.now(UTC) - timedelta(days=1)
        end_dt = parse_time(q.get("end_time"))
        ids = [q["entity"]] if q.get("entity") else None
        rows, truncated = await asyncio.to_thread(runtime.store.history_page, ids, start_dt, end_dt,
                                                  changes_only=True)
        out = []
        for r in rows:
            eid = r["entity_id"]
            if not p.may_view(eid):
                continue
            cur = hub.states.get(eid)
            if logbook_continuous(eid, r["attributes"]) or (cur is not None and logbook_continuous(eid, cur.attributes)):
                continue  # HA's logbook leaves measurements out, asked for by name or not
            out.append({"when": r["last_changed"], "state": r["state"], "entity_id": eid,
                        "name": r["attributes"].get("friendly_name") or eid})
        return JSONResponse(out, headers={TRUNCATED_HEADER: "true"} if truncated else None)

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
        if not principal(request).may_view(entity_id):
            raise HTTPException(404, detail="Entity not found.")

    @api.api_route("/api/tts_proxy/{name}", methods=["GET", "HEAD"])
    async def tts_proxy(name: str, request: Request) -> Response:
        """Spoken audio for a speaker; the unguessable token is the credential, as in HA.

        Speakers probe with HEAD and fetch in pieces with ``Range``, so both are answered properly.
        """
        from services.common.audio_clip import clip_response
        from services.homehub import tts

        audio = tts.clip(name.rsplit(".", 1)[0])
        if audio is None:
            return _error(404, "Not found")
        return clip_response(audio, "audio/wav", request.headers.get("range"), head=request.method == "HEAD",
                             headers={"Cache-Control": "no-store"})

    @api.api_route("/api/esphome/ffmpeg_proxy/{device}/{name}", methods=["GET", "HEAD"])
    async def esphome_ffmpeg_proxy(device: str, name: str, request: Request) -> Response:
        """Audio converted to the format an ESPHome media player asked for (``esphome_audio.py``).

        The unguessable token is the credential, as for ``/api/tts_proxy/`` and in HA.
        """
        from services.homehub import esphome_audio

        conv = esphome_audio.PROXY.lookup(device, name)
        if conv is None:
            return _error(404, "Not found")
        headers = {"Cache-Control": "no-store"}
        media_type = conv.fmt.content_type
        if request.method == "HEAD":
            return Response(b"", media_type=media_type, headers=headers)
        try:
            if conv.source_url is None:
                return Response(await esphome_audio.PROXY.clip_audio(conv), media_type=media_type, headers=headers)
            body = await esphome_audio.PROXY.open_stream(conv)
        except esphome_audio.ProxyError as exc:
            return _error(exc.status, exc.message)
        return StreamingResponse(body, media_type=media_type, headers=headers)

    @api.post("/hub/esphome/audio_url")
    async def esphome_audio_url(request: Request) -> Response:
        """A proxy URL for a node the caller talks to itself (execution's alarm and timer alerts).

        ``{"device", "url", "formats": [MediaPlayerSupportedFormat fields], "announcement"}`` ->
        ``{"url", "proxied"}``; the URL comes back unchanged when no format applies. Admins only.
        """
        admin(request)
        from services.homehub import esphome_audio

        try:
            body = await request.json()
        except ValueError:
            return _error(400, "Data should be valid JSON.")
        if not isinstance(body, dict) or not str(body.get("url") or ""):
            return _error(400, "url is required")
        device = str(body.get("device") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device):
            return _error(400, "device must be 1 to 64 letters, digits, '-' or '_'")
        try:
            formats = [esphome_audio.AudioFormat.from_dict(f) for f in body.get("formats") or []]
        except (TypeError, ValueError, AttributeError):
            return _error(400, "formats must be a list of {format, sample_rate, num_channels, sample_bytes, purpose}")
        url = str(body["url"])
        out = esphome_audio.proxied_url(device, url, formats, announcement=bool(body.get("announcement")))
        return JSONResponse({"url": out, "proxied": out != url})

    @api.post("/api/tts_get_url")
    async def tts_get_url(request: Request) -> Response:
        """HA's "speak this and hand me a URL" (Music Assistant's text announcements use it)."""
        principal(request)
        from services.homehub import tts

        try:
            body = await request.json()
        except ValueError:
            return _error(400, "Data should be valid JSON.")
        if not isinstance(body, dict) or not str(body.get("message") or "").strip():
            return _error(400, "message is required")
        if not (body.get("engine_id") or body.get("platform")):
            return _error(400, "Must specify 'engine_id'")
        options = body.get("options") if isinstance(body.get("options"), dict) else {}
        try:
            url = await tts.make_clip(str(body["message"]), options.get("voice"))
        except HubError as exc:
            return _error(exc.status, str(exc))
        return JSONResponse({"url": url, "path": "/api/tts_proxy/" + url.rsplit("/api/tts_proxy/", 1)[1]})

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

    @api.get("/api/image_proxy/{entity_id}")
    async def image_proxy(entity_id: str, request: Request) -> Response:
        """HA's image entity picture: the state's rotating ``access_token`` or a signed-in caller."""
        _camera_auth(request, entity_id)
        ent = hub.entities.get(entity_id)
        if ent is None or ent.domain != "image" or ent.disabled_by:
            return _error(404, "Entity not found.")
        driver = hub.drivers.get(ent.config_entry_id or "")
        if driver is None or not hasattr(driver, "async_image"):
            return _error(503, "Image is not available")
        try:
            body, content_type = await driver.async_image(ent)
        except HubError as exc:
            return _error(exc.status, str(exc))
        return Response(body, media_type=content_type, headers={"Cache-Control": "no-store"})

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

    def _recordings_of(ent: Any) -> Any:
        """Who keeps this camera's recordings: the camera itself (Reolink), or the hub's motion recorder."""
        driver = hub.drivers.get(ent.config_entry_id or "")
        if _supports_recordings(driver, ent):
            return driver
        return camera_recording.recorder_for(hub, ent.entity_id)

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
            "recordings": _recordings_of(ent) is not None,
            "live": live,
            # "camera" when the stream is the camera's own video, "snapshots" when it is built from stills.
            "stream": "camera" if driver is not None and hasattr(driver, "async_open_mjpeg_stream") else "snapshots",
            "snapshot_interval": float(getattr(driver, "snapshot_interval", 1.0) or 1.0) if driver else None,
            # Clockwise degrees to turn the picture by, for a camera mounted on its side or upside down.
            "rotation": camera_rotation(ent),
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

    @api.get("/api/homehub/hidden_entities")
    async def homehub_hidden(request: Request) -> dict[str, Any]:
        """Entities left out of what people and Jarvis browse (hidden, or on a hidden device; duplicates.py).

        They still work: Jarvis drops them from its lists, caches and pickers, never from a command naming one.
        """
        principal(request)
        return {"entity_ids": hub.hidden_entity_ids()}

    @api.get("/api/homehub/exposed_entities")
    async def homehub_exposed(request: Request, entity_id: str | None = None) -> dict[str, Any]:
        """What voice and chat may control (``exposure.py``): one entity's answer, or the whole list."""
        from services.homehub import exposure

        principal(request)
        if entity_id is not None:
            ent = hub.entities.get(entity_id)
            st = hub.states.get(entity_id)
            name = hub.friendly_name(ent) if ent is not None else (st.attributes.get("friendly_name") if st else None)
            # ``name``: what Jarvis says when it has to refuse (never the entity id).
            return {"entity_id": entity_id, "exposed": exposure.is_exposed(hub, entity_id), "name": name}
        return {"assistant": exposure.ASSISTANT, "entity_ids": exposure.exposed_entities(hub)}

    def _recording_driver(entity_id: str) -> tuple[Any, Any]:
        ent = hub.entities.get(entity_id)
        if ent is None or ent.domain != "camera" or ent.disabled_by:
            raise HTTPException(404, detail="Entity not found.")
        driver = _recordings_of(ent)
        if driver is None:
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
        if entry.disabled_by:
            return JSONResponse({"status": "error", "message": f"{entry.title} is disabled; enable it first"},
                                status_code=409)
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
        from services.homehub import entries as entries_mod

        try:
            entries_mod.check_option_keys(entry, patch)
        except entries_mod.EntryError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from None
        for k, v in patch.items():
            if v is None:
                entry.options.pop(k, None)
            else:
                entry.options[k] = v
        runtime.store.save_config_entry(entry)
        if entry.disabled_by:
            # Saved for when it is turned back on; a disabled entry is never started.
            return {"entry_id": entry_id, "options": entry.options, "state": entry.state}
        await runtime.stop_entry(entry_id)
        runtime.start_entry(entry)
        return {"entry_id": entry_id, "options": entry.options, "state": "reloading"}

    app.include_router(api)
    app.include_router(discovery_api.build_router(runtime, auth))
    app.include_router(automation_api.build_router(runtime, auth))
    app.include_router(entries_api.build_router(runtime, auth))
    app.include_router(registries_api.build_router(runtime, auth))
    app.include_router(hardware_api.build_router(runtime, auth))
    app.include_router(capabilities_api.build_router(runtime, auth))
    app.include_router(live_video_api.build_router(runtime, auth))  # real-time camera video (go2rtc)
    app.include_router(camera_recording_api.build_router(runtime, auth))  # motion recording to Nextcloud
    app.include_router(statistics_api.build_router(runtime, auth))  # long-term statistics, energy
    app.include_router(dashboard_api.build_router(runtime, auth))  # Jarvis's Home/Climate pages
    app.include_router(ha_auth_api.build_router(runtime, auth))  # also starts the login providers
    app.include_router(users_api.build_router(runtime, auth))  # hub users and login providers (admins)
    app.include_router(mobile_app_api.build_router(runtime, auth))
    app.include_router(prune_api.build_router(runtime, auth))  # /hub/prune/plan, /hub/prune/apply
    app.include_router(service_logins_api.build_router(runtime, auth))  # service logins, Music Assistant link
    app.include_router(voice_api.build_router(runtime, auth))  # voice satellites (voice_pipeline.py)
    app.include_router(restarts_api.build_router(runtime, auth))  # ESPHome restarts (esphome_restarts.py)
    app.include_router(conversation.build_router(runtime, auth))  # sentence triggers, asked by Jarvis first
    app.include_router(matter_ble.build_router(runtime, auth))  # the household Wi-Fi for new Matter devices
    app.include_router(bluetooth_api.build_router(runtime, auth))  # Bluetooth scanners, devices, room map
    app.include_router(helpers_api.build_router(runtime, auth))  # HA's helpers
    app.include_router(light_groups_api.build_router(runtime, auth))  # light groups

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
            elif self.restricted and kind not in CORE_WS and kind not in LIMITED_WS:
                await self.error(mid, "unauthorized", "This account may not do that")
            elif kind == "get_states":
                p = self.principal
                await self.result(mid, [s.as_dict() for s in hub.states.all() if p is None or p.may_view(s.entity_id)])
            elif kind == "get_config":
                await self.result(mid, config_payload(self.runtime))
            elif kind == "get_services":
                await self.result(mid, {d["domain"]: d["services"] for d in hub.list_services()})
            elif kind == "call_service":
                data = dict(msg.get("service_data") or {})
                if msg.get("target"):
                    data["target"] = msg["target"]
                refused = call_allowed(hub, self.principal, msg["domain"], msg["service"], data)
                if refused:
                    await self.error(mid, "unauthorized", refused)
                    return
                res = await hub.call_service(msg["domain"], msg["service"], data,
                                             return_response=bool(msg.get("return_response")))
                await self.result(mid, {"context": {"id": mid, "parent_id": None, "user_id": None},
                                        "response": jsonable(res["response"])})
            elif kind == "subscribe_events":
                event_type = msg.get("event_type")

                async def forward(etype: str, data: dict[str, Any], _mid: int = mid) -> None:
                    if not _event_visible(self.principal, data):
                        return
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
            elif kind == "config/label_registry/list":
                from services.homehub import registries

                await self.result(mid, registries.list_labels(self.runtime)["labels"])
            elif kind == "config/device_registry/list":
                await self.result(mid, [d.public() for d in hub.devices.values()])
            elif kind == "config/entity_registry/list":
                await self.result(mid, [registry_view(e, self.principal) for e in hub.entities.values()
                                        if self._sees(e.entity_id)])
            elif kind == "config/entity_registry/get":
                ent = hub.entities.get(msg.get("entity_id", ""))
                if ent is None or not self._sees(ent.entity_id):
                    await self.error(mid, "not_found", "Entity not found")
                else:
                    await self.result(mid, registry_view(ent, self.principal))
            elif kind == "history/history_during_period":
                # HA's defaults: significant changes only, with each entity's state at the start time.
                start = parse_time(msg.get("start_time")) or datetime.now(UTC) - timedelta(days=1)
                found, truncated = await history_by_entity(
                    self.runtime, msg.get("entity_ids") or None, start, parse_time(msg.get("end_time")),
                    changes_only=bool(msg.get("significant_changes_only", True)),
                    start_state=bool(msg.get("include_start_time_state", True)))
                if truncated:
                    log.warning("history_during_period from %s held more rows than one answer carries; "
                                "the oldest were left out", start.isoformat())
                out: dict[str, list[dict[str, Any]]] = {}
                for eid, rows in found.items():
                    if not self._sees(eid):
                        continue
                    for i, r in enumerate(rows):
                        lu = datetime.fromisoformat(r["last_updated"]).timestamp()
                        lc = datetime.fromisoformat(r["last_changed"]).timestamp()
                        item: dict[str, Any] = {"s": r["state"], "lu": lu}
                        if not (msg.get("minimal_response") and i > 0):
                            item["a"] = {} if msg.get("no_attributes") else r["attributes"]
                        if lc != lu:
                            item["lc"] = lc
                        out.setdefault(eid, []).append(item)
                await self.result(mid, out)
            elif kind in WS_COMMANDS:
                if self.restricted and kind.startswith("config/entity_registry/"):
                    await WS_COMMANDS[kind](_FilteredConn(self), mid, msg)
                else:
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

    @property
    def restricted(self) -> bool:
        return self.principal is not None and self.principal.restricted

    def _sees(self, entity_id: str) -> bool:
        return self.principal is None or self.principal.may_view(entity_id)

    async def _subscribe_entities(self, mid: int, entity_ids: list[str] | None) -> None:
        """The compressed state feed newer HA clients use (``a`` added, ``c`` changed, ``r`` removed)."""
        wanted = set(entity_ids or [])

        def compress(s: State) -> dict[str, Any]:
            return {"s": s.state, "a": s.attributes, "c": s.context_id,
                    "lc": s.last_changed.timestamp(), "lu": s.last_updated.timestamp()}

        async def forward(_etype: str, data: dict[str, Any]) -> None:
            eid = data.get("entity_id")
            if (wanted and eid not in wanted) or not self._sees(str(eid or "")):
                return
            new = data.get("new_state")
            payload = {"r": [eid]} if new is None else {"a": {eid: compress(new)}}
            with contextlib.suppress(Exception):
                await self.send({"id": mid, "type": "event", "event": payload})

        self.subs[mid] = self.hub.bus.subscribe(forward, "state_changed")
        await self.result(mid)
        initial = {s.entity_id: compress(s) for s in self.hub.states.all()
                   if (not wanted or s.entity_id in wanted) and self._sees(s.entity_id)}
        await self.send({"id": mid, "type": "event", "event": {"a": initial}})


#: The WebSocket commands api.py answers itself; each one checks a limited user's entities.
CORE_WS = {"ping", "supported_features", "get_states", "get_config", "get_services", "call_service",
           "subscribe_events", "subscribe_entities", "unsubscribe_events", "config_entries/get",
           "config/area_registry/list", "config/floor_registry/list", "config/label_registry/list",
           "config/device_registry/list", "config/entity_registry/list", "config/entity_registry/get",
           "history/history_during_period"}


class _FilteredConn:
    """A connection whose answers drop the entities its limited user may not see (registry commands)."""

    def __init__(self, conn: WSConnection) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def result(self, mid: int, result: Any = None) -> None:
        sees = self._conn._sees
        if isinstance(result, dict) and isinstance(result.get("entities"), list):
            result = {**result, "entities": [e for e in result["entities"]
                                             if not isinstance(e, dict) or sees(str(e.get("ei") or e.get("entity_id") or ""))]}
        elif isinstance(result, dict):
            result = {k: v for k, v in result.items() if sees(str(k))}
        elif isinstance(result, list):
            result = [e for e in result if not isinstance(e, dict) or sees(str(e.get("entity_id") or ""))]
        await self._conn.result(mid, result)
