# services/execution/main.py
import os
import logging
import asyncio
import httpx
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, Header, Request
try:
    from . import ha_client
    from .schemas import (
        UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
        TVCastRequest, HAServiceRequest, AnnouncementRequest,
        CalendarRequest, NoteRequest, TimerRequest, TalkRequest,
        WebSearchRequest, WebReadRequest, ExecutionResult,
        DockerLogsRequest, DockerComposeRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
        WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
        SystemLearningRequest, DiscoverySyncRequest
    )
    from .handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning
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
            CalendarRequest, NoteRequest, TimerRequest, TalkRequest,
            WebSearchRequest, WebReadRequest, ExecutionResult,
            DockerLogsRequest, DockerComposeRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
            SystemLearningRequest, DiscoverySyncRequest
        )
        from execution.handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning
        from execution.handlers import docker_logs as docker_logs_handler
        from execution.handlers import git as git_handler
        from execution.handlers import deployment as deployment_handler
        from execution.handlers import volumes as volume_handler
    except ImportError:
        import ha_client
        from schemas import (
            UserContext, LightControlRequest, MediaPlayRequest, MediaTransportRequest,
            TVCastRequest, HAServiceRequest, AnnouncementRequest,
            CalendarRequest, NoteRequest, TimerRequest, TalkRequest,
            WebSearchRequest, WebReadRequest, ExecutionResult,
            DockerLogsRequest, DockerComposeRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, StorageFileReadRequest, StorageFileWriteRequest,
            SystemLearningRequest, DiscoverySyncRequest
        )
        from handlers import light, media, climate, security, calendar, note, timer, talk, browser, workspace, storage, learning
        from handlers import docker_logs as docker_logs_handler
        from handlers import git as git_handler
        from handlers import deployment as deployment_handler
        from handlers import volumes as volume_handler

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
log = logging.getLogger("execution")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
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
    """
    Perform a Git lifecycle operation on /workspace/SharedLLM.
    Supports: status, diff, add, commit, pull, push (admin), log.
    Part of the Ouroboros ACT phase.
    """
    return await git_handler.handle_git(req)


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
    
    for svc in services:
        # Mocking DeploymentRequest for each service
        from schemas import DeploymentRequest
        # Ensure prefix
        container_name = svc if svc.startswith("sharedllm_") else f"sharedllm_{svc}"
        mock_req = DeploymentRequest(
            user_context=req.user_context,
            action=req.action if req.action != "up" else "restart", # 'up' becomes 'restart' for existing
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
    """
    Read a file from the local Git workspace. 
    Use this for source code analysis.
    """
    return await workspace.handle_workspace_read(req)


@app.post("/execute/workspace_file_write", response_model=ExecutionResult)
async def execute_workspace_file_write(req: WorkspaceFileWriteRequest):
    """
    Write or overwrite a file in the local Git workspace.
    Used for autonomous bug fixing.
    """
    return await workspace.handle_workspace_write(req)


@app.post("/execute/workspace_file_patch", response_model=ExecutionResult)
async def execute_workspace_file_patch(req: WorkspaceFilePatchRequest):
    """
    Surgically patch a file in the local Git workspace.
    Used for small bug fixes without full file overwrite.
    """
    return await workspace.handle_workspace_patch(req)


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
    # Note: Gateway is usually at http://gateway:8002 in docker
    GATEWAY_INTERNAL = os.getenv("GATEWAY_INTERNAL_URL", "http://gateway:8002")
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

@app.post("/execute/announce", response_model=ExecutionResult)
async def execute_announce(req: AnnouncementRequest):
    # Announcements are currently cross-domain (Volume + TTS)
    ctx = req.user_context
    target_player = req.entity_id
    if not target_player.startswith("media_player."):
        target_player = f"media_player.{target_player}"

    log.info(f"[announce] START user={ctx.user} target={target_player} msg='{req.message}' vol={req.volume}")
    
    # 1. Ensure the device is turned on (crucial for TVs)
    log.info(f"[announce] Step 1: Powering on {target_player}")
    pwr_res = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", target_player, {})
    log.info(f"[announce] Power on result: {pwr_res}")
    # Give it a tiny bit of time to wake up if it was off
    await asyncio.sleep(1.5)

    # 2. Set volume
    log.info(f"[announce] Step 2: Setting volume to {req.volume}")
    vol_res = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_set", target_player, {"volume_level": req.volume})
    log.info(f"[announce] Volume set result: {vol_res}")
    
    # 3. TTS Announce
    log.info(f"[announce] Step 3: Triggering TTS")
    # Most common integrations: google_translate_say, cloud_say
    # These services target the media player directly.
    tts_services = ["google_translate_say", "cloud_say", "piper"]
    
    result = {"ok": False, "error": "No TTS service succeeded"}
    
    for tts_srv in tts_services:
        log.info(f"[announce] Trying tts.{tts_srv} on {target_player}...")
        result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "tts", tts_srv, target_player, {
            "message": req.message
        })
        if result.get("ok"):
            log.info(f"[announce] SUCCESS using tts.{tts_srv}")
            break
        else:
            log.warning(f"[announce] tts.{tts_srv} failed: {result.get('error')}")

    if result.get("ok"):
        return _ok(f"Announcement sent successfully to {target_player}.", "announce")
    
    log.error(f"[announce] ALL TTS attempts failed for {target_player}")
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
    states = await ha_client.get_states(ha_url, ha_token)
    areas = await ha_client.get_areas(ha_url, ha_token)
    
    # Merge area_name into attributes for each entity
    for s in states:
        eid = s.get("entity_id")
        if eid in areas:
            if "attributes" not in s:
                s["attributes"] = {}
            s["attributes"]["area_id"] = areas[eid] # Overwrite area_id with human name for RAG
            
    return {"entities": states}

@app.get("/discovery/history")
async def discovery_history(ha_url: str, ha_token: str, entity_id: str, days: int = 1):
    return await ha_client.get_history(ha_url, ha_token, entity_id, days)

@app.get("/health")
def health():
    return {"status": "ok", "service": "execution"}
