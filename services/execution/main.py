# services/execution/main.py
import asyncio
import logging
import os
import time
import traceback
import warnings
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import aiohttp
from services.common.http import get_client
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Suppress InsecureRequestWarning for internal self-signed certs (homelab)
from urllib3.exceptions import InsecureRequestWarning

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

import threading

from services.config import (
    IDENTITY_SVC_URL,
    INTERNAL_SECRET,
    OLLAMA_URL,
)

# Now import everything from sibling modules
from services.execution import device_registry, ha_client
from services.execution.announce_handlers import detect_tv_type as _detect_tv_type
from services.execution.handlers import (
    audiobookshelf,
    browser,
    calendar,
    climate,
    composite,
    diagnostics,
    groups,
    learning,
    light,
    media,
    note,
    security,
    storage,
    talk,
    timer,
    video,
    workspace,
)
from services.execution.handlers import deployment as deployment_handler
from services.execution.handlers import docker_logs as docker_logs_handler
from services.execution.handlers import gh as gh_handler
from services.execution.handlers import git as git_handler
from services.execution.handlers import ha_config as ha_config_handler
from services.execution.handlers import volumes as volume_handler
from services.execution.media_playback_service import MediaPlaybackService
from services.execution.schemas import (
    AnnouncementRequest,
    AudiobookshelfRequest,
    CalendarRequest,
    DeploymentRequest,
    DiagnosticRequest,
    DiscoverySyncRequest,
    DockerComposeRequest,
    DockerLogsRequest,
    EntitySearchRequest,
    ExecutionLogRequest,
    ExecutionResult,
    GhRequest,
    GitOperationRequest,
    HAConfigRequest,
    HAServiceRequest,
    IdentityManageRequest,
    IdentityRequest,
    LightControlRequest,
    LLMInfoRequest,
    LogbookRequest,
    MediaPlayRequest,
    MediaStateSyncRequest,
    MediaStatusRequest,
    MediaTransportRequest,
    NetworkDeviceScanRequest,
    NoteRequest,
    ResolveStreamRequest,
    StorageFileReadRequest,
    StorageFileWriteRequest,
    StorageTextToAudioRequest,
    SystemLearningRequest,
    TalkRequest,
    TimerRequest,
    TTSRequest,
    TVCastRequest,
    UserContext,
    VideoPlayRequest,
    VolumeInventoryRequest,
    WebReadRequest,
    WebSearchRequest,
    WorkspaceFilePatchRequest,
    WorkspaceFileReadRequest,
    WorkspaceFileWriteRequest,
    WorkspaceLintRequest,
    WorkspaceSearchRequest,
    WorkspaceShellRequest,
)
from services.execution.tts import text_to_speech as _text_to_speech
from services.shared.info_endpoint import info_router

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
log = logging.getLogger("execution")

# Expose for test mocking
text_to_speech = _text_to_speech
detect_tv_type = _detect_tv_type

