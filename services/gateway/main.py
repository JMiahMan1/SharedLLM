# services/gateway/main.py
import os
import logging
import json
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, WebSocket, WebSocketDisconnect
from typing import Optional, Any, Dict
from fastapi.responses import JSONResponse, StreamingResponse
import re
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# --- Setup Logging IMMEDIATELY ---
log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
from gateway.schemas import ResolvedCredentials
from gateway.agent_loop import execute_inference as provider_execute_inference, get_vram_safe_params
from gateway.config import (
    OLLAMA_URL, IDENTITY_SVC, EXECUTION_SVC, RAG_SVC, 
    STORAGE_SVC, LOGGING_SVC, WORKSPACE_RUNTIME_SVC, 
    INTERNAL_SECRET, OLLAMA_TIMEOUT, CONFIG
)
from gateway.llm_providers import BaseLLMProvider, OllamaProvider, OpenRouterProvider

QWEN_GROUNDING_INSTRUCTION = """
# MISSION LOCK: Raven Autonomous Repair Protocol
1. **FOCUS**: You are a repair agent. Your ONLY mission is to resolve the specific BUG or TASK provided in the User Request.
2. **NO DISTRACTIONS**: You are strictly FORBIDDEN from acknowledging, proposing, or implementing any features, schemas, or capabilities seen in the context that are not related to the primary mission.
3. **ZERO CONVERSATION**: You MUST NOT ask questions, seek approval, or provide status updates. Your output must be 100PCT execution-oriented.
4. **TOOL MANDATE**: Every response MUST contain a valid JSON tool call. If you are 'thinking', do it within the 'comment' field of the JSON or as a concise prefix, but the JSON is mandatory.
5. **PATCH PROTOCOL**: Use 'WorkspaceFilePatchRequest' with the 'chunks' (old_text/new_text) schema for surgical edits. NEVER send ASCII art or summaries as 'content'.
6. **TERMINAL EXECUTION**: Continue until the task is verified fixed. If you stall, you are in violation of protocol.
"""

def _make_ollama_response(message: str, model: str, intent: str = None, debug_context: str = None, stream: bool = False):
    """Helper to create an Ollama-compatible response (streaming or non-streaming)."""
    from fastapi.responses import JSONResponse, StreamingResponse
    import json
    
    if not stream:
        res = {
            "model": model,
            "created_at": datetime.now().isoformat() + "Z",
            "message": {"role": "assistant", "content": message},
            "done": True,
            "status": "SUCCESS"
        }
        if intent: res["intent"] = intent
        if debug_context: res["debug_context"] = debug_context
        return JSONResponse(res)

    async def gen():
        # Yield the message content chunk
        chunk = {
            "model": model,
            "created_at": datetime.now().isoformat() + "Z",
            "message": {"role": "assistant", "content": message},
            "done": False
        }
        yield json.dumps(chunk) + "\n"
        # Yield the done chunk
        yield json.dumps({"model": model, "done": True}) + "\n"
    
    return StreamingResponse(gen(), media_type="application/x-ndjson")

def _make_ollama_chunk(content: str, model: str, done: bool = False):
    return {
        "model": model,
        "created_at": datetime.now().isoformat() + "Z",
        "message": {"role": "assistant", "content": content},
        "done": done
    }


def _make_openai_response(message: str, model: str, intent: str = None, debug_context: str = None, stream: bool = False):
    """Helper to create an OpenAI-compatible response (streaming or non-streaming)."""
    from fastapi.responses import JSONResponse, StreamingResponse
    import json
    import time
    
    if not stream:
        res = {
            "id": f"chatcmpl-{int(time.time())}", 
            "object": "chat.completion", 
            "created": int(time.time()), 
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": message}, "finish_reason": "stop", "index": 0}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }
        if intent: res["intent"] = intent
        if debug_context: res["debug_context"] = debug_context
        return JSONResponse(res)

    async def gen():
        # Yield the delta chunk
        chunk = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"delta": {"content": message}, "index": 0, "finish_reason": None}]
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        # Yield the stop chunk
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

