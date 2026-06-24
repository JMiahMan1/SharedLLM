# services/gateway/main.py
import os
import logging
import json
import asyncio
import httpx
import re
import traceback
import time
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, Any, Dict
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, WebSocket, WebSocketDisconnect, Response # pyright: ignore[reportUnusedImport]
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import UploadFile
from pydantic import BaseModel

from services.gateway.schemas import ResolvedCredentials, StorageListRequest, StorageIndexRequest
from services.gateway.agent_loop import (
    execute_inference as provider_execute_inference,
    get_vram_safe_params,
    extract_action_json,
)
from services.gateway.config import INTERNAL_SECRET, CONFIG
from services.gateway.llm_providers import BaseLLMProvider, OllamaProvider, OpenRouterProvider
from services.gateway.orchestrator import get_all_settings, _get, SINGLE_TURN_TOOL_GUIDE
from services.gateway.config_validator import validate_config
from services.gateway.intent_engine import engine
from services.gateway.history import update_history, ping_redis, get_history, get_long_term_memory
from services.gateway.media_device_cache import get_last_used_device, set_last_used_device
from services.gateway.ma_ws_client import MAWebSocketClient
from services.gateway.prompts import ASSIST_SYSTEM_INSTRUCTION, CODE_HELPER_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT
from services.gateway.messaging import InferenceJobQueue, JobStatus
from services.gateway.background_worker import worker as raven_worker

from services.shared.info_endpoint import info_router

START_TIME = time.time()

# --- Setup Logging IMMEDIATELY ---
log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

# Singleton lock for Ollama inference to prevent concurrent VRAM exhaustion
INFERENCE_LOCK = asyncio.Lock()


# Backward-compatible aliases — sourced from config.py, updated by _sync_main_constants from Identity settings
from services.gateway.config import (
    IDENTITY_SVC, EXECUTION_SVC, RAG_SVC, STORAGE_SVC, LOGGING_SVC,
    WORKSPACE_RUNTIME_SVC, CONTROL_PLANE_URL, OLLAMA_TIMEOUT, ABS_TIMEOUT,
)


QWEN_GROUNDING_INSTRUCTION = """
# MISSION LOCK: Raven Autonomous Repair Protocol
1. **FOCUS**: You are a repair agent. Your ONLY mission is to resolve the specific BUG or TASK provided in the User Request.
2. **NO DISTRACTIONS**: You are strictly FORBIDDEN from acknowledging, proposing, or implementing any features, schemas, or capabilities seen in the context that are not related to the primary mission.
3. **ZERO CONVERSATION**: You MUST NOT ask questions, seek approval, or provide status updates. Your output must be 100PCT execution-oriented.
4. **TOOL MANDATE**: Every response MUST contain a valid JSON tool call. If you are 'thinking', do it within the 'comment' field of the JSON or as a concise prefix, but the JSON is mandatory.
5. **PATCH PROTOCOL**: Use 'WorkspaceFilePatchRequest' with the 'chunks' (old_text/new_text) schema for surgical edits. NEVER send ASCII art or summaries as 'content'.
6. **TERMINAL EXECUTION**: Continue until the task is verified fixed. If you stall, you are in violation of protocol.
"""

def _make_ollama_response(message: str, model: str, intent: str | None = None, debug_context: str | None = None, stream: bool = False):
    """Helper to create an Ollama-compatible response (streaming or non-streaming)."""
    if not stream:
        res = {
            "model": model,
            "created_at": datetime.now().isoformat() + "Z",
            "message": {"role": "assistant", "content": message},
            "done": True,
            "status": "SUCCESS"
        }
        if intent:
            res["intent"] = intent
        if debug_context:
            res["debug_context"] = debug_context
        return JSONResponse(res)

    async def gen():
        chunk = {
            "model": model,
            "created_at": datetime.now().isoformat() + "Z",
            "message": {"role": "assistant", "content": message},
            "done": False
        }
        yield json.dumps(chunk) + "\n"
        yield json.dumps({"model": model, "done": True}) + "\n"
    
    return StreamingResponse(gen(), media_type="application/x-ndjson")

def _make_ollama_chunk(content: str, model: str, done: bool = False):
    return {
        "model": model,
        "created_at": datetime.now().isoformat() + "Z",
        "message": {"role": "assistant", "content": content},
        "done": done
    }


def _make_openai_response(message: str, model: str, intent: str | None = None, debug_context: str | None = None, stream: bool = False):
    """Helper to create an OpenAI-compatible response (streaming or non-streaming)."""
    if not stream:
        res = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": message}, "finish_reason": "stop", "index": 0}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }
        if intent:
            res["intent"] = intent
        if debug_context:
            res["debug_context"] = debug_context
        return JSONResponse(res)

    async def gen():
        chunk = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"delta": {"content": message}, "index": 0, "finish_reason": None}]
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        stop_chunk = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(stop_chunk)}\n\n"
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(gen(), media_type="text/event-stream")

def _make_openai_chunk(content: str, model: str, finish_reason: str | None = None):
    import time
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"delta": {"content": content} if content else {}, "index": 0, "finish_reason": finish_reason}]
    }

def _make_ollama_error(message: str, model: str) -> Any:
    """Create an Ollama-compatible error response."""
    return {
        "model": model,
        "created_at": datetime.now().isoformat() + "Z",
        "message": {"role": "assistant", "content": message},
        "done": True,
        "status": "ERROR",
        "error": message
    }

def _make_openai_error(message: str, model: str) -> Any:
    """Create an OpenAI-compatible error response."""
    import time
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [],
        "error": {"message": message, "type": "model_config_error", "code": 503}
    }

# --- Imports from internal modules ---

# REDIS URL resolved at runtime from Identity
async def _get_redis_url() -> str:
    settings = await get_all_settings()
    return _get(settings, "redis_url", "redis://redis:6379/0")

# Job queue initialized lazily
job_queue: Optional[InferenceJobQueue] = None

async def get_job_queue() -> InferenceJobQueue:
    global job_queue
    if job_queue is None:
        redis_url = await _get_redis_url()
        job_queue = InferenceJobQueue(redis_url)
    return job_queue

# REDIS moved below imports

# --- Ouroboros Worker ---
log.info("Successfully imported Raven background worker.")

_DEFAULT_FAST_PATH_THRESHOLD = 0.85

# Global Inference Lock (Strategy 8: Singleton Queue)
async def fetch_global_setting(key: str, default: str = "") -> str:
    """Fetch a global setting from the Identity Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings/{key}",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                val = resp.json().get("value", default)
                return val if val != "auto" else default
    except Exception as e:
        log.warning(f"Failed to fetch global setting '{key}': {e}")
    return default


async def get_llm_settings() -> Dict[str, str]:
    """Fetches full LLM settings from Identity Service (cached)."""
    return await get_all_settings()


async def get_provider(settings: dict) -> BaseLLMProvider:
    """Instantiates the correct provider based on settings."""
    active_provider = settings.get("active_llm_provider", "ollama")
    timeout = float(_get(settings, "ollama_timeout", "600"))
    if active_provider == "openrouter":
        return OpenRouterProvider(
            api_key=settings.get("llm_cloud_api_key", ""),
            base_url=settings.get("llm_cloud_url", "https://openrouter.ai/api/v1/chat/completions"),
            timeout=timeout
        )
    else:
        local_url = _get(settings, "llm_local_url")
        if not local_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        return OllamaProvider(
            base_url=local_url,
            timeout=timeout
        )


async def call_ollama(payload: Dict[str, Any], use_chat: bool = True) -> Dict[str, Any]:
    """
    Compatibility wrapper for the legacy chat-based inference path.
    Existing tests still patch this symbol, so keep it as a stable seam.
    """
    settings = await get_llm_settings()
    provider = await get_provider(settings)
    content = await provider.generate(
        payload["model"],
        payload["messages"],
        options=payload.get("options", {}),
        chunk_callback=payload.get("chunk_callback"),
    )
    return {"message": {"content": content}}


async def execute_inference(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gateway-local compatibility wrapper for payload-based inference calls.
    `agent_loop.execute_inference` now expects provider/model/messages/options, but
    this module and its tests still call `execute_inference(payload)`.
    """
    settings = await get_llm_settings()
    provider = await get_provider(settings)
    result = await provider_execute_inference(
        provider,
        payload["model"],
        payload["messages"],
        payload.get("options", {}),
    )
    content = str(result.get("message", {}).get("content") or "")
    if "response" not in result:
        result["response"] = content
    return result