async def resolve_internal_user(user_id: int | None = None, rag_user: str | None = None) -> dict[str, Any] | None:
    """Query Identity Service for full user credentials using internal secret.
    
    If called with a single positional arg, auto-detect: int -> user_id param, str -> rag_user param.
    """
    payload: dict[str, Any] = {}
    if user_id is not None:
        payload["user_id"] = user_id
    if rag_user is not None:
        payload["rag_user"] = rag_user
    try:
        async with get_client() as client:
            resp = await client.post(
                f"{IDENTITY_SVC_URL}/api/resolve",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        sample_user = user_id if user_id is not None else rag_user
        log.error(f"Failed to resolve internal user {sample_user}: {e}")
    return None


async def resolve_first_user() -> dict[str, Any] | None:
    """Resolve the first (ID=1) user in the system — the system default."""
    return await resolve_internal_user(user_id=1)

async def require_internal(request: Request, x_internal_secret: str = Header(None)):
    if request.url.path == "/health" or request.url.path.startswith("/media/"):
        return

    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def _check_internal_secret(x_internal_secret: str | None):
    """Simple secret check for endpoints without Request dependency."""
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ─── App ───────────────────────────────────────────────────────────────────────

async def telemetry_ingestion_loop(interval_seconds: int = 60):
    """Background task: periodically read each enrolled power/energy entity's
    current HA state and ingest it as a telemetry snapshot, so the Energy
    Insights dashboard reflects *live* usage instead of stale manual data.

    Resilient by design: any single failure (HA down, bad entity, identity
    error) is caught per-entity and the loop keeps running.
    """
    log.info("[telemetry] ingestion loop starting (interval=%ss)", interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            creds = await resolve_internal_user()
            ha_url = creds.get("ha_url") if creds else None
            ha_token = creds.get("ha_token") if creds else None
            if not ha_url or not ha_token:
                continue

            # Fetch enrollments (those flagged for power tracking)
            try:
                async with get_client() as session, session.get(
                    f"{IDENTITY_SVC_URL}/api/telemetry/enroll",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    enroll_data = await resp.json()
            except Exception as ex:
                log.warning(f"[telemetry] could not fetch enrollments: {ex}")
                continue

            enrollments = enroll_data.get("enrollments", [])
            power_enrollments = [e for e in enrollments if e.get("power_tracking")]

            for e in power_enrollments:
                entity_id = e.get("entity_id")
                if not entity_id:
                    continue
                try:
                    state = await ha_client.get_state(ha_url, ha_token, entity_id)
                    if not state:
                        continue
                    raw = state.get("state")
                    is_available = raw not in (None, "unavailable", "unknown", "none", "")
                    try:
                        power_w = float(raw) if raw not in (None, "unavailable", "unknown") else None
                    except (TypeError, ValueError):
                        power_w = None
                    snapshot = {
                        "entity_id": entity_id,
                        "power_w": power_w,
                        "is_available": is_available,
                        "state": raw,
                        "source": "ha-poll",
                    }
                    async with get_client() as session, session.post(
                        f"{IDENTITY_SVC_URL}/api/telemetry/snapshot",
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        json=snapshot,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            log.warning(f"[telemetry] snapshot ingest failed for {entity_id}: {resp.status}")
                except Exception as ex:
                    log.warning(f"[telemetry] failed to ingest {entity_id}: {ex}")
        except asyncio.CancelledError:
            log.info("[telemetry] ingestion loop cancelled")
            break
        except Exception as ex:
            log.error(f"[telemetry] ingestion loop error: {ex}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Patch DNS resolver to route .local domains through dns-sync for live failover
    try:
        from dns_resolver import patch_dns_resolver
        patch_dns_resolver()
    except Exception as e:
        log.warning(f"DNS resolver patch failed: {e}")

    # Resolve runtime config from Identity service
    from services.config import resolve_runtime_config
    await resolve_runtime_config()

    log.info("Execution Bridge starting up.")
    # Auto-download Kokoro models if missing
    from services.config import MODELS_DIR
    os.makedirs(MODELS_DIR, exist_ok=True)
    kokoro_path = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
    voices_path = os.path.join(MODELS_DIR, "voices-v1.0.bin")
    if not os.path.exists(kokoro_path) or not os.path.exists(voices_path):
        log.info("Downloading default Kokoro TTS models...")
        import subprocess
        try:
            if not os.path.exists(kokoro_path):
                subprocess.run(["curl", "-L", "-o", kokoro_path, "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"], check=True)
            if not os.path.exists(voices_path):
                subprocess.run(["curl", "-L", "-o", voices_path, "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"], check=True)
            log.info("Kokoro models downloaded successfully.")
        except Exception as e:
            log.error(f"Failed to auto-download Kokoro models: {e}")

    # Start FastAPI media server on port 8888 for external access
    # Uses FileResponse (same as main branch) for proper HTTP Range support
    def run_media_server():
        import threading

        import uvicorn
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse, Response

        media_app = FastAPI(title="Media Server")

        @media_app.get("/media/{media_id}")
        @media_app.head("/media/{media_id}")
        async def serve_media(media_id: str):  # pyright: ignore[reportUnusedFunction]
            # TTS audio from memory cache
            if media_id in TEMP_AUDIO_CACHE:
                return Response(
                    content=TEMP_AUDIO_CACHE[media_id],
                    media_type="audio/wav",
                    headers={"Accept-Ranges": "bytes"},
                )

            # TTS audio from disk (.wav)
            wav_path = os.path.join(TEMP_AUDIO_DIR, f"{media_id}.wav")
            if os.path.exists(wav_path):
                return FileResponse(
                    path=wav_path,
                    media_type="audio/wav",
                    headers={"Accept-Ranges": "bytes"},
                )

            # Video files from disk (.mp4) — FileResponse handles Range automatically
            mp4_path = os.path.join(TEMP_MEDIA_DIR, f"{media_id}.mp4")
            if os.path.exists(mp4_path):
                return FileResponse(
                    path=mp4_path,
                    media_type="video/mp4",
                    headers={
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "no-cache",
                    },
                )

            # Progressive download: serve .mp4.part files while download is in progress
            part_path = os.path.join(TEMP_MEDIA_DIR, f"{media_id}.mp4.part")
            if os.path.exists(part_path):
                return FileResponse(
                    path=part_path,
                    media_type="video/mp4",
                    headers={
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "no-cache",
                    },
                )

            raise HTTPException(status_code=404, detail="Media not found")

        def _run():
            uvicorn.run(media_app, host="0.0.0.0", port=8888, log_level="warning")

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        log.info("Media server (FastAPI) running on port 8888")
        return thread

    media_server: threading.Thread = run_media_server()

    # Background telemetry ingestion (live Energy Insights usage)
    telemetry_task = asyncio.create_task(telemetry_ingestion_loop())

    yield
    telemetry_task.cancel()
    try:
        await telemetry_task
    except Exception:
        pass
    media_server.join(timeout=5)
    log.info("Execution Bridge shutting down.")



app = FastAPI(
    title="SharedLLM Execution Bridge",
    version="1.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_internal)],
)

app.include_router(info_router)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    err_msg = f"Execution Error: {type(exc).__name__}: {exc!s}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "FAILURE", "message": err_msg, "service": "execution", "detail": traceback.format_exc().splitlines()[-3:]}
    )


# Transient cache for locally-generated media (TTS announcements)
TEMP_AUDIO_CACHE: dict[str, bytes] = {}
# Persistent disk cache for TTS files (survives container restarts)
from services.config import TEMP_MEDIA_DIR

TEMP_AUDIO_DIR = os.path.join(TEMP_MEDIA_DIR, "tts")
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
os.makedirs(TEMP_MEDIA_DIR, exist_ok=True)

def get_public_host():
    """Resolve the public host for media URLs (for external device access)."""
    from services.config import EXECUTION_EXTERNAL_HOST
    env_host = EXECUTION_EXTERNAL_HOST
    if env_host:
        return env_host
    try:
        with open("docker-compose.yml") as f:
            for line in f:
                if "ai-server:" in line:
                    return line.split(":")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    raise RuntimeError("EXECUTION_EXTERNAL_HOST is not set and no compose IP was discovered.")

async def verify_playback(ha_url: str, ha_token: str, entity_id: str, expected_media_url: str, timeout: int = 10, device_type: str = "unknown") -> dict[str, Any]:
    """Verify that a media player actually started playing the expected content.
    
    For speakers/Chromecast: requires 'playing' state with matching media URL.
    For TVs: accepts 'on' or 'idle' state since TVs don't always report 'playing' for audio.
    
    Returns dict with: verified (bool), state, media_content_id, app_name, detail
    """
    import time
    start = time.time()
    is_tv = device_type in ("roku", "samsung", "webos", "android_tv", "bravia", "generic_tv")
    state_resp: dict | None = None

    while time.time() - start < timeout:
        state_resp = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state_resp:
            await asyncio.sleep(0.5)
            continue

        current_state = state_resp.get("state", "unknown")
        attrs = state_resp.get("attributes", {})
        current_media = attrs.get("media_content_id", "")
        app_name = attrs.get("app_name", attrs.get("app_id", ""))

        # For TVs: accept 'on' or 'idle' as success (TVs don't report 'playing' for audio)
        if is_tv and current_state in ("on", "idle", "playing"):
            log.info(f"[verify_playback] TV device {entity_id} state='{current_state}' (acceptable for TV)")
            return {
                "verified": True,
                "state": current_state,
                "media_content_id": current_media,
                "app_name": app_name,
                "detail": f"Playback confirmed on TV (state='{current_state}')"
            }

        # For speakers/Chromecast: require 'playing' state with matching media URL
        if current_state == "playing" and expected_media_url in str(current_media):
            log.info(f"[verify_playback] CONFIRMED playing: {entity_id} -> {current_media[:60]}")
            await asyncio.sleep(1)
            return {
                "verified": True,
                "state": current_state,
                "media_content_id": current_media,
                "app_name": app_name,
                "detail": "Playback confirmed via state transition to 'playing'"
            }

        await asyncio.sleep(0.5)

    # Timeout without seeing acceptable state
    last_state = state_resp.get("state", "unknown") if state_resp else "unknown"
    last_media = state_resp.get("attributes", {}).get("media_content_id", "") if state_resp else ""
    return {
        "verified": False,
        "state": last_state,
        "media_content_id": last_media,
        "app_name": "",
        "detail": f"Playback not confirmed within {timeout}s. Last state: {last_state}"
    }

@app.get("/media/{media_id}")
async def get_temp_media(media_id: str):
    """Serves transient audio/video files for HA playback."""
    from fastapi.responses import FileResponse, Response

    # Check audio cache first
    if media_id in TEMP_AUDIO_CACHE:
        return Response(content=TEMP_AUDIO_CACHE[media_id], media_type="audio/wav")

    # Check video files on disk
    video_path = os.path.join(TEMP_MEDIA_DIR, f"{media_id}.mp4")
    if os.path.exists(video_path):
        return FileResponse(
            video_path,
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            }
        )

    # Return 503 (Service Unavailable) for media that's being prepared
    # This tells HA to retry instead of treating it as a permanent 404
    raise HTTPException(
        status_code=503,
        detail="Media not ready or expired"
    )

def _ok(message: str, service: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service=service, detail=detail)

def _fail(message: str, service: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service=service, detail=detail)

async def verify_entity_access(ctx: UserContext, entity_id: str) -> bool:
    """
    Checks if the user has permission to control this entity.
    Admins bypass all checks.
    """
    if ctx.is_admin:
        return True

    # In a stricter system, we would call Identity service here to check DeviceAssignment.
    # For now, we allow the request but log it.
    log.info(f"Access check for {ctx.user} on {entity_id}: ALLOWED (Implicit)")
    return True


# ─── Domain Endpoints ──────────────────────────────────────────────────────────

@app.post("/execute/light", response_model=ExecutionResult)
async def execute_light(req: LightControlRequest):
    if not await verify_entity_access(req.user_context, req.entity_id):
        raise HTTPException(status_code=403, detail="Access denied to this device")
    return await light.handle_light(req)

@app.post("/execute/media/play", response_model=ExecutionResult)
async def execute_media_play(req: MediaPlayRequest):
    if req.entity_id and req.entity_id.lower() not in ("local", "web_player", "browser", "android"):
        if not await verify_entity_access(req.user_context, req.entity_id):
            raise HTTPException(status_code=403, detail="Access denied to this device")
    # Resolve HA credentials via Identity service if not in context
    ctx = req.user_context
    if not ctx.ha_url or not ctx.ha_token:
        creds = await resolve_first_user()
        if creds:
            ctx.ha_url = ctx.ha_url or creds.get("ha_url", "")
            ctx.ha_token = ctx.ha_token or creds.get("ha_token", "")
    return await MediaPlaybackService.play(req)


@app.post("/execute/media/resolve_stream", response_model=ExecutionResult)
async def execute_media_resolve_stream(req: ResolveStreamRequest):
    from services.execution.handlers import video as video_handler

    query = req.query
    log.info(f"[media/resolve_stream] Resolving audio stream URL for query='{query}'")

    # Step 1: Find a YouTube page URL for the query
    video_url = await video_handler.search_youtube(query)
    if not video_url:
        return ExecutionResult(status="FAILURE", message="Could not find stream on YouTube", service="resolve_stream")

    # Step 2: Extract a direct, in-memory audio stream URL — no download needed
    stream_url, title = await video_handler.extract_audio_stream_url(video_url)
    if not stream_url:
        log.warning(f"[media/resolve_stream] Audio URL extraction failed for '{video_url}', falling back to progressive download")
        # Fallback: progressive video download (slower but reliable)
        from services.config import EXECUTION_EXTERNAL_HOST
        media_id, dl_title = await video_handler.download_video_progressive(video_url)
        if not media_id or not EXECUTION_EXTERNAL_HOST:
            return ExecutionResult(status="FAILURE", message="Could not resolve audio stream", service="resolve_stream")
        stream_url = f"http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}"
        title = dl_title or query

    log.info(f"[media/resolve_stream] Resolved: title='{title}' stream_url='{stream_url[:80]}...'")
    return ExecutionResult(
        status="SUCCESS",
        message=f"Resolved stream: {title}",
        service="resolve_stream",
        detail={"stream_url": stream_url, "title": title}
    )


@app.post("/execute/media/transport", response_model=ExecutionResult)
async def execute_media_transport(req: MediaTransportRequest):
    ctx = req.user_context
    if not ctx.ha_url or not ctx.ha_token:
        creds = await resolve_first_user()
        if creds:
            ctx.ha_url = ctx.ha_url or creds.get("ha_url", "")
            ctx.ha_token = ctx.ha_token or creds.get("ha_token", "")
    return await MediaPlaybackService.transport(req)

@app.post("/execute/tv_cast", response_model=ExecutionResult)
async def execute_tv_cast(req: TVCastRequest):
    return await media.handle_tv_cast(req)

@app.post("/execute/climate", response_model=ExecutionResult)
async def execute_climate(req: climate.ClimateRequest):
    if not await verify_entity_access(req.user_context, req.entity_id):
        raise HTTPException(status_code=403, detail="Access denied to this device")
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

@app.post("/execute/talk", response_model=ExecutionResult)
async def execute_talk(req: TalkRequest):
    return await talk.handle_talk(req)

@app.post("/execute/identity", response_model=ExecutionResult)
async def execute_identity(req: IdentityRequest):
    """
    Proxy user management actions to the Identity service.
    """
    action = req.action
    log.info(f"[identity] Proxying action={action} for user={req.user_context.user}")

    try:
        async with get_client() as client:
            headers = {"X-Internal-Secret": INTERNAL_SECRET}
            if action == "import_nextcloud":
                resp = await client.post(f"{IDENTITY_SVC_URL}/api/auth/import/nextcloud", headers=headers, timeout=aiohttp.ClientTimeout(total=30.0))
            elif action == "discover":
                resp = await client.get(f"{IDENTITY_SVC_URL}/api/auth/discover", headers=headers, timeout=aiohttp.ClientTimeout(total=30.0))
            elif action == "list":
                resp = await client.get(f"{IDENTITY_SVC_URL}/api/users", headers=headers, timeout=aiohttp.ClientTimeout(total=30.0))
            elif action == "create":
                payload = {
                    "username": req.username,
                    "display_name": req.display_name or req.username,
                    "is_admin": req.is_admin
                }
                resp = await client.post(f"{IDENTITY_SVC_URL}/api/users", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30.0))
            elif action == "delete":
                resp = await client.delete(f"{IDENTITY_SVC_URL}/api/users/{req.username}", headers=headers, timeout=aiohttp.ClientTimeout(total=30.0))
            else:
                return _fail(f"Action {action} not supported", "identity")

            if resp.status in (200, 201, 204):
                data = await resp.json() if resp.status != 204 else {}
                msg = f"Identity action '{action}' successful."
                if isinstance(data, list):
                    msg += f" Found {len(data)} results."
                elif isinstance(data, dict) and "message" in data:
                    msg = data["message"]
                return _ok(msg, "identity", {"data": data})
            else:
                return _fail(f"Identity service returned {resp.status}: {resp.text}", "identity")
    except Exception as e:
        log.error(f"Identity proxy error: {e}")
        return _fail(f"Identity proxy failed: {e}", "identity")

@app.post("/execute/web_search", response_model=ExecutionResult)
async def execute_web_search(req: WebSearchRequest):
    return await browser.handle_web_search(req)

@app.post("/execute/web_read", response_model=ExecutionResult)
async def execute_web_read(req: WebReadRequest):
    return await browser.handle_web_read(req)

@app.get("/execute/timers")
async def list_timers(user_id: str | None = None):
    return await timer.get_active_timers(user_id=user_id)

@app.post("/execute/trigger", response_model=ExecutionResult)
async def execute_trigger(payload: dict[str, Any]):
    """Internal endpoint for Automation scheduler."""
    timer_data = payload.get("timer", {})
    user_id = payload.get("user_id") or timer_data.get("user_id")

    log.info(f"ALARM TRIGGERED: {timer_data.get('title')} for user {user_id}")

    if not user_id:
        return _ok(f"Triggered {timer_data.get('title')} (no user context)", "automation_trigger")

    # Resolve full credentials for the user (to get HA token)
    if isinstance(user_id, int):
        creds = await resolve_internal_user(user_id=user_id)
    else:
        creds = await resolve_internal_user(rag_user=str(user_id))
    if not creds:
        log.error(f"Trigger failed: could not resolve credentials for user {user_id}")
        return _ok(f"Triggered {timer_data.get('title')} (failed to resolve creds)", "automation_trigger")

    # Execute the actual alert via Home Assistant
    try:
        # Construct a fake UserContext for the handler
        context = UserContext(**creds)
        target_device = timer_data.get("target_device")

        if target_device:
            log.info(f"Dispatching media alert to {target_device}")
            await media.handle_media_play(
                MediaPlayRequest(
                    user_context=context,
                    entity_id=target_device,
                    query="Timer " + str(timer_data.get("title", "")) + " is done",
                    media_type="announcement",
                    device_name=target_device,
                    media_content_id=None,
                    media_content_type=None,
                    enqueue="replace",
                    volume=None,
                )
            )

        return _ok(f"Triggered {timer_data.get('title')} on {target_device}", "automation_trigger")
    except Exception as e:
        log.error(f"Trigger execution error: {e}")
        return _ok(f"Triggered {timer_data.get('title')} but alert failed: {e}", "automation_trigger")


# ─── Ouroboros Autonomous Loop Endpoints ──────────────────────────────────────────────────

@app.post("/execute/docker_logs")
async def execute_docker_logs(req: DockerLogsRequest):
    """
    Fetch and optionally filter Docker container logs.
    Part of the Ouroboros telemetry/OBSERVE phase.
    Container name must start with 'sharedllm_'.
    """
    return await docker_logs_handler.handle_docker_logs(req)


@app.get("/execute/docker_containers")
async def list_docker_containers():
    """
    List all sharedllm_* containers and their statuses.
    Used by the autonomous loop to understand the deployment landscape.
    """
    return await docker_logs_handler.handle_list_containers(type('Req', (), {})())


@app.post("/execute/git")
async def execute_git(req: GitOperationRequest):
    res = await git_handler.handle_git(req)
    if res.status == "FAILURE":
        raise HTTPException(status_code=400, detail=res.message)
    return res


@app.post("/execute/gh", response_model=ExecutionResult)
async def execute_gh(req: GhRequest):
    """Run a `gh` (GitHub CLI) command inside a workspace. Raven/tool surface."""
    res = await gh_handler.handle_gh(req)
    if res["status"] == "FAILURE":
        raise HTTPException(status_code=400, detail=res["message"])
    return res


@app.post("/execute/deploy")
async def execute_deploy(req: DeploymentRequest):
    """
    Control SharedLLM Docker containers: restart, status, logs, list.
    Communicates via /var/run/docker.sock.
    Part of the Ouroboros ACT/OBSERVE phase.
    """
    return await deployment_handler.handle_deployment(req)


@app.post("/execute/docker")
async def execute_docker(req: DockerComposeRequest):
    """
    Multi-service Docker control (restart, up, down, logs).
    """
    # Reuse deployment_handler but loop over services
    results = []
    services = req.services or req.containers or []
    if not services:
        return _fail("No services or containers specified", "docker")

    if req.action == "up":
        import subprocess
        log.info(f"[docker] Running docker-compose up -d --build for services: {services}")
        try:
            # We run this from the workspace root where docker-compose.yml is
            cmd = ["docker-compose", "up", "-d", "--build"] + list(services)
            from services.config import COMPOSE_PROJECT_DIR
            compose_dir = COMPOSE_PROJECT_DIR or os.path.expanduser("~/workspace")
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=compose_dir)
            if res.returncode == 0:
                return _ok(f"Docker Compose up -d --build successful for {len(services)} services.", "docker", {"output": res.stdout})
            else:
                return _fail(f"Docker Compose up failed (code {res.returncode})", "docker", {"error": res.stderr, "output": res.stdout})
        except Exception as e:
            return _fail(f"Subprocess error during docker-compose up: {e}", "docker")

    for svc in services:
        # Ensure prefix
        container_name = svc if svc.startswith("sharedllm_") else f"sharedllm_{svc}"
        mock_req = DeploymentRequest(
            user_context=req.user_context,
            action=req.action if req.action in ("restart", "logs") else "restart",
            container_name=container_name,
            tail=100
        )
        res = await deployment_handler.handle_deployment(mock_req)
        results.append(res)

    return _ok(f"Docker action '{req.action}' applied to {len(services)} services.", "docker", {"results": results})


@app.post("/execute/volumes")
async def execute_volumes(req: VolumeInventoryRequest):
    """
    Inspect tracked Docker volumes, sizes, and backup/prune examples.
    Admin only.
    """
    return await volume_handler.handle_volumes(req)


def _normalize_llm_body(body: dict) -> dict:
    """Handle common LLM hallucinations: nested body/payload wrappers, action keys."""
    # Unwrap nested body/payload
    if "body" in body and isinstance(body["body"], dict):
        inner = body.pop("body")
        body.update(inner)
    if "payload" in body and isinstance(body["payload"], dict):
        inner = body.pop("payload")
        body.update(inner)
    # Remove non-schema keys
    body.pop("action", None)
    body.pop("operation", None)
    return body


@app.post("/execute/workspace_search", response_model=ExecutionResult)
async def execute_workspace_search(request: Request):
    """Accept workspace_search with hallucinated field names and normalize them."""
    body = await request.json()
    body = _normalize_llm_body(body)

    # Handle nested query object (LLM sometimes nests search params inside query)
    if "query" in body and isinstance(body["query"], dict):
        q_obj = body.pop("query")
        if "search_term" in q_obj:
            body["query"] = q_obj["search_term"]
        elif "search_query" in q_obj:
            body["query"] = q_obj["search_query"]
        elif "pattern" in q_obj:
            body["query"] = q_obj["pattern"]
        elif "text" in q_obj:
            body["query"] = q_obj["text"]
        else:
            body["query"] = str(q_obj)
        if "file_type" in q_obj and "include" not in body:
            ft = q_obj["file_type"]
            body["include"] = f"*.{ft}" if not ft.startswith("*") else ft
        if "file_pattern" in q_obj and "include" not in body:
            body["include"] = q_obj["file_pattern"]
        if "path" in q_obj and "path" not in body:
            body["path"] = q_obj["path"]
        if "directory" in q_obj and "path" not in body:
            body["path"] = q_obj["directory"]

    # Normalize common hallucinated field names
    if "search_term" in body and "query" not in body:
        body["query"] = body.pop("search_term")
    if "search_query" in body and "query" not in body:
        body["query"] = body.pop("search_query")
    if "pattern" in body and "query" not in body:
        body["query"] = body.pop("pattern")
    if "file_type" in body and "include" not in body:
        ft = body.pop("file_type")
        body["include"] = f"*.{ft}" if not ft.startswith("*") else ft
    if "file_pattern" in body and "include" not in body:
        body["include"] = body.pop("file_pattern")
    if "directory" in body and "path" not in body:
        body["path"] = body.pop("directory")
    if "search_path" in body and "path" not in body:
        body["path"] = body.pop("search_path")

    # Ensure user_context exists
    if "user_context" not in body:
        body["user_context"] = {"user": "", "is_admin": True}

    req = WorkspaceSearchRequest(**body)
    return await workspace.handle_workspace_search(req)

@app.post("/execute/workspace_shell", response_model=ExecutionResult)
async def execute_workspace_shell(req: WorkspaceShellRequest):
    return await workspace.handle_workspace_shell(req)

@app.post("/execute/workspace_file_read", response_model=ExecutionResult)
async def execute_workspace_file_read(request: Request):
    body = await request.json()
    body = _normalize_llm_body(body)
    if "file_path" in body and "path" not in body:
        body["path"] = body.pop("file_path")
    if "filename" in body and "path" not in body:
        body["path"] = body.pop("filename")
    if "user_context" not in body:
        body["user_context"] = {"user": "", "is_admin": True}
    req = WorkspaceFileReadRequest(**body)
    res = await workspace.handle_workspace_read(req)
    if res.status == "FAILURE":
        raise HTTPException(status_code=404, detail=res.message)
    return res

@app.post("/execute/workspace_file_write", response_model=ExecutionResult)
async def execute_workspace_file_write(request: Request):
    body = await request.json()
    body = _normalize_llm_body(body)
    if "file_path" in body and "path" not in body:
        body["path"] = body.pop("file_path")
    if "filename" in body and "path" not in body:
        body["path"] = body.pop("filename")
    if "user_context" not in body:
        body["user_context"] = {"user": "", "is_admin": True}
    req = WorkspaceFileWriteRequest(**body)
    res = await workspace.handle_workspace_write(req)
    if res.status == "FAILURE":
        raise HTTPException(status_code=400, detail=res.message)
    return res

@app.post("/execute/workspace_file_patch", response_model=ExecutionResult)
async def execute_workspace_file_patch(request: Request):
    body = await request.json()
    body = _normalize_llm_body(body)
    if "file_path" in body and "path" not in body:
        body["path"] = body.pop("file_path")
    if "filename" in body and "path" not in body:
        body["path"] = body.pop("filename")
    if "user_context" not in body:
        body["user_context"] = {"user": "", "is_admin": True}
    req = WorkspaceFilePatchRequest(**body)
    res = await workspace.handle_workspace_patch(req)
    if res.status == "FAILURE":
        raise HTTPException(status_code=400, detail=res.message)
    return res


@app.post("/execute/workspace_lint", response_model=ExecutionResult)
async def execute_workspace_lint(request: Request):
    """Lint a file in the local Git workspace with hallucinated field normalization."""
    body = await request.json()
    body = _normalize_llm_body(body)

    # Normalize hallucinated field names
    if "file_path" in body and "path" not in body:
        body["path"] = body.pop("file_path")
    if "file_paths" in body and "path" not in body:
        fps = body.pop("file_paths")
        body["path"] = fps[0] if isinstance(fps, list) else fps
    if "file_pattern" in body and "path" not in body:
        body["path"] = body.pop("file_pattern")
    if "files" in body and "path" not in body:
        files = body.pop("files")
        body["path"] = files[0] if isinstance(files, list) else files
    if "filename" in body and "path" not in body:
        body["path"] = body.pop("filename")
    if "target" in body and "path" not in body:
        body["path"] = body.pop("target")

    if "user_context" not in body:
        body["user_context"] = {"user": "", "is_admin": True}

    req = WorkspaceLintRequest(**body)
    return await workspace.handle_workspace_lint(req)


@app.post("/execute/discovery_sync", response_model=ExecutionResult)
async def execute_discovery_sync(req: DiscoverySyncRequest):
    """
    Trigger a Home Assistant discovery sync into the RAG database.
    Proxies to Gateway internal discovery API.
    """
    # Note: Gateway is usually at http://gateway:11435 in docker
    from services.config import GATEWAY_INTERNAL_URL
    GATEWAY_INTERNAL = GATEWAY_INTERNAL_URL or "http://localhost:11435"
    try:
        async with get_client() as client:
            resp = await client.post(
                f"{GATEWAY_INTERNAL}/api/discovery/sync",
                json={"api_key": "internal"}, # Simplified for internal bridge
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=60.0),
            )
            if resp.status == 200:
                data = await resp.json()
                return _ok(f"Discovery sync completed. Found {data.get('entities_count', 0)} entities.", "discovery")
            else:
                return _fail(f"Discovery sync failed with status {resp.status}", "discovery")
    except Exception as e:
        return _fail(f"Discovery sync bridge error: {e}", "discovery")


