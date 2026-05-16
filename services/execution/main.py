# services/execution/main.py
import os
import sys
import logging
import asyncio
import httpx
import warnings
from typing import Dict, Any, Optional
from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, Header, Request
from fastapi.responses import JSONResponse
import traceback

# Suppress InsecureRequestWarning for internal self-signed certs (homelab)
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

try:
    import ha_client
    from schemas import (
        UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
        TVCastRequest, HAServiceRequest, AnnouncementRequest,
        CalendarRequest, NoteRequest, TimerRequest, TalkRequest, IdentityRequest, IdentityManageRequest,
        WebSearchRequest, WebReadRequest, ExecutionResult,
        DockerLogsRequest, DockerComposeRequest, GitOperationRequest, GitExecutionResult, DeploymentRequest, VolumeInventoryRequest,
        WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
        SystemLearningRequest, DiscoverySyncRequest, TTSRequest, StorageTextToAudioRequest, LogbookRequest, DiagnosticRequest, MediaStatusRequest, ExecutionLogRequest, VideoPlayRequest, AudiobookshelfRequest, DocumentBroadcastRequest, NightModeRequest, EntitySearchRequest
    )
    from handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning, diagnostics, video, audiobookshelf, composite
    from handlers import docker_logs as docker_logs_handler
    from handlers import git as git_handler
    from handlers import deployment as deployment_handler
    from handlers import volumes as volume_handler
    from handlers import media_status as media_status_handler
except ImportError:
    try:
        from . import ha_client
        from .schemas import (
            UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
            TVCastRequest, HAServiceRequest, AnnouncementRequest,
            CalendarRequest, NoteRequest, TimerRequest, TalkRequest, IdentityRequest, IdentityManageRequest,
            WebSearchRequest, WebReadRequest, ExecutionResult,
            DockerLogsRequest, DockerComposeRequest, GitOperationRequest, GitExecutionResult, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
            SystemLearningRequest, DiscoverySyncRequest, TTSRequest, StorageTextToAudioRequest, LogbookRequest, DiagnosticRequest, MediaStatusRequest, ExecutionLogRequest, VideoPlayRequest, AudiobookshelfRequest
        )
        from .handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning, diagnostics, video, audiobookshelf, composite
        from .handlers import docker_logs as docker_logs_handler
        from .handlers import git as git_handler
        from .handlers import deployment as deployment_handler
        from .handlers import volumes as volume_handler
        from .handlers import media_status as media_status_handler
    except (ImportError, ValueError):
        from execution import ha_client
        from execution.schemas import (
            UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
            TVCastRequest, HAServiceRequest, AnnouncementRequest,
            CalendarRequest, NoteRequest, TimerRequest, TalkRequest, IdentityRequest,
            WebSearchRequest, WebReadRequest, ExecutionResult,
            DockerLogsRequest, DockerComposeRequest, GitOperationRequest, GitExecutionResult, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
            SystemLearningRequest, DiscoverySyncRequest, TTSRequest, StorageTextToAudioRequest, LogbookRequest, DiagnosticRequest, MediaStatusRequest, ExecutionLogRequest, VideoPlayRequest
        )
        from execution.handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning, diagnostics, video
        from execution.handlers import docker_logs as docker_logs_handler
        from execution.handlers import git as git_handler
        from execution.handlers import deployment as deployment_handler
        from execution.handlers import volumes as volume_handler
        from execution.handlers import media_status as media_status_handler

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
log = logging.getLogger("execution")

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import INTERNAL_SECRET, IDENTITY_SVC_URL