def _parse_llm_json_object(raw: Any) -> Any:
    """
    Robust JSON extractor for LLM outputs.
    Mirrors extract_action_json logic from agent_loop for consistency.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Empty LLM response")
    
    # Strip INFO logs that sometimes bleed into response
    text = re.sub(r"^INFO:.*?\n", "", text, flags=re.MULTILINE)
    
    # Priority 1: Fenced JSON block
    match = re.search(r"```json\s*(\{.*?\})(?:\s*```|$)", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass  # Fall through to outer braces
    
    # Priority 2: Outer-most braces with de-hanging
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace+1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Try to fix common trailing comma issues
            cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
            return json.loads(cleaned)
    
    raise ValueError(f"Could not extract JSON from LLM response: {text[:200]}")
async def get_assistant_model():
    settings = await get_llm_settings()
    active = settings.get("active_llm_provider", "ollama")
    model = settings.get("assistant_model")
    if not model:
        available_models = {k: v for k, v in settings.items() if "model" in k.lower() and v and v != "auto"}
        log.error(f"[get_assistant_model] No assistant model found. active_provider={active}. Available models: {available_models}")
        raise RuntimeError(f"No assistant model configured. Set assistant_model in Identity settings. Available: {available_models}")
    log.info(f"[get_assistant_model] active_provider={active} resolved_model={model}")
    return model


async def get_coding_model():
    settings = await get_llm_settings()
    active = settings.get("active_llm_provider", "ollama")
    model = settings.get("coding_model")
    if not model:
        available_models = {k: v for k, v in settings.items() if "model" in k.lower() and v and v != "auto"}
        log.error(f"[get_coding_model] No coding model found. active_provider={active}. Available models: {available_models}")
        raise RuntimeError(f"No coding model configured. Set coding_model in Identity settings. Available: {available_models}")
    log.info(f"[get_coding_model] active_provider={active} resolved_model={model}")
    return model


async def get_resident_model() -> Optional[str]:
    """Check what model is currently in VRAM to avoid unnecessary swaps."""
    try:
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{ollama_url}/api/ps")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                if models:
                    return models[0]["name"]
    except Exception:
        pass
    return None


async def get_librarian_model():
    settings = await get_llm_settings()
    active = settings.get("active_llm_provider", "ollama")
    model = settings.get("librarian_model")
    if not model:
        available_models = {k: v for k, v in settings.items() if "model" in k.lower() and v and v != "auto"}
        log.error(f"[get_librarian_model] No librarian model found. active_provider={active}. Available models: {available_models}")
        raise RuntimeError(f"No librarian model configured. Set librarian_model in Identity settings. Available: {available_models}")
    return model

async def fetch_autonomous_protocols() -> str:
    """Fetch the latest autonomous protocols from the Identity Service GlobalSettings."""
    return await fetch_global_setting("system_autonomous_protocols")


CODING_SIGNALS = (
  "python", "javascript", "typescript", "node", "react", "fastapi", "sql", "regex",
  "docker", "dockerfile", "bash", "shell", "pytest", "bug", "fix", "refactor",
  "implement", "function", "class", "stack trace", "traceback", "code", "script",
  "edit this file", "edit the file", "update this file", "change this file",
  "edit this module", "update this module", ".py", ".js", ".ts", ".tsx", ".jsx",
  "compile", "syntax", "test", "unit test", "integration test", "git"
)
LIBRARIAN_SIGNALS = (
  "summarize", "summary", "recap", "search my", "find in", "look up", "what do i have",
  "list my", "notes", "calendar", "documents", "document", "playlist", "playlists",
  "radio stations", "audiobook", "audiobooks", "library", "catalog", "catalogue",
  "files", "folders", "nextcloud", "storage", "cloud", "books", "book", "music",
  "photos", "photo", "images", "videos", "video", "code", "scripts"
)
WORKSPACE_README_ACTION_HINTS = (
  "write a readme", "create a readme", "generate a readme", "make a readme",
  "write readme", "create readme", "generate readme", "make readme",
  "readme.md", "readme file",
)
AUTONOMOUS_SIGNALS = (
    "look into the error", "analyze logs", "build the tool", "self repair",
    "self-heal", "self heal", "self-fix", "self fix", "fix the app",
    "fix the service", "fix the codebase", "fix the error", "auto-fix",
    "debug the system", "raven", "use raven", "dev loop", "agentic",
    "autonomous", "check container logs", "rebuild service", "deploy fix",
    "repair", "execute fix", "fix it", "debug it", "fix the code", "apply the fix",
    "audit the codebase", "sync workspace", "pull latest", "convert them to",
    "review requirements", "check dependencies", "report any conflicts",
)
TTS_SIGNALS = (
  "tts", "audiobook", "read this", "make audible", "clean for speech", 
  "narration", "voiceover", "ebook to speech", "pdf to speech", "prosody", "ssml"
)

# --- Capability Configuration ---
# Maps intents to the credential fields required in ResolvedCredentials
INTENT_CAPABILITY_MAP = {
    "turn_on": ["ha_url", "ha_token"],
    "turn_off": ["ha_url", "ha_token"],
    "play_media": ["ha_url", "ha_token"],
    "media_transport": ["ha_url", "ha_token"],
    "pause_media": ["ha_url", "ha_token"],
    "open_garage": ["ha_url", "ha_token"],
    "close_garage": ["ha_url", "ha_token"],
    "toggle": ["ha_url", "ha_token"],
    "set_brightness": ["ha_url", "ha_token"],
    "ha_status": ["ha_url", "ha_token"],
    "sync_ha": ["ha_url", "ha_token"],
    "workspace_coding": ["github_token"],
    "github": ["github_token"],
    "index_storage": ["nextcloud_url", "nextcloud_user", "nextcloud_pass"],
    "storage_search": ["nextcloud_url", "nextcloud_user", "nextcloud_pass"],
    "read_file": ["nextcloud_url", "nextcloud_user", "nextcloud_pass"],
    "storage_status": ["nextcloud_url", "nextcloud_user", "nextcloud_pass"],
    "self_repair": [],
    "dev_loop": []
}

HUMAN_READABLE_CAPABILITIES = {
    "ha_url": "Home Assistant URL",
    "ha_token": "Home Assistant Token",
    "github_token": "GitHub Personal Access Token",
    "nextcloud_url": "Nextcloud URL",
    "nextcloud_user": "Nextcloud Username",
    "nextcloud_pass": "Nextcloud Password"
}

# --- Global Clients ---
_original_async_client = httpx.AsyncClient
_global_http_client: Optional[httpx.AsyncClient] = None
_global_http_client_loop: Optional[asyncio.AbstractEventLoop] = None
_dns_recovery_lock = asyncio.Lock()

def get_http_client() -> httpx.AsyncClient:
    """Lazy initializer for the global httpx client to ensure test compatibility."""
    global _global_http_client, _global_http_client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _global_http_client is None or _global_http_client_loop != current_loop:
        _global_http_client = _original_async_client(
            timeout=httpx.Timeout(300.0, connect=30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        _global_http_client_loop = current_loop
    return _global_http_client

async def recreate_http_client():
    """Close the current client and create a new one with fresh DNS resolution.
    This is needed when DNS changes (e.g., dns-sync restart) cause stale keepalive
    connections to fail with empty httpx.RequestError messages."""
    async with _dns_recovery_lock:
        global _global_http_client, _global_http_client_loop
        if _global_http_client is not None:
            log.info("[DNSRecovery] Closing stale HTTP client to refresh DNS resolution")
            await _global_http_client.aclose()
            _global_http_client = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        _global_http_client = _original_async_client(
            timeout=httpx.Timeout(300.0, connect=30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        _global_http_client_loop = current_loop
        log.info("[DNSRecovery] New HTTP client created with fresh DNS resolution")

def _is_dns_failure(e: httpx.RequestError) -> bool:
    """Detect DNS-related failures that indicate stale DNS cache.
    These manifest as httpx.RequestError with empty messages when using
    keepalive connections that were established before a DNS change."""
    msg = str(e).strip()
    # Empty message on a keepalive connection = stale DNS resolution
    if not msg:
        return True
    # Docker DNS failure patterns
    dns_patterns = ["nodename", "noname", "could not resolve", "getaddrinfo",
                    "Name or service not known", "DNS", "resolve"]
    return any(p.lower() in msg.lower() for p in dns_patterns)

@asynccontextmanager
async def borrow_http_client():
    yield get_http_client()

async def retry_http_request(func, service_name: str, max_retries: int = 2, base_delay: float = 0.1, dns_recovery: bool = True):
    """Retry an httpx request with exponential backoff for transient errors.
    Detects DNS-related failures (empty RequestError messages on stale connections)
    and automatically recreates the HTTP client to refresh DNS resolution."""
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except httpx.RequestError as e:
            if attempt == max_retries:
                log.error(f"{service_name}: All {max_retries + 1} attempts failed: {e}")
                raise
            # If this looks like a DNS failure and recovery is enabled, recreate the client
            if dns_recovery and _is_dns_failure(e) and attempt == 0:
                log.warning(f"{service_name}: DNS-related failure detected, recreating HTTP client")
                try:
                    await recreate_http_client()
                except Exception as rec_err:
                    log.error(f"{service_name}: Failed to recreate HTTP client: {rec_err}")
            delay = base_delay * (2 ** attempt)
            log.warning(f"{service_name}: RequestError (attempt {attempt+1}/{max_retries+1}): {e}. Retrying in {delay}s")
            await asyncio.sleep(delay)
    raise Exception(f"Unexpected: exhausted retries for {service_name}")

# Global config validation state
_config_validation_result = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config_validation_result
    # Resolve runtime config from Identity service
    from services.config import resolve_runtime_config
    await resolve_runtime_config()
    
    log.info("Gateway starting up...")
    engine.load()
    # Initialize the client explicitly on startup
    get_http_client()
    jq = await get_job_queue()
    await jq.connect()
    log.info("Gateway initialized with FIFO Inference Queue")
    log.info("Gateway initialized with standardized 45s timeouts")
    
    # Validate critical configuration from Identity
    try:
        settings = await get_all_settings()
        _config_validation_result = validate_config(settings)
        log.info(f"[ConfigValidation] {_config_validation_result.summary()}")
        if not _config_validation_result.is_functional:
            log.critical(f"[ConfigValidation] Gateway has critical config failures: {_config_validation_result.critical_failures}")
        if _config_validation_result.is_degraded:
            log.warning(f"[ConfigValidation] Gateway is degraded: {_config_validation_result.required_failures}")
    except Exception as e:
        log.critical(f"[ConfigValidation] Failed to validate config: {e}")
        _config_validation_result = None
    if raven_worker:
        await raven_worker.start()
    
    yield

    log.info("Gateway shutting down...")
    if raven_worker:
        await raven_worker.stop()
    
    global _global_http_client
    if _global_http_client:
        await _global_http_client.aclose()
        _global_http_client = None

app = FastAPI(title="Jarvis OS Gateway", version="1.0.0", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://ai.local",
        "http://ai.local:8080",
        "https://ai.local",
        "http://ai.local",
        "https://jarvis.sumemail.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(info_router)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    err_msg = f"Gateway Error: {type(exc).__name__}: {str(exc)}"
    log.error(f"{err_msg}\n{tb}")
    return JSONResponse(
      status_code=500,
      content={"status": "ERROR", "message": "Internal Gateway Error", "detail": str(exc), "traceback": tb.splitlines()}
    )

# --- Global Health & Readiness ---
@app.delete("/api/history")
async def clear_history_endpoint(request: Request):
    """Clears conversation history for the current user."""
    try:
        body = await request.json()
        creds_data = await resolve_identity(body)
        user_id = creds_data.get("user") or ""
        
        from services.gateway.history import _redis, _get_history_key
        key = _get_history_key(user_id)
        _redis.delete(key)
        
        return {"status": "SUCCESS", "message": f"History cleared for {user_id}."}
    except Exception as e:
        log.error(f"History clear failed: {e}")
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}

@app.get("/health/ready")
async def readiness():
    """Verifies all downstream services are reachable."""
    services = {
      "identity": f"{IDENTITY_SVC}/health",
      "execution": f"{EXECUTION_SVC}/health",
      "rag": f"{RAG_SVC}/health",
      "storage": f"{STORAGE_SVC}/health",
      "logging": f"{LOGGING_SVC}/health",
      "workspace_runtime": f"{WORKSPACE_RUNTIME_SVC}/health",
      "control_plane": f"{CONTROL_PLANE_URL}/health",
    }

    services_status: dict[str, str] = {}
    service_details: dict[str, dict] = {}
    results: dict[str, Any] = {"status": "READY", "services": services_status, "service_details": service_details}
    all_ok = True

    async with httpx.AsyncClient(timeout=2.0) as client:
      for name, url in services.items():
          try:
            resp = await client.get(url)
            if resp.status_code == 200:
                services_status[name] = "OK"
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        service_details[name] = {
                            "git_sha": data.get("git_sha", "unknown"),
                            "start_time": data.get("start_time", None)
                        }
                except Exception:
                    pass
            else:
                services_status[name] = f"ERROR ({resp.status_code})"
                all_ok = False
          except Exception:
            services_status[name] = "UNREACHABLE"
            all_ok = False

    # The Gateway itself is running if we are responding to this request
    services_status["gateway"] = "OK"
    service_details["gateway"] = {
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME
    }

    if ping_redis():
      services_status["redis"] = "OK"
      service_details["redis"] = {
          "git_sha": "n/a",
          "start_time": None
      }
    else:
      services_status["redis"] = "ERROR"
      all_ok = False

    # Include config validation status
    if _config_validation_result:
        results["config"] = {
            "functional": _config_validation_result.is_functional,
            "degraded": _config_validation_result.is_degraded,
            "critical_failures": _config_validation_result.critical_failures,
            "required_failures": _config_validation_result.required_failures,
            "summary": _config_validation_result.summary(),
        }
        if _config_validation_result.is_degraded:
            all_ok = False

    if not all_ok:
      results["status"] = "DEGRADED"
    
    return results

# --- Documentation Endpoint ---
@app.get("/api/docs/{doc_name}")
async def get_documentation(
    doc_name: str, 
    request: Request
):
    """
    Serves system documentation securely from the /docs folder or root markdown files.
    """
    # 1. Authentication Check (Basic)
    # For docs, we'll check for a valid API Key or internal secret
    api_key = request.headers.get("X-API-Key")
    internal_secret = request.headers.get("X-Internal-Secret")
    auth_header = request.headers.get("Authorization")
    
    if not api_key and auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header.split(" ")[1]
    
    if not internal_secret == INTERNAL_SECRET and not api_key:
        # Fallback: check query params if needed, but header is preferred
        api_key = request.query_params.get("api_key")
        
    if not internal_secret == INTERNAL_SECRET and not api_key:
        raise HTTPException(status_code=401, detail="Authentication required to view system docs")

    # 2. Path Security & Resolution
    # We allow files from the 'docs' directory or specific root files
    base_dir = Path(__file__).parent.parent.parent
    docs_dir = base_dir / "docs"
    
    # Whitelist of allowed root-level files (everything else resolved from docs/)
    allowed_root_files = [
        "README.md",
    ]
    
    # Normalize doc_name
    if not doc_name.endswith(".md"):
        doc_name += ".md"
        
    # Prevent path traversal using resolution
    try:
        if doc_name in allowed_root_files:
            target_path = (base_dir / doc_name).resolve()
            # Must stay in base_dir
            target_path.relative_to(base_dir)
        else:
            target_path = (docs_dir / doc_name).resolve()
            # Must stay in docs_dir
            target_path.relative_to(docs_dir)
    except (ValueError, RuntimeError):
        log.warning(f"SECURITY: Blocked doc path traversal attempt: {doc_name}")
        raise HTTPException(status_code=403, detail="Forbidden: Document path traversal detected")
        
    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail=f"Documentation '{doc_name}' not found")
        
    try:
        content = target_path.read_text()
        return {"name": doc_name, "content": content}
    except Exception as e:
        log.error(f"Error reading doc {doc_name}: {e}")
        raise HTTPException(status_code=500, detail="Error reading documentation file")

# --- Logging Helper ---
async def emit_log(level: str, message: str, context: dict | None = None):
    try:
      from services.gateway.agent_loop import sanitize_for_llm
      safe_context = sanitize_for_llm(context) if context else None
      safe_message = sanitize_for_llm(message)
      async with httpx.AsyncClient() as client:
          await client.post(
            f"{LOGGING_SVC}/log",
            json={"service": "gateway", "level": level, "message": safe_message, "context": safe_context},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=1.0
          )
    except Exception:
      pass

@app.get("/api/logs")
async def get_api_logs(limit: int = 50):
    async with httpx.AsyncClient() as client:
      resp = await client.get(f"{LOGGING_SVC}/logs", params={"limit": limit})
      return resp.json()

# --- Contextualization Logic ---
async def contextualize_query(query: str, history: list) -> str:
    """Uses history to rewrite ambiguous queries like 'yes' or 'do it'."""
    if not history:
        return query

    q_lower = query.lower().strip().strip("!.")
    if len(q_lower.split()) > 4 and q_lower not in ["play the first one"]:
        return query

    hist_str = ""
    for m in history[-3:]:
        if not isinstance(m, dict):
            continue
        role = "USER" if m.get("role") == "user" else "ASSISTANT"
        hist_str += f"{role}: {m.get('content')}\n"

    prompt = f"Given history:\n{hist_str}\nRewrite follow-up to standalone command.\nFollow-up: {query}\nCommand:"
    try:
        settings = await get_llm_settings()
        provider = await get_provider(settings)
        
        assistant = await get_assistant_model()
        coding = await get_coding_model()
        resident = await get_resident_model()
        
        # If the coding model (usually large/slow to swap) is already resident,
        # use it for rewriting instead of swapping back to the assistant model.
        model_to_use = assistant
        if resident == coding:
            model_to_use = coding
            log.info(f"[Context] Using resident coding model '{coding}' for rewrite to avoid swap.")
        
        messages = [{"role": "user", "content": prompt}]
        rewritten = await provider.generate(model_to_use, messages, options={"temperature": 0.0, "num_predict": 256})
        if rewritten:
            rewritten = rewritten.strip().strip('"')
            log.info(f"[Context] '{query}' -> '{rewritten}'")
            return rewritten
    except Exception as e:
        log.warning(f"Contextualization failed: {e}")
    return query


async def select_model_for_query(query: str) -> str:
    """Route obvious coding, autonomous, and librarian tasks to specialized models."""
    q = (query or "").lower()

    if any(token in q for token in CODING_SIGNALS) or any(token in q for token in AUTONOMOUS_SIGNALS):
      return await get_coding_model()
    if any(token in q for token in LIBRARIAN_SIGNALS) or any(token in q for token in TTS_SIGNALS):
      return await get_librarian_model()
    return await get_assistant_model()


def select_system_instruction_for_query(query: str, selected_model: str) -> str:
    from services.gateway.prompts import RAVEN_AUTONOMOUS_PROTOCOL, RAVEN_NARRATOR_PROTOCOL
    q = (query or "").lower()
    if any(token in q for token in TTS_SIGNALS):
      return RAVEN_NARRATOR_PROTOCOL
    if any(token in q for token in AUTONOMOUS_SIGNALS):
      return RAVEN_AUTONOMOUS_PROTOCOL
    if any(token in q for token in CODING_SIGNALS):
      return CODE_HELPER_SYSTEM_INSTRUCTION
    return ASSIST_SYSTEM_INSTRUCTION


def is_coding_query(query: str) -> bool:
    q = (query or "").lower()
    return any(token in q for token in CODING_SIGNALS)


def is_librarian_query(query: str) -> bool:
    q = (query or "").lower()
    return not is_coding_query(q) and any(token in q for token in LIBRARIAN_SIGNALS)


def should_search_storage_for_code_query(query: str) -> bool:
    q = (query or "").lower()
    storage_code_signals = (
      "file", "files", "path", "module", "service", "function", "class", "repo",
      "repository", "workspace", "branch", "commit", "diff", "readme", "architecture",
      "design", "nextcloud", "storage", "docs", ".py", ".js", ".ts", ".md", "/"
    )
    return any(token in q for token in storage_code_signals)


def extract_media_request(query: str) -> tuple[str | None, str | None]:
    """
    Pull a likely media search string and target device name from commands like:
    - Play Brandon Lake on Office TV
    - Listen to jazz on the kitchen speaker
    """
    cleaned = (query or "").strip().strip("?.!")
    if not cleaned:
      return None, None

    # Capture common "play/listen/resume <content> on <device>" phrasing.
    match = re.match(
      r"^(?:play|listen to|listen|resume)\s+(.+?)(?:\s+on\s+(.+))?$",
      cleaned,
      flags=re.IGNORECASE,
    )
    if not match:
      return None, None

    media_query = match.group(1).strip(" \"'")
    device_name = match.group(2).strip(" \"'") if match.group(2) else None
    if device_name:
      device_name = re.sub(r"^(?:the)\s+", "", device_name, flags=re.IGNORECASE)
    return (media_query or None, device_name or None)


def is_likely_video_request(query: str) -> bool:
    q = (query or "").lower()
    video_signals = (
      "watch ",
      " video",
      "youtube",
      "youtu.be",
      "movie",
      "episode",
      "netflix",
      "hulu",
      "disney",
      "prime video",
      "vimeo",
    )
    return any(signal in q for signal in video_signals)


def extract_media_transport_command(query: str) -> str | None:
    q = (query or "").strip().lower()
    if not q:
      return None

    command_patterns = (
      (r"\b(?:pause|hold)\b", "pause"),
      (r"\bresume\b", "resume"),
      (r"\bstop\b", "stop"),
      (r"\b(?:back|previous|go back)\b", "previous"),
      (r"\b(?:next|skip)\b", "next"),
    )
    for pattern, command in command_patterns:
      if re.search(pattern, q, flags=re.IGNORECASE):
          return command
    return None


def is_time_or_date_query(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return False
    return any(
        phrase in q for phrase in (
            "what time is it",
            "current time",
            "time is it",
            "what is the date",
            "what date is it",
            "today's date",
            "todays date",
            "current date",
            "date today",
        )
    )


async def build_time_or_date_response(query: str) -> str:
    """Get timezone from Identity settings at runtime, not hardcoded."""
    try:
        from services.gateway.orchestrator import get_all_settings
        settings = await get_all_settings()
        tz_name = settings.get("timezone", "America/Phoenix")
    except Exception:
        tz_name = "America/Phoenix"
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now().astimezone()
        tz_name = str(now.tzinfo or tz_name)

    q = (query or "").strip().lower()
    wants_time = any(token in q for token in ("time", "clock"))
    wants_date = "date" in q or "day" in q or "today" in q

    if wants_time and wants_date:
        return f"It is {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d, %Y')} ({tz_name})."
    if wants_date and not wants_time:
        return f"Today is {now.strftime('%A, %B %d, %Y')} ({tz_name})."
    return f"It is {now.strftime('%I:%M %p')} ({tz_name})."


def has_explicit_action_request(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
      return False

    action_patterns = (
      r"\bturn on\b",
      r"\bturn off\b",
      r"\bswitch on\b",
      r"\bswitch off\b",
      r"\bpower on\b",
      r"\bpower off\b",
      r"\bplay\b",
      r"\bpause\b",
      r"\bstop\b",
      r"\bresume\b",
      r"\bopen\b",
      r"\bclose\b",
    )
    return any(re.search(pattern, q, flags=re.IGNORECASE) for pattern in action_patterns)


def requests_status_followup(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
      return False

    followup_signals = (
      "recheck",
      "check again",
      "status after",
      "state after",
      "afterward",
      "afterwards",
      "after that",
    )
    return any(signal in q for signal in followup_signals)


def wants_workspace_readme_generation(query: str) -> bool:
    q = (query or "").strip().lower()
    # Raven autonomous tasks should not take the fast readme path
    if "raven" in q:
        return False
    if "readme" not in q:
        return False
    action_requested = any(signal in q for signal in WORKSPACE_README_ACTION_HINTS)
    workspace_scoped = any(signal in q for signal in ("workspace", "repo", "repository", "folder", "temp", "nextcloud", "git"))
    return action_requested and workspace_scoped


def wants_direct_code_orchestration(query: str) -> bool:
    q = (query or "").strip().lower()
    # Raven autonomous tasks must go through AgentLoop, not the Librarian's fast code path
    if "raven" in q:
        return False
    action_requested = any(
        signal in q
        for signal in ("create", "write", "edit", "update", "modify", "add", "patch", "refactor")
    )
    file_scoped = any(
        signal in q
        for signal in (
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            "pytest file",
            "test file",
            "named ",
            "file ",
            "temp/",
            "workspace",
            "repo",
            "repository",
        )
    )
    return action_requested and file_scoped


async def workspace_runtime_request(method: str, path: str, *, json_payload: dict | None = None, params: dict | None = None) -> Any:
    client = get_http_client()

    resp = await client.request(
      method,
      f"{WORKSPACE_RUNTIME_SVC}{path}",
      json=json_payload,
      params=params,
      headers={"X-Internal-Secret": INTERNAL_SECRET},
      timeout=120.0,
    )
    if resp.status_code != 200:
      raise HTTPException(status_code=resp.status_code, detail=f"Workspace runtime request failed: {resp.text}")
    data = resp.json()
    if not isinstance(data, dict):
      raise HTTPException(status_code=500, detail=f"Workspace runtime returned invalid payload for {path}")
    return data


async def resolve_chat_workspace(body: dict, user_id: str) -> dict | None:
    workspace_id = str(body.get("workspace_id") or "").strip()
    params = {"rag_user": user_id}
    workspaces_data = await workspace_runtime_request("GET", "/workspaces", params=params)
    workspaces = workspaces_data.get("workspaces", []) if isinstance(workspaces_data, dict) else []
    if not isinstance(workspaces, list):
      return None

    async def try_bootstrap(item: dict) -> dict | None:
        candidate_id = str(item.get("id") or "").strip()
        if not candidate_id:
            return None
        try:
            bootstrap_data = await workspace_runtime_request(
                "POST",
                "/workspaces/bootstrap",
                json_payload={"workspace_id": candidate_id, "rag_user": user_id},
            )
        except HTTPException:
            return None
        bootstrapped = bootstrap_data.get("workspace")
        return bootstrapped if isinstance(bootstrapped, dict) else None

    available = [item for item in workspaces if isinstance(item, dict) and item.get("available")]
    if workspace_id:
      requested = next((item for item in workspaces if isinstance(item, dict) and item.get("id") == workspace_id), None)
      if requested and not requested.get("available"):
          bootstrapped = await try_bootstrap(requested)
          if bootstrapped:
              return bootstrapped
      for item in available:
          if item.get("id") == workspace_id:
              return item
      return None

    for item in available:
        if str(item.get("scope") or "user") == "user":
            return item
    for item in workspaces:
        if not isinstance(item, dict):
            continue
        if str(item.get("scope") or "user") != "user":
            continue
        if item.get("available"):
            continue
        bootstrapped = await try_bootstrap(item)
        if bootstrapped:
            return bootstrapped
    return available[0] if available else None


async def build_workspace_readme_context(workspace: dict, user_id: str) -> str:
    workspace_id = workspace.get("id")
    if not workspace_id:
      raise HTTPException(status_code=500, detail="Workspace runtime did not return an id")

    list_data = await workspace_runtime_request(
      "POST",
      "/files/list",
      json_payload={
        "workspace_id": workspace_id,
        "rag_user": user_id,
        "relative_path": ".",
        "recursive": True,
        "max_depth": 2,
        "max_entries": 120,
        "include_dirs": True,
      },
    )
    entries = list_data.get("entries", []) if isinstance(list_data, dict) else []
    listing_lines = []
    if isinstance(entries, list):
      for item in entries[:120]:
          if not isinstance(item, dict):
            continue
          path = item.get("path")
          if not path:
            continue
          suffix = "/" if item.get("is_dir") else ""
          listing_lines.append(f"- {path}{suffix}")

    read_candidates = [
      "README.md",
      "services/README.md",
      "docs/architecture.md",
      "docs/workspace_runtime.md",
      "config/workspaces.json",
    ]
    file_sections = []
    for relative_path in read_candidates:
        try:
          file_data = await workspace_runtime_request(
            "POST",
            "/files/read",
            json_payload={
              "workspace_id": workspace_id,
              "rag_user": user_id,
              "relative_path": relative_path,
              "max_bytes": 12000,
            },
          )
        except HTTPException:
          continue
        content = str(file_data.get("content") or "")
        if not content:
          continue
        file_sections.append(f"## {relative_path}\n{content[:12000]}")

    git_status = ""
    try:
      status_data = await workspace_runtime_request(
        "POST",
        "/git/status",
        json_payload={"workspace_id": workspace_id, "rag_user": user_id},
      )
      branch = str(status_data.get("branch") or "").strip() or "unknown"
      porcelain = status_data.get("porcelain") or []
      if isinstance(porcelain, list):
          status_lines = "\n".join(f"- {line}" for line in porcelain[:20]) or "- clean"
      else:
          status_lines = "- unavailable"
      git_status = f"Current branch: {branch}\nGit status:\n{status_lines}"
    except HTTPException:
      git_status = "Git status unavailable."

    listing_text = "\n".join(listing_lines) if listing_lines else "- no entries listed"
    file_text = "\n\n".join(file_sections) if file_sections else "No reference files could be read."
    return (
      f"Workspace ID: {workspace_id}\n"
      f"Workspace path: {workspace.get('resolved_path', 'unknown')}\n"
      f"Top-level and nearby workspace listing:\n{listing_text}\n\n"
      f"{git_status}\n\n"
      f"Reference file excerpts:\n{file_text}"
    )


async def generate_workspace_readme_via_coding_model(
    body: dict,
    user_id: str,
    refined_query: str,
    selected_model: str,
    should_stream: bool,
    is_openai: bool,
) -> JSONResponse | dict | StreamingResponse:
    workspace = await resolve_chat_workspace(body, user_id)
    if not workspace:
      msg = "I could not resolve an available workspace for this README generation request."
      if is_openai:
        return _make_openai_response(msg, selected_model, stream=should_stream)
      return _make_ollama_response(msg, selected_model, stream=should_stream)

    workspace_context = await build_workspace_readme_context(workspace, user_id)
    prompt = (
      "You are generating a README.md file for temp/ inside the current workspace.\n"
      "Use only the provided workspace context.\n"
      "Do not invent services, files, or capabilities that are not supported by the context.\n"
      "Write concise markdown only, with no code fences and no preamble.\n\n"
      f"Workspace context:\n{workspace_context}\n\n"
      f"User request:\n{refined_query}\n"
    )
    payload = {
      "model": selected_model,
      "messages": [
        {"role": "system", "content": CODE_HELPER_SYSTEM_INSTRUCTION},
        {"role": "user", "content": prompt},
      ],
      "stream": False,
      "options": {"num_predict": 4096},
    }
    data = await execute_inference(payload)
    generated = ""
    msg_obj = data.get("message")
    if isinstance(msg_obj, dict):
        generated = str(msg_obj.get("content") or "")
    else:
        generated = str(data.get("response") or "")
    if not generated.strip():
      raise HTTPException(status_code=502, detail="Coding model returned an empty README response")

    workspace_id = workspace.get("id")
    await workspace_runtime_request(
      "POST",
      "/files/write",
      json_payload={
        "workspace_id": workspace_id,
        "rag_user": user_id,
        "relative_path": "temp/README.md",
        "content": generated,
        "create_parents": True,
      },
    )
    sync_data = await workspace_runtime_request(
      "POST",
      "/provider/sync/file",
      json_payload={
        "workspace_id": workspace_id,
        "rag_user": user_id,
        "relative_path": "temp/README.md",
        "create_parents": True,
        "verify": True,
      },
    )

    provider_path = sync_data.get("provider_path", "/Code/SharedLLM/temp/README.md")
    response_message = (
      f"I generated temp/README.md in workspace '{workspace_id}' and synced it to {provider_path}.\n\n"
      f"{generated}"
    )
    if is_openai:
      return _make_openai_response(response_message, selected_model, stream=should_stream)
    return _make_ollama_response(response_message, selected_model, stream=should_stream)


async def orchestrate_code_change(
    body: dict,
    user_id: str,
    refined_query: str,
    selected_model: str,
    should_stream: bool,
    is_openai: bool,
) -> JSONResponse | dict | StreamingResponse:
    workspace = await resolve_chat_workspace(body, user_id)
    if not workspace:
        msg = "I could not resolve an available workspace for this code orchestration request."
        if is_openai:
            return _make_openai_response(msg, selected_model, stream=should_stream)
        return _make_ollama_response(msg, selected_model, stream=should_stream)

    workspace_id = workspace.get("id")
    workspace_context = await build_workspace_readme_context(workspace, user_id)
    
    prompt = (
        "### Task: Plan a Code Change\n"
        "Analyze the user request and provide a precise execution plan.\n\n"
        "### Instructions:\n"
        "1. Identify the 'relative_path' for the file.\n"
        "2. Provide the full 'content' for the file.\n"
        "3. Write a detailed 'reasoning' (2-3 sentences) explaining the change and its structure.\n"
        "4. **Verification Command**: If tests are needed, provide a 'test_cmd' that uses pytest and targets the smallest relevant scope.\n"
        "   - Example: 'pytest tests/test_feature.py -q'.\n"
        "   - Linting is handled automatically by the workspace runtime, so do not emit flake8 or eslint commands here.\n\n"
        "### Return ONLY JSON:\n"
        "{\n"
        '  "relative_path": "string",\n'
        '  "content": "string",\n'
        '  "reasoning": "string",\n'
        '  "test_cmd": "string (optional)"\n'
        "}\n\n"
        f"### Workspace Context:\n{workspace_context}\n\n"
        f"### User Request: {refined_query}\n"
    )
    
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": CODE_HELPER_SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    
    data = await execute_inference(payload)
    try:
        plan = data.get("message", {}).get("content") or data.get("response")
        plan_data = _parse_llm_json_object(plan)
    except Exception as e:
        log.error(f"Failed to parse coding plan: {e}\nRaw: {plan}")
        raise HTTPException(status_code=500, detail="Invalid JSON plan from coding model")

    rel_path = plan_data.get("relative_path")
    content = plan_data.get("content")
    reasoning = plan_data.get("reasoning", "No reasoning provided.")
    test_cmd = plan_data.get("test_cmd")
    
    if not rel_path or content is None:
        raise HTTPException(status_code=400, detail="Coding plan missing relative_path or content")

    def _pytest_targets_from_command(command: str | None) -> list[str]:
        if not command:
            return []
        parts = str(command).strip().split()
        if not parts:
            return []
        normalized = parts[0].lower()
        if normalized not in {"pytest", "python", "python3"}:
            return []
        if normalized in {"python", "python3"}:
            if len(parts) < 3 or parts[1] != "-m" or parts[2] != "pytest":
                return []
            parts = parts[3:]
        else:
            parts = parts[1:]
        targets = [part for part in parts if part and not part.startswith("-")]
        return targets

    pytest_targets = _pytest_targets_from_command(test_cmd)

    # Call the workflow endpoint
    workflow_payload = {
        "workspace_id": workspace_id,
        "rag_user": user_id,
        "relative_path": rel_path,
        "content": content,
        "commit_message": f"feat: {refined_query[:50]}",
        "lint_paths": [rel_path],
        "pytest_targets": pytest_targets,
        "auto_create_review_branch": True,
        "review_branch_prefix": "raven",
        "push": bool(pytest_targets),
        "sync_to_provider": True,
        "create_parents": True,
    }
    
    result = await workspace_runtime_request("POST", "/workflow/write-sync-commit", json_payload=workflow_payload)
    review = result.get("review") or {}
    review_summary = review.get("summary") or {}
    pytest_summary = review_summary.get("pytest") or {}
    
    summary = (
        f"### Code Orchestration Success\n\n"
        f"**File**: `{rel_path}`\n"
        f"**Action**: Autonomous creation and verification.\n\n"
        f"**Developer Reasoning & Description**:\n{reasoning}\n\n"
        f"**Workflow Result**:\n"
        f"- **Commit**: `{result.get('commit', {}).get('commit', 'N/A')}`\n"
        f"- **Review Branch**: `{review.get('head', 'N/A')}`\n"
        f"- **Base Branch**: `{review.get('base', 'N/A')}`\n"
        f"- **Sync**: {'SUCCESS' if result.get('provider_sync') else 'SKIPPED'}\n"
        f"- **Verification**: {'PASS' if pytest_summary.get('passed') else 'Lint only / no pytest'}\n"
    )
    
    if pytest_summary:
        summary += f"\n**Pytest Targets**: `{', '.join(pytest_summary.get('targets', [])) or 'none'}`\n"
    
    if is_openai:
      return _make_openai_response(summary, selected_model, stream=should_stream)
    return _make_ollama_response(summary, selected_model, stream=should_stream)


def resolve_media_target(query: str, entities: list[dict], media_type: str | None = None, cached_device: str | None = None) -> str | None:
    """
    Resolve media player entity from query using device capabilities/metadata.
    For video: prefer Cast/Chromecast devices (support play_media with local streams).
    For music: prefer Music Assistant queue/speaker entities.
    Names are only used for grouping/matching requested device, not for capability detection.
    Returns None when no entity can be confidently resolved.
    """
    _, requested_device = extract_media_request(query)
    requested_lower = requested_device.lower() if requested_device else ""

    def _normalize_name(value: str) -> str:
      cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
      cleaned = re.sub(r"\b(remote|cast|chrome)\b", " ", cleaned)
      return " ".join(cleaned.split())

    requested_normalized = _normalize_name(requested_lower)

    def _matches_requested_device(entity: dict) -> bool:
      eid = entity.get("entity_id", "")
      if requested_normalized:
          attrs = entity.get("attributes") or {}
          friendly = str(attrs.get("friendly_name") or "")
          friendly_normalized = _normalize_name(friendly)
          if not friendly_normalized:
              return False
          return (
              friendly_normalized == requested_normalized
              or requested_normalized in friendly_normalized
              or friendly_normalized in requested_normalized
          )
      if cached_device and eid == cached_device:
          return True
      return False

    def _is_cast_device(entity: dict) -> bool:
      """Check if entity is a Chromecast/Cast device based on capabilities."""
      attrs = entity.get("attributes") or {}
      app_id = str(attrs.get("app_id") or "").lower()
      app_name = str(attrs.get("app_name") or "").lower()
      device_class = str(attrs.get("device_class") or "").lower()
      if device_class == "speaker":
          return False
      if "cast" in app_id or "cast" in app_name:
          return True
      if app_id == "cc1ad845" or "default media receiver" in app_name:
          return True
      if not device_class and "music_assistant" not in app_id:
          return True
      return False

    def _is_ma_speaker(entity: dict) -> bool:
      """Check if entity is a Music Assistant speaker."""
      attrs = entity.get("attributes") or {}
      app_id = str(attrs.get("app_id") or "").lower()
      source = str(attrs.get("source") or "").lower()
      device_class = str(attrs.get("device_class") or "").lower()
      return device_class == "speaker" and ("music_assistant" in app_id or "music assistant" in source)

    def _score(entity: dict) -> tuple[int, str]:
      eid = entity.get("entity_id", "")
      attrs = entity.get("attributes") or {}
      friendly = str(attrs.get("friendly_name") or "").lower()
      friendly_normalized = _normalize_name(friendly)
      state = str(entity.get("state") or "").lower()
      device_class = str(attrs.get("device_class") or "").lower()

      score = 0
      if requested_lower and requested_lower in friendly:
          score += 100
      if requested_normalized and requested_normalized == friendly_normalized:
          score += 120
      elif requested_normalized and requested_normalized in friendly_normalized:
          score += 80
      elif requested_normalized and friendly_normalized in requested_normalized:
          score += 60
      if state not in {"unavailable", "unknown"}:
          score += 10
      if cached_device and eid == cached_device:
          score += 50

      if media_type == "video":
          if _is_cast_device(entity):
              score += 200
          if _is_ma_speaker(entity):
              score -= 200
      elif media_type == "power":
          if device_class == "tv":
              score += 200
          if _is_cast_device(entity):
              score -= 100
          if _is_ma_speaker(entity):
              score -= 200
      else:
          if _is_ma_speaker(entity):
              score += 200

      return score, eid

    candidates = [e for e in entities if e.get("entity_id", "").startswith("media_player.")]
    if not candidates:
      return None

    if requested_normalized:
      matched_candidates = [entity for entity in candidates if _matches_requested_device(entity)]
      if matched_candidates:
          candidates = matched_candidates
      else:
          return None
    elif cached_device:
      matched_candidates = [entity for entity in candidates if _matches_requested_device(entity)]
      if matched_candidates:
          candidates = matched_candidates
      else:
          return None
    else:
      return None

    ranked = sorted((_score(e) for e in candidates), reverse=True)
    best_score, best_eid = ranked[0]
    return best_eid if best_score > 0 else None


def resolve_video_target(query: str, entities: list[dict], cached_device: str | None = None) -> str | None:
    """
    Resolve a cast/video-capable media target for video-like requests.
    Prefer entities whose friendly name matches the requested device and avoid
    Music Assistant queues for video playback.
    Returns None when no entity can be confidently resolved.
    """
    _, requested_device = extract_media_request(query)
    requested_lower = requested_device.lower() if requested_device else ""

    def _normalize_name(value: str) -> str:
      cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
      cleaned = re.sub(r"\b(remote)\b", " ", cleaned)
      return " ".join(cleaned.split())

    requested_normalized = _normalize_name(requested_lower)

    def _matches_requested_device(entity: dict) -> bool:
      eid = entity.get("entity_id", "")
      if requested_normalized:
          attrs = entity.get("attributes") or {}
          friendly = str(attrs.get("friendly_name") or "")
          friendly_normalized = _normalize_name(friendly)
          if not friendly_normalized:
              return False
          return (
              friendly_normalized == requested_normalized
              or requested_normalized in friendly_normalized
              or friendly_normalized in requested_normalized
          )
      if cached_device and eid == cached_device:
          return True
      return False

    def _score(entity: dict) -> tuple[int, str]:
      eid = entity.get("entity_id", "")
      attrs = entity.get("attributes") or {}
      friendly = str(attrs.get("friendly_name") or "").lower()
      friendly_normalized = _normalize_name(friendly)
      source = str(attrs.get("source") or "").lower()
      device_class = str(attrs.get("device_class") or "").lower()
      state = str(entity.get("state") or "").lower()

      score = 0
      if requested_lower and requested_lower in friendly:
          score += 100
      if requested_normalized and requested_normalized == friendly_normalized:
          score += 120
      elif requested_normalized and requested_normalized in friendly_normalized:
          score += 80
      elif requested_normalized and friendly_normalized in requested_normalized:
          score += 60
      if "music assistant queue" in source:
          score -= 200
      if any(token in eid for token in ("cast", "android", "chromecast")):
          score += 100
      if any(token in friendly for token in ("cast", "android tv", "google tv", "tv")):
          score += 60
      if device_class in {"tv", "receiver"}:
          score += 30
      if state not in {"unavailable", "unknown"}:
          score += 10
      if cached_device and eid == cached_device:
          score += 50
      return score, eid

    candidates = [e for e in entities if e.get("entity_id", "").startswith("media_player.")]
    if not candidates:
      return None

    if requested_normalized:
      matched_candidates = [entity for entity in candidates if _matches_requested_device(entity)]
      if matched_candidates:
          candidates = matched_candidates
      else:
          return None
    elif cached_device:
      matched_candidates = [entity for entity in candidates if _matches_requested_device(entity)]
      if matched_candidates:
          candidates = matched_candidates
      else:
          return None
    else:
      return None

    ranked = sorted((_score(e) for e in candidates), reverse=True)
    best_score, best_eid = ranked[0]
    return best_eid if best_score > 0 else None




async def troubleshoot_media_failure(query: str, failure: str) -> dict | None:
    prompt = (
      f"{MEDIA_TROUBLESHOOTING_PROMPT}\n"
      f"User request: {query}\n"
      f"Failure: {failure}"
    )
    try:
      data = await execute_inference(
          {"model": await get_assistant_model(), "messages": [{"role": "user", "content": prompt}], "stream": False}
     )
      raw = str(data.get("response", "")).strip()
      start = raw.find("{")
      end = raw.rfind("}")
      if start == -1 or end == -1 or end <= start:
          return None
      json_data = _parse_llm_json_object(raw[start:end + 1])
      if not isinstance(json_data, dict):
          return None
      query_value = str(json_data.get("query") or "").strip()
      media_type = str(json_data.get("media_type") or "").strip().lower()
      if not query_value or media_type not in {"artist", "search", "music"}:
          return None
      return {"query": query_value, "media_type": media_type}
    except Exception as exc:
      log.warning(f"[MediaFallback] Troubleshooting fallback failed: {exc}")
      return None

# --- Helper Functions ---
async def decompose_command_query(query: str) -> list[str]:
    if " and " not in query.lower() and " then " not in query.lower():
      return [query]
    parts = re.split(r'\s+(?:and|then)\s+', query, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]

async def resolve_identity(body: dict) -> Any:
    client = get_http_client()
    async def do_resolve():
        resp = await client.post(
            f"{IDENTITY_SVC}/api/resolve",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=httpx.Timeout(300.0, connect=30.0)
        )
        if resp.status_code != 200:
            err_detail = f"Identity resolution failed: {resp.status_code} {resp.text}"
            log.error(err_detail)
            raise HTTPException(status_code=resp.status_code, detail=err_detail)
        data = resp.json()
        if not isinstance(data, dict):
            log.error(f"Identity resolution returned non-dict: {data}")
            raise HTTPException(status_code=500, detail="Identity resolution format error")
        return data
    try:
        return await retry_http_request(do_resolve, "Identity resolution", max_retries=2, base_delay=0.1)
    except httpx.RequestError as e:
      log.error(f"Identity service unreachable: {e}")
      raise HTTPException(status_code=503, detail="Identity service unreachable")


async def resolve_first_user() -> Any:
    """Resolve the first (ID=1) user in the system."""
    try:
        return await resolve_identity({"user_id": 1})
    except HTTPException:
        return {}


def _auth_body_from_request(request: Request, body: dict | None = None) -> Any:
    merged = dict(body or {})
    user_id = request.query_params.get("user_id")
    if user_id:
        merged["rag_user"] = user_id
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        merged["api_key"] = auth_header.split(" ", 1)[1]
    # Fallback: accept ?token= query param for browser-native requests (HTMLAudioElement)
    # that cannot set request headers (used by local media streaming).
    elif not merged.get("api_key"):
        token_qp = request.query_params.get("token")
        if token_qp:
            merged["api_key"] = token_qp
    return merged


async def _resolve_identity_from_request(request: Request, body: dict | None = None) -> Any:
    return await resolve_identity(_auth_body_from_request(request, body))


async def _proxy_execution_with_identity(
    request: Request,
    endpoint: str,
    payload: dict | None = None,
    *,
    method: str = "POST",
) -> JSONResponse:
    creds_data = await _resolve_identity_from_request(request)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with httpx.AsyncClient(timeout=60.0) as client:
        if method.upper() == "GET":
            params = {"user_id": creds_data.get("user") or ""}
            resp = await client.get(f"{EXECUTION_SVC}{endpoint}", headers=headers, params=params)
        else:
            exec_payload = {"user_context": creds_data, **(payload or {})}
            resp = await client.post(f"{EXECUTION_SVC}{endpoint}", json=exec_payload, headers=headers)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

async def fetch_ha_entities(creds: dict) -> list:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/discovery/entities",
                params={"ha_url": creds.get("ha_url"), "ha_token": creds.get("ha_token")},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            
            if resp.status_code != 200:
                log.warning(f"Failed to fetch entities: {resp.status_code}")
                return []

            try:
                data = resp.json()
            except Exception as e:
                log.error(f"Failed to parse HA entities JSON: {e} | Body: {resp.text[:200]}")
                return []
            
            entities = data.get("entities", []) if isinstance(data, dict) else []
            if entities:
                user_id = creds.get("user") or ""
                # Update IntentEngine cache for fuzzy matching
                engine.update_entity_cache(entities)
                
                # 1. Sync to RAG for discovery (and get orphan list for Redis cleanup)
                async def _sync_to_rag():
                    try:
                        resp = await get_http_client().post(
                            f"{RAG_SVC}/rag/sync/ha",
                            json={"entities": entities, "user_id": user_id},
                            headers={"X-Internal-Secret": INTERNAL_SECRET}
                        )
                        if resp.status_code == 200:
                            result = resp.json()
                            orphaned = result.get("orphaned_entity_ids", [])
                            if orphaned:
                                from services.gateway.ha_state_cache import get_redis
                                r = get_redis()
                                for eid in orphaned:
                                    try:
                                        r.delete(f"ha:state:{eid}")
                                    except Exception:
                                        pass
                                log.info(f"[ha_sync] Cleaned up {len(orphaned)} orphaned Redis cache entries")
                    except Exception as _e:
                        log.debug(f"RAG sync fire-and-forget failed (non-critical): {_e}")
                asyncio.create_task(_sync_to_rag())

                # 2. Auto-assign to user in Identity for RBAC bypass/mapping
                async def auto_assign():
                    try:
                        for e in entities:
                            eid = e.get("entity_id")
                            if not eid:
                                continue
                            await get_http_client().post(
                                f"{IDENTITY_SVC}/api/users/devices",
                                json={"username": user_id, "device_id": eid},
                                headers={"X-Internal-Secret": INTERNAL_SECRET}
                            )
                        log.info(f"Auto-assigned {len(entities)} entities to {user_id}")
                    except Exception as ae:
                        log.error(f"Auto-assign failed: {ae}")
                
                asyncio.create_task(auto_assign())

            return entities
    except Exception as e:
        log.error(f"Entity discovery error: {e}")
        return []

async def fetch_device_history(creds: dict, entity_id: str, days: int = 1) -> list:
    try:
      resp = await get_http_client().get(
          f"{EXECUTION_SVC}/discovery/history",
          params={
            "ha_url": creds.get("ha_url"),
            "ha_token": creds.get("ha_token"),
            "entity_id": entity_id,
            "days": days
          },
          headers={"X-Internal-Secret": INTERNAL_SECRET},
          timeout=5.0
      )
      if resp.status_code != 200:
          return []
      data = resp.json()
      if isinstance(data, list):
          return [d for d in data if isinstance(d, dict)]
      return []
    except Exception as e:
      log.error(f"History retrieval error for {entity_id}: {e}")
      return []

@app.post("/api/discovery/sync")
async def discovery_sync(request: Request):
    """Orchestrates HA entity discovery and RAG sync."""
    body = await request.json()
    creds = await resolve_identity(body)
    entities = await fetch_ha_entities(creds)
    return {"status": "SUCCESS", "entities_count": len(entities)}


@app.get("/api/entities")
async def get_entities(request: Request):
    """Return all Home Assistant entities for searchable dropdowns."""
    try:
        resp = await get_http_client().get(
            f"{EXECUTION_SVC}/discovery/entities",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return {"entities": []}
        data = resp.json()
        entities = data.get("entities", [])
        # Return lightweight format for UI dropdown
        return {
            "entities": [
                {
                    "entity_id": e.get("entity_id", ""),
                    "friendly_name": e.get("attributes", {}).get("friendly_name", ""),
                    "state": e.get("state", ""),
                    "domain": e.get("entity_id", "").split(".")[0] if "." in e.get("entity_id", "") else "",
                }
                for e in entities
            ]
        }
    except Exception:
        return {"entities": []}

async def execute_command(endpoint: str, payload: dict) -> Any:
    try:
      resp = await get_http_client().post(
          f"{EXECUTION_SVC}{endpoint}",
          json=payload,
          headers={"X-Internal-Secret": INTERNAL_SECRET},
          timeout=120.0
      )
      data = resp.json()
      if not isinstance(data, dict):
          return {"status": "FAILURE", "message": str(data)}
      return data
    except Exception as e:
      return {"status": "FAILURE", "message": str(e)}

# --- Middleware & Security ---
@app.middleware("http")
async def secure_logging_middleware(request: Request, call_next):
    """Logs incoming requests while redacting sensitive security headers."""
    # Redact sensitive headers for logging
    safe_headers = dict(request.headers)
    sensitive_keys = ["x-internal-secret", "x-api-key", "authorization", "cookie"]
    for key in sensitive_keys:
        if key in safe_headers:
            safe_headers[key] = "[REDACTED]"
            
    log.info(f"REQUEST: {request.method} {request.url} | Headers: {safe_headers}")
    asyncio.create_task(emit_log("INFO", f"{request.method} {request.url.path}", {"headers": safe_headers}))
    
    response = await call_next(request)
    
    log.info(f"RESPONSE: {request.method} {request.url} | Status: {response.status_code}")
    asyncio.create_task(emit_log("INFO", f"RESPONSE {request.method} {request.url.path} -> {response.status_code}", {}))
    return response

# --- Core Handlers ---
# Removed local extract_user_facts as it is now in history.py

async def decompose_query(query: str) -> list[str]:
    """
    Splits a complex query into simpler sub-queries.
    """
    if len(query.split()) < 6: # Simple queries don't need decomposition
        return [query]
        
    try:
        prompt = f"""Split this complex user request into individual, actionable sub-queries.
