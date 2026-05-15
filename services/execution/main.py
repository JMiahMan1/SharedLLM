# services/execution/main.py
import os
import sys
import logging
import asyncio
import httpx
from typing import Dict, Any, Optional
from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, Header, Request
from fastapi.responses import JSONResponse
import traceback
try:
    from . import ha_client
    from .schemas import (
        UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
        TVCastRequest, HAServiceRequest, AnnouncementRequest,
        CalendarRequest, NoteRequest, TimerRequest, TalkRequest, IdentityRequest,
        WebSearchRequest, WebReadRequest, ExecutionResult,
        DockerLogsRequest, DockerComposeRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
        WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
        SystemLearningRequest, DiscoverySyncRequest, TTSRequest, StorageTextToAudioRequest, LogbookRequest, DiagnosticRequest
    )
    from .handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning, diagnostics
    from .handlers import docker_logs as docker_logs_handler
    from .handlers import git as git_handler
    from .handlers import deployment as deployment_handler
    from .handlers import volumes as volume_handler
except (ImportError, ValueError):
    try:
        from execution import ha_client
        from execution.schemas import (
            UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
            TVCastRequest, HAServiceRequest, AnnouncementRequest,
            CalendarRequest, NoteRequest, TimerRequest, TalkRequest, IdentityRequest,
            WebSearchRequest, WebReadRequest, ExecutionResult,
            DockerLogsRequest, DockerComposeRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
            SystemLearningRequest, DiscoverySyncRequest, TTSRequest, StorageTextToAudioRequest, LogbookRequest, DiagnosticRequest
        )
        from execution.handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning, diagnostics
        from execution.handlers import docker_logs as docker_logs_handler
        from execution.handlers import git as git_handler
        from execution.handlers import deployment as deployment_handler
        from execution.handlers import volumes as volume_handler
    except ImportError:
        import ha_client
        from schemas import (
            UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
            TVCastRequest, HAServiceRequest, AnnouncementRequest,
            CalendarRequest, NoteRequest, TimerRequest, TalkRequest, IdentityRequest,
            WebSearchRequest, WebReadRequest, ExecutionResult,
            DockerLogsRequest, DockerComposeRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
            SystemLearningRequest, DiscoverySyncRequest, TTSRequest, StorageTextToAudioRequest, LogbookRequest, DiagnosticRequest
        )
        from handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning, diagnostics
        from handlers import docker_logs as docker_logs_handler
        from handlers import git as git_handler
        from handlers import deployment as deployment_handler
        from handlers import volumes as volume_handler

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
log = logging.getLogger("execution")

# Fail-Secure: refuse startup if INTERNAL_SECRET is not injected by the host
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")
if not INTERNAL_SECRET:
    log.critical("FATAL: INTERNAL_SECRET environment variable is not set. Refusing to start.")
    sys.exit(1)
IDENTITY_SVC_URL = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")

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
    os.makedirs("/app/models", exist_ok=True)
    kokoro_path = "/app/models/kokoro-v1.0.onnx"
    voices_path = "/app/models/voices-v1.0.bin"
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

@app.get("/media/{media_id}")
async def get_temp_media(media_id: str):
    """Serves transient audio files for HA playback."""
    if media_id not in TEMP_AUDIO_CACHE:
        raise HTTPException(status_code=404, detail="Media expired or not found")
    from fastapi.responses import Response
    return Response(content=TEMP_AUDIO_CACHE[media_id], media_type="audio/wav")

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
                return _ok(f"Identity action '{action}' successful.", "identity", data)
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
        log.info(f"[docker] Running docker-compose up -d for services: {services}")
        try:
            # We run this from the workspace root where docker-compose.yml is
            cmd = ["docker-compose", "up", "-d"] + list(services)
            res = subprocess.run(cmd, capture_output=True, text=True, cwd="/workspace/SharedLLM")
            if res.returncode == 0:
                return _ok(f"Docker Compose up -d successful for {len(services)} services.", {"output": res.stdout})
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
    GATEWAY_INTERNAL = os.getenv("GATEWAY_INTERNAL_URL", "http://gateway:11435")
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