async def resolve_internal_user(user_id: str) -> Optional[Dict[str, Any]]:
    """Query Identity Service for full user credentials using internal secret."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{IDENTITY_SVC_URL}/api/resolve",
                json={"rag_user": user_id},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        log.error(f"Failed to resolve internal user {user_id}: {e}")
    return None

async def require_internal(request: Request, x_internal_secret: str = Header(None)):
    if request.url.path == "/health" or request.url.path.startswith("/media/"):
        return

    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ─── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Execution Bridge starting up.")
    # Auto-download Kokoro models if missing
    from config import MODELS_DIR
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
    yield
    log.info("Execution Bridge shutting down.")



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


# Transient cache for locally-generated media (TTS announcements)
TEMP_AUDIO_CACHE: Dict[str, bytes] = {}
# Video files stored on disk (streamed for large files)
from config import TEMP_MEDIA_DIR
TEMP_VIDEO_DIR = TEMP_MEDIA_DIR
os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)

async def verify_playback(ha_url: str, ha_token: str, entity_id: str, expected_media_url: str, timeout: int = 10) -> Dict[str, Any]:
    """Verify that a media player actually started playing the expected content.
    
    Returns dict with: verified (bool), state, media_content_id, app_name, detail
    """
    import time
    start = time.time()
    playing_seen = False
    
    while time.time() - start < timeout:
        state_resp = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state_resp:
            await asyncio.sleep(0.5)
            continue
            
        current_state = state_resp.get("state", "unknown")
        attrs = state_resp.get("attributes", {})
        current_media = attrs.get("media_content_id", "")
        app_name = attrs.get("app_name", attrs.get("app_id", ""))
        
        # Check if we see 'playing' state with matching media URL
        if current_state == "playing" and expected_media_url in str(current_media):
            playing_seen = True
            log.info(f"[verify_playback] CONFIRMED playing: {entity_id} -> {current_media[:60]}")
            # Wait a moment to ensure it's stable, then return success
            await asyncio.sleep(1)
            return {
                "verified": True,
                "state": current_state,
                "media_content_id": current_media,
                "app_name": app_name,
                "detail": "Playback confirmed via state transition to 'playing'"
            }
        
        await asyncio.sleep(0.5)
    
    # Timeout without seeing playing state
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
    video_path = os.path.join(TEMP_VIDEO_DIR, f"{media_id}.mp4")
    if os.path.exists(video_path):
        return FileResponse(
            video_path,
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            }
        )
    
    raise HTTPException(status_code=404, detail="Media expired or not found")

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
    if not await verify_entity_access(req.user_context, req.entity_id):
        raise HTTPException(status_code=403, detail="Access denied to this device")
    return await media.handle_media_play(req)

@app.post("/execute/media/transport", response_model=ExecutionResult)
async def execute_media_transport(req: MediaTransportRequest):
    return await media.handle_media_transport(req)

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
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"X-Internal-Secret": INTERNAL_SECRET}
            if action == "import_nextcloud":
                resp = await client.post(f"{IDENTITY_SVC_URL}/api/auth/import/nextcloud", headers=headers)
            elif action == "discover":
                resp = await client.get(f"{IDENTITY_SVC_URL}/api/auth/discover", headers=headers)
            elif action == "list":
                resp = await client.get(f"{IDENTITY_SVC_URL}/api/users", headers=headers)
            elif action == "create":
                payload = {
                    "username": req.username,
                    "display_name": req.display_name or req.username,
                    "is_admin": req.is_admin
                }
                resp = await client.post(f"{IDENTITY_SVC_URL}/api/users", json=payload, headers=headers)
            elif action == "delete":
                resp = await client.delete(f"{IDENTITY_SVC_URL}/api/users/{req.username}", headers=headers)
            else:
                return _fail(f"Action {action} not supported", "identity")
            
            if resp.status_code in (200, 201, 204):
                data = resp.json() if resp.status_code != 204 else {}
                msg = f"Identity action '{action}' successful."
                if isinstance(data, list):
                    msg += f" Found {len(data)} results."
                elif isinstance(data, dict) and "message" in data:
                    msg = data["message"]
                return _ok(msg, "identity", {"data": data})
            else:
                return _fail(f"Identity service returned {resp.status_code}: {resp.text}", "identity")
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
async def list_timers(user_id: Optional[str] = None):
    return await timer.get_active_timers(user_id=user_id)

@app.post("/execute/trigger", response_model=ExecutionResult)
async def execute_trigger(payload: Dict[str, Any]):
    """Internal endpoint for Automation scheduler."""
    timer_data = payload.get("timer", {})
    user_id = payload.get("user_id") or timer_data.get("user_id")
    
    log.info(f"ALARM TRIGGERED: {timer_data.get('title')} for user {user_id}")
    
    if not user_id:
        return _ok(f"Triggered {timer_data.get('title')} (no user context)", "automation_trigger")

    # Resolve full credentials for the user (to get HA token)
    creds = await resolve_internal_user(user_id)
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
                    media_id="media-source://tts/google?message=" + f"Timer {timer_data.get('title')} is done",
                    entity_id=target_device
                ),
                context
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
            from config import COMPOSE_PROJECT_DIR
            compose_dir = COMPOSE_PROJECT_DIR or os.path.expanduser("~/workspace")
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=compose_dir)
            if res.returncode == 0:
                return _ok(f"Docker Compose up -d --build successful for {len(services)} services.", {"output": res.stdout})
            else:
                return _fail(f"Docker Compose up failed (code {res.returncode})", "docker", {"error": res.stderr, "output": res.stdout})
        except Exception as e:
            return _fail(f"Subprocess error during docker-compose up: {e}", "docker")

    for svc in services:
        # Ensure prefix
        container_name = svc if svc.startswith("sharedllm_") else f"sharedllm_{svc}"
        mock_req = DeploymentRequest(
            user_context=req.user_context,
            action=req.action,
            container_name=container_name
        )
        res = await deployment_handler.handle_deployment(mock_req)
        results.append(res)
    
    return _ok(f"Docker action '{req.action}' applied to {len(services)} services.", {"results": results})


@app.post("/execute/volumes")
async def execute_volumes(req: VolumeInventoryRequest):
    """
    Inspect tracked Docker volumes, sizes, and backup/prune examples.
    Admin only.
    """
    return await volume_handler.handle_volumes(req)


@app.post("/execute/workspace_search", response_model=ExecutionResult)
async def execute_workspace_search(req: WorkspaceSearchRequest):
    return await workspace.handle_workspace_search(req)

@app.post("/execute/workspace_shell", response_model=ExecutionResult)
async def execute_workspace_shell(req: WorkspaceShellRequest):
    return await workspace.handle_workspace_shell(req)

@app.post("/execute/workspace_file_read", response_model=ExecutionResult)
async def execute_workspace_file_read(req: WorkspaceFileReadRequest):
    res = await workspace.handle_workspace_read(req)
    if res.status == "FAILURE":
        raise HTTPException(status_code=404, detail=res.message)
    return res

@app.post("/execute/workspace_file_write", response_model=ExecutionResult)
async def execute_workspace_file_write(req: WorkspaceFileWriteRequest):
    res = await workspace.handle_workspace_write(req)
    if res.status == "FAILURE":
        raise HTTPException(status_code=400, detail=res.message)
    return res

@app.post("/execute/workspace_file_patch", response_model=ExecutionResult)
async def execute_workspace_file_patch(req: WorkspaceFilePatchRequest):
    res = await workspace.handle_workspace_patch(req)
    if res.status == "FAILURE":
        raise HTTPException(status_code=400, detail=res.message)
    return res


@app.post("/execute/workspace_lint", response_model=ExecutionResult)
async def execute_workspace_lint(req: WorkspaceLintRequest):
    """
    Lint a file in the local Git workspace.
    Auto-detects tool from extension: .py→black+flake8, .js/.ts→eslint, .json→json.tool, .yaml→yamllint.
    """
    return await workspace.handle_workspace_lint(req)


@app.post("/execute/discovery_sync", response_model=ExecutionResult)
async def execute_discovery_sync(req: DiscoverySyncRequest):
    """
    Trigger a Home Assistant discovery sync into the RAG database.
    Proxies to Gateway internal discovery API.
    """
    # Note: Gateway is usually at http://gateway:11435 in docker
    from config import GATEWAY_INTERNAL_URL
    GATEWAY_INTERNAL = GATEWAY_INTERNAL_URL or "http://gateway:11435"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{GATEWAY_INTERNAL}/api/discovery/sync",
                json={"api_key": "internal"}, # Simplified for internal bridge
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                data = resp.json()
                return _ok(f"Discovery sync completed. Found {data.get('entities_count', 0)} entities.", "discovery")
            else:
                return _fail(f"Discovery sync failed with status {resp.status_code}", "discovery")
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
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"X-Internal-Secret": INTERNAL_SECRET}

            if action == "update_password":
                resp = await client.post(
                    f"{IDENTITY_SVC}/api/users/{username}/password",
                    json={"new_password": req.display_name or ""},
                    headers=headers,
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
                )
            elif action == "list_devices":
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/users/devices",
                    params={"user_id": username},
                    headers=headers,
                )
            elif action == "generate_key":
                resp = await client.post(
                    f"{IDENTITY_SVC}/api/users/me/keys",
                    json={"label": req.display_name or "CLI Client"},
                    headers=headers,
                )
            elif action == "revoke_key":
                resp = await client.delete(
                    f"{IDENTITY_SVC}/api/users/me/keys/{req.display_name or ''}",
                    headers=headers,
                )
            elif action == "list_keys":
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/users/me/keys",
                    headers=headers,
                )
            elif action == "get_profile":
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/users/me",
                    headers=headers,
                )
            else:
                return _fail(f"Identity admin action '{action}' not supported. Use primary /execute/identity for list/create/delete/discover.", "identity_admin")

            if resp.status_code in (200, 201, 204):
                data = resp.json() if resp.status_code != 204 else {}
                msg = f"Identity admin action '{action}' succeeded."
                if isinstance(data, list):
                    msg += f" Found {len(data)} results."
                elif isinstance(data, dict) and "message" in data:
                    msg = data["message"]
                return _ok(msg, "identity_admin", {"data": data})
            else:
                return _fail(f"Identity service returned {resp.status_code}: {resp.text}", "identity_admin")
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
    from tts import text_to_speech
    import base64
    try:
        audio_bytes = await text_to_speech(req.text, voice=req.voice, storybook=req.storybook)
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
        return _fail(f"TTS generation failed: {str(e)}", "tts")


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
    
    from config import SCRIPTS_DIR
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
        return _fail(f"Subprocess error: {str(e)}", "capability_indexer")


# ─── Infrastructure Endpoints ────────────────────────────────────────────────────────

@app.get("/execute/tts/voices")
async def list_tts_voices():
    """List available voices for the active TTS engine."""
    from tts import get_tts_engine
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
    
    # Fallback to env vars if user context doesn't have HA credentials
    from config import HA_URL, HA_TOKEN
    ha_url = ctx.ha_url or HA_URL
    ha_token = ctx.ha_token or HA_TOKEN
    if not ha_url or not ha_token:
        return _fail("Home Assistant URL or token not configured (check user identity or HA_URL/HA_TOKEN env vars).", "announce")
    
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
    
    # Get loaded components for device type detection
    config = await ha_client.get_config(ha_url, ha_token) or {}
    loaded_components = set(config.get("components", []))
    
    was_off = initial_state and initial_state.get("state") in ("off", "unavailable", "standby")
    log.info(f"[announce] Initial state: {initial_state.get('state') if initial_state else 'unknown'}, was_off={was_off}")
    
    # 1. Power on if needed
    if was_off:
        log.info(f"[announce] Device was {initial_state.get('state')}, turning on...")
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", target_player, {})
        await asyncio.sleep(2.0)
    
    # 2. Set volume
    await ha_client.call_service(ha_url, ha_token, "media_player", "volume_set", target_player, {"volume_level": req.volume})
    
    # 3. TTS & Dispatch
    result = {"ok": False, "error": "No engine selected"}
    media_url = None
    
    if req.tts_engine == "kokoro":
        from tts import text_to_speech
        try:
            audio_bytes = await text_to_speech(req.message, storybook=req.storybook)
            if not audio_bytes:
                return _fail("Kokoro returned empty audio", "announce")
            
            media_id = f"tts-{uuid4().hex[:8]}"
            TEMP_AUDIO_CACHE[media_id] = audio_bytes
            def get_public_host():
                from config import EXECUTION_EXTERNAL_HOST
                env_host = EXECUTION_EXTERNAL_HOST
                if env_host: return env_host
                try:
                    with open("docker-compose.yml", "r") as f:
                        for line in f:
                            if "ai-server:" in line:
                                return line.split(":")[1].strip().strip('"').strip("'")
                except: pass
                raise RuntimeError("EXECUTION_EXTERNAL_HOST is not set and no compose IP was discovered.")
            
            public_host = get_public_host()
            media_url = f"http://{public_host}:8003/media/{media_id}"
            
            # Get entity attributes for TV type detection
            attrs = initial_state.get("attributes", {}) if initial_state else {}
            initial_state_str = initial_state.get("state", "unknown") if initial_state else "unknown"
            
            # Dispatch to TV-specific handler
            from announce_handlers import dispatch_announce
            result = await dispatch_announce(ha_url, ha_token, target_player, media_url, req.volume, initial_state_str, attrs, loaded_components)
            
            if req.save_path:
                from handlers import storage as storage_handler
                from schemas import StorageFileWriteRequest
                await storage_handler.handle_storage_write(StorageFileWriteRequest(
                    user_context=ctx, path=req.save_path, content=audio_bytes
                ))
        except Exception as e:
            log.error(f"[announce] Kokoro failed: {e}")
            result = {"ok": False, "error": str(e)}

    # Fallback: Music Assistant (MASS) play_announcement
    if not result.get("ok"):
        log.info("[announce] Attempting Music Assistant (MASS) announcement...")
        result = await ha_client.call_service(ha_url, ha_token, "mass", "play_announcement", target_player, {
            "message": req.message,
            "use_pre_announcement_signal": True
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
        verification = await verify_playback(ha_url, ha_token, target_player, media_url, timeout=15)
        if verification["verified"]:
            log.info(f"[announce] Playback VERIFIED: {verification['detail']}")
        else:
            log.warning(f"[announce] Playback NOT verified: {verification['detail']}")
            result = {"ok": False, "error": f"Playback not confirmed: {verification['detail']}", "verification": verification}
    
    # 5. Wait for playback to complete (state returns to idle)
    if result.get("ok"):
        log.info(f"[announce] Waiting for playback to complete...")
        for _ in range(20):
            await asyncio.sleep(1)
            states = await ha_client.get_states(ha_url, ha_token) or []
            for s in states:
                if s.get("entity_id") == target_player:
                    if s.get("state") in ("idle", "off", "unavailable"):
                        log.info(f"[announce] Playback complete, state={s.get('state')}")
                        break
            else:
                continue
            break
    
    # 6. Restore initial state if device was off
    if was_off and result.get("ok"):
        log.info(f"[announce] Restoring device to previous state (turning off)...")
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_off", target_player, {})
        await asyncio.sleep(1)

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
        from config import HA_URL, HA_TOKEN
        ha_url = ha_url or HA_URL
        ha_token = ha_token or HA_TOKEN
    
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
        if req.domain and not eid.startswith(f"{req.domain}."):
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
            "supported_features": attrs.get("supported_features", 0),
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
    result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, req.domain, req.service, req.entity_id, req.service_data)
    if result.get("ok"):
        return _ok(f"{req.domain}.{req.service} executed.", "ha_service")
    return _fail(f"Service call failed: {result.get('error')}", "ha_service", result)

@app.get("/discovery/entities")
async def discovery_entities(ha_url: str, ha_token: str):
    states = await ha_client.get_states(ha_url, ha_token) or []
    areas = await ha_client.get_areas(ha_url, ha_token) or {}
    for s in states:
        eid = s.get("entity_id")
        if eid in areas:
            if "attributes" not in s:
                s["attributes"] = {}
            s["attributes"]["area_id"] = areas[eid]
    return {"entities": states}

@app.get("/discovery/history")
async def discovery_history(ha_url: str, ha_token: str, entity_id: str, days: int = 1):
    return await ha_client.get_history(ha_url, ha_token, entity_id, days)

@app.get("/health")
def health():
    return {"status": "ok", "service": "execution"}

@app.post("/execute/ha_logbook", response_model=ExecutionResult)
async def execute_ha_logbook(req: LogbookRequest):
    ctx = req.user_context
    full_entity_id = ha_client.sanitize_entity_id("sensor", req.entity_id)
    log.info(f"[ha_logbook] user={ctx.user} entity={full_entity_id}")
    
    entries = await ha_client.get_logbook(ctx.ha_url, ctx.ha_token, full_entity_id, days=req.days)
    
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
    return await media_status_handler.handle_media_status(req)

@app.post("/execute/logs", response_model=ExecutionResult)
async def execute_execution_logs(req: ExecutionLogRequest):
    ctx = req.user_context
    log.info(f"[execution/logs] user={ctx.user} service={req.service} keyword={req.keyword} lines={req.lines}")
    return await diagnostics.handle_execution_logs(req.model_dump())

@app.post("/execute/video/play", response_model=ExecutionResult)
async def execute_video_play(req: VideoPlayRequest):
    ctx = req.user_context
    log.info(f"[video/play] user={ctx.user} entity={req.entity_id} query='{req.query}'")
    return await video.handle_video_play(req)

@app.post("/execute/diagnostics", response_model=ExecutionResult)
async def execute_diagnostics(req: DiagnosticRequest):
    log.info(f"[diagnostics] user={req.user_context.user} service={req.service} lines={req.lines}")
    return await diagnostics.handle_get_system_logs(req.model_dump())

@app.post("/execute/audiobookshelf", response_model=ExecutionResult)
async def execute_audiobookshelf(req: AudiobookshelfRequest):
    ctx = req.user_context
    log.info(f"[abs] user={ctx.user} action={req.action} query={req.query}")
    return await audiobookshelf.handle_audiobookshelf(req)

@app.post("/execute/composite/broadcast", response_model=ExecutionResult)
async def execute_composite_broadcast(req):
    """Read a document from storage and broadcast as TTS to a media player."""
    ctx = req.user_context
    log.info(f"[composite] broadcast: {getattr(req, 'input_path', '')} -> {getattr(req, 'entity_id', '')}")
    return await composite.handle_document_broadcast(req)

@app.post("/execute/composite/night_mode", response_model=ExecutionResult)
async def execute_composite_night_mode(req):
    """Activate night mode: lights off, climate set, optional sleep sounds."""
    ctx = req.user_context
    log.info(f"[composite] night_mode for user={ctx.user}")
    return await composite.handle_night_mode(req)