Request: "{query}"
Return a simple JSON list of strings.
Example: ["Turn on the office light", "Play some jazz music"]
"""
        data = await execute_inference({"model": await get_assistant_model(), "messages": [{"role": "user", "content": prompt}], "stream": False})
        text = data.get("message", {}).get("content", "").strip()
        if "[" in text and "]" in text:
            import json
            try:
                return json.loads(text[text.find("["):text.rfind("]")+1])
            except (json.JSONDecodeError, ValueError):
                pass
        return [query]
    except Exception as e:
        log.warning(f"Decomposition failed: {e}")
        return [query]

async def perform_shadow_execution(query: str, creds: ResolvedCredentials, history: list, rag_context: str) -> str:
    """
    Shadow Execution: Queries the live application model for a proposal, 
    then returns it to be analyzed by the dev model.
    """
    log.info("[ShadowExecution] Initiating...")
    # 1. Query the 'Live' model for a proposal
    proposal_prompt = (
        "You are the production instance of SharedLLM. Provide a concise, logical proposal "
        "for how to address the following user request within the current architecture.\n\n"
        f"User Request: {query}\n\n"
        f"Capability Context: {rag_context}\n"
    )
    try:
        # Strategy 7: Dynamic VRAM Awareness for Shadow Execution
        assistant = await get_assistant_model()
        settings = await get_all_settings()
        vram_params = await get_vram_safe_params(assistant, settings)
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")

        payload = {
            "model": assistant,
            "messages": [{"role": "user", "content": proposal_prompt}],
            "stream": False,
            "options": {**vram_params, "num_predict": 512}
        }
        log.info(f"[ShadowExecution] Requesting proposal from {assistant} (Timeout: {OLLAMA_TIMEOUT}s)")
        # Wait for available slot if all are busy
        try:
            async with httpx.AsyncClient(timeout=3.0) as slot_client:
                deadline = asyncio.get_running_loop().time() + 120.0
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        ps_resp = await slot_client.get(f"{ollama_url}/api/ps", timeout=3.0)
                        if ps_resp.status_code == 200:
                            slots = ps_resp.json().get("slots", {})
                            if slots.get("available", 0) > 0:
                                break
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                else:
                    log.warning("[ShadowExecution] No slots available, proceeding anyway")
        except Exception:
            log.warning("[ShadowExecution] Could not check slot availability")
        start_t = asyncio.get_event_loop().time()
        resp = await get_http_client().post(f"{ollama_url}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
        elapsed = asyncio.get_event_loop().time() - start_t
        log.info(f"[ShadowExecution] Ollama responded in {elapsed:.1f}s with status {resp.status_code}")
        
        if resp.status_code == 200:
            proposal = resp.json().get("message", {}).get("content", "")
            return f"\n\n### LIVE SYSTEM PROPOSAL (Shadow Execution)\n{proposal}\n\n[Dev Agent: Compare this proposal against the codebase and architectural intent. Identify any deltas and select the optimal path.]"
        else:
            log.warning(f"[ShadowExecution] Non-200 response: {resp.status_code} - {resp.text}")
    except Exception as e:
        log.warning(f"[ShadowExecution] Failed: {type(e).__name__}: {e}")
    return ""

async def AgentLoop(query: str, selected_model: str, full_system: str, short_term: list, rag_user: str, creds: ResolvedCredentials) -> Any:
    """
    Raven Autonomous Loop (Strategy 7 & 8 implementation).
    Supports multi-turn tool execution: call Ollama, execute tool, feed result back, repeat.
    """
    # Initialize the base payload for the loop
    ollama_payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": full_system}
        ] + short_term + [{"role": "user", "content": query}],
        "stream": False # AgentLoop is always non-streaming for the brain
    }

    MAX_TOOL_ITERATIONS = 30
    HEARTBEAT_INTERVAL = 15   # seconds between heartbeat log lines
    HUNG_THRESHOLD = 240      # seconds before a HUNG WARNING is emitted
    agent_messages: list[dict] = list(ollama_payload.get("messages") or [])  # pyright: ignore[reportArgumentType]
    exec_data = None
    ans = ""
    loop_start = asyncio.get_event_loop().time()

    for agent_iter in range(MAX_TOOL_ITERATIONS):
        iter_num = agent_iter + 1
        iter_start = asyncio.get_event_loop().time()
        
        # MISSION PRESSURE: Stop mapping, start patching
        if iter_num > 5:
            pressure_msg = "\n\n[MISSION PRESSURE: You have performed multiple mapping turns. STOP READING. IMMEDIATELY apply the WorkspaceFilePatchRequest for get_collection_docs (line 2821). This is your FINAL directive.]"
            full_system += pressure_msg
            agent_messages[0]["content"] = full_system
            log.warning(f"[AgentLoop] MISSION PRESSURE INJECTED into Iteration {iter_num}")

        log.info(f"[AgentLoop] Iteration {iter_num}/{MAX_TOOL_ITERATIONS} | "
                 f"total elapsed {iter_start - loop_start:.0f}s")
        log.info(f"[AgentLoop] System prompt preview: {full_system[:300]}...")

        # ── Heartbeat task ──────────────────────────────────────────────────
        heartbeat_stop = asyncio.Event()

        async def _heartbeat(iter_n: int, t0: float) -> None:
            elapsed = 0.0
            while not heartbeat_stop.is_set():
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if heartbeat_stop.is_set():
                    break
                elapsed = asyncio.get_event_loop().time() - t0
                if elapsed > HUNG_THRESHOLD:
                    log.warning(
                        f"[AgentLoop] ⚠ HUNG WARNING — iter {iter_n} has been waiting "
                        f"{elapsed:.0f}s for Ollama response (threshold={HUNG_THRESHOLD}s)"
                    )
                else:
                    log.info(
                        f"[AgentLoop] ♥ heartbeat — iter {iter_n} | "
                        f"waiting for Ollama {elapsed:.0f}s | stage=inference"
                    )

        hb_task = asyncio.create_task(_heartbeat(agent_iter + 1, iter_start))

        try:
            # Strategy 7 & 8: Dynamic VRAM Awareness & Singleton Queue
            vram_params = await get_vram_safe_params(selected_model, {})
            ollama_payload["options"] = vram_params  # pyright: ignore[reportArgumentType]

            # ISOLATED CONTEXT: Only send the mission, protocol, and the LAST tool result to prevent history drift
            ollama_payload["messages"] = [
                {"role": "system", "content": full_system},
                {"role": "user", "content": f"MISSION LOCK: {query}"}
            ]
            if exec_data:
                ollama_payload["messages"].append({
                    "role": "user", 
                    "content": f"LAST TOOL RESULT (Execution Status: SUCCESS):\n{json.dumps(exec_data) if isinstance(exec_data, dict) else str(exec_data)}"
                })
            ollama_payload["messages"].append({"role": "user", "content": "Execute the next step immediately using a JSON tool call block."})
            
            async with INFERENCE_LOCK:
                log.info(f"[Strategy 8] Inference Lock ACQUIRED for {selected_model} (Iter {agent_iter + 1})")
                resp = await call_ollama(ollama_payload, use_chat=True)
                log.info(f"[Strategy 8] Inference Lock RELEASED for {selected_model}")
                
            heartbeat_stop.set()
            await hb_task
            if not resp:
                return JSONResponse({"status": "ERROR", "message": "Brain offline."}, status_code=502)
            ans = resp.get("message", {}).get("content", "Error.")
            ollama_ms = (asyncio.get_event_loop().time() - iter_start) * 1000
            log.info(f"[AgentLoop] Ollama responded in {ollama_ms:.0f}ms — iter {agent_iter + 1}")
        except (httpx.TimeoutException, httpx.ConnectError):
            heartbeat_stop.set()
            await hb_task
            ans = "Jarvis is currently operating in low-latency mode due to a downstream service timeout. I am available for core operations, but complex reasoning may be delayed."
            log.warning(f"[AgentLoop] Ollama timeout/connect error on iter {agent_iter + 1}")
            return JSONResponse({"status": "SUCCESS", "message": ans, "degraded": True})

        # 8. Tool Execution — Intercept JSON blocks for execution
        resp_content = ans
        log.info(f"[AgentLoop] Response length: {len(resp_content)}")
        log.info(f"[AgentLoop] Raw response: {resp_content}")
        
        # 4. Extract Tool Call
        tool_data = extract_action_json(resp_content)
        
        tag = "```json" if "```json" in ans else "```"
        start = ans.find(tag)
        if start != -1:
            start += len(tag)
            end = ans.find("```", start)
            if end > start:
                try:
                    tool_data = json.loads(ans[start:end].strip())
                except Exception:
                    pass

        # Strategy 2: First { to last }
        if tool_data is None:
            first_brace = ans.find("{")
            last_brace = ans.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                try:
                    tool_data = json.loads(ans[first_brace:last_brace+1])
                except Exception:
                    pass

        # Strategy 3: First [ to last ] (for array-wrapped)
        if tool_data is None:
            first_bracket = ans.find("[")
            last_bracket = ans.rfind("]")
            if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
                try:
                    tool_data = json.loads(ans[first_bracket:last_bracket+1])
                except Exception:
                    pass

        # Strategy 4: Fallback for raw markdown code blocks (Diffs or Full Code)
        if tool_data is None and "```" in ans:
            try:
                parts = ans.split("```")
                if len(parts) >= 3:
                    block_content = parts[1]
                    first_nl = block_content.find('\n')
                    if first_nl != -1:
                        code_text = block_content[first_nl+1:]
                        # Simple detection for unified diff
                        if "--- " in code_text and "+++ " in code_text and "@@ " in code_text:
                            # It's a diff
                            path = "auto"
                            chunks = []
                            current_old = []
                            current_new = []
                            in_hunk = False
                            for line in code_text.splitlines():
                                if line.startswith("+++ b/") or line.startswith("+++ "):
                                    path = line.split("+++ ")[-1].replace("b/", "", 1).strip()
                                elif line.startswith("@@"):
                                    if in_hunk and (current_old or current_new):
                                        chunks.append({
                                            "old_text": "\n".join(current_old) + "\n" if current_old else "",
                                            "new_text": "\n".join(current_new) + "\n" if current_new else ""
                                        })
                                        current_old, current_new = [], []
                                    in_hunk = True
                                elif in_hunk:
                                    if line.startswith("-"):
                                        current_old.append(line[1:])
                                    elif line.startswith("+"):
                                        current_new.append(line[1:])
                                    elif line.startswith(" "):
                                        current_old.append(line[1:])
                                        current_new.append(line[1:])
                            if in_hunk and (current_old or current_new):
                                chunks.append({
                                    "old_text": "\n".join(current_old) + "\n" if current_old else "",
                                    "new_text": "\n".join(current_new) + "\n" if current_new else ""
                                })
                            
                            if chunks:
                                tool_data = {
                                    "action": "WorkspaceFilePatchRequest",
                                    "payload": {"path": path, "chunks": chunks}
                                }
                                log.info(f"[AgentLoop] Parsed raw markdown diff into WorkspaceFilePatchRequest for {path}")
                        # Fallback for ANY code block if no strategy matched
                        elif len(code_text.strip()) > 10:
                            # If it's a full block, use Write (which supports 'content')
                            action = "WorkspaceFileWriteRequest"
                            tool_data = {
                                "action": action,
                                "payload": {"path": path if path != "auto" else "auto", "content": code_text}
                            }
                            log.info(f"[AgentLoop] Parsed raw markdown block into {action}")
            except Exception as e:
                log.error(f"[AgentLoop] Error parsing raw markdown block: {e}")

        # Strategy 5: Fallback for XML-like tags (e.g. <commit>, <tool>, <action>)
        if tool_data is None and "<" in ans and ">" in ans:
            try:
                start = ans.find("<")
                end = ans.find(">", start)
                if start != -1 and end != -1:
                    tag_content = ans[start+1:end]
                    if "action=" in tag_content or "path=" in tag_content:
                        attrs = {}
                        import re
                        for match in re.finditer(r'(\w+)=["\']([^"\']+)["\']', tag_content):
                            attrs[match.group(1)] = match.group(2)
                        
                        action_val = attrs.get("action") or attrs.get("type")
                        path_val = attrs.get("path")
                        
                        if action_val:
                            action_map_shorthand = {"read": "WorkspaceFileReadRequest", "patch": "WorkspaceFilePatchRequest"}
                            tool_data = {
                                "action": action_map_shorthand.get(action_val, action_val),
                                "payload": {"path": path_val}
                            }
                            log.info(f"[AgentLoop] Parsed pseudo-tag <{tag_content}> into {tool_data['action']}")
            except Exception as e:
                log.error(f"[AgentLoop] Error parsing pseudo-tag: {e}")
        
        # Strategy 6: Payload Normalization
        if tool_data and isinstance(tool_data, dict):
            # Handle array format (e.g. OpenAI or custom tool array)
            for array_key in ("tools", "tool_calls", "actions"):
                if array_key in tool_data and isinstance(tool_data[array_key], list) and len(tool_data[array_key]) > 0:  # pyright: ignore[reportArgumentType]
                    log.info(f"[AgentLoop] Normalizing tool schema: extracting first item from '{array_key}'")
                    tool_data = tool_data[array_key][0]  # pyright: ignore[reportArgumentType,reportOptionalSubscript]
                    break

            for nest_key in ("arguments", "payload", "args", "json", "tool_call"):
                if tool_data and isinstance(tool_data, dict) and nest_key in tool_data and isinstance(tool_data[nest_key], dict):  # pyright: ignore[reportAttributeAccessIssue]
                    log.info(f"[AgentLoop] Normalizing tool schema: hoisting '{nest_key}' to top level")
                    nested_vals = tool_data.pop(nest_key)
                    # Preserve top-level action/type if they exist, but inner payload fields take precedence
                    # for parameters like 'action' in GitOperationRequest.
                    for k, v in nested_vals.items():  # pyright: ignore[reportAttributeAccessIssue]
                        if k in ("action", "type") and k in tool_data:  # pyright: ignore[reportArgumentType]
                            # Move the outer discriminator to 'tool_name' to avoid clobbering
                            tool_data["tool_name"] = tool_data.get(k)  # pyright: ignore[reportArgumentType]
                        tool_data[k] = v  # pyright: ignore[reportArgumentType]
            
            mapping = {"offset": "offset_lines", "limit": "limit_lines"}
            if isinstance(tool_data, dict):
                for old_key, new_key in mapping.items():
                    if old_key in tool_data and new_key not in tool_data:
                        log.info(f"[AgentLoop] Normalizing parameter: '{old_key}' -> '{new_key}'")
                        tool_data[new_key] = tool_data.pop(old_key)  # pyright: ignore[reportArgumentType]
                
                if "properties" in tool_data and "type" in tool_data:
                    # Detect schema hallucinations
                    log.warning(f"[AgentLoop] Detected schema hallucination — triggering protocol correction")
                    tool_data = None
            
            # SCHEMA_WHITELIST: Strictly enforce allowed workspace tools
            ALLOWED_TOOLS = [
                "workspacefilereadrequest", "workspacefilepatchrequest", 
                "workspacefilewriterequest", "workspacesearchrequest", 
                "workspacelintrequest", "workspacefiledeleterequest",
                "workspacebootstraprequest", "workspaceshellrequest",
                "gitoperationrequest", "dockerlogsrequest", "dockercomposerequest",
                "storageindexrequest", "storagefilereadrequest", "storagefilewriterequest",
                "ripgrep", "read_file", "patch_file", "grep", "search", "shell", "git", "logs", "compose", "index"
            ]
            # Tool Discriminator: Detect which tool is being called. 
            # Prioritize 'tool_name' if it was set during hoisting to avoid clobbering by payload parameters.
            action_key = None
            if isinstance(tool_data, dict):
                action_key = tool_data.get("tool_name") or tool_data.get("type") or tool_data.get("action") or tool_data.get("tool_choice") or tool_data.get("tool")
            dispatch_action = None
            
            if action_key:
                # Normalization: lower, strip underscores/hyphens, handle 'request' suffix
                norm_action = str(action_key).lower().replace("_", "").replace("-", "").strip()
                if not norm_action.endswith("request") and (norm_action + "request") in ALLOWED_TOOLS:
                    norm_action = norm_action + "request"
                
                if norm_action in ALLOWED_TOOLS:
                    dispatch_action = norm_action

            if not dispatch_action:
                log.warning(f"[AgentLoop] Hallucinated tool detected: {action_key} — triggering protocol correction")
                tool_data = None
            else:
                # We have a valid dispatch action. Use it but don't clobber the payload's 'action' if it belongs to the tool schema.
                action = dispatch_action
        
        if not tool_data:
            log.warning(f"[AgentLoop] No valid JSON tool call found in iteration {agent_iter + 1}. Conversational output detected.")
            # If we've already performed at least one action, a conversational response is acceptable as a final status.
            if agent_iter > 0:
                log.info(f"[AgentLoop] Mission likely accomplished. Terminating loop.")
                break
                
            if agent_iter < MAX_TOOL_ITERATIONS - 1:
                log.info(f"[AgentLoop] Re-prompting for autonomous tool execution...")
                agent_messages.append({"role": "assistant", "content": str(ans) if ans else ""})
                QWEN_GROUNDING_INSTRUCTION = """
