# services/gateway/main.py
import asyncio
import hashlib
import base64
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
import zipfile
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlsplit
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect  # pyright: ignore[reportUnusedImport]
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from services.gateway.agent_loop import (
    execute_inference as provider_execute_inference,
)
from services.gateway.agent_loop import (
    extract_action_json,
    get_dynamic_llm_settings,
    get_vram_safe_params,
)
from services.gateway.apk_manifest import clear_cache as clear_apk_version_cache
from services.gateway.apk_manifest import read_apk_digest, read_apk_version
from services.gateway.background_worker import worker as raven_worker
from services.gateway.config import (
    ABS_TIMEOUT,
    ALPACA_ARCADE_PUBLIC_URL,
    ALPACA_ARCADE_URL,
    ALPACA_AUDIO_URL,
    ALPACA_SD_URL,
    CONFIG,
    CONTROL_PLANE_URL,
    EXECUTION_SVC,
    GEO_SVC,
    IDENTITY_SVC,
    INTERNAL_SECRET,
    LOGGING_SVC,
    OLLAMA_TIMEOUT,
    RAG_SVC,
    STORAGE_SVC,
    TELEMETRY_SVC,
    WORKSPACE_RUNTIME_SVC,
)
from services.gateway.config_validator import validate_config
from services.gateway.history import get_history, get_long_term_memory, ping_redis, update_history
from services.gateway.intent_engine import engine, is_raven_intent
from services.gateway.llm_providers import (
    BaseLLMProvider,
    OllamaProvider,
    OpenRouterProvider,
    extract_thinking_and_content,
    get_provider,
    strip_thinking_blocks,
)
from services.gateway.ma_ws_client import MAWebSocketClient
from services.gateway.media_device_cache import get_last_used_device, set_last_used_device
from services.gateway.ma_scope import check_ma_frame_scope, record_web_player_id
from services.gateway.media_events import acquire_media_hub, stop_all_media_hubs
from services.gateway.media_models import (
    AbsProgressRequest,
    MediaErrorInfo,
    MediaFavoritesResponse,
    MediaHomeResponse,
    MediaItemChild,
    MediaItemDetail,
    MediaLibrary,
    MediaLibraryResponse,
    MediaSearchResponse,
)
from services.gateway.messaging import InferenceJobQueue, JobStatus
from services.gateway.orchestrator import _get, call_ollama, get_all_settings, get_llm_settings
from services.gateway.prompts import (
    PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION,
    PROMPT_MEDIA_TROUBLESHOOTING,
    PROMPT_RAVEN_AUTONOMOUS_PROTOCOL,
    load_prompt,
    load_prompt_sync,
)
from services.gateway.redact import redact_url
from services.gateway.schemas import ResolvedCredentials, StorageIndexRequest, StorageListRequest
from services.gateway.skylight_scope import ChoreScopeError, resolve_chore_scope
from services.gateway.tool_registry import SVC_ALPACA_SD, SVC_EXECUTION, SVC_WORKSPACE, get_tool_schemas
from services.gateway.external_agent import run_external_agent
from services.shared import ma_library
from services.shared.info_endpoint import info_router
from services.shared.media_token import sign, verify

START_TIME = time.time()

# --- Setup Logging IMMEDIATELY ---
# force=True: uvicorn's own logging config may already have set root to
# WARNING (silently dropping all agent-loop INFO diagnostics). Re-assert INFO
# so mission observability lines (Reply shape, Guidance branch, Normalized
# Tool Data) actually reach the logs.
log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s", force=True)

# Singleton lock for Ollama inference to prevent concurrent VRAM exhaustion
INFERENCE_LOCK = asyncio.Lock()


QWEN_GROUNDING_INSTRUCTION = """
# MISSION LOCK: Raven Autonomous Repair Protocol
1. **FOCUS**: You are a repair agent. Your ONLY mission is to resolve the specific BUG or TASK provided in the User Request.
2. **NO DISTRACTIONS**: You are strictly FORBIDDEN from acknowledging, proposing, or implementing any features, schemas, or capabilities seen in the context that are not related to the primary mission.
3. **ZERO CONVERSATION**: You MUST NOT ask questions, seek approval, or provide status updates. Your output must be 100PCT execution-oriented.
4. **TOOL MANDATE**: Every response MUST contain a valid JSON tool call. If you are 'thinking', do it within the 'comment' field of the JSON or as a concise prefix, but the JSON is mandatory.
5. **PATCH PROTOCOL**: Use 'WorkspaceFilePatchRequest' with the 'chunks' (old_text/new_text) schema for surgical edits. NEVER send ASCII art or summaries as 'content'.
6. **TERMINAL EXECUTION**: Continue until the task is verified fixed. If you stall, you are in violation of protocol.
"""

def _split_reasoning(message: str, thinking: str | None) -> tuple[str, str | None]:
    """Keep model reasoning out of the user-visible answer.

    Most paths hand us an answer the orchestrator already cleaned, but any
    caller that forwards raw model output (e.g. the telemetry analyzer calling
    /v1/chat/completions) would otherwise surface `<think>` blocks as the reply
    itself. Split here so every non-streaming response is clean, and the
    reasoning is still available in its own field.
    """
    if thinking or not message:
        return message, thinking
    extracted, content = extract_thinking_and_content(message)
    if not extracted:
        return message, thinking
    # Never return an empty answer just because the model only produced
    # reasoning — the original text is more useful than nothing.
    return (content or message), (extracted or None)


def _make_ollama_response(message: str, model: str, intent: str | None = None, debug_context: str | None = None, stream: bool = False, thinking: str | None = None):
    """Helper to create an Ollama-compatible response (streaming or non-streaming)."""
    if not stream:
        message, thinking = _split_reasoning(message, thinking)
        msg: dict[str, Any] = {"role": "assistant", "content": message}
        if thinking:
            msg["thinking"] = thinking
        res = {
            "model": model,
            "created_at": datetime.now().isoformat() + "Z",
            "message": msg,
            "done": True,
            "status": "SUCCESS"
        }
        if intent:
            res["intent"] = intent
        if debug_context:
            res["debug_context"] = debug_context
        return JSONResponse(res)

    async def gen():
        msg: dict[str, Any] = {"role": "assistant", "content": message}
        if thinking:
            msg["thinking"] = thinking
        chunk = {
            "model": model,
            "created_at": datetime.now().isoformat() + "Z",
            "message": msg,
            "done": False
        }
        yield json.dumps(chunk) + "\n"
        yield json.dumps({"model": model, "done": True}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")

def _make_ollama_chunk(content: str, model: str, done: bool = False, thinking: str | None = None):
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    return {
        "model": model,
        "created_at": datetime.now().isoformat() + "Z",
        "message": msg,
        "done": done
    }


def _make_openai_response(message: str, model: str, intent: str | None = None, debug_context: str | None = None, stream: bool = False, thinking: str | None = None):
    """Helper to create an OpenAI-compatible response (streaming or non-streaming)."""
    if not stream:
        message, thinking = _split_reasoning(message, thinking)
        msg: dict[str, Any] = {"role": "assistant", "content": message}
        if thinking:
            msg["reasoning_content"] = thinking
        res = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"message": msg, "finish_reason": "stop", "index": 0}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }
        if intent:
            res["intent"] = intent
        if debug_context:
            res["debug_context"] = debug_context
        return JSONResponse(res)

    async def gen():
        delta: dict[str, Any] = {"content": message}
        if thinking:
            delta["reasoning_content"] = thinking
        chunk = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"delta": delta, "index": 0, "finish_reason": None}]
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

def _make_openai_chunk(content: str, model: str, finish_reason: str | None = None, reasoning_content: str | None = None):
    import time
    delta: dict[str, Any] = {}
    if content:
        delta["content"] = content
    if reasoning_content:
        delta["reasoning_content"] = reasoning_content
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"delta": delta, "index": 0, "finish_reason": finish_reason}]
    }


def _make_openai_agentic_response(content: str, thinking: str, tool_trace: list, model: str, iterations: int):
    """OpenAI-compatible response for the agentic path: reasoning + tool trace.

    ``reasoning_content`` carries thinking (OpenRouter-style, rendered by
    OpenWebUI); ``sharedllm_tool_trace`` preserves the executed Raven tool
    calls for training-data harvesting.
    """
    import time
    message: dict = {"role": "assistant", "content": content}
    if thinking:
        message["reasoning_content"] = thinking
    return JSONResponse({
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"message": message, "finish_reason": "stop", "index": 0}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "sharedllm_iterations": iterations,
        "sharedllm_tool_trace": tool_trace,
    })


def _make_ollama_agentic_response(content: str, thinking: str, tool_trace: list, model: str, iterations: int):
    """Ollama-compatible response for the agentic path (message.thinking)."""
    message: dict = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    return JSONResponse({
        "model": model,
        "created_at": datetime.now().isoformat() + "Z",
        "message": message,
        "done": True,
        "sharedllm_iterations": iterations,
        "sharedllm_tool_trace": tool_trace,
    })

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
job_queue: InferenceJobQueue | None = None

async def get_job_queue() -> InferenceJobQueue:
    global job_queue
    if job_queue is None:
        redis_url = await _get_redis_url()
        job_queue = InferenceJobQueue(redis_url)
    return job_queue

# REDIS moved below imports

# --- Ouroboros Worker ---
log.info("Successfully imported Raven background worker.")

_DEFAULT_FAST_PATH_THRESHOLD = 0.65

# Global Inference Lock (Strategy 8: Singleton Queue)
async def fetch_global_setting(key: str, default: str = "") -> str:
    """Fetch a global setting from the Identity Service."""
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings/{key}",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status == 200:
                val = (await resp.json()).get("value", default)
                return val
    except Exception as e:
        log.warning(f"Failed to fetch global setting '{key}': {e}")
    return default


async def execute_inference(payload: dict[str, Any]) -> dict[str, Any]:
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
        available_models = {k: v for k, v in settings.items() if "model" in k.lower() and v}
        log.error(f"[get_assistant_model] No assistant model found. active_provider={active}. Available models: {available_models}")
        raise RuntimeError(f"No assistant model configured. Set assistant_model in Identity settings. Available: {available_models}")
    log.info(f"[get_assistant_model] active_provider={active} resolved_model={model}")
    return model


async def get_coding_model():
    settings = await get_llm_settings()
    active = settings.get("active_llm_provider", "ollama")
    model = settings.get("coding_model")
    if not model:
        available_models = {k: v for k, v in settings.items() if "model" in k.lower() and v}
        log.error(f"[get_coding_model] No coding model found. active_provider={active}. Available models: {available_models}")
        raise RuntimeError(f"No coding model configured. Set coding_model in Identity settings. Available: {available_models}")
    log.info(f"[get_coding_model] active_provider={active} resolved_model={model}")
    return model


async def resolve_current_coding_model() -> str:
    """Resolve the coding model currently selected in Identity settings.

    NO fallback chain: only ``coding_model`` from the settings/config DB is
    accepted. Used at mission execution time so system-triggered missions run
    on the model selected in the UI right now, never a frozen or fallback value.
    """
    settings = await get_all_settings()
    model = settings.get("coding_model")
    if not model:
        raise RuntimeError(
            "No coding model configured. Set coding_model in Identity settings "
            "and try again."
        )
    return model


async def get_resident_model() -> str | None:
    """Check what model is currently in VRAM to avoid unnecessary swaps."""
    try:
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        async with shared_http_client() as client:
            resp = await client.get(f"{ollama_url}/api/ps", timeout=aiohttp.ClientTimeout(total=1.0))
            if resp.status == 200:
                models = (await resp.json()).get("models", [])
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
        available_models = {k: v for k, v in settings.items() if "model" in k.lower() and v}
        log.error(f"[get_librarian_model] No librarian model found. active_provider={active}. Available models: {available_models}")
        raise RuntimeError(f"No librarian model configured. Set librarian_model in Identity settings. Available: {available_models}")
    return model


async def get_telemetry_model():
    """Model for telemetry/health/fitness report analysis.

    Deliberately separate from the assistant model: scheduled report runs must
    never share the voice assistant's model, so a report can neither slow down
    nor be slowed by an in-flight conversation. Falls back to librarian, then
    coding, and only then to the assistant — never silently shares the assistant
    while another role is configured.
    """
    settings = await get_llm_settings()
    model = (
        settings.get("telemetry_model")
        or settings.get("librarian_model")
        or settings.get("coding_model")
    )
    if not model:
        log.warning(
            "[get_telemetry_model] No telemetry_model/librarian_model/coding_model configured; "
            "falling back to the assistant model for report analysis"
        )
        return await get_assistant_model()
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
_original_async_client = aiohttp.ClientSession
# One HTTP client per event loop. The Raven background worker now runs on its
# OWN dedicated event loop (its own thread) so a long-running Raven mission
# never competes with the FastAPI API loop for loop turns. Each loop therefore
# needs its own aiohttp client: a single global keyed only by "the last loop
# that touched it" would thrash and leak sessions whenever both loops call in.
_http_clients: dict[asyncio.AbstractEventLoop, aiohttp.ClientSession] = {}
_fallback_http_client: aiohttp.ClientSession | None = None
# threading.Lock (not asyncio.Lock) so it is safe to acquire from BOTH the API
# loop thread and the worker thread without "attached to a different loop" errors.
_dns_recovery_lock = threading.Lock()
_DNS_TTL = 60  # re-resolve DNS at most every 60s so pooled connectors don't go stale
_CLIENT_RECREATE_COOLDOWN = 10.0  # s; avoid tearing down the shared pool on every DNS blip
_last_client_recreate = 0.0


def _new_http_client() -> aiohttp.ClientSession:
    return _original_async_client(
        headers={"X-Request-Source": "shared-llm/app"},
        timeout=aiohttp.ClientTimeout(300.0, connect=30.0),
        connector=aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=_DNS_TTL),
    )


def get_http_client() -> aiohttp.ClientSession:
    """Return the aiohttp client for the CURRENT event loop (lazy, one per loop).

    Caching per-loop is required now that the Raven worker runs on a separate
    event loop from the FastAPI API loop. Each loop keeps a stable client; the
    client is recreated on first use within a loop and reused thereafter.
    """
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is None:
        # No running loop (e.g. a synchronous context). Fall back to a module global.
        global _fallback_http_client
        if _fallback_http_client is None or _fallback_http_client.closed:
            _fallback_http_client = _new_http_client()
        return _fallback_http_client

    client = _http_clients.get(current_loop)
    if client is None or client.closed:
        client = _new_http_client()
        _http_clients[current_loop] = client
    return client


@asynccontextmanager
async def shared_http_client():
    """Yield the shared, pooled global HTTP client WITHOUT closing it on exit.

    Use this instead of ``async with aiohttp.ClientSession()`` for internal
    service-to-service calls so TCP connections are reused across requests
    (the global client is created once per event loop with a connection pool).
    The caller must not close the yielded session.
    """
    yield get_http_client()

async def recreate_http_client():
    """Close the current loop's client and mark it for recreation with fresh DNS
    resolution. This is needed when DNS changes (e.g., dns-sync restart) cause
    stale keepalive connections to fail with empty aiohttp.ClientError messages.

    Operates on the CURRENT event loop's client only (per-loop clients), so the
    API loop and the Raven worker loop each refresh independently. A short
    cooldown prevents storms of DNS failures from churning connections. The new
    client is created lazily on the next ``get_http_client()`` call for that loop.
    """
    with _dns_recovery_lock:
        global _last_client_recreate, _fallback_http_client
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        now = current_loop.time() if current_loop is not None else time.monotonic()
        if current_loop is not None:
            existing = _http_clients.get(current_loop)
            if existing is not None and now - _last_client_recreate < _CLIENT_RECREATE_COOLDOWN:
                log.debug("[DNSRecovery] Skipping client recreation (cooldown active); relying on ttl_dns_cache")
                return
            if existing is not None:
                log.info("[DNSRecovery] Closing stale HTTP client (loop=%s) to refresh DNS resolution", id(current_loop))
                # Close on the owning loop; we are already running on it here.
                await existing.close()
                _http_clients.pop(current_loop, None)
        else:
            if _fallback_http_client is not None and now - _last_client_recreate < _CLIENT_RECREATE_COOLDOWN:
                return
            if _fallback_http_client is not None:
                log.info("[DNSRecovery] Closing stale fallback HTTP client to refresh DNS resolution")
                with suppress(Exception):
                    _fallback_http_client.close()
                _fallback_http_client = None
        _last_client_recreate = now
        log.info("[DNSRecovery] HTTP client marked for refresh (recreated on next use)")

def _is_dns_failure(e: aiohttp.ClientError) -> bool:
    """Detect DNS-related failures that indicate stale DNS cache.
    These manifest as aiohttp.ClientError with empty messages when using
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
    """Retry an aiohttp request with exponential backoff for transient errors.
    Detects DNS-related failures (empty RequestError messages on stale connections)
    and automatically recreates the HTTP client to refresh DNS resolution."""
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except RuntimeError as e:
            # Handle race condition where client was closed by DNS recovery
            if "client has been closed" in str(e):
                if attempt == max_retries:
                    log.error(f"{service_name}: Client closed, all retries exhausted")
                    raise
                log.warning(f"{service_name}: HTTP client was closed, recreating and retrying")
                try:
                    await recreate_http_client()
                except Exception as rec_err:
                    log.error(f"{service_name}: Failed to recreate HTTP client: {rec_err}")
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise
        except aiohttp.ClientError as e:
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
        except TimeoutError as e:
            if attempt == max_retries:
                log.error(f"{service_name}: Timed out after {max_retries + 1} attempts: {e}")
                # Re-raise as aiohttp.ClientError so callers' error handling returns clean 5xx
                raise aiohttp.ClientError(f"Timeout: {e}") from e
            delay = base_delay * (2 ** attempt)
            log.warning(f"{service_name}: Timeout (attempt {attempt+1}/{max_retries+1}): {e}. Retrying in {delay}s")
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
    # Patch DNS resolver so .local hosts (e.g. Ollama) resolve via dns-sync
    # instead of the flaky container DNS path. No hardcoded LAN IPs in compose.
    try:
        from services.common.dns_resolver import patch_dns_resolver
        patch_dns_resolver()
    except Exception as e:
        log.warning(f"[dns-sync] Gateway DNS resolver patch failed: {e}")
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
        raven_worker.start()

    # Warm RAG's embedding model in the background so the FIRST Raven mission's
    # lesson injection doesn't hit RAG's ~25s cold start and silently skip lessons.
    try:
        asyncio.create_task(_warm_rag_lesson_cache())
    except Exception as e:  # non-fatal
        log.warning(f"[Raven] RAG warmup scheduling failed (non-fatal): {e}")

    yield

    log.info("Gateway shutting down...")
    if raven_worker:
        raven_worker.stop()

    with suppress(Exception):
        await stop_all_media_hubs()

    # Close every per-loop HTTP client we created (API loop + worker loop).
    for _client in list(_http_clients.values()):
        with suppress(Exception):
            await _client.close()
    _http_clients.clear()
    global _fallback_http_client
    if _fallback_http_client is not None:
        with suppress(Exception):
            await _fallback_http_client.close()
        _fallback_http_client = None

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
    err_msg = f"Gateway Error: {type(exc).__name__}: {exc!s}"
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

        from services.gateway.history import _get_history_key, _redis
        key = _get_history_key(user_id)
        if _redis is not None:
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

    async with shared_http_client() as client:
      for name, url in services.items():
          # Retry with a generous per-probe timeout. RAG/Execution are the two
          # services the Raven worker hammers; under worker load their /health
          # can briefly exceed a tight timeout. A short retry here prevents a
          # transient blip from marking a healthy service UNREACHABLE (which in
          # turn made the whole gateway report DEGRADED for no real reason).
          ok = False
          status_val = "UNREACHABLE"
          for _attempt in range(3):
              try:
                log.info(f"[health] Checking {name} at {redact_url(url)} (attempt {_attempt + 1})")
                resp = await client.get(url, timeout=aiohttp.ClientTimeout(total=5.0))
                log.info(f"[health] {name} response: {resp.status}")
                if resp.status == 200:
                    ok = True
                    try:
                        data = await resp.json()
                        if isinstance(data, dict):
                            service_details[name] = {
                                "git_sha": data.get("git_sha", "unknown"),
                                "start_time": data.get("start_time", None)
                            }
                    except Exception:
                        pass
                    break
                else:
                    status_val = f"ERROR ({resp.status})"
              except Exception as e:
                status_val = "UNREACHABLE"
                log.warning(f"[health] {name} failed (attempt {_attempt + 1}): {e}")
              if not ok:
                await asyncio.sleep(0.3)
          if ok:
              services_status[name] = "OK"
          else:
              services_status[name] = status_val
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

    if internal_secret != INTERNAL_SECRET and not api_key:
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
        raise HTTPException(status_code=403, detail="Forbidden: Document path traversal detected") from None

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail=f"Documentation '{doc_name}' not found")

    try:
        content = target_path.read_text()
        return {"name": doc_name, "content": content}
    except Exception as e:
        log.error(f"Error reading doc {doc_name}: {e}")
        raise HTTPException(status_code=500, detail="Error reading documentation file") from e

# --- Logging Helper ---
async def emit_log(level: str, message: str, context: dict | None = None):
    try:
      from services.gateway.agent_loop import sanitize_for_llm
      safe_context = sanitize_for_llm(context) if context else None
      safe_message = sanitize_for_llm(message)
      async with shared_http_client() as client:
          await client.post(
            f"{LOGGING_SVC}/log",
            json={"service": "gateway", "level": level, "message": safe_message, "context": safe_context},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=1.0)
          )
    except Exception:
      pass

@app.get("/api/logs")
async def get_api_logs(limit: int = 50, service: str | None = None):
    async with shared_http_client() as client:
      params: dict[str, object] = {"limit": limit}
      if service:
        params["service"] = service
      resp = await client.get(
        f"{LOGGING_SVC}/logs",
        params=params,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
      )
      return await resp.json()

@app.delete("/api/logs")
async def delete_api_logs():
    async with shared_http_client() as client:
      resp = await client.delete(
        f"{LOGGING_SVC}/api/logs",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
      )
      return await resp.json()

@app.get("/api/admin/logs")
async def get_api_admin_logs(request: Request, limit: int = 50, service: str | None = None):
    # This route had no authentication at all -- it took no request, so it could
    # not even look at a caller -- and forwarded straight to the logging service
    # with the internal secret. Anyone who could reach the gateway could read the
    # whole system's logs. Admin-only, verified like every other read.
    await _require_admin(request)
    async with shared_http_client() as client:
      params: dict[str, object] = {"limit": limit}
      if service:
        params["service"] = service
      resp = await client.get(
        f"{LOGGING_SVC}/api/admin/logs",
        params=params,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
      )
      return await resp.json()

@app.delete("/api/admin/logs")
async def delete_api_admin_logs(request: Request):
    # Same hole as the GET: an unauthenticated DELETE of the system log.
    await _require_admin(request)
    async with shared_http_client() as client:
      resp = await client.delete(
        f"{LOGGING_SVC}/api/admin/logs",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
      )
      return await resp.json()

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
    q = (query or "").lower()
    if any(token in q for token in TTS_SIGNALS):
      return load_prompt_sync("raven_narrator_protocol")
    if any(token in q for token in AUTONOMOUS_SIGNALS):
      return load_prompt_sync("raven_autonomous_protocol")
    if any(token in q for token in CODING_SIGNALS):
      return load_prompt_sync("code_helper_system_instruction")
    return load_prompt_sync("assistant_system_instruction")


def is_coding_query(query: str) -> bool:
    q = (query or "").lower()
    return any(token in q for token in CODING_SIGNALS)


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
    if "podcast" in q or "audiobook" in q or "audio" in q or "song" in q or "music" in q:
        return False
    video_signals = (
      "watch ",
      " video",
      "youtube",
      "youtu.be",
      "movie",
      "tv show",
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


def wants_workspace_creation(query: str) -> bool:
    q = (query or "").strip().lower()
    if "raven" in q:
        return False
    if any(sig in q for sig in (
        ".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".yaml", ".yml",
        ".txt", ".sh", ".html", ".css", "file", "pytest", "readme", "script"
    )):
        return False
    create_verbs = ("create", "make", "setup", "set up", "init", "initialize", "new", "add", "provision", "start")
    if not any(v in q for v in create_verbs):
        return False
    return any(
        term in q
        for term in (
            "workspace",
            "work space",
            "workspace environment",
            "work environment",
            "dev environment",
            "development environment",
        )
    )


def extract_workspace_name_from_query(query: str) -> str:
    q = (query or "").strip()
    m = re.search(r"['\"`]([^'\"`]+)['\"`]", q)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:named|called|for)\s+([a-zA-Z0-9_\- ]+)", q, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        name = re.sub(r"[.?!,;]+$", "", name).strip()
        return name
    m = re.search(r"(?:workspace|work space)(?:\s+environment)?\s+([a-zA-Z0-9_\- ]+)", q, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        name = re.sub(r"[.?!,;]+$", "", name).strip()
        return name
    return ""


def wants_direct_code_orchestration(query: str) -> bool:
    q = (query or "").strip().lower()
    # Raven autonomous tasks must go through AgentLoop, not the Librarian's fast code path
    if "raven" in q:
        return False
    if wants_workspace_creation(q):
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
      timeout=aiohttp.ClientTimeout(total=120.0),
    )
    if resp.status != 200:
      raise HTTPException(status_code=resp.status, detail=f"Workspace runtime request failed: {await resp.text()}")
    data = await resp.json()
    if not isinstance(data, dict):
      raise HTTPException(status_code=500, detail=f"Workspace runtime returned invalid payload for {path}")
    return data


async def handle_workspace_creation(
    body: dict,
    user_id: str,
    query: str,
    selected_model: str,
    should_stream: bool,
    is_openai: bool,
) -> JSONResponse | dict | StreamingResponse:
    raw_name = extract_workspace_name_from_query(query)
    if not raw_name:
        raw_name = f"workspace-{uuid.uuid4().hex[:8]}"

    display_name = raw_name
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", raw_name.strip().lower()).strip("-")
    if not slug:
        slug = f"workspace-{uuid.uuid4().hex[:8]}"

    payload = {
        "id": slug,
        "display_name": display_name,
        "owner_user": user_id,
        "scope": "user",
        "description": f"Workspace created for {display_name}",
        "is_default": False,
    }

    try:
        data = await workspace_runtime_request("POST", "/workspaces", json_payload=payload)
        created_id = data.get("id", slug)
        created_name = data.get("display_name", display_name)
        msg = f"Created workspace environment '{created_name}' (id: `{created_id}`) successfully."
    except Exception as e:
        log.error(f"Failed to create workspace: {e}", exc_info=True)
        msg = f"Failed to create workspace '{display_name}': {e}"

    await update_history(user_id, "user", query)
    await update_history(user_id, "assistant", msg)

    if is_openai:
        return _make_openai_response(msg, selected_model, intent="workspace_creation", stream=should_stream)
    return _make_ollama_response(msg, selected_model, intent="workspace_creation", stream=should_stream)


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

    def _query_text() -> str:
        raw = body.get("query")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        for message in body.get("messages") or []:
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return ""

    def _match_named_workspace(query: str) -> dict | None:
        # "in the <name> workspace", "<name> workspace", "workspace <name>",
        # "the <name> workspace" — case-insensitive against id/display_name/local_path
        patterns = [
            r"\bin (?:the |my |our )?([A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*) workspace\b",
            r"\bthe ([A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*) workspace\b",
            r"\b([A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*) workspace\b",
            r"\bworkspace (?:named |called )?[\"']?([A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*)[\"']?\b",
        ]
        query_l = query.lower()
        for pattern in patterns:
            for match in re.finditer(pattern, query_l):
                name = match.group(1).strip().lower()
                if not name or len(name) > 64:
                    continue
                for item in workspaces:
                    if not isinstance(item, dict):
                        continue
                    if name in (
                        str(item.get("id") or "").lower(),
                        str(item.get("display_name") or "").lower(),
                        str(item.get("local_path") or "").lower(),
                    ):
                        return item
        return None

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

    named = _match_named_workspace(_query_text())
    if named:
        if named.get("available"):
            return named
        bootstrapped = await try_bootstrap(named)
        if bootstrapped:
            return bootstrapped

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
      status_lines = "\n".join(f"- {line}" for line in porcelain[:20]) or "- clean" if isinstance(porcelain, list) else "- unavailable"
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
    code_helper_prompt = await load_prompt(get_http_client(), PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION)
    payload = {
      "model": selected_model,
      "messages": [
        {"role": "system", "content": code_helper_prompt},
        {"role": "user", "content": prompt},
      ],
      "stream": False,
      "options": {"num_predict": 4096},
    }
    data = await execute_inference(payload)
    generated = ""
    msg_obj = data.get("message")
    generated = str(msg_obj.get("content") or "") if isinstance(msg_obj, dict) else str(data.get("response") or "")
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

    code_helper_prompt = await load_prompt(get_http_client(), PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION)
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": code_helper_prompt},
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
        raise HTTPException(status_code=500, detail="Invalid JSON plan from coding model") from e

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
      return bool(cached_device and eid == cached_device)

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
      return bool(not device_class and "music_assistant" not in app_id)

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

    if requested_normalized or cached_device:
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


def resolve_video_target(query: str, entities: list[dict], fallback: str | None = None) -> str | None:
    """Resolve target media player specifically for video playback."""
    return resolve_media_target(query, entities, media_type="video") or fallback


# --- Helper Functions ---
async def decompose_command_query(query: str) -> list[str]:
    if " and " not in query.lower() and " then " not in query.lower():
      return [query]
    parts = re.split(r'\s+(?:and|then)\s+', query, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]

async def resolve_identity(body: dict) -> Any:
    from services.gateway.cache import get_cached_identity

    async def _do_resolve() -> Any:
        client = get_http_client()
        resp = await client.post(
            f"{IDENTITY_SVC}/api/resolve",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(300.0, connect=30.0)
        )
        if resp.status != 200:
            err_detail = f"Identity resolution failed: {resp.status} {await resp.text()}"
            log.error(err_detail)
            raise HTTPException(status_code=resp.status, detail=err_detail)
        data = await resp.json()
        if not isinstance(data, dict):
            log.error(f"Identity resolution returned non-dict: {data}")
            raise HTTPException(status_code=500, detail="Identity resolution format error")
        return data

    try:
        return await get_cached_identity(
            body,
            lambda: retry_http_request(_do_resolve, "Identity resolution", max_retries=2, base_delay=0.1),
        )
    except aiohttp.ClientError as e:
        log.error(f"Identity service unreachable: {e}")
        raise HTTPException(status_code=503, detail="Identity service unreachable") from e


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


def _normalize_ma_url(mass_url: str) -> tuple[str, str, int]:
    """Normalize MA URL for direct connections to the gateway.

    Port 8095 is MA's direct HTTP port (never behind Caddy TLS).
    If the user stored https://ha.sumemail.com:8095, returns ('http', hostname, 8095).
    If the user stored https://ha.sumemail.com (port 443, through Caddy),
    returns ('https', hostname, 443).
    """
    parsed = urlparse(mass_url)
    port = parsed.port or 8095
    scheme = "http" if port == 8095 else parsed.scheme
    return scheme, parsed.hostname or "", port


async def _resolve_identity_from_request(request: Request, body: dict | None = None) -> Any:
    """The identity behind a request that must present a real API key.

    Strict by default. Every route that reads or writes family data goes through
    here, and Identity's resolver upgrades *anything* it does not recognise to
    the system default user -- an administrator. Verified live before this was
    tightened, all with no credentials at all:

        GET /api/integrations/skylight/chores   -> 200, 55 family chores
        GET /api/search?q=...                   -> 200, an answer from the RAG index
        GET /api/workspaces                     -> 200, workspace paths and owners
        GET /api/admin/services                 -> 200, the container inventory
        GET /api/admin/logs                     -> 200, system logs
        GET /api/raven/missions                 -> 200, the family's missions
        GET /api/ma-jsonrpc/debug/players       -> 200, every speaker's state
        GET /api/entities                       -> 200, every HA entity

    Those routes check ``is_admin`` afterwards, which the fallback makes
    unconditionally true, so the check was decoration. Now a missing or unknown
    key is a 401 and nothing is served.

    A route that genuinely has no API key -- a voice request, a registered
    device, a media stream URL -- must say so by calling
    ``_resolve_identity_allow_anonymous`` instead.
    """
    return await _require_identity_from_request(request, body)


async def _resolve_identity_allow_anonymous(request: Request, body: dict | None = None) -> Any:
    """Identity with Identity's own fallback rules, including anonymous.

    Identity's ``POST /api/resolve`` tries user_id -> api_key -> rag_user ->
    voice_id -> device_id and then **falls back to the system default user,
    which is an administrator**. That is what lets a voice request, a registered
    device or a stream URL resolve with no API key at all -- which is also why it
    must never guard a route that reads real family data (see
    ``_require_identity_from_request``).

    Only use this where "who is calling" is genuinely not the question.
    """
    return await resolve_identity(_auth_body_from_request(request, body))


def _presented_api_key(request: Request) -> str:
    """The API key this request presents, from the header or the query string."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    # HTMLAudioElement cannot set headers, so local media streaming passes the
    # key as ?token=. Same secret, same handling: it must still be a real key.
    return (request.query_params.get("token") or "").strip()


async def _require_identity_from_request(request: Request, body: dict | None = None) -> Any:
    """Identity for a route that serves real family data. 401 unless proven.

    ``_resolve_identity_from_request`` is permissive on purpose: Identity's
    ``POST /api/resolve`` falls back to the system default user -- an admin --
    when nothing matches, which is what lets a media player, a voice request or
    a stream URL resolve without an API key at all.

    That fallback is exactly wrong for a route that reads somebody's chores,
    books or playlists. Used there, it hands the whole family's data to any
    anonymous caller, and to any caller with a made-up key. Verified live before
    this helper existed::

        GET /api/integrations/skylight/chores          (no auth)      -> 200, 55 chores
        GET /api/integrations/skylight/chores          (bogus key)   -> 200, 55 chores
        GET /api/media/audiobookshelf/last-played      (no auth)      -> 200, books

    So the key is validated with the strict, no-fallback Identity endpoint
    first; only then are the full credentials fetched. A missing or unknown key
    is a 401, never a silent upgrade to the administrator.
    """
    api_key = _presented_api_key(request)
    if not api_key:
        # Service-to-service callers (the automation and agent loops, another
        # container on the bridge network) authenticate with the internal secret
        # rather than a user's API key. They are already inside the trust
        # boundary, so they keep Identity's own resolution rules.
        if request.headers.get("X-Internal-Secret") == INTERNAL_SECRET:
            return await _resolve_identity_allow_anonymous(request, body)
        raise HTTPException(
            status_code=401,
            detail="Missing API Key. Sign in and send 'Authorization: Bearer <api_key>'.",
        )
    ident = await _resolve_strict_identity(api_key)
    if not ident:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    # The key is proven real, so the permissive resolve can no longer fall back:
    # it will find this key's owner and return that user's credentials. It is the
    # *allow anonymous* resolver on purpose -- going back through
    # _resolve_identity_from_request would validate a second time and recurse.
    return await _resolve_identity_allow_anonymous(request, body)


async def _resolve_identity_from_media_token(request: Request) -> Any | None:
    """Resolve identity from a short-lived signed media token (``?mt=&user=``).

    Returns None when no ``mt`` param is present so callers can fall back to
    normal header/token auth. When ``mt`` is present it is authoritative: an
    invalid, tampered or expired token raises 403 even if other credentials
    in the request would otherwise be valid (no fallback).
    """
    mt = request.query_params.get("mt")
    if not mt:
        return None
    user = request.query_params.get("user", "")
    if not user or not verify(mt, user):
        raise HTTPException(status_code=403, detail="Invalid or expired media token")
    try:
        return await resolve_identity({"rag_user": user})
    except HTTPException as e:
        raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}") from e


