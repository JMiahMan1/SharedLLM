# services/execution/main.py
"""
Microservice 3: HA & Media Execution Bridge
Strictly validates and executes commands against Home Assistant.
All endpoints require X-Internal-Secret.
"""
import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Header, status

from schemas import (
    MediaPlayRequest, MediaTransportRequest,
    LightControlRequest, HAServiceRequest,
    AnnouncementRequest, TVCastRequest,
    ExecutionResult,
)
from  import ha_client

log = logging.getLogger("execution")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")


# ─── Security ──────────────────────────────────────────────────────────────────

def require_internal(x_internal_secret: str = Header(...)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ─── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Execution Bridge starting up.")
    yield
    log.info("Execution Bridge shutting down.")

app = FastAPI(
    title="SharedLLM Execution Bridge",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_internal)],
)


# ─── Helpers ───────────────────────────────────────────────────────────────────

async def _run(func, *args, **kwargs):
    """Run a blocking HA client call in a thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def _ok(message: str, service: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service=service, detail=detail)


def _fail(message: str, service: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service=service, detail=detail)


# ─── Media Endpoints ───────────────────────────────────────────────────────────

@app.post("/execute/media/play", response_model=ExecutionResult)
async def execute_media_play(req: MediaPlayRequest):
    ctx = req.user_context
    log.info(f"[media/play] user={ctx.user} entity={req.entity_id} query={req.media_content_id or req.query}")

    service_data: dict = {}
    if req.media_content_id:
        service_data["media_content_id"] = req.media_content_id
        service_data["media_content_type"] = req.media_content_type
    if req.enqueue:
        service_data["enqueue"] = req.enqueue

    result = await _run(
        ha_client.call_service,
        ctx.ha_url, ctx.ha_token,
        "media_player", "play_media",
        req.entity_id, service_data,
    )
    if result.get("ok"):
        return _ok(f"Playing on {req.entity_id}.", "media_play")
    return _fail(f"Failed to play on {req.entity_id}: {result.get('error')}", "media_play", result)


@app.post("/execute/media/transport", response_model=ExecutionResult)
async def execute_media_transport(req: MediaTransportRequest):
    ctx = req.user_context
    log.info(f"[media/transport] user={ctx.user} entity={req.entity_id} cmd={req.command}")

    # Volume commands use a different HA service
    if req.command in ("volume_up", "volume_down") and req.volume_level is not None:
        result = await _run(
            ha_client.call_service,
            ctx.ha_url, ctx.ha_token,
            "media_player", "volume_set",
            req.entity_id, {"volume_level": req.volume_level},
        )
    else:
        ha_service_map = {
            "pause": "media_pause",
            "resume": "media_play",
            "stop": "media_stop",
            "next": "media_next_track",
            "previous": "media_previous_track",
            "volume_up": "volume_up",
            "volume_down": "volume_down",
        }
        service = ha_service_map.get(req.command, req.command)
        result = await _run(
            ha_client.call_service,
            ctx.ha_url, ctx.ha_token,
            "media_player", service,
            req.entity_id,
        )

    if result.get("ok"):
        return _ok(f"Transport '{req.command}' executed on {req.entity_id}.", "media_transport")
    return _fail(f"Transport command failed: {result.get('error')}", "media_transport", result)


# ─── Light Endpoints ───────────────────────────────────────────────────────────

@app.post("/execute/light", response_model=ExecutionResult)
async def execute_light(req: LightControlRequest):
    ctx = req.user_context
    log.info(f"[light] user={ctx.user} entity={req.entity_id} action={req.action}")

    service_data: dict = {}
    if req.brightness_pct is not None:
        service_data["brightness_pct"] = req.brightness_pct
    if req.color_temp is not None:
        service_data["color_temp"] = req.color_temp
    if req.rgb_color is not None:
        service_data["rgb_color"] = list(req.rgb_color)

    result = await _run(
        ha_client.call_service,
        ctx.ha_url, ctx.ha_token,
        "light", req.action,
        req.entity_id, service_data or None,
    )
    if result.get("ok"):
        return _ok(f"Light '{req.action}' executed on {req.entity_id}.", "light_control")
    return _fail(f"Light command failed: {result.get('error')}", "light_control", result)


# ─── Generic HA Service ────────────────────────────────────────────────────────

@app.post("/execute/ha_service", response_model=ExecutionResult)
async def execute_ha_service(req: HAServiceRequest):
    ctx = req.user_context
    log.info(f"[ha_service] user={ctx.user} {req.domain}.{req.service} → {req.entity_id}")

    result = await _run(
        ha_client.call_service,
        ctx.ha_url, ctx.ha_token,
        req.domain, req.service,
        req.entity_id, req.service_data,
    )
    if result.get("ok"):
        return _ok(f"{req.domain}.{req.service} on {req.entity_id} executed.", "ha_service")
    return _fail(f"Service call failed: {result.get('error')}", "ha_service", result)


# ─── Announcements ─────────────────────────────────────────────────────────────

@app.post("/execute/announce", response_model=ExecutionResult)
async def execute_announce(req: AnnouncementRequest):
    ctx = req.user_context
    log.info(f"[announce] user={ctx.user} entity={req.entity_id} msg={req.message[:60]}")

    # 1. Set volume
    await _run(
        ha_client.call_service,
        ctx.ha_url, ctx.ha_token,
        "media_player", "volume_set",
        req.entity_id, {"volume_level": req.volume},
    )

    # 2. Play TTS
    result = await _run(
        ha_client.call_service,
        ctx.ha_url, ctx.ha_token,
        "tts", "speak",
        req.entity_id, {
            "message": req.message,
            "media_player_entity_id": req.entity_id,
        },
    )
    if result.get("ok"):
        return _ok(f"Announcement sent to {req.entity_id}.", "announce")
    return _fail(f"Announcement failed: {result.get('error')}", "announce", result)


# ─── SmartPowerSync TV Cast ────────────────────────────────────────────────────

@app.post("/execute/tv_cast", response_model=ExecutionResult)
async def execute_tv_cast(req: TVCastRequest):
    """
    SmartPowerSync: power the TV on (if off), wait, then cast.
    """
    ctx = req.user_context
    log.info(f"[tv_cast] user={ctx.user} entity={req.media_player_entity_id}")

    # Check power state
    state = await _run(ha_client.get_state, ctx.ha_url, ctx.ha_token, req.media_player_entity_id)
    if state and state.get("state") not in ("on", "playing", "paused", "idle"):
        log.info(f"[tv_cast] Device is off — powering on first.")
        await _run(
            ha_client.call_service,
            ctx.ha_url, ctx.ha_token,
            "media_player", "turn_on",
            req.media_player_entity_id,
        )
        await asyncio.sleep(req.power_on_wait_ms / 1000)

    result = await _run(
        ha_client.call_service,
        ctx.ha_url, ctx.ha_token,
        "media_player", "play_media",
        req.media_player_entity_id,
        {"media_content_id": req.media_content_id, "media_content_type": req.media_content_type},
    )
    if result.get("ok"):
        return _ok(f"TV cast started on {req.media_player_entity_id}.", "tv_cast")
    return _fail(f"TV cast failed: {result.get('error')}", "tv_cast", result)


# ─── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "execution"}