# MISSION LOCK: Raven Autonomous Repair Protocol
1. **FOCUS**: YOU ARE RAVEN. Your ONLY mission is to resolve the BUG or TASK.
2. **ZERO CONVERSATION**: You MUST NOT ask questions or provide status updates.
3. **TOOL MANDATE**: Every response MUST contain a valid JSON tool call. Output ONLY JSON.
4. **NO DISTRACTIONS**: Do not acknowledge instructions. Just execute.
"""
                agent_messages.append({
                    "role": "user", 
                    "content": f"{QWEN_GROUNDING_INSTRUCTION}\n\nCRITICAL PROTOCOL VIOLATION: You provided a conversational response without a tool call. You are FORBIDDEN from asking questions or seeking user approval. You MUST execute the next step of your plan immediately using a JSON tool call block. Continue the mission now."
                })
                continue
            else:
                log.info(f"[AgentLoop] Max iterations reached — final response.")
                break

        if isinstance(tool_data, dict):
            log.info(f"[AgentLoop] Dispatching action: {json.dumps({k: v for k, v in tool_data.items() if k != 'user_context'}, indent=2)}")
        else:
            log.warning(f"[AgentLoop] tool_data is not dict: {type(tool_data)}")
            break

        try:
            if not isinstance(tool_data, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
                log.warning(f"[AgentLoop] tool_data is not a dict, skipping action dispatch")
                break
            action = (
                dispatch_action or
                str(tool_data.get("tool_name") or tool_data.get("action") or tool_data.get("tool") or tool_data.get("name") or tool_data.get("type") or tool_data.get("tool_choice") or "")
            )
            # If hoisting occurred, tool_data itself contains the payload fields.
            # Otherwise, we use the 'payload' sub-dictionary if it exists.
            orig_payload = tool_data.get("payload")
            if orig_payload and isinstance(orig_payload, dict):
                payload = orig_payload
            else:
                # Use everything in tool_data as the payload, excluding internal discriminators
                payload = {k: v for k, v in tool_data.items() if k not in ("payload", "tool_name", "tool_choice")}
            
            if isinstance(tool_data, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
                for k, v in tool_data.items():
                    if k not in ("action", "payload", "tool", "name", "type", "tool_name") and k not in payload:
                        if "path" in k.lower():
                            payload["path"] = v
                        else:
                            payload[k] = v

            if not action:
                if "path" in payload:
                    if (payload.get("is_patch") or "chunks" in payload):
                        action = "WorkspaceFilePatchRequest"
                    elif payload.get("content") is not None:
                        action = "WorkspaceFileWriteRequest"
                    else:
                        action = "WorkspaceFileReadRequest"

            action_map_aliases = {
                "read_file": "WorkspaceFileReadRequest",
                "write_file": "WorkspaceFileWriteRequest",
                "patch_file": "WorkspaceFilePatchRequest",
                "lint_file": "WorkspaceLintRequest",
                "lint": "WorkspaceLintRequest",
                "ripgrep": "WorkspaceSearchRequest",
                "grep": "WorkspaceSearchRequest",
                "search": "WorkspaceSearchRequest",
                "fd": "WorkspaceSearchRequest",
                "shell": "WorkspaceShellRequest",
                "terminal": "WorkspaceShellRequest",
                "command": "WorkspaceShellRequest",
                "exec": "WorkspaceShellRequest",
                "run": "WorkspaceShellRequest"
            }
            if action in action_map_aliases:
                action = action_map_aliases[action]

            action_map = {
                "lightcontrolrequest": (EXECUTION_SVC, "/execute/light"),
                "mediaplayrequest": (EXECUTION_SVC, "/execute/media/play"),
                "mediatransportrequest": (EXECUTION_SVC, "/execute/media/transport"),
                "tvcastrequest": (EXECUTION_SVC, "/execute/tv_cast"),
                "climaterequest": (EXECUTION_SVC, "/execute/climate"),
                "securityrequest": (EXECUTION_SVC, "/execute/security"),
                "announcementrequest": (EXECUTION_SVC, "/execute/announce"),
                "haservicerequest": (EXECUTION_SVC, "/execute/ha_service"),
                "calendarrequest": (EXECUTION_SVC, "/execute/calendar"),
                "noterequest": (EXECUTION_SVC, "/execute/note"),
                "timerrequest": (EXECUTION_SVC, "/execute/timer"),
                "talkrequest": (EXECUTION_SVC, "/execute/talk"),
                "websearchrequest": (EXECUTION_SVC, "/execute/web_search"),
                "webreadrequest": (EXECUTION_SVC, "/execute/web_read"),
                "dockerlogsrequest": (EXECUTION_SVC, "/execute/docker_logs"),
                "gitoperationrequest": (EXECUTION_SVC, "/execute/git"),
                "deploymentrequest": (EXECUTION_SVC, "/execute/deploy"),
                "capabilityindexrequest": (EXECUTION_SVC, "/execute/index_capabilities"),
                "volumeinventoryrequest": (EXECUTION_SVC, "/execute/volumes"),
                "workspacefilereadrequest": (EXECUTION_SVC, "/execute/workspace_file_read"),
                "workspacefilewriterequest": (EXECUTION_SVC, "/execute/workspace_file_write"),
                "workspacefilepatchrequest": (EXECUTION_SVC, "/execute/workspace_file_patch"),
                "workspacelintrequest": (EXECUTION_SVC, "/execute/workspace_lint"),
                "workspacesearchrequest": (EXECUTION_SVC, "/execute/workspace_search"),
                "workspaceshellrequest": (EXECUTION_SVC, "/execute/workspace_shell"),
                "storagefilereadrequest": (EXECUTION_SVC, "/execute/storage_file_read"),
                "storagefilewriterequest": (EXECUTION_SVC, "/execute/storage_file_write"),
                "workspacebootstraprequest": (WORKSPACE_RUNTIME_SVC, "/workspaces/bootstrap"),
                "systemlearningrequest": (EXECUTION_SVC, "/execute/learning"),
                "discoverysyncrequest": (EXECUTION_SVC, "/execute/discovery_sync"),
                "storageindexrequest": (STORAGE_SVC, "/index/full"),
            }

            lookup_action = action.lower().strip() if action else ""

            if lookup_action in action_map:
                svc_base, endpoint = action_map[lookup_action]
                payload["user_context"] = {
                    "user": creds.user,
                    "is_admin": creds.is_admin,
                    "ha_url": creds.ha_url,
                    "ha_token": creds.ha_token
                }

                if action.lower() == "storageindexrequest":
                    # Specialized payload for storage indexing
                    payload = {
                        "provider": {
                            "kind": "nextcloud",
                            "settings": {
                                "url": creds.nextcloud_url,
                                "username": creds.nextcloud_user,
                                "password": creds.nextcloud_pass
                            }
                        },
                        "path": payload.get("path", "/"),
                        "recursive": True
                    }

                log.info(f"[AgentLoop] Triggering {action} via {svc_base}{endpoint} (iter {agent_iter + 1})")
                exec_resp = await get_http_client().post(
                    f"{svc_base}{endpoint}",
                    json=payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=120.0
                )

                exec_msg = ""
                if exec_resp.status_code == 200:
                    exec_data = exec_resp.json()
                    exec_msg = exec_data.get("message", "Action completed.")
                    detail = exec_data.get("detail")
                    if detail:
                        if isinstance(detail, dict):
                            if "content" in detail:
                                detail_txt = str(detail["content"])
                            elif "logs" in detail:
                                detail_txt = str(detail["logs"])
                            else:
                                detail_txt = str(detail)
                        else:
                            detail_txt = str(detail)
                        exec_msg += f"\n\n[DETAIL]\n{detail_txt[:10000]}"
                else:
                    try:
                        err_detail = exec_resp.json().get("detail", exec_resp.text)
                    except:
                        err_detail = exec_resp.text
                    exec_msg = f"Failed: {err_detail}"

                log.info(f"[AgentLoop] Tool result (iter {agent_iter + 1}): {exec_msg[:200]}...")
                agent_messages.append({"role": "assistant", "content": str(ans) if ans else ""})
                agent_messages.append({
                    "role": "user",
                    "content": f"[TOOL RESULT for {action}]: {exec_msg[:8000]}\n\nContinue with your plan."
                })
                continue
            else:
                log.warning(f"[AgentLoop] Unknown action '{action}' — breaking loop")
                break

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.error(f"[AgentLoop] Tool execution failed: {e}\n{tb}")
            agent_messages.append({"role": "assistant", "content": str(ans) if ans else ""})
            agent_messages.append({
                "role": "user",
                "content": f"[FATAL ERROR IN TOOL EXECUTION]: {e}\n\nTraceback:\n{tb}\n\nPlease analyze this failure, adjust your approach or parameters, and continue the mission."
            })
            continue

    return _make_ollama_response(ans, selected_model, "autonomous_mission")


@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def chat_handler(request: Request, background_tasks=None):
    log.info("Chat handler entered")
    client = get_http_client()
    # 1. Resolve Identity
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        body["api_key"] = auth_header.split(" ")[1] # Identity expects 'api_key' for resolution
    elif "api_key" in body:
        # body["api_key"] is already set
        pass
    
    is_openai = "/v1/chat/completions" in str(request.url)
    should_stream = body.get("stream", False)
    explicit_model = str(body.get("model") or "").strip()
    show_thinking = body.get("show_thinking", False)

    try:
        selected_model = (explicit_model if explicit_model and explicit_model != "auto" else None) or await get_assistant_model()
        log.info(f"[ChatHandler] Model selection: explicit_model='{explicit_model}' selected_model='{selected_model}'")
    except RuntimeError as e:
        log.error(f"[ChatHandler] Model configuration error: {e}")
        err_msg = str(e) + " Please configure models in the UI settings."
        if is_openai:
            return _make_openai_response(err_msg, "unknown", "model_config_error")
        return _make_ollama_response(err_msg, "unknown", "model_config_error")

    # 2. Extract Query
    query = body.get("query")
    if not query and "messages" in body and isinstance(body["messages"], list) and len(body["messages"]) > 0:
        last_msg = body["messages"][-1]
        if isinstance(last_msg, dict):
            query = last_msg.get("content")
        else:
            query = str(last_msg)
    
    if not query:
        return JSONResponse({"status": "ERROR", "message": "No query provided."}, status_code=400)

    # Dynamic Model Routing: Use specialized coder model for engineering tasks
    if not explicit_model or explicit_model == "auto":
        coding_keywords = ["code", "script", "python", "bug", "fix", "repair", "raven", "audit", "develop", "refactor"]
        if any(k in (query or "").lower() for k in coding_keywords):
            try:
                override_model = await get_coding_model()
                log.info(f"[ChatHandler] Coding task detected, overriding model from '{selected_model}' to '{override_model}'")
                selected_model = override_model
            except RuntimeError as e:
                log.error(f"[ChatHandler] Coding model configuration error: {e}")
                err_msg = str(e) + " Please configure models in the UI settings."
                if is_openai:
                    return JSONResponse(_make_openai_error(err_msg, "unknown"), status_code=503)
                return JSONResponse(_make_ollama_error(err_msg, "unknown"), status_code=503)

    try:
        creds_data = await resolve_identity(body)
        creds = ResolvedCredentials(**creds_data)
        user_id = creds.user
    except HTTPException as he:
        if he.status_code == 401:
            msg = "Authentication failed. Please log in or provide a valid API key."
            if is_openai:
                return _make_openai_response(msg, selected_model, "unauthorized")
            return _make_ollama_response(msg, selected_model, "unauthorized")
        raise he
    except Exception as e:
        log.error(f"Identity resolution crash: {e}")
        msg = "The Identity service is currently unavailable. Please try again later."
        if is_openai:
            return _make_openai_response(msg, selected_model, "degraded")
        return _make_ollama_response(msg, selected_model, "degraded")
    log.info(f"Chat request from {user_id} query='{query}'")

    if is_time_or_date_query(query):
        ans = await build_time_or_date_response(query)
        await update_history(user_id, "user", query)
        await update_history(user_id, "assistant", ans)
        if is_openai:
            return _make_openai_response(ans, selected_model, "datetime")
        return _make_ollama_response(ans, selected_model, "datetime")

    if wants_workspace_readme_generation(query):
        return await generate_workspace_readme_via_coding_model(
            body=body,
            user_id=user_id,
            refined_query=query,
            selected_model=selected_model,
            should_stream=should_stream,
            is_openai=is_openai,
        )

    if wants_direct_code_orchestration(query):
        return await orchestrate_code_change(
            body=body,
            user_id=user_id,
            refined_query=query,
            selected_model=selected_model,
            should_stream=should_stream,
            is_openai=is_openai,
        )

    # 3. Semantic Routing (Fast Path Detection)
    intent, confidence = engine.classify(query)
    log.info(f"[FastPath] classify result: intent='{intent}' confidence={confidence:.3f} is_active={engine.is_active} threshold={engine.FAST_PATH_CONFIDENCE}")
    
    # Resolve dynamic threshold from Identity
    threshold_str = await fetch_global_setting("fast_path_threshold", str(_DEFAULT_FAST_PATH_THRESHOLD))
    try:
        engine.FAST_PATH_CONFIDENCE = float(threshold_str)
    except (ValueError, TypeError):
        engine.FAST_PATH_CONFIDENCE = _DEFAULT_FAST_PATH_THRESHOLD

    is_fast_path = engine.is_fast_path(intent, confidence)
    log.info(f"[FastPath] is_fast_path={is_fast_path} for intent='{intent}'")
    resolved_entity = None
    cached_device_unavailable = False
    
    if is_fast_path:
        media_entities = None
        cached_device_info = None
        cached_device_id = None
        if intent in ["play_media", "pause_media", "media_transport", "turn_on", "turn_off"]:
            media_entities = await fetch_ha_entities(creds.model_dump())
            cached_device_info = get_last_used_device(user_id)
            if cached_device_info:
                cached_device_id = cached_device_info.get("entity_id")
                entity_states = {e.get("entity_id"): e.get("state") for e in media_entities or []}
                cached_state = entity_states.get(cached_device_id, "unknown")
                if cached_state in {"unavailable", "unknown", "off"}:
                    log.info(f"[FastPath] Cached device {cached_device_id} is {cached_state}, bypassing to LLM")
                    cached_device_unavailable = True
                    cached_device_id = None
                else:
                    log.info(f"[FastPath] Using cached device: {cached_device_id} (state={cached_state})")

        # Attempt entity extraction/resolution for control intents
        if intent == "play_media":
            media_type = "video" if is_likely_video_request(query) else None
            resolved_entity = resolve_media_target(query, media_entities or [], media_type, cached_device_id)
        elif intent in ["turn_on", "turn_off"]:
            resolved_entity = resolve_media_target(query, media_entities or [], media_type="power", cached_device=cached_device_id)
        elif intent in ["pause_media", "media_transport"]:
            resolved_entity = engine.extract_entity(query, intent) or resolve_media_target(query, media_entities or [], cached_device=cached_device_id)
        else:
            resolved_entity = engine.extract_entity(query, intent)
        
        # If cached device was unavailable, bypass to LLM to ask user
        if cached_device_unavailable and not resolved_entity:
            log.info(f"[FastPath] BYPASSED for {intent}: Cached device unavailable, LLM should ask for target")
            is_fast_path = False
        # If the intent requires an entity (light, media, transport) but we couldn't resolve one,
        # fallback to the slow-path (LLM) to avoid executing without a target.
        elif intent in ["turn_on", "turn_off", "play_media", "pause_media", "media_transport"] and not resolved_entity:
            log.info(f"[FastPath] BYPASSED for {intent}: Could not resolve entity from '{query}'")
            is_fast_path = False
        else:
            log.info(f"[FastPath] MATCHED: intent='{intent}' confidence={confidence} entity='{resolved_entity}'")


    if is_fast_path:
        # Execute immediate tool for simple intents
        endpoint_map = {
            "turn_on": "/execute/light",
            "turn_off": "/execute/light",
            "play_media": "/execute/media/play",
            "pause_media": "/execute/media/transport",
            "media_transport": "/execute/media/transport",
            "index_storage": "/index/full",
            "sync_ha": "/health",
            "ha_status": "/health",
        }
        endpoint = endpoint_map.get(intent)
        if endpoint:
            exec_payload = {
                "user_context": creds.model_dump(),
                "action": "turn_on" if intent == "turn_on" else ("turn_off" if intent == "turn_off" else "play"),
                "entity_id": resolved_entity
            }

            # Add specialized payload for storage/ha
            if intent == "index_storage":
                exec_payload = {
                    "provider": {"kind": "nextcloud", "settings": {"url": creds.nextcloud_url, "username": creds.nextcloud_user, "password": creds.nextcloud_pass}},
                    "path": "/", "recursive": True
                }
                svc_base = STORAGE_SVC
            elif intent == "play_media":
                media_query, _ = extract_media_request(query)
                media_type = "video" if is_likely_video_request(query) else None
                exec_payload = {
                    "user_context": creds.model_dump(),
                    "entity_id": resolved_entity,
                    "query": media_query or query,
                    "media_content_type": "artist",
                    "media_type": media_type,
                }
                svc_base = EXECUTION_SVC
            elif intent in ["pause_media", "media_transport"]:
                exec_payload = {
                    "user_context": creds.model_dump(),
                    "entity_id": resolved_entity,
                    "command": "pause",
                }
                svc_base = EXECUTION_SVC
            else:
                svc_base = EXECUTION_SVC

            fast_timeout = 120.0 if intent == "play_media" else 30.0
            async with httpx.AsyncClient(timeout=fast_timeout) as client:
                exec_resp = await client.post(f"{svc_base}{endpoint}", json=exec_payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
                ans = exec_resp.json().get("message", "Action completed.")
            
            if resolved_entity and intent in ["play_media", "pause_media", "media_transport", "turn_on", "turn_off"]:
                entity_map = {e.get("entity_id"): e for e in media_entities or []}
                entity = entity_map.get(resolved_entity, {})
                attrs = entity.get("attributes", {})
                set_last_used_device(
                    user_id,
                    resolved_entity,
                    friendly_name=attrs.get("friendly_name", ""),
                    state=entity.get("state", ""),
                )
            
            await update_history(user_id, "user", query)
            await update_history(user_id, "assistant", ans)
            if is_openai:
                return _make_openai_response(ans, selected_model, intent)
            return _make_ollama_response(ans, selected_model, intent)

    # 5. Retrieve Tiered Memory
    short_term = await get_history(user_id)
    long_term = await get_long_term_memory(user_id, query)
    
    # 6. Context Injection (RAG)
    rag_context = ""
    # MISSION LOCK: Disable RAG to prevent architectural hallucinations
    if "MISSION LOCK" in query:
        log.info("[RAG] MISSION LOCK detected — bypassing all collections to ensure focus.")
    else:
        try:
            collections = ["ha_entities", "nextcloud_files", "system_capabilities", "system_learnings"]
            for coll in collections:
                client = get_http_client()
                resp = await client.post(
                    f"{RAG_SVC}/rag/search",
                    json={"collection_name": coll, "query": query, "user_id": user_id, "k": 15},
                    headers={"X-Internal-Secret": INTERNAL_SECRET, "Authorization": f"Bearer {INTERNAL_SECRET}"},
                    timeout=10.0
                )
                resp.raise_for_status()
                res = resp.json()
                hits = res.get("results", [])
                if hits:
                    rag_context += f"\n[{coll.upper()}]\n" + "\n".join([h["content"] for h in hits])
                    log.info(f"[RAG] Collection '{coll}' returned {len(hits)} hits.")
                else:
                    log.info(f"[RAG] Collection '{coll}' returned NO hits.")
        except Exception as e:
            log.error(f"RAG Retrieval error: {e}")

    # 7. Slow Path Execution (LLM Pipeline)
    shadow_context = ""
    complex_signals = ["complex", "bug", "refactor", "design", "how to", "fix", "error", "traceback", "implement", "logic"]
    if any(k in query.lower() for k in complex_signals):
        shadow_context = await perform_shadow_execution(query, creds, short_term, rag_context)

    system_instruction = select_system_instruction_for_query(query, selected_model)
    
    # Detection of autonomous agent engagement
    is_autonomous = False
    # Hardened intent logic: also trigger on 'Raven' or explicit 'perform' keywords
    autonomy_signals = ["raven", "perform", "audit", "index", "reindex", "scan", "repair", "fix", "check", "synchronize", "sync"]
    if any(k in query.lower() for k in autonomy_signals) or ":" in query[:15]:
        log.info("[ShadowExecution] AUTONOMOUS MISSION DETECTED via keyword/protocol signal")
        is_autonomous = True
        try:
            from .prompts import RAVEN_AUTONOMOUS_PROTOCOL
        except (ImportError, ValueError):
            try:
                from prompts import RAVEN_AUTONOMOUS_PROTOCOL
            except ImportError:
                from gateway.prompts import RAVEN_AUTONOMOUS_PROTOCOL
        system_instruction = RAVEN_AUTONOMOUS_PROTOCOL

    admin_tag = " (ADMIN)" if creds.is_admin else ""
    user_info = f"Current User: {user_id}{admin_tag}"
    
    protocols = await fetch_autonomous_protocols()
    full_system = f"{system_instruction}\n\n{protocols}\n\n{user_info}\n\n{long_term}\n\n### Capability Context\n{rag_context}{shadow_context}"
    
    final_query = query
    if any(k in query.lower() for k in ["scan", "index", "reindex", "storage", "/notes", "list", "find"]):
        full_system += (
            "\n\n[SYSTEM OVERRIDE: CRITICAL DIRECTIVE: You have full permission to access the storage system. "
            "You ARE fully capable of executing this storage action. "
            "DO NOT apologize or say you lack access. DO NOT provide a tutorial. "
            "You MUST immediately execute the appropriate tool: `StorageListRequest` to find resources, "
            "or `StorageIndexRequest` to index them. Output the correct JSON block now.]"
        )
    
    if any(k in query.lower() for k in ["log", "logs", "docker", "output", "error"]):
        final_query += (
            "\n\n[SYSTEM OVERRIDE: You ARE authorized and REQUIRED to print diagnostic log snippets to the user. "
            "If you fetch logs via `DockerLogsRequest`, you MUST parse the output and display relevant snippets "
            "in a markdown code block. Never claim you cannot show logs.]"
        )

    # 8. Final Message Construction & Shadow Dispatch
    if is_autonomous:
        # Use coding model for autonomous tasks
        coding_model = await get_coding_model()
        selected_model = coding_model
        log.info("[ShadowExecution] Routing to autonomous AgentLoop...")
        return await AgentLoop(final_query, selected_model, full_system, short_term, body.get("rag_user") or "", creds)

    settings = await get_all_settings()
    _vram_params = await get_vram_safe_params(selected_model, settings)

    # Build job_payload for the FIFO queue
    default_sys = select_system_instruction_for_query(query, selected_model)
    job_payload = {
        "model": selected_model,
        "query": final_query,
        "system": body.get("system") or default_sys,
        "creds": creds.model_dump(),
        "client": body.get("client"),
        "source": body.get("source"),
        "device_id": body.get("device_id"),
        "rag_user": body.get("rag_user"),
        "show_thinking": show_thinking,
        "is_openai": is_openai,
    }
    
    job_id = await job_queue.enqueue_job(user_id, job_payload)
    
    # 5. Modality-Specific Response Logic (Phase 2 Integration)
    # Standard clients (OpenAI/Ollama/OpenWebUI) require synchronous or standard streaming.
    # We bridge the Async Queue to their expectation here.
    
    is_standard_client = is_openai or body.get("standard_client", False)
    # If using /api/chat (Ollama format) and not explicitly asking for async, assume standard
    if "/api/chat" in str(request.url) and not body.get("async_job", False) and body.get("client") != "voice":
        is_standard_client = True

    if is_standard_client:
        if should_stream:
            async def standard_stream_gen():
                last_pos = -1
                last_keepalive = asyncio.get_event_loop().time()
                while True:
                    job = await job_queue.get_job_status(job_id)
                    if not job:
                        break
                    
                    # Pop and yield chunks
                    chunks = await job_queue.get_chunks(job_id)
                    for chunk in chunks:
                        if is_openai:
                            yield f"data: {json.dumps(_make_openai_chunk(chunk, selected_model))}\n\n"
                        else:
                            yield json.dumps(_make_ollama_chunk(chunk, selected_model)) + "\n"
                    
                    if job["status"] == JobStatus.COMPLETED:
                        if is_openai:
                            yield f"data: {json.dumps(_make_openai_chunk('', selected_model, 'stop'))}\n\n"
                            yield "data: [DONE]\n\n"
                        else:
                            yield json.dumps(_make_ollama_chunk("", selected_model, True)) + "\n"
                        break
                    
                    if job["status"] == JobStatus.FAILED:
                        err = job.get("error", "Unknown error")
                        if is_openai:
                            yield f"data: {json.dumps(_make_openai_chunk(f'[ERROR]: {err}', selected_model, 'stop'))}\n\n"
                        else:
                            yield json.dumps(_make_ollama_chunk(f"[ERROR]: {err}", selected_model, True)) + "\n"
                        break

                    if is_openai:
                        now = asyncio.get_event_loop().time()
                        if now - last_keepalive >= 5.0:
                            # SSE comment heartbeat keeps OpenAI-compatible clients like Open WebUI
                            # from treating slower tool calls as dead connections.
                            yield ": keepalive\n\n"
                            last_keepalive = now
                    
                    # Optional: Yield queue position if it changes
                    pos = await job_queue.get_queue_position(job_id)
                    if pos != last_pos and pos > 0:
                        # We send this as a subtle prefix or hidden content if possible, 
                        # but for standard clients, it's safer to just wait.
                        last_pos = pos
                        
                    await asyncio.sleep(0.1)
            
            return StreamingResponse(
                standard_stream_gen(), 
                media_type="text/event-stream" if is_openai else "application/x-ndjson"
            )
        else:
            # Blocking path
            while True:
                job = await job_queue.get_job_status(job_id)
                if not job:
                    break
                if job["status"] == JobStatus.COMPLETED:
                    ans = job["result"]
                    if is_openai:
                        return _make_openai_response(ans, selected_model)
                    return _make_ollama_response(ans, selected_model)
                if job["status"] == JobStatus.FAILED:
                    err_msg = job.get("error", "Job failed")
                    if is_openai:
                        return JSONResponse(_make_openai_error(err_msg, selected_model), status_code=500)
                    return JSONResponse(_make_ollama_error(err_msg, selected_model), status_code=500)
                await asyncio.sleep(0.2)

    # JARVIS-SPECIFIC CLIENTS (202 Accepted + SSE Polling)
    # This keeps the UI responsive even during long inference.
    return JSONResponse(
        status_code=202,
        content={
            "status": "QUEUED",
            "job_id": job_id,
            "message": "Inference task queued. Raven is currently processing or waiting for compute resources.",
            "position": await job_queue.get_queue_position(job_id)
        }
    )

@app.get("/api/chat/stream/{job_id}")
async def stream_chat_job(job_id: str):
    """
    SSE endpoint for real-time job completion updates.
    The UI subscribes to this to receive the final Markdown once Raven finishes.
    """
    async def event_generator():
        last_status = None
        while True:
            job = await job_queue.get_job_status(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
            
            status = job["status"]
            if status != last_status:
                yield f"data: {json.dumps({'status': status.upper(), 'job_id': job_id, 'position': await job_queue.get_queue_position(job_id)})}\n\n"
                last_status = status
            
            if status == JobStatus.COMPLETED:
                result = job["result"]
                yield f"data: {json.dumps({'status': 'COMPLETED', 'result': result})}\n\n"
                break
            
            if status == JobStatus.FAILED:
                yield f"data: {json.dumps({'status': 'FAILED', 'error': job.get('error')})}\n\n"
                break
                
            await asyncio.sleep(1.0) # Poll Redis every second for status changes

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/chat/job/{job_id}")
async def get_chat_job_status(job_id: str):
    """Checks the status of an inference job."""
    job = await job_queue.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] == JobStatus.COMPLETED:
        result = job["result"]
        if job["payload"].get("is_openai"):
            return _make_openai_response(result, job["payload"]["model"], "completed")
        return _make_ollama_response(result, job["payload"]["model"], "completed")

    if job["status"] == JobStatus.FAILED:
        err_msg = job.get("error", "Job failed")
        if job["payload"].get("is_openai"):
            return JSONResponse(_make_openai_error(err_msg, job["payload"].get("model", "unknown")), status_code=500)
        return JSONResponse(_make_ollama_error(err_msg, job["payload"].get("model", "unknown")), status_code=500)

    return {
        "status": job["status"].upper(),
        "job_id": job_id,
        "position": await job_queue.get_queue_position(job_id),
        "message": "Raven is still thinking..." if job["status"] == JobStatus.PROCESSING else "Queued in FIFO buffer."
    }
    
@app.post("/api/auth/login")
async def proxy_login(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{IDENTITY_SVC}/api/auth/login", json=body)
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/api/auth/change-password")
async def proxy_change_password(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/auth/change-password", 
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/users/{username}/password")
async def proxy_admin_set_password(username: str, request: Request):
    body = await request.json()
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{username}/password",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/auth/import/nextcloud")
async def proxy_import_nextcloud_users(request: Request):
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/auth/import/nextcloud",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/auth/test-connection")
async def proxy_test_connection(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/auth/test-connection", 
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/api/auth/discover")
async def proxy_discover(request: Request):
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/auth/discover",
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.patch("/api/users/me")
async def proxy_update_me(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/users/me",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.patch("/api/users/{username}")
async def proxy_update_user(username: str, request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/users/{username}",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/api/settings")
async def proxy_get_settings(request: Request):
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/settings",
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.patch("/api/settings/{key}")
async def proxy_update_setting(key: str, request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/settings/{key}",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/api/settings")
async def proxy_update_settings_bulk(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/settings",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/api/users")
async def proxy_users(request: Request):
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users",
            headers={"Authorization": auth_header} if auth_header else {}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.delete("/api/devices/{device_id:path}")
async def proxy_delete_device(device_id: str, request: Request):
    auth_header = request.headers.get("Authorization")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(
            f"{IDENTITY_SVC}/api/devices/{device_id}",
            headers={"Authorization": auth_header} if auth_header else {}
        )
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/communication/timers")
async def proxy_list_timers(request: Request):
    return await _proxy_execution_with_identity(request, "/execute/timers", method="GET")


@app.post("/api/communication/timers")
async def proxy_add_timer(request: Request):
    body = await request.json()
    payload = {
        "action": "add",
        "type": body.get("type", "timer"),
        "title": body.get("title"),
        "duration_str": body.get("duration_str"),
        "time_str": body.get("time_str"),
        "recurrence": body.get("recurrence"),
        "target_device": body.get("target_device"),
    }
    return await _proxy_execution_with_identity(request, "/execute/timer", payload)


@app.delete("/api/communication/timers")
async def proxy_delete_timer(request: Request):
    body = await request.json()
    payload = {
        "action": "delete",
        "type": body.get("type", "timer"),
        "title": body.get("title"),
        "query": body.get("query"),
    }
    return await _proxy_execution_with_identity(request, "/execute/timer", payload)


@app.get("/api/communication/calendar/calendars")
async def proxy_list_calendars(request: Request):
    payload = {"action": "list"}
    return await _proxy_execution_with_identity(request, "/execute/calendar", payload)


@app.get("/api/communication/calendar/events")
async def proxy_read_calendar(request: Request, calendar_name: str | None = None):
    payload = {"action": "read"}
    if calendar_name:
        payload["calendar_name"] = calendar_name
    return await _proxy_execution_with_identity(request, "/execute/calendar", payload)


@app.post("/api/communication/calendar/events")
async def proxy_add_calendar_event(request: Request):
    body = await request.json()
    payload = {
        "action": "add",
        "summary": body.get("summary"),
        "start_time": body.get("start_time"),
        "calendar_name": body.get("calendar_name"),
    }
    return await _proxy_execution_with_identity(request, "/execute/calendar", payload)


@app.post("/api/communication/notes/create")
async def proxy_create_note(request: Request):
    body = await request.json()
    payload = {
        "action": "create",
        "title": body.get("title"),
        "content": body.get("content"),
        "category": body.get("category", "General"),
        "storage": body.get("storage", "nextcloud"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/read")
async def proxy_read_note(request: Request):
    body = await request.json()
    payload = {
        "action": "read",
        "title": body.get("title"),
        "path": body.get("path"),
        "storage": body.get("storage", "nextcloud"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/append")
async def proxy_append_note(request: Request):
    body = await request.json()
    payload = {
        "action": "append",
        "title": body.get("title"),
        "content": body.get("content"),
        "path": body.get("path"),
        "storage": body.get("storage", "nextcloud"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/delete")
async def proxy_delete_note(request: Request):
    body = await request.json()
    payload = {
        "action": "delete",
        "title": body.get("title"),
        "path": body.get("path"),
        "storage": body.get("storage", "nextcloud"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/list")
async def proxy_list_notes(request: Request):
    body = await request.json() if request.method == "POST" else {}
    payload = {
        "action": "list",
        "storage": body.get("storage", "nextcloud"),
        "directories": body.get("directories"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/sync_rag")
async def proxy_sync_notes_rag(request: Request):
    body = await request.json()
    payload = {
        "action": "sync_rag",
        "storage": body.get("storage", "nextcloud"),
        "directories": body.get("directories"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.get("/api/integrations/skylight/chores")
async def proxy_get_skylight_chores(request: Request, user: Optional[str] = None, date: Optional[str] = None):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})
    
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", ""), "date": date or ""}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{EXECUTION_SVC}/api/integrations/skylight/chores", headers=headers, params=params)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/integrations/skylight/chores/{chore_id}/complete")
async def proxy_complete_skylight_chore(chore_id: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})
    
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{EXECUTION_SVC}/api/integrations/skylight/chores/{chore_id}/complete", headers=headers, params=params)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/integrations/skylight/chores/{chore_id}/uncomplete")
async def proxy_uncomplete_skylight_chore(chore_id: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})
    
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{EXECUTION_SVC}/api/integrations/skylight/chores/{chore_id}/uncomplete", headers=headers, params=params)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/integrations/skylight/rewards")
async def proxy_get_skylight_rewards(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})
    
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{EXECUTION_SVC}/api/integrations/skylight/rewards", headers=headers, params=params)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/integrations/skylight/rewards/{reward_id}/redeem")
async def proxy_redeem_skylight_reward(reward_id: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})
    
    body = await request.json() if await request.body() else {}
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{EXECUTION_SVC}/api/integrations/skylight/rewards/{reward_id}/redeem", json=body, headers=headers, params=params)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/communication/announcements")
async def proxy_send_announcement(request: Request):
    body = await request.json()
    payload = {
        "entity_id": body.get("entity_id"),
        "message": body.get("message"),
        "volume": body.get("volume", 0.6),
        "tts_engine": body.get("tts_engine", "kokoro"),
        "storybook": body.get("storybook", False),
        "save_path": body.get("save_path")
    }

    return await _proxy_execution_with_identity(request, "/execute/announce", payload)


@app.get("/api/communication/talk/conversations")
async def proxy_list_talk_conversations(request: Request):
    payload = {"action": "list"}
    return await _proxy_execution_with_identity(request, "/execute/talk", payload)


@app.post("/api/communication/talk/conversations/open")
async def proxy_open_talk_conversation(request: Request):
    body = await request.json()
    payload = {
        "action": "open",
        "token": body.get("token"),
        "target_user": body.get("target_user"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload)


@app.get("/api/communication/talk/messages")
async def proxy_get_talk_messages(request: Request):
    payload = {
        "action": "messages",
        "token": request.query_params.get("token"),
        "limit": int(request.query_params.get("limit", "50")),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload)


@app.post("/api/communication/talk/messages")
async def proxy_send_talk_message(request: Request):
    body = await request.json()
    payload = {
        "action": "send",
        "token": body.get("token"),
        "message": body.get("message"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload)


@app.post("/api/communication/talk/voice")
async def proxy_send_talk_voice(request: Request):
    body = await request.json()
    payload = {
        "action": "send_voice",
        "token": body.get("token"),
        "audio_base64": body.get("audio_base64"),
        "mime_type": body.get("mime_type"),
        "file_name": body.get("file_name"),
        "caption": body.get("caption"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload)

@app.post("/api/generate")
async def proxy_generate(request: Request):
    try:
        body = await request.json()
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        async with httpx.AsyncClient(timeout=None) as client:
            req = client.build_request("POST", f"{ollama_url}/api/generate", json=body)
            resp = await client.send(req, stream=True)
            if resp.status_code != 200:
                await resp.aread()
                return JSONResponse({"status": "ERROR", "message": resp.text}, status_code=resp.status_code)
            
            async def generate():
                async for chunk in resp.aiter_raw():
                    yield chunk
            
            from fastapi.responses import StreamingResponse
            return StreamingResponse(generate(), media_type="application/x-ndjson")
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)

@app.get("/api/tags")
async def proxy_tags():
    try:
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            return JSONResponse({"models": []}, status_code=503)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code != 200:
                return JSONResponse({"models": []}, status_code=200)
            data = resp.json()
            if not isinstance(data, dict):
                return {"models": []}
            return data
    except Exception:
        return JSONResponse({"models": []}, status_code=200)

@app.get("/api/version")
async def proxy_version():
    return {"version": "0.1.32"}


@app.post("/api/show")
async def proxy_show(request: Request):
    try:
        body = await request.json()
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            return JSONResponse({"error": "Ollama not configured"}, status_code=503)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{ollama_url}/api/show", json=body)
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


@app.post("/api/embeddings")
async def proxy_embeddings(request: Request):
    try:
        body = await request.json()
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            return JSONResponse({"error": "Ollama not configured"}, status_code=503)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{ollama_url}/api/embeddings", json=body)
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)


@app.post("/api/embed")
async def proxy_embed(request: Request):
    try:
        body = await request.json()
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            return JSONResponse({"error": "Ollama not configured"}, status_code=503)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{ollama_url}/api/embed", json=body)
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)
@app.get("/api/search")
async def global_search(q: str, request: Request):
    """Global semantic search proxying to RAG service."""
    # Resolve user for multi-tenancy
    auth_header = request.headers.get("Authorization")
    user_id = "admin"
    if auth_header and auth_header.startswith("Bearer "):
        try:
            # Simple identity check
            resp = await get_http_client().post(
                f"{IDENTITY_SVC}/api/resolve",
                headers={"Authorization": auth_header, "X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                user_id = resp.json().get("user", "admin")
        except Exception:
            pass

    try:
        resp = await get_http_client().post(
            f"{RAG_SVC}/rag/search",
            json={"query": q, "user_id": user_id, "collection_name": "nextcloud_files", "k": 5},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=10.0
        )
        if resp.status_code != 200:
            return JSONResponse({"status": "ERROR", "message": "Search failed"}, status_code=502)
        
        data = resp.json()
        # Transform for UI
        results = data.get("results", [])
        return {
            "answer": results[0]["content"] if results else "No specific context found.",
            "files": [{"name": os.path.basename(r["metadata"].get("path", "unknown")), "path": r["metadata"].get("path", "unknown")} for r in results]
        }
    except Exception as e:
        log.error(f"Search proxy failed: {e}")
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)

@app.get("/api/workspaces")
async def get_workspaces_proxy(request: Request):
    """Proxy to workspace runtime."""
    creds = await _resolve_identity_from_request(request)
    params = {}
    if creds:
        if creds.get("user"):
            params["rag_user"] = creds["user"]
        if creds.get("voice_id"):
            params["voice_id"] = creds["voice_id"]
        if creds.get("device_id"):
            params["device_id"] = creds["device_id"]
        
    try:
        async with borrow_http_client() as client:
            resp = await client.get(
                f"{WORKSPACE_RUNTIME_SVC}/workspaces",
                params=params,
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as e:
        log.error(f"Workspaces proxy failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})

async def _proxy_workspace_runtime_json(method: str, path: str, request = None):
    body = await request.json() if request is not None else None
    resp = await get_http_client().request(
        method,
        f"{WORKSPACE_RUNTIME_SVC}{path}",
        json=body,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/api/workspaces")
async def create_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/workspaces", request)

@app.post("/api/workspaces/bootstrap")
async def bootstrap_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/workspaces/bootstrap", request)

@app.post("/api/workspaces/resolve")
async def resolve_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/workspace/resolve", request)

@app.patch("/api/workspaces/{workspace_id}")
async def update_workspace_proxy(workspace_id: str, request: Request):
    return await _proxy_workspace_runtime_json("PATCH", f"/workspaces/{workspace_id}", request)

@app.delete("/api/workspaces/{workspace_id}")
async def delete_workspace_proxy(workspace_id: str):
    return await _proxy_workspace_runtime_json("DELETE", f"/workspaces/{workspace_id}")

@app.post("/api/workspaces/files/read")
async def read_workspace_file_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/files/read", request)

@app.post("/api/workspaces/files/list")
async def list_workspace_files_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/files/list", request)

@app.post("/api/workspaces/files/write")
async def write_workspace_file_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/files/write", request)

@app.post("/api/workspaces/git/status")
async def git_status_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/status", request)

@app.post("/api/workspaces/git/pull")
async def git_pull_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/pull", request)

@app.post("/api/workspaces/git/revert")
async def git_revert_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/revert", request)

@app.post("/api/workspaces/tests/pytest")
async def pytest_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/tests/pytest", request)

@app.post("/api/workspaces/workflow/write-sync-commit")
async def write_sync_commit_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/workflow/write-sync-commit", request)

@app.post("/api/storage/list")
async def list_storage_files(request: Request, body: StorageListRequest):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("nextcloud_url") or not creds.get("nextcloud_user") or not creds.get("nextcloud_pass"):
        raise HTTPException(status_code=400, detail="NextCloud credentials not configured for this user.")
    
    payload = {
        "provider": {
            "kind": "nextcloud",
            "settings": {
                "url": creds["nextcloud_url"],
                "username": creds["nextcloud_user"],
                "password": creds["nextcloud_pass"]
            }
        },
        "path": body.path,
        "recursive": body.recursive
    }
    
    resp = await get_http_client().post(
        f"{STORAGE_SVC}/providers/list",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/api/storage/index")
async def trigger_storage_indexing(request: Request, body: StorageIndexRequest):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("nextcloud_url") or not creds.get("nextcloud_user") or not creds.get("nextcloud_pass"):
        raise HTTPException(status_code=400, detail="NextCloud credentials not configured for this user.")
    
    payload = {
        "provider": {
            "kind": "nextcloud",
            "settings": {
                "url": creds["nextcloud_url"],
                "username": creds["nextcloud_user"],
                "password": creds["nextcloud_pass"]
            }
        },
        "path": body.path,
        "recursive": body.recursive
    }
    
    resp = await get_http_client().post(
        f"{STORAGE_SVC}/index/full",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/api/storage/stats")
async def get_storage_stats(request: Request):
    # Resolve user and nextcloud IDs from request
    try:
        creds_data = await _resolve_identity_from_request(request)
        jarvis_user = creds_data.get("user") or ""
        nc_user = creds_data.get("nextcloud_user")
    except Exception:
        first_user = await resolve_first_user()
        jarvis_user = first_user.get("user") or ""
        nc_user = None

    # Helper to merge stats from multiple users
    def merge_stats(base, extra):
        if not extra or extra.get("status") != "SUCCESS":
            return base
        if base.get("status") != "SUCCESS":
            return extra
        
        base["total_chunks"] = base.get("total_chunks", 0) + extra.get("total_chunks", 0)
        base["total_documents"] = base.get("total_documents", 0) + extra.get("total_documents", 0)
        
        breakdown = base.get("breakdown", {})
        for k, v in extra.get("breakdown", {}).items():
            if k in breakdown:
                # Merge individual collection counts
                breakdown[k]["chunks"] = breakdown[k].get("chunks", 0) + v.get("chunks", 0)
                breakdown[k]["documents"] = breakdown[k].get("documents", 0) + v.get("documents", 0)
            else:
                breakdown[k] = v
        base["breakdown"] = breakdown
        return base

    # 1. Query for Jarvis User (typically HA entities and system learnings)
    resp1 = await get_http_client().get(
        f"{RAG_SVC}/rag/stats?user_id={jarvis_user}",
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    try:
        content = resp1.json()
    except Exception as e:
        log.error(f"Failed to parse Jarvis RAG stats: {e}")
        content = {"status": "ERROR", "message": "Failed to fetch Jarvis stats", "breakdown": {}}

    # 2. Query for Nextcloud User if different (typically file chunks)
    if nc_user and nc_user != jarvis_user:
        try:
            resp2 = await get_http_client().get(
                f"{RAG_SVC}/rag/stats?user_id={nc_user}",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            content = merge_stats(content, resp2.json())
        except Exception as e:
            log.warning(f"Failed to fetch or merge Nextcloud RAG stats: {e}")
        
    return JSONResponse(status_code=200, content=content)
    
@app.get("/api/storage/collection/{collection_name}")
async def get_collection_docs(collection_name: str, request: Request, limit: int = 100):
    try:
        creds_data = await _resolve_identity_from_request(request)
        user_id = request.query_params.get("user_id") or creds_data.get("nextcloud_user") or creds_data.get("user") or ""
    except Exception:
        first_user = await resolve_first_user()
        user_id = first_user.get("user") or ""

    resp = await get_http_client().get(
        f"{RAG_SVC}/rag/collection/{collection_name}?user_id={user_id}&limit={limit}",
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    
    try:
        content = resp.json()
    except Exception as e:
        log.error(f"Failed to parse collection docs JSON: {e} | Body: {resp.text[:200]}")
        content = {"status": "ERROR", "message": "Upstream RAG service returned non-JSON response", "detail": str(e)}
        
    return JSONResponse(status_code=resp.status_code, content=content)


@app.post("/api/storage/purge/{collection_name}")
async def purge_storage_collection(collection_name: str, request: Request):
    try:
        creds_data = await _resolve_identity_from_request(request)
        user_id = creds_data.get("user") or ""
    except Exception:
        first_user = await resolve_first_user()
        user_id = first_user.get("user") or ""

    body = await request.json()
    
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{RAG_SVC}/rag/purge/{collection_name}?user_id={user_id}",
            json=body.get("filter", {}),
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/admin/tests/smoke")
async def proxy_smoke_test(request: Request):
    client = get_http_client()
    resp = await client.post(
        f"{WORKSPACE_RUNTIME_SVC}/api/admin/tests/smoke",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=65.0
    )
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/admin/tests/unit")
async def proxy_unit_tests(request: Request):
    client = get_http_client()
    resp = await client.post(
        f"{WORKSPACE_RUNTIME_SVC}/api/admin/tests/unit",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=130.0
    )
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/admin/volumes")
async def proxy_admin_volumes(request: Request):
    creds_data = await _resolve_identity_from_request(request)
    if not creds_data.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with borrow_http_client() as client:
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/volumes",
            json={"user_context": creds_data},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=120.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
# ---- Autonomous Ops (Raven) Endpoints ----
@app.get("/api/admin/raven/config")
async def get_raven_config(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    
    settings = await get_llm_settings()
    return {
        "raven_suspended": settings.get("raven_suspended", "false").lower() == "true",
        "raven_scan_interval": int(settings.get("raven_scan_interval", "300")),
        "raven_error_threshold": int(settings.get("raven_error_threshold", "5")),
        "active_coding_model": settings.get("coding_model") or settings.get("ollama_coding_model"),
        "system_default_tts_voice": settings.get("system_default_tts_voice", "af_heart"),
        "system_default_tts_engine": settings.get("system_default_tts_engine", "kokoro")
    }

@app.patch("/api/admin/raven/config")
async def update_raven_config(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    body = await request.json()
    
    async with borrow_http_client() as client:
        for k, v in body.items():
            if k in ["raven_suspended", "raven_scan_interval", "raven_error_threshold", "system_default_tts_voice", "system_default_tts_engine"]:
                await client.patch(
                    f"{IDENTITY_SVC}/api/settings/{k}",
                    json={"value": str(v).lower() if isinstance(v, bool) else str(v)},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
    return {"status": "SUCCESS"}
@app.get("/api/admin/raven/tts/voices")
async def get_raven_tts_voices(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/tts/voices",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/admin/raven/queue")
async def get_raven_queue(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/raven/missions",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/api/admin/services/{service_name}/restart")
async def restart_service(service_name: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{CONTROL_PLANE_URL}/api/restart/{service_name}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Service {service_name} not found")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/api/admin/services")
async def list_services(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{CONTROL_PLANE_URL}/api/containers",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/api/admin/services/{service_name}/logs")
async def get_service_logs(service_name: str, request: Request, tail: int = 100):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    
    async with borrow_http_client() as client:
        # Proxy to control_plane which handles the Docker socket
        resp = await client.get(
            f"{CONTROL_PLANE_URL}/api/containers/{service_name}/logs?tail={tail}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code != 200:
            return JSONResponse(status_code=resp.status_code, content={"detail": resp.text})
        return JSONResponse(status_code=200, content=resp.json())

@app.get("/api/models")
async def list_models(request: Request):
    """List available models from the active provider."""
    settings = await get_llm_settings()
    provider = await get_provider(settings)
    
    # If Ollama, we can hit its /api/tags endpoint
    if isinstance(provider, OllamaProvider):
        async with borrow_http_client() as client:
            resp = await client.get(f"{provider.base_url}/api/tags")
            if resp.status_code == 200:
                tags = resp.json().get("models", [])
                return {"status": "SUCCESS", "models": [m["name"] for m in tags]}
    
    # For OpenRouter or others, we might return the config models
    return {
        "status": "SUCCESS", 
        "models": [settings.get("assistant_model"), settings.get("coding_model"), settings.get("librarian_model")],
        "note": "Active config models returned for this provider."
    }






@app.get("/v1/models")
async def list_openai_models(request: Request):
    """OpenAI-compatible endpoint to list models."""
    settings = await get_llm_settings()
    provider = await get_provider(settings)
    
    model_names = []
    if isinstance(provider, OllamaProvider):
        try:
            async with borrow_http_client() as client:
                resp = await client.get(f"{provider.base_url}/api/tags")
                if resp.status_code == 200:
                    tags = resp.json().get("models", [])
                    model_names = [m["name"] for m in tags]
        except Exception as e:
            log.error(f"Error querying Ollama models for OpenAI list: {e}")
            
    if not model_names:
        # Fallback to configured models
        for model_key in ["assistant_model", "coding_model", "librarian_model"]:
            model_name = settings.get(model_key)
            if model_name and model_name not in model_names:
                model_names.append(model_name)
                
    # Map to OpenAI list format
    openai_models = []
    for m in model_names:
        openai_models.append({
            "id": m,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "system"
        })
        
    return JSONResponse(content={
        "object": "list",
        "data": openai_models
    }, status_code=200)


@app.post("/v1/embeddings")
async def openai_embeddings(request: Request):
    """OpenAI-compatible embeddings endpoint."""
    try:
        body = await request.json()
        model = body.get("model", "default")
        input_data = body.get("input", "")
        
        # input_data can be a string or a list of strings
        inputs = []
        if isinstance(input_data, str):
            inputs = [input_data]
        elif isinstance(input_data, list):
            inputs = input_data
            
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            return JSONResponse({"error": "Ollama not configured"}, status_code=503)
            
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{ollama_url}/api/embed", 
                json={"model": model, "input": inputs}
            )
            if resp.status_code != 200:
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
                
            data = resp.json()
            embeddings_list = data.get("embeddings", [])
            
            # Map to OpenAI list format
            openai_data = []
            for idx, emb in enumerate(embeddings_list):
                openai_data.append({
                    "object": "embedding",
                    "index": idx,
                    "embedding": emb
                })
                
            return JSONResponse(content={
                "object": "list",
                "data": openai_data,
                "model": model,
                "usage": {
                    "prompt_tokens": 0,
                    "total_tokens": 0
                }
            }, status_code=200)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)

@app.post("/api/admin/raven/queue/{id}/execute")
async def execute_raven_mission(id: int, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    
    async with borrow_http_client() as client:
        # Get mission
        resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions", headers={"X-Internal-Secret": INTERNAL_SECRET})
        missions = resp.json()
        target = next((m for m in missions if m["id"] == id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Mission not found")
            
        system_prompt = ""
        if target["mission_type"] == "admin_fix":
            protocols = await fetch_autonomous_protocols()
            system_prompt = f"{protocols}\n\n[ADMIN ROZ ACTIVE]\nYou are the Raven Sentinel operating in the Restricted Operating Zone. Your mission is to fix backend/frontend components. You have elevated access. Execute the following mission:\n{target['proposed_mission']}"
        else:
            system_prompt = f"You are Raven, an autonomous agent executing a user-assigned background mission. Execute the following task to the best of your ability:\n{target['proposed_mission']}"
        
        # Push job
        await job_queue.enqueue_job("raven_admin", {
            "query": target["proposed_mission"],
            "model": target["coding_model"],
            "system": system_prompt,
            "stream": False,
            "creds": creds,
            "_mission_id": target["id"]
        })
        
        # Update status
        await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{id}",
            json={"status": "queued"},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
    return {"status": "SUCCESS", "message": "Mission dispatched."}

# ---- User Missions Endpoints ----

class UserMissionRequest(BaseModel):
    query: str
    slug: Optional[str] = None
    priority: int = 1
    coding_model: Optional[str] = None

@app.post("/api/raven/missions")
async def create_user_mission(body: UserMissionRequest, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    settings = await get_llm_settings()
    coding_model = settings.get("coding_model") or settings.get("ollama_coding_model")
    if not coding_model:
        raise HTTPException(status_code=400, detail="No coding model configured. Mission cannot be dispatched.")
        
    mission_payload = {
        "slug": body.slug,
        "mission_type": "user_task",
        "priority": body.priority,
        "proposed_mission": body.query,
        "coding_model": (body.coding_model if body.coding_model and body.coding_model != "auto" else None) or coding_model,
        "user_id": creds.get("user_id")
    }
    
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/raven/missions",
            json=mission_payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        
        mission_data = resp.json()
        
        # Enqueue the job for execution
        target_model = (body.coding_model if body.coding_model and body.coding_model != "auto" else None) or coding_model
        system_prompt = "You are Raven, an autonomous agent executing a user-assigned background mission.\n\n"
        system_prompt += SINGLE_TURN_TOOL_GUIDE
        system_prompt += f"\n\nExecute the following task to the best of your ability:\n{mission_data['proposed_mission']}"
        
        await job_queue.enqueue_job(creds.get("user_id") or "raven_user", {
            "query": mission_data["proposed_mission"],
            "model": target_model,
            "system": system_prompt,
            "stream": False,
            "creds": creds,
            "_mission_id": mission_data["id"]
        })
        
        # Update status to queued
        await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{mission_data['id']}",
            json={"status": "queued"},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        mission_data["status"] = "queued"
        
        return {"status": "SUCCESS", "mission": mission_data}

@app.get("/api/raven/missions/{id_or_slug}")
async def get_mission_details(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Mission not found")
        return resp.json()

@app.post("/api/raven/missions/{id_or_slug}/kill")
async def kill_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        # Resolve to real ID
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status_code != 200:
            raise HTTPException(status_code=m_resp.status_code, detail="Mission not found")
        mission_data = m_resp.json()
        real_id = mission_data["id"]

        # 1. Update status in database
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{real_id}",
            json={
                "status": "failed", 
                "result": "Aborted by user"
            },
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Failed to update mission status")
        
        # 2. Publish kill signal to Redis
        from services.gateway.history import REDIS_URL
        
        import redis.asyncio as redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        await r.set(f"raven:mission:kill:{real_id}", "KILL", ex=3600)
        await r.publish(f"raven:mission:kill:{real_id}", "KILL")
        await r.close()
        
        return {"status": "SUCCESS", "message": f"Mission {real_id} kill signal sent."}

@app.post("/api/raven/missions/{id_or_slug}/pause")
async def pause_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status_code != 200:
            raise HTTPException(status_code=m_resp.status_code, detail="Mission not found")
        mission_data = m_resp.json()
        real_id = mission_data["id"]

        from services.gateway.history import REDIS_URL
        import redis.asyncio as redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        await r.set(f"raven:mission:pause:{real_id}", "PAUSED", ex=3600)
        await r.close()
        
        return {"status": "SUCCESS", "message": f"Mission {real_id} paused. LLM access will be deferred until resumed."}

@app.post("/api/raven/missions/{id_or_slug}/resume")
async def resume_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status_code != 200:
            raise HTTPException(status_code=m_resp.status_code, detail="Mission not found")
        mission_data = m_resp.json()
        real_id = mission_data["id"]

        from services.gateway.history import REDIS_URL
        import redis.asyncio as redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        await r.delete(f"raven:mission:pause:{real_id}")
        await r.close()
        
        return {"status": "SUCCESS", "message": f"Mission {real_id} resumed. LLM access restored."}

@app.delete("/api/raven/missions/{id_or_slug}")
async def delete_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        resp = await client.delete(
            f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return {"status": "SUCCESS", "message": f"Mission {id_or_slug} deleted."}

@app.get("/api/raven/missions")
async def get_user_missions(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Ideally filter by user_id if we want isolation, for now just proxy it all
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/raven/missions",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        missions = [m for m in resp.json() if m["mission_type"] != "admin_fix" or creds.get("is_admin")]
        return JSONResponse(status_code=resp.status_code, content=missions)

@app.patch("/api/raven/missions/{id_or_slug}")
async def update_mission_status(id_or_slug: str, body: Dict[str, Any], request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    async with borrow_http_client() as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()

# --- Docker Control API ---
@app.get("/api/docker/containers")
async def proxy_list_containers(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds or not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{CONTROL_PLANE_URL}/api/containers",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/api/docker/exec/{service_name}")
async def proxy_docker_exec(service_name: str, body: Dict[str, Any], request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds or not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{CONTROL_PLANE_URL}/api/containers/{service_name}/exec",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/api/raven/missions/{id_or_slug}/logs")
async def get_mission_logs(id_or_slug: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Resolve to real ID
    async with borrow_http_client() as client:
        resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Mission not found")
        mission_data = resp.json()
        real_id = mission_data["id"]
    
    from services.gateway.history import REDIS_URL
    import redis.asyncio as redis
    r = redis.from_url(REDIS_URL, decode_responses=True)
    history_key = f"raven:mission:history:{real_id}"
    existing_logs = await r.lrange(history_key, 0, -1)  # type: ignore[misc]
    await r.close()
    
    if not existing_logs and mission_data.get("output_log"):
        try:
            import json
            parsed = json.loads(mission_data["output_log"])
            if isinstance(parsed, list):
                existing_logs = [
                    json.dumps(item) if isinstance(item, dict) else str(item)
                    for item in parsed
                ]
        except Exception as e:
            log.warning(f"Failed to parse database output_log for mission {real_id}: {e}")
    
    return JSONResponse(status_code=200, content={"logs": existing_logs})

@app.websocket("/api/raven/missions/{id_or_slug}/stream")
async def raven_mission_stream(websocket: WebSocket, id_or_slug: str, token: str = ""):
    # Validate auth token
    if token:
        try:
            async with borrow_http_client() as client:
                auth_resp = await client.get(
                    f"{IDENTITY_SVC}/api/users/me",
                    headers={"Authorization": f"Bearer {token}"}
                )
                if auth_resp.status_code != 200:
                    log.warning(f"[WebSocket] Token validation failed for mission {id_or_slug}: {auth_resp.status_code}")
                    await websocket.close(code=1008, reason="Invalid token")
                    return
        except Exception as e:
            log.warning(f"[WebSocket] Token validation error: {e}")
            await websocket.close(code=1011, reason="Auth service unavailable")
            return
    
    try:
        await websocket.accept()
    except Exception as e:
        log.error(f"[WebSocket] Failed to accept connection: {e}")
        return
        
    # Resolve to real ID
    try:
        async with borrow_http_client() as client:
            resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
            if resp.status_code != 200:
                await websocket.send_text(json.dumps({"type": "system", "data": f"Mission {id_or_slug} not found"}))
                await websocket.close()
                return
            mission_data = resp.json()
            real_id = mission_data["id"]

        from services.gateway.history import REDIS_URL
        import redis.asyncio as redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        
        # 1. Send all existing historical messages first
        history_key = f"raven:mission:history:{real_id}"
        existing_logs = await r.lrange(history_key, 0, -1)  # type: ignore[misc]
        for msg in existing_logs:
            try:
                await websocket.send_text(msg)
            except Exception:
                pass
                
        # 2. Subscribe to new messages
        pubsub = r.pubsub()
        channel = f"raven:mission:stream:{real_id}"
        await pubsub.subscribe(channel)
        
        async def reader():
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        await websocket.send_text(message["data"])
            except Exception as e:
                log.warning(f"[WebSocket] Pubsub reader error: {e}")

        async def keep_alive():
            """Send periodic pings to keep the WebSocket connection alive."""
            try:
                while True:
                    await asyncio.sleep(25)
                    await websocket.ping("keepalive")
            except Exception:
                pass

        reader_task = asyncio.create_task(reader())
        keep_alive_task = asyncio.create_task(keep_alive())
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30)
                except asyncio.TimeoutError:
                    pass  # Connection is still alive, handled by ping loop
                except WebSocketDisconnect:
                    break
        except Exception as e:
            log.warning(f"[WebSocket] Client disconnect: {e}")
        finally:
            reader_task.cancel()
            keep_alive_task.cancel()
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            await r.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"[WebSocket] Setup error for mission {id_or_slug}: {e}")
        try:
            await websocket.close()
        except Exception:
            pass

# ---- Config Endpoints ----

@app.get("/api/config/status")
async def get_config_status():
    """Return detailed configuration validation status."""
    global _config_validation_result
    if not _config_validation_result:
        # Re-validate on demand if not yet run
        try:
            settings = await get_all_settings()
            _config_validation_result = validate_config(settings)
        except Exception as e:
            return {"status": "ERROR", "message": f"Failed to validate config: {e}"}
    
    return {
        "status": "OK" if _config_validation_result.is_functional else "CRITICAL",
        "functional": _config_validation_result.is_functional,
        "degraded": _config_validation_result.is_degraded,
        "summary": _config_validation_result.summary(),
        "critical_failures": _config_validation_result.critical_failures,
        "required_failures": _config_validation_result.required_failures,
        "optional_failures": _config_validation_result.optional_failures,
        "validated_keys": _config_validation_result.ok,
    }


@app.get("/api/config/models")
async def get_ollama_models():
    """Proxy to Ollama to list available tags."""
    try:
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            return {"status": "ERROR", "message": "Ollama URL not configured in Identity settings", "models": []}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = sorted(list(set(m["name"] for m in data.get("models", []))))
                return {"status": "SUCCESS", "models": models}
            return {"status": "ERROR", "message": f"Ollama returned {resp.status_code}", "models": []}
    except Exception as e:
        return {"status": "ERROR", "message": str(e), "models": []}

@app.get("/api/config")
async def get_gateway_config():
    # Fetch all three model settings from Identity Service to ensure UI is in sync
    assistant = await get_assistant_model()
    coding = await get_coding_model()
    librarian = await get_librarian_model()
    return {
        "status": "SUCCESS", 
        "config": {
            "assistant_model": assistant,
            "coding_model": coding,
            "librarian_model": librarian
        }
    }

@app.post("/api/config")
async def update_gateway_config(new_config: dict):
    # Save the new configuration to the Identity Service GlobalSettings
    async with httpx.AsyncClient(timeout=5.0) as client:
        for key in ["assistant_model", "coding_model", "librarian_model"]:
            if key in new_config:
                val = new_config[key]
                # Identity Service uses PATCH /api/settings/{key} with a body {"value": val}
                try:
                    resp = await client.patch(
                        f"{IDENTITY_SVC}/api/settings/{key}",
                        json={"value": val},
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    if resp.status_code != 200:
                        log.error(f"Failed to sync global config {key}: Identity SVC returned {resp.status_code}")
                        raise HTTPException(status_code=resp.status_code, detail=f"Identity Service error for {key}")
                    
                    # Refresh the internal CONFIG cache ONLY on success
                    CONFIG[key] = val
                    log.info(f"Synchronized global config: {key} -> {val}")
                except Exception as e:
                    log.error(f"Exception during global config sync for {key}: {e}")
                    raise HTTPException(status_code=500, detail=str(e))
    
    log.info(f"Updated Gateway Config via Identity SVC: {new_config}")
    return {"status": "SUCCESS", "config": new_config}


# --- DNS Management Endpoints ---
@app.get("/api/admin/dns")
async def get_dns_config(request: Request):
    """Get full DNS configuration (mappings, upstream, poll interval)."""
    raw_mappings = await fetch_global_setting("dns_mappings", "{}")
    upstream = await fetch_global_setting("dns_upstream", "8.8.8.8,1.1.1.1")
    poll_interval_str = await fetch_global_setting("dns_poll_interval", "30")

    try:
        dns_mappings = json.loads(raw_mappings)
    except (json.JSONDecodeError, TypeError):
        dns_mappings = {}

    try:
        poll_interval = int(poll_interval_str)
    except (ValueError, TypeError):
        poll_interval = 30

    return {
        "dns_mappings": dns_mappings,
        "dns_upstream": upstream,
        "dns_poll_interval": poll_interval,
    }


@app.post("/api/admin/dns/register")
async def register_dns_entry(request: Request):
    """Register a new DNS hostname-to-IP mapping."""
    body = await request.json()
    hostname = body.get("hostname", "").strip()
    ip = body.get("ip", "").strip()

    if not hostname or not ip:
        raise HTTPException(status_code=400, detail="hostname and ip are required")

    raw_mappings = await fetch_global_setting("dns_mappings", "{}")
    try:
        dns_mappings = json.loads(raw_mappings)
    except (json.JSONDecodeError, TypeError):
        dns_mappings = {}

    dns_mappings[hostname] = ip

    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.patch(
            f"{IDENTITY_SVC}/api/settings/dns_mappings",
            json={"value": json.dumps(dns_mappings)},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )

    return {"status": "SUCCESS", "message": f"Registered {hostname} -> {ip}"}


@app.delete("/api/admin/dns/{hostname:path}")
async def remove_dns_entry(hostname: str, request: Request):
    """Remove a DNS hostname-to-IP mapping."""
    raw_mappings = await fetch_global_setting("dns_mappings", "{}")
    try:
        dns_mappings = json.loads(raw_mappings)
    except (json.JSONDecodeError, TypeError):
        dns_mappings = {}

    if hostname not in dns_mappings:
        raise HTTPException(status_code=404, detail=f"DNS entry '{hostname}' not found")

    del dns_mappings[hostname]

    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.patch(
            f"{IDENTITY_SVC}/api/settings/dns_mappings",
            json={"value": json.dumps(dns_mappings)},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )

    return {"status": "SUCCESS", "message": f"Removed {hostname}"}


@app.post("/api/admin/dns/update")
async def update_dns_config(request: Request):
    """Update DNS configuration (upstream, poll interval, or full mappings)."""
    body = await request.json()

    if "dns_upstream" in body:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{IDENTITY_SVC}/api/settings/dns_upstream",
                json={"value": body["dns_upstream"]},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )

    if "dns_poll_interval" in body:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{IDENTITY_SVC}/api/settings/dns_poll_interval",
                json={"value": str(body["dns_poll_interval"])},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )

    if "dns_mappings" in body:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{IDENTITY_SVC}/api/settings/dns_mappings",
                json={"value": json.dumps(body["dns_mappings"])},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )

    return {"status": "SUCCESS", "message": "DNS configuration updated"}


# --- Presence & Location Endpoints ---

@app.get("/api/presence/{user_id}")
async def get_user_presence(user_id: str):
    """Get presence data for a user."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/presence/{user_id}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    raise HTTPException(status_code=502, detail="Presence service unavailable")