@app.post("/execute/identity/manage", response_model=ExecutionResult)
async def execute_identity_admin(req: IdentityManageRequest):
    """
    Extended identity management: user profile updates, device assignments,
    API key management, and credential rotation.
    Complements the primary /execute/identity handler which covers basic CRUD.
    """
    IDENTITY_SVC = IDENTITY_SVC_URL
    try:
        action = req.action
        username = req.username or req.user_context.user
        async with get_client() as client:
            headers = {"X-Internal-Secret": INTERNAL_SECRET}

            if action == "update_password":
                resp = await client.post(
                    f"{IDENTITY_SVC}/api/users/{username}/password",
                    json={"new_password": req.display_name or ""},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            elif action == "update_user":
                payload = {}
                if req.display_name:
                    payload["display_name"] = req.display_name
                if req.is_admin is not None:
                    payload["is_admin"] = req.is_admin
                resp = await client.patch(
                    f"{IDENTITY_SVC}/api/users/{username}",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            elif action == "assign_device":
                resp = await client.post(
                    f"{IDENTITY_SVC}/api/users/devices",
                    json={
                        "user_id": username,
                        "device_name": req.display_name or "",
                        "device_type": req.category or "media_player",
                    },
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            elif action == "list_devices":
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/users/devices",
                    params={"user_id": username},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            elif action == "generate_key":
                resp = await client.post(
                    f"{IDENTITY_SVC}/api/users/me/keys",
                    json={"label": req.display_name or "CLI Client"},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            elif action == "revoke_key":
                resp = await client.delete(
                    f"{IDENTITY_SVC}/api/users/me/keys/{req.display_name or ''}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            elif action == "list_keys":
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/users/me/keys",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            elif action == "get_profile":
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/users/me",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
            else:
                return _fail(f"Identity admin action '{action}' not supported. Use primary /execute/identity for list/create/delete/discover.", "identity_admin")

            if resp.status in (200, 201, 204):
                data = await resp.json() if resp.status != 204 else {}
                msg = f"Identity admin action '{action}' succeeded."
                if isinstance(data, list):
                    msg += f" Found {len(data)} results."
                elif isinstance(data, dict) and "message" in data:
                    msg = data["message"]
                return _ok(msg, "identity_admin", {"data": data})
            else:
                return _fail(f"Identity service returned {resp.status}: {resp.text}", "identity_admin")
    except Exception as e:
        log.error(f"Identity admin error: {e}")
        return _fail(f"Identity admin bridge error: {e}", "identity_admin")


@app.post("/execute/storage_file_read", response_model=ExecutionResult)
async def execute_storage_file_read(req: StorageFileReadRequest):
    """
    Read a file from Nextcloud storage.
    Used for document discovery.
    """
    return await storage.handle_storage_read(req)


@app.post("/execute/storage_file_write", response_model=ExecutionResult)
async def execute_storage_file_write(req: StorageFileWriteRequest):
    """
    Write a file to Nextcloud storage.
    """
    return await storage.handle_storage_write(req)


@app.post("/execute/tts", response_model=ExecutionResult)
async def execute_tts(req: TTSRequest):
    """
    Converts text to speech using the local Kokoro engine or Edge-TTS.
    Returns the audio bytes as a base64 encoded string in the detail.
    """
    import base64
    try:
        audio_bytes = await _text_to_speech(req.text, voice=req.voice, storybook=req.storybook)
        if not audio_bytes:
            return _fail("TTS generation returned empty bytes", "tts")

        return _ok(
            f"TTS generated successfully ({len(audio_bytes)} bytes)",
            "tts",
            {
                "audio_base64": base64.b64encode(audio_bytes).decode('utf-8'),
                "mime_type": "audio/wav",
                "length_bytes": len(audio_bytes)
            }
        )
    except Exception as e:
        log.error(f"TTS endpoint error: {e}")
        return _fail(f"TTS generation failed: {e!s}", "tts")


@app.post("/execute/storage_text_to_audio", response_model=ExecutionResult)
async def execute_storage_text_to_audio(req: StorageTextToAudioRequest):
    """
    Converts a text file in Nextcloud storage to an audio file.
    """
    return await storage.handle_storage_tts(req)


@app.post("/execute/learning", response_model=ExecutionResult)
async def execute_system_learning(req: SystemLearningRequest):
    """
    Persist architectural insights and bug resolutions to the RAG ledger.
    """
    return await learning.handle_system_learning(req)


@app.post("/execute/index_capabilities", response_model=ExecutionResult)
async def execute_index_capabilities():
    """
    Triggers the JIT Capability Discovery indexing script.
    Allows the agent to refresh its own tool definitions in RAG.
    """
    import subprocess
    import sys

    from services.config import SCRIPTS_DIR
    script_path = os.path.join(os.getcwd(), "scripts", "index_capabilities.py")
    if not os.path.exists(script_path):
        # Fallback for Docker environment
        fallback = os.path.join(SCRIPTS_DIR, "index_capabilities.py")
        if os.path.exists(fallback):
            script_path = fallback

    try:
        log.info(f"Triggering capability indexing: {script_path}")
        # Run the script with current python interpreter and env
        # Ensure PYTHONPATH includes the workspace root for imports
        env = {**os.environ}
        env["PYTHONPATH"] = f"{os.getcwd()}:{os.path.expanduser('~/workspace')}:/app"

        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            env=env
        )

        if result.returncode == 0:
            return _ok("Capabilities re-indexed successfully.", "capability_indexer", {"output": result.stdout})
        else:
            return _fail(f"Indexing failed (code {result.returncode})", "capability_indexer", {"error": result.stderr, "output": result.stdout})

    except Exception as e:
        log.error(f"Failed to run indexing script: {e}")
        return _fail(f"Subprocess error: {e!s}", "capability_indexer")


# ─── Infrastructure Endpoints ────────────────────────────────────────────────────────

@app.get("/execute/tts/voices")
async def list_tts_voices():
    """List available voices for the active TTS engine."""
    from services.execution.tts import get_tts_engine
    engine = get_tts_engine()
    return {"status": "SUCCESS", "voices": engine.list_voices()}

@app.post("/execute/tts/download")
async def download_tts_voice(voice_type: str = "kokoro-v1.0"):
    """
    Downloads the Kokoro ONNX model and voices if missing.
    """
    import subprocess
    log.info(f"[tts] Downloading model files for {voice_type}")

    links = [
        ("kokoro-v1.0.onnx", "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"),
        ("voices-v1.0.bin", "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin")
    ]

    results = []
    for filename, url in links:
        path = f"/app/models/{filename}"
        if os.path.exists(path):
            results.append(f"{filename} already exists.")
            continue
        try:
            cmd = ["curl", "-L", "-o", path, url]
            subprocess.run(cmd, check=True)
            results.append(f"Successfully downloaded {filename}")
        except Exception as e:
            results.append(f"Failed to download {filename}: {e}")
    return {"status": "SUCCESS", "results": results}

@app.post("/execute/announce", response_model=ExecutionResult)
async def execute_announce(req: AnnouncementRequest):
    ctx = req.user_context
    target_player = req.entity_id

    # Resolve HA credentials via Identity service
    ha_url = ctx.ha_url
    ha_token = ctx.ha_token
    if not ha_url or not ha_token:
        creds = await resolve_first_user()
        ha_url = ha_url or (creds or {}).get("ha_url", "")
        ha_token = ha_token or (creds or {}).get("ha_token", "")
    if not ha_url or not ha_token:
        return _fail("Home Assistant URL or token not configured (check Identity service).", "announce")

    # Entity resolution: if entity_id is missing, resolve from device_name
    if not target_player and req.device_name:
        target_player = await ha_client.resolve_entity_by_name(ha_url, ha_token, req.device_name, "media_player")
        if target_player:
            log.info(f"[announce] Resolved device_name='{req.device_name}' -> entity_id='{target_player}'")
        else:
            return _fail(f"Could not find media_player matching '{req.device_name}'. Available devices: check HA entity list.", "announce")

    if not target_player:
        return _fail("entity_id or device_name is required for announcements", "announce")

    if not target_player.startswith("media_player."):
        target_player = f"media_player.{target_player}"

    log.info(f"[announce] START user={ctx.user} target={target_player} msg='{req.message}'")

    # 0. Capture initial state for restoration
    all_states = await ha_client.get_states(ha_url, ha_token) or []
    initial_state = None
    for s in all_states:
        if s.get("entity_id") == target_player:
            initial_state = s
            break

    # Detect if this is an MA-wrapped Roku and store both entities
    ma_player_entity = None
    if initial_state:
        attrs = initial_state.get("attributes", {})
        log.info(f"[announce] Checking for MA-wrapped Roku: app_id={attrs.get('app_id')}, mass_player_type={attrs.get('mass_player_type')}, active_queue={attrs.get('active_queue')}")
        if attrs.get("app_id") == "music_assistant" and attrs.get("mass_player_type") == "player":
            active_queue = (attrs.get("active_queue") or "").lower()
            if "roku" in active_queue:
                # This is an MA player wrapping a Roku - store MA entity for audio
                ma_player_entity = target_player
                # Find the actual Roku media_player entity for ECP control
                for s in all_states:
                    eid = s.get("entity_id", "")
                    s.get("attributes", {})
                    if eid.startswith("media_player.") and "roku" in eid.lower():
                        log.info(f"[announce] MA-wrapped Roku: MA={ma_player_entity}, Roku={eid}")
                        target_player = eid
                        initial_state = s
                        break
                else:
                    log.warning(f"[announce] No Roku entity found in all_states ({len(all_states)} entities)")

    # Pass MA player entity in attributes for announce handler
    if ma_player_entity and initial_state:
        if "attributes" not in initial_state:
            initial_state["attributes"] = {}
        initial_state["attributes"]["_ma_player_entity"] = ma_player_entity
        log.info(f"[announce] Stored _ma_player_entity={ma_player_entity} in initial_state")

    # Get loaded components for device type detection
    config = await ha_client.get_config(ha_url, ha_token) or {}
    loaded_components = set(config.get("components", []))

    was_off = initial_state and initial_state.get("state") in ("off", "unavailable", "standby")
    log.info(f"[announce] Initial state: {initial_state.get('state') if initial_state else 'unknown'}, was_off={was_off}")

    # 1. Power on if needed
    if was_off:
        log.info(f"[announce] Device was {initial_state.get('state') if initial_state else 'unknown'}, turning on...")
        # Detect device type for platform-specific power-on
        detect_tv_type = _detect_tv_type
        attrs = initial_state.get("attributes", {}) if initial_state else {}
        initial_state_str = initial_state.get("state", "unknown") if initial_state else "unknown"
        device_type = detect_tv_type(target_player, initial_state_str, attrs, loaded_components)

        # Samsung TVs need longer wait time (15-30s to boot)
        if device_type == "samsung":
            await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", target_player, {})
            await asyncio.sleep(15.0)
        elif device_type == "webos":
            await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", target_player, {})
            await asyncio.sleep(10.0)
        else:
            await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", target_player, {})
            await asyncio.sleep(2.0)

    # 2. Set volume
    await ha_client.call_service(ha_url, ha_token, "media_player", "volume_set", target_player, {"volume_level": req.volume})

    # 3. TTS & Dispatch
    result = {"ok": False, "error": "No engine selected"}
    media_url = None

    if req.tts_engine == "kokoro":
        try:
            log.info(f"[announce] Generating TTS audio (engine=kokoro, storybook={req.storybook})")
            audio_bytes = await _text_to_speech(req.message, storybook=req.storybook)
            if not audio_bytes:
                return _fail("Kokoro returned empty audio", "announce")

            log.info(f"[announce] TTS generated: {len(audio_bytes)} bytes")
            media_id = f"tts-{uuid4().hex[:8]}"
            TEMP_AUDIO_CACHE[media_id] = audio_bytes
            log.info(f"[announce] Audio cached: media_id={media_id}, cache_size={len(TEMP_AUDIO_CACHE)}")

            public_host = get_public_host()
            # Use dedicated media port 8888 (accessible externally)
            media_url = f"http://{public_host}:8888/media/{media_id}"
            log.info(f"[announce] Media URL: {media_url}")

            # VERIFY: Ensure media endpoint is accessible before dispatching to HA
            log.info("[announce] Verifying media endpoint accessibility...")
            media_ready = False
            for attempt in range(5):
                try:
                    async with get_client() as client:
                        resp = await client.get(media_url, timeout=aiohttp.ClientTimeout(total=2.0))
                        if resp.status == 200 and len(resp.content) > 0:
                            media_ready = True
                            log.info(f"[announce] Media endpoint verified: {len(resp.content)} bytes, content-type={resp.headers.get('content-type')}")
                            break
                        else:
                            log.warning(f"[announce] Media check attempt {attempt+1}/5: status={resp.status}, size={len(resp.content)}")
                except Exception as e:
                    log.warning(f"[announce] Media check attempt {attempt+1}/5 failed: {e}")

                if attempt < 4:
                    await asyncio.sleep(0.5)

            if not media_ready:
                log.error(f"[announce] Media endpoint not accessible after 5 attempts. URL: {media_url}")
                return _fail("Media endpoint not accessible - file not served correctly", "announce")

            # Get entity attributes for TV type detection
            attrs = initial_state.get("attributes", {}) if initial_state else {}
            initial_state_str = initial_state.get("state", "unknown") if initial_state else ""

            # Dispatch to TV-specific handler
            log.info(f"[announce] Dispatching: target={target_player}, state={initial_state_str}, attrs_keys={list(attrs.keys())}, _ma_player={attrs.get('_ma_player_entity')}")
            from announce_handlers import dispatch_announce
            result = await dispatch_announce(ha_url, ha_token, target_player, media_url, req.volume or 0.5, initial_state_str, attrs, loaded_components, message=req.message)
            log.info(f"[announce] Dispatch result: {result}")

            if req.save_path:
                from schemas import StorageFileWriteRequest

                from services.execution.handlers import storage as storage_handler
                await storage_handler.handle_storage_write(StorageFileWriteRequest(
                    user_context=ctx, path=req.save_path, content=audio_bytes.decode('utf-8', errors='replace')
                ))
        except Exception as e:
            log.error(f"[announce] Kokoro failed: {e}")
            result = {"ok": False, "error": str(e)}

    # Fallback: Music Assistant (MASS) play_announcement
    if not result.get("ok") and media_url:
        log.info("[announce] Attempting Music Assistant (MASS) play_announcement...")
        # Resolve to MA player entity if target is a Roku
        fallback_target = target_player
        if attrs.get("_ma_player_entity"):
            fallback_target = attrs["_ma_player_entity"]
            log.info(f"[announce] Using MA player for fallback: {fallback_target}")
        result = await ha_client.call_service(ha_url, ha_token, "music_assistant", "play_announcement", fallback_target, {
            "url": media_url,
            "use_pre_announce": False
        })

    # Last resort: local Piper
    if not result.get("ok"):
        log.info("[announce] Falling back to HA Piper...")
        result = await ha_client.call_service(ha_url, ha_token, "tts", "piper", target_player, {
            "message": req.message
        })

    # 4. Verify playback actually happened
    if result.get("ok") and media_url:
        log.info(f"[announce] Verifying playback on {target_player}...")
        # Detect device type for lenient TV verification
        detect_tv_type = _detect_tv_type
        attrs = initial_state.get("attributes", {}) if initial_state else {}
        initial_state_str = initial_state.get("state", "unknown") if initial_state else "unknown"
        device_type = detect_tv_type(target_player, initial_state_str, attrs, loaded_components)

        # TVs need longer verification timeout
        verify_timeout = 30 if device_type in ("samsung", "webos", "roku") else 15

        verification = await verify_playback(ha_url, ha_token, target_player, media_url, timeout=verify_timeout, device_type=device_type)
        if verification["verified"]:
            log.info(f"[announce] Playback VERIFIED: {verification['detail']}")
        else:
            log.warning(f"[announce] Playback NOT verified: {verification['detail']}")
            result = {"ok": False, "error": f"Playback not confirmed: {verification['detail']}", "verification": verification}

    # 5. Quick confirmation via logbook (faster than polling state)
    if result.get("ok"):
        try:
            log_entries = await ha_client.get_logbook(ha_url, ha_token, target_player, days=1)
            if log_entries:
                latest = log_entries[-1]
                log.info(f"[announce] Logbook confirms: {latest.get('state', '?')} - {latest.get('message', '')[:80]}")
        except Exception as e:
            log.debug(f"[announce] Logbook check skipped: {e}")

    # Wait for announcement audio to finish before restoring
    if result.get("ok"):
        await asyncio.sleep(10)

    # 6. Restore initial state for ALL devices
    # Detect device type for restoration policy
    detect_tv_type = _detect_tv_type
    device_type = detect_tv_type(target_player, initial_state.get("state", "unknown") if initial_state else "unknown", initial_state.get("attributes", {}) if initial_state else {}, loaded_components)

    # For Roku: restore the MA player entity (where volume/source/playback state lives)
    restore_target = target_player
    if device_type == "roku" and attrs.get("_ma_player_entity"):
        restore_target = attrs["_ma_player_entity"]
        log.info(f"[announce] Using MA player for state restoration: {restore_target}")

    # Roku devices: never turn off after announcement (they handle their own power)
    # Other devices: only turn off if truly off/unavailable
    truly_off = initial_state and initial_state.get("state") in ("off", "unavailable")
    if truly_off and result.get("ok") and device_type != "roku":
        log.info("[announce] Restoring device to previous state (turning off)...")
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_off", restore_target, {})
        await asyncio.sleep(1)
    elif initial_state and result.get("ok"):
        # Restore volume, source, and resume playback if device was playing
        init_attrs = initial_state.get("attributes", {})
        initial_state_str = initial_state.get("state", "unknown")

        # Restore volume
        saved_volume = init_attrs.get("volume_level")
        if saved_volume is not None:
            current_state = await ha_client.get_state(ha_url, ha_token, restore_target)
            if current_state:
                current_volume = current_state.get("attributes", {}).get("volume_level")
                if current_volume is not None and abs(current_volume - saved_volume) > 0.01:
                    log.info(f"[announce] Restoring volume from {current_volume} to {saved_volume}")
                    await ha_client.call_service(ha_url, ha_token, "media_player", "volume_set", restore_target, {"volume_level": saved_volume})

        # Restore source/input
        saved_source = init_attrs.get("source")
        if saved_source:
            current_state = await ha_client.get_state(ha_url, ha_token, restore_target)
            if current_state:
                current_source = current_state.get("attributes", {}).get("source")
                if current_source != saved_source:
                    log.info(f"[announce] Restoring source from '{current_source}' to '{saved_source}'")
                    await ha_client.call_service(ha_url, ha_token, "media_player", "select_source", restore_target, {"source": saved_source})
                    await asyncio.sleep(2)

        # Resume playback if device was playing/paused
        if initial_state_str in ("playing", "paused"):
            saved_media = init_attrs.get("media_content_id")
            saved_position = init_attrs.get("media_position")
            if saved_media:
                log.info(f"[announce] Resuming previous media on {restore_target}")
                await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", restore_target,
                    {"media_content_id": saved_media, "media_content_type": init_attrs.get("media_content_type", "url")})
                if saved_position and initial_state_str == "playing":
                    await asyncio.sleep(2)
                    await ha_client.call_service(ha_url, ha_token, "media_player", "media_seek", restore_target,
                        {"seek_position": saved_position})
            else:
                # No media URL saved, just restore play/pause state
                if initial_state_str == "playing":
                    await ha_client.call_service(ha_url, ha_token, "media_player", "media_play", restore_target)
                elif initial_state_str == "paused":
                    await ha_client.call_service(ha_url, ha_token, "media_player", "media_pause", restore_target)

        # Restore mute state
        saved_muted = init_attrs.get("is_volume_muted")
        if saved_muted is not None:
            current_state = await ha_client.get_state(ha_url, ha_token, restore_target)
            if current_state:
                current_muted = current_state.get("attributes", {}).get("is_volume_muted", False)
                if current_muted != saved_muted:
                    log.info(f"[announce] Restoring mute state to {saved_muted}")
                    await ha_client.call_service(ha_url, ha_token, "media_player", "volume_mute", restore_target,
                        {"is_volume_muted": saved_muted})

    if result.get("ok"):
        return _ok(f"Announcement sent successfully to {target_player}.", "announce")
    return _fail(f"Announcement failed: {result.get('error')}", "announce", result)

@app.post("/execute/entity/search", response_model=ExecutionResult)
async def execute_entity_search(req: EntitySearchRequest):
    """Search for HA entities by name, domain, area, or state."""
    ctx = req.user_context
    ha_url = ctx.ha_url
    ha_token = ctx.ha_token

    if not ha_url or not ha_token:
        creds = await resolve_first_user()
        ha_url = ha_url or (creds or {}).get("ha_url", "")
        ha_token = ha_token or (creds or {}).get("ha_token", "")

    if not ha_url or not ha_token:
        return _fail("Home Assistant URL or token not configured.", "entity_search")

    all_states = await ha_client.get_states(ha_url, ha_token) or []
    results = []

    search_terms = req.query.lower().split()

    for state in all_states:
        eid = state.get("entity_id", "")
        attrs = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").lower()
        device_class = attrs.get("device_class", "")
        area = attrs.get("area_id", "")
        current_state = state.get("state", "")

        # Apply filters
        if req.domain:
            allowed_domains = [d.strip() for d in req.domain.split(',') if d.strip()]
            entity_domain = eid.split('.')[0] if '.' in eid else ''
            if allowed_domains and entity_domain not in allowed_domains:
                continue
        if req.area and req.area.lower() not in area.lower():
            continue
        if req.state and req.state.lower() != current_state.lower():
            continue

        # Search matching
        if search_terms:
            searchable = f"{eid} {friendly} {device_class} {area}".lower()
            if not any(term in searchable for term in search_terms):
                continue

        results.append({
            "entity_id": eid,
            "friendly_name": attrs.get("friendly_name", ""),
            "state": current_state,
            "domain": eid.split(".")[0] if "." in eid else "",
            "device_class": device_class,
            "area_id": area,
            "app_id": attrs.get("app_id", ""),
            "source": attrs.get("source", ""),
            "source_list": attrs.get("source_list", [])[:5],
            "supported_features": attrs.get("supported_features", 0),
            "platform": _detect_media_platform(eid, attrs),
        })

    # Sort by relevance: exact matches first, then partial
    if search_terms:
        def relevance_score(r):
            score = 0
            searchable = f"{r['entity_id']} {r['friendly_name']}".lower()
            for term in search_terms:
                if term in searchable:
                    score += 1
                if term == r['friendly_name']:
                    score += 10
            return score
        results.sort(key=relevance_score, reverse=True)

    return ExecutionResult(
        status="SUCCESS",
        message=f"Found {len(results)} matching entities.",
        service="entity_search",
        detail={"entities": results[:20]}
    )

@app.post("/execute/ha_service", response_model=ExecutionResult)
async def execute_ha_service(req: HAServiceRequest):
    ctx = req.user_context
    result = await ha_client.call_service(ctx.ha_url or "", ctx.ha_token or "", req.domain, req.service, req.entity_id, req.service_data)
    if result.get("ok"):
        return _ok(f"{req.domain}.{req.service} executed.", "ha_service")
    return _fail(f"Service call failed: {result.get('error')}", "ha_service", result)

def _detect_media_platform(entity_id: str, attrs: dict) -> str:
    """Detect the TV/media platform type from entity attributes."""
    eid_lower = entity_id.lower()
    app_id = (attrs.get("app_id") or "").lower()
    source_list = [s.lower() for s in (attrs.get("source_list") or [])]

    platform_map = {
        "roku": ["roku", "tcl", "sharp"],
        "webos": ["webos", "lg_", "lg.webos"],
        "samsung": ["samsung", "samsungtv", "tizen"],
        "android_tv": ["androidtv", "android_tv", "com.google.android", "com.google.tv"],
        "chromecast": ["chrome", "_cast", "backdrop"],
        "bravia": ["bravia", "sony"],
        "esphome": ["esphome"],
        "dlna": ["dlna"],
    }
    for platform, indicators in platform_map.items():
        for ind in indicators:
            if ind in eid_lower or ind in app_id or any(ind in src for src in source_list):
                return platform
    return "unknown"

@app.get("/discovery/entities")
async def discovery_entities(request: Request):
    creds = await resolve_first_user()
    ha_url = (creds or {}).get("ha_url", "")
    ha_token = (creds or {}).get("ha_token", "")
    if not ha_url or not ha_token:
        raise HTTPException(status_code=400, detail="HA credentials not configured in Identity")
    states = await ha_client.get_states(ha_url, ha_token) or []
    areas = await ha_client.get_areas(ha_url, ha_token) or {}
    registry = await device_registry.list_devices()
    for s in states:
        eid = s.get("entity_id")
        if eid in areas:
            if "attributes" not in s:
                s["attributes"] = {}
            s["attributes"]["area_id"] = areas[eid]
        if eid in registry:
            dev = registry[eid]
            if "attributes" not in s:
                s["attributes"] = {}
            s["attributes"]["_device_ip"] = dev.get("ip")
            s["attributes"]["_device_mac"] = dev.get("mac")
            s["attributes"]["_device_hostname"] = dev.get("hostname")
            s["attributes"]["_device_discovery_method"] = dev.get("discovery_method")
            s["attributes"]["_device_last_verified"] = dev.get("last_verified")
            if dev.get("metadata"):
                s["attributes"]["_device_metadata"] = dev["metadata"]
    return {"entities": states}

@app.get("/discovery/history")
async def discovery_history(entity_id: str, days: int = 1):
    creds = await resolve_first_user()
    ha_url = (creds or {}).get("ha_url", "")
    ha_token = (creds or {}).get("ha_token", "")
    if not ha_url or not ha_token:
        raise HTTPException(status_code=400, detail="HA credentials not configured in Identity")
    return await ha_client.get_history(ha_url, ha_token, entity_id, days)

@app.get("/discovery/devices")
async def discovery_devices():
    """List all registered devices with network info."""
    return {"devices": await device_registry.list_devices()}

@app.get("/discovery/devices/{entity_id}")
async def discovery_device(entity_id: str):
    """Get registered device info for a specific entity."""
    device = await device_registry.get_device(entity_id)
    if device:
        return {"device": device}
    return {"device": None, "message": f"No device registered for {entity_id}"}

@app.post("/discovery/devices/{entity_id}/refresh")
async def discovery_device_refresh(entity_id: str, request: Request):
    """Trigger re-discovery for a specific device."""
    import device_discovery
    body = await request.json() if request.headers.get("content-length") or request.headers.get("content-type") else {}
    device_type = body.get("device_type")
    subnet = body.get("subnet") or device_discovery.DEFAULT_SUBNET

    creds = await resolve_first_user()
    ha_url = (creds or {}).get("ha_url", "")
    ha_token = (creds or {}).get("ha_token", "")
    if not ha_url or not ha_token:
        return {"status": "FAILURE", "message": "HA credentials not configured in Identity"}

    await device_registry.invalidate_device(entity_id, reason="manual_refresh")
    result = await device_discovery.discover_device(
        entity_id, ha_url, ha_token, device_type, subnet, use_cache=False
    )
    if result:
        return {"status": "SUCCESS", "device": result}
    return {"status": "FAILURE", "message": f"Could not discover {entity_id}"}

@app.post("/discovery/scan")
async def discovery_bulk_scan(request: Request):
    """Bulk network scan for all media devices."""
    import device_discovery
    body = await request.json() if request.headers.get("content-length") or request.headers.get("content-type") else {}
    subnet = body.get("subnet") or device_discovery.DEFAULT_SUBNET

    creds = await resolve_first_user()
    ha_url = (creds or {}).get("ha_url", "")
    ha_token = (creds or {}).get("ha_token", "")
    if not ha_url or not ha_token:
        return {"status": "FAILURE", "message": "HA credentials not configured in Identity"}

    discovered = await device_discovery.bulk_scan(ha_url, ha_token, subnet)
    return {"status": "SUCCESS", "discovered": discovered, "count": len(discovered)}

@app.delete("/discovery/devices/{entity_id}")
async def discovery_device_remove(entity_id: str):
    """Remove a device from the registry."""
    removed = await device_registry.remove_device(entity_id)
    if removed:
        return {"status": "SUCCESS", "message": f"Removed {entity_id}"}
    return {"status": "FAILURE", "message": f"Device {entity_id} not found"}

@app.get("/discovery/profile/{entity_id}")
async def discovery_device_profile(entity_id: str, subnet: str | None = None):
    """Generate a complete device profile with network info, HA data, and control methods."""
    import device_discovery
    import device_profiler
    creds = await resolve_first_user()
    ha_url = (creds or {}).get("ha_url", "")
    ha_token = (creds or {}).get("ha_token", "")
    if not ha_url or not ha_token:
        return {"status": "FAILURE", "message": "HA credentials not configured in Identity"}
    subnet = subnet or device_discovery.DEFAULT_SUBNET
    profile = await device_profiler.profile_device(entity_id, ha_url, ha_token, subnet)
    return profile

@app.get("/discovery/profile")
async def discovery_profile_all(subnet: str | None = None):
    """Profile all media_player entities."""
    import device_discovery
    import device_profiler
    creds = await resolve_first_user()
    ha_url = (creds or {}).get("ha_url", "")
    ha_token = (creds or {}).get("ha_token", "")
    if not ha_url or not ha_token:
        return {"status": "FAILURE", "message": "HA credentials not configured in Identity"}
    subnet = subnet or device_discovery.DEFAULT_SUBNET
    profiles = await device_profiler.profile_all_media_devices(ha_url, ha_token, subnet)
    return {"profiles": profiles, "count": len(profiles)}

@app.post("/discovery/network_scan", response_model=ExecutionResult)
async def discovery_network_scan(req: NetworkDeviceScanRequest):
    """
    Comprehensive network scan for smart TVs and media devices.
    Auto-detects local subnet if not specified.
    """
    from services.execution.handlers.network_scan import scan_network
    ctx = req.user_context
    log.info(f"[network_scan] user={ctx.user} subnet={req.subnet} type={req.device_type}")

    devices = await scan_network(
        subnet=req.subnet,
        device_type=req.device_type,
        include_mac=req.include_mac if req.include_mac is not None else False,
    )

    if devices:
        return ExecutionResult(
            status="SUCCESS",
            message=f"Found {len(devices)} device(s) on network.",
            service="network_scan",
            detail={"devices": devices, "count": len(devices)},
        )
    return ExecutionResult(
        status="FAILURE",
        message="No devices found on network.",
        service="network_scan",
        detail={"devices": [], "count": 0},
    )


@app.get("/discovery/control_methods")
async def discovery_control_methods():
    """Document all supported device types and their control methods."""
    import device_profiler
    return {"control_methods": device_profiler.CONTROL_METHODS}

START_TIME = time.time()

@app.get("/health")
async def health():
    """Check execution service health and all critical dependencies."""
    checks = {}
    overall_status = "healthy"

    # Check Redis with retry
    redis_ok = await _check_dependency(
        "redis",
        lambda: _ping_redis(),
        max_retries=2,
        retry_delay=0.5
    )
    checks["redis"] = redis_ok["status"]
    if not redis_ok["healthy"]:
        overall_status = "degraded"

    # Check Identity Service with retry
    identity_ok = await _check_dependency(
        "identity",
        lambda: _check_service_health(IDENTITY_SVC_URL, "identity"),
        max_retries=2,
        retry_delay=0.5
    )
    checks["identity"] = identity_ok["status"]
    if not identity_ok["healthy"]:
        overall_status = "unhealthy"

    # Check RAG Service
    rag_url = os.getenv("RAG_SVC_URL", "http://localhost:8004")
    rag_ok = await _check_dependency(
        "rag",
        lambda: _check_service_health(rag_url, "rag"),
        max_retries=1,
        retry_delay=0.5
    )
    checks["rag"] = rag_ok["status"]
    if not rag_ok["healthy"]:
        overall_status = "degraded"

    # Check Storage Service
    storage_url = os.getenv("STORAGE_SVC_URL", "http://localhost:8005")
    storage_ok = await _check_dependency(
        "storage",
        lambda: _check_service_health(storage_url, "storage"),
        max_retries=1,
        retry_delay=0.5
    )
    checks["storage"] = storage_ok["status"]
    if not storage_ok["healthy"]:
        overall_status = "degraded"

    status_code = 200 if overall_status == "healthy" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            "service": "execution",
            "checks": checks,
            "git_sha": os.getenv("GIT_SHA", "unknown"),
            "start_time": START_TIME
        }
    )


async def _check_dependency(name: str, check_fn, max_retries: int = 2, retry_delay: float = 0.5) -> dict:
    """Check a dependency with retry logic and proper error handling."""
    for attempt in range(max_retries + 1):
        try:
            result = await check_fn()
            if result["healthy"]:
                return {"status": "ok", "healthy": True}
            log.warning(f"[health] {name} unhealthy (attempt {attempt + 1}/{max_retries + 1}): {result['status']}")
        except Exception as e:
            log.error(f"[health] {name} check failed (attempt {attempt + 1}/{max_retries + 1}): {e}")

        if attempt < max_retries:
            await asyncio.sleep(retry_delay)

    return {"status": f"unreachable after {max_retries + 1} attempts", "healthy": False}


async def _ping_redis() -> dict:
    """Ping Redis and return health status."""
    try:
        from redis.asyncio import Redis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = Redis.from_url(redis_url)
        await redis_client.ping()
        await redis_client.close()
        return {"healthy": True, "status": "ok"}
    except ImportError:
        return {"healthy": False, "status": "redis library not installed"}
    except Exception as e:
        error_msg = str(e)
        if "Timeout" in error_msg:
            return {"healthy": False, "status": f"timeout: {error_msg[:80]}"}
        elif "Connection refused" in error_msg:
            return {"healthy": False, "status": f"connection refused: {error_msg[:80]}"}
        else:
            return {"healthy": False, "status": f"error: {error_msg[:100]}"}


async def _check_service_health(service_url: str, service_name: str) -> dict:
    """Check if a service is healthy by hitting its /health endpoint."""
    try:
        async with get_client() as client:
            resp = await client.get(f"{service_url.rstrip('/')}/health", timeout=aiohttp.ClientTimeout(total=3.0))
            if resp.status == 200:
                return {"healthy": True, "status": "ok"}
            elif resp.status == 503:
                body = await resp.json()
                return {"healthy": False, "status": f"degraded: {body.get('status', 'unknown')}"}
            else:
                return {"healthy": False, "status": f"unexpected status: {resp.status}"}
    except aiohttp.ClientError as e:
        if "Timeout" in str(e):
            return {"healthy": False, "status": f"timeout: {str(e)[:80]}"}
        elif "Cannot assign" in str(e) or "Connection refused" in str(e):
            return {"healthy": False, "status": f"connection refused: {str(e)[:80]}"}
        else:
            return {"healthy": False, "status": f"error: {str(e)[:100]}"}
    except Exception as e:
        return {"healthy": False, "status": f"unexpected error: {str(e)[:100]}"}

@app.post("/execute/ha_logbook", response_model=ExecutionResult)
async def execute_ha_logbook(req: LogbookRequest):
    ctx = req.user_context
    full_entity_id = ha_client.sanitize_entity_id("sensor", req.entity_id)
    log.info(f"[ha_logbook] user={ctx.user} entity={full_entity_id}")

    entries = await ha_client.get_logbook(ctx.ha_url or "", ctx.ha_token or "", full_entity_id, days=req.days)

    if entries:
        return ExecutionResult(
            status="SUCCESS",
            message=f"Retrieved {len(entries)} logbook entries for {full_entity_id}.",
            service="ha_logbook",
            detail={"entries": entries}
        )
    return ExecutionResult(
        status="FAILURE",
        message=f"No logbook entries found for {full_entity_id} in the last {req.days} day(s).",
        service="ha_logbook"
    )

@app.post("/execute/media/status", response_model=ExecutionResult)
async def execute_media_status(req: MediaStatusRequest):
    ctx = req.user_context
    log.info(f"[media/status] user={ctx.user} area={req.area} entity={req.entity_id}")
    if not ctx.ha_url or not ctx.ha_token:
        creds = await resolve_first_user()
        if creds:
            ctx.ha_url = ctx.ha_url or creds.get("ha_url", "")
            ctx.ha_token = ctx.ha_token or creds.get("ha_token", "")
    return await MediaPlaybackService.status(req)


@app.post("/execute/media/state/sync", response_model=ExecutionResult)
async def execute_media_state_sync(req: MediaStateSyncRequest):
    return await MediaPlaybackService.sync_local(req)


async def _resolve_mass_ha_creds(user_id: str):
    """Resolve HA credentials for a user via MA's HA integration (avoids MA REST API auth issues)."""
    if not user_id:
        return None
    creds = await resolve_internal_user(rag_user=user_id)
    if not creds:
        return None
    ha_url = creds.get("ha_url")
    ha_token = creds.get("ha_token")
    if not ha_url or not ha_token:
        return None
    # If mass_config_entry_id is missing, discover it from HA
    if not creds.get("mass_config_entry_id"):
        from services.execution import ha_client as _hc
        entry_id = await _hc.find_mass_config_entry(ha_url, ha_token)
        if entry_id:
            creds["mass_config_entry_id"] = entry_id
            log.info(f"[mass_ha] Discovered MA config entry: {entry_id}")
    return creds


async def _get_ma_playlists_via_ha(ha_url: str, ha_token: str, mass_entry_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Get MA playlists via HA's music_assistant.get_library service."""
    if not ha_url or not ha_token:
        return []

    service_data = {
        "media_type": "playlist",
        "limit": limit,
    }
    if mass_entry_id:
        service_data["config_entry_id"] = mass_entry_id

    result = await ha_client.call_service(
        ha_url, ha_token, "music_assistant", "get_library", entity_id="",
        service_data=service_data,
        return_response=True,
    )

    if not result.get("ok") or not result.get("service_response"):
        log.warning(f"[ma/playlists] HA call failed or no response: {result}")
        return []

    raw = result["service_response"]
    resp = raw.get("service_response", raw)
    log.info(f"[ma/playlists] HA response keys: {list(resp.keys()) if isinstance(resp, dict) else type(resp).__name__}, raw keys: {list(raw.keys())}")
    items = resp.get("playlists", resp.get("items", []))
    log.info(f"[ma/playlists] Found {len(items)} items in key 'playlists' or 'items'")

    return [
        {
            "name": item.get("name", ""),
            "items": item.get("num_tracks", item.get("track_count", 0)),
            "uri": item.get("uri", ""),
            "type": item.get("type", "playlist"),
            "image": item.get("image", ""),
            "description": item.get("description", ""),
        }
        for item in items
    ]


async def _get_ma_recent_via_ha(ha_url: str, ha_token: str, mass_entry_id: str = "", limit: int = 25) -> list[dict[str, Any]]:
    """Get MA recently played via HA's music_assistant.get_library service (tracks)."""
    if not ha_url or not ha_token:
        return []

    service_data = {
        "media_type": "track",
        "limit": limit,
        "order_by": "recently_played_rank",
    }
    if mass_entry_id:
        service_data["config_entry_id"] = mass_entry_id

    result = await ha_client.call_service(
        ha_url, ha_token, "music_assistant", "get_library", entity_id="",
        service_data=service_data,
        return_response=True,
    )

    if not result.get("ok") or not result.get("service_response"):
        log.warning(f"[ma/recent] HA call failed or no response: {result}")
        return []

    raw = result["service_response"]
    resp = raw.get("service_response", raw)
    log.info(f"[ma/recent] HA response keys: {list(resp.keys()) if isinstance(resp, dict) else type(resp).__name__}, raw keys: {list(raw.keys())}")
    items = resp.get("tracks", resp.get("items", []))
    log.info(f"[ma/recent] Found {len(items)} items in key 'tracks' or 'items'")

    return [
        {
            "name": item.get("name", ""),
            "artist": item.get("artists", [{}])[0].get("name", "") if item.get("artists") else item.get("artist", ""),
            "uri": item.get("uri", ""),
            "last_played": item.get("last_played", item.get("added_at", item.get("timestamp", ""))),
            "type": "track",
            "image": item.get("image", ""),
        }
        for item in items
    ]


@app.get("/execute/media/music-assistant/playlists")
async def get_ma_playlists(user_id: str = ""):
    """Get Music Assistant playlists via direct API if configured, fallback to HA."""
    try:
        creds = await _resolve_mass_ha_creds(user_id)
        mass_url = creds.get("mass_url") if creds else None
        mass_token = creds.get("mass_token") if creds else None

        if mass_url and mass_token:
            from services.execution.handlers.mass_client import get_playlists as _direct_playlists
            playlists = await _direct_playlists(mass_url, mass_token)
            if playlists:
                return {"status": "SUCCESS", "playlists": playlists}

        ha_url = creds.get("ha_url") if creds else None
        ha_token = creds.get("ha_token") if creds else None
        mass_entry_id = creds.get("mass_config_entry_id", "") if creds else ""
        if ha_url and ha_token:
            playlists = await _get_ma_playlists_via_ha(ha_url, ha_token, mass_entry_id)
            return {"status": "SUCCESS", "playlists": playlists}

        return {"status": "SUCCESS", "playlists": []}
    except Exception as e:
        log.error(f"[ma/playlists] Error: {e}")
        return {"status": "SUCCESS", "playlists": []}


@app.get("/execute/media/music-assistant/recent")
async def get_ma_recent(user_id: str = ""):
    """Get Music Assistant recently played via direct API if configured, fallback to HA."""
    try:
        creds = await _resolve_mass_ha_creds(user_id)
        mass_url = creds.get("mass_url") if creds else None
        mass_token = creds.get("mass_token") if creds else None

        if mass_url and mass_token:
            from services.execution.handlers.mass_client import get_recent as _direct_recent
            recent = await _direct_recent(mass_url, mass_token)
            if recent:
                return {"status": "SUCCESS", "recent": recent}

        ha_url = creds.get("ha_url") if creds else None
        ha_token = creds.get("ha_token") if creds else None
        mass_entry_id = creds.get("mass_config_entry_id", "") if creds else ""
        if ha_url and ha_token:
            recent = await _get_ma_recent_via_ha(ha_url, ha_token, mass_entry_id)
            return {"status": "SUCCESS", "recent": recent}

        return {"status": "SUCCESS", "recent": []}
    except Exception as e:
        log.error(f"[ma/recent] Error: {e}")
        return {"status": "SUCCESS", "recent": []}


@app.get("/execute/media/music-assistant/browse")
async def get_ma_library(user_id: str = "", media_type: str = "TRACKS", offset: int = 0, limit: int = 50, search: str = "", order_by: str = ""):
    """Browse MA library content via HA proxy (tracks, albums, artists, playlists, radio)."""
    try:
        creds = await _resolve_mass_ha_creds(user_id)
        ha_url = creds.get("ha_url") if creds else None
        ha_token = creds.get("ha_token") if creds else None

        if not ha_url or not ha_token:
            return {"status": "SUCCESS", "items": [], "notice": "MA/HA not configured"}

        from services.execution.handlers.mass_ha_client import get_library as _ma_get_library
        items = await _ma_get_library(ha_url, ha_token, media_type, limit=limit, offset=offset, search=search, order_by=order_by)
        return {"status": "SUCCESS", "items": items, "media_type": media_type, "offset": offset, "limit": limit}
    except Exception as e:
        log.error(f"[ma/browse] Error: {e}")
        return {"status": "SUCCESS", "items": []}


@app.get("/execute/media/music-assistant/search")
async def search_ma(user_id: str = "", query: str = "", media_type: str = "", limit: int = 20, artist: str = "", album: str = "", library_only: bool = True):
    """Search MA for media items — prefer the direct MA token API, fall back to HA proxy."""
    try:
        creds = await _resolve_mass_ha_creds(user_id)
        mass_url = creds.get("mass_url") if creds else None
        mass_token = creds.get("mass_token") if creds else None

        # Primary path: direct MA search via the MA token (no HA dependency).
        # This is authoritative: the MA token search uses config.providers=["library"]
        # and is the only reliable path (the HA music_assistant.search service and the
        # MA REST /api endpoint are unreliable in MA 2.9.x). Return its results directly,
        # including an empty list when there are no matches — do NOT fall through to HA
        # on an empty result, which previously produced spurious FAILURE responses.
        if mass_url and mass_token:
            from services.execution.handlers.mass_client import search as _direct_search
            try:
                results = await _direct_search(
                    mass_url, mass_token, query,
                    limit=limit,
                    media_types=[media_type.lower()] if media_type else None,
                )
                return {"status": "SUCCESS", "results": results, "query": query, "source": "music_assistant"}
            except Exception as e:
                log.warning(f"[ma/search] direct MA search failed, falling back to HA: {e}")

        # Fallback: HA music_assistant.search service
        ha_url = creds.get("ha_url") if creds else None
        ha_token = creds.get("ha_token") if creds else None
        mass_entry_id = creds.get("mass_config_entry_id", "") if creds else ""
        if ha_url and ha_token:
            from services.execution.handlers.mass_ha_client import search as _ma_search
            results = await _ma_search(ha_url, ha_token, query, mass_entry_id=mass_entry_id, media_types=[media_type] if media_type else None, limit=limit, artist=artist, album=album, library_only=library_only)
            return {"status": "SUCCESS", "results": results, "query": query, "source": "ha"}

        return {"status": "SUCCESS", "results": [], "notice": "MA/HA not configured"}
    except Exception as e:
        log.error(f"[ma/search] Error: {e}")
        return {"status": "FAILURE", "message": str(e), "results": []}


@app.get("/execute/audiobookshelf")
async def handle_audiobookshelf_get(action: str = "last_played", user_id: str = "", library_id: str = "", query: str = "", limit: int = 25):
    """Handle Audiobookshelf GET requests (per-user credentials)."""
    try:
        from services.execution.handlers.audiobookshelf import handle_audiobookshelf
        from services.execution.schemas import AudiobookshelfRequest, UserContext

        # Resolve per-user credentials from Identity service
        user_ctx = UserContext(user=user_id)
        creds = await resolve_internal_user(rag_user=user_id)
        if creds:
                user_ctx = UserContext(
                    user=user_id,
                    ha_url=creds.get("ha_url"),
                    ha_token=creds.get("ha_token"),
                    audiobookshelf_url=creds.get("audiobookshelf_url"),
                    audiobookshelf_user=creds.get("audiobookshelf_user"),
                    audiobookshelf_pass=creds.get("audiobookshelf_pass"),
                    audiobookshelf_api_key=creds.get("audiobookshelf_api_key"),
                )

        req = AudiobookshelfRequest(
            action=action if action in ("search", "play", "resume", "progress", "libraries", "list", "get_book", "last_played") else "list",
            user_context=user_ctx,
            library_id=library_id or None,
            query=query or None,
            limit=limit,
            book_id=None,
            entity_id=None,
        )
        return await handle_audiobookshelf(req)
    except Exception as e:
        log.error(f"[abs] Error: {e}")
        return {"status": "SUCCESS", "books": [], "libraries": []}

@app.post("/execute/logs", response_model=ExecutionResult)
async def execute_execution_logs(req: ExecutionLogRequest):
    ctx = req.user_context
    log.info(f"[execution/logs] user={ctx.user} service={req.service} keyword={req.keyword} lines={req.lines}")
    return await diagnostics.handle_execution_logs(req.model_dump())

@app.post("/execute/video/play", response_model=ExecutionResult)
async def execute_video_play(req: VideoPlayRequest):
    ctx = req.user_context
    if not ctx.ha_url or not ctx.ha_token:
        creds = await resolve_first_user()
        if creds:
            ctx.ha_url = ctx.ha_url or creds.get("ha_url", "")
            ctx.ha_token = ctx.ha_token or creds.get("ha_token", "")
    log.info(f"[video/play] user={ctx.user} entity={req.entity_id} query='{req.query}'")
    return await video.handle_video_play(req)

@app.post("/execute/diagnostics", response_model=ExecutionResult)
async def execute_diagnostics(req: DiagnosticRequest):
    log.info(f"[diagnostics] user={req.user_context.user} service={req.service} lines={req.lines}")
    return await diagnostics.handle_get_system_logs(req.model_dump())

@app.post("/execute/llm/info", response_model=ExecutionResult)
async def execute_llm_info(req: LLMInfoRequest):
    """Query Alpaca/Ollama for model and system information."""
    # Read Ollama URL from Identity settings at runtime
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
    ollama_url = ""
    try:
        async with get_client() as client:
            resp = await client.get(f"{IDENTITY_SVC_URL}/api/settings", headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=5.0))
            if resp.status == 200:
                for s in await resp.json():
                    if s.get("key") == "llm_local_url" and s.get("value"):
                        ollama_url = s["value"]
                        break
    except Exception:
        pass
    if not ollama_url:
        return _fail("Ollama URL not configured in Identity settings", "llm_info")
    action = req.action.lower()

    endpoints = {
        "list": "/api/tags",
        "ps": "/api/ps",
        "version": "/api/version",
        "show": "/api/show",
    }

    if action not in endpoints:
        return _fail(f"Unknown action: {action}. Valid: {list(endpoints.keys())}", "llm_info")

    endpoint = endpoints[action]
    payload = {}
    if action == "show" and req.model:
        payload = {"name": req.model}
    elif action == "show" and not req.model:
        return _fail("model is required for 'show' action", "llm_info")

    try:
        async with get_client() as client:
            if payload:
                resp = await client.post(f"{OLLAMA_URL}{endpoint}", json=payload, timeout=aiohttp.ClientTimeout(total=10.0))
            else:
                resp = await client.get(f"{OLLAMA_URL}{endpoint}", timeout=aiohttp.ClientTimeout(total=10.0))

            if resp.status == 200:
                data = await resp.json()
                if action == "list":
                    models = [m.get("name", "?") for m in data.get("models", [])]
                    return _ok(f"Available models: {', '.join(models)}", "llm_info", {"models": data.get("models", [])})
                elif action == "ps":
                    loaded = [m.get("name", "?") for m in data.get("models", [])]
                    status = f"Loaded models: {', '.join(loaded) if loaded else 'none'}"
                    return _ok(status, "llm_info", {"loaded": data.get("models", [])})
                elif action == "version":
                    return _ok(f"Version: {data.get('version', '?')}", "llm_info", data)
                elif action == "show":
                    details = {
                        "name": data.get("name", "?"),
                        "architecture": data.get("details", {}).get("architecture", "?"),
                        "parameters": data.get("details", {}).get("parameter_size", "?"),
                        "quantization": data.get("details", {}).get("quantization_level", "?"),
                        "context_length": data.get("context_length", "?"),
                    }
                    return _ok(f"Model: {details['name']} ({details['parameters']}, {details['quantization']})", "llm_info", details)
            else:
                return _fail(f"Alpaca returned {resp.status}: {resp.text[:200]}", "llm_info")
    except Exception as e:
        return _fail(f"Failed to query Alpaca: {e}", "llm_info")

@app.post("/execute/audiobookshelf", response_model=ExecutionResult)
async def execute_audiobookshelf(req: AudiobookshelfRequest):
    ctx = req.user_context
    log.info(f"[abs] user={ctx.user} action={req.action} query={req.query}")
    return await audiobookshelf.handle_audiobookshelf(req)

@app.post("/execute/composite/broadcast", response_model=ExecutionResult)
async def execute_composite_broadcast(req):
    """Read a document from storage and broadcast as TTS to a media player."""
    log.info(f"[composite] broadcast: {getattr(req, 'input_path', '')} -> {getattr(req, 'entity_id', '')}")
    return await composite.handle_document_broadcast(req)

@app.post("/execute/composite/night_mode", response_model=ExecutionResult)
async def execute_composite_night_mode(req):
    """Activate night mode: lights off, climate set, optional sleep sounds."""
    ctx = req.user_context
    log.info(f"[composite] night_mode for user={ctx.user}")
    return await composite.handle_night_mode(req)


@app.post("/execute/ha_config", response_model=ExecutionResult)
async def execute_ha_config(req: "HAConfigRequest"):
    """Inspect Home Assistant integration configurations via WebSocket API."""
    ctx = req.user_context
    action = req.action
    domain = getattr(req, "domain", None)
    log.info(f"[ha_config] user={ctx.user} action={action} domain={domain}")
    return await ha_config_handler.handle_ha_config(req.model_dump())


# ─── Device & Light Grouping (Section 3.14) ───────────────────────────────────

@app.post("/execute/groups/media", response_model=ExecutionResult)
async def execute_media_group(req):
    """Manage media device groups."""
    from schemas_groups import MediaGroupRequest
    if isinstance(req, BaseModel):
        dump = req.model_dump()
    else:
        dump = req
    if "user_context" not in dump:
        dump["user_context"] = UserContext(user="")
    parsed = MediaGroupRequest(**dump)
    ctx = getattr(parsed, "user_context", UserContext(user=""))
    log.info(f"[groups] media_group action={parsed.action} group_id={parsed.group_id}")
    return await groups.handle_media_group(parsed, ctx)


@app.post("/execute/groups/lights", response_model=ExecutionResult)
async def execute_light_cluster(req):
    """Manage light clusters."""
    from schemas_groups import LightClusterRequest
    if isinstance(req, BaseModel):
        dump = req.model_dump()
    else:
        dump = req
    if "user_context" not in dump:
        dump["user_context"] = UserContext(user="")
    parsed = LightClusterRequest(**dump)
    ctx = getattr(parsed, "user_context", UserContext(user=""))
    log.info(f"[groups] light_cluster action={parsed.action} cluster_id={parsed.cluster_id}")
    return await groups.handle_light_cluster(parsed, ctx)


@app.post("/execute/groups/patterns", response_model=ExecutionResult)
async def execute_light_pattern(req):
    """Manage light patterns."""
    from schemas_groups import LightPatternRequest
    parsed = LightPatternRequest(**req.model_dump() if hasattr(req, 'model_dump') else req)
    log.info(f"[groups] light_pattern action={parsed.action} pattern_id={parsed.pattern_id}")
    return await groups.handle_light_pattern(parsed)


# ─── Presence Detection (Section 3.8) ─────────────────────────────────────────

@app.get("/execute/presence/{user_id}")
async def get_user_presence(user_id: str, x_internal_secret: str = Header(None)):
    """Get current presence data for a user from Redis."""
    await _check_internal_secret(x_internal_secret)
    from services.execution.presence import get_presence_tracker
    tracker = get_presence_tracker()
    presence = await tracker.get_user_presence(user_id)
    if presence:
        return {"status": "SUCCESS", "user_id": user_id, "presence": presence}
    return {"status": "SUCCESS", "user_id": user_id, "presence": None, "message": "No presence data"}


@app.get("/execute/presence/all")
async def get_all_presence(x_internal_secret: str = Header(None)):
    """Get presence data for all tracked users."""
    await _check_internal_secret(x_internal_secret)
    from services.execution.presence import get_presence_tracker
    tracker = get_presence_tracker()
    all_presence = await tracker.get_all_presence()
    return {"status": "SUCCESS", "presence": all_presence}


@app.get("/execute/presence/rooms")
async def get_presence_rooms(x_internal_secret: str = Header(None)):
    """Get list of all known rooms from presence data."""
    await _check_internal_secret(x_internal_secret)
    from services.execution.presence import get_presence_tracker
    tracker = get_presence_tracker()
    rooms = await tracker.get_rooms()
    return {"status": "SUCCESS", "rooms": rooms}


# ─── Speech-to-Text (Section 3.9) ─────────────────────────────────────────────

@app.post("/execute/stt/transcribe")
async def transcribe_audio(file: UploadFile = File(...), model: str = "base", language: str = "en"):
    """Transcribe audio file using Whisper STT."""
    import os
    import tempfile
    try:
        import whisper  # pyright: ignore[reportMissingImports]
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={"status": "FAILURE", "message": "Whisper not installed. Run: pip install openai-whisper"}
        )

    try:
        model_obj = whisper.load_model(model)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "FAILURE", "message": f"Failed to load Whisper model: {e}"}
        )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = model_obj.transcribe(tmp_path, language=language)
        transcript = result.get("text", "").strip()
        return {"status": "SUCCESS", "transcript": transcript, "language": language, "model": model}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "FAILURE", "message": f"Transcription failed: {e}"}
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ─── Voice Command Routing ────────────────────────────────────────────────────