async def _resolve_strict_identity(api_key: str) -> dict | None:
    """Resolve an API key to its real owner, with NO default-user fallback.

    ``resolve_identity`` (``POST /api/resolve``) tries user_id -> api_key ->
    rag_user -> voice_id -> device_id and then **falls back to the system
    default user, which is an admin**. That makes it useless for deciding *who
    is calling*: every junk string "resolves" successfully and comes back as the
    administrator. Verified directly::

        POST /api/resolve {"api_key": "totally-bogus-key"}
        -> {"user": "default", "is_admin": true, "api_key": "bf7ca7c0..."}

    ``GET /api/internal/validate-api-key`` is the strict variant: no fallback at
    all, so a miss is a 401.

    Returns ``None`` when the key is not a real user's key, or when Identity
    cannot be reached. Callers must deny in that case rather than assume an
    identity -- defaulting to the admin is exactly the bug this replaces.
    """
    key = (api_key or "").strip()
    if not key:
        return None
    try:
        resp = await get_http_client().get(
            f"{IDENTITY_SVC}/api/internal/validate-api-key",
            params={"api_key": key},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
    except Exception as exc:
        log.warning(f"[identity] strict key lookup failed (identity unreachable): {exc}")
        return None
    if resp.status != 200:
        return None
    try:
        ident = await resp.json()
    except Exception:
        return None
    if not isinstance(ident, dict):
        return None
    user = ident.get("user")
    if not user:
        return None
    return ident


async def _acting_identity(request: Request) -> dict | None:
    """The verified acting identity for a geo request, or ``None`` if anonymous.

    Returns ``{"user": str, "is_admin": bool}``.
    """
    # X-User-Id is a gateway->geo transport header. Nothing in the UI, the
    # Android client or the e2e suite ever sends it, so accepting it from an
    # arbitrary client let anyone assert any identity -- including an admin's,
    # and ahead of their own Bearer token. Honour it only from a caller that
    # already holds the internal secret (service-to-service).
    if request.headers.get("X-Internal-Secret") == INTERNAL_SECRET:
        trusted = (request.headers.get("X-User-Id") or "").strip()
        if trusted:
            return {"user": trusted, "is_admin": True}

    auth_header = request.headers.get("Authorization")
    if not (auth_header and auth_header.startswith("Bearer ")):
        return None
    ident = await _resolve_strict_identity(auth_header.split(" ", 1)[1])
    if not ident:
        return None
    return {"user": str(ident["user"]), "is_admin": bool(ident.get("is_admin"))}


async def _user_id_from_request(request: Request) -> str | None:
    """Resolve the acting username, or ``None`` when the caller is anonymous.

    Anonymous callers get ``None`` (never ``"default"``/``"all"``) so that a
    route which forgets to check cannot silently fall back to serving everyone.
    """
    ident = await _acting_identity(request)
    return ident["user"] if ident else None


async def _caller_is_admin(request: Request) -> bool:
    """True when the caller is a verified admin (or an internal service)."""
    ident = await _acting_identity(request)
    return bool(ident and ident.get("is_admin"))


async def _require_authenticated(request: Request) -> str:
    """401 unless the caller is authenticated; returns their username."""
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    return ident["user"]


async def _require_admin(request: Request) -> str:
    """403 unless the caller is an authenticated administrator; returns their username."""
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not ident.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    return ident["user"]


async def _geo_read_target(request: Request, requested: str | None) -> tuple[str, str, str]:
    """Resolve whose data a geo read may return, the viewer, and the admin flag.

    These routes used to do::

        user_id = request.query_params.get("user_id") or await _user_id_from_request(request) or "all"

    so a caller-supplied ``?user_id=`` always beat the authenticated identity,
    and an anonymous caller silently fell through to the ``"all"`` bucket --
    which geo serves from an index containing *every* user's rows.

    Returns ``(target, viewer, is_admin)``. The viewer is forwarded so that GEO
    can apply the opt-in consent check in one place (``geo._viewer_may_see``);
    consent is deliberately not reimplemented here, because two copies of a
    privacy policy is how they drift apart.

    ``is_admin`` has to be forwarded explicitly or geo cannot honour the admin
    carve-out in ``geo._is_privileged_viewer``, and every admin read would be
    refused as if the caller were an ordinary user. It is a third return value
    on purpose: a two-value unpack would let a future route silently forget it.
    """
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    viewer = ident["user"]
    target = (requested or "").strip() or viewer
    is_admin = "true" if ident.get("is_admin") else ""
    return target, viewer, is_admin


async def _resolve_ma_credentials(request: Request, body: dict | None = None) -> tuple[str, str]:
    """Resolve the caller's Music Assistant base URL and token."""
    try:
        creds = await _resolve_identity_from_request(request, body)
        if not isinstance(creds, dict):
            creds = creds.model_dump() if hasattr(creds, "model_dump") else (
                creds.dict() if hasattr(creds, "dict") else dict(creds)
            )
    except HTTPException as e:
        raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Identity resolution failed: {e}") from e
    return creds.get("mass_url") or "", creds.get("mass_token") or ""


async def _ma_rpc(
    mass_url: str,
    mass_token: str,
    command: str,
    args: dict[str, Any] | None = None,
    *,
    message_id: str | None = None,
    timeout: float = 10.0,
) -> Any:
    """Send one JSON-RPC command to Music Assistant's HTTP `/api` endpoint.

    Single place that knows how MA's REST JSON-RPC is shaped, so the player
    picker, the command relay, the stream path and the debug endpoints all
    agree on URL normalization, auth and response unwrapping.

    Returns the unwrapped `result` (MA v2 answers with the payload directly;
    older builds wrap it in `{"result": ...}`).
    """
    ma_scheme, ma_host, ma_port = _normalize_ma_url(mass_url)
    ma_api = f"{ma_scheme}://{ma_host}:{ma_port}/api"
    headers = {"Content-Type": "application/json"}
    if mass_token:
        headers["Authorization"] = f"Bearer {mass_token}"

    payload: dict[str, Any] = {
        "message_id": message_id or uuid.uuid4().hex,
        "command": command,
    }
    if args:
        payload["args"] = args

    async with shared_http_client() as client:
        resp = await client.post(
            ma_api,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        if resp.status != 200:
            detail = await resp.text()
            raise HTTPException(
                status_code=502,
                detail=f"Music Assistant returned HTTP {resp.status}: {detail[:200]}",
            )
        data = await resp.json(content_type=None)

    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data



def _identity_cred_dict(creds: Any) -> dict:
    """Normalize resolved credentials (dict or model) to a plain dict."""
    if isinstance(creds, dict):
        return creds
    dump = getattr(creds, "model_dump", None) or getattr(creds, "dict", None)
    return dump() if callable(dump) else {}


async def _resolve_acting_identity(request: Request, as_user: str | None = None) -> Any:
    """Resolve whose credentials an action should run as.

    `as_user="admin"` lets an authenticated admin post as the shared Admin
    (default) identity — user ID 1, the same account Identity falls back to
    in /api/resolve. Anything else keeps the caller's own identity, and a
    client can never name an arbitrary user: any other value is ignored, and
    a non-admin asking for "admin" gets a 403.
    """
    creds = await _resolve_identity_from_request(request)
    if as_user != "admin":
        return creds
    if not _identity_cred_dict(creds).get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can send as the Admin identity")
    admin_creds = await resolve_first_user()
    if not _identity_cred_dict(admin_creds):
        raise HTTPException(status_code=503, detail="Admin identity is unavailable")
    return admin_creds


async def _proxy_execution_with_identity(
    request: Request,
    endpoint: str,
    payload: dict | None = None,
    *,
    method: str = "POST",
    as_user: str | None = None,
) -> JSONResponse:
    creds_data = await _resolve_acting_identity(request, as_user)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    url = f"{EXECUTION_SVC}{endpoint}"
    async with shared_http_client() as client:
        if method.upper() == "GET":
            resp = await client.get(url, headers=headers, params={"user_id": creds_data.get("user") or ""})
        else:
            exec_payload = {"user_context": creds_data, **(payload or {})}
            resp = await client.post(url, json=exec_payload, headers=headers)
        resp_text = await resp.text()
        try:
            resp_json = json.loads(resp_text)
        except (json.JSONDecodeError, ValueError):
            resp_json = {"detail": resp_text}
    return JSONResponse(status_code=resp.status, content=resp_json)

async def fetch_ha_entities(creds: dict) -> list:
    try:
        url = f"{EXECUTION_SVC}/discovery/entities"
        async with shared_http_client() as client:
            resp = await client.get(
                url,
                params={"ha_url": creds.get("ha_url"), "ha_token": creds.get("ha_token")},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )

            if resp.status != 200:
                log.warning(f"Failed to fetch entities: {resp.status}")
                return []

            try:
                resp_text = await resp.text()
                data = json.loads(resp_text)
            except (json.JSONDecodeError, ValueError) as e:
                log.error(f"Failed to parse HA entities JSON: {e} | Body: {resp_text[:200] if resp_text else 'None'}")
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
                        if resp.status == 200:
                            result = await resp.json()
                            orphaned = result.get("orphaned_entity_ids", [])
                            if orphaned:
                                from services.gateway.ha_state_cache import get_redis
                                r = get_redis()
                                for eid in orphaned:
                                    with suppress(Exception):
                                        r.delete(f"ha:state:{eid}")
                                log.info(f"[ha_sync] Cleaned up {len(orphaned)} orphaned Redis cache entries")
                    except Exception as _e:
                        log.debug(f"RAG sync fire-and-forget failed (non-critical): {_e}")
                _ =                 _ = asyncio.create_task(_sync_to_rag())

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

                _ =                 _ = asyncio.create_task(auto_assign())

            return entities
    except Exception as e:
        log.error(f"Entity discovery error: {e}")
        return []

async def fetch_device_history(creds: dict, entity_id: str, days: int = 1) -> list:
    try:
      resp = await get_http_client().get(
          f"{EXECUTION_SVC}/discovery/history",
          params={
            "ha_url": creds.get("ha_url") or "",
            "ha_token": creds.get("ha_token") or "",
            "entity_id": entity_id,
            "days": days
          },
          headers={"X-Internal-Secret": INTERNAL_SECRET},
          timeout=aiohttp.ClientTimeout(total=5.0)
      )
      if resp.status != 200:
          return []
      data = await resp.json()
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
    creds = await _resolve_identity_from_request(request)
    ha_url = creds.get("ha_url") if creds else None
    ha_token = creds.get("ha_token") if creds else None
    if not ha_url or not ha_token:
        return {
            "entities": [],
            "status": "FAILURE",
            "message": (
                f"Home Assistant credentials not configured for user "
                f"'{creds.get('user') if creds else 'unknown'}' (Identity -> Services)."
            ),
        }
    try:
        resp = await get_http_client().get(
            f"{EXECUTION_SVC}/discovery/entities",
            params={"ha_url": ha_url, "ha_token": ha_token},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        if resp.status != 200:
            return {"entities": []}
        data = await resp.json()
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

    log.info(f"REQUEST: {request.method} {redact_url(str(request.url))} | Headers: {safe_headers}")
    _ =     _ = asyncio.create_task(emit_log("INFO", f"{request.method} {request.url.path}", {"headers": safe_headers}))

    try:
        response = await call_next(request)
    except HTTPException as e:
        return JSONResponse(
            status_code=e.status_code,
            content={"status": "ERROR", "message": str(e.detail)}
        )
    except Exception as e:
        log.error(f"Middleware error: {type(e).__name__}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "ERROR", "message": "Internal Gateway Error"}
        )

    status_code = getattr(response, 'status_code', None) or getattr(response, 'status', 'N/A')
    log.info(f"RESPONSE: {request.method} {redact_url(str(request.url))} | Status: {status_code}")
    _ =     _ = asyncio.create_task(emit_log("INFO", f"RESPONSE {request.method} {request.url.path} -> {status_code}", {}))
    return response

# --- Core Handlers ---
# Removed local extract_user_facts as it is now in history.py


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
            async with shared_http_client() as slot_client:
                deadline = asyncio.get_running_loop().time() + 120.0
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        ps_resp = await slot_client.get(f"{ollama_url}/api/ps", timeout=aiohttp.ClientTimeout(total=3.0))
                        if ps_resp.status == 200:
                            slots = (await ps_resp.json()).get("slots", {})
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
        resp = await get_http_client().post(f"{ollama_url}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT))
        elapsed = asyncio.get_event_loop().time() - start_t
        log.info(f"[ShadowExecution] Ollama responded in {elapsed:.1f}s with status {resp.status}")

        if resp.status == 200:
            proposal = (await resp.json()).get("message", {}).get("content", "")
            return f"\n\n### LIVE SYSTEM PROPOSAL (Shadow Execution)\n{proposal}\n\n[Dev Agent: Compare this proposal against the codebase and architectural intent. Identify any deltas and select the optimal path.]"
        else:
            log.warning(f"[ShadowExecution] Non-200 response: {resp.status} - {await resp.text()}")
    except Exception as e:
        log.warning(f"[ShadowExecution] Failed: {type(e).__name__}: {e}")
    return ""



# Signals indicating a mission needs GitHub/Git authentication (remote repo access).
# Used for a pre-flight gate so we fail fast with an actionable message instead of
# letting Raven run blindly into auth failures mid-mission.
_GITHUB_AUTH_SIGNALS = [
    "github", "create a repo", "create repo", "new repository", "repository named",
    "git push", "push to", "pull request", "pr ", "open a pr", "deploy",
    "git remote", "clone", "gh repo",
]


def _mission_requires_github_auth(query: str) -> bool:
    q = (query or "").lower()
    return any(sig in q for sig in _GITHUB_AUTH_SIGNALS)


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
    show_thinking = body.get("show_thinking", False) or body.get("think", False)

    # Expose SharedLLM's agent tool surface (gh, git, file write, Stable Diffusion
    # image tools) to external OpenAI/Ollama/OpenWebUI clients that explicitly
    # request SharedLLM tools or agentic mode. Never pollute empty tool arrays
    # or external voice pipelines with all 66 internal developer tools.
    if isinstance(body.get("tools"), list) and len(body["tools"]) > 0 and (body.get("agentic") or body.get("sharedllm_tools")):
        existing = {t.get("function", {}).get("name") for t in body["tools"] if isinstance(t, dict)}
        for tool in get_tool_schemas():
            if tool["function"]["name"] not in existing:
                body["tools"].append(tool)

    # 2. Extract Query
    query = body.get("query")
    if not query and "messages" in body and isinstance(body["messages"], list) and len(body["messages"]) > 0:
        last_msg = body["messages"][-1]
        query = last_msg.get("content") if isinstance(last_msg, dict) else str(last_msg)

    if not query:
        return JSONResponse({"status": "ERROR", "message": "No query provided."}, status_code=400)

    # Model selection: an explicit model wins. Otherwise route by query semantics
    # through the canonical selector (code/autonomous -> coding/35B gatekeeper,
    # librarian/RAG -> librarian, everything else -> assistant).
    try:
        clean_req_model = explicit_model[:-7] if explicit_model.endswith(":latest") else explicit_model
        model_lower = clean_req_model.lower()
        if model_lower in ("jarvis", "assistant", "default", "auto"):
            selected_model = await get_assistant_model()
        elif model_lower in ("coding", "coder"):
            selected_model = await get_coding_model()
        elif model_lower in ("librarian", "rag"):
            selected_model = await get_librarian_model()
        elif model_lower in ("telemetry", "health", "fitness", "report"):
            # Scheduled report analysis runs on its own model so it never
            # contends with voice/assistant traffic.
            selected_model = await get_telemetry_model()
        elif clean_req_model:
            selected_model = clean_req_model
        else:
            selected_model = await select_model_for_query(query)
        log.info(f"[ChatHandler] Model selection: explicit_model='{explicit_model}' selected_model='{selected_model}'")
    except RuntimeError as e:
        log.error(f"[ChatHandler] Model configuration error: {e}")
        err_msg = str(e) + " Please configure models in the UI settings."
        if is_openai:
            return _make_openai_response(err_msg, "unknown", "model_config_error")
        return _make_ollama_response(err_msg, "unknown", "model_config_error")

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

    if wants_workspace_creation(query):
        return await handle_workspace_creation(
            body=body,
            user_id=user_id,
            query=query,
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
            resolved_entity = engine.extract_entity(query, intent)
            if not resolved_entity:
                resolved_entity = resolve_media_target(query, media_entities or [], media_type="power", cached_device=cached_device_id)
        elif intent in ["pause_media", "media_transport"]:
            resolved_entity = engine.extract_entity(query, intent) or resolve_media_target(query, media_entities or [], cached_device=cached_device_id)
            if not resolved_entity and media_entities:
                for e in media_entities:
                    if e.get("entity_id", "").startswith("media_player.") and e.get("state") in ("playing", "paused"):
                        resolved_entity = e.get("entity_id")
                        log.info(f"[FastPath] Auto-targeting active {e.get('state')} player: {resolved_entity}")
                        break
        else:
            resolved_entity = engine.extract_entity(query, intent)

        # If cached device was unavailable, bypass to LLM to ask user
        if cached_device_unavailable and not resolved_entity and intent in ["turn_on", "turn_off"]:
            log.info(f"[FastPath] BYPASSED for {intent}: Cached device unavailable, LLM should ask for target")
            is_fast_path = False
        # If the intent requires an entity (lights) but we couldn't resolve one,
        # fallback to the slow-path (LLM) to avoid turning on unknown devices.
        # Media intents DO NOT require resolved_entity because Execution service resolves it.
        elif intent in ["turn_on", "turn_off"] and not resolved_entity:
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
            "location_query": "/execute/location",
        }
        endpoint = endpoint_map.get(intent)
        if endpoint:
            exec_payload: dict = {
                "user_context": creds.model_dump(),
                "action": "turn_on" if intent == "turn_on" else ("turn_off" if intent == "turn_off" else "play"),
                "entity_id": resolved_entity
            }

            # Add specialized payload for storage/ha/location
            if intent == "index_storage":
                exec_payload = {
                    "provider": {"kind": "nextcloud", "settings": {"url": creds.nextcloud_url, "username": creds.nextcloud_user, "password": creds.nextcloud_pass}},
                    "path": "/", "recursive": True
                }
                svc_base = STORAGE_SVC
            elif intent == "location_query":
                q_lower = query.lower()
                m = re.search(r"(?:where is|where's|how fast is|speed of)\s+([A-Za-z]+)", query, re.IGNORECASE)
                user_target = m.group(1).strip() if m else creds.user
                detail = None
                if any(k in q_lower for k in ("speed", "fast", "mph", "driving")):
                    detail = "speed"
                elif any(k in q_lower for k in ("still", "dwell", "stationary", "how long")):
                    detail = "dwell"
                elif any(k in q_lower for k in ("frequent", "often", "most visited", "places")):
                    detail = "frequented"
                elif any(k in q_lower for k in ("cost", "mpg", "vehicle", "fuel", "gas")):
                    detail = "cost"

                exec_payload = {
                    "user_context": creds.model_dump(),
                    "user": user_target,
                    "detail": detail,
                }
                svc_base = EXECUTION_SVC
            elif intent == "play_media":
                media_query, _ = extract_media_request(query)
                media_type = "video" if is_likely_video_request(query) else None
                if not media_type:
                    q_lower = query.lower()
                    if "podcast" in q_lower:
                        media_type = "podcast"
                    elif any(w in q_lower for w in ["audiobook", "book", "audio book"]):
                        media_type = "audiobook"
                clean_q = media_query or query
                if clean_q.lower().startswith("the "):
                    clean_q = clean_q[4:].strip()
                clean_q = re.sub(r'\bSaint\b', 'St.', clean_q, flags=re.IGNORECASE)
                exec_payload = {
                    "user_context": creds.model_dump(),
                    "entity_id": resolved_entity,
                    "query": clean_q,
                    "media_content_type": media_type or "artist",
                    "media_type": media_type,
                }
                svc_base = EXECUTION_SVC
            elif intent in ["pause_media", "media_transport"]:
                transport_cmd = extract_media_transport_command(query) or "pause"
                if transport_cmd == "resume":
                    transport_cmd = "play"
                exec_payload = {
                    "user_context": creds.model_dump(),
                    "entity_id": resolved_entity,
                    "command": transport_cmd,
                }
                svc_base = EXECUTION_SVC
            else:
                svc_base = EXECUTION_SVC

            fast_timeout=aiohttp.ClientTimeout(total=120.0) if intent == "play_media" else aiohttp.ClientTimeout(total=30.0)
            async with shared_http_client() as client:
                exec_resp = await client.post(f"{svc_base}{endpoint}", json=exec_payload, headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=fast_timeout)
                ans = (await exec_resp.json()).get("message", "Action completed.")

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
                    timeout=aiohttp.ClientTimeout(total=10.0)
                )
                resp.raise_for_status()
                res = await resp.json()
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
    # Hardened intent logic: a Raven autonomous mission is only triggered when
    # the prompt EXPLICITLY invokes Raven AND pairs it with a command verb.
    # Bare action words (e.g. "scan", "build", "check") and colon-prefixed
    # phrasing (e.g. "Note: ...", "Q: ...") no longer auto-route ordinary
    # requests to the Raven mission queue.
    if is_raven_intent(query):
        log.info("[ShadowExecution] AUTONOMOUS MISSION DETECTED via Raven keyword + command")
        is_autonomous = True
        system_instruction = await load_prompt(get_http_client(), "raven_autonomous_protocol")

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
        coding_model = await get_coding_model()
        selected_model = coding_model
        log.info("[ShadowExecution] Routing chat request to Raven mission queue...")

        # Auth pre-flight: only gate when the mission actually needs GitHub/Git access.
        if _mission_requires_github_auth(final_query) and not (
            creds.github_token or creds.gitlab_token or creds.git_token
        ):
            msg = (
                f"This mission requires GitHub/Git access (create repository, push, etc.), "
                f"but user '{user_id}' has no Git token configured. Connect your GitHub account "
                f"in Settings, then resubmit the mission."
            )
            if is_openai:
                return _make_openai_response(msg, selected_model, "auth_required")
            return _make_ollama_response(msg, selected_model, "auth_required")

        # Dispatch a real Raven mission so it shows up in the Raven queue. The Raven
        # worker runs the agent loop, which bootstraps its own workspace and executes
        # the task. Pass the agent-loop task (the user's query + full system prompt).
        _ws_id = str(body.get("workspace_id") or "").strip() or None
        try:
            mission = await _enqueue_user_mission(
                query=final_query,
                system=full_system,
                creds=creds.model_dump(),
                coding_model=coding_model,
                workspace_id=_ws_id,
            )
        except HTTPException as he:
            if is_openai:
                return JSONResponse(_make_openai_error(str(he.detail), selected_model), status_code=he.status_code)
            return JSONResponse({"status": "ERROR", "message": str(he.detail)}, status_code=he.status_code)
        except RuntimeError as e:
            msg = str(e) + " Please configure models in the UI settings."
            if is_openai:
                return _make_openai_response(msg, selected_model, "model_config_error")
            return _make_ollama_response(msg, selected_model, "model_config_error")

        mission_id = mission["id"]
        result_payload = {
            "status": "queued",
            "mission_id": mission_id,
            "message": (
                f"Raven mission #{mission_id} dispatched. You will be notified when it completes. "
                f"Track its progress in JarvisLab > Missions or at /api/raven/missions/{mission_id}. "
                "Results are written into the mission's shared workspace (Workspaces page, "
                "path users/default/raven-<topic>) for you to open, inspect, and download."
            ),
            "mission": mission,
        }
        if is_openai:
            return _make_openai_response(
                json.dumps(result_payload, indent=2), selected_model, "raven_mission"
            )
        return JSONResponse(status_code=202, content=result_payload)

    # Agentic path: standard OpenAI/Ollama clients that send `tools` (or
    # explicit `"agentic": true`) get a bounded multi-turn loop where the
    # model can invoke the FULL Raven tool surface with thinking visible.
    # This is the endpoint to point chat windows at for Raven teaching data.
    _req_tools = [t for t in body.get("tools", []) if isinstance(t, dict)] if isinstance(body.get("tools"), list) else []
    _wants_agentic = bool(body.get("agentic")) or len(_req_tools) > 0
    _std_client = (
        is_openai
        or body.get("standard_client", False)
        or ("/api/chat" in str(request.url) and not body.get("async_job", False) and body.get("client") != "voice")
    )
    if _wants_agentic and _std_client and isinstance(body.get("messages"), list) and body["messages"]:
        _msgs: list[dict] = []
        if body.get("system"):
            _msgs.append({"role": "system", "content": str(body["system"])})
        if rag_context:
            if _msgs and _msgs[0].get("role") == "system":
                _msgs[0]["content"] += f"\n\nContext & Known Entities:\n{rag_context}"
            else:
                _msgs.append({"role": "system", "content": f"Context & Known Entities:\n{rag_context}"})
        for _m in body["messages"]:
            if isinstance(_m, dict) and _m.get("role"):
                _msgs.append({k: _m.get(k) for k in ("role", "content", "tool_calls", "tool_call_id", "name") if _m.get(k) is not None})
        log.info(f"[ChatHandler] Agentic loop: model={selected_model} tools={len(_req_tools)} think={show_thinking}")
        _ag_settings = await get_all_settings()
        async with INFERENCE_LOCK:
            outcome = await run_external_agent(
                model=selected_model,
                messages=_msgs,
                tools=body["tools"] if isinstance(body.get("tools"), list) else None,
                creds=creds.model_dump(),
                think=bool(show_thinking),
                ollama_url=_get(_ag_settings, "llm_local_url"),
            )
        try:
            await update_history(user_id, "user", query)
            await update_history(user_id, "assistant", outcome["content"])
        except Exception:
            pass
        if should_stream:
            _chunks = [outcome["content"][i:i + 200] for i in range(0, len(outcome["content"]), 200)] or [""]
            if is_openai:
                async def _agentic_openai_stream():
                    if outcome.get("thinking"):
                        yield f"data: {json.dumps(_make_openai_chunk('', selected_model, reasoning_content=outcome['thinking']))}\n\n"
                    for _c in _chunks:
                        yield f"data: {json.dumps(_make_openai_chunk(_c, selected_model))}\n\n"
                    yield f"data: {json.dumps(_make_openai_chunk('', selected_model, 'stop'))}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(_agentic_openai_stream(), media_type="text/event-stream")
            async def _agentic_ollama_stream():
                if outcome.get("thinking"):
                    yield json.dumps(_make_ollama_chunk('', selected_model, thinking=outcome['thinking'])) + "\n"
                for _c in _chunks:
                    yield json.dumps(_make_ollama_chunk(_c, selected_model)) + "\n"
                yield json.dumps(_make_ollama_chunk('', selected_model, done=True)) + "\n"
            return StreamingResponse(_agentic_ollama_stream(), media_type="application/x-ndjson")
        if is_openai:
            return _make_openai_agentic_response(
                outcome["content"], outcome["thinking"], outcome["tool_trace"],
                selected_model, outcome["iterations"],
            )
        return _make_ollama_agentic_response(
            outcome["content"], outcome["thinking"], outcome["tool_trace"],
            selected_model, outcome["iterations"],
        )

    settings = await get_all_settings()
    _vram_params = await get_vram_safe_params(selected_model, settings)

    # Build job_payload for the FIFO queue
    default_sys = select_system_instruction_for_query(query, selected_model)
    if any(k in query.lower() for k in ["scan", "index", "reindex", "storage", "/notes", "list", "find"]):
        default_sys += (
            "\n\n[SYSTEM OVERRIDE: CRITICAL DIRECTIVE: You have full permission to access the storage system. "
            "You ARE fully capable of executing this storage action. "
            "DO NOT apologize or say you lack access. DO NOT provide a tutorial. "
            "You MUST immediately execute the appropriate tool: `StorageListRequest` to find resources, "
            "or `StorageIndexRequest` to index them. Output the correct JSON block now.]"
        )
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

    assert job_queue is not None, "Job queue not initialized"
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
                        chunk_type = "content"
                        text = chunk
                        if isinstance(chunk, str) and chunk.startswith('{"type":'):
                            try:
                                parsed = json.loads(chunk)
                                chunk_type = parsed.get("type", "content")
                                text = parsed.get("text", "")
                            except Exception:
                                pass

                        if not text:
                            continue

                        if chunk_type == "thinking":
                            if show_thinking:
                                if is_openai:
                                    yield f"data: {json.dumps(_make_openai_chunk('', selected_model, reasoning_content=text))}\n\n"
                                else:
                                    yield json.dumps(_make_ollama_chunk('', selected_model, thinking=text)) + "\n"
                        else:
                            if is_openai:
                                yield f"data: {json.dumps(_make_openai_chunk(text, selected_model))}\n\n"
                            else:
                                yield json.dumps(_make_ollama_chunk(text, selected_model)) + "\n"

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
                    thinking = ""
                    clean_ans = str(ans) if ans is not None else ""
                    if isinstance(ans, str):
                        thinking, clean_ans = extract_thinking_and_content(ans)
                    if is_openai:
                        return _make_openai_response(clean_ans, selected_model, thinking=thinking if show_thinking else None)
                    return _make_ollama_response(clean_ans, selected_model, thinking=thinking if show_thinking else None)
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
        import redis.asyncio as redis

        from services.gateway.config import JOB_STATUS_POLL_INTERVAL, REDIS_URL

        last_status = None
        r = redis.from_url(REDIS_URL, decode_responses=True)
        status_ch = f"raven:job:status:{job_id}"
        pubsub = r.pubsub()
        await pubsub.subscribe(status_ch)
        try:
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
                    # Voice and UI clients render this verbatim (voice speaks it
                    # aloud), so the model's reasoning must not ride along in
                    # the answer. Every other completion path already splits it.
                    thinking = ""
                    if isinstance(result, str):
                        thinking, result = extract_thinking_and_content(result)
                    payload = {'status': 'COMPLETED', 'result': result}
                    if thinking:
                        payload['thinking'] = thinking
                    yield f"data: {json.dumps(payload)}\n\n"
                    break

                if status == JobStatus.FAILED:
                    yield f"data: {json.dumps({'status': 'FAILED', 'error': job.get('error')})}\n\n"
                    break

                # Wait for the next status change via pub/sub; fall back to a
                # periodic GET every JOB_STATUS_POLL_INTERVAL seconds so a missed
                # publish (or a queue-position change) is still observed.
                msg = await pubsub.get_message(timeout=JOB_STATUS_POLL_INTERVAL)
                if msg and msg.get("type") == "message":
                    continue  # status changed; loop re-fetches and yields
        finally:
            with suppress(Exception):
                await pubsub.unsubscribe(status_ch)
            with suppress(Exception):
                await pubsub.close()
            with suppress(Exception):
                await r.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/chat/job/{job_id}")
async def get_chat_job_status(job_id: str):
    """Checks the status of an inference job."""
    if job_queue is None:
        raise HTTPException(status_code=503, detail="Inference queue not available")
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


async def _proxy_json_response(resp: aiohttp.ClientResponse) -> JSONResponse:
    """Proxy an upstream aiohttp response back to the caller.

    Upstream services sometimes return a non-JSON body on error (e.g. a 500
    with ``text/plain`` or an HTML error page). Calling ``resp.json()`` on
    those raises ``ContentTypeError`` and surfaces here as a spurious 500.
    Fall back to the raw text so the upstream status code and body survive.

    Tolerates lightweight response stubs (e.g. ``types.SimpleNamespace`` used
    in tests) that lack ``content_type``/``text``.
    """
    status = getattr(resp, "status", 200)
    try:
        body = await resp.json()
    except Exception:
        text = ""
        try:
            text = await resp.text()
        except Exception:
            text = str(resp)
        return JSONResponse(status_code=status, content=text)
    return JSONResponse(status_code=status, content=body)


@app.post("/api/auth/login")
async def proxy_login(request: Request):
    body = await request.json()
    async with shared_http_client() as client:
        resp = await client.post(f"{IDENTITY_SVC}/api/auth/login", json=body, timeout=aiohttp.ClientTimeout(total=10.0))
        return await _proxy_json_response(resp)

@app.post("/api/auth/change-password")
async def proxy_change_password(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/auth/change-password",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status < 400:
            from services.gateway.cache import invalidate_identity
            invalidate_identity()
        return await _proxy_json_response(resp)


@app.post("/api/users/{username}/password")
async def proxy_admin_set_password(username: str, request: Request):
    body = await request.json()
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{username}/password",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return await _proxy_json_response(resp)


@app.post("/api/auth/import/nextcloud")
async def proxy_import_nextcloud_users(request: Request):
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/auth/import/nextcloud",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return await _proxy_json_response(resp)


@app.post("/api/auth/test-connection")
async def proxy_test_connection(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/auth/test-connection",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)

@app.get("/api/auth/discover")
async def proxy_discover(request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/auth/discover",
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=60.0),
        )
        return await _proxy_json_response(resp)

@app.post("/api/users/{username}/service-token")
async def proxy_create_service_token(username: str, request: Request):
    """One-time password -> per-user service token (admin onboarding a user)."""
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{username}/service-token",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=60.0),
        )
        return await _proxy_json_response(resp)


# ---------------------------------------------------------------------------
# User Panel: device registry + telemetry
#
# The phones only ever talk to the gateway, so identity serving these routes is
# not enough -- without these the register call 404s and the Device table stays
# empty. That is exactly the gap that left self-registration silently dead
# (the same class of mistake as the missing /api/users/sharing-recipients route),
# so each of these is declared ahead of the {device_key} routes below and
# covered by its own proxy test.
# ---------------------------------------------------------------------------


def _panel_headers(request: Request) -> dict:
    """Forward the caller's own credentials; never invent an identity."""
    auth = request.headers.get("Authorization")
    return {"Authorization": auth} if auth else {}


@app.post("/api/user-panel/devices/register")
async def proxy_register_device(request: Request):
    """Phone self-registration, called on login. Fire-and-forget for the client."""
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/user-panel/devices/register",
            headers=_panel_headers(request),
            json=await request.json(),
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/user-panel/devices")
async def proxy_list_devices(request: Request):
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/user-panel/devices",
            headers=_panel_headers(request),
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.post("/api/user-panel/devices")
async def proxy_create_device(request: Request):
    """Admin-only in identity; the gateway just forwards and relays."""
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/user-panel/devices",
            headers=_panel_headers(request),
            json=await request.json(),
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.post("/api/user-panel/devices/telemetry")
async def proxy_ingest_telemetry(request: Request):
    """Declared before {device_key} so 'telemetry' is never read as a key."""
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/user-panel/devices/telemetry",
            headers=_panel_headers(request),
            json=await request.json(),
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        return await _proxy_json_response(resp)


@app.post("/api/user-panel/devices/{device_key}/capabilities")
async def proxy_report_capabilities(device_key: str, request: Request):
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/user-panel/devices/{device_key}/capabilities",
            headers=_panel_headers(request),
            json=await request.json(),
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.patch("/api/user-panel/devices/{device_key}")
async def proxy_assign_device(device_key: str, request: Request):
    async with shared_http_client() as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/user-panel/devices/{device_key}",
            headers=_panel_headers(request),
            json=await request.json(),
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/users/me")
async def proxy_get_me(request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users/me",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)

@app.patch("/api/users/me")
async def proxy_update_me(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/users/me",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)

@app.get("/api/users/sharing-recipients")
async def proxy_sharing_recipients(request: Request):
    """Who the caller may share activity with.

    Identity serves this and deliberately does not require admin, because the
    audience picker has to work for a non-admin -- `getUsers` is admin-only and
    rendering a non-admin's picker empty would leave "Everyone" as the only
    option, which is the opposite of opting in.

    Declared before the "/api/users/{username}" routes so a literal path is
    never captured as a username.
    """
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users/sharing-recipients",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.patch("/api/users/{username}")
async def proxy_update_user(username: str, request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/users/{username}",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)

@app.get("/api/users/{username}/credential-shares")
async def proxy_get_credential_shares(username: str, request: Request):
    """Which shared (system default) services this user may borrow.

    Readable by the user themselves and by admins; Identity enforces that.
    """
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users/{username}/credential-shares",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.put("/api/users/{username}/credential-shares")
async def proxy_put_credential_shares(username: str, request: Request):
    """Grant/revoke shared services for a user. Identity allows admins only."""
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.put(
            f"{IDENTITY_SVC}/api/users/{username}/credential-shares",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/entity-protection")
async def proxy_list_entity_protection(request: Request):
    """Every entity currently locked against normal users, with its permit list.

    Identity allows admins only; a non-admin gets a 403 passed straight
    through, so the gateway deliberately does no role check of its own.
    """
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/entity-protection",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.put("/api/entity-protection/{entity_id}")
async def proxy_put_entity_protection(entity_id: str, request: Request):
    """Lock an entity (with a permit list) or release it. Admins only.

    The id is a Home Assistant ``domain.object`` id, so a single path segment
    is the whole story: the default converter refuses any ``/`` outright. It
    is then percent-encoded before being spliced into the upstream URL, so an
    encoded ``?`` or ``#`` in the id can't truncate the path and quietly lock
    a different entity than the caller asked for.
    """
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.put(
            f"{IDENTITY_SVC}/api/entity-protection/{quote(entity_id, safe='')}",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/settings")
async def proxy_get_settings(request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/settings",
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)
@app.patch("/api/settings/{key}")
async def proxy_update_setting(key: str, request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/settings/{key}",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status < 400:
            from services.gateway.cache import invalidate_settings
            invalidate_settings()
        return await _proxy_json_response(resp)


# --- Arcade (alpaca) game shelf ---------------------------------------------

ARCADE_FEATURED_SETTING_KEY = "arcade_featured_games"
ARCADE_MIN_VOTES = 1
ARCADE_FEATURED_LIMIT = 6


async def _get_arcade_featured_slugs() -> list[str]:
    """Admin-curated featured slugs from Identity's GlobalSetting.

    Stored as a JSON array string under `arcade_featured_games`. A missing or
    malformed value simply means "no curation" — the caller falls back to
    rating-based picks.
    """
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings/{ARCADE_FEATURED_SETTING_KEY}",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status == 404:
                return []
            data = await _safe_json(resp)
    except Exception as exc:  # noqa: BLE001 - curation is optional, never fatal
        log.warning("arcade: featured-settings lookup failed: %s", exc)
        return []
    value = (data or {}).get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _arcade_sort_key(game: dict) -> tuple:
    rating = game.get("rating") or {}
    return (
        -(rating.get("average") or 0.0),
        -(rating.get("count") or 0),
        -(game.get("benchmark_score") or 0),
        (game.get("title") or game.get("slug") or "").lower(),
    )


def _rank_arcade_games(games: list, featured_slugs: list) -> dict:
    """Order the shelf: admin picks first, then best-rated; never hide a game."""
    by_slug = {g.get("slug"): g for g in games}
    featured: list = []
    seen: set = set()
    for slug in featured_slugs:
        game = by_slug.get(slug)
        if game and slug not in seen:
            featured.append({**game, "featured": True})
            seen.add(slug)
    if featured:
        source = "admin"
    else:
        rated = [g for g in games if ((g.get("rating") or {}).get("count") or 0) >= ARCADE_MIN_VOTES]
        pool = rated if rated else games
        featured = [{**g, "featured": True} for g in sorted(pool, key=_arcade_sort_key)[:ARCADE_FEATURED_LIMIT]]
        source = "rating" if rated else "benchmark"
    return {
        "featured": featured,
        "featured_source": source if featured else "none",
        "games": sorted(games, key=_arcade_sort_key),
    }


async def _fetch_arcade_games() -> list:
    async with shared_http_client() as client:
        resp = await client.get(
            f"{ALPACA_ARCADE_URL}/api/games",
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        data = await _safe_json(resp)
    if resp.status >= 400 or not isinstance(data, dict) or not data.get("success"):
        raise ValueError(f"arcade responded {resp.status}")
    games = data.get("games")
    if not isinstance(games, list):
        raise ValueError("arcade returned no games list")
    return games


@app.get("/api/arcade/games")
async def proxy_arcade_games():
    """Featured + full game shelf from the alpaca Arcade service.

    Always 200 with `arcade_available` so the Games tab can render an honest
    offline state instead of failing, and `play_base` so the browser knows
    where to open a game.
    """
    payload = {"play_base": ALPACA_ARCADE_PUBLIC_URL}
    try:
        games = await _fetch_arcade_games()
        featured_slugs = await _get_arcade_featured_slugs()
        ranked = _rank_arcade_games(games, featured_slugs)
        return {"success": True, "arcade_available": True, **payload, **ranked, "count": len(games)}
    except Exception as exc:  # noqa: BLE001 - report the failure, never swallow it
        log.warning("arcade: games fetch failed: %s", exc)
        return {
            "success": False,
            "arcade_available": False,
            "error": f"Arcade unreachable: {exc}",
            **payload,
            "featured": [],
            "featured_source": "none",
            "games": [],
            "count": 0,
        }


@app.put("/api/arcade/featured")
async def proxy_arcade_featured(request: Request):
    """Admin curation: set the featured slug order.

    Identity enforces admin rights on the forwarded credentials, so a
    non-admin caller gets its 403 back unchanged.
    """
    body = await request.json()
    slugs = body.get("slugs")
    if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs):
        raise HTTPException(status_code=422, detail="slugs must be a list of strings")
    clean = [s.strip() for s in slugs if s.strip()]
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/settings/{ARCADE_FEATURED_SETTING_KEY}",
            json={"value": json.dumps(clean)},
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/widgets/settings")
async def proxy_get_widget_settings(request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/widgets/settings",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.put("/api/widgets/settings/{widget_key}")
async def proxy_update_widget_setting(widget_key: str, request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.put(
            f"{IDENTITY_SVC}/api/widgets/settings/{widget_key}",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/users/devices")
async def proxy_list_user_devices(request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users/devices",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.post("/api/users/devices")
async def proxy_add_user_device(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/devices",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.post("/api/settings")
async def proxy_update_settings_bulk(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/settings",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status < 400:
            from services.gateway.cache import invalidate_settings
            invalidate_settings()
        return await _proxy_json_response(resp)

@app.get("/api/calendar/settings")
async def proxy_get_calendar_settings(request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/calendar/settings",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)

@app.put("/api/calendar/settings")
async def proxy_update_calendar_settings(request: Request):
    body = await request.json()
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.put(
            f"{IDENTITY_SVC}/api/calendar/settings",
            json=body,
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)

@app.get("/api/users")
async def proxy_users(request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users",
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.api_route("/api/groups/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_groups(path: str, request: Request):
    auth_header = request.headers.get("Authorization")
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    if auth_header:
        headers["Authorization"] = auth_header
    body = None
    if request.method in ("POST", "PUT"):
        try:
            body = await request.json()
        except Exception:
            body = None
    async with shared_http_client() as client:
        resp = await client.request(
            request.method,
            f"{IDENTITY_SVC}/api/groups/{path}",
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


# --- Telemetry Monitoring ---
@app.get("/api/telemetry/enroll")
async def proxy_list_telemetry_enrollments(request: Request):
    await _resolve_identity_from_request(request)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/telemetry/enroll",
            headers=headers
        )
        return await _proxy_json_response(resp)

@app.post("/api/telemetry/enroll")
async def proxy_enroll_telemetry(request: Request):
    await _resolve_identity_from_request(request)
    body = await request.json()
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/telemetry/enroll",
            json=body,
            headers=headers
        )
        return await _proxy_json_response(resp)


@app.post("/api/telemetry/enroll/{entity_id:path}")
async def proxy_enroll_telemetry_by_id(entity_id: str, request: Request):
    """Path-based enrollment used by the UI (enrollTelemetry(entityId, config))."""
    await _resolve_identity_from_request(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = {**body, "entity_id": entity_id}
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/telemetry/enroll",
            json=body,
            headers=headers
        )
        return await _proxy_json_response(resp)

@app.delete("/api/telemetry/enroll/{entity_id:path}")
async def proxy_unenroll_telemetry(entity_id: str, request: Request):
    await _resolve_identity_from_request(request)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.delete(
            f"{IDENTITY_SVC}/api/telemetry/enroll/{entity_id}",
            headers=headers
        )
        return await _proxy_json_response(resp)

@app.post("/api/telemetry/analyze")
async def proxy_trigger_telemetry_analysis(request: Request):
    await _resolve_identity_from_request(request)
    body = await request.json()
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/telemetry/analyze",
            json=body,
            headers=headers
        )
        return await _proxy_json_response(resp)

@app.get("/api/telemetry/summary/{entity_id:path}")
async def proxy_get_telemetry_summary(entity_id: str, request: Request):
    await _resolve_identity_from_request(request)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/telemetry/summary/{entity_id}",
            headers=headers
        )
        return await _proxy_json_response(resp)

@app.get("/api/telemetry/data/{entity_id:path}")
async def proxy_get_telemetry_data(entity_id: str, request: Request, hours: int | None = None):
    await _resolve_identity_from_request(request)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"hours": hours} if hours else None
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/telemetry/data/{entity_id}",
            headers=headers,
            params=params,
        )
        return await _proxy_json_response(resp)

@app.post("/api/telemetry/snapshot")
async def proxy_ingest_telemetry_snapshot(request: Request):
    await _resolve_identity_from_request(request)
    body = await request.json()
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/telemetry/snapshot",
            json=body,
            headers=headers
        )
        return await _proxy_json_response(resp)

@app.post("/api/telemetry/snapshot/{entity_id:path}")
async def proxy_trigger_telemetry_snapshot(entity_id: str, request: Request):
    """Trigger a manual snapshot for a specific entity (UI calls this for instant updates)."""
    creds = await _resolve_identity_from_request(request)
    ha_url = creds.get("ha_url") if creds else None
    ha_token = creds.get("ha_token") if creds else None
    if not ha_url or not ha_token:
        return {"status": "ERROR", "message": "Home Assistant credentials not configured"}
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/discovery/entities",
            params={"ha_url": ha_url, "ha_token": ha_token},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status != 200:
            return {"status": "ERROR", "message": f"Failed to fetch HA entities: {resp.status}"}
        data = await resp.json()
        entities = data.get("entities", []) if isinstance(data, dict) else []
        target_entity = next((e for e in entities if e.get("entity_id") == entity_id), None)
        if not target_entity:
            return {"status": "ERROR", "message": f"Entity {entity_id} not found in Home Assistant"}

        state = target_entity.get("state")
        attrs = target_entity.get("attributes", {})
        is_available = state not in (None, "unavailable", "unknown", "none", "")
        power_w = None

        try:
            enroll_resp = await client.get(
                f"{IDENTITY_SVC}/api/telemetry/enroll",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if enroll_resp.status == 200:
                enrollments = (await enroll_resp.json()).get("enrollments", [])
                enrollment = next((e for e in enrollments if e.get("entity_id") == entity_id), None)
                if enrollment:
                    power_attr = enrollment.get("power_attribute")
                    if power_attr:
                        attr_value = attrs.get(power_attr)
                        if attr_value is not None:
                            with suppress(TypeError, ValueError):
                                power_w = float(attr_value)
        except Exception:
            pass

        if power_w is None:
            if state not in (None, "unavailable", "unknown"):
                with suppress(TypeError, ValueError):
                    power_w = float(state)
            if power_w is None and "current_power_w" in attrs:
                with suppress(TypeError, ValueError):
                    power_w = float(attrs.get("current_power_w"))

        snapshot = {
            "entity_id": entity_id,
            "power_w": power_w,
            "is_available": is_available,
            "state": state,
            "source": "manual-trigger",
        }
        resp = await client.post(
            f"{IDENTITY_SVC}/api/telemetry/snapshot",
            json=snapshot,
            headers=headers
        )
        if resp.status != 200:
            return {"status": "ERROR", "message": f"Snapshot failed: {resp.status}"}
        return await resp.json()

@app.get("/api/telemetry/insights")
async def proxy_get_telemetry_insights(request: Request):
    await _resolve_identity_from_request(request)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/telemetry/insights",
            headers=headers
        )
        return await _proxy_json_response(resp)


@app.get("/api/telemetry/insights/{entity_id:path}")
async def proxy_get_telemetry_insight(entity_id: str, request: Request):
    """Latest analysis for a single enrolled entity (what the UI requests)."""
    await _resolve_identity_from_request(request)
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/telemetry/insights/{entity_id}",
            headers=headers
        )
        return await _proxy_json_response(resp)


@app.put("/api/telemetry/enroll/{entity_id:path}")
async def proxy_update_telemetry_enrollment(entity_id: str, request: Request):
    """Update an existing enrollment in place (the UI's edit action)."""
    user = await _resolve_identity_from_request(request)
    body = await request.json()
    if isinstance(body, dict):
        body.setdefault("entity_id", entity_id)
        body["owner_user_id"] = getattr(user, "user", None) or body.get("owner_user_id")
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with borrow_http_client() as client:
        resp = await client.put(
            f"{IDENTITY_SVC}/api/telemetry/enroll/{entity_id}",
            json=body,
            headers=headers,
        )
        return await _proxy_json_response(resp)


@app.delete("/api/devices/{device_id:path}")
async def proxy_delete_device(device_id: str, request: Request):
    auth_header = request.headers.get("Authorization")
    async with shared_http_client() as client:
        resp = await client.delete(
            f"{IDENTITY_SVC}/api/devices/{device_id}",
            headers={"Authorization": auth_header} if auth_header else {}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
    return await _proxy_json_response(resp)


@app.get("/api/communication/timers")
async def proxy_list_timers(request: Request):
    return await _proxy_execution_with_identity(request, "/execute/timers", method="GET")


@app.post("/api/communication/timers")
async def proxy_add_timer(request: Request):
    body = await request.json()
    payload = {
        "action": body.get("action", "add"),
        "type": body.get("type", "timer"),
        "title": body.get("title"),
        "duration_str": body.get("duration_str"),
        "time_str": body.get("time_str"),
        "recurrence": body.get("recurrence"),
        "target_device": body.get("target_device"),
    }
    if body.get("id") is not None:
        payload["id"] = body.get("id")
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
    if body.get("id") is not None:
        payload["id"] = body.get("id")
    return await _proxy_execution_with_identity(request, "/execute/timer", payload)


@app.get("/api/communication/calendar/calendars")
async def proxy_list_calendars(request: Request):
    payload = {"action": "list"}
    return await _proxy_execution_with_identity(request, "/execute/calendar", payload)


@app.get("/api/communication/calendar/events")
async def proxy_read_calendar(request: Request, calendar_name: str | None = None, integration: str | None = None):
    payload = {"action": "read"}
    if calendar_name:
        payload["calendar_name"] = calendar_name
    if integration:
        payload["integration"] = integration
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
    if body.get("integration"):
        payload["integration"] = body.get("integration")
    return await _proxy_execution_with_identity(request, "/execute/calendar", payload)


@app.put("/api/communication/calendar/events")
async def proxy_update_calendar_event(request: Request):
    body = await request.json()
    payload = {
        "action": "update",
        "event_id": body.get("event_id"),
        "integration": body.get("integration"),
        "query": body.get("query"),
        "summary": body.get("summary"),
        "start_time": body.get("start_time"),
    }
    return await _proxy_execution_with_identity(request, "/execute/calendar", payload)


@app.delete("/api/communication/calendar/events")
async def proxy_delete_calendar_event(request: Request):
    body = await request.json()
    payload = {
        "action": "delete",
        "event_id": body.get("event_id"),
        "integration": body.get("integration"),
        "query": body.get("query"),
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
    return await _proxy_execution_with_identity(request, "/execute/note", payload, as_user=body.get("as_user"))


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
    return await _proxy_execution_with_identity(request, "/execute/note", payload, as_user=body.get("as_user"))


@app.post("/api/communication/notes/write")
async def proxy_write_note(request: Request):
    """Full replace save from the notes editor (create overwrites in place)."""
    body = await request.json()
    payload = {
        "action": "write",
        "title": body.get("title"),
        "content": body.get("content"),
        "category": body.get("category"),
        "path": body.get("path"),
        "storage": body.get("storage", "nextcloud"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload, as_user=body.get("as_user"))


@app.post("/api/communication/notes/check_off")
async def proxy_check_off_note(request: Request):
    """Toggle one checklist item in a note."""
    body = await request.json()
    payload = {
        "action": "check_off",
        "title": body.get("title"),
        "item": body.get("item"),
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
    return await _proxy_execution_with_identity(request, "/execute/note", payload, as_user=body.get("as_user"))


@app.post("/api/communication/notes/list")
async def proxy_list_notes(request: Request):
    body = await request.json() if request.method == "POST" else {}
    payload = {
        "action": "list",
        "storage": body.get("storage", "nextcloud"),
        "directories": body.get("directories"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload, as_user=body.get("as_user"))


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
async def proxy_get_skylight_chores(
    request: Request,
    date: str | None = None,
    scope: str | None = None,
):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})

    # The Skylight login is one shared household credential, so it says nothing
    # about whose chores to show -- the caller decides. `scope` lets an admin
    # narrow the view to their own chores ("me") or to one member by login name;
    # without it an admin still gets the whole frame and everyone else still gets
    # only their own, filtered by the Skylight category label.
    try:
        scope_user = resolve_chore_scope(
            scope=scope,
            caller_user=creds.get("user") or "",
            is_admin=bool(creds.get("is_admin")),
        )
    except ChoreScopeError as e:
        return JSONResponse(status_code=403, content={"status": "FAILURE", "message": str(e)})

    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": scope_user, "date": date or ""}
    async with shared_http_client() as client:
        resp = await client.get(f"{EXECUTION_SVC}/api/integrations/skylight/chores", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30.0))
    return await _proxy_json_response(resp)


@app.post("/api/integrations/skylight/chores/{chore_id}/complete")
async def proxy_complete_skylight_chore(chore_id: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})

    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with shared_http_client() as client:
        resp = await client.post(f"{EXECUTION_SVC}/api/integrations/skylight/chores/{chore_id}/complete", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30.0))
    return await _proxy_json_response(resp)


@app.post("/api/integrations/skylight/chores/{chore_id}/uncomplete")
async def proxy_uncomplete_skylight_chore(chore_id: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})

    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with shared_http_client() as client:
        resp = await client.post(f"{EXECUTION_SVC}/api/integrations/skylight/chores/{chore_id}/uncomplete", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30.0))
    return await _proxy_json_response(resp)


@app.get("/api/integrations/skylight/rewards")
async def proxy_get_skylight_rewards(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})

    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with shared_http_client() as client:
        resp = await client.get(f"{EXECUTION_SVC}/api/integrations/skylight/rewards", headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30.0))
    return await _proxy_json_response(resp)


@app.post("/api/integrations/skylight/rewards/{reward_id}/redeem")
async def proxy_redeem_skylight_reward(reward_id: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("skylight_enabled", True):
        return JSONResponse(status_code=400, content={"status": "FAILURE", "message": "Skylight is disabled for your account"})

    body = await request.json() if await request.body() else {}
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    params = {"user": creds.get("user", "")}
    async with shared_http_client() as client:
        resp = await client.post(f"{EXECUTION_SVC}/api/integrations/skylight/rewards/{reward_id}/redeem", json=body, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30.0))
    return await _proxy_json_response(resp)


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
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


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
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


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
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))

@app.get("/api/communication/talk/reactions")
async def proxy_get_talk_reactions(request: Request):
    payload = {
        "action": "reactions",
        "token": request.query_params.get("token"),
        "message_id": int(request.query_params.get("message_id", "0")),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload)


@app.post("/api/communication/talk/react")
async def proxy_react_talk_message(request: Request):
    body = await request.json()
    payload = {
        "action": "react",
        "token": body.get("token"),
        "message_id": body.get("message_id"),
        "reaction": body.get("reaction"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


@app.post("/api/communication/talk/call")
async def proxy_talk_call(request: Request):
    """Join or leave the room's Talk call and return its signalling details."""
    body = await request.json()
    action = "call_leave" if body.get("leave") else "call_join"
    payload = {"action": action, "token": body.get("token")}
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


@app.post("/api/communication/talk/game")
async def proxy_talk_game(request: Request):
    """Drive a family game in a room: start | answer | flip | stop."""
    body = await request.json()
    payload = {
        "action": "game",
        "token": body.get("token"),
        "message": body.get("message"),
        "game_command": body.get("game_command"),
        "game_kind": body.get("game_kind"),
        "game_words": body.get("game_words"),
        "card_detail": body.get("player") or body.get("card_detail"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


@app.post("/api/communication/talk/card")
async def proxy_post_talk_card(request: Request):
    """Post a typed card (achievement, game move, creation) into a room."""
    body = await request.json()
    payload = {
        "action": "post_card",
        "token": body.get("token"),
        "message": body.get("message"),
        "card_kind": body.get("card_kind"),
        "card_title": body.get("card_title"),
        "card_detail": body.get("card_detail"),
        "card_stars": body.get("card_stars"),
        "card_stats": body.get("card_stats"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


@app.post("/api/communication/talk/read")
async def proxy_mark_talk_read(request: Request):
    """Clear the unread badge for a conversation."""
    body = await request.json()
    payload = {"action": "mark_read", "token": body.get("token")}
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


@app.get("/api/communication/talk/polls")
async def proxy_get_talk_polls(request: Request):
    payload = {"action": "polls", "token": request.query_params.get("token")}
    return await _proxy_execution_with_identity(request, "/execute/talk", payload)


@app.post("/api/communication/talk/polls/create")
async def proxy_create_talk_poll(request: Request):
    body = await request.json()
    payload = {
        "action": "create_poll",
        "token": body.get("token"),
        "question": body.get("question"),
        "options": body.get("options"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


@app.post("/api/communication/talk/polls/vote")
async def proxy_vote_talk_poll(request: Request):
    body = await request.json()
    payload = {
        "action": "vote_poll",
        "token": body.get("token"),
        "poll_id": body.get("poll_id"),
        "option_id": body.get("option_id"),
    }
    return await _proxy_execution_with_identity(request, "/execute/talk", payload, as_user=body.get("as_user"))


@app.post("/api/generate")
async def proxy_generate(request: Request):
    try:
        body = await request.json()
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        async with shared_http_client() as client:
            resp = await client.post(f"{ollama_url}/api/generate", json=body, timeout=None)
            if resp.status != 200:
                error_text = await resp.text()
                return JSONResponse({"status": "ERROR", "message": error_text}, status_code=resp.status)

            async def generate():
                async for chunk in resp.content.iter_any():
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
        async with shared_http_client() as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status != 200:
                return JSONResponse({"models": []}, status_code=200)
            data = await resp.json()
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
        async with shared_http_client() as client:
            resp = await client.post(f"{ollama_url}/api/show", json=body, timeout=aiohttp.ClientTimeout(total=30.0))
            return await _proxy_json_response(resp)
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
        async with shared_http_client() as client:
            resp = await client.post(f"{ollama_url}/api/embeddings", json=body, timeout=aiohttp.ClientTimeout(total=60.0))
            return await _proxy_json_response(resp)
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
        async with shared_http_client() as client:
            resp = await client.post(f"{ollama_url}/api/embed", json=body, timeout=aiohttp.ClientTimeout(total=60.0))
            return await _proxy_json_response(resp)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)
@app.get("/api/search")
async def global_search(q: str, request: Request):
    """Global semantic search proxying to RAG.

    Searches *all* of the caller's collections (HA entities, Nextcloud files,
    system capabilities/learnings, missions, conversations, network topology,
    telemetry, etc.) — not just Nextcloud files — across both the resolved
    Jarvis user and the linked Nextcloud user, then merges the best hits.
    """
    if not q or not q.strip():
        return {"status": "SUCCESS", "answer": "No query provided.", "files": []}

    # Resolve user(s) for multi-tenancy — mirror /api/storage/stats. No fallback
    # to the admin: an unproven caller is a 401, not the owner's index.
    creds = await _resolve_identity_from_request(request)
    jarvis_user = creds.get("user") or ""
    nc_user = creds.get("nextcloud_user") or jarvis_user

    # Preserve order, de-dupe identical users.
    users = list(dict.fromkeys([jarvis_user, nc_user]))

    merged: dict[str, dict] = {}
    for user_id in users:
        try:
            resp = await get_http_client().post(
                f"{RAG_SVC}/rag/search",
                json={"query": q, "user_id": user_id, "collection_name": "all", "k": 8},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status != 200:
                continue
            data = await resp.json()
            for r in data.get("results", []):
                content = r.get("content", "")
                if not content or content in merged:
                    continue
                merged[content] = r
        except Exception as e:
            log.error(f"Search proxy failed for user {user_id}: {e}")

    results = list(merged.values())
    results.sort(key=lambda r: (r.get("score") or 0.0), reverse=True)
    results = results[:8]

    if not results:
        return {"status": "SUCCESS", "answer": "No specific context found.", "files": []}

    files = []
    for r in results:
        meta = r.get("metadata", {}) or {}
        name = (
            meta.get("friendly_name")
            or meta.get("name")
            or meta.get("entity_id")
            or meta.get("mission_id")
            or meta.get("path")
            or "Result"
        )
        path = meta.get("path") or meta.get("entity_id") or meta.get("source") or ""
        files.append({"name": str(name), "path": str(path)})

    return {
        "status": "SUCCESS",
        "answer": results[0]["content"],
        "files": files,
    }

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
            return await _proxy_json_response(resp)
    except Exception as e:
        log.error(f"Workspaces proxy failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})

async def _caller_context_or_none(request: Request, body: dict) -> dict | None:
    """The authenticated caller's identity, or None for an anonymous request.

    Anonymous requests still reach the runtime, which refuses any workspace
    that needs a user; this only decides WHO the user is.
    """
    with suppress(HTTPException):
        return await _resolve_user_context(request, dict(body))
    return None


def _with_caller_context(body: dict, context: dict | None) -> dict:
    """Replace any client-supplied user_context with the resolved caller.

    A browser-supplied user_context used to pass straight through when present,
    letting a caller act as another user (or as an admin) in the workspace
    runtime.
    """
    body = {k: v for k, v in body.items() if k != "user_context"}
    if context is not None:
        body["user_context"] = context
    return body


async def _proxy_workspace_runtime_json(method: str, path: str, request = None):
    body = await request.json() if request is not None else None
    if isinstance(body, dict):
        body = _with_caller_context(body, await _caller_context_or_none(request, body))
    resp = await get_http_client().request(
        method,
        f"{WORKSPACE_RUNTIME_SVC}{path}",
        json=body,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return await _proxy_json_response(resp)

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


@app.post("/api/workspaces/files/delete")
async def delete_workspace_file_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/files/delete", request)

@app.post("/api/workspaces/files/move")
async def move_workspace_file_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/files/move", request)


async def _proxy_workspace_runtime_authed(path: str, request: Request, timeout: aiohttp.ClientTimeout):
    """JSON proxy that ALWAYS resolves the caller (never trusts a client user_context)."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    body["user_context"] = await _resolve_user_context(request, body)
    resp = await get_http_client().post(
        f"{WORKSPACE_RUNTIME_SVC}{path}",
        json=body,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=timeout,
    )
    return await _proxy_json_response(resp)


# A first two-way sync of a large Nextcloud folder downloads everything, so it
# gets far longer than the shared client's 300 s.
_WORKSPACE_SYNC_TIMEOUT = aiohttp.ClientTimeout(total=3600, sock_connect=30)


@app.post("/api/workspaces/sync")
async def sync_workspace_proxy(request: Request):
    """Sync a workspace with its Nextcloud folder (direction: both | push | pull)."""
    return await _proxy_workspace_runtime_authed("/provider/sync/workspace", request, _WORKSPACE_SYNC_TIMEOUT)


@app.post("/api/workspaces/sync/reset")
async def reset_workspace_sync_proxy(request: Request):
    return await _proxy_workspace_runtime_authed(
        "/provider/sync/workspace/reset", request, aiohttp.ClientTimeout(total=60)
    )


@app.post("/api/workspaces/files/upload")
async def upload_workspace_files_proxy(request: Request):
    """Stream a multipart upload (many files / a folder) to the workspace runtime.

    The body is passed through untouched - never buffered or re-encoded here -
    and the caller's identity travels in a header the browser cannot set.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(status_code=415, detail="Uploads must be multipart/form-data")
    length = request.headers.get("content-length")
    if length is None:
        raise HTTPException(status_code=411, detail="Uploads must send Content-Length")
    user_context = await _resolve_user_context(request, {})
    encoded_context = base64.b64encode(json.dumps(user_context).encode("utf-8")).decode("ascii")
    resp = await get_http_client().post(
        f"{WORKSPACE_RUNTIME_SVC}/files/upload",
        data=request.stream(),
        headers={
            "X-Internal-Secret": INTERNAL_SECRET,
            "X-Workspace-User-Context": encoded_context,
            "Content-Type": content_type,
            "Content-Length": length,
        },
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=600),
    )
    return await _proxy_json_response(resp)


@app.post("/api/workspaces/files/raw")
async def read_workspace_file_raw_proxy(request: Request):
    body = await request.json()
    if isinstance(body, dict):
        body = _with_caller_context(body, await _caller_context_or_none(request, body))
    resp = await get_http_client().post(
        f"{WORKSPACE_RUNTIME_SVC}/files/raw",
        json=body,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    body_bytes = await resp.read()
    return Response(
        content=body_bytes,
        status_code=resp.status,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
    )


@app.post("/api/workspaces/files/zip")
async def zip_workspace_files_proxy(request: Request):
    body = await request.json()
    if isinstance(body, dict):
        body = _with_caller_context(body, await _caller_context_or_none(request, body))
    resp = await get_http_client().post(
        f"{WORKSPACE_RUNTIME_SVC}/files/zip",
        json=body,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    body_bytes = await resp.read()
    content_disposition = resp.headers.get("content-disposition", 'attachment; filename="artifacts.zip"')
    return Response(
        content=body_bytes,
        status_code=resp.status,
        media_type=resp.headers.get("content-type", "application/zip"),
        headers={"Content-Disposition": content_disposition},
    )

async def _api_key_is_valid(api_key: str) -> bool:
    """Ask Identity whether this exact key authenticates a real user.

    Deliberately does NOT go through ``resolve_identity``: that endpoint falls
    back to the system default user (an admin) when nothing matches, so every
    string "resolves" successfully and it cannot be used to prove a caller is
    authenticated. ``/api/internal/validate-api-key`` is the strict variant,
    shared with ``_resolve_strict_identity``.

    Fails closed: if Identity is unreachable the key cannot be confirmed, so we
    deny rather than wave the request through.
    """
    return await _resolve_strict_identity(api_key) is not None


async def _sd_request_authorized(request: Request) -> bool:
    """Gate the image/music/OCR routes on a *verified* key, not a present one.

    This used to be ``bool(api_key)`` — a presence check, so any non-empty
    string authorized the caller. Combined with the default-user fallback in
    ``resolve_identity`` that made these routes, including GPU-spending image
    generation, reachable by an anonymous caller. It now validates the key
    against Identity and fails closed when Identity is unreachable.

    Accepts, in this order: the internal secret (service-to-service), then a
    Bearer token, then ``X-API-Key``.
    """
    if request.headers.get("X-Internal-Secret") == INTERNAL_SECRET:
        return True
    api_key = request.headers.get("X-API-Key")
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header.split(" ", 1)[1]
    if not api_key or not api_key.strip():
        return False
    return await _api_key_is_valid(api_key.strip())

async def _sd_proxy_base_url() -> str:
    """Resolve the SD/image proxy base URL from Identity settings (llm_local_url).

    The alpaca SD backend is only reachable through the user-configured LLM proxy
    (Settings > AI & Compute > llm_local_url) — the legacy ALPACA_SD_URL default
    (port 8081 direct) is refused from the server. Fail loudly when unconfigured.
    """
    settings = await get_dynamic_llm_settings()
    url = (settings.get("llm_local_url") or "").strip().rstrip("/")
    if not url:
        raise RuntimeError(
            "llm_local_url not configured in Identity settings (Settings > AI & Compute)."
        )
    return url

@app.post("/api/images/generate")
async def sd_image_generate_proxy(request: Request):
    if not await _sd_request_authorized(request):
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    body = await request.json()
    try:
        base_url = await _sd_proxy_base_url()
        resp = await get_http_client().post(f"{base_url}/v1/images/generations", json=body)
        return JSONResponse(status_code=resp.status, content=await resp.json())
    except RuntimeError as exc:
        return JSONResponse(status_code=400, content={"status": "ERROR", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"status": "ERROR", "message": f"Stable Diffusion backend unreachable: {exc}"})


@app.post("/api/music/generate")
async def music_generate_proxy(request: Request):
    """Generate a short piece of music from a prompt.

    The audio backend is optional and must be configured: an unset
    ALPACA_AUDIO_URL is a clear failure, not a probe of some remembered host.
    The backend's own error text is passed through so a refused prompt reads
    as refused instead of as silence.
    """
    if not await _sd_request_authorized(request):
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    if not ALPACA_AUDIO_URL:
        return JSONResponse(
            status_code=503,
            content={
                "status": "ERROR",
                "message": "Music generation is not configured on this deployment (set ALPACA_AUDIO_URL)",
            },
        )

    body = await request.json()
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse(status_code=422, content={"status": "ERROR", "message": "A prompt is required"})

    payload = {"prompt": prompt}
    for key in ("duration_s", "temperature", "guidance_scale", "seed", "top_k"):
        if body.get(key) is not None:
            payload[key] = body[key]

    try:
        resp = await get_http_client().post(
            f"{ALPACA_AUDIO_URL}/api/music", json=payload, timeout=aiohttp.ClientTimeout(total=600.0)
        )
        result = await resp.json()
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        return JSONResponse(
            status_code=502, content={"status": "ERROR", "message": f"Audio backend unreachable: {exc}"}
        )

    if resp.status >= 400 or result.get("error"):
        return JSONResponse(
            status_code=resp.status if resp.status >= 400 else 502,
            content={"status": "ERROR", "message": result.get("error") or f"Audio backend returned {resp.status}"},
        )

    audio_b64 = result.get("audio_b64")
    if not audio_b64:
        return JSONResponse(
            status_code=502, content={"status": "ERROR", "message": "Audio backend returned no audio"}
        )
    mime = result.get("mime") or "audio/wav"
    return JSONResponse(
        content={
            "status": "SUCCESS",
            "mime": mime,
            # A data URL so the browser can play it without a temp file.
            "audio_url": f"data:{mime};base64,{audio_b64}",
        }
    )


@app.post("/api/images/edit")
async def sd_image_edit_proxy(request: Request):
    if not await _sd_request_authorized(request):
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    body = await request.json()
    base_url = resolve_service_base_url(SVC_ALPACA_SD)
    try:
        resp = await get_http_client().post(f"{base_url}/v1/images/edits", json=body)
        return JSONResponse(status_code=resp.status, content=await resp.json())
    except Exception as exc:
        return JSONResponse(status_code=502, content={"status": "ERROR", "message": f"Stable Diffusion backend unreachable: {exc}"})


@app.get("/api/ai/capabilities")
async def ai_capabilities_proxy(request: Request):
    """Expose the execution service's AI capability probe to the UI.

    Read-only, and the gateway does no capability logic of its own: the
    execution service owns the probes, because it is the service that holds the
    image/audio configuration and the record of what actually worked.
    """
    if not await _sd_request_authorized(request):
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    try:
        resp = await get_http_client().get(
            f"{EXECUTION_SVC}/execute/ai_capabilities",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=20.0),
        )
        return JSONResponse(status_code=resp.status, content=await resp.json())
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"status": "ERROR", "message": f"Capability check unavailable: {exc}"},
        )


@app.get("/api/images/models")
async def sd_image_models_proxy(request: Request):
    if not await _sd_request_authorized(request):
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    try:
        base_url = await _sd_proxy_base_url()
        resp = await get_http_client().get(f"{base_url}/v1/images/models")
        return JSONResponse(status_code=resp.status, content=await resp.json())
    except RuntimeError as exc:
        return JSONResponse(status_code=400, content={"status": "ERROR", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"status": "ERROR", "message": f"Stable Diffusion backend unreachable: {exc}"})

@app.post("/api/workspaces/{workspace_id}/images/edit")
async def workspace_image_edit_proxy(workspace_id: str, request: Request):
    """Workspace-scoped AI image edit: reads <image_path> in the workspace, runs the
    configured image edit model via the LLM proxy, and saves the result as
    <output_path> (default <stem>_edited.<ext>) back into the same workspace.

    Delegates to the execution service /execute/image_edit handler (which owns
    the workspace resolution, proxy multipart call, and binary save) so the IDE
    and Raven share one code path. Long timeout: CPU-offloaded SD takes minutes.

    A face swap is a two-image edit: image_path is the photo to keep and
    face_image_path (alias donor_path) supplies the donor face. The execution
    handler rejects a swap prompt with no donor rather than editing the wrong
    photo.
    """
    if not await _sd_request_authorized(request):
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    body = await request.json()
    creds = await _resolve_identity_from_request(request)
    if not creds:
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    payload = {
        "workspace_id": workspace_id,
        "image_path": body.get("image_path") or body.get("path"),
        "face_image_path": body.get("face_image_path") or body.get("donor_path"),
        "prompt": body.get("prompt"),
        "model": body.get("model"),
        "output_path": body.get("output_path"),
        "size": body.get("size"),
        "user_context": {
            "user": creds.get("user", ""),
            "is_admin": creds.get("is_admin", False),
            "api_key": creds.get("api_key"),
            "ha_url": creds.get("ha_url"),
            "ha_token": creds.get("ha_token"),
            "nextcloud_url": creds.get("nextcloud_url"),
            "nextcloud_user": creds.get("nextcloud_user"),
            "nextcloud_pass": creds.get("nextcloud_pass"),
            "github_token": creds.get("github_token"),
            "gitlab_token": creds.get("gitlab_token"),
            "git_token": creds.get("git_token"),
        },
    }
    if not payload["image_path"] or not payload["prompt"]:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": "image_path (or path) and prompt are required."},
        )
    try:
        async with shared_http_client() as client:
            resp = await client.post(
                f"{EXECUTION_SVC}/execute/image_edit",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=620.0),
            )
            try:
                data = await resp.json()
            except Exception:
                data = {"status": "ERROR", "message": (await resp.text())[:500]}
            return JSONResponse(status_code=resp.status, content=data)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"status": "ERROR", "message": f"Image edit failed: {exc}"})


@app.post("/api/workspaces/{workspace_id}/ocr")
async def workspace_ocr_proxy(workspace_id: str, request: Request):
    """Workspace-scoped OCR: reads <image_path> in the workspace and extracts
    the visible text with the configured vision model (vision_ocr_model).

    Delegates to the execution service /execute/ocr handler (same code path
    Raven uses) so the IDE and missions share one implementation. Long
    timeout: vision LLM inference on CPU takes up to a minute or two.
    """
    if not await _sd_request_authorized(request):
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    body = await request.json()
    creds = await _resolve_identity_from_request(request)
    if not creds:
        return JSONResponse(status_code=401, content={"status": "ERROR", "message": "Unauthorized"})
    payload = {
        "workspace_id": workspace_id,
        "image_path": body.get("image_path") or body.get("path"),
        "task": body.get("task") or "general",
        "model": body.get("model"),
        "user_context": {
            "user": creds.get("user", ""),
            "is_admin": creds.get("is_admin", False),
            "api_key": creds.get("api_key"),
            "ha_url": creds.get("ha_url"),
            "ha_token": creds.get("ha_token"),
            "nextcloud_url": creds.get("nextcloud_url"),
            "nextcloud_user": creds.get("nextcloud_user"),
            "nextcloud_pass": creds.get("nextcloud_pass"),
            "github_token": creds.get("github_token"),
            "gitlab_token": creds.get("gitlab_token"),
            "git_token": creds.get("git_token"),
        },
    }
    if not payload["image_path"]:
        return JSONResponse(
            status_code=400,
            content={"status": "ERROR", "message": "image_path (or path) is required."},
        )
    try:
        async with shared_http_client() as client:
            resp = await client.post(
                f"{EXECUTION_SVC}/execute/ocr",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=620.0),
            )
            try:
                data = await resp.json()
            except Exception:
                data = {"status": "ERROR", "message": (await resp.text())[:500]}
            return JSONResponse(status_code=resp.status, content=data)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"status": "ERROR", "message": f"OCR failed: {exc}"})

@app.post("/api/workspaces/git/status")
async def git_status_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/status", request)

@app.post("/api/workspaces/git/pull")
async def git_pull_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/pull", request)

@app.post("/api/workspaces/git/revert")
async def git_revert_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/revert", request)

@app.post("/api/workspaces/git/diff")
async def git_diff_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/diff", request)

@app.post("/api/workspaces/git/add")
async def git_add_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/add", request)

@app.post("/api/workspaces/git/commit")
async def git_commit_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/commit", request)

@app.post("/api/workspaces/git/push")
async def git_push_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/push", request)

@app.post("/api/workspaces/git/log")
async def git_log_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/log", request)

@app.post("/api/workspaces/git/fetch")
async def git_fetch_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/fetch", request)


@app.post("/api/workspaces/git/branches")
async def git_branches_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/branches", request)


@app.post("/api/workspaces/git/checkout")
async def git_checkout_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/checkout", request)


@app.post("/api/workspaces/git/branch/create")
async def git_branch_create_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/branch/create", request)

@app.get("/api/workspaces/{workspace_id}/raven/missions")
async def get_workspace_raven_missions_proxy(workspace_id: str, request: Request, limit: int = 50):
    resp = await get_http_client().get(
        f"{IDENTITY_SVC}/api/raven/missions?workspace_id={workspace_id}&limit={limit}",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return await _proxy_json_response(resp)

@app.post("/api/storage/mirror")
async def mirror_storage(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("nextcloud_url") or not creds.get("nextcloud_user") or not creds.get("nextcloud_pass"):
        raise HTTPException(status_code=400, detail="NextCloud credentials not configured for this user.")
    body = await request.json()
    payload = {
        "provider": {
            "kind": "nextcloud",
            "settings": {
                "url": creds["nextcloud_url"],
                "username": creds["nextcloud_user"],
                "password": creds["nextcloud_pass"],
            },
        },
        "remote_path": body.get("remote_path"),
        "local_path": body.get("local_path"),
        "excludes": body.get("excludes", []),
    }
    resp = await get_http_client().post(
        f"{STORAGE_SVC}/providers/mirror",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return await _proxy_json_response(resp)

@app.post("/api/workspaces/tests/pytest")
async def pytest_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/tests/pytest", request)

@app.post("/api/workspaces/workflow/write-sync-commit")
async def write_sync_commit_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/workflow/write-sync-commit", request)

@app.post("/api/workspaces/ports/expose")
@app.post("/ports/expose")
async def expose_workspace_port_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/ports/expose", request)

@app.get("/api/workspaces/ports/{workspace_id}")
@app.get("/ports/list/{workspace_id}")
async def list_workspace_ports_proxy(workspace_id: str, request: Request):
    return await _proxy_workspace_runtime_json("GET", f"/ports/list/{workspace_id}", request)

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
    return await _proxy_json_response(resp)

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
        "recursive": body.recursive,
        "force": body.force
    }

    resp = await get_http_client().post(
        f"{STORAGE_SVC}/index/full",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    return await _proxy_json_response(resp)

@app.get("/api/storage/stats")
async def get_storage_stats(request: Request):
    # Resolve user and nextcloud IDs from request. An unproven caller is a 401:
    # this used to fall back to the first (admin) user's stats, which published
    # the whole family's document counts to anyone who asked.
    creds_data = await _resolve_identity_from_request(request)
    jarvis_user = creds_data.get("user") or ""
    nc_user = creds_data.get("nextcloud_user")

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
        content = await resp1.json()
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
            content = merge_stats(content, await resp2.json())
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
        content = await resp.json()
    except Exception as e:
        log.error(f"Failed to parse collection docs JSON: {e} | Body: {(await resp.text())[:200]}")
        content = {"status": "ERROR", "message": "Upstream RAG service returned non-JSON response", "detail": str(e)}

    return JSONResponse(status_code=resp.status, content=content)


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
        return await _proxy_json_response(resp)


@app.get("/api/storage/learning")
async def get_raven_learnings(request: Request, limit: int = 200, sort: str = "recent"):
    """List Raven lessons (system_learnings) with reuse stats for the UI."""
    try:
        creds_data = await _resolve_identity_from_request(request)
        user_id = creds_data.get("user") or "default"
    except Exception:
        first_user = await resolve_first_user()
        user_id = first_user.get("user") or "default"

    resp = await get_http_client().get(
        f"{RAG_SVC}/rag/learning?user_id={user_id}&limit={limit}&sort={sort}",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return await _proxy_json_response(resp)


@app.patch("/api/storage/learning/{doc_id}")
async def edit_raven_learning(doc_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    resp = await get_http_client().patch(
        f"{RAG_SVC}/rag/learning/{doc_id}",
        json=body,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return await _proxy_json_response(resp)


@app.delete("/api/storage/learning/{doc_id}")
async def delete_raven_learning(doc_id: str):
    resp = await get_http_client().delete(
        f"{RAG_SVC}/rag/learning/{doc_id}",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return await _proxy_json_response(resp)


@app.patch("/api/storage/learning/{doc_id}/applied")
async def mark_raven_learning_applied(doc_id: str):
    """Record that a mission actually APPLIED this lesson (honest reuse signal)."""
    resp = await get_http_client().patch(
        f"{RAG_SVC}/rag/learning/{doc_id}/applied",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    return await _proxy_json_response(resp)


@app.post("/api/admin/tests/smoke")
async def proxy_smoke_test(request: Request):
    client = get_http_client()
    resp = await client.post(
        f"{WORKSPACE_RUNTIME_SVC}/api/admin/tests/smoke",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=aiohttp.ClientTimeout(total=65.0)
    )
    return await _proxy_json_response(resp)


@app.post("/api/admin/tests/unit")
async def proxy_unit_tests(request: Request):
    client = get_http_client()
    resp = await client.post(
        f"{WORKSPACE_RUNTIME_SVC}/api/admin/tests/unit",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=aiohttp.ClientTimeout(total=130.0)
    )
    return await _proxy_json_response(resp)


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
            timeout=aiohttp.ClientTimeout(total=120.0),
        )
        return await _proxy_json_response(resp)
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
            if k in ["raven_suspended", "raven_scan_interval", "raven_error_threshold", "raven_max_total_seconds", "system_default_tts_voice", "system_default_tts_engine", "coding_model", "ollama_coding_model", "assistant_model", "librarian_model", "telemetry_model"]:
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
        return await _proxy_json_response(resp)


@app.post("/execute/tts/download")
async def proxy_tts_voice_download(request: Request):
    """Proxy the Kokoro TTS voice-model download to the execution service.

    The UI (RavenOpsPanel 'Download models') calls POST /execute/tts/download
    through the gateway; the execution service downloads the Kokoro ONNX
    voice assets from GitHub if they are missing.
    """
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        body = await request.json()
    except Exception:
        body = {}

    voice_type = (body or {}).get("voice_type") or request.query_params.get("voice_type") or "kokoro-v1.0"

    async with borrow_http_client() as client:
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/tts/download?voice_type={voice_type}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return await _proxy_json_response(resp)


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
        return await _proxy_json_response(resp)

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
        if resp.status == 404:
            raise HTTPException(status_code=404, detail=f"Service {service_name} not found")
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=await resp.text())
        return await _proxy_json_response(resp)

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
        return await _proxy_json_response(resp)

@app.post("/api/admin/services/{service_name}/pull")
async def pull_service_image(service_name: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with borrow_http_client() as client:
        resp = await client.post(
            f"{CONTROL_PLANE_URL}/api/containers/{service_name}/pull",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return await _proxy_json_response(resp)

@app.get("/api/admin/services/{service_name}/pull/status")
async def get_pull_status(service_name: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with borrow_http_client() as client:
        resp = await client.get(
            f"{CONTROL_PLANE_URL}/api/containers/{service_name}/pull/status",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return await _proxy_json_response(resp)

@app.post("/api/admin/services/{service_name}/pull-and-restart")
async def pull_and_restart_service(service_name: str, request: Request):
    """Pull latest image and restart the container with it (phone-update flow)."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with borrow_http_client() as client:
        resp = await client.post(
            f"{CONTROL_PLANE_URL}/api/containers/{service_name}/pull-and-restart",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=120.0),
        )
        return await _proxy_json_response(resp)

@app.get("/api/admin/services/health")
async def get_system_health(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with borrow_http_client() as client:
        resp = await client.get(
            f"{CONTROL_PLANE_URL}/api/health",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return await _proxy_json_response(resp)

@app.get("/api/admin/services/updates")
async def check_service_updates(request: Request):
    """Check all sharedllm services for available image updates without pulling."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with borrow_http_client() as client:
        try:
            resp = await client.get(
                f"{CONTROL_PLANE_URL}/api/admin/services/updates",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=60.0),  # registry checks can be slow
            )
        except (TimeoutError, aiohttp.ClientError) as e:
            log.error(f"Control plane unreachable for service updates: {e}")
            return JSONResponse(
                status_code=504,
                content={"detail": "Control plane unreachable while checking for updates", "updates_available": 0, "services": []}
            )
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=await resp.text())

        data = await resp.json()
        updates_available = data.get("updates_available", 0)
        if updates_available > 0:
            services = [s.get("service", "unknown").replace("sharedllm_", "") for s in data.get("services", []) if s.get("has_update")]
            services_str = ", ".join(services)
            await emit_log(
                level="WARNING",
                message=f"Docker image updates available: {services_str}",
                context={"updates": services}
            )

        return JSONResponse(status_code=resp.status, content=data)

@app.get("/api/intercom/config")
async def get_intercom_config(request: Request):
    """Proxy intercom config to Identity service."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with borrow_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/intercom/config",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status != 200:
            return JSONResponse(status_code=resp.status, content={"detail": await resp.text()})
        return JSONResponse(status_code=200, content=await resp.json())


@app.api_route("/api/intercom/sessions", methods=["GET", "POST"])
async def proxy_intercom_sessions(request: Request):
    """Proxy intercom session list/create to Identity service."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    body = None
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = None
    async with borrow_http_client() as client:
        resp = await client.request(
            request.method,
            f"{IDENTITY_SVC}/api/intercom/sessions",
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.delete("/api/intercom/sessions/{session_id}")
async def proxy_delete_intercom_session(session_id: str, request: Request):
    """Proxy intercom session deletion to Identity service."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    async with borrow_http_client() as client:
        resp = await client.delete(
            f"{IDENTITY_SVC}/api/intercom/sessions/{session_id}",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        return await _proxy_json_response(resp)


@app.post("/api/intercom/announce")
async def proxy_intercom_announce(request: Request):
    """Proxy one-way announcement to Execution (real HA dispatch)."""
    body = await request.json()
    return await _proxy_execution_with_identity(request, "/execute/intercom/announce", body)


@app.post("/api/intercom/broadcast")
async def proxy_intercom_broadcast(request: Request):
    """Proxy broadcast to Execution (real HA dispatch, room→speaker resolution)."""
    body = await request.json()
    return await _proxy_execution_with_identity(request, "/execute/intercom/broadcast", body)


@app.api_route("/api/intercom/room-speakers", methods=["GET", "PUT"])
async def proxy_room_speakers(request: Request):
    """Proxy room→speaker map CRUD to Identity."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    body = None
    if request.method == "PUT":
        try:
            body = await request.json()
        except Exception:
            body = None
    async with borrow_http_client() as client:
        resp = await client.request(
            request.method,
            f"{IDENTITY_SVC}/api/intercom/room-speakers",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


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
        if resp.status != 200:
            return JSONResponse(status_code=resp.status, content={"detail": await resp.text()})
        return JSONResponse(status_code=200, content=await resp.json())

@app.get("/api/models")
async def list_models(request: Request):
    """List available models from the active provider."""
    settings = await get_llm_settings()
    provider = await get_provider(settings)

    # If Ollama, we can hit its /api/tags endpoint
    if isinstance(provider, OllamaProvider):
        async with borrow_http_client() as client:
            resp = await client.get(f"{provider.base_url}/api/tags")
            if resp.status == 200:
                tags = (await resp.json()).get("models", [])
                return {"status": "SUCCESS", "models": [m["name"] for m in tags]}

    # For OpenRouter or others, we might return the config models
    return {
        "status": "SUCCESS",
        "models": [
            settings.get("assistant_model"),
            settings.get("coding_model"),
            settings.get("librarian_model"),
            settings.get("telemetry_model"),
        ],
        "note": "Active config models returned for this provider."
    }







@app.post("/api/models/switch")
async def switch_model(request: Request):
    """Switch to a different model by loading it (unloads current if needed)."""
    body = await request.json()
    model = body.get("model") if body else None
    if not model:
        return JSONResponse(status_code=400, content={"error": "model is required"})

    settings = await get_llm_settings()
    provider = await get_provider(settings)

    if isinstance(provider, OllamaProvider):
        try:
            async with borrow_http_client() as client:
                # Ollama auto-unloads old model when a new one is requested.
                # We trigger the switch by making a minimal /api/chat call.
                chat_resp = await client.post(f"{provider.base_url}/api/chat", json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False
                })

                if chat_resp.status == 200:
                    return {"status": "loaded", "model": model}
                else:
                    return JSONResponse(
                        status_code=chat_resp.status,
                        content={"error": await chat_resp.text()}
                    )
        except Exception as e:
            log.error(f"Error switching model: {e}")
            return JSONResponse(status_code=500, content={"error": str(e)})

    return JSONResponse(status_code=500, content={"error": "Model switching only supported for Ollama provider"})

@app.post("/api/models/unload")
async def unload_model(request: Request):
    """Unload a model from Ollama."""
    body = await request.json()
    model = body.get("model") if body else None

    settings = await get_llm_settings()
    provider = await get_provider(settings)

    if isinstance(provider, OllamaProvider):
        try:
            async with borrow_http_client() as client:
                # If model not specified, get whatever is loaded
                if not model:
                    ps_resp = await client.get(f"{provider.base_url}/api/ps")
                    if ps_resp.status == 200:
                        ps_data = await ps_resp.json()
                        loaded = ps_data.get("models", [])
                        if loaded:
                            model = loaded[0].get("model")

                if not model:
                    return {"status": "success", "message": "No model loaded to unload"}

                # Ollama doesn't have a dedicated unload endpoint.
                # We trigger unload by sending a request with keep_alive=0.
                await client.post(f"{provider.base_url}/api/generate", json={
                    "model": model,
                    "prompt": "",
                    "keep_alive": 0
                })

                # Verify the model was actually unloaded
                ps_resp = await client.get(f"{provider.base_url}/api/ps")
                if ps_resp.status == 200:
                    ps_data = await ps_resp.json()
                    remaining = [m["model"] for m in ps_data.get("models", [])]
                    if model not in remaining:
                        return {"status": "unloaded", "model": model}

                return {"status": "success", "message": f"Model {model} may still be cached"}
        except Exception as e:
            log.error(f"Error unloading model: {e}")
            return JSONResponse(status_code=500, content={"error": str(e)})

    return JSONResponse(status_code=500, content={"error": "Model unloading only supported for Ollama provider"})

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
                if resp.status == 200:
                    tags = (await resp.json()).get("models", [])
                    model_names = [m["name"] for m in tags]
        except Exception as e:
            log.error(f"Error querying Ollama models for OpenAI list: {e}")

    if not model_names:
        # Fallback to configured models
        for model_key in ["assistant_model", "coding_model", "librarian_model", "telemetry_model"]:
            model_name = settings.get(model_key)
            if model_name and model_name not in model_names:
                model_names.append(model_name)

    # Map to OpenAI list format
    openai_models = []
    for alias in ["jarvis", "assistant"]:
        openai_models.append({
            "id": alias,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "system"
        })
    for m in model_names:
        if m not in ("jarvis", "assistant"):
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

        async with shared_http_client() as client:
            resp = await client.post(
                f"{ollama_url}/api/embed",
                json={"model": model, "input": inputs}
                , timeout=aiohttp.ClientTimeout(total=60.0),
            )
            if resp.status != 200:
                return await _proxy_json_response(resp)

            data = await resp.json()
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
        missions = await resp.json()
        target = next((m for m in missions if m["id"] == id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Mission not found")

        system_prompt = ""
        if target["mission_type"] == "admin_fix":
            protocols = await fetch_autonomous_protocols()
            system_prompt = f"{protocols}\n\n[ADMIN ROZ ACTIVE]\nYou are the Raven Sentinel operating in the Restricted Operating Zone. Your mission is to fix backend/frontend components. You have elevated access. Execute the following mission:\n{target['proposed_mission']}"
        else:
            # Build the mission system prompt from the prompts/ directory
            # (raven_autonomous_protocol.md + global autonomous protocols) rather
            # than hard-coding discipline here. The protocol already enforces
            # the greenfield coding workflow: create the workspace, `gh repo
            # create` to initialize + wire git, write files directly, validate,
            # then commit/push via GitOperationRequest.
            system_prompt = await _build_raven_system_prompt(target["proposed_mission"])

        # Push job
        assert job_queue is not None, "Job queue not initialized"
        mission_model = target["coding_model"]
        if target["mission_type"] == "admin_fix":
            # System-triggered repair missions always run on the coding model
            # currently selected in the config DB — never a stale frozen value.
            mission_model = await resolve_current_coding_model()
        await job_queue.enqueue_job("raven_admin", {
            "query": target["proposed_mission"],
            "model": mission_model,
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
    slug: str | None = None
    priority: int = 1
    coding_model: str | None = None
    workspace_id: str | None = None
    depends_on_mission_id: int | None = None
    next_mission_query: str | None = None


async def _fetch_relevant_lessons(query: str, client=None, limit: int = 5) -> str:
    """Retrieve the top-K lessons RELEVANT to THIS mission (smart, not a dumb dump).

    Uses the RAG service's hybrid semantic search over the ``system_learnings``
    collection keyed on the mission text, so as the lesson store grows into the
    hundreds only the few lessons actually pertinent to the current task are
    surfaced. Results are compacted to a one-line transferable ``rule`` each
    (plus outcome/confidence for credibility) and count-capped, so the prompt is
    never bloated with irrelevant history. Fails safe: any error returns '' and
    the mission prompt is unchanged.
    """
    # NOTE: RAG /rag/search has a one-time per-process warmup (embedding model /
    # sqlite-vec first query) that can take ~25s; subsequent calls are <100ms.
    # The timeout must clear that cold start, and we route through retry_http_request
    # so a stale/closed pooled connection (which surfaces as an empty-str ClientError)
    # is transparently recreated instead of silently dropping the injection.
    try:
        async def _do_search() -> aiohttp.ClientResponse:
            cl = client or get_http_client()
            return await cl.post(
                f"{RAG_SVC}/rag/search",
                json={
                    "collection_name": "system_learnings",
                    "query": query,
                    "user_id": "default",
                    "k": limit,
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET, "Authorization": f"Bearer {INTERNAL_SECRET}"},
                timeout=aiohttp.ClientTimeout(total=120.0),
            )

        if client is not None:
            resp = await _do_search()
        else:
            resp = await retry_http_request(_do_search, "RAG lesson retrieval", max_retries=2, base_delay=0.2)
        if resp.status != 200:
            return ""
        data = await resp.json()
        hits = data.get("results") or []
        if not hits:
            return ""
        lines: list[str] = []
        for h in hits[:limit]:
            rule = (h.get("rule") or h.get("content") or "").strip()
            if not rule:
                continue
            outcome = h.get("outcome") or ""
            conf = h.get("confidence")
            tag = ""
            if outcome:
                tag = f" [{outcome}{', conf ' + format(conf, '.2f') if isinstance(conf, (int, float)) else ''}]"
            # one compact line — the transferable takeaway only, not the narrative
            rule_one = rule.split("\n")[0].strip()
            if len(rule_one) > 260:
                rule_one = rule_one[:257] + "..."
            lines.append(f"- {rule_one}{tag}")
        if not lines:
            return ""
        log.info(f"[Raven] injected {len(lines)} relevant lesson(s) into mission prompt")
        return "\n".join(lines)
    except Exception as e:  # never block mission start on a learning lookup failure
        log.warning(f"[Raven] relevant-lesson retrieval failed (skipping injection): {e}")
        return ""


async def _warm_rag_lesson_cache() -> None:
    """Best-effort background warmup of RAG's embedding model.

    RAG's first /rag/search per process is a one-time ~25s cold start (embedding
    model + sqlite-vec first query); later calls are <100ms. If the first Raven
    mission after a gateway/rag restart hits that cold start inside lesson
    injection, it can blow past even a generous timeout and the learning loop
    silently drops. Warming it at gateway startup (before any mission runs)
    keeps the injection fast and reliable. Fails safe — never raises.
    """
    try:
        await asyncio.sleep(2)  # let startup settle before hammering RAG
        async with aiohttp.ClientSession() as c:
            await c.post(
                f"{RAG_SVC}/rag/search",
                json={"collection_name": "system_learnings", "query": "warmup", "user_id": "default", "k": 1},
                headers={"X-Internal-Secret": INTERNAL_SECRET, "Authorization": f"Bearer {INTERNAL_SECRET}"},
                timeout=aiohttp.ClientTimeout(total=120.0),
            )
        log.info("[Raven] RAG lesson cache warmed (first mission injection will be fast)")
    except Exception as e:  # non-fatal
        log.warning(f"[Raven] RAG warmup failed (non-fatal): {e}")


async def _build_raven_system_prompt(query: str) -> str:
    """Build the single, self-contained system prompt used for Raven missions.

    The prompt is ONE portable document: the Raven autonomous protocol (which
    already embeds the workspace tool-call format, language-aware quality gates,
    and the execution loop) plus the global autonomous protocols and the mission
    itself. It depends on no test-harness scaffolding or runtime-injected context,
    so the same prompt can be copied and run standalone against any capable model.

    Relevant PAST LESSONS are injected (smart, relevance-ranked, capped) so Raven
    starts each mission informed by what worked/failed before — closing the
    learning loop. The model no longer has to remember to call RavenRecallRequest.
    """
    async with borrow_http_client() as client:
        protocol = await load_prompt(client, PROMPT_RAVEN_AUTONOMOUS_PROTOCOL)
        protocols = await fetch_autonomous_protocols()
        lessons_block = await _fetch_relevant_lessons(query)
    lesson_section = (
        f"[Raven Lessons — learned from PAST missions, most relevant to THIS task]\n"
        f"{lessons_block}\n\n"
        if lessons_block
        else ""
    )
    tts_section = ""
    if any(token in (query or "").lower() for token in TTS_SIGNALS):
        tts_section = (
            "\n\n[TTS & Narration] This mission requires AUDIO SYNTHESIS (text-to-speech). "
            "Do NOT stop after preparing the text, and do NOT hand-roll audio yourself. "
            "Use the `TTSRequest` tool for every piece of spoken text. Payload fields: "
            "`text` (the speech text), `text_file` (a workspace file whose content "
            "becomes the speech text — PREFERRED for anything longer than a short "
            "paragraph, since it keeps file contents OUT of your context window; it "
            "also accepts PDF/EPUB/DOCX/HTML source documents — their text layer is "
            "extracted automatically), `voice` "
            "(optional; omit to use the default Kokoro voice), `file_path` (output audio "
            "filename — use a `.wav` extension, e.g. `narration.wav`, since the engine "
            "produces WAV audio), `workspace_id` (your workspace). Example:\n"
            '  {"@type": "TTSRequest", "text_file": "part_00.txt", '
            '"file_path": "narration_00.wav", "workspace_id": "<your workspace id>"}\n'
            "The audio is automatically decoded and saved into your workspace as a binary "
            "file, and the result returns the saved path. The engine automatically splits "
            "long text at sentence boundaries with natural pauses, so ONE `TTSRequest` per "
            "chapter/section is fine — you do NOT need to split text yourself.\n"
            "The engine reads Bible references (e.g. `John 3:16` -> `John chapter 3, "
            "verse 16`), years, eras, and small numbers naturally, so leave them exactly "
            "as they appear in the source. You may also use SSMD markup for explicit "
            "pause control in the text: `...p` (paragraph pause), `...s` (sentence "
            "pause), `...c` (clause/comma pause), `...500ms` (custom duration), "
            "say-as `[123]{as=\"cardinal\"}` annotations, and voice-bound "
            "`<div voice=\"...\">` blocks — the engine renders these verbatim.\n"
            "AUDIOBOOK PROTOCOL (learned from the Docket-TTS audiobook pipeline): (1) "
            "Prepare the narration text as one clean file PER chapter/section "
            "(e.g. `narration_chapter_01.txt`, ...) using shell `cat`/`sed`/a write tool; "
            "strip page numbers, running headers/footers, table-of-contents and index "
            "material; keep each heading, title, and every list item on its OWN line "
            "(e.g. `Week 2: Scripture`, `Day 0: Introduction`, `Chapter One.`, or each "
            "numbered `1. ...`/`1) ...` line). The TTS engine automatically adds a "
            "slightly longer beat after (a) headings/titles, (b) each list item, and "
            "(c) scripture references, so do NOT invent your own pause punctuation or "
            "write a script to inject silences — just keep the structure on separate "
            "lines exactly as in the source. (2) DO NOT write a Python/JS script "
            "to normalize pronunciation and DO NOT expand scripture references or years "
            "yourself — the TTS engine already reads Bible verses correctly and "
            "years/dates correctly (e.g. `1995` as `nineteen ninety-five`, `1400 BC` as "
            "`fourteen hundred "
            "B.C.`). Leave verses and years exactly as they appear in the source. (3) DO "
            "NOT read those text files into context — "
            "use `text_file` to point at them. If your workspace holds the source "
            "material only as a PDF/EPUB/DOCX (e.g. `Week 2 - Scripture.pdf`), read it "
            "once with WorkspaceFileReadRequest to see its embedded text, write the "
            "per-chapter `.txt` narration files from it, then synthesize those. (4) In a "
            "SINGLE turn, emit a JSON ARRAY of "
            "TTSRequest calls, one per chapter, each with its own `text_file` and a "
            "distinct `file_path` (e.g. `chapter_01.wav` ... `chapter_08.wav`). For a "
            "calm, realistic audiobook narration prefer a warm natural voice such as "
            "`am_michael` and pass it as `voice` on every call. (5) After "
            "the calls return, verify every .wav exists and is non-empty, then assemble "
            "the complete audiobook: write a concat list and run ffmpeg — "
            "`printf \"file '%s'\\n\" chapter_*.wav > concat.txt && "
            "ffmpeg -f concat -safe 0 -i concat.txt -c:a libmp3lame -b:a 128k "
             "audiobook.mp3` — and report the final file. Never re-read or re-narrate a "
             "file you have already processed — reading file contents into context "
             "exhausts your window and causes failures. (6) To REBUILD an existing "
             "audiobook with the current engine (e.g. after a TTS normalization or pause "
             "improvement), use the `audiobookregeneraterequest` tool in ONE call: pass "
             "`workspace_id` plus `text_files` (the per-chapter txt files OR the "
             "PDF/EPUB/DOCX source documents, in order; documents are extracted to text "
             "before synthesis) and "
             "optional `output_mp3` + `voice`. It re-synthesizes every chapter to WAV and "
             "concatenates them into the MP3 automatically — do NOT hand-re-run chapter-"
             "by-chapter TTSRequest calls for a full rebuild."
             "\n[VERIFY — narrated output feedback loop] After synthesizing and "
            "concatenating, VERIFY the narration actually read verses, years and numbers "
            "correctly by transcribing a sample of the audio back to text. Use the "
            "`STTRequest` tool (workspace id + `file_path`, e.g. "
            "`{\"@type\": \"STTRequest\", \"file_path\": \"chapter_01.wav\", "
            "\"workspace_id\": \"<your workspace id>\"}`) — it runs Whisper and returns the "
            "transcript. Cut a short clip with `ffmpeg -y -v error -t 30 -i chapter_01.wav "
            "clip.wav` if a whole chapter is long. Check the transcript against the "
            "expected spoken forms: e.g. source `John 3:16` should come back as "
            "`John chapter 3, verse 16`, `1995` as `nineteen ninety-five`, `1400 BC` as "
            "`fourteen hundred B.C.`, `66 books` as `sixty-six books`. Whisper prints "
            "numbers as numerals (e.g. `verses 16 through 17`) — that is fine, the "
            "wording is what matters. If verses/years are NOT read correctly, stop and "
            "report it (the engine normalization may have a gap) rather than shipping the "
            "audiobook."
        )
    return (
        f"{protocol}\n\n{protocols}\n\n"
        f"{lesson_section}"
        f"{tts_section}"
        f"[Raven Mission]\n{query}\n\n"
        f"Execute the mission above using the tool-call format defined in your protocol. "
        f"Every turn must be tool calls only (never prose): emit a JSON ARRAY of tool-call "
        f"objects when the next steps are a known, order-dependent sequence (preferred for "
        f"speed), or a single tool-call JSON object when the next action needs a result you "
        f"must read first. Drive the mission to completion."
    )


async def _enqueue_user_mission(
    *,
    query: str,
    system: str,
    creds: dict,
    coding_model: str | None = None,
    workspace_id: str | None = None,
    slug: str | None = None,
    priority: int = 1,
    depends_on_mission_id: int | None = None,
    next_mission_query: str | None = None,
    job_queue_override: "InferenceJobQueue | None" = None,
) -> dict:
    """Create a Raven user mission in Identity and enqueue it for the Raven worker.

    Shared by the ``/api/raven/missions`` HTTP endpoint and the chat handler's
    autonomous routing, so that any chat request recognized as a Raven mission
    shows up in the Raven queue. Returns the mission record (status=queued).

    ``job_queue_override`` lets the background worker (which runs on its own
    event loop) inject its own loop-bound InferenceJobQueue instead of the
    module-global one owned by the API loop.

    The mission runs with NO pre-assigned workspace: Raven creates its own
    dedicated workspace at the start of the mission (via the WorkspaceCreateRequest
    tool) and operates inside it. The gateway only supplies the means (the tool);
    Raven performs the creation.
    """
    settings = await get_llm_settings()
    target_model = coding_model or settings.get("coding_model") or settings.get("ollama_coding_model")
    if not target_model:
        raise RuntimeError("No coding model configured. Mission cannot be dispatched.")

    # Identity returns the caller's numeric id as "id"; older payloads used
    # "user_id". Accept both so a mission is never persisted ownerless.
    owner_user = creds.get("user_id") or creds.get("id") or creds.get("user")

    mission_payload = {
        "slug": slug,
        "mission_type": "user_task",
        "priority": priority,
        "proposed_mission": query,
        "coding_model": target_model,
        "user_id": owner_user,
        "workspace_id": workspace_id,
        "depends_on_mission_id": depends_on_mission_id,
        "next_mission_query": next_mission_query,
    }

    async with borrow_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/raven/missions",
            json=mission_payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=await resp.text())

        mission_data = await resp.json()
        mission_id = mission_data["id"]

        if depends_on_mission_id:
            # Chained mission: wait until the dependency completes. The worker
            # will enqueue + mark queued in its completion hook.
            mission_data["status"] = "pending"
            return mission_data

        queue = job_queue_override or job_queue
        assert queue is not None, "Job queue not initialized"
        await queue.enqueue_job(creds.get("user") or owner_user or "raven_user", {
            "query": query,
            "model": target_model,
            "system": system,
            "stream": False,
            "creds": creds,
            "_mission_id": mission_id,
            "workspace_id": workspace_id,
            "next_mission_query": next_mission_query,
        })

        await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
            json={"status": "queued"},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        mission_data["status"] = "queued"
        return mission_data


@app.post("/api/raven/missions")
async def create_user_mission(body: UserMissionRequest, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        settings = await get_llm_settings()
    except Exception:
        settings = {}
    coding_model = body.coding_model or settings.get("coding_model") or settings.get("ollama_coding_model")
    if not coding_model:
        raise HTTPException(status_code=400, detail="No coding model configured. Mission cannot be dispatched.")

    system_prompt = await _build_raven_system_prompt(body.query)
    mission_data = await _enqueue_user_mission(
        query=body.query,
        system=system_prompt,
        creds=creds,
        coding_model=coding_model,
        workspace_id=body.workspace_id,
        slug=body.slug,
        priority=body.priority,
        depends_on_mission_id=body.depends_on_mission_id,
        next_mission_query=body.next_mission_query,
    )
    return {
        "status": "SUCCESS",
        "mission": mission_data,
        "message": (
            f"Raven mission #{mission_data['id']} dispatched. You will be notified when it completes. "
            f"Track its progress in JarvisLab > Missions or at /api/raven/missions/{mission_data['id']}. "
            "Results are written into the mission's shared workspace (Workspaces page, "
            "path users/default/raven-<topic>) for you to open, inspect, and download."
        ),
    }

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
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail="Mission not found")
        mission = await resp.json()
        # Reading a mission is an owner-scoped action, same as mutating one.
        _ensure_mission_owner(creds, mission)
        return mission

def _ensure_mission_owner(creds: dict, mission_data: dict) -> None:
    """403 unless the caller owns the mission or is an admin.

    Missions with user_id=None are system/shared missions that any
    authenticated caller may act on (legacy behavior).
    """
    is_admin = bool(creds.get("is_admin"))
    user_id = creds.get("id")
    mission_user_id = mission_data.get("user_id")
    if not is_admin and mission_user_id is not None and mission_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You are not the owner of this mission.")

@app.post("/api/raven/missions/{id_or_slug}/kill")
async def kill_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    caller_ip = getattr(request.client, "host", "unknown")
    log.warning(
        f"[AUDIT] KILL requested for mission '{id_or_slug}' by user="
        f"{creds.get('user')} from {caller_ip} at {datetime.now(timezone.utc).isoformat()}"
    )

    async with borrow_http_client() as client:
        # Resolve to real ID
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status != 200:
            raise HTTPException(status_code=m_resp.status, detail="Mission not found")
        mission_data = await m_resp.json()
        real_id = mission_data["id"]
        _ensure_mission_owner(creds, mission_data)

        # 1. Update status in database — only if mission is still active.
        # Never overwrite a terminal state (completed / failed / cancelled).
        TERMINAL_STATES = {"completed", "failed", "cancelled"}
        if mission_data.get("status") in TERMINAL_STATES:
            log.warning(
                f"[AUDIT] Kill on already-terminal mission {real_id} "
                f"(status={mission_data.get('status')}) — skipping DB patch."
            )
        else:
            resp = await client.patch(
                f"{IDENTITY_SVC}/api/raven/missions/{real_id}",
                json={
                    "status": "failed",
                    "result": "Aborted by user"
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status != 200:
                raise HTTPException(status_code=resp.status, detail="Failed to update mission status")

        # 2. Publish kill signal to Redis
        import redis.asyncio as redis

        from services.gateway.config import REDIS_URL
        r = redis.from_url(REDIS_URL, decode_responses=True)
        log.warning(f"[AUDIT] Setting kill flag raven:mission:kill:{real_id}=KILL (ttl=3600) for mission '{id_or_slug}'")
        await r.set(f"raven:mission:kill:{real_id}", "KILL", ex=3600)
        await r.publish(f"raven:mission:kill:{real_id}", "KILL")
        await r.close()

        # Also remove any pending job from the Redis queue so a queued mission
        # can never be picked up again after a kill.
        if job_queue is not None:
            try:
                await job_queue.drop_jobs_for_mission(real_id)
            except Exception as e:
                log.warning(f"Failed to drop Redis job for killed mission {real_id}: {e}")

        return {"status": "SUCCESS", "message": f"Mission {real_id} kill signal sent."}

@app.post("/api/raven/missions/{id_or_slug}/cancel")
async def cancel_mission(request: Request, id_or_slug: str):
    """Close/cancel a Raven mission in ANY state (queued, executing, paused, failed).

    Unlike ``/kill`` — which only sets the DB status and publishes a kill flag that
    the running agent loop checks between steps — this fully purges the backing
    Redis job (and its lease/metadata) so the singleton worker can never claim a
    job whose mission no longer exists. This is the safe way to clear orphaned
    ``queued`` missions that have no active worker.
    """
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    caller_ip = getattr(request.client, "host", "unknown")
    log.warning(
        f"[AUDIT] CANCEL requested for mission '{id_or_slug}' by user="
        f"{creds.get('user')} from {caller_ip} at {datetime.now(timezone.utc).isoformat()}"
    )

    async with borrow_http_client() as client:
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status != 200:
            raise HTTPException(status_code=m_resp.status, detail="Mission not found")
        mission_data = await m_resp.json()
        real_id = mission_data["id"]
        _ensure_mission_owner(creds, mission_data)

        # 1. Remove any pending/processing job from Redis.
        dropped = 0
        if job_queue is not None:
            try:
                dropped = await job_queue.drop_jobs_for_mission(real_id)
            except Exception as e:
                log.warning(f"Failed to drop Redis job for cancelled mission {real_id}: {e}")

        # 2. Publish kill signal for safety (in case it is currently running).
        import redis.asyncio as redis

        from services.gateway.config import REDIS_URL
        r = redis.from_url(REDIS_URL, decode_responses=True)
        log.warning(f"[AUDIT] Setting kill flag raven:mission:kill:{real_id}=KILL (ttl=3600) for mission '{id_or_slug}'")
        await r.set(f"raven:mission:kill:{real_id}", "KILL", ex=3600)
        await r.publish(f"raven:mission:kill:{real_id}", "KILL")
        await r.close()

        # 3. Mark the mission cancelled in the database.
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{real_id}",
            json={"status": "failed", "result": "Cancelled by user"},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail="Failed to update mission status")

        return {"status": "SUCCESS", "message": f"Mission {real_id} cancelled.", "jobs_dropped": dropped}

@app.post("/api/raven/missions/{id_or_slug}/pause")
async def pause_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status != 200:
            raise HTTPException(status_code=m_resp.status, detail="Mission not found")
        mission_data = await m_resp.json()
        real_id = mission_data["id"]
        _ensure_mission_owner(creds, mission_data)

        import redis.asyncio as redis

        from services.gateway.config import REDIS_URL
        r = redis.from_url(REDIS_URL, decode_responses=True)
        await r.set(f"raven:mission:pause:{real_id}", "PAUSED", ex=3600)
        await r.publish(f"raven:mission:pause:{real_id}", "PAUSED")
        await r.close()

        return {"status": "SUCCESS", "message": f"Mission {real_id} paused. LLM access will be deferred until resumed."}

@app.post("/api/raven/missions/{id_or_slug}/resume")
async def resume_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status != 200:
            raise HTTPException(status_code=m_resp.status, detail="Mission not found")
        mission_data = await m_resp.json()
        real_id = mission_data["id"]
        _ensure_mission_owner(creds, mission_data)

        import redis.asyncio as redis

        from services.gateway.config import REDIS_URL
        r = redis.from_url(REDIS_URL, decode_responses=True)
        await r.delete(f"raven:mission:pause:{real_id}")
        await r.publish(f"raven:mission:pause:{real_id}", "RESUMED")
        await r.close()

        return {"status": "SUCCESS", "message": f"Mission {real_id} resumed. LLM access restored."}

class MissionRefineRequest(BaseModel):
    prompt: str

@app.post("/api/raven/missions/{id_or_slug}/refine")
async def refine_mission(request: Request, id_or_slug: str, body: MissionRefineRequest):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Refinement prompt cannot be empty")

    async with borrow_http_client() as client:
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status != 200:
            raise HTTPException(status_code=m_resp.status, detail="Mission not found")
        mission_data = await m_resp.json()
        real_id = mission_data["id"]

        # Ownership authorization check
        _ensure_mission_owner(creds, mission_data)

        # Capture history log before resetting database fields
        history_log = mission_data.get("output_log")

        new_proposed_mission = mission_data["proposed_mission"] + f"\n\n[USER REFINE DIRECTIVE]: {body.prompt}"

        patch_resp = await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{real_id}",
            json={
                "proposed_mission": new_proposed_mission,
                "status": "queued",
                "output_log": None,
                "result": None,
                "completed_at": None,
                "duration": None,
            },
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if patch_resp.status != 200:
            raise HTTPException(status_code=patch_resp.status, detail="Failed to update mission for refinement")

        system_prompt = ""
        if mission_data.get("mission_type") == "admin_fix":
            protocols = await fetch_autonomous_protocols()
            system_prompt = f"{protocols}\n\n[ADMIN ROZ ACTIVE]\nYou are the Raven Sentinel operating in the Restricted Operating Zone. Your mission is to fix backend/frontend components. You have elevated access. Execute the following mission:\n{new_proposed_mission}"
        else:
            system_prompt = await _build_raven_system_prompt(new_proposed_mission)

        # Fall back to default coding model if not set on the mission
        model = mission_data.get("coding_model")
        if not model:
            try:
                model = await get_coding_model()
            except Exception:
                model = ""

        if mission_data.get("mission_type") == "admin_fix":
            # System-triggered repair missions always run on the coding model
            # currently selected in the config DB — never a stale frozen value.
            model = await resolve_current_coding_model()

        assert job_queue is not None, "Job queue not initialized"
        await job_queue.enqueue_job("raven_admin", {
            "query": new_proposed_mission,
            "model": model,
            "system": system_prompt,
            "stream": False,
            "creds": creds,
            "_mission_id": real_id,
            "workspace_id": mission_data.get("workspace_id"),
            "history_log": history_log
        })

        return {"status": "SUCCESS", "message": f"Mission {real_id} refinement enqueued successfully.", "mission_id": real_id}

@app.delete("/api/raven/missions/{id_or_slug}")
async def delete_mission(request: Request, id_or_slug: str):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        m_resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if m_resp.status == 200:
            _ensure_mission_owner(creds, await m_resp.json())
        resp = await client.delete(
            f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=await resp.text())
        return {"status": "SUCCESS", "message": f"Mission {id_or_slug} deleted."}

@app.get("/api/raven/missions")
async def get_user_missions(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Per-user isolation: a non-admin only ever sees their own missions plus
    # shared/system ones (user_id is null). Admins see everything.
    qs = request.url.query
    url = f"{IDENTITY_SVC}/api/raven/missions" + (f"?{qs}" if qs else "")
    async with borrow_http_client() as client:
        resp = await client.get(
            url,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        missions = [m for m in await resp.json() if m["mission_type"] != "admin_fix" or creds.get("is_admin")]
        if not creds.get("is_admin"):
            caller_id = creds.get("id")
            missions = [
                m for m in missions
                if m.get("user_id") is None or m.get("user_id") == caller_id
            ]
        return JSONResponse(status_code=resp.status, content=missions)

@app.patch("/api/raven/missions/{id_or_slug}")
async def update_mission_status(id_or_slug: str, body: dict[str, Any], request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with borrow_http_client() as client:
        resp = await client.patch(
            f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=await resp.text())
        return await resp.json()

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
        return await _proxy_json_response(resp)

@app.post("/api/docker/exec/{service_name}")
async def proxy_docker_exec(service_name: str, body: dict[str, Any], request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds or not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    async with borrow_http_client() as client:
        resp = await client.post(
            f"{CONTROL_PLANE_URL}/api/containers/{service_name}/exec",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return await _proxy_json_response(resp)

@app.get("/api/raven/missions/{id_or_slug}/logs")
async def get_mission_logs(id_or_slug: str, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Resolve to real ID
    async with borrow_http_client() as client:
        resp = await client.get(f"{IDENTITY_SVC}/api/raven/missions/{id_or_slug}", headers={"X-Internal-Secret": INTERNAL_SECRET})
        if resp.status != 200:
            raise HTTPException(status_code=404, detail="Mission not found")
        mission_data = await resp.json()
        real_id = mission_data["id"]

    import redis.asyncio as redis

    from services.gateway.config import REDIS_URL
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
    # Validate auth token — a missing token must be rejected, not silently
    # allowed to replay mission history and subscribe to the live feed.
    if not token:
        await websocket.close(code=1008, reason="Missing token")
        return
    try:
        async with borrow_http_client() as client:
            auth_resp = await client.get(
                f"{IDENTITY_SVC}/api/users/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            if auth_resp.status != 200:
                log.warning(f"[WebSocket] Token validation failed for mission {id_or_slug}: {auth_resp.status}")
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
            if resp.status != 200:
                await websocket.send_text(json.dumps({"type": "system", "data": f"Mission {id_or_slug} not found"}))
                await websocket.close()
                return
            mission_data = await resp.json()
            real_id = mission_data["id"]

        import redis.asyncio as redis

        from services.gateway.config import REDIS_URL
        r = redis.from_url(REDIS_URL, decode_responses=True)

        # 1. Send all existing historical messages first
        history_key = f"raven:mission:history:{real_id}"
        existing_logs = await r.lrange(history_key, 0, -1)  # type: ignore[misc]
        for msg in existing_logs:
            with suppress(Exception):
                await websocket.send_text(msg)

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
                except TimeoutError:
                    pass  # Connection is still alive, handled by ping loop
                except WebSocketDisconnect:
                    break
        except Exception as e:
            log.warning(f"[WebSocket] Client disconnect: {e}")
        finally:
            reader_task.cancel()
            keep_alive_task.cancel()
            with suppress(Exception):
                await pubsub.unsubscribe(channel)
            await r.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"[WebSocket] Setup error for mission {id_or_slug}: {e}")
        with suppress(Exception):
            await websocket.close()

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
    """List locally-available models from the configured Ollama/model server.

    Tries both the native Ollama `/api/tags` endpoint and the OpenAI-compatible
    `/v1/models` endpoint (used by newer Ollama releases and the alpaca proxy),
    then merges and de-duplicates the results so the Local Model Mapping is never
    empty simply because one endpoint shape was assumed.
    """
    try:
        settings = await get_all_settings()
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            return {"status": "ERROR", "message": "Ollama URL not configured in Identity settings", "models": []}

        base = ollama_url.rstrip("/")
        models: set[str] = set()
        last_error: str | None = None

        async with shared_http_client() as client:
            # 1. Native Ollama tags endpoint: {"models": [{"name": ...}]}
            try:
                resp = await client.get(f"{base}/api/tags", timeout=aiohttp.ClientTimeout(total=10.0))
                if resp.status == 200:
                    data = await resp.json()
                    for m in data.get("models", []):
                        name = m.get("name") or m.get("model")
                        if name:
                            models.add(name)
            except Exception as e:  # pragma: no cover - network dependent
                last_error = str(e)

            # 2. OpenAI-compatible models endpoint: {"data": [{"id": ...}]}
            #    Queried in addition to (not only as a fallback for) /api/tags so the
            #    Local Model Mapping is fully populated regardless of which endpoint
            #    shape the model server exposes.
            try:
                resp = await client.get(f"{base}/v1/models", timeout=aiohttp.ClientTimeout(total=10.0))
                if resp.status == 200:
                    data = await resp.json()
                    for m in data.get("data", []):
                        name = m.get("id") or m.get("name")
                        if name:
                            models.add(name)
            except Exception as e:  # pragma: no cover - network dependent
                last_error = last_error or str(e)

        if models:
            return {"status": "SUCCESS", "models": sorted(models)}
        return {
            "status": "ERROR",
            "message": last_error or "No models returned by Ollama",
            "models": [],
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e), "models": []}

@app.get("/api/config")
async def get_gateway_config():
    # Fetch all three model settings from Identity Service to ensure UI is in sync
    assistant = await get_assistant_model()
    coding = await get_coding_model()
    librarian = await get_librarian_model()
    telemetry = await get_telemetry_model()
    return {
        "status": "SUCCESS",
        "config": {
            "assistant_model": assistant,
            "coding_model": coding,
            "librarian_model": librarian,
            "telemetry_model": telemetry,
        }
    }

@app.post("/api/config")
async def update_gateway_config(new_config: dict):
    # Save the new configuration to the Identity Service GlobalSettings
    async with shared_http_client() as client:
        for key in ["assistant_model", "coding_model", "librarian_model"]:
            if key in new_config:
                val = new_config[key]
                # Identity Service uses PATCH /api/settings/{key} with a body {"value": val}
                try:
                    resp = await client.patch(
                        f"{IDENTITY_SVC}/api/settings/{key}",
                        json={"value": val},
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                        , timeout=aiohttp.ClientTimeout(total=5.0),
                    )
                    if resp.status != 200:
                        log.error(f"Failed to sync global config {key}: Identity SVC returned {resp.status}")
                        raise HTTPException(status_code=resp.status, detail=f"Identity Service error for {key}")

                    # Refresh the internal CONFIG cache ONLY on success
                    CONFIG[key] = val
                    log.info(f"Synchronized global config: {key} -> {val}")
                except Exception as e:
                    log.error(f"Exception during global config sync for {key}: {e}")
                    raise HTTPException(status_code=500, detail=str(e)) from e

    log.info(f"Updated Gateway Config via Identity SVC: {new_config}")
    from services.gateway.cache import invalidate_settings
    invalidate_settings()
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

    async with shared_http_client() as client:
        await client.patch(
            f"{IDENTITY_SVC}/api/settings/dns_mappings",
            json={"value": json.dumps(dns_mappings)},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=5.0),
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

    async with shared_http_client() as client:
        await client.patch(
            f"{IDENTITY_SVC}/api/settings/dns_mappings",
            json={"value": json.dumps(dns_mappings)},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=5.0),
        )

    return {"status": "SUCCESS", "message": f"Removed {hostname}"}


@app.post("/api/admin/dns/update")
async def update_dns_config(request: Request):
    """Update DNS configuration (upstream, poll interval, or full mappings)."""
    body = await request.json()

    if "dns_upstream" in body:
        async with shared_http_client() as client:
            await client.patch(
                f"{IDENTITY_SVC}/api/settings/dns_upstream",
                json={"value": body["dns_upstream"]},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=5.0),
            )

    if "dns_poll_interval" in body:
        async with shared_http_client() as client:
            await client.patch(
                f"{IDENTITY_SVC}/api/settings/dns_poll_interval",
                json={"value": str(body["dns_poll_interval"])},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=5.0),
            )

    if "dns_mappings" in body:
        async with shared_http_client() as client:
            await client.patch(
                f"{IDENTITY_SVC}/api/settings/dns_mappings",
                json={"value": json.dumps(body["dns_mappings"])},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=5.0),
            )

    from services.gateway.cache import invalidate_settings
    invalidate_settings()
    return {"status": "SUCCESS", "message": "DNS configuration updated"}


# --- DNS Management (UI-facing /api/dns routes) ---
# The UI's DnsManagementPanel calls /api/dns (GET/POST/PUT/DELETE) with a rich
# record schema (id, domain, record_type, values[], ttl, is_active, timestamps).
# The backing store is the `dns_mappings` global setting (a hostname -> ip dict).
# These routes adapt between the two so the panel loads and mutates correctly
# instead of 404-ing and endlessly re-polling (the "DNS reload loop").


def _dns_id(hostname: str) -> int:
    return int(hashlib.md5(hostname.encode()).hexdigest(), 16) % 1_000_000_000


async def _load_dns_mappings() -> dict:
    raw = await fetch_global_setting("dns_mappings", "{}")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _normalize_dns_values(entry: Any) -> list[str]:
    """Flatten a DNS mapping entry (str | list | dict) into a clean list of strings."""
    if isinstance(entry, str):
        return [entry] if entry.strip() else []
    if isinstance(entry, list):
        return [str(v).strip() for v in entry if str(v).strip()]
    if isinstance(entry, dict):
        vals = entry.get("values")
        if isinstance(vals, list):
            return [str(v).strip() for v in vals if str(v).strip()]
        ip = entry.get("ip")
        if isinstance(ip, str):
            return [ip.strip()] if ip.strip() else []
        if isinstance(ip, list):
            return [str(v).strip() for v in ip if str(v).strip()]
    return []


def _record_from(hostname: str, entry: Any) -> dict:
    values = _normalize_dns_values(entry)
    if isinstance(entry, dict):
        record_type = entry.get("record_type", "A")
        ttl = entry.get("ttl", 300)
        is_active = entry.get("is_active", True)
        created_at = entry.get("created_at", "")
        updated_at = entry.get("updated_at", "")
    else:
        record_type = "A"
        ttl = 300
        is_active = True
        created_at = ""
        updated_at = ""
    return {
        "id": _dns_id(hostname),
        "domain": hostname,
        "record_type": record_type,
        "values": values,
        "ttl": ttl,
        "is_active": is_active,
        "created_at": created_at,
        "updated_at": updated_at,
    }


async def _save_dns_mappings(mappings: dict) -> None:
    async with shared_http_client() as client:
        await client.patch(
            f"{IDENTITY_SVC}/api/settings/dns_mappings",
            json={"value": json.dumps(mappings)},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
             timeout=aiohttp.ClientTimeout(total=5.0),
        )
    from services.gateway.cache import invalidate_settings
    invalidate_settings()


def _hostname_for_id(mappings: dict, record_id: int) -> str | None:
    for hostname in mappings:
        if _dns_id(hostname) == record_id:
            return hostname
    return None


@app.get("/api/dns")
async def ui_list_dns_records():
    """List DNS records in the shape the UI's DnsManagementPanel expects."""
    mappings = await _load_dns_mappings()
    return [_record_from(h, mappings[h]) for h in sorted(mappings)]


@app.post("/api/dns")
async def ui_create_dns_record(request: Request):
    """Create a DNS record from the UI's rich schema."""
    body = await request.json()
    domain = (body.get("domain") or "").strip()
    values = body.get("values") or []
    ip = (values[0] if values else "").strip()
    if not domain or not ip:
        raise HTTPException(status_code=400, detail="domain and at least one value are required")

    mappings = await _load_dns_mappings()
    now = datetime.now().isoformat() + "Z"
    mappings[domain] = {
        "ip": ip,
        "record_type": body.get("record_type", "A"),
        "values": values,
        "ttl": int(body.get("ttl", 300) or 300),
        "is_active": bool(body.get("is_active", True)),
        "created_at": now,
        "updated_at": now,
    }
    await _save_dns_mappings(mappings)
    return _record_from(domain, mappings[domain])


@app.put("/api/dns/{record_id:int}")
async def ui_update_dns_record(record_id: int, request: Request):
    """Update a DNS record identified by the UI's numeric id."""
    mappings = await _load_dns_mappings()
    hostname = _hostname_for_id(mappings, record_id)
    if not hostname:
        raise HTTPException(status_code=404, detail=f"DNS record {record_id} not found")

    body = await request.json()
    entry = mappings[hostname]
    if not isinstance(entry, dict):
        entry = {"ip": entry, "record_type": "A", "ttl": 300, "is_active": True,
                 "created_at": "", "updated_at": ""}
    if "domain" in body and body["domain"].strip():
        new_hostname = body["domain"].strip()
        if new_hostname != hostname:
            del mappings[hostname]
            hostname = new_hostname
    if "record_type" in body:
        entry["record_type"] = body["record_type"]
    if body.get("values"):
        vals = [str(v).strip() for v in body["values"] if str(v).strip()]
        if vals:
            entry["ip"] = vals[0]
            entry["values"] = vals
    if "ttl" in body:
        entry["ttl"] = int(body["ttl"] or 300)
    if "is_active" in body:
        entry["is_active"] = bool(body["is_active"])
    entry["updated_at"] = datetime.now().isoformat() + "Z"
    mappings[hostname] = entry
    await _save_dns_mappings(mappings)
    return _record_from(hostname, mappings[hostname])


@app.delete("/api/dns/{record_id:int}")
async def ui_delete_dns_record(record_id: int):
    """Delete a DNS record identified by the UI's numeric id."""
    mappings = await _load_dns_mappings()
    hostname = _hostname_for_id(mappings, record_id)
    if not hostname:
        raise HTTPException(status_code=404, detail=f"DNS record {record_id} not found")
    del mappings[hostname]
    await _save_dns_mappings(mappings)
    return {"status": "SUCCESS", "message": f"Removed {hostname}"}


# --- Tool Registry (OpenAI/Ollama tool-calling) ---

def resolve_service_base_url(service: str) -> str:
    """Map a tool_registry service id to its base URL."""
    if service == SVC_EXECUTION:
        if EXECUTION_SVC is None:
            raise ValueError("EXECUTION_SVC not configured")
        return EXECUTION_SVC
    if service == SVC_WORKSPACE:
        if WORKSPACE_RUNTIME_SVC is None:
            raise ValueError("WORKSPACE_RUNTIME_SVC not configured")
        return WORKSPACE_RUNTIME_SVC
    if service == SVC_ALPACA_SD:
        return ALPACA_SD_URL
    raise ValueError(f"Unknown tool service: {service}")


async def run_sharedllm_tool(
    resolved,
    *,
    creds: dict | None = None,
) -> dict:
    """Execute a ResolvedToolCall against the appropriate backend service.

    Internal services (execution, workspace_runtime) are called with the
    X-Internal-Secret handshake. The alpaca SD backend is treated as an external
    image service and called without internal credentials.
    """
    base = resolve_service_base_url(resolved.service)
    url = f"{base}{resolved.path}"
    headers = {}
    if resolved.service in (SVC_EXECUTION, SVC_WORKSPACE):
        headers["X-Internal-Secret"] = INTERNAL_SECRET

    async with shared_http_client() as client:
        if resolved.method == "GET":
            async with client.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=180.0)) as resp:
                return {"status": resp.status, "body": await _safe_json(resp)}
        else:
            async with client.post(url, json=resolved.json, headers=headers, timeout=aiohttp.ClientTimeout(total=180.0)) as resp:
                return {"status": resp.status, "body": await _safe_json(resp)}


async def _safe_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json()
    except Exception:
        text = await resp.text()
        return {"raw": text}


@app.get("/v1/tools")
async def list_sharedllm_tools():
    """OpenAI-compatible tool discovery for external clients (OpenAI SDK, Ollama, OpenWebUI)."""
    return {"object": "list", "tools": get_tool_schemas()}


# --- Presence & Location Endpoints ---

@app.get("/api/presence/{user_id}")
async def get_user_presence(user_id: str):
    """Get presence data for a user."""
    async with shared_http_client() as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/presence/{user_id}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Presence service unavailable")


@app.get("/api/presence/all")
async def get_all_presence():
    """Get presence data for all users."""
    async with shared_http_client() as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/presence/all",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Presence service unavailable")


@app.get("/api/presence/rooms")
async def get_presence_rooms():
    """Get list of all known rooms."""
    async with shared_http_client() as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/presence/rooms",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Presence service unavailable")


# NOTE: the path is deliberately "/api/users/location/all" and not
# "/api/users/locations" — the latter is captured by the earlier
# PATCH /api/users/{user_id} route, which answers 405 for GET.
@app.get("/api/users/location/all")
async def get_all_user_locations(request: Request):
    """Last known GPS position for every user who has location sharing on."""
    if not await _user_id_from_request(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users/location/all",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Identity service unavailable")


@app.post("/api/users/{user_id}/location")
async def update_user_location(user_id: str, request: Request):
    """Update user GPS location."""
    body = await request.json()
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{user_id}/location",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Identity service unavailable")


@app.post("/api/users/location")
@app.post("/api/identity/users/location")
async def update_current_user_location(request: Request):
    """Update current user GPS location (resolving user_id from body, params, or default)."""
    body = await request.json()
    user_id = "default"
    if isinstance(body, dict) and body.get("user_id"):
        user_id = body["user_id"]
    elif request.query_params.get("user_id"):
        user_id = request.query_params["user_id"]
    async with shared_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{user_id}/location",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Identity service unavailable")


@app.get("/api/users/{user_id}/location")
async def get_user_location(user_id: str):
    """Get user GPS location."""
    async with shared_http_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC}/api/users/{user_id}/location",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=404, detail="Location not found")


# --- Life360 & Vehicle Telemetry Endpoints ---

@app.get("/api/geo/vehicles")
async def get_geo_vehicles():
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicles",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Geo service unavailable")


@app.post("/api/geo/vehicles")
async def save_geo_vehicle(request: Request):
    body = await request.json()
    async with shared_http_client() as client:
        resp = await client.post(
            f"{GEO_SVC}/vehicles",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        err_msg = await resp.text()
        log.error(f"[geo/vehicles] Save vehicle failed: {resp.status} - {err_msg}")
    raise HTTPException(status_code=resp.status if resp.status < 500 else 502, detail=f"Failed to save vehicle: {err_msg}")


@app.delete("/api/geo/vehicles/{vehicle_id}")
async def delete_geo_vehicle(vehicle_id: str):
    async with shared_http_client() as client:
        resp = await client.delete(
            f"{GEO_SVC}/vehicles/{vehicle_id}",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        err_msg = await resp.text()
        log.error(f"[geo/vehicles] Delete vehicle failed: {resp.status} - {err_msg}")
    raise HTTPException(status_code=resp.status if resp.status < 500 else 502, detail=f"Failed to delete vehicle: {err_msg}")


@app.get("/api/geo/vehicles/assigned/{user_id}")
async def get_geo_assigned_vehicle(user_id: str):
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicles/assigned/{user_id}",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Geo service unavailable")


@app.post("/api/geo/vehicles/assign")
async def assign_geo_vehicle(request: Request):
    body = await request.json()
    async with shared_http_client() as client:
        resp = await client.post(
            f"{GEO_SVC}/vehicles/assign",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        err_msg = await resp.text()
        log.error(f"[geo/vehicles] Assign vehicle failed: {resp.status} - {err_msg}")
    raise HTTPException(status_code=resp.status if resp.status < 500 else 502, detail=f"Failed to assign vehicle: {err_msg}")


@app.get("/api/geo/fuel-prices")
async def get_fuel_prices(location: str = ""):
    """Proxy fuel price lookup to geo service."""
    if not location:
        raise HTTPException(status_code=400, detail="location parameter is required")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/fuel-prices",
            params={"location": location},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        if resp.status == 200:
            return await resp.json()
        err_msg = await resp.text()
        log.error(f"[geo/fuel-prices] Lookup failed: {resp.status} - {err_msg}")
    raise HTTPException(status_code=resp.status if resp.status < 500 else 502, detail=f"Fuel price lookup failed: {err_msg}")


@app.get("/api/geo/vehicle-lookup/years")
async def vehicle_lookup_years():
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicle-lookup/years",
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Vehicle lookup failed")


@app.get("/api/geo/vehicle-lookup/makes")
async def vehicle_lookup_makes(year: int = 0):
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicle-lookup/makes",
            params={"year": year},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Vehicle lookup failed")


@app.get("/api/geo/vehicle-lookup/models")
async def vehicle_lookup_models(year: int = 0, make: str = ""):
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicle-lookup/models",
            params={"year": year, "make": make},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Vehicle lookup failed")


@app.get("/api/geo/vehicle-lookup/options")
async def vehicle_lookup_options(year: int = 0, make: str = "", model: str = ""):
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicle-lookup/options",
            params={"year": year, "make": make, "model": model},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Vehicle lookup failed")


@app.get("/api/geo/vehicle-lookup/vin/{vin}")
async def vehicle_lookup_vin(vin: str):
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicle-lookup/vin/{vin}",
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "VIN lookup failed")


@app.get("/api/geo/vehicle-lookup/{vehicle_id}")
async def vehicle_lookup_detail(vehicle_id: str):
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/vehicle-lookup/{vehicle_id}",
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Vehicle lookup failed")


@app.get("/api/geo/telemetry/{user_id}")
async def get_geo_telemetry(request: Request, user_id: str, hours: float = 24.0):
    """Fine-grained GPS telemetry for one person — own, admin, or opted-in only."""
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/people/{target}/telemetry",
            params={"hours": hours, "viewer": viewer, "is_admin": is_admin},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        raise HTTPException(status_code=resp.status, detail="Geo telemetry unavailable")


@app.get("/api/geo/people")
async def get_geo_people(request: Request, viewer: str | None = None):
    """Live HA presence for the family circle.

    Requires authentication (this returned the live position of every person
    entity to an anonymous caller), and forwards the viewer so geo can drop
    anyone who has not opted into being visible.
    """
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/people",
            params={"viewer": ident["user"], "is_admin": str(bool(ident.get("is_admin")))},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Geo people unavailable")


@app.get("/api/geo/android_auto")
async def get_geo_android_auto(request: Request, user_id: str | None = None):
    """Android auto-reporting configuration — own data only unless admin."""
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {"user_id": target, "viewer": viewer, "is_admin": is_admin}
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/android_auto",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Geo android_auto unavailable")


@app.get("/api/geo/zones")
async def get_geo_zones():
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/zones",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Geo zones unavailable")


async def _raise_geo_failure(resp, label: str) -> None:
    """Translate a non-200 from geo into the right answer for the client.

    geo deliberately answers **404** when a caller may not read another's
    activity, so a probe cannot tell "no such user" from "not shared with
    you". Collapsing that into a blanket 502 told the client the server was
    broken, which is both wrong and unhelpful -- it is the reason a refused
    read looked identical to an outage. Only a genuine upstream fault is a 502.

    **400/422 join them.** geo answers those for a request it understood and
    rejected -- an out-of-bounds `days`, an unknown `range`. Reporting "geo is
    unavailable" for a bad parameter is the same mistake one status code
    further down, and it is actively misleading: the fix is in the caller's
    URL, not on the server. Verified live before this change:
    `GET /api/geo/steps?days=365` returned 502 while geo had correctly said 422.
    """
    if resp.status in (400, 401, 403, 404, 422):
        detail: Any = None
        try:
            body = await resp.json()
            if isinstance(body, dict):
                detail = body.get("detail")
        except Exception:  # noqa: BLE001 - a non-JSON error body is not fatal
            detail = None
        raise HTTPException(status_code=resp.status, detail=detail or label)
    raise HTTPException(status_code=502, detail=label)


@app.get("/api/geo/trips")
async def get_geo_trips(request: Request, user_id: str | None = None, limit: int = 50):
    """List trips.

    This used to forward no identity at all, so geo served its ``geo:trips:all``
    index -- every user's trips to any authenticated caller (verified live:
    a non-admin received 7 trips, all belonging to the admin). The viewer is
    now forwarded and geo applies the opt-in consent check.
    """
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {"limit": limit, "user_id": target, "viewer": viewer, "is_admin": is_admin}
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/trips",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to fetch trips")


@app.get("/api/geo/trips/{trip_id}")
async def get_geo_trip(request: Request, trip_id: str):
    await _require_authenticated(request)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/trips/{trip_id}",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=404, detail="Trip not found")


@app.get("/api/geo/trips/{trip_id}/locations")
async def get_geo_trip_locations(request: Request, trip_id: str):
    """Resolved start/end place names + coordinates for a trip."""
    await _require_authenticated(request)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/trips/{trip_id}/locations",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=12.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=404, detail="Trip locations not found")


@app.patch("/api/geo/trips/{trip_id}")
async def update_geo_trip(trip_id: str, request: Request):
    user = await _require_authenticated(request)

    body = await request.json()
    headers = {
        "X-Internal-Secret": INTERNAL_SECRET,
        "X-User-Id": user,
    }
    async with shared_http_client() as client:
        resp = await client.patch(
            f"{GEO_SVC}/trips/{trip_id}",
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        elif resp.status == 403:
            err = await resp.json()
            raise HTTPException(status_code=403, detail=err.get("detail", "Forbidden"))
        elif resp.status == 404:
            raise HTTPException(status_code=404, detail="Trip not found")
    await _raise_geo_failure(resp, "Failed to update trip")


@app.get("/api/geo/trips/{trip_id}/route")
async def get_geo_trip_route(request: Request, trip_id: str):
    await _require_authenticated(request)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/trips/{trip_id}/route",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to fetch trip route")


@app.get("/api/geo/locations/suggestions")
async def get_geo_location_suggestions(lat: float, lon: float):
    """Nearby place-name suggestions for a coordinate (trip editing)."""
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/locations/suggestions",
            params={"lat": lat, "lon": lon},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
        if resp.status == 422:
            err = await resp.json()
            raise HTTPException(status_code=422, detail=err.get("detail", "Invalid coordinates"))
    await _raise_geo_failure(resp, "Failed to fetch location suggestions")


@app.patch("/api/geo/trips/{trip_id}/share")
async def share_geo_trip(trip_id: str, request: Request):
    """Share a trip with riders.

    Previously this forwarded no ``X-User-Id`` at all, which made geo's owner
    check (``if request_user and request_user != trip_user``) vacuous -- it
    received an empty string and skipped the comparison, so any caller could
    share anyone's trip.
    """
    user = await _require_authenticated(request)
    body = await request.json()
    async with shared_http_client() as client:
        resp = await client.patch(
            f"{GEO_SVC}/trips/{trip_id}/share",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET, "X-User-Id": user},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        elif resp.status == 403:
            err = await resp.json()
            raise HTTPException(status_code=403, detail=err.get("detail", "Forbidden"))
        elif resp.status == 404:
            raise HTTPException(status_code=404, detail="Trip not found")
    await _raise_geo_failure(resp, "Failed to share trip")


@app.get("/api/geo/workouts")
async def get_geo_workouts(request: Request, user_id: str | None = None, limit: int = 20):
    """List workouts.

    Identical defect to /api/geo/trips: no identity was forwarded, so geo served
    ``geo:workouts:all`` -- every user's workouts to any authenticated caller.
    """
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {"limit": limit, "user_id": target, "viewer": viewer, "is_admin": is_admin}
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/workouts",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to fetch workouts")


@app.get("/api/geo/workouts/active")
async def get_geo_active_workout(request: Request, user_id: str | None = None):
    """The caller's in-progress workout, if any.

    Always scoped to the caller: an in-progress session is live state, not
    history, and there is no "all" bucket to request. 404 is passed through as
    "nothing running" rather than an error.
    """
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/workouts/active",
            params={"viewer": viewer, "is_admin": is_admin},
            headers={"X-Internal-Secret": INTERNAL_SECRET, "X-User-Id": target},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        if resp.status == 200:
            return await resp.json()
        if resp.status == 404:
            return {"workout": None}
    await _raise_geo_failure(resp, "Failed to fetch active workout")


@app.post("/api/geo/workouts/start")
async def start_geo_workout(request: Request):
    """Start a workout for the caller.

    The body's ``user_id`` is no longer trusted over the authenticated caller:
    starting a workout for someone else requires admin.
    """
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    claimed = str(body.get("user_id") or "").strip().lower()
    if claimed and claimed != ident["user"].lower() and not ident.get("is_admin"):
        raise HTTPException(
            status_code=403, detail="Cannot start a workout for another user"
        )
    body["user_id"] = claimed or ident["user"]
    async with shared_http_client() as client:
        resp = await client.post(
            f"{GEO_SVC}/workouts/start",
            json=body,
            headers={
                "X-Internal-Secret": INTERNAL_SECRET,
                "X-User-Id": ident["user"],
            },
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        elif resp.status == 409:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to start workout")


@app.post("/api/geo/workouts/stop")
async def stop_geo_workout(request: Request):
    """Stop the caller's active workout (admins may stop anyone's)."""
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    claimed = str(body.get("user_id") or "").strip().lower()
    if claimed and claimed != ident["user"].lower() and not ident.get("is_admin"):
        raise HTTPException(
            status_code=403, detail="Cannot stop another user's workout"
        )
    body["user_id"] = claimed or ident["user"]
    async with shared_http_client() as client:
        resp = await client.post(
            f"{GEO_SVC}/workouts/stop",
            json=body,
            headers={
                "X-Internal-Secret": INTERNAL_SECRET,
                "X-User-Id": ident["user"],
            },
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        elif resp.status == 404:
            err = await resp.json()
            raise HTTPException(status_code=404, detail=err.get("detail", "No active workout"))
    await _raise_geo_failure(resp, "Failed to stop workout")


@app.get("/api/geo/workouts/{workout_id}/route")
async def get_geo_workout_route(request: Request, workout_id: str):
    await _require_authenticated(request)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/workouts/{workout_id}/route",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to fetch workout route")


@app.get("/api/geo/steps/ranges")
async def get_geo_step_ranges(request: Request, range: str = "W", user_id: str | None = None):
    """Pre-aggregated step history for D/W/M/3M/Y.

    Declared before the "/api/geo/steps" route so a literal path is never
    captured as something else. The 30-day cap on the daily route stays where
    it is: this is the way to look further back, not a widened version of it.
    """
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {"range": range, "user_id": target, "viewer": viewer, "is_admin": is_admin}
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/steps/ranges",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/geo/events")
async def get_geo_events(
    request: Request,
    user_id: str | None = None,
    days: int = 30,
    limit: int = 60,
    domain: str = "health",
):
    """A dated timeline of what actually happened.

    `domain` chooses the page's view: "health" (workouts, badges, goals) or
    "wander" (drives). Forwarded so the two pages never have to fetch and
    filter client-side.

    Declared before the metrics siblings so a literal path is never captured as
    a parameter, and it uses the same reader identity path as every other geo
    read so a caller-supplied `viewer` cannot widen consent.
    """
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {
        "days": days,
        "limit": limit,
        "domain": domain,
        "viewer": viewer or "",
        "is_admin": is_admin or "",
    }
    if target:
        params["user_id"] = target
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/events",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/geo/metrics/catalog")
async def get_geo_metric_catalog(request: Request):
    """Which health metrics the panel can show, and why the rest are absent.

    Public within the app: it names no data about anyone, only what is
    recorded, so the UI can say "calories aren't tracked" instead of drawing
    an empty card.
    """
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/metrics/catalog",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/geo/metrics/ranges")
async def get_geo_metric_ranges(
    request: Request, metric: str = "workouts", range: str = "W", user_id: str | None = None
):
    """Pre-aggregated workout/distance history, same shape as the steps route.

    Declared before any sibling under /api/geo/metrics so a literal path is
    never captured as a parameter.
    """
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {
        "metric": metric,
        "range": range,
        "user_id": target,
        "viewer": viewer,
        "is_admin": is_admin,
    }
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/metrics/ranges",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/geo/steps")
async def get_geo_steps(request: Request, user_id: str | None = None, days: int = 7):
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    # Always pass user_id -- geo GET /steps 400s when it's omitted.
    params = {"days": days, "user_id": target, "viewer": viewer, "is_admin": is_admin}
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/steps",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=8.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to fetch steps")


@app.post("/api/geo/steps")
async def proxy_geo_steps(request: Request):
    """Ingest a hardware pedometer reading (sent by the mobile app with Bearer auth).

    Previously the body's ``user_id`` was forwarded verbatim with no check at
    all, so anyone could write readings into anyone's history. It must now match
    the authenticated caller (or the caller must be admin). An unauthenticated
    write is rejected outright rather than stored -- that is what produced the
    orphan ``geo:steps_meta:me`` bucket.
    """
    body = await request.json()
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    claimed = str(body.get("user_id") or "").strip().lower()
    if claimed and claimed != ident["user"].lower() and not ident.get("is_admin"):
        raise HTTPException(
            status_code=403, detail="Cannot record steps for another user"
        )
    body["user_id"] = claimed or ident["user"]
    async with shared_http_client() as client:
        resp = await client.post(
            f"{GEO_SVC}/steps",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to record steps")


@app.get("/api/geo/goals")
async def proxy_get_goals(request: Request, user_id: str | None = None):
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/goals",
            params={"user_id": target, "viewer": viewer, "is_admin": is_admin},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to read goals")


@app.put("/api/geo/goals")
async def proxy_put_goals(request: Request):
    """Set activity goals for the caller (admins may set anyone's).

    geo's PUT /goals never verified the internal secret, so this route was an
    unauthenticated *write* hole: anyone could rewrite anyone's goals.
    """
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    target = str(body.get("user_id") or "").strip().lower()
    if target and target != ident["user"].lower() and not ident.get("is_admin"):
        raise HTTPException(
            status_code=403, detail="Cannot change goals for another user"
        )
    body["user_id"] = target or ident["user"]
    async with shared_http_client() as client:
        resp = await client.put(
            f"{GEO_SVC}/goals",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        detail = await resp.text()
    raise HTTPException(status_code=resp.status, detail=detail[:200])


@app.get("/api/geo/stars")
async def proxy_get_stars(request: Request, user_id: str | None = None):
    """Read a star balance. Own balance by default; another user's needs admin."""
    viewer = await _user_id_from_request(request)
    if not viewer:
        raise HTTPException(status_code=401, detail="Authentication required")
    target = (user_id or "").strip() or viewer
    if target != viewer and not await _caller_is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/api/geo/stars",
            params={"user_id": target},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.post("/api/admin/users/{user_id}/stars")
async def admin_grant_stars_with_mirror(user_id: str, request: Request):
    """Grant a user stars and mirror them into their Skylight account.

    Per ``docs/ACHIEVEMENTS.md``: "Everything is recorded in the points ledger
    first, then mirrored to Skylight, so a Skylight outage cannot lose the
    award." So the two outcomes are reported separately rather than collapsed:

    * If the ledger write fails, nothing is mirrored and the failure is
      returned -- mirroring an award that was never recorded would be a lie.
    * If the ledger write succeeds but Skylight is down or unconfigured, the
      grant still stands and ``skylight.status`` says so. Retrying the mirror
      later is safe; re-running the grant would double-count it.
    """
    if not await _caller_is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required to grant stars")

    target = (user_id or "").strip().lower()
    if not target:
        raise HTTPException(status_code=422, detail="user_id is required")
    body = await request.json()

    async with shared_http_client() as client:
        grant = await client.post(
            f"{GEO_SVC}/api/geo/stars",
            json={**body, "user_id": target},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if grant.status >= 400:
            # Surface geo's own message (a refused negative balance, a bad
            # reason) rather than a generic 500 from this hop.
            return await _proxy_json_response(grant)

        result: dict = {"user_id": target, "ledger": await grant.json()}

        if body.get("mirror_to_skylight", True):
            # `user` selects the *target's* Skylight credentials, not the
            # admin's -- the stars belong to them.
            mirror = await client.post(
                f"{EXECUTION_SVC}/api/integrations/skylight/stars",
                json={
                    "member": target,
                    "stars": body.get("stars"),
                    "reason": body.get("reason"),
                    "note": body.get("note"),
                    "user": target,
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=30.0),
            )
            try:
                result["skylight"] = await mirror.json()
            except Exception:
                result["skylight"] = {
                    "status": "FAILURE",
                    "message": f"Skylight mirror returned HTTP {mirror.status}",
                }
            if mirror.status >= 400:
                result["skylight"]["status"] = "FAILURE"
        else:
            result["skylight"] = {"status": "SKIPPED", "message": "mirror_to_skylight was false"}

        return JSONResponse(status_code=200, content=result)


@app.post("/api/geo/stars")
async def proxy_grant_stars(request: Request):
    """Grant bonus stars. Admin only.

    The previous docstring claimed "Identity enforces admin rights on the
    caller", but nothing did: geo has no admin check on this route and the
    gateway forwarded no admin signal, so any authenticated user could mint
    stars for anyone. The check is enforced here instead of being asserted.
    """
    if not await _caller_is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required to grant stars")
    body = await request.json()
    if not body.get("user_id"):
        body["user_id"] = await _user_id_from_request(request) or ""
    async with shared_http_client() as client:
        resp = await client.post(
            f"{GEO_SVC}/api/geo/stars",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        return await _proxy_json_response(resp)


@app.get("/api/geo/achievements")
async def proxy_get_achievements(
    request: Request, user_id: str | None = None, days: int = 30
):
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/achievements",
            params={"user_id": target, "viewer": viewer, "is_admin": is_admin, "days": days},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to read achievements")


@app.get("/api/geo/points")
async def proxy_get_points(request: Request, user_id: str | None = None):
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/points",
            params={"user_id": target, "viewer": viewer, "is_admin": is_admin},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to read points")


@app.get("/api/geo/activity/summary")
async def proxy_activity_summary(
    request: Request, user_id: str | None = None, window: str = "week"
):
    """Running totals — own data always; anyone else's only with opt-in."""
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/activity/summary",
            params={"user_id": target, "window": window, "viewer": viewer, "is_admin": is_admin},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
        detail = await resp.text()
    raise HTTPException(status_code=resp.status, detail=detail[:200])


@app.get("/api/geo/activity/feed")
async def proxy_activity_feed(request: Request, window: str = "week"):
    """Activity of others who opted in and included the caller in their audience."""
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/activity/feed",
            params={"viewer": ident["user"], "window": window},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to read activity feed")


@app.get("/api/geo/steps/goal")
async def proxy_get_step_goal(request: Request, user_id: str | None = None):
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/steps/goal",
            params={"user_id": target, "viewer": viewer, "is_admin": is_admin},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to read step goal")


@app.put("/api/geo/steps/goal")
async def proxy_set_step_goal(request: Request):
    """Set the daily step goal for the caller (admins may set anyone's).

    geo's PUT /steps/goal never verified the internal secret, so this was an
    unauthenticated *write* hole.
    """
    ident = await _acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    target = str(body.get("user_id") or "").strip().lower()
    if target and target != ident["user"].lower() and not ident.get("is_admin"):
        raise HTTPException(
            status_code=403, detail="Cannot change another user's step goal"
        )
    body["user_id"] = target or ident["user"]
    async with shared_http_client() as client:
        resp = await client.put(
            f"{GEO_SVC}/steps/goal",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status == 200:
            return await resp.json()
        detail = await resp.text()
    raise HTTPException(status_code=resp.status, detail=detail[:200])


@app.get("/api/geo/trends/activity")
async def get_geo_activity_trends(request: Request, user_id: str | None = None, days: int = 7, refresh: bool = False):
    if not user_id:
        target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {"days": days, "user_id": target, "viewer": viewer, "is_admin": is_admin}
    if refresh:
        params["refresh"] = "true"
    async with shared_http_client() as client:
        resp = await client.get(
            f"{GEO_SVC}/trends/activity",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=100.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to fetch activity trends")


@app.post("/api/geo/trends/activity/analyze")
async def post_geo_activity_trends_analyze(
    request: Request, user_id: str | None = None, days: int = 7, refresh: bool = False
):
    """Explicitly generate the activity narrative (never triggered by a page load)."""
    target, viewer, is_admin = await _geo_read_target(request, user_id)
    params = {"days": days, "user_id": target, "viewer": viewer, "is_admin": is_admin}
    if refresh:
        params["refresh"] = "true"
    async with shared_http_client() as client:
        resp = await client.post(
            f"{GEO_SVC}/trends/activity/analyze",
            params=params,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=300.0),
        )
        if resp.status == 200:
            return await resp.json()
    await _raise_geo_failure(resp, "Failed to analyze activity trends")


# --- Scheduled telemetry reports (health/fitness + power) --------------------
# The user is always taken from the authenticated request, never from the body,
# so one user cannot schedule or read another user's reports.

_TELEMETRY_TIMEOUT = aiohttp.ClientTimeout(total=30.0)


async def _telemetry_call(method: str, path: str, **kwargs):
    async with shared_http_client() as client:
        resp = await client.request(
            method,
            f"{TELEMETRY_SVC}{path}",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=_TELEMETRY_TIMEOUT,
            **kwargs,
        )
        if resp.status >= 400:
            detail = await resp.text()
            raise HTTPException(status_code=resp.status, detail=detail[:300])
        return await resp.json(content_type=None)


@app.get("/api/telemetry/schedules")
async def get_telemetry_schedules(request: Request):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _telemetry_call("GET", f"/api/telemetry/schedules/{user}")


@app.put("/api/telemetry/schedules")
async def put_telemetry_schedule(request: Request):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    return await _telemetry_call("PUT", f"/api/telemetry/schedules/{user}", json=body)


@app.delete("/api/telemetry/schedules/{job_id}")
async def delete_telemetry_schedule(request: Request, job_id: str):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _telemetry_call("DELETE", f"/api/telemetry/schedules/{user}/{job_id}")


@app.post("/api/telemetry/reports/request")
async def request_telemetry_report(request: Request):
    """Queue an on-demand report. It runs when Alpaca is free, not immediately."""
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    body["user"] = user
    return await _telemetry_call("POST", "/api/telemetry/reports/request", json=body)


@app.get("/api/telemetry/reports")
async def get_telemetry_reports(
    request: Request,
    type: str | None = None,
    period: str | None = None,
    limit: int = 20,
):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    params = {"limit": limit}
    if type:
        params["type"] = type
    if period:
        params["period"] = period
    return await _telemetry_call("GET", f"/api/telemetry/reports/{user}", params=params)


@app.get("/api/telemetry/reports/latest")
async def get_latest_telemetry_report(request: Request, type: str = "health", period: str = "any"):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _telemetry_call(
        "GET",
        f"/api/telemetry/reports/{user}/latest",
        params={"type": type, "period": period},
    )


@app.get("/api/telemetry/notifications")
async def get_telemetry_notifications(request: Request, limit: int = 20):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _telemetry_call(
        "GET", f"/api/telemetry/notifications/{user}", params={"limit": limit}
    )


@app.get("/api/telemetry/push/key")
async def get_push_key(request: Request):
    if not await _user_id_from_request(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _telemetry_call("GET", "/api/telemetry/push/key")


@app.post("/api/telemetry/push/subscribe")
async def subscribe_push(request: Request):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    body["user"] = user
    return await _telemetry_call("POST", "/api/telemetry/push/subscribe", json=body)


@app.post("/api/telemetry/push/unsubscribe")
async def unsubscribe_push(request: Request):
    user = await _user_id_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    body["user"] = user
    return await _telemetry_call("POST", "/api/telemetry/push/unsubscribe", json=body)


# ---------------------------------------------------------------------------
# Mobile App Over-The-Air (OTA) & APK In-App Updates
# ---------------------------------------------------------------------------
APP_UPDATES_DIR = Path(os.getenv("APP_UPDATES_DIR", "/app/data/app_updates"))
try:
    APP_UPDATES_DIR.mkdir(parents=True, exist_ok=True)
except OSError as _e:
    # Importing this module must not fail just because the container data path
    # is absent (tests and tooling import it outside Docker). The OTA endpoints
    # already treat a missing directory as "nothing published".
    log.warning(f"[AppUpdates] Could not create {APP_UPDATES_DIR}: {_e}")
UI_SVC = os.getenv("UI_SVC", "http://ui:8008")


def _normalize_version_meta(raw: dict) -> dict:
    """Accept both `git_sha` (build.js / publish API) and `gitSha` (Docker build arg)."""
    if not isinstance(raw, dict):
        return {}
    meta = dict(raw)
    if not meta.get("git_sha") and meta.get("gitSha"):
        meta["git_sha"] = meta["gitSha"]
    return meta


def _read_bundle_version(bundle_path: Path) -> dict:
    """Read version.json out of an OTA bundle.zip.

    The SHA a client ends up running is whatever is inside the bundle, so the
    bundle is the only trustworthy source for the version we advertise. Serving
    metadata that disagrees with the bundle makes the mobile app download,
    apply, restart, and find itself still "out of date" — an endless reload
    loop. See services/ui/src/lib/appUpdater.ts.
    """
    try:
        with zipfile.ZipFile(bundle_path) as zf:
            for name in ("version.json", "./version.json"):
                try:
                    return _normalize_version_meta(json.loads(zf.read(name)))
                except KeyError:
                    continue
            # Some packagers nest the app under a single top-level folder.
            for info in zf.infolist():
                if info.filename.count("/") == 1 and info.filename.endswith("/version.json"):
                    return _normalize_version_meta(json.loads(zf.read(info.filename)))
    except Exception as e:
        log.warning(f"[AppUpdates] Could not read version.json from {bundle_path}: {e}")
    return {}


@app.get("/api/app-updates/version")
async def get_app_update_version(request: Request):
    """Return version and bundle info for OTA live updates and native APK updates."""
    # 1. Published metadata on local disk (release notes, apk_version_code, ...)
    local_version_file = APP_UPDATES_DIR / "version.json"
    version_data = {}
    if local_version_file.exists():
        try:
            version_data = _normalize_version_meta(json.loads(local_version_file.read_text()))
        except Exception as e:
            log.warning(f"[AppUpdates] Failed reading local version.json: {e}")

    # 2. If not on local disk, fetch from UI service container
    if not version_data:
        try:
            async with shared_http_client() as client:
                resp = await client.get(f"{UI_SVC}/version.json", timeout=aiohttp.ClientTimeout(total=3.0))
                if resp.status == 200:
                    version_data = _normalize_version_meta(await resp.json(content_type=None))
        except Exception:
            pass

    # 3. Fallback defaults if not populated yet
    if not version_data:
        version_data = {
            "version": "1.2.0",
            "git_sha": os.getenv("GIT_SHA", "unknown"),
            "build_timestamp": datetime.now(timezone.utc).isoformat(),
            "release_notes": "Jarvis OS Over-The-Air Update",
        }

    # 4. The bundle we will actually serve is authoritative for git_sha. The
    #    published metadata is written by the deploy script from the server's
    #    git HEAD, which drifts from the SHA baked into the image being served.
    local_bundle = APP_UPDATES_DIR / "bundle.zip"
    bundle_available = local_bundle.exists()
    bundle_meta = _read_bundle_version(local_bundle) if bundle_available else {}

    if not bundle_available:
        # No staged bundle — the gateway proxies the UI container's copy, so the
        # UI container's own version.json describes what clients would receive.
        try:
            async with shared_http_client() as client:
                head = await client.head(f"{UI_SVC}/bundle.zip", timeout=aiohttp.ClientTimeout(total=3.0))
                bundle_available = head.status == 200
                if bundle_available:
                    vresp = await client.get(
                        f"{UI_SVC}/version.json", timeout=aiohttp.ClientTimeout(total=3.0)
                    )
                    if vresp.status == 200:
                        bundle_meta = _normalize_version_meta(await vresp.json(content_type=None))
        except Exception as e:
            log.warning(f"[AppUpdates] UI service bundle unavailable: {e}")
            bundle_available = False

    effective_sha = bundle_meta.get("git_sha") or "unknown"
    if bundle_available and effective_sha == "unknown":
        # We cannot prove what the bundle contains; advertising a guessed SHA is
        # what causes reload loops, so withhold the bundle instead.
        log.warning("[AppUpdates] Bundle present but its version.json is unreadable; not offering OTA.")
        bundle_available = False

    metadata_sha = version_data.get("git_sha", "unknown")
    if bundle_available and metadata_sha not in ("unknown", effective_sha):
        log.warning(
            f"[AppUpdates] Published metadata git_sha={metadata_sha} disagrees with "
            f"bundle git_sha={effective_sha}; advertising the bundle's SHA."
        )

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    if "sumemail.com" in host or request.headers.get("x-forwarded-ssl") == "on":
        scheme = "https"
    base_url = f"{scheme}://{host}"

    bundle_url = f"{base_url}/api/app-updates/bundle.zip"
    apk_file = APP_UPDATES_DIR / APK_FILENAME
    apk_available = apk_file.exists()
    apk_size = apk_file.stat().st_size if apk_available else 0
    apk_version_name = None

    # The APK we actually serve is authoritative for its own version code, for
    # the same reason the bundle is authoritative for git_sha above. Reading
    # this from build.gradle or from published metadata let the advertised
    # number drift ahead of the real artifact, so a user who installed the
    # newest APK on offer was told to update forever.
    apk_version_code = None
    apk_sha256 = None
    if apk_available:
        apk_info = read_apk_version(apk_file)
        if apk_info and apk_info.get("version_code") is not None:
            apk_version_code = apk_info["version_code"]
            apk_version_name = apk_info.get("version_name")
            # Published so the client can verify the download before it hands
            # the file to the system installer.
            apk_sha256 = read_apk_digest(apk_file)
            if apk_sha256 is None:
                # Without a digest we cannot prove what the user installs, so
                # withhold the APK rather than offer an unverifiable one.
                log.warning("[AppUpdates] APK present but unreadable for hashing; not offering it.")
                apk_available = False
                apk_size = 0
                apk_version_code = None
                apk_version_name = None
        else:
            # We cannot say what we are serving, and a wrong version code is
            # exactly what makes the notice un-clearable. Withhold it instead
            # of guessing -- an APK the user cannot install is better than one
            # we misdescribe.
            log.warning(
                "[AppUpdates] APK present but its manifest version is unreadable; not offering it."
            )
            apk_available = False
            apk_size = 0

    return {
        "version": bundle_meta.get("version") or version_data.get("version", "1.2.0"),
        "git_sha": effective_sha,
        "build_timestamp": bundle_meta.get("build_timestamp") or version_data.get("build_timestamp"),
        "release_notes": version_data.get("release_notes", "Jarvis OS live update"),
        "bundle_url": bundle_url,
        "bundle_available": bundle_available,
        "apk_available": apk_available,
        "apk_url": f"{base_url}/api/app-updates/{APK_FILENAME}" if apk_available else None,
        "apk_size_bytes": apk_size,
        # None when no APK is published -- never a placeholder. The client
        # treats a missing code as "cannot tell", not "up to date".
        "apk_version_code": apk_version_code,
        "apk_version_name": apk_version_name,
        # The digest of the file at apk_url, so the client can verify what it
        # downloaded before installing it. Null whenever no APK is offered.
        "apk_sha256": apk_sha256,
    }


@app.api_route("/api/app-updates/bundle.zip", methods=["GET", "HEAD"])
async def get_app_update_bundle():
    """Stream or serve the OTA web bundle zip for live in-app updating."""
    local_bundle = APP_UPDATES_DIR / "bundle.zip"
    if local_bundle.exists():
        return FileResponse(
            str(local_bundle),
            media_type="application/zip",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            filename="bundle.zip",
        )

    try:
        async with shared_http_client() as client:
            resp = await client.get(f"{UI_SVC}/bundle.zip", timeout=aiohttp.ClientTimeout(total=20.0))
            if resp.status == 200:
                content = await resp.read()
                return Response(
                    content=content,
                    media_type="application/zip",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Content-Disposition": "attachment; filename=bundle.zip",
                    },
                )
    except Exception as e:
        log.error(f"[AppUpdates] Failed to fetch bundle from UI service: {e}")

    raise HTTPException(status_code=404, detail="Update bundle not found")


# The published artifact is the *release* build (the workflow publishes
# app-release.apk, which has no `debuggable` flag). It used to be stored and
# served as "app-debug.apk", which was simply wrong and misled anyone auditing
# what the server was shipping. LEGACY_APK_NAME is kept only so a stale client
# still resolves to the current file.
APK_FILENAME = "jarvis-os.apk"
LEGACY_APK_NAME = "app-debug.apk"


@app.api_route(f"/api/app-updates/{APK_FILENAME}", methods=["GET", "HEAD"])
async def get_app_apk():
    """Serve the published release APK for in-app native installation."""
    apk_file = APP_UPDATES_DIR / APK_FILENAME
    if not apk_file.exists():
        raise HTTPException(status_code=404, detail="No APK build currently available on server")
    return FileResponse(
        str(apk_file),
        media_type="application/vnd.android.package-archive",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Content-Disposition": f"attachment; filename={APK_FILENAME}",
        },
        filename=APK_FILENAME,
    )


@app.api_route(f"/api/app-updates/{LEGACY_APK_NAME}", methods=["GET", "HEAD"])
async def get_app_apk_legacy():
    """Redirect the old app-debug.apk path to the real filename.

    An installed app reads the URL from the version endpoint, so this only
    matters for a stale client or an old bookmark -- but a 404 there would look
    like a broken update, which is the exact confusion this rename removes.
    """
    return RedirectResponse(url=f"/api/app-updates/{APK_FILENAME}", status_code=307)


def _write_temp_apk(content: bytes):
    """Stage an upload beside the live APK so it can be validated first."""
    tmp = APP_UPDATES_DIR / f"{APK_FILENAME}.incoming"
    tmp.write_bytes(content)
    return tmp


@app.post("/api/app-updates/publish")
async def publish_app_update(request: Request):
    """Publish a new bundle.zip and/or the release APK and update metadata.

    An uploaded APK is validated before it replaces the live one, and the
    advertised `apk_version_code` is read from that APK rather than trusted
    from the request -- see apk_manifest.py for why.
    """
    form = await request.form()
    secret = request.headers.get("X-Internal-Secret") or form.get("secret")
    if secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    version = form.get("version", "1.2.0")
    git_sha = form.get("git_sha", "unknown")
    notes = form.get("release_notes", "Jarvis OS update")

    bundle_file = form.get("bundle")
    if isinstance(bundle_file, UploadFile) and bundle_file.filename:
        out_b = APP_UPDATES_DIR / "bundle.zip"
        content = await bundle_file.read()
        out_b.write_bytes(content)
        log.info(f"[AppUpdates] Published new bundle.zip ({len(content)} bytes)")

    apk_file = form.get("apk")
    apk_published = False
    if isinstance(apk_file, UploadFile) and apk_file.filename:
        out_a = APP_UPDATES_DIR / APK_FILENAME
        content = await apk_file.read()
        # Validate before replacing the live APK. Writing first would mean a
        # truncated or corrupt upload destroys the build currently being
        # served, turning a bad CI publish into an outage for every user.
        try:
            probe = read_apk_version(_write_temp_apk(content))
        except Exception:
            probe = None
        if not probe or probe.get("version_code") is None:
            raise HTTPException(
                status_code=400,
                detail="Uploaded APK has an unreadable AndroidManifest versionCode; refusing to publish it.",
            )
        out_a.write_bytes(content)
        log.info(
            f"[AppUpdates] Published new {APK_FILENAME} ({len(content)} bytes, "
            f"versionCode {probe['version_code']})"
        )
        apk_published = True

    # Take the version code from the APK we just wrote, so the advertised
    # number always describes the artifact clients can actually download. A
    # caller-supplied code is only ever a cross-check.
    apk_code = None
    if apk_published:
        clear_apk_version_cache()
        info = read_apk_version(APP_UPDATES_DIR / APK_FILENAME)
        if info and info.get("version_code") is not None:
            apk_code = info["version_code"]
            claimed = form.get("apk_version_code")
            if claimed is not None and str(claimed).isdigit() and int(claimed) != apk_code:
                log.warning(
                    f"[AppUpdates] Publish claimed apk_version_code={claimed} but the "
                    f"uploaded APK is versionCode {apk_code}. Advertising the APK's own code."
                )
        else:
            # Refuse to advertise a build we cannot read -- a wrong code here is
            # what made a correctly-installed app look permanently out of date.
            raise HTTPException(
                status_code=400,
                detail="Uploaded APK has an unreadable AndroidManifest versionCode; refusing to publish it.",
            )
    else:
        # Bundle-only publish: keep whatever the currently published APK says.
        existing = read_apk_version(APP_UPDATES_DIR / APK_FILENAME)
        if existing and existing.get("version_code") is not None:
            apk_code = existing["version_code"]

    meta = {
        "version": str(version),
        "git_sha": str(git_sha),
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "release_notes": str(notes),
    }
    if apk_code is not None:
        meta["apk_version_code"] = apk_code
    (APP_UPDATES_DIR / "version.json").write_text(json.dumps(meta, indent=2))
    return {"status": "ok", "metadata": meta}


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

    async with shared_http_client() as client:
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/stt/transcribe",
            files={"file": (audio_file.filename, audio_file.file, "audio/wav")},
            data={"model": model, "language": language},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=30.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="STT service unavailable")


@app.post("/api/voice/command")
async def execute_voice_command(request: Request):
    """Route voice command to execution service."""
    body = await request.json()
    async with shared_http_client() as client:
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/voice/command",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
            , timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status == 200:
            return await resp.json()
    raise HTTPException(status_code=502, detail="Voice command service unavailable")


@app.get("/api/media/music-assistant/playlists")
async def get_ma_playlists(request: Request):
    """Get Music Assistant playlists (per-user credentials)."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [media/playlists]
    # BUG-21: an unreachable execution service is a gateway upstream failure,
    # not a 500 — report it explicitly so the UI can distinguish it from empty.
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/media/music-assistant/playlists",
                params={"user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status == 200:
                return await resp.json()
        return {"status": "SUCCESS", "playlists": []}
    except (TimeoutError, aiohttp.ClientError) as e:
        log.error(f"[media/playlists] upstream unavailable ({type(e).__name__}): {e}")
        return JSONResponse(status_code=502, content={"status": "ERROR", "error": str(e)})


@app.get("/api/media/music-assistant/recent")
async def get_ma_recent(request: Request):
    """Get Music Assistant recently played items (per-user credentials)."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [media/recent]
    # BUG-21: unreachable execution service → 502 {status:ERROR}, not a 500.
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/media/music-assistant/recent",
                params={"user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status == 200:
                return await resp.json()
        return {"status": "SUCCESS", "recent": []}
    except (TimeoutError, aiohttp.ClientError) as e:
        log.error(f"[media/recent] upstream unavailable ({type(e).__name__}): {e}")
        return JSONResponse(status_code=502, content={"status": "ERROR", "error": str(e)})


@app.get("/api/media/music-assistant/browse")
async def get_ma_browse(request: Request, media_type: str = "TRACKS", offset: int = 0, limit: int = 50, search: str = "", order_by: str = ""):
    """Browse MA library (tracks, albums, artists, playlists, radio) via HA."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [ma/browse]
    # BUG-21: unreachable execution service → 502 {status:ERROR}, not a 500.
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/media/music-assistant/browse",
                params={"user_id": creds.get("user") or "", "media_type": media_type, "offset": offset, "limit": limit, "search": search, "order_by": order_by},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status == 200:
                return await resp.json()
        return {"status": "SUCCESS", "items": []}
    except (TimeoutError, aiohttp.ClientError) as e:
        log.error(f"[ma/browse] upstream unavailable ({type(e).__name__}): {e}")
        return JSONResponse(status_code=502, content={"status": "ERROR", "error": str(e)})


@app.get("/api/media/music-assistant/search")
async def search_ma(request: Request, query: str = "", media_type: str = "", limit: int = 20, artist: str = "", album: str = "", library_only: bool = True):
    """Search MA for media items via HA."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [ma/search]
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/media/music-assistant/search",
                params={"user_id": creds.get("user") or "", "query": query, "media_type": media_type, "limit": limit, "artist": artist, "album": album, "library_only": str(library_only)},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=15.0),
            )
            if resp.status == 200:
                return await resp.json()
        return {"status": "SUCCESS", "results": []}
    except (TimeoutError, aiohttp.ClientError) as e:
        log.warning(f"[ma/search] upstream unavailable ({type(e).__name__}), returning empty results")
        return {"status": "SUCCESS", "results": []}


@app.get("/api/media/audiobookshelf/libraries")
async def get_abs_libraries(request: Request):
    """Get Audiobookshelf libraries (per-user credentials)."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [abs/libraries]
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "libraries", "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=ABS_TIMEOUT),
            )
            if resp.status == 200:
                data = await resp.json()
                detail = data.get("detail") or {}
                # BUG-15: key presence means execution answered; [] libraries is valid.
                if "libraries" in detail:
                    # Normalize 'type' → 'media_type' for UI compatibility
                    libs = detail["libraries"] or []
                    return {
                        "status": "SUCCESS",
                        "libraries": [
                            {**lib, "media_type": lib.get("media_type") or lib.get("type") or lib.get("media_type", "audiobook")}
                            for lib in libs
                        ],
                    }
    except (TimeoutError, aiohttp.ClientConnectionError) as e:
        log.warning(f"[abs/libraries] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/libraries] ABS error: {e}")
    return {"status": "SUCCESS", "libraries": [], "notice": "ABS unavailable"}


@app.get("/api/media/audiobookshelf/last-played")
async def get_abs_last_played(request: Request):
    """Get Audiobookshelf last played books (per-user credentials)."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [abs/last-played]
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "last_played", "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=ABS_TIMEOUT),
            )
            if resp.status == 200:
                data = await resp.json()
                detail = data.get("detail") or {}
                # BUG-15: key presence (not a non-empty list) means execution answered.
                if "books" in detail:
                    return {"status": "SUCCESS", "books": detail["books"]}
    except (TimeoutError, aiohttp.ClientConnectionError) as e:
        log.warning(f"[abs/last-played] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/last-played] ABS error: {e}")
    return {"status": "SUCCESS", "books": [], "notice": "ABS unavailable"}


@app.get("/api/media/audiobookshelf/library/{library_id}")
async def get_abs_library_items(library_id: str, request: Request, limit: int = 50):
    """Get audiobooks from a specific Audiobookshelf library (per-user credentials)."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [abs/library]
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "list", "library_id": library_id, "limit": limit, "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=ABS_TIMEOUT),
            )
            if resp.status == 200:
                data = await resp.json()
                detail = data.get("detail") or {}
                # BUG-15: an empty library is a valid answer, not "ABS unavailable".
                if "books" in detail:
                    return {"status": "SUCCESS", "books": detail["books"]}
    except (TimeoutError, aiohttp.ClientConnectionError) as e:
        log.warning(f"[abs/library] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/library] ABS error: {e}")
    return {"status": "SUCCESS", "books": [], "notice": "ABS unavailable"}


@app.get("/api/media/audiobookshelf/search")
async def search_abs(q: str, request: Request, limit: int = 20):
    """Search Audiobookshelf for books, podcasts, and authors (per-user credentials)."""
    # An unproven caller is a 401, never an empty 200: swallowing the auth
    # failure here is what let an anonymous caller read this data.
    creds = await _resolve_identity_from_request(request)  # [abs/search]
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"action": "search", "query": q, "limit": limit, "user_id": creds.get("user") or ""},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
                , timeout=aiohttp.ClientTimeout(total=ABS_TIMEOUT),
            )
            if resp.status == 200:
                data = await resp.json()
                detail = data.get("detail") or {}
                books = detail.get("books", [])
                podcasts = detail.get("podcasts", [])
                authors = detail.get("authors", [])
                total = detail.get("total", len(books) + len(podcasts) + len(authors))
                return {
                    "status": "SUCCESS",
                    "books": books,
                    "podcasts": podcasts,
                    "authors": authors,
                    "total": total,
                }
    except (TimeoutError, aiohttp.ClientConnectionError) as e:
        log.warning(f"[abs/search] ABS timeout: {e}")
    except Exception as e:
        log.warning(f"[abs/search] ABS error: {e}")
    return {"status": "SUCCESS", "books": [], "podcasts": [], "authors": [], "total": 0, "notice": "ABS unavailable"}


# ─── ABS connectivity status ─────────────────────────────────────────────

@app.get("/api/media/audiobookshelf/status")
async def get_abs_status(request: Request):
    """Check ABS connectivity by pinging the caller's own ABS instance.

    BUG-16: requires auth (resolves the caller's identity), uses the user's
    ``audiobookshelf_url`` instead of the global settings list (which also
    left ``abs_url`` unbound on a non-200 settings response), and pings the
    real ``GET /ping`` route instead of the nonexistent ``/api/books``.
    """
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[abs/status] identity resolution failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication required") from e

    abs_url = (creds.get("audiobookshelf_url") or "").rstrip("/") if isinstance(creds, dict) else ""
    if not abs_url:
        return {"status": "UNAVAILABLE", "error": "ABS URL not configured", "reachable": False}
    try:
        async with shared_http_client() as client:
            resp = await client.get(f"{abs_url}/ping", timeout=aiohttp.ClientTimeout(total=ABS_TIMEOUT))
            if resp.status == 200:
                return {"status": "AVAILABLE", "url": abs_url, "reachable": True}
            return {"status": "ERROR", "url": abs_url, "reachable": False, "code": resp.status}
    except TimeoutError as e:
        log.warning(f"[abs/status] ABS timeout: {e}")
        return {"status": "UNREACHABLE", "error": "Connection timed out", "reachable": False}
    except aiohttp.ClientConnectionError as e:
        log.warning(f"[abs/status] ABS connect error: {e}")
        return {"status": "UNREACHABLE", "error": str(e), "reachable": False}
    except Exception as e:
        log.warning(f"[abs/status] ABS status check failed: {e}")
        return {"status": "ERROR", "error": str(e), "reachable": False}


# ─── Unified media endpoints (§7.5) ──────────────────────────────────────
#
# These endpoints normalize Music Assistant item mappings and Audiobookshelf
# payloads into the Pydantic models in ``services/gateway/media_models.py``.
# A failed upstream never fails the whole response: each service contributes
# what it can and names itself in ``errors`` (the UI's partial state, §4.5).

_MEDIA_UPSTREAM_TIMEOUT = 4.0
_MEDIA_CHILD_TIMEOUT = 8.0
_MEDIA_EXEC_TIMEOUT = 10.0

_MEDIA_LIBRARY_TABS = frozenset(
    {"tracks", "albums", "artists", "playlists", "radio", "podcasts", "audiobooks"}
)
_MEDIA_SEARCH_BUCKETS = ("tracks", "artists", "albums", "playlists", "audiobooks", "podcasts", "authors")

# MA command per detail media_type for the child listing. Verified live against
# MA (2026-10-05): the child commands take the numeric library item id plus
# ``provider_instance_id_or_domain`` -- not the ``provider`` key.
_MEDIA_CHILD_COMMANDS = {
    "album": "music/albums/album_tracks",
    "artist": "music/artists/artist_albums",
    "playlist": "music/playlists/playlist_tracks",
    "podcast": "music/podcasts/podcast_episodes",
}


class _MediaUpstreamError(Exception):
    """One media upstream (MA or ABS) is unavailable for this request."""


def _media_ma_item(raw: Any) -> dict:
    """Normalize a Music Assistant item mapping (or full item) to MediaItem kwargs.

    Recently-played endpoints wrap the item in ``media_item``; search and
    library endpoints return the item directly. ``image`` is a dict with a
    ``path`` on some providers and a plain string on others.
    """
    if not isinstance(raw, dict):
        return {"name": str(raw or "")}
    item = raw.get("media_item")
    if not isinstance(item, dict):
        item = raw
    artists = item.get("artists") or []
    artist = ""
    if isinstance(artists, list) and artists:
        first = artists[0]
        artist = first.get("name", "") if isinstance(first, dict) else str(first)
    elif isinstance(item.get("artist"), str):
        artist = item["artist"]
    album = item.get("album")
    if isinstance(album, dict):
        album = album.get("name") or ""
    elif not isinstance(album, str):
        album = ""
    image = item.get("image")
    if isinstance(image, dict):
        image = image.get("path") or ""
    elif not isinstance(image, str):
        image = ""
    duration = item.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    favorite = item.get("favorite")
    return {
        "uri": item.get("uri") or "",
        "name": item.get("name") or item.get("title") or "",
        "media_type": str(item.get("media_type") or item.get("type") or "").lower(),
        "artist": artist,
        "album": album or "",
        "image": image or "",
        "duration": duration,
        "version": str(item.get("version") or ""),
        "favorite": favorite if isinstance(favorite, bool) else None,
    }


def _media_ma_children(raw: Any) -> list[dict]:
    out = []
    for entry in _media_ma_list(raw):
        item = _media_ma_item(entry)
        item.pop("favorite", None)
        item.pop("artist", None)
        item.pop("album", None)
        item.pop("version", None)
        item["index"] = entry.get("disc_number") or entry.get("track_number") if isinstance(entry, dict) else None
        out.append(item)
    return out


def _media_ma_list(result: Any) -> list[Any]:
    """Flatten MA library results: a bare list, an ``items`` dict, or buckets."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        items = result.get("items")
        if isinstance(items, list):
            return items
        out: list[Any] = []
        for value in result.values():
            if isinstance(value, list):
                out.extend(value)
        return out
    return []


async def _media_ma_rpc(
    request: Request,
    command: str,
    args: dict | None = None,
    *,
    timeout: float = _MEDIA_UPSTREAM_TIMEOUT,
) -> Any:
    """Run one MA JSON-RPC command for the caller; raise ``_MediaUpstreamError``."""
    mass_url, mass_token = await _resolve_ma_credentials(request)
    if not mass_url or not mass_token:
        raise _MediaUpstreamError("Music Assistant is not configured")
    try:
        return await _ma_rpc(mass_url, mass_token, command, args, timeout=timeout)
    except HTTPException as e:
        raise _MediaUpstreamError(f"Music Assistant returned HTTP {e.status_code}") from e
    except (TimeoutError, aiohttp.ClientError) as e:
        raise _MediaUpstreamError(f"Music Assistant is unreachable: {type(e).__name__}") from e


async def _media_ma_items(request: Request, command: str, args: dict | None = None, *, timeout: float = _MEDIA_UPSTREAM_TIMEOUT) -> list[dict]:
    result = await _media_ma_rpc(request, command, args, timeout=timeout)
    return [_media_ma_item(entry) for entry in _media_ma_list(result)]


async def _media_exec_audiobookshelf(creds: Any, params: dict, *, timeout: float = _MEDIA_EXEC_TIMEOUT) -> dict:
    """Fetch an ABS action through the execution service; return ``detail``.

    ABS talks to the upstream with the *user's* credentials, which only the
    execution service holds the client for, so the gateway proxies rather than
    duplicating that client (mirrors the audiobookshelf routes above).
    """
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/execute/audiobookshelf",
                params={"user_id": creds.get("user") or "", **params},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
            if resp.status != 200:
                raise _MediaUpstreamError(f"Audiobookshelf is unreachable (execution HTTP {resp.status})")
            data = await resp.json()
    except (TimeoutError, aiohttp.ClientError) as e:
        raise _MediaUpstreamError(f"Audiobookshelf is unreachable: {type(e).__name__}") from e
    detail = data.get("detail") or {}
    if data.get("status") == "FAILURE":
        raise _MediaUpstreamError(data.get("message") or "Audiobookshelf request failed")
    return detail


def _media_abs_book_item(book: dict) -> dict:
    """Normalize an ABS last-played/list book to MediaItem kwargs."""
    return {
        "uri": f"abs://{book.get('id') or ''}",
        "name": book.get("title") or "",
        "media_type": "audiobook",
        "artist": book.get("author") or "",
        "album": book.get("series") or "",
        "image": book.get("cover_path") or "",
        "duration": book.get("duration"),
        "favorite": None,
    }


@app.get("/api/media/home")
async def get_media_home(request: Request):
    """One call for Listen Now: MA shelves + ABS last played, 4s each, partial."""
    creds = await _resolve_identity_from_request(request)
    errors = MediaErrorInfo()

    async def shelf(name: str, command: str, args: dict) -> tuple[str, list[dict]]:
        try:
            return name, await _media_ma_items(request, command, args)
        except _MediaUpstreamError as e:
            if errors.ma is None:
                errors.ma = str(e)
            return name, []

    async def abs_recent() -> list[dict]:
        try:
            detail = await _media_exec_audiobookshelf(creds, {"action": "last_played", "limit": 20})
        except _MediaUpstreamError as e:
            errors.abs = str(e)
            return []
        return [_media_abs_book_item(b) for b in detail.get("books") or []]

    results = await asyncio.gather(
        shelf("recent", "music/recently_played_items", {"limit": 20}),
        shelf("continue", "music/in_progress_items", {"limit": 20}),
        shelf("playlists", "music/playlists/library_items", {"limit": 20}),
        # Live-verified 2026-10-05: the library_items family accepts favorite=true.
        shelf("favorites", "music/tracks/library_items", {"limit": 20, "favorite": True}),
        shelf("radio", "music/radios/library_items", {"limit": 20}),
        abs_recent(),
    )
    shelves = {name: items for name, items in results[:-1]}
    payload = {
        "recent": shelves["recent"] + results[-1],
        "continue": shelves["continue"],
        "playlists": shelves["playlists"],
        "favorites": shelves["favorites"],
        "radio": shelves["radio"],
        "errors": errors,
    }
    return MediaHomeResponse(**payload).model_dump(by_alias=True)


@app.get("/api/media/search")
async def search_media(request: Request, q: str = "", types: str = "", limit: int = 20):
    """Unified MA + ABS search (§7.5); each side fails independently."""
    query = (q or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="q is required")
    creds = await _resolve_identity_from_request(request)
    limit = max(1, min(limit, 50))
    wanted = {t.strip().lower() for t in (types or "").split(",") if t.strip()}
    errors = MediaErrorInfo()
    buckets: dict[str, list[dict]] = {key: [] for key in _MEDIA_SEARCH_BUCKETS}

    async def ma_search() -> dict:
        args: dict = {"search_query": query, "limit": limit, "config": {"providers": ["library"]}}
        if wanted:
            args["media_type"] = sorted(wanted)
        return await _media_ma_rpc(request, "music/search", args, timeout=_MEDIA_EXEC_TIMEOUT)

    async def abs_search() -> dict:
        if wanted and not (wanted & {"audiobooks", "podcasts", "authors"}):
            return {}
        return await _media_exec_audiobookshelf(creds, {"action": "search", "query": query, "limit": limit})

    ma_result: Any = {}
    abs_detail: dict = {}
    ma_ok, abs_ok = await asyncio.gather(
        ma_search(),
        abs_search(),
        return_exceptions=True,
    )
    if isinstance(ma_ok, BaseException):
        errors.ma = str(ma_ok) if isinstance(ma_ok, _MediaUpstreamError) else f"Music Assistant is unreachable: {type(ma_ok).__name__}"
        ma_result = {}
    else:
        ma_result = ma_ok
    if isinstance(abs_ok, BaseException):
        errors.abs = str(abs_ok) if isinstance(abs_ok, _MediaUpstreamError) else f"Audiobookshelf is unreachable: {type(abs_ok).__name__}"
        abs_detail = {}
    else:
        abs_detail = abs_ok

    if isinstance(ma_result, dict):
        for key, value in ma_result.items():
            bucket = str(key).lower()
            if bucket in buckets and isinstance(value, list):
                buckets[bucket].extend(_media_ma_item(entry) for entry in value)
    if isinstance(abs_detail, dict):
        buckets["audiobooks"].extend(_media_abs_book_item(b) for b in abs_detail.get("books") or [])
        for podcast in abs_detail.get("podcasts") or []:
            buckets["podcasts"].append(
                {
                    "uri": f"abs://{podcast.get('id') or ''}",
                    "name": podcast.get("title") or "",
                    "media_type": "podcast",
                    "artist": podcast.get("author") or "",
                    "image": podcast.get("cover") or "",
                }
            )
        for author in abs_detail.get("authors") or []:
            buckets["authors"].append(
                {
                    "uri": f"abs://author/{author.get('id') or ''}",
                    "name": author.get("name") or "",
                    "media_type": "author",
                }
            )

    top = None
    for key in ("tracks", "albums", "artists", "playlists", "audiobooks", "podcasts"):
        for item in buckets[key]:
            if (item.get("name") or "").casefold() == query.casefold():
                top = item
                break
        if top:
            break
    if top is None and buckets["tracks"]:
        top = buckets["tracks"][0]

    return MediaSearchResponse(top=top, errors=errors, **buckets).model_dump(by_alias=True)


async def _media_abs_item_detail(creds: Any, item_id: str) -> dict:
    """ABS item detail via direct ``GET /api/items/{id}?expanded=1`` (P2-T29).

    The user's stored ABS API key is available on the resolved identity, and
    the imageproxy route already uses it the same way. A missing key fails
    fast -- ABS answers 401 and pretending otherwise would look like data loss.
    """
    abs_url = (creds.get("audiobookshelf_url") or "").rstrip("/")
    abs_key = creds.get("audiobookshelf_api_key") or ""
    if not abs_url:
        raise HTTPException(status_code=400, detail="Audiobookshelf is not configured")
    if not abs_key:
        raise HTTPException(status_code=400, detail="Audiobookshelf API key is not configured")
    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{abs_url}/api/items/{item_id}",
                params={"expanded": "1"},
                headers={"Authorization": f"Bearer {abs_key}"},
                timeout=aiohttp.ClientTimeout(total=_MEDIA_CHILD_TIMEOUT),
            )
            if resp.status == 404:
                raise HTTPException(status_code=404, detail="Audiobookshelf does not know this item")
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"Audiobookshelf returned HTTP {resp.status}")
            raw = await resp.json()
    except HTTPException:
        raise
    except (TimeoutError, aiohttp.ClientError) as e:
        raise HTTPException(status_code=502, detail=f"Audiobookshelf is unreachable: {type(e).__name__}") from e

    media = raw.get("media") or {}
    metadata = media.get("metadata") or {}
    episodes = media.get("episodes") or []
    is_podcast = bool(episodes)
    children = []
    if is_podcast:
        for episode in episodes:
            children.append(
                MediaItemChild(
                    uri=f"abs://{item_id}/{episode.get('id') or ''}",
                    name=episode.get("title") or "",
                    media_type="podcast_episode",
                    duration=episode.get("duration"),
                    index=episode.get("episode"),
                )
            )
    else:
        for index, chapter in enumerate(media.get("chapters") or []):
            children.append(
                MediaItemChild(
                    uri=f"abs://{item_id}#chapter-{index}",
                    name=chapter.get("title") or "",
                    media_type="chapter",
                    duration=chapter.get("duration"),
                    index=index,
                )
            )
    return MediaItemDetail(
        uri=f"abs://{item_id}",
        name=metadata.get("title") or "",
        media_type="podcast" if is_podcast else "audiobook",
        artist=metadata.get("authorName") or "",
        album=metadata.get("seriesName") or "",
        image=f"/api/items/{item_id}/cover",
        duration=media.get("duration"),
        description=metadata.get("description") or "",
        children=children,
    ).model_dump(by_alias=True)


@app.get("/api/media/item")
async def get_media_item(request: Request, uri: str = ""):
    """Album/artist/playlist/book/podcast detail with children (§7.5).

    ``abs://<item_id>`` addresses an Audiobookshelf item; every other URI is
    handed to Music Assistant's ``music/item_by_uri``.
    """
    uri = (uri or "").strip()
    if not uri:
        raise HTTPException(status_code=422, detail="uri is required")
    creds = await _resolve_identity_from_request(request)
    if uri.startswith("abs://"):
        return await _media_abs_item_detail(creds, uri[len("abs://"):])

    try:
        item = await _media_ma_rpc(request, "music/item_by_uri", {"uri": uri}, timeout=_MEDIA_CHILD_TIMEOUT)
    except _MediaUpstreamError as e:
        # An unreachable MA is 502; "MA does not know this URI" is the 404 below.
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not isinstance(item, dict) or not item.get("uri"):
        raise HTTPException(status_code=404, detail="Music Assistant does not know this item")

    errors = MediaErrorInfo()
    children: list[dict] = []
    media_type = str(item.get("media_type") or "").lower()
    command = _MEDIA_CHILD_COMMANDS.get(media_type)
    if command and item.get("item_id"):
        try:
            raw_children = await _media_ma_rpc(
                request,
                command,
                {
                    "item_id": str(item["item_id"]),
                    "provider_instance_id_or_domain": item.get("provider") or "library",
                },
                timeout=_MEDIA_CHILD_TIMEOUT,
            )
            children = _media_ma_children(raw_children)
        except _MediaUpstreamError as e:
            errors.ma = str(e)

    detail = _media_ma_item(item)
    detail.pop("favorite", None)
    return MediaItemDetail(children=[MediaItemChild(**c) for c in children], errors=errors, **detail).model_dump(by_alias=True)


@app.get("/api/media/library/{tab}")
async def get_media_library(tab: str, request: Request, offset: int = 0, limit: int = 50, order_by: str = "", library_id: str = ""):
    """Paginated library tab: MA ``*/library_items`` or ABS libraries/books."""
    tab = (tab or "").strip().lower()
    if tab not in _MEDIA_LIBRARY_TABS:
        raise HTTPException(status_code=422, detail=f"Unknown library tab '{tab}'")
    creds = await _resolve_identity_from_request(request)
    errors = MediaErrorInfo()
    limit = max(1, min(limit, 200))

    if tab == "audiobooks":
        try:
            if library_id:
                detail = await _media_exec_audiobookshelf(creds, {"action": "list", "library_id": library_id, "limit": min(limit, 50)}, timeout=ABS_TIMEOUT)
                items = [_media_abs_book_item(b) for b in detail.get("books") or []]
                return MediaLibraryResponse(tab=tab, items=items, offset=offset, limit=limit, errors=errors).model_dump(by_alias=True)
            detail = await _media_exec_audiobookshelf(creds, {"action": "libraries"}, timeout=ABS_TIMEOUT)
            libraries = [
                MediaLibrary(id=str(lib.get("id") or ""), name=lib.get("name") or "", media_type=lib.get("type") or lib.get("media_type") or "audiobook")
                for lib in detail.get("libraries") or []
            ]
            return MediaLibraryResponse(tab=tab, libraries=libraries, offset=offset, limit=limit, errors=errors).model_dump(by_alias=True)
        except _MediaUpstreamError as e:
            errors.abs = str(e)
            return MediaLibraryResponse(tab=tab, offset=offset, limit=limit, errors=errors).model_dump(by_alias=True)

    ma_tab = "radios" if tab == "radio" else tab
    args: dict = {"limit": limit, "offset": offset}
    if order_by:
        args["order_by"] = order_by
    try:
        items = await _media_ma_items(request, f"music/{ma_tab}/library_items", args, timeout=_MEDIA_EXEC_TIMEOUT)
    except _MediaUpstreamError as e:
        errors.ma = str(e)
        items = []
    return MediaLibraryResponse(tab=tab, items=items, offset=offset, limit=limit, errors=errors).model_dump(by_alias=True)


@app.get("/api/media/favorites")
async def get_media_favorites(request: Request, limit: int = 50):
    """Favorite items across MA media types (live-verified ``favorite=true``)."""
    await _resolve_identity_from_request(request)
    errors = MediaErrorInfo()
    limit = max(1, min(limit, 200))

    async def favorite(media_type: str) -> list[dict]:
        try:
            return await _media_ma_items(
                request,
                f"music/{media_type}/library_items",
                {"limit": limit, "favorite": True},
                timeout=_MEDIA_EXEC_TIMEOUT,
            )
        except _MediaUpstreamError as e:
            if errors.ma is None:
                errors.ma = str(e)
            return []

    tracks, albums, artists, playlists = await asyncio.gather(
        favorite("tracks"), favorite("albums"), favorite("artists"), favorite("playlists")
    )
    return MediaFavoritesResponse(items=tracks + albums + artists + playlists, errors=errors).model_dump(by_alias=True)


@app.post("/api/media/abs/progress")
async def post_abs_progress(req: AbsProgressRequest, request: Request):
    """Push playback progress for an ABS item/episode (§7.5, P2-T32).

    The UI calls this every 15s while an ABS item plays on any output and on
    pause/stop; the execution service owns the ABS credentials and PATCHes
    ``/api/me/progress/{id}[/{episode}]``.
    """
    payload = {
        "action": "update_progress",
        "item_id": req.item_id,
        "current_time": req.current_time,
        "duration": req.duration,
        "is_finished": req.is_finished,
    }
    if req.episode_id:
        payload["episode_id"] = req.episode_id
    return await _proxy_execution_with_identity(request, "/execute/audiobookshelf", payload=payload)


# ─── Execution service proxy routes (for UI access) ──────────────────────

async def _resolve_user_context(request: Request, body: dict) -> Any:
    """Resolve user context from the authenticated request only.

    Never trusts a client-supplied ``user_context`` (BUG-01): it is deleted
    from the incoming body so a caller can't impersonate another user or
    inject credentials (ha_url/ha_token) into upstream calls.
    """
    body.pop("user_context", None)

    # Resolve only from the authenticated request; never fall back to the
    # first user (BUG-02) — unauthenticated calls must fail with 401.
    try:
        creds_data = await _resolve_identity_from_request(request)
    except HTTPException as e:
        raise HTTPException(status_code=401, detail="Authentication required") from e
    except Exception as e:
        raise HTTPException(status_code=401, detail="Authentication required") from e
    if isinstance(creds_data, dict) and creds_data.get("user"):
        return creds_data
    raise HTTPException(status_code=401, detail="Authentication required")


# BUG-20: a transient ClientError can surface AFTER the execution service
# already performed the command (e.g. while reading the response), so retrying
# a non-idempotent command would run it twice on the player. Only known
# idempotent operations may go through the retry path; everything else
# (play/next/volume_up, ha_service, ABS actions, …) gets exactly one attempt.
_IDEMPOTENT_TRANSPORT_COMMANDS = frozenset({"pause", "volume_set", "seek"})
_IDEMPOTENT_EXECUTION_ENDPOINTS = frozenset({
    "/execute/media/status",
    "/execute/media/state/sync",
    "/execute/entity/search",
})


def _execution_request_is_idempotent(endpoint: str, body: dict) -> bool:
    """Whether proxying this request is safe to retry on a transient error."""
    if endpoint == "/execute/media/transport":
        return str(body.get("command") or "").lower() in _IDEMPOTENT_TRANSPORT_COMMANDS
    return endpoint in _IDEMPOTENT_EXECUTION_ENDPOINTS


async def _forward_execution_request(
    request: Request,
    endpoint: str,
    service_label: str,
    timeout: float = 60.0,
    transform_result: bool = False,
):
    """Unified proxy forwarding helper for execution service endpoints."""
    client = get_http_client()
    body = await request.json() if await request.body() else {}
    max_retries = 2 if _execution_request_is_idempotent(endpoint, body) else 0

    async def do_proxy():
        user_ctx = await _resolve_user_context(request, body)
        exec_body = {**body, "user_context": user_ctx}
        resp = await client.post(
            f"{EXECUTION_SVC}{endpoint}",
            json=exec_body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        if transform_result:
            data = await resp.json()
            if isinstance(data, dict):
                entities = data.get("detail", {}).get("entities", [])
                data["result"] = entities
            return JSONResponse(content=data, status_code=resp.status)
        return await _proxy_json_response(resp)

    try:
        return await retry_http_request(do_proxy, f"Execution service ({service_label})", max_retries=max_retries, base_delay=0.1)
    except (TimeoutError, aiohttp.ClientError) as e:
        log.error(f"Execution service unreachable for {service_label}: {e}")
        raise HTTPException(status_code=503, detail="Execution service unreachable") from e


@app.post("/execute/media/status")
async def proxy_media_status(request: Request):
    """Proxy media status requests from UI to execution service."""
    return await _forward_execution_request(request, "/execute/media/status", "media status", timeout=15.0)


@app.post("/execute/media/transport")
async def proxy_media_transport(request: Request):
    """Proxy media transport requests from UI to execution service."""
    return await _forward_execution_request(request, "/execute/media/transport", "media transport")


@app.post("/execute/media/play")
async def proxy_media_play(request: Request):
    """Proxy media play requests from UI to execution service."""
    return await _forward_execution_request(request, "/execute/media/play", "media play")


@app.post("/execute/media/state/sync")
async def proxy_media_state_sync(request: Request):
    """Proxy media state sync requests from UI to execution service."""
    return await _forward_execution_request(request, "/execute/media/state/sync", "media state sync")


@app.post("/execute/entity/search")
async def proxy_entity_search(request: Request):
    """Proxy entity search requests from UI to execution service."""
    return await _forward_execution_request(request, "/execute/entity/search", "entity search", transform_result=True)


@app.post("/execute/audiobookshelf")
async def proxy_audiobookshelf(request: Request):
    """Proxy audiobookshelf requests from UI to execution service."""
    return await _forward_execution_request(request, "/execute/audiobookshelf", "audiobookshelf")


@app.post("/execute/ha_service")
async def proxy_ha_service(request: Request):
    """Proxy Home Assistant service calls from the device-control widget to execution."""
    return await _forward_execution_request(request, "/execute/ha_service", "ha_service")


async def _resolve_abs_session_creds(request: Request) -> dict:
    """Auth (signed media token is authoritative) + identity for ABS session streams.

    ABS session routes (/public/session/:sid/track/:i and /hls/:sid/...) need no
    API key — the session id itself is the capability — so only the user's
    ABS URL matters. Mirrors the sibling MA stream route's auth semantics (§7.4).
    """
    creds = await _resolve_identity_from_media_token(request)
    if creds is None:
        try:
            creds = await _resolve_identity_from_request(request)
        except HTTPException as e:
            log.error(f"[stream/abs-session] Identity resolution failed: {e.detail}")
            raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}") from e
        except Exception as e:
            log.error(f"[stream/abs-session] Identity resolution crashed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error resolving identity") from e
    if not isinstance(creds, dict):
        creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
    return creds


# ABS writes a session's HLS playlist only once ffmpeg has probed the source
# and started emitting segments, so a freshly started session redirects to a
# URL that 404s for a while (a cold 8.8h audiobook took >25s live). Poll it
# instead of handing the device the 404; a warm transcode cache is instant.
ABS_PLAYLIST_POLL_INTERVAL = float(os.getenv("ABS_PLAYLIST_POLL_INTERVAL", "2.0"))
ABS_PLAYLIST_MAX_ATTEMPTS = int(os.getenv("ABS_PLAYLIST_MAX_ATTEMPTS", "45"))
ABS_PLAYLIST_READY_TIMEOUT = float(os.getenv("ABS_PLAYLIST_READY_TIMEOUT", "90"))

# The playlist itself appears *before* the first segment does. A TV, speaker or
# cast device that gets a 200 playlist and then a 404 on its very first segment
# treats the stream as fatally broken and gives up — no retry, no audio. So the
# first segment is polled too. A 1-byte Range keeps the probe cheap.
ABS_FIRST_SEGMENT_POLL_INTERVAL = float(os.getenv("ABS_FIRST_SEGMENT_POLL_INTERVAL", "1.0"))
ABS_FIRST_SEGMENT_MAX_ATTEMPTS = int(os.getenv("ABS_FIRST_SEGMENT_MAX_ATTEMPTS", "30"))


def _first_segment_uri(playlist: str) -> str | None:
    """The first media segment URI in an m3u8 playlist, or None.

    Comment lines (``#EXTINF``, ``#EXT-X-…``) carry the timing, not the media, so
    the first non-empty non-comment line is the segment players fetch first.
    """
    for line in playlist.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return None


async def _await_abs_first_segment(client: aiohttp.ClientSession, playlist_url: str, playlist: str) -> bool:
    """Poll the playlist's first segment until ABS has written it.

    Returns True when the device can safely be handed this playlist, False when
    the first segment never appeared. A device is only ever served a playlist
    whose first segment already resolves, so it cannot die on its initial
    request.

    Segments are host-relative to the playlist, so the URL is joined against the
    playlist URL rather than the configured ABS base — ABS serves HLS off the
    server origin, not under a router base path (see ``_abs_playlist_url``).
    """
    segment = _first_segment_uri(playlist)
    if not segment:
        # A playlist with no media segments has nothing to wait for. Serving it
        # is honest; reporting "still transcoding" would not be.
        log.warning("[stream/abs-session] Playlist contains no media segments; serving as-is")
        return True
    segment_url = urljoin(playlist_url, segment)
    headers = {
        # Ask for a single byte: enough to prove the segment exists without
        # pulling a multi-megabyte transcode segment per probe.
        "Range": "bytes=0-0",
        "User-Agent": "Mozilla/5.0 (compatible; JarvisOS/2.0; audio-proxy)",
    }
    for attempt in range(1, ABS_FIRST_SEGMENT_MAX_ATTEMPTS + 1):
        try:
            resp = await client.get(segment_url, headers=headers)
        except Exception as e:
            log.warning(
                f"[stream/abs-session] First-segment probe {attempt}/{ABS_FIRST_SEGMENT_MAX_ATTEMPTS} failed: {e}"
            )
        else:
            # 200/206 both mean ABS has the segment. Anything else (404 = still
            # transcoding, 5xx = upstream trouble) is worth another look.
            if resp.status in (200, 206):
                await resp.release()
                log.info(f"[stream/abs-session] First segment ready: {segment_url}")
                return True
            await resp.release()
            if resp.status != 404:
                log.warning(
                    f"[stream/abs-session] First segment {segment_url} returned {resp.status}; "
                    "treating as not ready"
                )
        if attempt < ABS_FIRST_SEGMENT_MAX_ATTEMPTS:
            await asyncio.sleep(ABS_FIRST_SEGMENT_POLL_INTERVAL)
    log.error(
        f"[stream/abs-session] First segment {segment_url} was still not ready after "
        f"{ABS_FIRST_SEGMENT_MAX_ATTEMPTS} attempts"
    )
    return False


def _rewrite_m3u8_segments(playlist: str, base: str, session_id: str, track_index: int, user: str, mt: str) -> str:
    """Rewrite ABS-host-relative segment URIs to this gateway's segment route.

    Devices fetch the segments without headers, so each rewritten URL carries
    the signed media token; the ABS host never reaches the device (§7.4).
    """
    suffix = f"?user={quote(user, safe='')}&mt={quote(mt, safe='')}" if mt else ""
    lines = []
    for line in playlist.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(line)
            continue
        lines.append(f"{base.rstrip('/')}/api/media/stream/abs-session/{session_id}/{track_index}/{quote(stripped, safe='')}{suffix}")
    return "\n".join(lines) + "\n"


def _abs_playlist_url(abs_url: str, location: str) -> str:
    """Resolve a session track's redirect target against the ABS origin.

    ABS redirects to ``/hls/:sid/output.m3u8`` at the server origin, which is
    *not* under a configured router base path (``/audiobookshelf``), so the
    location is joined to the origin only. An absolute Location (a CDN, or a
    LAN IP with no Caddy) is used verbatim.
    """
    if location.startswith(("http://", "https://")):
        return location
    parsed = urlsplit(abs_url)
    return f"{parsed.scheme}://{parsed.netloc}{location if location.startswith('/') else '/' + location}"


async def _await_abs_playlist(client: aiohttp.ClientSession, playlist_url: str):
    """Poll the HLS playlist until ABS has written it, or give up loudly.

    Returns the live ``aiohttp`` response, or ``None`` when the playlist is
    still missing after ``ABS_PLAYLIST_MAX_ATTEMPTS`` tries. Non-404 upstream
    errors are returned immediately so a real failure (401/500) is not masked
    by retrying.
    """
    for attempt in range(1, ABS_PLAYLIST_MAX_ATTEMPTS + 1):
        resp = await client.get(
            playlist_url,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JarvisOS/2.0; audio-proxy)"},
        )
        if resp.status != 404:
            return resp
        await resp.release()
        if attempt < ABS_PLAYLIST_MAX_ATTEMPTS:
            await asyncio.sleep(ABS_PLAYLIST_POLL_INTERVAL)
    return None


@app.get("/api/media/stream/abs-session/{session_id}/{track_index}")
async def stream_abs_session(session_id: str, track_index: int, request: Request):
    """Proxy an ABS 2.x playback-session track (m3u8 playlist) to a device.

    Live ABS removed /api/items/:id/stream; audio now comes from a session
    (POST /api/items/:id/play[/episode]) via /public/session/:sid/track/:i,
    which 302-redirects to an HLS playlist whose segment URIs are relative to
    the ABS host. Those URIs are rewritten to this endpoint's sibling segment
    route so all media bytes stay on the gateway domain (§7.4).

    The playlist appears only once ABS has started transcoding, so the
    redirect target is polled (see the ABS_PLAYLIST_* settings) instead of
    passing its 404 straight to the device.
    """
    log.info(f"[stream/abs-session] track request: session={session_id} track={track_index}")
    creds = await _resolve_abs_session_creds(request)
    abs_url = (creds.get("audiobookshelf_url") or "").rstrip("/")
    if not abs_url:
        log.error("[stream/abs-session] Audiobookshelf URL not configured in resolved credentials")
        raise HTTPException(status_code=400, detail="Audiobookshelf URL not configured")

    user = request.query_params.get("user") or creds.get("user") or creds.get("username") or ""
    mt = request.query_params.get("mt") or (sign(user)[0] if user else None)

    track_url = f"{abs_url}/public/session/{session_id}/track/{track_index}"
    playlist_url: str | None = None
    client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0, connect=15.0))
    try:
        resp = await client.get(
            track_url,
            allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JarvisOS/2.0; audio-proxy)"},
        )
    except Exception as e:
        log.error(f"[stream/abs-session] Track fetch failed: {e}", exc_info=True)
        await client.close()
        raise HTTPException(status_code=502, detail="Failed to connect to media source") from e

    if 300 <= resp.status < 400:
        location = resp.headers.get("Location")
        await resp.release()
        if not location:
            await client.close()
            log.error(f"[stream/abs-session] Session {session_id} track {track_index} redirected without a Location")
            return Response(
                content="Audiobookshelf redirected to the HLS playlist without a Location header.",
                status_code=502,
                media_type="text/plain",
            )
        playlist_url = _abs_playlist_url(abs_url, location)
        log.info(f"[stream/abs-session] Waiting for {playlist_url} (session={session_id} track={track_index})")
        try:
            resp = await _await_abs_playlist(client, playlist_url)
        except Exception as e:
            log.error(f"[stream/abs-session] Playlist fetch failed: {e}", exc_info=True)
            await client.close()
            raise HTTPException(status_code=502, detail="Failed to connect to media source") from e
        if resp is None:
            await client.close()
            log.error(
                f"[stream/abs-session] Playlist for session {session_id} track {track_index} "
                f"was not ready after {ABS_PLAYLIST_MAX_ATTEMPTS} attempts"
            )
            return Response(
                content=(
                    f"Audiobookshelf is still transcoding session {session_id} "
                    f"(the HLS playlist does not exist yet). Try again shortly."
                ),
                status_code=504,
                media_type="text/plain",
            )

    if resp.status >= 400:
        body = await resp.text()
        await resp.release()
        await client.close()
        log.info(f"[stream/abs-session] Upstream error {resp.status} for session {session_id} track {track_index}")
        return Response(content=body, status_code=resp.status, media_type="text/plain")

    ctype = resp.headers.get("Content-Type", "")
    if "mpegurl" in ctype.lower():
        body = await resp.text()
        await resp.release()
        # Wait for segment 0 *before* closing the session — returning the playlist
        # earlier only moves the 404 to the device, which treats it as fatal.
        if playlist_url is None:
            # ABS served the playlist without redirecting, so we never learned its
            # URL and cannot join segments against it. Serve what we have rather
            # than guessing a base — a wrong guess would rewrite the segments to
            # a URL that 404s.
            log.warning(
                f"[stream/abs-session] Session {session_id} track {track_index} returned a "
                "playlist without a redirect; skipping the first-segment readiness check"
            )
        else:
            if not await _await_abs_first_segment(client, playlist_url, body):
                await client.close()
                return Response(
                    content=(
                        f"Audiobookshelf is still transcoding session {session_id}; the first "
                        "audio segment does not exist yet. Try again shortly."
                    ),
                    status_code=504,
                    media_type="text/plain",
                )
        await client.close()
        base = str(request.base_url)
        rewritten = _rewrite_m3u8_segments(body, base, session_id, track_index, user, mt)
        return Response(
            content=rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache", "Accept-Ranges": "bytes"},
        )

    # Not a playlist (direct audio on some deployments): stream bytes through.
    async def stream_generator(cli, r):
        bytes_sent = 0
        try:
            async for chunk in r.content.iter_chunked(64 * 1024):
                try:
                    if await request.is_disconnected():
                        log.info(f"[stream/abs-session/generator] Client disconnected after {bytes_sent} bytes")
                        break
                except Exception:
                    pass
                yield chunk
                bytes_sent += len(chunk)
            log.info(f"[stream/abs-session/generator] Finished streaming {bytes_sent} bytes")
        finally:
            # The session outlives this request unless it is closed here. An
            # unclosed ClientSession keeps its connector (and its pool) alive
            # until the event loop is garbage collected, so a device pulling HLS
            # segments leaks one pool per segment for the life of the track.
            await r.release()
            await cli.close()

    response_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    for key in ("Content-Range", "Content-Length", "Content-Type"):
        val = resp.headers.get(key)
        if val:
            response_headers[key] = val
    return StreamingResponse(
        stream_generator(client, resp),
        status_code=resp.status,
        media_type=response_headers.get("Content-Type", "audio/mpeg"),
        headers=response_headers,
    )


@app.get("/api/media/stream/abs-session/{session_id}/{track_index}/{segment}")
async def stream_abs_session_segment(session_id: str, track_index: int, segment: str, request: Request):
    """Proxy one HLS segment of an ABS playback session from the ABS /hls route."""
    creds = await _resolve_abs_session_creds(request)
    abs_url = (creds.get("audiobookshelf_url") or "").rstrip("/")
    if not abs_url:
        log.error("[stream/abs-session] Audiobookshelf URL not configured in resolved credentials")
        raise HTTPException(status_code=400, detail="Audiobookshelf URL not configured")

    seg_url = f"{abs_url}/hls/{session_id}/{segment}"
    range_header = request.headers.get("range")
    client = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, connect=15.0),
    )
    try:
        req_headers = {"User-Agent": "Mozilla/5.0 (compatible; JarvisOS/2.0; audio-proxy)"}
        if range_header:
            req_headers["Range"] = range_header
        resp = await client.get(seg_url, headers=req_headers, allow_redirects=True)
    except Exception as e:
        log.error(f"[stream/abs-session] Segment fetch failed: {e}", exc_info=True)
        await client.close()
        raise HTTPException(status_code=502, detail="Failed to connect to media source") from e

    async def stream_generator(cli, r):
        bytes_sent = 0
        try:
            async for chunk in r.content.iter_chunked(64 * 1024):
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    pass
                yield chunk
                bytes_sent += len(chunk)
            log.info(f"[stream/abs-session/segment] Finished streaming {bytes_sent} bytes for {segment}")
        finally:
            # One of these runs per HLS segment; without the close each one
            # strands a ClientSession and its connector for the whole track.
            await r.release()
            await cli.close()

    response_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    for key in ("Content-Range", "Content-Length", "Content-Type"):
        val = resp.headers.get(key)
        if val:
            response_headers[key] = val
    return StreamingResponse(
        stream_generator(client, resp),
        status_code=resp.status,
        media_type=response_headers.get("Content-Type", "video/mp2t"),
        headers=response_headers,
    )