@app.get("/api/presence/all")
async def get_all_presence():
    """Get presence data for all users."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/presence/all",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    raise HTTPException(status_code=502, detail="Presence service unavailable")


@app.get("/api/presence/rooms")
async def get_presence_rooms():
    """Get list of all known rooms."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/presence/rooms",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    raise HTTPException(status_code=502, detail="Presence service unavailable")


@app.post("/api/users/{user_id}/location")
async def update_user_location(user_id: str, request: Request):
    """Update user GPS location."""
    body = await request.json()
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{user_id}/location",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    raise HTTPException(status_code=502, detail="Identity service unavailable")


@app.get("/api/users/{user_id}/location")
async def get_user_location(user_id: str):
    """Get user GPS location."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users/{user_id}/location",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    raise HTTPException(status_code=404, detail="Location not found")


@app.post("/api/stt/transcribe")
async def transcribe_audio(request: Request):
    """Transcribe audio using Whisper STT."""
    form = await request.form()
    audio_file = form.get("audio")
    assert isinstance(audio_file, UploadFile), "audio must be a file"
    model = form.get("model", "base")
    language = form.get("language", "en")

    if not audio_file:
        raise HTTPException(status_code=400, detail="audio file required")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/stt/transcribe",
            files={"file": (audio_file.filename, audio_file.file, "audio/wav")},
            data={"model": model, "language": language},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    raise HTTPException(status_code=502, detail="STT service unavailable")


@app.post("/api/voice/command")
async def execute_voice_command(request: Request):
    """Route voice command to execution service."""
    body = await request.json()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/voice/command",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    raise HTTPException(status_code=502, detail="Voice command service unavailable")


@app.get("/api/media/music-assistant/playlists")
async def get_ma_playlists(request: Request):
    """Get Music Assistant playlists (per-user credentials)."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[media/playlists] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "playlists": []}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/media/music-assistant/playlists",
            params={"user_id": creds.get("user") or ""},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    return {"status": "SUCCESS", "playlists": []}