@app.post("/execute/voice/command")
async def execute_voice_command(req: dict, x_internal_secret: str = Header(None)):
    """Route voice command transcript to appropriate handler."""
    await _check_internal_secret(x_internal_secret)
    from schemas import LightControlRequest, MediaPlayRequest, UserContext

    from services.execution.handlers import light, media

    transcript = req.get("transcript", "").strip().lower()
    user_id = req.get("user_id") or ""
    ctx = UserContext(user=user_id)

    if not transcript:
        return {"status": "FAILURE", "message": "Empty transcript"}

    log.info(f"[voice] user={user_id} transcript='{transcript}'")

    # Light commands
    light_keywords = ["light", "lights", "lamp", "brightness", "dim", "turn on", "turn off"]
    if any(kw in transcript for kw in light_keywords):
        entity_id = req.get("entity_id", "")
        if not entity_id:
            return {"status": "FAILURE", "message": "entity_id required for light commands"}
        if "turn on" in transcript or "on" in transcript or "dim" in transcript or "brightness" in transcript:
            action = "turn_on"
        else:
            action = "turn_off"
        brightness_pct = None
        if "dim" in transcript or "brightness" in transcript:
            brightness_pct = 50
        light_req = LightControlRequest(
            user_context=ctx,
            action=action,
            entity_id=entity_id,
            brightness_pct=brightness_pct,
        )
        return await light.handle_light(light_req)

    # Media commands
    media_keywords = ["play", "pause", "stop", "music", "video", "youtube"]
    if any(kw in transcript for kw in media_keywords):
        entity_id = req.get("entity_id", "")
        if not entity_id:
            return {"status": "FAILURE", "message": "entity_id required for media commands"}
        if "play" in transcript:
            media_req = MediaPlayRequest(
                user_context=ctx,
                entity_id=entity_id,
                media_type="music",
                device_name=entity_id,
                query=None,
                media_content_id=None,
                media_content_type=None,
                enqueue="replace",
                volume=None,
            )
            return await media.handle_media_play(media_req)
        elif "pause" in transcript:
            media_req = MediaPlayRequest(
                user_context=ctx,
                entity_id=entity_id,
                media_type="music",
                device_name=entity_id,
                query=None,
                media_content_id=None,
                media_content_type=None,
                enqueue="replace",
                volume=None,
            )
            return await media.handle_media_transport(media_req)