@app.post("/execute/identity", response_model=ExecutionResult)
async def execute_identity(req: IdentityRequest):
    """
    Proxy identity management requests (list, import, discover) to the Identity service.
    """
    IDENTITY_SVC = os.getenv("IDENTITY_SVC", "http://identity:8001")
    try:
        action = req.action
        async with httpx.AsyncClient(timeout=30.0) as client:
            if action == "import_nextcloud":
                resp = await client.post(f"{IDENTITY_SVC}/api/auth/import/nextcloud", headers={"X-Internal-Secret": INTERNAL_SECRET})
            elif action == "list":
                resp = await client.get(f"{IDENTITY_SVC}/api/users", headers={"X-Internal-Secret": INTERNAL_SECRET})
            elif action == "discover":
                resp = await client.get(f"{IDENTITY_SVC}/api/users/discover", headers={"X-Internal-Secret": INTERNAL_SECRET})
            else:
                return _fail(f"Identity action '{action}' not yet implemented via tool interface.", "identity")
            
            if resp.status_code == 200:
                data = resp.json()
                msg = f"Identity action '{action}' succeeded."
                if isinstance(data, list):
                    msg += f" Found {len(data)} results."
                elif isinstance(data, dict) and "message" in data:
                    msg = data["message"]
                return _ok(msg, "identity", {"data": data})
            else:
                return _fail(f"Identity service returned {resp.status_code}: {resp.text}", "identity")
    except Exception as e:
        log.error(f"Identity tool error: {e}")
        return _fail(f"Identity bridge error: {e}", "identity")


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
    
    script_path = os.path.join(os.getcwd(), "scripts", "index_capabilities.py")
    if not os.path.exists(script_path):
        # Fallback for Docker environment
        fallbacks = [
            "/workspace/SharedLLM/scripts/index_capabilities.py",
            "/app/scripts/index_capabilities.py"
        ]
        for fb in fallbacks:
            if os.path.exists(fb):
                script_path = fb
                break
        
    try:
        log.info(f"Triggering capability indexing: {script_path}")
        # Run the script with current python interpreter and env
        # Ensure PYTHONPATH includes the workspace root for imports
        env = {**os.environ}
        env["PYTHONPATH"] = f"{os.getcwd()}:/workspace/SharedLLM:/app"
        
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
    if not target_player.startswith("media_player."):
        target_player = f"media_player.{target_player}"

    log.info(f"[announce] START user={ctx.user} target={target_player} msg='{req.message}'")
    
    # 1. Power on
    await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", target_player, {})
    await asyncio.sleep(1.0)

    # 2. Set volume
    await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_set", target_player, {"volume_level": req.volume})
    
    # 3. TTS & Dispatch
    result = {"ok": False, "error": "No engine selected"}
    
    # Check if target is a Roku to use specialized Media Assistant app (ID 782875)
    is_roku = "roku" in target_player.lower()
    
    if req.tts_engine == "kokoro":
        from tts import text_to_speech
        try:
            audio_bytes = await text_to_speech(req.message, storybook=req.storybook)
            if not audio_bytes:
                return _fail("Kokoro returned empty audio", "announce")
            
            media_id = f"tts-{uuid4().hex[:8]}"
            TEMP_AUDIO_CACHE[media_id] = audio_bytes
            # Note: Using host.docker.internal or a resolvable IP is better, but 'execution' works within the docker net.
            # For HA to reach it, we need the execution service's public/internal IP from the host's perspective.
            # We'll try to discover the production IP from the environment or docker-compose.
            def get_public_host():
                # 1. Check if configured in env
                env_host = os.getenv("EXECUTION_EXTERNAL_HOST")
                if env_host: return env_host
                
                # 2. Try to find ai-server IP from compose if we are on the same machine
                try:
                    with open("docker-compose.yml", "r") as f:
                        for line in f:
                            if "ai-server:" in line:
                                return line.split(":")[1].strip().strip('"').strip("'")
                except: pass
                
                # 3. Fallback to a common local IP if we are in the .2.x subnet or similar
                return "192.168.2.205" # Default Production Host
            
            public_host = get_public_host()
            media_url = f"http://{public_host}:8080/media/{media_id}"
            
            if is_roku:
                # Specialized Roku Media Assistant App (ID 782875)
                # Parameters: t=a (audio), u=URL
                log.info(f"[announce] Roku detected. Launching Media Assistant App (782875) with URL: {media_url}")
                result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "play_media", target_player, {
                    "media_content_id": "782875",
                    "media_content_type": "app",
                    "extra": {"content_id": media_url, "media_type": "audio/wav"}
                })
                # If app launch succeeded, wait a moment for it to buffer
                if result.get("ok"):
                    await asyncio.sleep(2.0)
            else:
                log.info(f"[announce] Playing Kokoro URL: {media_url}")
                result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "play_media", target_player, {
                    "media_content_id": media_url,
                    "media_content_type": "audio/wav"
                })
            
            if req.save_path:
                from handlers import storage as storage_handler
                from schemas import StorageFileWriteRequest
                await storage_handler.handle_storage_write(StorageFileWriteRequest(
                    user_context=ctx, path=req.save_path, content=audio_bytes
                ))
        except Exception as e:
            log.error(f"[announce] Kokoro failed: {e}")
            result = {"ok": False, "error": str(e)}

    # Fallback / Alternate: Music Assistant (MASS) play_announcement
    # This is often what users mean by 'Media Assistant' integration in HA
    if not result.get("ok"):
        log.info("[announce] Attempting Music Assistant (MASS) announcement...")
        result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "mass", "play_announcement", target_player, {
            "message": req.message,
            "use_pre_announcement_signal": True
        })

    # Last resort: local Piper
    if not result.get("ok"):
        log.info("[announce] Falling back to HA Piper...")
        result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "tts", "piper", target_player, {
            "message": req.message
        })


    if result.get("ok"):
        return _ok(f"Announcement sent successfully to {target_player}.", "announce")
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

@app.post("/execute/diagnostics", response_model=ExecutionResult)
async def execute_diagnostics(req: DiagnosticRequest):
    log.info(f"[diagnostics] user={req.user_context.user} service={req.service} lines={req.lines}")
    return await diagnostics.handle_get_system_logs(req.model_dump())