@app.get("/api/media/music-assistant/recent")
async def get_ma_recent(request: Request):
    """Get Music Assistant recently played items (per-user credentials)."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[media/recent] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "recent": []}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/media/music-assistant/recent",
            params={"user_id": creds.get("user") or ""},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    return {"status": "SUCCESS", "recent": []}


@app.get("/api/media/music-assistant/browse")
async def get_ma_browse(request: Request, media_type: str = "TRACKS", offset: int = 0, limit: int = 50, search: str = "", order_by: str = ""):
    """Browse MA library (tracks, albums, artists, playlists, radio) via HA."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[ma/browse] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "items": []}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/media/music-assistant/browse",
            params={"user_id": creds.get("user") or "", "media_type": media_type, "offset": offset, "limit": limit, "search": search, "order_by": order_by},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    return {"status": "SUCCESS", "items": []}


@app.get("/api/media/music-assistant/search")
async def search_ma(request: Request, query: str = "", media_type: str = "", limit: int = 20, artist: str = "", album: str = ""):
    """Search MA for media items via HA."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[ma/search] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "results": []}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/media/music-assistant/search",
            params={"user_id": creds.get("user") or "", "query": query, "media_type": media_type, "limit": limit, "artist": artist, "album": album},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            return resp.json()
    return {"status": "SUCCESS", "results": []}


@app.get("/api/media/audiobookshelf/libraries")
async def get_abs_libraries(request: Request):
    """Get Audiobookshelf libraries (per-user credentials)."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[abs/libraries] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "libraries": []}
    try:
        async with httpx.AsyncClient(timeout=ABS_TIMEOUT) as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "libraries", "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                data = resp.json()
                detail = data.get("detail") or {}
                if detail.get("libraries"):
                    # Normalize 'type' → 'media_type' for UI compatibility
                    libs = detail["libraries"]
                    return {
                        "status": "SUCCESS",
                        "libraries": [
                            {**lib, "media_type": lib.get("media_type") or lib.get("type") or lib.get("media_type", "audiobook")}
                            for lib in libs
                        ],
                    }
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as e:
        log.warning(f"[abs/libraries] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/libraries] ABS error: {e}")
    return {"status": "SUCCESS", "libraries": [], "notice": "ABS unavailable"}