def _fix_sendspin_client_hello(msg: dict) -> dict:
    """Convert browser sendspin-js client/hello format to MA's expected format.

    @sendspin/sendspin-js sends supported_formats as string arrays like
    ['mp3', 'aac', 'opus'] but MA expects SupportedAudioFormat objects:
    {codec: 'opus', channels: 2, sample_rate: 48000, bit_depth: 16}

    MA only supports opus, flac, pcm codecs. Map/filter browser formats accordingly.
    """
    payload = msg.get("payload", {})
    player_support = payload.get("player@v1_support") or payload.get("player_support")

    if player_support:
        sf = player_support.get("supported_formats")
        if sf and isinstance(sf, list) and sf and isinstance(sf[0], str):
            log.info(f"[sendspin] Converting string supported_formats to SupportedAudioFormat objects: {sf}")
            fixed = []
            for fmt_str in sf:
                codec_lower = fmt_str.lower()
                if codec_lower == "wav":
                    codec_lower = "pcm"
                if codec_lower in ("opus", "flac", "pcm"):
                    fixed.append({"codec": codec_lower, "channels": 2, "sample_rate": 48000, "bit_depth": 16})
                elif codec_lower in ("mp3", "aac"):
                    # MA doesn't support mp3/aac in sendspin protocol - skip these
                    log.info(f"[sendspin] Skipping unsupported sendspin codec: {codec_lower}")
            if not fixed:
                log.warning("[sendspin] No valid sendspin codecs found, defaulting to opus")
                fixed = [{"codec": "opus", "channels": 2, "sample_rate": 48000, "bit_depth": 16}]
            player_support["supported_formats"] = fixed
            log.info(f"[sendspin] Fixed supported_formats: {fixed}")

        payload["player@v1_support"] = player_support
        if "player_support" in payload:
            del payload["player_support"]

    msg["payload"] = payload
    return msg


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
    from fastapi.websockets import WebSocketDisconnect
    from websockets.exceptions import InvalidStatus

    # Accept browser connection FIRST (required by FastAPI before any close())
    await websocket.accept()
    log.info("[sendspin] Browser connection accepted")

    # Extract API token from query params (WebSocket can't set headers)
    api_token = websocket.query_params.get("token")
    if not api_token:
        log.error("[sendspin] Browser connected with no token in query params")
        await websocket.close(code=1008, reason="Missing token")
        return

    log.info("[sendspin] Browser connection received (token redacted)")

    # Resolve MA credentials via identity service
    creds = None
    try:
        creds = await resolve_identity({"api_key": api_token})
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""
        log.info(f"[sendspin] Identity resolved: user={creds.get('user', 'unknown')}, mass_url={redact_url(mass_url)}, mass_token={'set' if mass_token else 'NOT SET'}")
    except HTTPException as e:
        log.error(f"[sendspin] Identity resolution HTTP error: status={e.status_code}, detail={e.detail}")
        await websocket.close(code=1008, reason="Authentication failed")
        return
    except Exception as e:
        log.error(f"[sendspin] Identity resolution failed: {type(e).__name__}: {e}", exc_info=True)
        await websocket.close(code=1011, reason="Identity service error")
        return

    if not mass_url or not mass_token:
        log.error(f"[sendspin] MA credentials not configured: mass_url={'set' if mass_url else 'NOT SET'}, mass_token={'set' if mass_token else 'NOT SET'}")
        await websocket.close(code=1008, reason="MA not configured")
        return

    # Build MA sendspin URL (NO query string token — auth via message)
    ma_scheme, ma_host, ma_port = _normalize_ma_url(mass_url)
    ws_scheme = "ws" if ma_scheme == "http" else "wss"
    ma_sendspin_url = f"{ws_scheme}://{ma_host}:{ma_port}/sendspin"
    log.info(f"[sendspin] Connecting to MA sendspin: {redact_url(ma_sendspin_url)}...")

    # Receive the first message from the browser (client/hello)
    log.info("[sendspin] STEP 1: Waiting for browser to send first message...")
    message = await websocket.receive()
    # Handle disconnect events from browser (e.g., SendspinPlayer timeout)
    msg_type = message.get("type")
    if msg_type == "websocket.disconnect":
        code = message.get("code", 1000)
        reason = message.get("reason", "")
        log.warning(f"[sendspin] Browser disconnected before sending hello (code={code}, reason='{reason}')")
        return
    first_text = message.get("text")
    first_bytes = message.get("bytes")
    if first_text is not None:
        first_data = first_text
    elif first_bytes is not None:
        first_data = first_bytes.decode("utf-8")
    else:
        first_data = None
    if first_data is None:
        log.error(f"[sendspin] First message from browser was empty: keys={list(message.keys())}")
        await websocket.close(code=1008, reason="Empty message")
        return

    first_msg = json.loads(first_data)
    client_id = first_msg.get("payload", {}).get("client_id", "") if first_msg.get("type") == "client/hello" else "unknown"
    log.info(f"[sendspin] STEP 2: Received {first_msg.get('type', 'unknown')} from browser (client_id={client_id}, full_msg={first_data[:500]})")
    if first_msg.get("type") == "client/hello" and client_id and client_id != "unknown":
        record_web_player_id(str(creds.get("user") or ""), str(client_id))

    ma_ws = None
    try:
        # Connect to MA's sendspin endpoint (no query string)
        log.info(f"[sendspin] STEP 3: Connecting to MA sendspin URL: {redact_url(ma_sendspin_url)}")
        ma_ws = await websockets.connect(ma_sendspin_url)
        log.info("[sendspin] STEP 4: WebSocket connected to MA")

        # MA sendspin requires {"type":"auth","token":"..."} as FIRST message
        auth_msg = json.dumps({"type": "auth", "token": mass_token})
        log.info("[sendspin] STEP 5: Sending auth to MA (token redacted)")
        await ma_ws.send(auth_msg)

        # MA responds to auth — expect server/hello
        log.info("[sendspin] STEP 6: Waiting for MA auth response...")
        try:
            auth_response = await asyncio.wait_for(ma_ws.recv(), timeout=10.0)
            log.info(f"[sendspin] STEP 7: MA auth response received: {auth_response[:500]!r}")
        except TimeoutError:
            log.error("[sendspin] STEP 6 FAILED: MA auth response timed out after 10s")
            await ma_ws.close()
            await websocket.close(code=1008, reason="MA auth timeout")
            return

        # Check if auth response is an error
        try:
            auth_json = json.loads(auth_response)
            if auth_json.get("type") in ("error", "auth/reject", "auth/failure"):
                log.error(f"[sendspin] MA auth REJECTED: {auth_json}")
                await ma_ws.close()
                await websocket.close(code=1008, reason="MA auth rejected")
                return
        except json.JSONDecodeError:
            pass  # Not JSON, likely a protocol message

        # Now forward the buffered client/hello to MA
        if first_msg.get("type") == "client/hello":
            log.info("[sendspin] STEP 8: Forwarding client/hello to MA")
            # MA's CommandMessage schema requires a message_id field
            # The browser's sendspin-js client doesn't include one, so we inject it
            import uuid as _uuid
            hello_msg = json.loads(first_data)
            hello_msg = _fix_sendspin_client_hello(hello_msg)
            hello_msg["message_id"] = _uuid.uuid4().hex
            log.info(f"[sendspin] client/hello with message_id={hello_msg['message_id']}")
            await ma_ws.send(json.dumps(hello_msg))

            # MA sends server/hello back — forward to browser
            log.info("[sendspin] STEP 9: Waiting for MA server/hello response...")
            try:
                hello_response = await asyncio.wait_for(ma_ws.recv(), timeout=10.0)
                log.info(f"[sendspin] STEP 10: MA server/hello received: {hello_response[:500]!r}")
            except TimeoutError:
                log.error("[sendspin] STEP 9 FAILED: MA server/hello timed out after 10s")
                await ma_ws.close()
                await websocket.send_text(json.dumps({"type": "error", "message": "MA did not respond to client/hello"}))
                return

            log.info("[sendspin] STEP 11: Forwarding server/hello to browser")
            try:
                hello_text = hello_response if isinstance(hello_response, str) else hello_response.decode(errors="replace")
                await websocket.send_text(hello_text)
                log.info("[sendspin] STEP 12: server/hello sent to browser successfully")
            except Exception as send_err:
                log.error(f"[sendspin] STEP 11 FAILED: Failed to send server/hello to browser: {send_err}", exc_info=True)
                await ma_ws.close()
                return
        else:
            log.warning(f"[sendspin] Unexpected first message type: {first_msg.get('type')}, msg={first_data[:200]}")
            await ma_ws.close()
            await websocket.close(code=4001, reason="Unexpected message type")
            return

        log.info("[sendspin] Handshake complete, starting proxy loop")

        client_goodbye_sent = asyncio.Event()
        proxy_done = asyncio.Event()

        async def forward_client_to_ma():
            """Forward browser messages to MA (handles both text and binary frames)."""
            log.info("[sendspin] STEP 13: Proxy loop started — browser→MA direction active")
            try:
                while True:
                    message = await websocket.receive()
                    text_data = message.get("text")
                    binary_data = message.get("bytes")
                    if text_data is None and binary_data is None:
                        log.info("[sendspin] Proxy: browser connection closed by remote")
                        break
                    if text_data is None and binary_data is not None:
                        try:
                            text_data = binary_data.decode("utf-8")
                            log.warning("[sendspin] Proxy: browser sent unexpected binary frame; decoding as UTF-8 text")
                        except Exception:
                            log.warning("[sendspin] Proxy: browser sent unexpected non-text frame; ignoring")
                            continue
                    try:
                        parsed = json.loads(text_data)
                        if parsed.get("type") == "client/goodbye":
                            log.info("[sendspin] Proxy: received client/goodbye from browser")
                            client_goodbye_sent.set()
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    log.info(f"[sendspin] Proxy: browser→MA ({len(text_data)} chars)")
                    await ma_ws.send(text_data)
            except WebSocketDisconnect:
                log.info("[sendspin] Proxy: Browser disconnected from gateway")
            except Exception as e:
                log.error(f"[sendspin] Proxy: Client→MA forward error: {e}", exc_info=True)
            finally:
                log.info("[sendspin] Proxy: browser→MA direction ended")
                proxy_done.set()

        async def forward_ma_to_client():
            """Forward MA messages to browser (handles both text and binary frames)."""
            log.info("[sendspin] STEP 13: Proxy loop started — MA→browser direction active")
            try:
                while True:
                    message = await ma_ws.recv()
                    if isinstance(message, str):
                        log.info(f"[sendspin] Proxy: MA→browser ({len(message)} chars, first 100='{message[:100]}')")
                        await websocket.send_text(message)
                    else:
                        log.info(f"[sendspin] Proxy: MA→browser ({len(message)} bytes binary)")
                        await websocket.send_bytes(message)
            except WebSocketDisconnect:
                log.info("[sendspin] Proxy: Browser disconnected from gateway (MA→browser)")
            except Exception as e:
                log.error(f"[sendspin] Proxy: MA→Client forward error: {e}", exc_info=True)
            finally:
                log.info("[sendspin] Proxy: MA→browser direction ended")

        async def graceful_disconnect():
            """Handle graceful disconnect: send goodbye to MA if not already sent."""
            try:
                await proxy_done.wait()
                if not client_goodbye_sent.is_set():
                    log.info("[sendspin] Proxy: browser did not send goodbye, sending client/goodbye to MA")
                    goodbye_msg = json.dumps({
                        "type": "client/goodbye",
                        "payload": {"reason": "shutdown"}
                    })
                    try:
                        await ma_ws.send(goodbye_msg)
                    except Exception as e:
                        log.error(f"[sendspin] Proxy: failed to send goodbye to MA: {e}", exc_info=True)
            except Exception as e:
                log.error(f"[sendspin] Proxy: graceful disconnect error: {e}", exc_info=True)
            finally:
                with suppress(Exception):
                    await ma_ws.close()

        # Run all three tasks in parallel
        log.info("[sendspin] STEP 14: Starting asyncio.gather for proxy loops")
        await asyncio.gather(
            forward_client_to_ma(),
            forward_ma_to_client(),
            graceful_disconnect(),
        )
        log.info("[sendspin] STEP 15: Proxy loops complete — both directions ended")
    except InvalidStatus as e:
        log.error(f"[sendspin] MA sendspin connection failed (status {e.response}): {e}", exc_info=True)
        with suppress(Exception):
            await websocket.close(code=1011, reason=f"MA connection failed: {e}")
    except websockets.exceptions.ConnectionClosed as e:
        log.error(f"[sendspin] MA sendspin connection closed: code={e.code}, reason={e.reason}", exc_info=True)
        with suppress(Exception):
            await websocket.close(code=1011, reason=f"MA connection closed: {e}")
    except websockets.exceptions.WebSocketException as e:
        log.error(f"[sendspin] MA sendspin WebSocket error: {type(e).__name__}: {e}", exc_info=True)
        with suppress(Exception):
            await websocket.close(code=1011, reason=f"MA WebSocket error: {e}")
    except Exception as e:
        log.error(f"[sendspin] Sendspin proxy error: {type(e).__name__}: {e}", exc_info=True)
        with suppress(Exception):
            await websocket.close(code=1011, reason=f"{type(e).__name__}: {e}")
    finally:
        if ma_ws:
            with suppress(Exception):
                await ma_ws.close()