def _make_openai_chunk(content: str, model: str, finish_reason: str = None):
    import time
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"delta": {"content": content} if content else {}, "index": 0, "finish_reason": finish_reason}]
    }

# --- Imports from internal modules ---
from gateway.schemas import StorageListRequest, StorageIndexRequest
from gateway.intent_engine import engine
from gateway.history import update_history, ping_redis
from gateway.prompts import ASSIST_SYSTEM_INSTRUCTION, CODE_HELPER_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT
from gateway.messaging import InferenceJobQueue, JobStatus
from gateway.config import REDIS_URL as _REDIS_URL
REDIS_URL = _REDIS_URL
job_queue = InferenceJobQueue(REDIS_URL)

# REDIS moved below imports

# --- Ouroboros Worker ---
from gateway.background_worker import worker as raven_worker
log.info("Successfully imported Raven background worker.")

# --- Configuration (from shared config) ---
from gateway.config import (
    CONTROL_PLANE_URL,
    FAST_PATH_THRESHOLD as _DEFAULT_FAST_PATH_THRESHOLD,
)
LOGGING_SVC_URL = LOGGING_SVC

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
    """Fetches full LLM settings from Identity Service."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                fetched = {item["key"]: item["value"] for item in resp.json()}
                for k, v in list(fetched.items()):
                    if v in ["auto", ""]:
                        fetched[k] = None
                return fetched
    except Exception as e:
        log.error(f"Failed to fetch dynamic LLM settings: {e}")
    return {}


async def get_provider(settings: dict) -> BaseLLMProvider:
    """Instantiates the correct provider based on settings."""
    from gateway.config import OLLAMA_TIMEOUT
    active_provider = settings.get("active_llm_provider", "ollama")
    timeout = float(settings.get("ollama_timeout", str(OLLAMA_TIMEOUT)))
    if active_provider == "openrouter":
        return OpenRouterProvider(
            api_key=settings.get("llm_cloud_api_key", ""),
            base_url=settings.get("llm_cloud_url", "https://openrouter.ai/api/v1/chat/completions"),
            timeout=timeout
        )
    else:
        return OllamaProvider(
            base_url=settings.get("llm_local_url", OLLAMA_URL),
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


def _parse_llm_json_object(raw: Any) -> dict:
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
    if active == "openrouter":
        model = settings.get("cloud_assistant_model")
    else:
        model = settings.get("ollama_assistant_model") or settings.get("assistant_model")
    if not model:
        raise RuntimeError("No assistant model configured. Please set ollama_assistant_model in the UI settings.")
    log.info(f"[get_assistant_model] active_provider={active} resolved_model={model}")
    return model


async def get_coding_model():
    settings = await get_llm_settings()
    active = settings.get("active_llm_provider", "ollama")
    if active == "openrouter":
        model = settings.get("cloud_coding_model")
    else:
        model = settings.get("ollama_coding_model") or settings.get("coding_model")
    if not model:
        raise RuntimeError("No coding model configured. Please set ollama_coding_model in the UI settings.")
    log.info(f"[get_coding_model] active_provider={active} resolved_model={model}")
    return model


async def get_resident_model() -> Optional[str]:
    """Check what model is currently in VRAM to avoid unnecessary swaps."""
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/ps")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                if models:
                    return models[0]["name"]
    except: pass
    return None

async def get_librarian_model():
    settings = await get_llm_settings()
    active = settings.get("active_llm_provider", "ollama")
    if active == "openrouter":
        model = settings.get("cloud_librarian_model")
    else:
        model = settings.get("ollama_librarian_model") or settings.get("librarian_model")
    if not model:
        raise RuntimeError("No librarian model configured. Please set ollama_librarian_model in the UI settings.")
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
_global_http_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    """Lazy initializer for the global httpx client to ensure test compatibility."""
    global _global_http_client
    if _global_http_client is None:
        _global_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _global_http_client

@asynccontextmanager
async def borrow_http_client():
    yield get_http_client()

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Gateway starting up...")
    engine.load()
    # Initialize the client explicitly on startup
    get_http_client()
    await job_queue.connect()
    log.info("Gateway initialized with FIFO Inference Queue")
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

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://ai.local",
        "http://ai.local:8080",
        "https://ai.local",
        "http://ai.local"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        user_id = creds_data.get("user", "default")
        
        from gateway.history import _redis, _get_history_key
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
      "logging": f"{LOGGING_SVC_URL}/health",
      "workspace_runtime": f"{WORKSPACE_RUNTIME_SVC}/health",
      "control_plane": f"{CONTROL_PLANE_URL}/health",
    }

    results = {"status": "READY", "services": {}}
    all_ok = True

    async with httpx.AsyncClient(timeout=2.0) as client:
      for name, url in services.items():
          try:
            resp = await client.get(url)
            if resp.status_code == 200:
                results["services"][name] = "OK"
            else:
                results["services"][name] = f"ERROR ({resp.status_code})"
                all_ok = False
          except Exception:
            results["services"][name] = "UNREACHABLE"
            all_ok = False

    # The Gateway itself is running if we are responding to this request
    results["services"]["gateway"] = "OK"

    if ping_redis():
      results["services"]["redis"] = "OK"
    else:
      results["services"]["redis"] = "ERROR"
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
async def emit_log(level: str, message: str, context: dict = None):
    try:
      from gateway.agent_loop import sanitize_for_llm
      safe_context = sanitize_for_llm(context) if context else None
      safe_message = sanitize_for_llm(message) if isinstance(message, str) else message
      async with httpx.AsyncClient() as client:
          await client.post(
            f"{LOGGING_SVC_URL}/log",
            json={"service": "gateway", "level": level, "message": safe_message, "context": safe_context},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=1.0
          )
    except Exception:
      pass

@app.get("/api/logs")
async def get_api_logs(limit: int = 50):
    async with httpx.AsyncClient() as client:
      resp = await client.get(f"{LOGGING_SVC_URL}/logs", params={"limit": limit})
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
    from gateway.prompts import RAVEN_AUTONOMOUS_PROTOCOL, RAVEN_NARRATOR_PROTOCOL
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


def build_time_or_date_response(query: str) -> str:
    from gateway.config import TIMEZONE
    tz_name = TIMEZONE
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


async def workspace_runtime_request(method: str, path: str, *, json_payload: dict | None = None, params: dict | None = None) -> dict:
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
        if not isinstance(item, dict):
            return None
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
    if isinstance(data, dict):
      msg_obj = data.get("message")
      if isinstance(msg_obj, dict):
        generated = str(msg_obj.get("content") or "")
      else:
        generated = str(data.get("response") or "")
    if not generated.strip():
      raise HTTPException(status_code=502, detail="Coding model returned an empty README response")

    workspace_id = workspace.get("id")
    write_data = await workspace_runtime_request(
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
        if is_openai: return _make_openai_response(msg, selected_model, stream=should_stream)
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


def resolve_media_target(query: str, entities: list[dict]) -> str:
    """
    Prefer a Music Assistant queue/speaker entity for music playback on named targets.
    Fall back to the first media_player entity if nothing better is found.
    """
    _, requested_device = extract_media_request(query)
    requested_lower = requested_device.lower() if requested_device else ""
    fallback = "auto"

    def _normalize_name(value: str) -> str:
      cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
      cleaned = re.sub(r"\b(remote|cast|chrome)\b", " ", cleaned)
      return " ".join(cleaned.split())

    requested_normalized = _normalize_name(requested_lower)

    def _matches_requested_device(entity: dict) -> bool:
      if not requested_normalized:
          return True
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
          score += 200
      if device_class == "speaker":
          score += 20
      if state not in {"unavailable", "unknown"}:
          score += 10
      if "chrome" in eid or "cast" in friendly:
          score -= 25
      if "remote" in friendly:
          score -= 25
      return score, eid

    candidates = [e for e in entities if isinstance(e, dict) and e.get("entity_id", "").startswith("media_player.")]
    if not candidates:
      return fallback

    if requested_normalized:
      matched_candidates = [entity for entity in candidates if _matches_requested_device(entity)]
      if matched_candidates:
          candidates = matched_candidates
      else:
          return fallback

    if requested_normalized:
      matching_ma_queues = []
      for entity in candidates:
          attrs = entity.get("attributes") or {}
          friendly_normalized = _normalize_name(str(attrs.get("friendly_name") or ""))
          source = str(attrs.get("source") or "").lower()
          if "music assistant queue" not in source:
            continue
          if requested_normalized == friendly_normalized or requested_normalized in friendly_normalized:
            matching_ma_queues.append(entity)

      if matching_ma_queues:
          ranked_queues = sorted((_score(e) for e in matching_ma_queues), reverse=True)
          return ranked_queues[0][1]

    ranked = sorted((_score(e) for e in candidates), reverse=True)
    best_score, best_eid = ranked[0]
    return best_eid if best_score > 0 else candidates[0]["entity_id"]


def resolve_video_target(query: str, entities: list[dict]) -> str:
    """
    Resolve a cast/video-capable media target for video-like requests.
    Prefer entities whose friendly name matches the requested device and avoid
    Music Assistant queues for video playback.
    """
    _, requested_device = extract_media_request(query)
    requested_lower = requested_device.lower() if requested_device else ""
    fallback = "auto"

    def _normalize_name(value: str) -> str:
      cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
      cleaned = re.sub(r"\b(remote)\b", " ", cleaned)
      return " ".join(cleaned.split())

    requested_normalized = _normalize_name(requested_lower)

    def _matches_requested_device(entity: dict) -> bool:
      if not requested_normalized:
          return True
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
      return score, eid

    candidates = [e for e in entities if isinstance(e, dict) and e.get("entity_id", "").startswith("media_player.")]
    if not candidates:
      return fallback

    if requested_normalized:
      matched_candidates = [entity for entity in candidates if _matches_requested_device(entity)]
      if matched_candidates:
          candidates = matched_candidates
      else:
          return fallback

    ranked = sorted((_score(e) for e in candidates), reverse=True)
    best_score, best_eid = ranked[0]
    return best_eid if best_score > 0 else fallback




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
      if not isinstance(data, dict):
          return None
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

async def resolve_identity(body: dict) -> dict:
    try:
      async with httpx.AsyncClient() as client:
          resp = await client.post(
            f"{IDENTITY_SVC}/api/resolve",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=5.0
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
    except httpx.RequestError as e:
      log.error(f"Identity service unreachable: {e}")
      raise HTTPException(status_code=503, detail="Identity service unreachable")


def _auth_body_from_request(request: Request, body: dict | None = None) -> dict:
    merged = dict(body or {})
    user_id = request.query_params.get("user_id")
    if user_id:
        merged["rag_user"] = user_id
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        merged["api_key"] = auth_header.split(" ", 1)[1]
    return merged


async def _resolve_identity_from_request(request: Request, body: dict | None = None) -> dict:
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
            params = {"user_id": creds_data.get("user", "default")}
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
                user_id = creds.get("user", "default")
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
                                from gateway.ha_state_cache import get_redis
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
                            if not eid: continue
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

async def execute_command(endpoint: str, payload: dict) -> dict:
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
            except: pass
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
        vram_params = await get_vram_safe_params(assistant)
        
        payload = {
            "model": assistant,
            "messages": [{"role": "user", "content": proposal_prompt}],
            "stream": False,
            "options": {**vram_params, "num_predict": 512}
        }
        log.info(f"[ShadowExecution] Requesting proposal from {assistant} (Timeout: {OLLAMA_TIMEOUT}s)")
        start_t = asyncio.get_event_loop().time()
        resp = await get_http_client().post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
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


@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def chat_handler(request: Request, background_tasks: BackgroundTasks = None):
    log.info("Chat handler entered")
    exec_data = None
    client = get_http_client()
    # 1. Resolve Identity
    try:
        body = await request.json()
    except:
        body = {}
    if not isinstance(body, dict): body = {}
    
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
        selected_model = explicit_model or await get_assistant_model()
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
    if not explicit_model:
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
                    return _make_openai_response(err_msg, "unknown", "model_config_error")
                return _make_ollama_response(err_msg, "unknown", "model_config_error")

    try:
        creds_data = await resolve_identity(body)
        creds = ResolvedCredentials(**creds_data)
        user_id = creds.user
    except HTTPException as he:
        if he.status_code == 401:
            msg = "Authentication failed. Please log in or provide a valid API key."
            if is_openai: return _make_openai_response(msg, selected_model, "unauthorized")
            return _make_ollama_response(msg, selected_model, "unauthorized")
        raise he
    except Exception as e:
        log.error(f"Identity resolution crash: {e}")
        msg = "The Identity service is currently unavailable. Please try again later."
        if is_openai: return _make_openai_response(msg, selected_model, "degraded")
        return _make_ollama_response(msg, selected_model, "degraded")
    log.info(f"Chat request from {user_id} query='{query}'")

    if is_time_or_date_query(query):
        ans = build_time_or_date_response(query)
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
    except:
        engine.FAST_PATH_CONFIDENCE = _DEFAULT_FAST_PATH_THRESHOLD

    is_fast_path = engine.is_fast_path(intent, confidence)
    log.info(f"[FastPath] is_fast_path={is_fast_path} for intent='{intent}'")
    resolved_entity = None
    
    if is_fast_path:
        media_entities = None
        if intent in ["play_media", "pause_media"]:
            media_entities = await fetch_ha_entities(creds)

        # Attempt entity extraction/resolution for control intents
        if intent == "play_media":
            if is_likely_video_request(query):
                resolved_entity = resolve_video_target(query, media_entities or [])
                log.info(f"[FastPath] BYPASSED for video-like play request; resolved target='{resolved_entity}' and deferring to full tool path")
                is_fast_path = False
            else:
                resolved_entity = resolve_media_target(query, media_entities or [])
        elif intent == "pause_media":
            resolved_entity = engine.extract_entity(query, intent) or resolve_media_target(query, media_entities or [])
        else:
            resolved_entity = engine.extract_entity(query, intent)
        
        # If the intent requires an entity (light, media) but we couldn't resolve one,
        # fallback to the slow-path (LLM) to avoid 'light.auto' errors.
        if intent in ["turn_on", "turn_off", "play_media"] and not resolved_entity:
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
                "entity_id": resolved_entity or "auto"
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
                exec_payload = {
                    "user_context": creds.model_dump(),
                    "entity_id": resolved_entity or "auto",
                    "query": media_query or query,
                    "media_content_type": "artist",
                }
                svc_base = EXECUTION_SVC
            elif intent in ["pause_media", "media_transport"]:
                exec_payload = {
                    "user_context": creds.model_dump(),
                    "entity_id": resolved_entity or "auto",
                    "command": "pause",
                }
                svc_base = EXECUTION_SVC
            else:
                svc_base = EXECUTION_SVC

            async with httpx.AsyncClient(timeout=30.0) as client:
                exec_resp = await client.post(f"{svc_base}{endpoint}", json=exec_payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
                ans = exec_resp.json().get("message", "Action completed.")
            
            await update_history(user_id, "user", query)
            await update_history(user_id, "assistant", ans)
            if is_openai: return _make_openai_response(ans, selected_model, intent)
            return _make_ollama_response(ans, selected_model, intent)

    # 4. Slow Path Execution (FIFO Queue Redirect)
    # Pack full context for the worker
    default_sys = select_system_instruction_for_query(query, selected_model)
    job_payload = {
        "model": selected_model,
        "query": query,
        "system": body.get("system") or default_sys,
        "creds": creds.model_dump(),
        "client": body.get("client"),
        "source": body.get("source"),
        "device_id": body.get("device_id"),
        "rag_user": body.get("rag_user"),
        "show_thinking": show_thinking,
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
                    if not job: break
                    
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
                        if is_openai: yield f"data: {json.dumps(_make_openai_chunk(f'[ERROR]: {err}', selected_model, 'stop'))}\n\n"
                        else: yield json.dumps(_make_ollama_chunk(f"[ERROR]: {err}", selected_model, True)) + "\n"
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
                if not job: break
                if job["status"] == JobStatus.COMPLETED:
                    ans = job["result"]
                    if is_openai: return _make_openai_response(ans, selected_model)
                    return _make_ollama_response(ans, selected_model)
                if job["status"] == JobStatus.FAILED:
                    raise HTTPException(status_code=500, detail=job.get("error", "Job failed"))
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
        # Return the final result
        result = job["result"]
        # Wrap in expected response format
        if job["payload"].get("is_openai"):
            return _make_openai_response(result, job["payload"]["model"], "completed")
        return _make_ollama_response(result, job["payload"]["model"], "completed")
    
    if job["status"] == JobStatus.FAILED:
        return JSONResponse({
            "status": "FAILED",
            "error": job.get("error", "Unknown error during inference"),
            "job_id": job_id
        }, status_code=500)
    
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
async def proxy_read_calendar(request: Request, calendar_name: str = None):
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
        async with httpx.AsyncClient(timeout=None) as client:
            # Use httpx.stream to proxy the streaming response
            req = client.build_request("POST", f"{OLLAMA_URL}/api/generate", json=body)
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
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
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
        except: pass

    try:
        resp = await get_http_client().post(
            f"{RAG_SVC}/rag/search",
            json={"query": q, "user_id": user_id, "collection_name": "nextcloud_files", "k": 5},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
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
        if creds.get("rag_user"): params["rag_user"] = creds["rag_user"]
        if creds.get("voice_id"): params["voice_id"] = creds["voice_id"]
        if creds.get("device_id"): params["device_id"] = creds["device_id"]
        
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

async def _proxy_workspace_runtime_json(method: str, path: str, request: Request | None = None):
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
        jarvis_user = creds_data.get("user", "default")
        nc_user = creds_data.get("nextcloud_user")
    except:
        jarvis_user = "default"
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
        user_id = request.query_params.get("user_id") or creds_data.get("nextcloud_user") or creds_data.get("user", "default")
    except:
        user_id = "default"

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
        user_id = creds_data.get("user", "default")
    except:
        user_id = "default"

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
    if not creds.get("is_admin"): raise HTTPException(status_code=403, detail="Admin only")
    
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
    if not creds.get("is_admin"): raise HTTPException(status_code=403, detail="Admin only")
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
    if not creds.get("is_admin"): raise HTTPException(status_code=403, detail="Admin only")
    
    async with borrow_http_client() as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/tts/voices",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/admin/raven/queue")
async def get_raven_queue(request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"): raise HTTPException(status_code=403, detail="Admin only")
    
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
    if not creds.get("is_admin"): raise HTTPException(status_code=403, detail="Admin only")
    
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

@app.post("/api/admin/raven/queue/{id}/execute")
async def execute_raven_mission(id: int, request: Request):
    creds = await _resolve_identity_from_request(request)
    if not creds.get("is_admin"): raise HTTPException(status_code=403, detail="Admin only")
    
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
from pydantic import BaseModel
from gateway.orchestrator import SINGLE_TURN_TOOL_GUIDE

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
        "coding_model": body.coding_model or coding_model,
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
        target_model = body.coding_model or coding_model
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
        from gateway.history import REDIS_URL
        
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

        from gateway.history import REDIS_URL
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

        from gateway.history import REDIS_URL
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

        from gateway.history import REDIS_URL
        import redis.asyncio as redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        
        # 1. Send all existing historical messages first
        history_key = f"raven:mission:history:{real_id}"
        existing_logs = await r.lrange(history_key, 0, -1)
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

        reader_task = asyncio.create_task(reader())
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.warning(f"[WebSocket] Client disconnect: {e}")
        finally:
            reader_task.cancel()
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

@app.get("/api/config/models")
async def get_ollama_models():
    """Proxy to Ollama to list available tags."""
    from gateway.config import OLLAMA_URL as _OLLAMA_URL
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = sorted(list(set(m["name"] for m in data.get("models", []))))
                return {"status": "SUCCESS", "models": models}
            return {"status": "ERROR", "message": f"Ollama returned {resp.status_code}"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

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