@app.get("/api/media/audiobookshelf/last-played")
async def get_abs_last_played(request: Request):
    """Get Audiobookshelf last played books (per-user credentials)."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[abs/last-played] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "books": []}
    try:
        async with httpx.AsyncClient(timeout=ABS_TIMEOUT) as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "last_played", "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                data = resp.json()
                detail = data.get("detail") or {}
                if detail.get("books"):
                    return {"status": "SUCCESS", "books": detail["books"]}
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as e:
        log.warning(f"[abs/last-played] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/last-played] ABS error: {e}")
    return {"status": "SUCCESS", "books": [], "notice": "ABS unavailable"}


@app.get("/api/media/audiobookshelf/library/{library_id}")
async def get_abs_library_items(library_id: str, request: Request, limit: int = 50):
    """Get audiobooks from a specific Audiobookshelf library (per-user credentials)."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[abs/library] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "books": []}
    try:
        async with httpx.AsyncClient(timeout=ABS_TIMEOUT) as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "list", "library_id": library_id, "limit": limit, "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                data = resp.json()
                detail = data.get("detail") or {}
                if detail.get("books"):
                    return {"status": "SUCCESS", "books": detail["books"]}
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as e:
        log.warning(f"[abs/library] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/library] ABS error: {e}")
    return {"status": "SUCCESS", "books": [], "notice": "ABS unavailable"}