@app.get("/api/ma-jsonrpc/debug/players")
async def debug_list_players(request: Request):
    """Debug endpoint: list all MA players (raw MA payload). Admin only."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    mass_url, mass_token = await _resolve_ma_credentials(request)
    if not mass_token:
        raise HTTPException(status_code=400, detail="MA token not configured")
    return {"result": await _ma_rpc(mass_url, mass_token, "players/all", message_id="debug_players")}


@app.get("/api/ma-jsonrpc/debug/queues")
async def debug_list_queues(request: Request):
    """Debug endpoint: list all MA queues (raw MA payload). Admin only."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    mass_url, mass_token = await _resolve_ma_credentials(request)
    if not mass_token:
        raise HTTPException(status_code=400, detail="MA token not configured")
    return {"result": await _ma_rpc(mass_url, mass_token, "player_queues/all", message_id="debug_queues")}


@app.get("/api/ma-jsonrpc/debug/player/{player_id}")
async def debug_get_player(request: Request, player_id: str):
    """Debug endpoint: get specific player info (raw MA payload). Admin only."""
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    mass_url, mass_token = await _resolve_ma_credentials(request)
    if not mass_token:
        raise HTTPException(status_code=400, detail="MA token not configured")
    result = await _ma_rpc(
        mass_url, mass_token, "players/get", {"player_id": player_id}, message_id="debug_player"
    )
    return {"result": result}


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
    - {"message_id": "counter1", "command": "players/play_media", "args": {"player_id": "...", "media": "mass://search?q=artist&limit=10"}}
    - {"message_id": "counter2", "command": "players/cmd_play", "args": {"player_id": "..."}}
    - {"message_id": "counter3", "command": "players/cmd_pause", "args": {"player_id": "..."}}
    - {"message_id": "counter4", "command": "players/cmd_seek", "args": {"player_id": "...", "position": 30}}

    MA responds with:
    - {"type": "RESULT", "message_id": "counter1", "result": {...}}
    - {"event": "queue_updated", "data": {...}}
    - {"event": "player_updated", "data": {...}}
    """
    import websockets
    from websockets.exceptions import InvalidStatus

    # Extract API token from query params
    await websocket.accept()
    log.info("[ma-jsonrpc] Browser WebSocket connection accepted")
    api_token = websocket.query_params.get("token")
    if not api_token:
        await websocket.close(code=1008, reason="Missing token")
        return

    # Resolve MA credentials via identity service
    log.info("[ma-jsonrpc] STEP 2: Resolving MA credentials...")
    try:
        creds = await resolve_identity({"api_key": api_token})
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
        mass_url = creds.get("mass_url") or ""
        mass_token = creds.get("mass_token") or ""
        log.info(f"[ma-jsonrpc] STEP 2 PASS: Identity resolved — user={creds.get('user', 'unknown')}")
    except HTTPException as e:
        log.error(f"[ma-jsonrpc] STEP 2 FAIL: Identity resolution HTTP error: status={e.status_code}, detail={e.detail}")
        await websocket.close(code=1008, reason="Authentication failed")
        return
    except Exception as e:
        log.error(f"[ma-jsonrpc] STEP 2 FAIL: Identity resolution failed: {e}")
        await websocket.close(code=1011, reason="Identity service error")
        return

    if not mass_url or not mass_token:
        log.error("[ma-jsonrpc] STEP 2 FAIL: MA credentials not configured")
        await websocket.close(code=1008, reason="MA not configured")
        return

    # Build MA JSON-RPC URL
    ma_scheme, ma_host, ma_port = _normalize_ma_url(mass_url)
    ws_scheme = "ws" if ma_scheme == "http" else "wss"
    ma_jsonrpc_url = f"{ws_scheme}://{ma_host}:{ma_port}/ws?token={mass_token}"
    log.info(f"[ma-jsonrpc] STEP 3: Connecting to MA JSON-RPC: {redact_url(ma_jsonrpc_url)[:100]}...")

    try:
        log.info("[ma-jsonrpc] STEP 4: Opening WebSocket to MA...")
        async with websockets.connect(
            ma_jsonrpc_url,
            ping_interval=15,
            ping_timeout=10,
        ) as ma_ws:
            log.info("[ma-jsonrpc] STEP 4 PASS: WebSocket connected to MA JSON-RPC")

            auth_msg = json.dumps({"message_id": "gateway-auth", "command": "auth", "args": {"token": mass_token}})
            log.info("[ma-jsonrpc] STEP 4.5: Sending auth to MA")
            await ma_ws.send(auth_msg)
            log.info("[ma-jsonrpc] STEP 4.6: Waiting for MA auth response...")
            try:
                auth_response = await asyncio.wait_for(ma_ws.recv(), timeout=10.0)
            except TimeoutError:
                log.error("[ma-jsonrpc] STEP 4.6 FAILED: MA auth response timed out after 10s")
                await websocket.close(code=1008, reason="MA auth timeout")
                return

            if isinstance(auth_response, str):
                log.info(f"[ma-jsonrpc] STEP 4.7: MA auth response received: {auth_response[:500]}")
                try:
                    auth_json = json.loads(auth_response)
                    if auth_json.get("type") in ("error", "auth/reject", "auth/failure") or auth_json.get("error_code") is not None:
                        log.error(f"[ma-jsonrpc] MA auth rejected: {auth_json}")
                        await websocket.close(code=1008, reason="MA auth rejected")
                        return
                except json.JSONDecodeError:
                    pass
                try:
                    await websocket.send_text(auth_response)
                    log.info("[ma-jsonrpc] STEP 4.8: Forwarded MA auth response to browser")
                except Exception as send_err:
                    log.error(f"[ma-jsonrpc] STEP 4.8 FAILED: Could not forward MA auth response: {send_err}", exc_info=True)
                    with suppress(Exception):
                        await websocket.close(code=1011, reason="Failed to forward MA auth response")
                    return
            else:
                log.info(f"[ma-jsonrpc] STEP 4.7: MA auth response received as binary ({len(auth_response)} bytes)")

            async def forward_client_to_ma():
                """Forward browser JSON-RPC commands to MA (allowlist-checked)."""
                from services.gateway.ma_allowlist import validate_ma_frame

                log.info("[ma-jsonrpc] STEP 5: Proxy loop started — browser→MA direction active")
                try:
                    while True:
                        msg = await websocket.receive()
                        text_data = msg.get("text")
                        binary_data = msg.get("bytes")
                        if text_data is None and binary_data is None:
                            log.info("[ma-jsonrpc] Proxy: browser connection closed")
                            break
                        if text_data is None and binary_data is not None:
                            try:
                                text_data = binary_data.decode("utf-8")
                                log.warning("[ma-jsonrpc] Proxy: browser sent unexpected binary frame; decoding as UTF-8 text")
                            except Exception:
                                log.warning("[ma-jsonrpc] Proxy: browser sent unexpected non-text frame; ignoring")
                                continue

                        forbidden = validate_ma_frame(text_data)
                        if forbidden is not None:
                            log.warning(
                                f"[ma-jsonrpc] Proxy: rejected non-allowlisted frame ({len(text_data)} chars)"
                            )
                            await websocket.send_text(forbidden)
                            continue
                        scope_error = await check_ma_frame_scope(
                            text_data,
                            user=str(creds.get("user") or ""),
                            is_admin=bool(creds.get("is_admin")),
                        )
                        if scope_error is not None:
                            log.warning(
                                f"[ma-jsonrpc] Proxy: rejected frame outside the caller's player scope ({len(text_data)} chars)"
                            )
                            await websocket.send_text(scope_error)
                            continue
                        log.info(f"[ma-jsonrpc] Proxy: browser→MA ({len(text_data)} chars)")
                        await ma_ws.send(text_data)
                except WebSocketDisconnect:
                    log.info("[ma-jsonrpc] Proxy: Browser disconnected from gateway")
                except Exception as e:
                    log.error(f"[ma-jsonrpc] Proxy: Client→MA forward error: {e}", exc_info=True)
                finally:
                    log.info("[ma-jsonrpc] Proxy: browser→MA direction ended")

            async def forward_ma_to_client():
                """Forward MA events/responses to browser."""
                log.info("[ma-jsonrpc] STEP 5: Proxy loop started — MA→browser direction active")
                try:
                    while True:
                        message = await ma_ws.recv()
                        if isinstance(message, str):
                            log.info(f"[ma-jsonrpc] Proxy: MA→browser ({len(message)} chars, first 100='{message[:100]}')")
                            await websocket.send_text(message)
                        else:
                            log.info(f"[ma-jsonrpc] Proxy: MA→browser ({len(message)} bytes binary)")
                            await websocket.send_bytes(message)
                except WebSocketDisconnect:
                    # Browser closed the tab/moved on — expected, not an error.
                    log.info("[ma-jsonrpc] Proxy: Browser disconnected from gateway (MA→browser)")
                except Exception as e:
                    log.error(f"[ma-jsonrpc] Proxy: MA→Client forward error: {e}", exc_info=True)
                finally:
                    log.info("[ma-jsonrpc] Proxy: MA→browser direction ended")

            # Run both directions in parallel
            log.info("[ma-jsonrpc] STEP 6: Starting asyncio.gather for proxy loops")
            await asyncio.gather(
                forward_client_to_ma(),
                forward_ma_to_client(),
            )
            log.info("[ma-jsonrpc] STEP 7: Proxy loops complete — both directions ended")
    except InvalidStatus as e:
        log.error(f"[ma-jsonrpc] MA JSON-RPC connection failed (status {e.response}): {e}")
        with suppress(Exception):
            await websocket.close(code=1011, reason=f"MA connection failed: {e}")
    except Exception as e:
        log.error(f"[ma-jsonrpc] JSON-RPC proxy error: {e}", exc_info=True)
        with suppress(Exception):
            await websocket.close(code=1011, reason=str(e))


# ── Music Assistant stream: start (POST) vs bytes (GET) — BUG-19 ──────────────
# Every GET (including Range/seek) used to re-run player_queues/play_media
# (option=replace), restarting the queue on each byte-range request. Starting
# playback is now an explicit POST; GET only ever fetches bytes — from the
# session cache, or by resolving an ALREADY-RUNNING queue (never play_media).

_MA_STREAM_CACHE: dict[str, tuple[float, str]] = {}
_MA_STREAM_CACHE_TTL = 7200.0  # seconds


def _ma_stream_cache_get(user: str, uri: str) -> str | None:
    key = f"{user}|{uri}"
    entry = _MA_STREAM_CACHE.get(key)
    if not entry:
        return None
    expires_at, url = entry
    if time.time() > expires_at:
        _MA_STREAM_CACHE.pop(key, None)
        return None
    return url


def _ma_stream_cache_put(user: str, uri: str, url: str) -> None:
    now = time.time()
    for key in [k for k, (exp, _) in _MA_STREAM_CACHE.items() if now > exp]:
        _MA_STREAM_CACHE.pop(key, None)
    _MA_STREAM_CACHE[f"{user}|{uri}"] = (now + _MA_STREAM_CACHE_TTL, url)


async def _ma_stream_creds(request: Request) -> dict[str, Any]:
    """Resolve identity for the MA stream endpoints (401 on auth failure)."""
    try:
        creds = await _resolve_identity_from_request(request)
    except HTTPException as e:
        log.error(f"[stream/ma] Identity resolution failed: {e.detail}")
        raise HTTPException(status_code=401, detail=f"Authentication required: {e.detail}") from e
    except Exception as e:
        log.error(f"[stream/ma] Identity resolution crashed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error resolving identity") from e
    if not isinstance(creds, dict):
        creds = creds.model_dump() if hasattr(creds, "model_dump") else (creds.dict() if hasattr(creds, "dict") else dict(creds))
    log.info(f"[stream/ma] Identity resolved for user: {creds.get('user')}")
    return creds


async def _ma_discover_and_select(creds: dict[str, Any], player_id: str | None) -> tuple[str, str, str]:
    """Discover MA players and select the browser Sendspin player.

    Returns ``(mass_url, mass_token, target_player_id)``.
    Raises 400 (MA not configured), 404 (no players), 409 (no browser player).
    """
    mass_url = creds.get("mass_url") or ""
    mass_token = creds.get("mass_token") or ""

    log.info(f"[stream/ma] Credentials: url={redact_url(mass_url)}, has_token={bool(mass_token)}")

    if not mass_url:
        log.error("[stream/ma] Music Assistant URL not configured")
        raise HTTPException(status_code=400, detail="Music Assistant not configured")

    ma_scheme, ma_host, ma_port = _normalize_ma_url(mass_url)
    ma_api = f"{ma_scheme}://{ma_host}:{ma_port}/api"

    auth_headers = {"Authorization": f"Bearer {mass_token}"} if mass_token else {}

    # ── Step 1: Discover MA players via JSON-RPC ──────────────────────────
    log.info("[stream/ma] Discovering MA players...")
    available_players: dict[str, dict[str, Any]] = {}
    async with shared_http_client() as client:
        try:
            resp = await client.post(
                ma_api,
                json={"message_id": uuid.uuid4().hex, "command": "players/all"},
                headers={"Content-Type": "application/json", **auth_headers},
                timeout=aiohttp.ClientTimeout(total=15.0),
            )
            log.info(f"[stream/ma] players/all status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                # MA v2 REST returns data directly (not wrapped in {"result": ...})
                if isinstance(data, list):
                    for p in data:
                        if isinstance(p, dict):
                            pid = p.get("player_id") or p.get("id")
                            if pid:
                                player_info: dict[str, Any] = {
                                    "player_id": str(pid),
                                    "name": str(p.get("name") or p.get("friendly_name") or ""),
                                    "state": str(p.get("state") or p.get("available_state") or "").lower(),
                                }
                                device_info = p.get("device_info")
                                if isinstance(device_info, dict):
                                    player_info["device_info"] = device_info
                                available_players[str(pid)] = player_info
                                player_name = player_info["name"] or "unnamed"
                                log.info(f"[stream/ma] Found player: {pid} ({player_name})")
        except Exception as err:
            log.warning(f"[stream/ma] players/all call failed: {err}", exc_info=True)

    if not available_players:
        log.error("[stream/ma] No MA players found")
        raise HTTPException(status_code=404, detail="No Music Assistant players available")

    # ── Step 2: Select the browser Sendspin player ────────────────────────
    log.info("[stream/ma] Selecting browser player...")

    def _player_text(player: dict[str, Any]) -> str:
        parts = [
            str(player.get("name") or ""),
            str(player.get("friendly_name") or ""),
        ]
        device_info = player.get("device_info")
        if isinstance(device_info, dict):
            parts.extend(
                str(device_info.get(key) or "")
                for key in ("product_name", "manufacturer", "model", "software_version")
            )
        return " ".join(parts).lower()

    def _is_browser_player(player: dict[str, Any]) -> bool:
        player_text = _player_text(player)
        return any(term in player_text for term in ("sendspin", "browser", "web player", "webplayer"))

    target_player: dict[str, Any] | None = None
    if player_id:
        target_player = available_players.get(player_id)
        if not target_player:
            log.error(f"[stream/ma] Requested browser player_id '{player_id}' is not available")
            raise HTTPException(
                status_code=409,
                detail="Browser player is not connected. Open the browser Web Player first.",
            )
        log.info(
            f"[stream/ma] Using explicit browser player '{player_id}' "
            f"({target_player.get('name') or 'unnamed'})"
        )
    else:
        browser_players = [p for p in available_players.values() if _is_browser_player(p)]
        if browser_players:
            target_player = browser_players[0]
            log.info(
                f"[stream/ma] Using detected browser player '{target_player['player_id']}' "
                f"({target_player.get('name') or 'unnamed'})"
            )
        else:
            log.error("[stream/ma] No browser Sendspin player is connected")
            raise HTTPException(
                status_code=409,
                detail="Browser player is not connected. Open the browser Web Player first.",
            )

    return mass_url, mass_token, str(target_player["player_id"])


def _ma_pick_stream_url(ma_client: Any, mass_url: str, target_player_id: str) -> str | None:
    """Single-pass stream URL resolution from MA's current state.

    Never sends commands — priorities mirror the original poll loop:
    1. MA-provided stream URL (queue_updated events),
    2. queue_state current_item.media_item.stream_url,
    3. queue_state current_item.stream_url,
    4. constructed flow URL from queue_id/queue_item_id.
    Returns None when the queue has no resolvable current item.
    """
    ma_provided_url = ma_client.get_stream_url()
    if ma_provided_url:
        log.info(f"[stream/ma] Stream URL from MA: {redact_url(ma_provided_url)[:150]}")
        return ma_provided_url
    queue_state = ma_client.get_queue_state()
    current_item = queue_state.get("current_item", {})
    if isinstance(current_item, dict) and current_item.get("queue_item_id"):
        media_item = current_item.get("media_item", {})
        if isinstance(media_item, dict) and media_item.get("stream_url"):
            log.info(f"[stream/ma] Stream URL from media_item: {redact_url(media_item['stream_url'])[:150]}")
            return media_item["stream_url"]
        if current_item.get("stream_url"):
            log.info(f"[stream/ma] Stream URL from current_item: {redact_url(current_item['stream_url'])[:150]}")
            return current_item["stream_url"]
        # Last resort: construct flow URL using MA's actual queue_id + queue_item_id
        # Use MA's generated session (queue_id), not the gateway-generated one
        queue_item_id = current_item["queue_item_id"]
        queue_id = queue_state.get("queue_id", target_player_id)
        flow_player_id = queue_state.get("player_id", target_player_id)
        http_base = mass_url.replace("http://", "").replace("https://", "")
        constructed = f"http://{http_base}/flow/{queue_id}/{queue_item_id}/{flow_player_id}.mp3"
        log.info(f"[stream/ma] Stream URL constructed (fallback): {redact_url(constructed)[:150]}")
        return constructed
    return None


async def _ma_proxy_bytes(request: Request, stream_url: str) -> StreamingResponse:
    """Proxy MA stream bytes through the Gateway (Range passthrough, Step 5)."""
    log.info(f"[stream/ma] Initiating byte proxy from: {redact_url(stream_url)[:120]}...")

    async def stream_generator_ma(cli, r):
        try:
            bytes_sent = 0
            async for chunk in r.content.iter_chunked(64 * 1024):
                try:
                    if await request.is_disconnected():
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
            await r.release()
            await cli.close()

    range_header = request.headers.get("range")
    log.info(f"[stream/ma] Client requested range: {range_header}")
    proxy_client = aiohttp.ClientSession(
        # No total deadline: a long MA stream must not be cut at 5 min.
        timeout=aiohttp.ClientTimeout(total=None, connect=15.0),
    )
    # BUG-22: close proxy_client on every exit except the streaming hand-off,
    # where stream_generator_ma's finally owns it. Covers HTTPException
    # (upstream 404/5xx), generic errors, and cancellation alike.
    handed_to_generator = False
    try:
        proxy_headers: dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (compatible; JarvisOS/2.0; audio-proxy)",
            "Accept": "audio/*,*/*;q=0.9",
        }
        if range_header:
            proxy_headers["Range"] = range_header

        proxy_resp: aiohttp.ClientResponse | None = None
        last_status_code: int | None = None
        for attempt in range(1, 11):
            proxy_resp = await proxy_client.get(
                stream_url, headers=proxy_headers, allow_redirects=False
            )
            last_status_code = proxy_resp.status
            log.info(f"[stream/ma] MA stream response status: {proxy_resp.status} (attempt {attempt}/10)")
            if proxy_resp.status != 404:
                break
            await proxy_resp.release()
            proxy_resp = None
            if attempt < 10:
                await asyncio.sleep(0.5)

        if proxy_resp is None:
            raise HTTPException(
                status_code=502,
                detail=f"MA stream endpoint returned HTTP {last_status_code} while waiting for audio readiness",
            )

        if proxy_resp.status >= 400:
            body = ""
            with suppress(Exception):
                body = (await proxy_resp.read()).decode("utf-8", errors="ignore").strip()
            await proxy_resp.release()
            raise HTTPException(
                status_code=502,
                detail=f"MA stream endpoint returned HTTP {proxy_resp.status}{f': {body[:200]}' if body else ''}",
            )

        proxy_response_headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        }
        for key in ("Content-Range", "Content-Length", "Content-Type"):
            val = proxy_resp.headers.get(key)
            if val:
                proxy_response_headers[key] = val

        proxy_status_code = proxy_resp.status

        response = StreamingResponse(
            stream_generator_ma(proxy_client, proxy_resp),
            status_code=proxy_status_code,
            media_type=proxy_response_headers.get("Content-Type", "audio/mpeg"),
            headers=proxy_response_headers,
        )
        handed_to_generator = True
        return response
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[stream/ma] Stream proxy failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to proxy MA stream: {e}") from e
    finally:
        if not handed_to_generator:
            await proxy_client.close()


@app.post("/api/media/stream/music-assistant")
async def start_music_assistant_stream(uri: str, request: Request, player_id: str | None = None):
    """Start Music Assistant playback for `uri` on the browser player (BUG-19).

    The ONLY place that may send player_queues/play_media. Resolves the queue's
    stream URL (15s event loop), caches it for GET byte fetches, and returns
    SUCCESS without streaming any bytes.

    Flow:
    1. Resolve credentials (mass_url, mass_token) from the Jarvis identity service.
    2. Select the browser Sendspin player (explicit `player_id` or name match).
    3. Connect to MA WebSocket and authenticate.
    4. Send player_queues/play_media (option=replace) to populate the queue.
    5. Resolve the queue's stream URL from MA state (15s poll).
    6. Cache the session (user|uri -> url) and return SUCCESS.
    """
    log.info(f"[stream/ma] Received start request for uri='{uri}'")
    try:
        creds = await _ma_stream_creds(request)
        mass_url, mass_token, target_player_id = await _ma_discover_and_select(creds, player_id)

        # Convert ABS book IDs to MA-compatible URIs
        ma_uri = uri
        if not re.match(r'^[a-z]+://', uri) and re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', uri, re.IGNORECASE):
            ma_uri = f"library://audiobookshelf/book/{uri}"
            log.info(f"[stream/ma] Detected ABS book ID, converted to MA URI: {ma_uri}")

        session_id = str(uuid.uuid4())
        ma_client = MAWebSocketClient(
            mass_url=mass_url,
            mass_token=mass_token,
        )
        try:
            await ma_client.connect()
            log.info(f"[stream/ma] WebSocket connected: {redact_url(ma_client.ws_url)}")
        except Exception as e:
            log.error(f"[stream/ma] WebSocket connection failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to Music Assistant WebSocket: {e}"
            ) from e

        try:
            # Send play_media using the MA frontend signature so the requested URI
            # replaces the active queue instead of reusing the previous item.
            log.info(f"[stream/ma] Sending play_media for uri='{ma_uri}' on player='{target_player_id}' (session_id={session_id})")
            play_media_response = await ma_client.send_command(
                "player_queues/play_media",
                {"queue_id": target_player_id, "media": ma_uri, "option": "replace", "radio_mode": False, "custom_data": {"session_id": session_id}},
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
                    with suppress(Exception):
                        await ma_client.disconnect()
                    raise HTTPException(
                        status_code=502,
                        detail=f"MA error: {ma_error['code']}: {ma_error['details']}"
                    )
                if ma_client.connected:
                    stream_url = _ma_pick_stream_url(ma_client, mass_url, target_player_id)
                    if stream_url:
                        break
                await asyncio.sleep(0.2)

            if not stream_url:
                queue_desc = ma_client.get_queue_state_description()
                log.error(f"[stream/ma] Stream URL not resolved within timeout. queue_state={ma_client.get_queue_state()}")
                with suppress(Exception):
                    await ma_client.disconnect()
                raise HTTPException(
                    status_code=502,
                    detail=f"MA did not resolve queue state within {stream_timeout}s. Queue state: {queue_desc}. Session ID: {session_id}"
                )

            # ── Step 4: Disconnect MA WebSocket (no longer needed) ──────────────
            await ma_client.disconnect()
            log.info("[stream/ma] WebSocket closed after stream URL resolved")
        except HTTPException:
            raise
        except Exception as e:
            log.error(f"[stream/ma] WebSocket/stream handling failed: {e}", exc_info=True)
            with suppress(Exception):
                await ma_client.disconnect()
            raise HTTPException(status_code=502, detail=f"Failed to resolve Music Assistant stream: {e}") from e

        _ma_stream_cache_put(str(creds.get("user") or ""), uri, stream_url)
        return {"status": "SUCCESS"}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[stream/ma] Unhandled exception: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to resolve Music Assistant stream: {e}") from e


@app.get("/api/media/stream/music-assistant")
async def stream_music_assistant(uri: str, request: Request, player_id: str | None = None):
    """Fetch bytes for a Music Assistant stream — NEVER starts playback (BUG-19).

    This endpoint is browser-local only: the browser's Sendspin player owns the
    queue, and the gateway proxies the resolved MA bytes back to that same
    browser. We never mutate a random physical player to make the stream work,
    and (since BUG-19) we never re-send player_queues/play_media from GET —
    every Range/seek request would otherwise restart the queue.

    Cache hit: proxy immediately (no players/all discovery, no WebSocket).
    Cache miss: resolve the ALREADY-RUNNING queue via player_queues/get and
    proxy. No resolvable session -> 409 (start playback via POST first).
    """
    log.info(f"[stream/ma] Received stream request for uri='{uri}'")
    try:
        creds = await _ma_stream_creds(request)
        user = str(creds.get("user") or "")

        cached_url = _ma_stream_cache_get(user, uri)
        if cached_url:
            log.info(f"[stream/ma] Session cache hit for uri='{uri}', fetching bytes only")
            return await _ma_proxy_bytes(request, cached_url)

        mass_url, mass_token, target_player_id = await _ma_discover_and_select(creds, player_id)

        ma_client = MAWebSocketClient(
            mass_url=mass_url,
            mass_token=mass_token,
        )
        try:
            await ma_client.connect()
            log.info(f"[stream/ma] WebSocket connected: {redact_url(ma_client.ws_url)}")
        except Exception as e:
            log.error(f"[stream/ma] WebSocket connection failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to Music Assistant WebSocket: {e}"
            ) from e

        stream_url: str | None = None
        try:
            # Adopt the live queue state over the wire (player_queues/get is on
            # the COMMAND_PREFIX allowlist) instead of play_media: an
            # already-running queue produces no events to wait for.
            result = await ma_client.send_command("player_queues/get", {"queue_id": target_player_id})
            if isinstance(result, dict) and result:
                ma_client.ingest_queue_state(result)
            stream_url = _ma_pick_stream_url(ma_client, mass_url, target_player_id)
        except HTTPException:
            with suppress(Exception):
                await ma_client.disconnect()
            raise
        except Exception as e:
            log.error(f"[stream/ma] Queue state resolution failed: {e}", exc_info=True)
            with suppress(Exception):
                await ma_client.disconnect()
            raise HTTPException(status_code=502, detail=f"Failed to resolve Music Assistant stream: {e}") from e

        with suppress(Exception):
            await ma_client.disconnect()

        if not stream_url:
            log.error(f"[stream/ma] No resolvable session for uri='{uri}' (queue idle or empty)")
            raise HTTPException(
                status_code=409,
                detail="Playback session not started for this URI. Start playback first.",
            )

        _ma_stream_cache_put(user, uri, stream_url)
        return await _ma_proxy_bytes(request, stream_url)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[stream/ma] Unhandled exception: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to resolve Music Assistant stream: {e}") from e


@app.get("/api/media/imageproxy")
async def media_imageproxy(path: str, request: Request, service: str = "", w: int | None = None):
    """Proxy image requests (entity pictures/covers) from Home Assistant, Music Assistant, or Audiobookshelf.

    The `path` may be a relative path (e.g. HA entity_picture, ABS /api/items/<id>/cover) or a full URL
    (e.g. MA returns http://<host>:8095/imageproxy?path=...). MA's public image endpoint needs no auth,
    while HA and ABS require a Bearer token from the resolved identity. Use `service=ma|abs|ha` to force
    the upstream when it cannot be inferred from the path.
    """
    log.info(f"[imageproxy] Proxy request path={redact_url(path)[:160]} service={service}")
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        creds = await _resolve_identity_from_request(request)
        if not isinstance(creds, dict):
            creds = creds.dict() if hasattr(creds, "dict") else (creds.model_dump() if hasattr(creds, "model_dump") else dict(creds))
    except Exception as e:
        log.error(f"[imageproxy] Identity resolution failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication required") from e

    parsed = urlparse(path)
    is_full = bool(parsed.scheme and parsed.netloc)

    # BUG-05: full URLs are fetched server-side (the MA branch below can fetch
    # them as-is), so only hosts the user has actually configured may be
    # contacted (SSRF). An unknown host is never fetched: instead of failing
    # outright, the request is REBASED onto the configured base for the service
    # implied by the path — the original host is discarded — which keeps
    # mismatched-host MA/HA covers working (owner directive: never break player
    # functionality). 400 remains only when that service is not configured.
    rebase = False
    if is_full:
        allowed_hosts = set()
        for _key in ("mass_url", "abs_url", "audiobookshelf_url", "ha_url"):
            _h = (urlparse(creds.get(_key, "") or "").hostname or "").lower()
            if _h:
                allowed_hosts.add(_h)
        rebase = (parsed.hostname or "").lower() not in allowed_hosts

    # Infer which upstream hosts the image.
    svc = (service or "").lower()
    if not svc:
        if is_full and not rebase:
            host = (parsed.hostname or "").lower()
            mhost = (urlparse(creds.get("mass_url", "") or "").hostname or "").lower()
            ahost = (urlparse(creds.get("audiobookshelf_url", "") or "").hostname or "").lower()
            hhost = (urlparse(creds.get("ha_url", "") or "").hostname or "").lower()
            if mhost and host == mhost:
                svc = "ma"
            elif ahost and host == ahost:
                svc = "abs"
            elif hhost and host == hhost:
                svc = "ha"
        if not svc:
            p = parsed.path
            if "/api/items/" in p:
                svc = "abs"
            elif "/api/image/serve" in p:
                # HA entity_picture (…/api/image/serve/…), even on an
                # external host such as Nabu Casa — fetch from configured HA.
                svc = "ha"
            elif p.startswith("/imageproxy") or "/api/image" in p:
                svc = "ma"
            else:
                svc = "ha"

    target_url = None
    headers: dict[str, str] = {}

    if svc == "ma":
        # MA imageproxy is publicly reachable (no auth) and may embed an internal IP the
        # browser cannot reach, so fetch the supplied URL server-side as-is when possible.
        # Only for configured hosts; unknown hosts are rebased onto mass_url below.
        if is_full and not rebase and (parsed.path.startswith("/imageproxy") or "/api/image" in parsed.path):
            target_url = path
        else:
            base = creds.get("mass_url") or ""
            token = creds.get("mass_token") or ""
            if not base:
                raise HTTPException(status_code=400, detail="Music Assistant not configured")
            rel = parsed.path or (path if path.startswith("/") else "/" + path)
            target_url = f"{base.rstrip('/')}{rel}"
            if parsed.query:
                target_url += "?" + parsed.query
            if token:
                headers["Authorization"] = f"Bearer {token}"
    elif svc == "abs":
        base = creds.get("audiobookshelf_url") or ""
        token = creds.get("audiobookshelf_api_key") or ""
        if not base:
            raise HTTPException(status_code=400, detail="Audiobookshelf not configured")
        rel = parsed.path if is_full else (path if path.startswith("/") else "/" + path)
        target_url = f"{base.rstrip('/')}{rel}"
        if parsed.query:
            target_url += "?" + parsed.query
        if token:
            headers["Authorization"] = f"Bearer {token}"
    else:
        base = creds.get("ha_url") or ""
        token = creds.get("ha_token") or ""
        if not base:
            raise HTTPException(status_code=400, detail="Home Assistant not configured")
        rel = parsed.path if is_full else (path if path.startswith("/") else "/" + path)
        target_url = f"{base.rstrip('/')}{rel}"
        if parsed.query:
            target_url += "?" + parsed.query
        if token:
            headers["Authorization"] = f"Bearer {token}"

    # BUG-11: forward the client's conditional request upstream and the
    # client's requested width to Music Assistant (imageproxy `size=`).
    req_headers = dict(headers)
    if_none_match = request.headers.get("if-none-match")
    if if_none_match:
        req_headers["If-None-Match"] = if_none_match

    if w and svc == "ma":
        parts = urlparse(target_url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "size"]
        query.append(("size", str(w)))
        target_url = parts._replace(query=urlencode(query)).geturl()

    try:
        async with shared_http_client() as client:
            resp = await client.get(
                target_url, headers=req_headers, timeout=aiohttp.ClientTimeout(total=30.0, connect=5.0)
            )
            if resp.status == 304:
                await resp.release()
                return Response(status_code=304)
            if resp.status != 200:
                log.error(f"[imageproxy] upstream {svc} status {resp.status} for {redact_url(target_url)}")
                await resp.release()
                raise HTTPException(status_code=resp.status, detail="Failed to fetch image from upstream")

            content_type = resp.headers.get("Content-Type", "image/jpeg")
            resp_headers = {"Cache-Control": "private, max-age=86400, immutable"}
            upstream_etag = resp.headers.get("ETag")
            if upstream_etag:
                resp_headers["ETag"] = upstream_etag

            async def _image_stream():
                try:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        yield chunk
                finally:
                    await resp.release()

            return StreamingResponse(_image_stream(), media_type=content_type, headers=resp_headers)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[imageproxy] Exception proxying image ({svc}) {redact_url(target_url)}: {e}")
        raise HTTPException(status_code=500, detail="Error fetching image") from e


@app.get("/api/media/detail")
async def get_media_detail(uri: str, request: Request):
    """Resolve full media details from Music Assistant for a given URI."""
    log.info(f"[media/detail] Resolving details for uri='{uri}'")
    mass_url, mass_token = await _resolve_ma_credentials(request)
    if not mass_url:
        raise HTTPException(status_code=400, detail="Music Assistant URL not configured")
    return await _ma_rpc(mass_url, mass_token, "music/item_by_uri", {"uri": uri})


class MALibraryURIRequest(BaseModel):
    """Ask for the Music Assistant URI that plays an Audiobookshelf item."""

    abs_item_id: str
    title: str = ""


@app.post("/api/media/ma-library-uri")
async def resolve_ma_library_uri(req: MALibraryURIRequest, request: Request):
    """Map an Audiobookshelf item id to the URI Music Assistant can actually play.

    MA streams an audiobook from its own Audiobookshelf provider and only accepts
    its own ``library://audiobook/<n>`` URI — it is not a URL player, and handing
    it a stream URL leaves the player idle with the URL as its track title. The
    numeric ``<n>`` is MA-internal, so only MA can produce it: this endpoint owns
    that lookup (``services/shared/ma_library.py``) so the browser and the
    execution service never diverge.

    Returns 404 when MA does not know the item, and the caller must surface that
    rather than falling back to the stream URL.
    """
    abs_item_id = (req.abs_item_id or "").strip()
    if not abs_item_id:
        raise HTTPException(status_code=422, detail="abs_item_id is required")
    # Authenticate strictly before touching MA. `_resolve_ma_credentials` goes
    # through `resolve_identity`, which falls back to the system default user for
    # *any* string — measured: an anonymous request and `Bearer sk-not-a-real-key`
    # both got a 200 here, i.e. an open MA lookup. This endpoint reaches MA's
    # library index, so an unauthenticated caller must be refused outright.
    await _require_authenticated(request)
    mass_url, mass_token = await _resolve_ma_credentials(request)
    log.info(
        f"[media/ma-library-uri] Resolving MA URI for abs_item_id={abs_item_id} title={req.title!r}"
    )
    try:
        resolved = await ma_library.resolve_audiobook_uri(
            mass_url, mass_token, abs_item_id, req.title
        )
    except ma_library.MALibraryLookupError as e:
        # The reason travels with the response so the UI can distinguish "MA is
        # not configured" from "MA does not have this book".
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "status": "SUCCESS",
        "abs_item_id": resolved.abs_item_id,
        "ma_uri": resolved.ma_uri,
        "title": resolved.title,
    }


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

    mass_url, mass_token = await _resolve_ma_credentials(request)
    if not mass_url:
        raise HTTPException(status_code=400, detail="Music Assistant not configured")

    if req.favorite:
        await _ma_rpc(mass_url, mass_token, "music/favorites/add_item", {"item": req.uri})
        return {"status": "SUCCESS", "favorite": True}

    # Removing needs the library item id and media type, which only the item
    # lookup can provide.
    item = await _ma_rpc(mass_url, mass_token, "music/item_by_uri", {"uri": req.uri})
    if not isinstance(item, dict):
        raise HTTPException(status_code=404, detail="Could not resolve library item")
    item_id = item.get("item_id")
    media_type = item.get("media_type")
    if not item_id or not media_type:
        raise HTTPException(status_code=404, detail="Could not resolve library item ID or media type")

    await _ma_rpc(
        mass_url,
        mass_token,
        "music/favorites/remove_item",
        {"library_item_id": item_id, "media_type": media_type},
    )
    return {"status": "SUCCESS", "favorite": False}


@app.post("/api/media/token")
async def post_media_token(request: Request):
    """Issue a short-lived signed media token for the caller (§7.4).

    Authenticated with the normal API key header; the returned token is used
    as ``?mt=`` on media URLs (image proxies, streams, SSE) instead of the
    raw API key.
    """
    creds = await _resolve_identity_from_request(request)
    if not isinstance(creds, dict):
        creds = creds.model_dump() if hasattr(creds, "model_dump") else (
            creds.dict() if hasattr(creds, "dict") else dict(creds)
        )
    user = creds.get("user") or ""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    token, expires_at = sign(user)
    return {"token": token, "expires_at": expires_at}


@app.get("/api/media/events")
async def media_events_stream(request: Request):
    """SSE stream of normalized media events for the caller (§7.2).

    EventSource cannot set headers, so the signed ``?mt=`` media token (§7.4)
    is accepted here in addition to the normal API key; native clients may
    keep the Authorization header. The first message is a full player
    snapshot, followed by live player/queue events and a heartbeat every 15
    seconds.
    """
    creds = await _resolve_identity_from_media_token(request)
    if creds is None:
        creds = await _resolve_identity_from_request(request)
    creds_dict = _identity_cred_dict(creds) if not isinstance(creds, dict) else creds
    user = creds_dict.get("user") or ""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    hub = await acquire_media_hub(user, creds_dict)
    return StreamingResponse(
        hub.subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/api/workspaces/{workspace_id}/terminal")
async def workspaces_terminal_ws(websocket: WebSocket, workspace_id: str, token: str = ""):
    """Proxy WebSocket connection for the interactive terminal to the workspace runtime service."""
    await websocket.accept()
    log.info(f"[workspaces-terminal] WebSocket connection accepted for workspace {workspace_id}")

    api_token = token or websocket.query_params.get("token")
    if not api_token:
        log.error("[workspaces-terminal] Missing authentication token")
        await websocket.close(code=1008, reason="Missing token")
        return

    # Validate token against Identity service
    try:
        async with borrow_http_client() as client:
            auth_resp = await client.get(
                f"{IDENTITY_SVC}/api/users/me",
                headers={"Authorization": f"Bearer {api_token}"}
            )
            if auth_resp.status != 200:
                log.warning(f"[workspaces-terminal] Token validation failed: status={auth_resp.status}")
                await websocket.close(code=1008, reason="Invalid token")
                return
    except Exception as e:
        log.error(f"[workspaces-terminal] Token validation failed due to error: {e}")
        await websocket.close(code=1011, reason="Auth service unavailable")
        return

    # Connect to the workspace_runtime WebSocket terminal endpoint
    ws_url = WORKSPACE_RUNTIME_SVC.replace("http://", "ws://").replace("https://", "wss://")
    target_url = f"{ws_url}/ws/workspace/{workspace_id}/terminal?token={api_token}"
    log.info(f"[workspaces-terminal] Connecting to workspace runtime terminal: {redact_url(target_url)}")

    import websockets
    try:
        async with websockets.connect(target_url, open_timeout=10, close_timeout=5) as ws_run:
            log.info("[workspaces-terminal] Connection to workspace runtime established")

            async def forward_client_to_run():
                try:
                    while True:
                        msg = await websocket.receive()
                        text_data = msg.get("text")
                        binary_data = msg.get("bytes")
                        if text_data is None and binary_data is None:
                            break
                        if text_data is not None:
                            await ws_run.send(text_data)
                        elif binary_data is not None:
                            await ws_run.send(binary_data)
                except WebSocketDisconnect:
                    log.info("[workspaces-terminal] Browser disconnected")
                except Exception as e:
                    log.warning(f"[workspaces-terminal] Client->Run error: {e}")

            async def forward_run_to_client():
                try:
                    while True:
                        message = await ws_run.recv()
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except WebSocketDisconnect:
                    log.info("[workspaces-terminal] Browser disconnected (Run->Client)")
                except Exception as e:
                    log.warning(f"[workspaces-terminal] Run->Client error: {e}")

            await asyncio.gather(
                forward_client_to_run(),
                forward_run_to_client(),
            )
    except Exception as e:
        log.error(f"[workspaces-terminal] Failed to connect to workspace runtime: {e}", exc_info=True)
        with suppress(Exception):
            await websocket.send_text(json.dumps({"type": "stdout", "data": f"\r\n\x1b[31mFailed to connect to workspace runtime terminal: {e}\x1b[0m\r\n"}))
        with suppress(Exception):
            await websocket.close(code=1011, reason=f"Terminal service unavailable: {str(e)[:100]}")

