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
        DockerLogsRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
        WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, StorageFileReadRequest, StorageFileWriteRequest,
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
            DockerLogsRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, StorageFileReadRequest, StorageFileWriteRequest,
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
            DockerLogsRequest, GitOperationRequest, DeploymentRequest, VolumeInventoryRequest,
            WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, StorageFileReadRequest, StorageFileWriteRequest,
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
async def list_timers():
    return await timer.get_active_timers()

@app.post("/execute/trigger", response_model=ExecutionResult)
async def execute_trigger(payload: Dict[str, Any]):
    """Internal endpoint for Automation scheduler."""
    timer_data = payload.get("timer", {})
    log.info(f"ALARM TRIGGERED: {timer_data.get('title')}")
    # Legacy: This is where we'd play audio on target_device
    return _ok(f"Triggered {timer_data.get('title')}", "automation_trigger")


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


@app.post("/execute/volumes")
async def execute_volumes(req: VolumeInventoryRequest):
    """
    Inspect tracked Docker volumes, sizes, and backup/prune examples.
    Admin only.
    """
    return await volume_handler.handle_volumes(req)


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
    log.info(f"[announce] user={ctx.user} entity={req.entity_id}")
    
    # 1. Ensure the device is turned on (crucial for TVs)
    if req.entity_id.startswith("media_player."):
        log.info(f"[announce] Powering on {req.entity_id}")
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", req.entity_id, {})
        # Give it a tiny bit of time to wake up if it was off
        await asyncio.sleep(1.0)

    # 2. Set volume
    await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_set", req.entity_id, {"volume_level": req.volume})
    
    # Try modern tts.speak first
    result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "tts", "speak", req.entity_id, {
        "message": req.message, 
        "media_player_entity_id": req.entity_id,
        "cache": True
    })
    
    if not result.get("ok"):
        log.warning(f"[announce] tts.speak failed, trying fallback: {result.get('error')}")
        # Fallback to common google_translate_say
        # Many users have 'google_translate' or 'google_say'
        # We'll try a few common ones or a generic tts call if possible
        # For now, let's try the most common one: google_translate_say
        result = await ha_client.call_service(ctx.ha_url, ctx.ha_token, "tts", "google_translate_say", req.entity_id, {
            "message": req.message
        })

    if result.get("ok"):
        return _ok(f"Announcement sent to {req.entity_id}.", "announce")
    return _fail(f"Announcement failed after fallback: {result.get('error')}", "announce", result)

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