# ─── Skylight Integration Proxy ────────────────────────────────────────────────

_SKYLIGHT_BASE = os.environ.get("SKYLIGHT_BASE_URL", "https://app.ourskylight.com")
_skylight_tokens: dict[str, str] = {}


async def _get_skylight_auth(username: str | None = None) -> tuple[str | None, str | None]:
    """Resolve Skylight credentials from identity service."""
    creds = await resolve_first_user()
    log.debug(f"[skylight] resolve_first_user returned: type={type(creds).__name__}, creds={creds is not None}")
    if not creds:
        return None, None

    url = creds.get("skylight_url") or _SKYLIGHT_BASE
    password = creds.get("skylight_pass")

    # Determine the email/login name to use
    email = username if username else creds.get("skylight_email")

    if not email or not password:
        return None, None

    # Check if the user has disabled the integration
    if username:
        user_creds = await resolve_internal_user(rag_user=username)
        if user_creds and not user_creds.get("skylight_enabled", True):
            log.info(f"[skylight] Integration disabled for user {username}")
            return None, None

    # Reuse cached token if available
    if email in _skylight_tokens:
        return url, _skylight_tokens[email]


    try:
        async with get_client() as client:
            resp = await client.post(
                f"{url}/api/v1/auth/login",
                json={"email": email, "password": password},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status == 200:
                body = await resp.json()
                token = body.get("token") or body.get("access_token")
                if token:
                    _skylight_tokens[email] = token
                    return url, token
    except Exception as e:
        log.error(f"[skylight] Auth failed for {email}: {e}")

    return None, None


async def _skylight_api(url: str, token: str, method: str, path: str, json_body: dict | None = None) -> dict | None:
    """Make a Skylight API call."""
    try:
        async with get_client() as client:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            resp = await client.request(method, f"{url}{path}", json=json_body, headers=headers, timeout=aiohttp.ClientTimeout(total=15.0))
            if resp.status in (200, 201):
                return await resp.json()
            log.error(f"[skylight] API error {resp.status}: {resp.text[:200]}")
            return None
    except Exception as e:
        log.error(f"[skylight] API exception: {e}")
        return None


@app.get("/api/integrations/skylight/chores")
async def get_skylight_chores(
    user: str | None = None,
    date: str | None = None,
    x_internal_secret: str = Header(None)
):
    """Get chores from Skylight, optionally filtered by user and/or date."""
    url, token = await _get_skylight_auth(user)
    if not url or not token:
        return {"status": "FAILURE", "message": "Skylight not configured"}

    result = await _skylight_api(url, token, "GET", "/api/v1/chores")
    if not result:
        return {"status": "FAILURE", "message": "Failed to fetch chores"}

    chores = result.get("data", []) if result else []

    # Filter by user if specified
    if user:
        chores = [
            c for c in chores
            if user.lower() in [a.lower() for a in c.get("assignees", [])]
        ]

    # Filter by date if specified
    if date:
        chores = [
            c for c in chores
            if c.get("due_date", "") == date
        ]

    return {
        "status": "SUCCESS",
        "chores": chores,
    }


@app.post("/api/integrations/skylight/chores/{chore_id}/complete")
async def complete_skylight_chore(
    chore_id: str,
    user: str | None = None,
    x_internal_secret: str = Header(None)
):
    """Mark a Skylight chore as complete."""
    url, token = await _get_skylight_auth(user)
    if not url or not token:
        return {"status": "FAILURE", "message": "Skylight not configured"}

    result = await _skylight_api(url, token, "POST", f"/api/v1/chores/{chore_id}/complete")
    if result:
        return {"status": "SUCCESS", "message": "Chore completed"}
    return {"status": "FAILURE", "message": "Failed to complete chore"}


@app.post("/api/integrations/skylight/chores/{chore_id}/uncomplete")
async def uncomplete_skylight_chore(
    chore_id: str,
    user: str | None = None,
    x_internal_secret: str = Header(None)
):
    """Mark a Skylight chore as incomplete."""
    url, token = await _get_skylight_auth(user)
    if not url or not token:
        return {"status": "FAILURE", "message": "Skylight not configured"}

    result = await _skylight_api(url, token, "POST", f"/api/v1/chores/{chore_id}/uncomplete")
    if result:
        return {"status": "SUCCESS", "message": "Chore uncompleted"}
    return {"status": "FAILURE", "message": "Failed to uncomplete chore"}


@app.get("/api/integrations/skylight/rewards")
async def get_skylight_rewards(
    user: str | None = None,
    x_internal_secret: str = Header(None)
):
    """Get Skylight rewards."""
    url, token = await _get_skylight_auth(user)
    if not url or not token:
        return {"status": "FAILURE", "message": "Skylight not configured"}

    result = await _skylight_api(url, token, "GET", "/api/v1/rewards")
    if not result:
        return {"status": "FAILURE", "message": "Failed to fetch rewards"}

    rewards = result.get("data", []) if result else []
    return {"status": "SUCCESS", "rewards": rewards}


@app.post("/api/integrations/skylight/rewards/{reward_id}/redeem")
async def redeem_skylight_reward(
    reward_id: str,
    body: dict | None = None,
    user: str | None = None,
    x_internal_secret: str = Header(None)
):
    """Redeem a Skylight reward."""
    body = body or {}
    url, token = await _get_skylight_auth(user)
    if not url or not token:
        return {"status": "FAILURE", "message": "Skylight not configured"}

    result = await _skylight_api(url, token, "POST", f"/api/v1/rewards/{reward_id}/redeem", body)
    if result:
        return {"status": "SUCCESS", "message": "Reward redeemed"}
    return {"status": "FAILURE", "message": "Failed to redeem reward"}
