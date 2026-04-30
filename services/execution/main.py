# services/execution/main.py
import os
import logging
import asyncio
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, Header, Request
try:
    from . import ha_client
    from .schemas import (
        LightControlRequest, MediaPlayRequest, MediaTransportRequest,
        TVCastRequest, HAServiceRequest, AnnouncementRequest,
        CalendarRequest, NoteRequest, TimerRequest, ExecutionResult
    )
    from .handlers import light, media, climate, security, calendar, note, timer
except ImportError:
    from execution import ha_client
    from execution.schemas import (
        LightControlRequest, MediaPlayRequest, MediaTransportRequest,
        TVCastRequest, HAServiceRequest, AnnouncementRequest,
        CalendarRequest, NoteRequest, TimerRequest, ExecutionResult
    )
    from execution.handlers import light, media, climate, security, calendar, note, timer

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
log = logging.getLogger("execution")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

async def require_internal(request: Request, x_internal_secret: str = Header(None)):
    if request.url.path == "/health":
        return
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ─── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Execution Bridge starting up.")
    yield
    log.info("Execution Bridge shutting down.")

from fastapi.responses import JSONResponse
import traceback

app = FastAPI(
    title="SharedLLM Execution Bridge",
    version="1.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_internal)],
)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    err_msg = f"Execution Error: {type(exc).__name__}: {str(exc)}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "FAILURE", "message": err_msg, "service": "execution", "detail": traceback.format_exc().splitlines()[-3:]}
    )

def _ok(message: str, service: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service=service, detail=detail)

def _fail(message: str, service: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service=service, detail=detail)


# ─── Domain Endpoints ──────────────────────────────────────────────────────────

@app.post("/execute/light", response_model=ExecutionResult)
async def execute_light(req: LightControlRequest):
    return await light.handle_light(req)

@app.post("/execute/media/play", response_model=ExecutionResult)
async def execute_media_play(req: MediaPlayRequest):
    return await media.handle_media_play(req)

@app.post("/execute/media/transport", response_model=ExecutionResult)
async def execute_media_transport(req: MediaTransportRequest):
    return await media.handle_media_transport(req)

@app.post("/execute/tv_cast", response_model=ExecutionResult)
async def execute_tv_cast(req: TVCastRequest):
    return await media.handle_tv_cast(req)

@app.post("/execute/climate", response_model=ExecutionResult)
async def execute_climate(req: climate.ClimateRequest):
    return await climate.handle_climate(req)

@app.post("/execute/security", response_model=ExecutionResult)
async def execute_security(req: security.SecurityRequest):
    return await security.handle_security(req)

@app.post("/execute/calendar", response_model=ExecutionResult)
async def execute_calendar(req: CalendarRequest):
    return await calendar.handle_calendar(req)

@app.post("/execute/note", response_model=ExecutionResult)
async def execute_note(req: NoteRequest):
    return await note.handle_note(req)

@app.post("/execute/timer", response_model=ExecutionResult)
async def execute_timer(req: TimerRequest):
    return await timer.handle_timer(req)

@app.post("/execute/trigger", response_model=ExecutionResult)
async def execute_trigger(payload: Dict[str, Any]):
    """Internal endpoint for Automation scheduler."""
    timer_data = payload.get("timer", {})
    log.info(f"ALARM TRIGGERED: {timer_data.get('title')}")
    # Legacy: This is where we'd play audio on target_device
    return _ok(f"Triggered {timer_data.get('title')}", "automation_trigger")


# ─── Infrastructure Endpoints ───────────────────────────────────────────────────

@app.post("/execute/announce", response_model=ExecutionResult)
async def execute_announce(req: AnnouncementRequest):
    # Announcements are currently cross-domain (Volume + TTS)
    ctx = req.user_context
    log.info(f"[announce] user={ctx.user} entity={req.entity_id}")
    await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_set", req.entity_id, {"volume_level": req.volume})
    result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "tts", "speak", req.entity_id, {"message": req.message, "media_player_entity_id": req.entity_id})
    if result.get("ok"):
        return _ok(f"Announcement sent to {req.entity_id}.", "announce")
    return _fail(f"Announcement failed: {result.get('error')}", "announce", result)

@app.post("/execute/ha_service", response_model=ExecutionResult)
async def execute_ha_service(req: HAServiceRequest):
    ctx = req.user_context
    result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, req.domain, req.service, req.entity_id, req.service_data)
    if result.get("ok"):
        return _ok(f"{req.domain}.{req.service} executed.", "ha_service")
    return _fail(f"Service call failed: {result.get('error')}", "ha_service", result)

@app.get("/discovery/entities")
async def discovery_entities(ha_url: str, ha_token: str):
    return await ha_client.get_states(ha_url, ha_token)

@app.get("/health")
def health():
    return {"status": "ok", "service": "execution"}