@app.get("/api/media/audiobookshelf/search")
async def search_abs(q: str, request: Request, limit: int = 20):
    """Search Audiobookshelf for audiobooks (per-user credentials)."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[abs/search] identity resolution failed: {e.detail}")
        return {"status": "SUCCESS", "books": []}
    try:
        async with httpx.AsyncClient(timeout=ABS_TIMEOUT) as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "search", "query": q, "limit": limit, "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                data = resp.json()
                detail = data.get("detail") or {}
                if detail.get("books"):
                    return {"status": "SUCCESS", "books": detail["books"]}
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as e:
        log.warning(f"[abs/search] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/search] ABS error: {e}")
    return {"status": "SUCCESS", "books": [], "notice": "ABS unavailable"}


# ─── ABS connectivity status ─────────────────────────────────────────────

@app.get("/api/media/audiobookshelf/status")
async def get_abs_status():
    """Check ABS server connectivity by pinging the login endpoint."""
    try:
        from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
        async with httpx.AsyncClient(timeout=ABS_TIMEOUT) as client:
            # Resolve ABS URL from identity settings
            settings_resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if settings_resp.status_code == 200:
                settings = settings_resp.json()
                abs_url = ""
                for s in settings:
                    if s.get("key") == "audiobookshelf_url" and s.get("value"):
                        abs_url = s["value"]
                        break
                if not abs_url:
                    return {"status": "UNAVAILABLE", "error": "ABS URL not configured", "reachable": False}

            # Ping ABS with a lightweight HEAD request
            async with httpx.AsyncClient(timeout=ABS_TIMEOUT) as ping_client:
                resp = await ping_client.get(f"{abs_url}/api/books?limit=1")
                if resp.status_code == 200:
                    return {"status": "AVAILABLE", "url": abs_url, "reachable": True}
                return {"status": "ERROR", "url": abs_url, "reachable": False, "code": resp.status_code}
    except httpx.TimeoutException as e:
        log.warning(f"[abs/status] ABS timeout: {e}")
        return {"status": "UNREACHABLE", "error": "Connection timed out", "reachable": False}
    except httpx.ConnectError as e:
        log.warning(f"[abs/status] ABS connect error: {e}")
        return {"status": "UNREACHABLE", "error": str(e), "reachable": False}
    except Exception as e:
        log.warning(f"[abs/status] ABS status check failed: {e}")
        return {"status": "ERROR", "error": str(e), "reachable": False}


# ─── Execution service proxy routes (for UI access) ──────────────────────

async def _resolve_user_context(request: Request, body: dict) -> Any:
    """Resolve user context from request, falling back to first user."""
    # If body already has user_context, use it
    if body.get("user_context"):
        return body["user_context"]
    
    # Try to resolve from request
    try:
        creds_data = await _resolve_identity_from_request(request)
        if creds_data.get("user"):
            return creds_data
    except Exception:
        pass
    
    # Fall back to first user
    try:
        first_user = await resolve_first_user()
        if first_user:
            return first_user
    except Exception:
        pass
    
    return {"user": ""}


@app.post("/execute/media/status")
async def proxy_media_status(request: Request):
    """Proxy media status requests from UI to execution service."""
    client = get_http_client()
    async def do_proxy():
        body = await request.json() if await request.body() else {}
        user_ctx = await _resolve_user_context(request, body)
        exec_body = {**body, "user_context": user_ctx}
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/media/status",
            json=exec_body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    try:
        return await retry_http_request(do_proxy, "Execution service (media status)", max_retries=2, base_delay=0.1)
    except httpx.RequestError as e:
        log.error(f"Execution service unreachable for media status: {e}")
        raise HTTPException(status_code=503, detail="Execution service unreachable")


@app.post("/execute/media/transport")
async def proxy_media_transport(request: Request):
    """Proxy media transport requests from UI to execution service."""
    client = get_http_client()
    async def do_proxy():
        body = await request.json() if await request.body() else {}
        user_ctx = await _resolve_user_context(request, body)
        exec_body = {**body, "user_context": user_ctx}
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/media/transport",
            json=exec_body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    try:
        return await retry_http_request(do_proxy, "Execution service (media transport)", max_retries=2, base_delay=0.1)
    except httpx.RequestError as e:
        log.error(f"Execution service unreachable for media transport: {e}")
        raise HTTPException(status_code=503, detail="Execution service unreachable")


@app.post("/execute/media/play")
async def proxy_media_play(request: Request):
    """Proxy media play requests from UI to execution service."""
    client = get_http_client()
    async def do_proxy():
        body = await request.json() if await request.body() else {}
        user_ctx = await _resolve_user_context(request, body)
        exec_body = {**body, "user_context": user_ctx}
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/media/play",
            json=exec_body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    try:
        return await retry_http_request(do_proxy, "Execution service (media play)", max_retries=2, base_delay=0.1)
    except httpx.RequestError as e:
        log.error(f"Execution service unreachable for media play: {e}")
        raise HTTPException(status_code=503, detail="Execution service unreachable")


@app.post("/execute/media/state/sync")
async def proxy_media_state_sync(request: Request):
    """Proxy media state sync requests from UI to execution service."""
    client = get_http_client()
    async def do_proxy():
        body = await request.json() if await request.body() else {}
        user_ctx = await _resolve_user_context(request, body)
        exec_body = {**body, "user_context": user_ctx}
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/media/state/sync",
            json=exec_body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    try:
        return await retry_http_request(do_proxy, "Execution service (media state sync)", max_retries=2, base_delay=0.1)
    except httpx.RequestError as e:
        log.error(f"Execution service unreachable for media state sync: {e}")
        raise HTTPException(status_code=503, detail="Execution service unreachable")


@app.post("/execute/entity/search")
async def proxy_entity_search(request: Request):
    """Proxy entity search requests from UI to execution service."""
    client = get_http_client()
    async def do_proxy():
        body = await request.json() if await request.body() else {}
        user_ctx = await _resolve_user_context(request, body)
        exec_body = {**body, "user_context": user_ctx}
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/entity/search",
            json=exec_body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        data = resp.json()
        if isinstance(data, dict):
            entities = data.get("detail", {}).get("entities", [])
            data["result"] = entities
        return JSONResponse(content=data, status_code=resp.status_code)
    try:
        return await retry_http_request(do_proxy, "Execution service (entity search)", max_retries=2, base_delay=0.1)
    except httpx.RequestError as e:
        log.error(f"Execution service unreachable for entity search: {e}")
        raise HTTPException(status_code=503, detail="Execution service unreachable")


@app.post("/execute/audiobookshelf")
async def proxy_audiobookshelf(request: Request):
    """Proxy audiobookshelf requests from UI to execution service."""
    client = get_http_client()
    async def do_proxy():
        body = await request.json() if await request.body() else {}
        user_ctx = await _resolve_user_context(request, body)
        exec_body = {**body, "user_context": user_ctx}
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/audiobookshelf",
            json=exec_body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    try:
        return await retry_http_request(do_proxy, "Execution service (audiobookshelf)", max_retries=2, base_delay=0.1)
    except httpx.RequestError as e:
        log.error(f"Execution service unreachable for audiobookshelf: {e}")
        raise HTTPException(status_code=503, detail="Execution service unreachable")


@app.get("/api/media/stream/audiobookshelf/{book_id}")
async def stream_audiobookshelf(book_id: str, request: Request):
    """Stream audiobook audio directly from ABS to mobile device."""
    log.info(f"[stream/abs] Received stream request for book_id={book_id}")
    try:
        try:
            creds = await _resolve_identity_from_request(request)
            if not isinstance(creds, dict):
                creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
            log.info(f"[stream/abs] Identity resolved successfully for user: {creds.get('user')}")
        except HTTPException as e:
            log.error(f"[stream/abs] Identity resolution failed: {e.detail}")
            raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}")
        except Exception as e:
            log.error(f"[stream/abs] Identity resolution crashed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error resolving identity")

        abs_url = creds.get("audiobookshelf_url") or ""
        abs_key = creds.get("audiobookshelf_api_key") or ""
        abs_user = creds.get("audiobookshelf_user") or ""
        abs_pass = creds.get("audiobookshelf_pass") or ""

        log.info(f"[stream/abs] Resolved credentials: url={abs_url}, has_key={bool(abs_key)}, user={abs_user}, has_pass={bool(abs_pass)}")

        if not abs_url:
            log.error("[stream/abs] Audiobookshelf URL not configured in resolved credentials")
            raise HTTPException(status_code=400, detail="Audiobookshelf URL not configured")

        # Try API key first, then login with username/password
        if not abs_key and abs_user and abs_pass:
            log.info(f"[stream/abs] No API key present. Attempting username/password login for user '{abs_user}' to {abs_url}")
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    login_resp = await client.post(
                        f"{abs_url.rstrip('/')}/login",
                        json={"username": abs_user, "password": abs_pass}
                    )
                    log.info(f"[stream/abs] Login response code: {login_resp.status_code}")
                    if login_resp.status_code == 200:
                        abs_key = login_resp.json().get("user", {}).get("token")
                        log.info("[stream/abs] Successfully obtained token from ABS login")
                    else:
                        log.error(f"[stream/abs] Login failed with status {login_resp.status_code}: {login_resp.text}")
            except Exception as e:
                log.error(f"[stream/abs] Exception during login attempt: {e}", exc_info=True)

        if not abs_key:
            log.error("[stream/abs] Audiobookshelf credentials/token not configured or resolved")
            raise HTTPException(status_code=400, detail="Audiobookshelf credentials not configured")

        stream_url = f"{abs_url.rstrip('/')}/api/items/{book_id}/stream?format=mp4&token={abs_key}"
        log.info(f"[stream/abs] Constructed ABS stream URL for book {book_id}")

        async def stream_generator(cli, r):
            try:
                bytes_sent = 0
                async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                    try:
                        if request.client and await request.client.disconnect():
                            log.info(f"[stream/abs/generator] Client disconnected after {bytes_sent} bytes for book {book_id}")
                            break
                    except Exception:
                        pass
                    yield chunk
                    bytes_sent += len(chunk)
                log.info(f"[stream/abs/generator] Finished streaming {bytes_sent} bytes for book {book_id}")
            except Exception as e:
                log.error(f"[stream/abs/generator] Error streaming chunks for book {book_id}: {e}", exc_info=True)
                raise
            finally:
                await r.aclose()
                await cli.aclose()

        range_header = request.headers.get("range")
        log.info(f"[stream/abs] Client requested range: {range_header} for book {book_id}")
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=15.0),
            follow_redirects=True,
        )
        try:
            req_headers: dict[str, str] = {
                # Mimic a browser so CDN servers don't reject the proxy request
                "User-Agent": "Mozilla/5.0 (compatible; JarvisOS/2.0; audio-proxy)",
                "Accept": "audio/*,*/*;q=0.9",
                "Authorization": f"Bearer {abs_key}",
            }
            if range_header:
                req_headers["Range"] = range_header
            
            resp = await client.send(
                client.build_request("GET", stream_url, headers=req_headers),
                stream=True
            )
            log.info(f"[stream/abs] ABS stream response status: {resp.status_code}, headers: {dict(resp.headers)}")
            
            response_headers = {
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            }
            for key in ("Content-Range", "Content-Length", "Content-Type"):
                val = resp.headers.get(key)
                if val:
                    response_headers[key] = val
                    
            status_code = resp.status_code
            
            return StreamingResponse(
                stream_generator(client, resp),
                status_code=status_code,
                media_type=response_headers.get("Content-Type", "audio/mpeg"),
                headers=response_headers
            )
        except Exception as e:
            log.error(f"[stream/abs] Stream initiation failed: {e}", exc_info=True)
            await client.aclose()
            raise HTTPException(status_code=502, detail="Failed to connect to media source")
    except HTTPException as he:
        raise he
    except Exception as e:
        log.error(f"[stream/abs] Unhandled exception in stream_audiobookshelf: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal stream error: {str(e)}")


@app.websocket("/api/sendspin")
async def sendspin_proxy(websocket: WebSocket):
    """Proxy WebSocket for MA Sendspin audio streaming.

    The browser connects here via @sendspin/sendspin-js, which sends client/hello
    as its first WebSocket message. MA's sendspin endpoint requires an auth message
    ({"type":"auth","token":"..."}) as the FIRST message before any protocol
    messages. The gateway intercepts client/hello, sends auth to MA, forwards
    client/hello, and begins bidirectional proxying.

    Flow:
    1. Browser sends client/hello → gateway buffers it
    2. Gateway connects to MA sendspin (no query string token)
    3. Gateway sends {"type":"auth","token":"<MA_TOKEN>"} to MA
    4. Gateway forwards buffered client/hello to MA
    5. MA sends server/hello → gateway forwards to browser
    6. Proxy begins bidirectional forwarding
    """
    import websockets
    from urllib.parse import urlparse

    # Extract API token from query params (WebSocket can't set headers)
    api_token = websocket.query_params.get("token")
    if not api_token:
        await websocket.close(code=1008, reason="Missing token")
        return

    # Accept browser connection FIRST (before resolving identity to avoid
    # FastAPI rejecting the WebSocket with 403 before we can call accept())
    await websocket.accept()
    log.info("[sendspin] Browser connection accepted")

    # Resolve MA credentials via identity service
    try:
        creds = await resolve_identity({"api_key": api_token})
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""
    except HTTPException:
        log.error("[sendspin] Identity resolution failed")
        await websocket.close(code=1008, reason="Authentication failed")
        return
    except Exception as e:
        log.error(f"[sendspin] Identity resolution failed: {e}")
        await websocket.close(code=1011, reason="Identity service error")
        return

    if not mass_url or not mass_token:
        log.error("[sendspin] MA credentials not configured")
        await websocket.close(code=1008, reason="MA not configured")
        return

    # Build MA sendspin URL (NO query string token — auth via message)
    parsed = urlparse(mass_url)
    ma_sendspin_url = f"{'wss' if parsed.scheme == 'https' else 'ws'}://{parsed.hostname}:{parsed.port or 8095}/sendspin"
    log.info(f"[sendspin] Connecting to MA sendspin: {ma_sendspin_url}...")

    # Receive the first message from the browser (client/hello)
    message = await websocket.receive()
    first_data = message.get("text") or message.get("bytes")
    if first_data is None or isinstance(first_data, bytes):
        await websocket.close(code=4001, message=b"Expected text message")
        return

    first_msg = json.loads(first_data)
    client_id = first_msg.get("payload", {}).get("client_id", "") if first_msg.get("type") == "client/hello" else "unknown"
    log.info(f"[sendspin] Received {first_msg.get('type', 'unknown')} from browser (client_id={client_id})")

    ma_ws = None
    try:
        # Connect to MA's sendspin endpoint (no query string)
        ma_ws = await websockets.connect(ma_sendspin_url)
        log.info("[sendspin] Connected to MA sendspin")

        # MA sendspin requires {"type":"auth","token":"..."} as FIRST message
        auth_msg = json.dumps({"type": "auth", "token": mass_token})
        log.info("[sendspin] Sending auth to MA")
        await ma_ws.send(auth_msg)

        # MA responds to auth — expect server/hello
        auth_response = await ma_ws.recv()
        log.info(f"[sendspin] MA auth response: {auth_response[:200]}")

        # Now forward the buffered client/hello to MA
        if first_msg.get("type") == "client/hello":
            log.info("[sendspin] Forwarding client/hello to MA")
            await ma_ws.send(first_data)

            # MA sends server/hello back — forward to browser
            hello_response = await ma_ws.recv()
            log.info(f"[sendspin] MA server/hello: {hello_response[:200]}")
            await websocket.send_text(hello_response)
        else:
            log.warning(f"[sendspin] Unexpected first message type: {first_msg.get('type')}")
            await ma_ws.close()
            await websocket.close(code=4001, message=b"Unexpected message type")
            return

        log.info("[sendspin] Handshake complete, starting proxy")

        async def forward_client_to_ma():
            """Forward browser messages to MA (handles both text and binary frames)."""
            try:
                while True:
                    message = await websocket.receive()
                    data = message.get("text") or message.get("bytes")
                    if data is None:
                        break
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    await ma_ws.send(data)
            except WebSocketDisconnect:
                log.info("[sendspin] Browser disconnected")
            except Exception as e:
                log.warning(f"[sendspin] Client→MA forward error: {e}")

        async def forward_ma_to_client():
            """Forward MA messages to browser (handles both text and binary frames)."""
            try:
                while True:
                    message = await ma_ws.recv()
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)
            except Exception as e:
                log.warning(f"[sendspin] MA→Client forward error: {e}")

        # Run both directions in parallel
        await asyncio.gather(
            forward_client_to_ma(),
            forward_ma_to_client(),
        )
    except websockets.exceptions.InvalidStatusCode as e:
        log.error(f"[sendspin] MA sendspin connection failed (status {e.status_code}): {e}")
        try:
            await websocket.close(code=1011, reason=f"MA connection failed: {e}")
        except Exception:
            pass
    except Exception as e:
        log.error(f"[sendspin] Sendspin proxy error: {e}", exc_info=True)
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception:
            pass
    finally:
        if ma_ws:
            try:
                await ma_ws.close()
            except Exception:
                pass


@app.get("/api/ma-jsonrpc/debug/players")
async def debug_list_players(request: Request):
    """Debug endpoint: list all MA players."""
    try:
        creds = await _resolve_identity_from_request(request)
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""
    except HTTPException as e:
        raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Identity resolution failed: {e}")
    
    if not mass_token:
        raise HTTPException(status_code=400, detail="MA token not configured")
    
    from urllib.parse import urlparse
    parsed = urlparse(mass_url)
    ma_api = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8095}/api"
    auth_headers = {"Authorization": f"Bearer {mass_token}"}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            ma_api,
            json={"message_id": "debug_players", "command": "players/all"},
            headers={"Content-Type": "application/json", **auth_headers},
        )
    return {"status": resp.status_code, "result": resp.json()} if resp.status_code == 200 else {"status": resp.status_code, "error": resp.text}


@app.get("/api/ma-jsonrpc/debug/queues")
async def debug_list_queues(request: Request):
    """Debug endpoint: list all MA queues."""
    try:
        creds = await _resolve_identity_from_request(request)
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""
    except HTTPException as e:
        raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Identity resolution failed: {e}")
    
    if not mass_token:
        raise HTTPException(status_code=400, detail="MA token not configured")
    
    from urllib.parse import urlparse
    parsed = urlparse(mass_url)
    ma_api = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8095}/api"
    auth_headers = {"Authorization": f"Bearer {mass_token}"}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            ma_api,
            json={"message_id": "debug_queues", "command": "player_queues/all"},
            headers={"Content-Type": "application/json", **auth_headers},
        )
    return {"status": resp.status_code, "result": resp.json()} if resp.status_code == 200 else {"status": resp.status_code, "error": resp.text}


@app.get("/api/ma-jsonrpc/debug/player/{player_id}")
async def debug_get_player(request: Request, player_id: str):
    """Debug endpoint: get specific player info."""
    try:
        creds = await _resolve_identity_from_request(request)
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""
    except HTTPException as e:
        raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Identity resolution failed: {e}")
    
    if not mass_token:
        raise HTTPException(status_code=400, detail="MA token not configured")
    
    from urllib.parse import urlparse
    parsed = urlparse(mass_url)
    ma_api = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8095}/api"
    auth_headers = {"Authorization": f"Bearer {mass_token}"}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            ma_api,
            json={"message_id": "debug_player", "command": "players/get", "args": {"player_id": player_id}},
            headers={"Content-Type": "application/json", **auth_headers},
        )
    return {"status": resp.status_code, "result": resp.json()} if resp.status_code == 200 else {"status": resp.status_code, "error": resp.text}


@app.websocket("/api/ma-jsonrpc")
async def ma_jsonrpc_proxy(websocket: WebSocket):
    """Proxy WebSocket for MA JSON-RPC control API.

    The browser connects here to send play/pause/seek commands to MA.
    The gateway:
    1. Authenticates the browser client (token from ?token= query param)
    2. Resolves MA credentials from the identity service
    3. Connects to MA's JSON-RPC WebSocket (ws://ma_host:8095/ws?token=...)
    4. Forwards all JSON-RPC messages bidirectionally

    Browser sends JSON-RPC commands like:
    - {"message_id": "counter1", "command": "players/play_media", "args": {"player_id": "...", "media": "spotify://track/..."}}
    - {"message_id": "counter2", "command": "players/cmd_play", "args": {"player_id": "..."}}
    - {"message_id": "counter3", "command": "players/cmd_pause", "args": {"player_id": "..."}}
    - {"message_id": "counter4", "command": "players/cmd_seek", "args": {"player_id": "...", "position": 30}}

    MA responds with:
    - {"type": "RESULT", "message_id": "counter1", "result": {...}}
    - {"event": "queue_updated", "data": {...}}
    - {"event": "player_updated", "data": {...}}
    """
    import websockets
    from urllib.parse import urlparse

    # Extract API token from query params
    api_token = websocket.query_params.get("token")
    if not api_token:
        await websocket.close(code=1008, reason="Missing token")
        return

    await websocket.accept()

    # Resolve MA credentials via identity service
    try:
        creds = await resolve_identity({"api_key": api_token})
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""
    except HTTPException:
        await websocket.close(code=1008, reason="Authentication failed")
        return
    except Exception as e:
        log.error(f"[ma-jsonrpc] Identity resolution failed: {e}")
        await websocket.close(code=1011, reason="Identity service error")
        return

    if not mass_url or not mass_token:
        log.error("[ma-jsonrpc] MA credentials not configured")
        await websocket.close(code=1008, reason="MA not configured")
        return

    # Build MA JSON-RPC URL
    parsed = urlparse(mass_url)
    ws_scheme = 'wss' if parsed.scheme == 'https' else 'ws'
    ma_jsonrpc_url = f"{ws_scheme}://{parsed.hostname}:{parsed.port or 8095}/ws?token={mass_token}"
    log.info(f"[ma-jsonrpc] Connecting to MA JSON-RPC: {ma_jsonrpc_url[:100]}...")

    try:
        async with websockets.connect(
            ma_jsonrpc_url,
            ping_interval=15,
            ping_timeout=10,
        ) as ma_ws:
            log.info("[ma-jsonrpc] Connected to MA JSON-RPC")

            async def forward_client_to_ma():
                """Forward browser JSON-RPC commands to MA."""
                try:
                    while True:
                        msg = await websocket.receive()
                        data = msg.get("text") or msg.get("bytes")
                        if data is None:
                            break
                        await ma_ws.send(data)
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    log.warning(f"[ma-jsonrpc] Client→MA forward error: {e}")

            async def forward_ma_to_client():
                """Forward MA events/responses to browser."""
                try:
                    while True:
                        message = await ma_ws.recv()
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except Exception as e:
                    log.warning(f"[ma-jsonrpc] MA→Client forward error: {e}")

            # Run both directions in parallel
            await asyncio.gather(
                forward_client_to_ma(),
                forward_ma_to_client(),
            )
    except websockets.exceptions.InvalidStatusCode as e:
        log.error(f"[ma-jsonrpc] MA JSON-RPC connection failed (status {e.status_code}): {e}")
        try:
            await websocket.close(code=1011, reason=f"MA connection failed: {e}")
        except Exception:
            pass
    except Exception as e:
        log.error(f"[ma-jsonrpc] JSON-RPC proxy error: {e}", exc_info=True)
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception:
            pass


@app.get("/api/media/stream/music-assistant")
async def stream_music_assistant(uri: str, request: Request):
    """Proxy Music Assistant audio stream to browser.

    Flow (MA web-player pattern):
    1. Resolve credentials (mass_url, mass_token) from the Jarvis identity service.
    2. Discover MA players via JSON-RPC player/list and select an idle player.
    3. Connect to MA WebSocket and authenticate.
    4. Mute the physical player to prevent audio leaking to the hardware device.
    5. Send player_queues/play_media with the URI to populate the queue.
    6. Listen for the queue_updated event to construct the stream URL from queue state.
    7. Send player_queues/pause to stop the physical player.
    8. Unmute the player (restore original state) and disconnect MA WebSocket.
    9. Proxy audio bytes from MA stream server (/flow/) through the Gateway.

    The browser binds <audio src> directly to this gateway endpoint. The physical
    MA player is muted and paused so audio only plays through the browser.
    """
    log.info(f"[stream/ma] Received stream request for uri='{uri}'")
    try:
        try:
            creds = await _resolve_identity_from_request(request)
            if not isinstance(creds, dict):
                creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
            log.info(f"[stream/ma] Identity resolved for user: {creds.get('user')}")
        except HTTPException as e:
            log.error(f"[stream/ma] Identity resolution failed: {e.detail}")
            raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}")
        except Exception as e:
            log.error(f"[stream/ma] Identity resolution crashed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error resolving identity")

        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""

        log.info(f"[stream/ma] Credentials: url={mass_url}, has_token={bool(mass_token)}")

        if not mass_url:
            log.error("[stream/ma] Music Assistant URL not configured")
            raise HTTPException(status_code=400, detail="Music Assistant not configured")

        from urllib.parse import urlparse
        parsed = urlparse(mass_url)
        ma_host = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8095}"
        ma_api = f"{ma_host}/api"

        auth_headers = {"Authorization": f"Bearer {mass_token}"} if mass_token else {}

        # ── Step 1: Discover MA players via JSON-RPC ──────────────────────────
        log.info("[stream/ma] Discovering MA players...")
        available_players: list[str] = []
        import uuid as _uuid
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(
                    ma_api,
                    json={"message_id": _uuid.uuid4().hex, "command": "players/all"},
                    headers={"Content-Type": "application/json", **auth_headers},
                )
                log.info(f"[stream/ma] players/all status: {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    # MA v2 REST returns data directly (not wrapped in {"result": ...})
                    if isinstance(data, list):
                        for p in data:
                            if isinstance(p, dict):
                                pid = p.get("player_id") or p.get("id")
                                if pid:
                                    available_players.append(str(pid))
                                    log.info(f"[stream/ma] Found player: {pid}")
            except Exception as err:
                log.warning(f"[stream/ma] players/all call failed: {err}", exc_info=True)

        if not available_players:
            log.error("[stream/ma] No MA players found")
            raise HTTPException(status_code=404, detail="No Music Assistant players available")

        # ── Step 2: Select target player (prefer idle to avoid interrupting) ─
        log.info("[stream/ma] Selecting target player...")
        target_player_id: str | None = None
        target_previous_state: str = "idle"
        # Priority: idle > paused > playing (don't interrupt active playback)
        best_priority: int = 99
        priority_map = {"idle": 0, "paused": 1, "playing": 2}
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(
                    ma_api,
                    json={"message_id": _uuid.uuid4().hex, "command": "player_queues/all"},
                    headers={"Content-Type": "application/json", **auth_headers},
                )
                log.info(f"[stream/ma] player_queues/all status: {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    # MA v2 REST returns data directly
                    if isinstance(data, list):
                        for q in data:
                            if isinstance(q, dict):
                                queue_id = q.get("queue_id") or ""
                                state = str(q.get("state", "")).lower()
                                # queue_id matches player_id in MA
                                if queue_id in available_players and state in priority_map:
                                    prio = priority_map[state]
                                    if prio < best_priority:
                                        best_priority = prio
                                        target_player_id = queue_id
                                        target_previous_state = state
                                        log.info(f"[stream/ma] Candidate player '{queue_id}' (state={state}, priority={prio})")
                                        if state == "idle":
                                            break  # Can't do better than idle
            except Exception as err:
                log.warning(f"[stream/ma] player_queues/all call failed: {err}", exc_info=True)

        if not target_player_id:
            target_player_id = available_players[0]
            log.info(f"[stream/ma] No suitable player found, defaulting to '{target_player_id}'")
        else:
            log.info(f"[stream/ma] Selected player '{target_player_id}' (previous_state={target_previous_state})")

        # ── Step 3: Connect to MA WebSocket and get stream URL ────────────────
        log.info(f"[stream/ma] Connecting to MA WebSocket...")
        ma_client = MAWebSocketClient(
            mass_url=mass_url,
            mass_token=mass_token,
        )
        try:
            await ma_client.connect()
            log.info(f"[stream/ma] WebSocket connected: {ma_client.ws_url}")
        except Exception as e:
            log.error(f"[stream/ma] WebSocket connection failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to Music Assistant WebSocket: {e}"
            )

        try:
            # Generate session_id for MA stream URL construction
            session_id = str(_uuid.uuid4())
            # Convert ABS book IDs to MA-compatible URIs
            import re as _re
            ma_uri = uri
            if not _re.match(r'^[a-z]+://', uri):
                # URI has no scheme - check if it looks like an ABS book ID (UUID)
                if _re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', uri, _re.IGNORECASE):
                    ma_uri = f"library://audiobookshelf/book/{uri}"
                    log.info(f"[stream/ma] Detected ABS book ID, converted to MA URI: {ma_uri}")

            # ── Mute the player BEFORE play_media to prevent audio on physical device ──
            # We need play_media to populate the queue (for queue_item_id), but we don't
            # want audio coming out of the physical speaker. Mute → play → get URL → pause → unmute.
            try:
                await ma_client.send_command_no_wait(
                    "players/mute",
                    {"player_id": target_player_id, "muted": True},
                )
                log.info(f"[stream/ma] Muted player '{target_player_id}' before play_media")
            except Exception as mute_err:
                log.warning(f"[stream/ma] Failed to mute player before play_media: {mute_err}")

            # Send play_media command with custom_data containing session_id
            log.info(f"[stream/ma] Sending play_media for uri='{ma_uri}' on player='{target_player_id}' (session_id={session_id})")
            play_media_response = await ma_client.send_command(
                "player_queues/play_media",
                {"queue_id": target_player_id, "media": ma_uri, "custom_data": {"session_id": session_id}},
            )
            log.info(f"[stream/ma] play_media response: {play_media_response}")

            # Wait for queue_updated event and extract stream URL from MA's queue state
            stream_url: str | None = None
            stream_timeout = 15.0
            start_time = asyncio.get_event_loop().time()

            while (asyncio.get_event_loop().time() - start_time) < stream_timeout:
                ma_error = ma_client.get_ma_error()
                if ma_error:
                    log.error(f"[stream/ma] MA returned error: {ma_error}")
                    await ma_client.disconnect()
                    raise HTTPException(
                        status_code=502,
                        detail=f"MA error: {ma_error['code']}: {ma_error['details']}"
                    )
                if ma_client.connected:
                    # Priority 1: Use stream URL that MA provides in queue state events
                    ma_provided_url = ma_client.get_stream_url()
                    if ma_provided_url:
                        stream_url = ma_provided_url
                        log.info(f"[stream/ma] Stream URL from MA: {stream_url[:150]}")
                        break
                    # Priority 2: Check queue state directly for stream_url in current_item
                    queue_state = ma_client.get_queue_state()
                    current_item = queue_state.get("current_item", {})
                    if isinstance(current_item, dict) and current_item.get("queue_item_id"):
                        media_item = current_item.get("media_item", {})
                        if isinstance(media_item, dict) and media_item.get("stream_url"):
                            stream_url = media_item["stream_url"]
                            log.info(f"[stream/ma] Stream URL from media_item: {stream_url[:150]}")
                            break
                        if current_item.get("stream_url"):
                            stream_url = current_item["stream_url"]
                            log.info(f"[stream/ma] Stream URL from current_item: {stream_url[:150]}")
                            break
                        # Last resort: construct flow URL using MA's actual queue_id + queue_item_id
                        # Use MA's generated session (queue_id), not the gateway-generated one
                        queue_item_id = current_item["queue_item_id"]
                        queue_id = queue_state.get("queue_id", target_player_id)
                        player_id = queue_state.get("player_id", target_player_id)
                        http_base = mass_url.replace("http://", "").replace("https://", "")
                        stream_url = f"http://{http_base}/flow/{queue_id}/{queue_item_id}/{player_id}.mp3"
                        log.info(f"[stream/ma] Stream URL constructed (fallback): {stream_url[:150]}")
                        break
                await asyncio.sleep(0.2)

            if not stream_url:
                queue_state = ma_client.get_queue_state()
                queue_desc = ma_client.get_queue_state_description()
                log.error(f"[stream/ma] Stream URL not resolved within timeout. queue_state={queue_state}")
                raise HTTPException(
                    status_code=502,
                    detail=f"MA did not resolve queue state within {stream_timeout}s. Queue state: {queue_desc}. Session ID: {session_id}"
                )

            # ── Step 4: Pause the MA player and restore mute state ──────────────
            # The /flow/ stream URL serves audio bytes regardless of player state,
            # so pausing the player stops physical device output while the browser
            # continues to receive the proxied stream.
            try:
                await ma_client.send_command_no_wait(
                    "player_queues/pause",
                    {"queue_id": target_player_id},
                )
                log.info(f"[stream/ma] Sent pause to player '{target_player_id}' to prevent physical audio output")
            except Exception as pause_err:
                log.warning(f"[stream/ma] Failed to pause player after stream URL resolved: {pause_err}")

            # Restore the player's original mute state (unmute unless it was muted before)
            if target_previous_state != "paused":
                try:
                    await ma_client.send_command_no_wait(
                        "players/mute",
                        {"player_id": target_player_id, "muted": False},
                    )
                    log.info(f"[stream/ma] Unmuted player '{target_player_id}' (restoring original state)")
                except Exception as unmute_err:
                    log.warning(f"[stream/ma] Failed to unmute player after pause: {unmute_err}")

            # ── Step 5: Disconnect MA WebSocket (no longer needed) ──────────────
            await ma_client.disconnect()
            log.info("[stream/ma] WebSocket closed after stream URL resolved")

            # ── Step 6: Proxy MA stream bytes through the Gateway ──────────────
            log.info(f"[stream/ma] Initiating byte proxy from: {stream_url[:120]}...")

            async def stream_generator_ma(cli, r):
                try:
                    bytes_sent = 0
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        try:
                            if request.client and await request.client.disconnect():
                                log.info(f"[stream/ma/generator] Client disconnected after {bytes_sent} bytes")
                                break
                        except Exception:
                            pass
                        yield chunk
                        bytes_sent += len(chunk)
                    log.info(f"[stream/ma/generator] Finished streaming {bytes_sent} bytes")
                except Exception as e:
                    log.error(f"[stream/ma/generator] Error streaming chunks: {e}", exc_info=True)
                    raise
                finally:
                    await r.aclose()
                    await cli.aclose()

            range_header = request.headers.get("range")
            log.info(f"[stream/ma] Client requested range: {range_header}")
            proxy_client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=15.0),
                follow_redirects=False,
            )
            try:
                proxy_headers: dict[str, str] = {
                    "User-Agent": "Mozilla/5.0 (compatible; JarvisOS/2.0; audio-proxy)",
                    "Accept": "audio/*,*/*;q=0.9",
                }
                if range_header:
                    proxy_headers["Range"] = range_header

                proxy_resp = await proxy_client.send(
                    proxy_client.build_request("GET", stream_url, headers=proxy_headers),
                    stream=True
                )
                log.info(f"[stream/ma] MA stream response status: {proxy_resp.status_code}")

                proxy_response_headers = {
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "no-cache",
                }
                for key in ("Content-Range", "Content-Length", "Content-Type"):
                    val = proxy_resp.headers.get(key)
                    if val:
                        proxy_response_headers[key] = val

                proxy_status_code = proxy_resp.status_code

                return StreamingResponse(
                    stream_generator_ma(proxy_client, proxy_resp),
                    status_code=proxy_status_code,
                    media_type=proxy_response_headers.get("Content-Type", "audio/mpeg"),
                    headers=proxy_response_headers,
                )
            except HTTPException as he:
                raise he
            except Exception as e:
                log.error(f"[stream/ma] Stream proxy failed: {e}", exc_info=True)
                await proxy_client.aclose()
                raise HTTPException(status_code=502, detail=f"Failed to proxy MA stream: {e}")

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"[stream/ma] WebSocket/stream handling failed: {e}", exc_info=True)
            try:
                await ma_client.disconnect()
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=f"Failed to resolve Music Assistant stream: {e}")

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[stream/ma] Unhandled exception: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to resolve Music Assistant stream: {e}")


@app.get("/api/media/imageproxy")
async def media_imageproxy(path: str, request: Request):
    """Proxy image requests (e.g., entity pictures/covers) from Home Assistant or external sources."""
    log.info(f"[imageproxy] Proxying image request for path={path}")
    try:
        creds = await _resolve_identity_from_request(request)
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
    except Exception as e:
        log.error(f"[imageproxy] Identity resolution failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication required")

    ha_url = creds.get("ha_url") or ""
    ha_token = creds.get("ha_token") or ""

    if not ha_url or not ha_token:
        log.error("[imageproxy] Home Assistant URL/token not configured")
        raise HTTPException(status_code=400, detail="Home Assistant not configured")

    if path.startswith("/"):
        target_url = f"{ha_url.rstrip('/')}{path}"
    else:
        target_url = path

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {ha_token}"}
            resp = await client.get(target_url, headers=headers)
            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                return Response(content=resp.content, media_type=content_type)
            else:
                log.error(f"[imageproxy] Upstream returned status {resp.status_code}")
                raise HTTPException(status_code=resp.status_code, detail="Failed to fetch image from upstream")
    except Exception as e:
        log.error(f"[imageproxy] Exception proxying image: {e}")
        raise HTTPException(status_code=500, detail="Error fetching image")


@app.get("/api/media/detail")
async def get_media_detail(uri: str, request: Request):
    """Resolve full media details from Music Assistant for a given URI."""
    log.info(f"[media/detail] Resolving details for uri='{uri}'")
    try:
        creds = await _resolve_identity_from_request(request)
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
    except Exception as e:
        log.error(f"[media/detail] Identity resolution failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication required")

    mass_url = creds.get("mass_url") or ""
    mass_token = creds.get("mass_token") or ""

    if not mass_url:
        raise HTTPException(status_code=400, detail="Music Assistant URL not configured")

    from urllib.parse import urlparse
    parsed = urlparse(mass_url)
    ma_host = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8095}"
    ma_api = f"{ma_host}/api"
    auth_headers = {"Authorization": f"Bearer {mass_token}"} if mass_token else {}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                ma_api,
                json={"command": "music/item_by_uri", "args": {"uri": uri}},
                headers={"Content-Type": "application/json", **auth_headers},
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                log.error(f"[media/detail] Music Assistant returned status {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail="Failed to fetch media details from Music Assistant")
        except Exception as e:
            log.error(f"[media/detail] Exception: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Error communicating with Music Assistant")


class FavoriteRequest(BaseModel):
    uri: str
    favorite: bool


@app.post("/api/media/favorite")
async def toggle_media_favorite(req: FavoriteRequest, request: Request):
    """Add or remove an item from Music Assistant favorites."""
    log.info(f"[media/favorite] Toggling favorite for uri='{req.uri}' to favorite={req.favorite}")
    if not req.uri or "://" not in req.uri:
        log.info(f"[media/favorite] Skipping non-Music-Assistant URI: '{req.uri}'")
        return {"status": "SKIPPED", "favorite": req.favorite, "reason": "Not a Music Assistant URI"}
    try:
        creds = await _resolve_identity_from_request(request)
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
    except Exception as e:
        log.error(f"[media/favorite] Identity resolution failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication required")

    mass_url = creds.get("mass_url") or ""
    mass_token = creds.get("mass_token") or ""

    if not mass_url:
        raise HTTPException(status_code=400, detail="Music Assistant not configured")

    from urllib.parse import urlparse
    parsed = urlparse(mass_url)
    ma_host = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8095}"
    ma_api = f"{ma_host}/api"
    auth_headers = {"Authorization": f"Bearer {mass_token}"} if mass_token else {}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            if req.favorite:
                # Add to favorites
                resp = await client.post(
                    ma_api,
                    json={"command": "music/favorites/add_item", "args": {"item": req.uri}},
                    headers={"Content-Type": "application/json", **auth_headers},
                )
                if resp.status_code == 200:
                    return {"status": "SUCCESS", "favorite": True}
                else:
                    log.error(f"[media/favorite] Add failed: {resp.status_code} - {resp.text}")
                    raise HTTPException(status_code=resp.status_code, detail="Failed to add to favorites")
            else:
                # To remove, first resolve item details to get item_id and media_type
                resolve_resp = await client.post(
                    ma_api,
                    json={"command": "music/item_by_uri", "args": {"uri": req.uri}},
                    headers={"Content-Type": "application/json", **auth_headers},
                )
                if resolve_resp.status_code != 200:
                    log.error(f"[media/favorite] Resolve failed: {resolve_resp.status_code} - {resolve_resp.text}")
                    raise HTTPException(status_code=resolve_resp.status_code, detail="Failed to resolve item details")

                item = resolve_resp.json()
                item_id = item.get("item_id")
                media_type = item.get("media_type")

                if not item_id or not media_type:
                    # Try looking under result if the response is wrapped
                    res = item.get("result", {}) if isinstance(item, dict) else {}
                    item_id = res.get("item_id")
                    media_type = res.get("media_type")

                if not item_id or not media_type:
                    raise HTTPException(status_code=404, detail="Could not resolve library item ID or media type")

                resp = await client.post(
                    ma_api,
                    json={
                        "command": "music/favorites/remove_item",
                        "args": {"library_item_id": item_id, "media_type": media_type}
                    },
                    headers={"Content-Type": "application/json", **auth_headers},
                )
                if resp.status_code == 200:
                    return {"status": "SUCCESS", "favorite": False}
                else:
                    log.error(f"[media/favorite] Remove failed: {resp.status_code} - {resp.text}")
                    raise HTTPException(status_code=resp.status_code, detail="Failed to remove from favorites")
        except HTTPException as he:
            raise he
        except Exception as e:
            log.error(f"[media/favorite] Exception: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Error communicating with Music Assistant")



