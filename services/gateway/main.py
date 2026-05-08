# services/gateway/main.py
import os
import logging
import json
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from typing import Optional, Any, Dict, List
from fastapi.responses import JSONResponse, StreamingResponse
import re
import traceback
from datetime import datetime
from pathlib import Path
try:
    from .schemas import ChatRequest, ResolvedCredentials
except (ImportError, ValueError):
    try:
        from schemas import ChatRequest, ResolvedCredentials
    except ImportError:
        from gateway.schemas import ChatRequest, ResolvedCredentials

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

# --- Imports from internal modules ---
try:
    from .schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest, StorageListRequest, StorageIndexRequest
    from .intent_engine import engine
    from .history import get_history, update_history, ping_redis, get_long_term_memory, extract_and_store_user_facts
    from .prompts import CODE_HELPER_SYSTEM_INSTRUCTION, LIBRARIAN_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT
except (ImportError, ValueError):
    try:
      from services.gateway.schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest, StorageListRequest, StorageIndexRequest
      from services.gateway.intent_engine import engine
      from services.gateway.history import get_history, update_history, ping_redis, get_long_term_memory, extract_and_store_user_facts
      from services.gateway.prompts import CODE_HELPER_SYSTEM_INSTRUCTION, LIBRARIAN_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT
    except ImportError:
      from schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest, StorageListRequest, StorageIndexRequest
      from intent_engine import engine
      from history import get_history, update_history, ping_redis, get_long_term_memory, extract_and_store_user_facts
      from prompts import CODE_HELPER_SYSTEM_INSTRUCTION, LIBRARIAN_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT

# --- Ouroboros Worker ---
try:
    from .background_worker import worker as raven_worker
except ImportError:
    try:
        from background_worker import raven_worker
    except ImportError:
        raven_worker = None

# --- Setup Logging ---
log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

# --- Configuration ---
IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://127.0.0.1:8001")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://127.0.0.1:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://127.0.0.1:8004")
STORAGE_SVC = os.getenv("STORAGE_SVC_URL", "http://127.0.0.1:8005")
LOGGING_SVC_URL = os.getenv("LOGGING_SVC_URL", "http://127.0.0.1:8006")
WORKSPACE_RUNTIME_SVC = os.getenv("WORKSPACE_RUNTIME_SVC_URL", "http://127.0.0.1:8007")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
FAST_PATH_THRESHOLD = float(os.getenv("FAST_PATH_THRESHOLD", "0.85"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120.0"))
# ---- Dynamic Model Config ----
CONFIG = {
    "assistant_model": os.getenv("ASSISTANT_MODEL", os.getenv("DEFAULT_MODEL", "qwen3:8b")),
    "coding_model": os.getenv("CODING_MODEL", "qwen3:8b"),
    "librarian_model": os.getenv("LIBRARIAN_MODEL", "qwen3:8b")
}

# Global Inference Lock (Strategy 8: Singleton Queue)
# Ensures only one LLM request is processed at a time to protect 8GB VRAM.
INFERENCE_LOCK = asyncio.Lock()

def get_assistant_model():
    return CONFIG["assistant_model"]

def get_coding_model():
    return CONFIG["coding_model"]

async def get_vram_safe_params(model: str) -> dict:
    """
    Strategy 7: Dynamic VRAM Awareness & Elastic Scaling.
    Checks Ollama's current load and adjusts context parameters.
    Scales UP if VRAM is free, scales DOWN if under pressure.
    Bypasses throttling if running against external APIs (OpenRouter/OpenAI).
    """
    # Environment-aware ceiling
    max_ctx = int(os.getenv("MAX_CONTEXT_WINDOW", "32768"))
    target_ctx = int(os.getenv("DEFAULT_CONTEXT_WINDOW", "12288"))
    
    # EXTERNAL API BYPASS: If using a cloud provider, assume unlimited/large context
    external_indicators = ("openrouter.ai", "openai.com", "anthropic.com", "groq.com")
    if any(ind in OLLAMA_URL.lower() for ind in external_indicators):
        log.info(f"[Strategy 7] External API detected ({OLLAMA_URL}). Unlocking full context: {max_ctx}")
        return {"num_ctx": max_ctx}

    params = {"num_ctx": target_ctx}
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            # Note: /api/ps only works for local Ollama instances
            resp = await client.get(f"{OLLAMA_URL}/api/ps")
            if resp.status_code == 200:
                ps = resp.json()
                models = ps.get("models", [])
                
                # CASE A: High Pressure (Multiple models or massive model active)
                # Threshold of 7GB is tailored for 8GB cards. 
                # If we have a model > 7GB, we downshift to ensure room for the KV cache.
                if len(models) > 1 or any(m.get("size", 0) > 7*1024*1024*1024 for m in models):
                    log.warning("[Strategy 7] VRAM pressure detected (local). Downshifting to 4096.")
                    params["num_ctx"] = 4096
                # CASE B: Free Capacity (Zero models active)
                elif len(models) == 0:
                    log.info(f"[Strategy 7] VRAM is clear. Up-shifting to {max_ctx}.")
                    params["num_ctx"] = max_ctx
    except Exception as e:
        # Fallback if ps endpoint is unavailable or blocked
        log.warning(f"[Strategy 7] Could not poll VRAM state (ps unavailable): {e}")
    
    return params

def get_librarian_model():
    return CONFIG["librarian_model"]

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
  "fix the error", "auto-fix", "debug the system", "raven", "dev loop",
  "check container logs", "rebuild service", "deploy fix", "repair", "execute fix",
  "fix it", "debug it", "fix the code", "apply the fix"
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
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _global_http_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Gateway starting up...")
    engine.load()
    if raven_worker:
        await raven_worker.start()
    
    yield

    log.info("Gateway shutting down...")
    if raven_worker:
        await raven_worker.stop()

app = FastAPI(title="Jarvis OS Gateway", version="1.0.0", lifespan=lifespan)

@app.on_event("startup")
async def startup_event():
    # Initialize the client explicitly on startup
    get_http_client()
    log.info("Gateway initialized with standardized 45s timeouts")

@app.on_event("shutdown")
async def shutdown_event():
    global _global_http_client
    if _global_http_client:
        await _global_http_client.aclose()
        _global_http_client = None

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://192.168.2.205",
        "http://192.168.2.205:8080",
        "https://ai.local",
        "http://ai.local"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Gateway Error: {type(exc).__name__}: {str(exc)}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    await emit_log("ERROR", err_msg, {"trace": traceback.format_exc()})
    return JSONResponse(
      status_code=500,
      content={"status": "ERROR", "message": "Internal Gateway Error", "detail": str(exc)}
    )

# --- Global Health & Readiness ---
@app.delete("/api/history")
async def clear_history_endpoint(request: Request):
    """Clears conversation history for the current user."""
    try:
        body = await request.json()
        creds_data = await resolve_identity(body)
        user_id = creds_data.get("user", "default")
        
        from history import _redis, _get_history_key
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
    
    # Whitelist of allowed files
    allowed_root_files = ["EXAMPLES.md", "README.md", "TESTING.md"]
    
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
    print(f"DIAGNOSTIC [{level}] {message} context={context}")
    try:
      async with httpx.AsyncClient() as client:
          await client.post(
            f"{LOGGING_SVC_URL}/log",
            json={"service": "gateway", "level": level, "message": message, "context": context},
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
        payload = {"model": get_assistant_model(), "prompt": prompt, "stream": False, "options": {"temperature": 0.0}}
        resp = await get_http_client().post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                rewritten = str(data.get("response", query)).strip().strip('"')
                log.info(f"[Context] '{query}' -> '{rewritten}'")
                return rewritten
    except Exception as e:
        log.warning(f"Contextualization failed: {e}")
    return query


def select_model_for_query(query: str) -> str:
    """Route obvious coding and librarian tasks to specialized models."""
    q = (query or "").lower()

    if any(token in q for token in CODING_SIGNALS):
      return get_coding_model()
    if any(token in q for token in LIBRARIAN_SIGNALS):
      return get_librarian_model()
    return get_assistant_model()


def select_system_instruction_for_query(query: str, selected_model: str) -> str:
    try:
        from .prompts import AUTONOMOUS_EVOLUTION_AGENT_PROMPT
    except (ImportError, ValueError):
        try:
            from prompts import AUTONOMOUS_EVOLUTION_AGENT_PROMPT
        except ImportError:
            from gateway.prompts import AUTONOMOUS_EVOLUTION_AGENT_PROMPT
    q = (query or "").lower()
    if any(token in q for token in AUTONOMOUS_SIGNALS):
      return AUTONOMOUS_EVOLUTION_AGENT_PROMPT
    if any(token in q for token in CODING_SIGNALS):
      return CODE_HELPER_SYSTEM_INSTRUCTION
    # Librarian is for research/knowledge queries. 
    # For everything else, use the standard Librarian prompt which now includes hardware control.
    return LIBRARIAN_SYSTEM_INSTRUCTION


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
    if "readme" not in q:
      return False
    action_requested = any(signal in q for signal in WORKSPACE_README_ACTION_HINTS)
    workspace_scoped = any(signal in q for signal in ("workspace", "repo", "repository", "folder", "temp", "nextcloud", "git"))
    return action_requested and workspace_scoped


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
    }
    resp = await call_ollama(payload, use_chat=True)
    if resp.status_code != 200:
      raise HTTPException(status_code=502, detail="Coding model did not return a README response")

    data = resp.json()
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
      "4. **Verification Command**: If the user asks for linting, testing, or running, you MUST provide a 'test_cmd'.\n"
      "   - Examples: 'shellcheck path/to/file.sh', 'flake8 path/to/file.py', 'go vet path/to/file.go', 'node path/to/file.js'.\n"
      "   - To run the script and check output: 'bash path/to/file.sh', 'python3 path/to/file.py', etc.\n\n"
      "### Return ONLY JSON:\n"
      "{\n"
      "  \"relative_path\": \"string\",\n"
      "  \"content\": \"string\",\n"
      "  \"reasoning\": \"string\",\n"
      "  \"test_cmd\": \"string (optional)\"\n"
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
      "format": "json"
    }
    
    resp = await call_ollama(payload, use_chat=True)
    if resp.status_code != 200:
      raise HTTPException(status_code=502, detail="Coding model failed to generate a plan")
      
    try:
        plan = resp.json().get("message", {}).get("content") or resp.json().get("response")
        plan_data = json.loads(plan)
    except Exception as e:
        log.error(f"Failed to parse coding plan: {e}\nRaw: {plan}")
        raise HTTPException(status_code=500, detail="Invalid JSON plan from coding model")

    rel_path = plan_data.get("relative_path")
    content = plan_data.get("content")
    reasoning = plan_data.get("reasoning", "No reasoning provided.")
    test_cmd = plan_data.get("test_cmd")
    
    if not rel_path or content is None:
        raise HTTPException(status_code=400, detail="Coding plan missing relative_path or content")

    # Call the workflow endpoint
    workflow_payload = {
        "workspace_id": workspace_id,
        "rag_user": user_id,
        "relative_path": rel_path,
        "content": content,
        "commit_message": f"feat: {refined_query[:50]}",
        "run_tests": True if test_cmd else False,
        "test_command": test_cmd,
        "sync_to_provider": True,
        "create_parents": True
    }
    
    result = await workspace_runtime_request("POST", "/workflow/write-sync-commit", json_payload=workflow_payload)
    
    summary = (
        f"### Code Orchestration Success\n\n"
        f"**File**: `{rel_path}`\n"
        f"**Action**: Autonomous creation and verification.\n\n"
        f"**Developer Reasoning & Description**:\n{reasoning}\n\n"
        f"**Workflow Result**:\n"
        f"- **Commit**: `{result.get('commit_sha', 'N/A')}`\n"
        f"- **Sync**: {result.get('sync_status', 'N/A')}\n"
        f"- **Verification**: {result.get('test_status', 'N/A')}\n"
    )
    
    if result.get("test_stdout"):
        summary += f"\n**Verification Output**:\n```\n{result.get('test_stdout')}\n```\n"
    
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
      if "music assistant queue" in source:
          score += 50
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


async def call_ollama(payload: dict, use_chat: bool = True, timeout: float = None) -> httpx.Response:
    endpoint = "/api/chat" if use_chat else "/api/generate"
    return await get_http_client().post(
      f"{OLLAMA_URL}{endpoint}",
      json=payload,
      timeout=timeout if timeout is not None else OLLAMA_TIMEOUT,
    )


async def troubleshoot_media_failure(query: str, failure: str) -> dict | None:
    prompt = (
      f"{MEDIA_TROUBLESHOOTING_PROMPT}\n"
      f"User request: {query}\n"
      f"Failure: {failure}"
    )
    try:
      resp = await call_ollama(
          {"model": get_assistant_model(), "prompt": prompt, "stream": False},
          use_chat=False,
      )
      if resp.status_code != 200:
          return None
      data = resp.json()
      if not isinstance(data, dict):
          return None
      raw = str(data.get("response", "")).strip()
      start = raw.find("{")
      end = raw.rfind("}")
      if start == -1 or end == -1 or end <= start:
          return None
      json_data = json.loads(raw[start:end + 1])
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
            resp = await client.get(f"{EXECUTION_SVC}{endpoint}", headers=headers)
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
                # 1. Sync to RAG for discovery
                asyncio.create_task(get_http_client().post(
                    f"{RAG_SVC}/rag/sync/ha",
                    json={"entities": entities, "user_id": user_id},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                ))
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

# --- Chat Handler ---
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
        resp = await call_ollama({"model": get_assistant_model(), "prompt": prompt, "stream": False}, use_chat=False)
        text = resp.json().get("response", "").strip()
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
        vram_params = await get_vram_safe_params(get_assistant_model())
        
        payload = {
            "model": get_assistant_model(),
            "messages": [{"role": "user", "content": proposal_prompt}],
            "stream": False,
            "options": vram_params
        }
        resp = await get_http_client().post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
        if resp.status_code == 200:
            proposal = resp.json().get("message", {}).get("content", "")
            return f"\n\n### LIVE SYSTEM PROPOSAL (Shadow Execution)\n{proposal}\n\n[Dev Agent: Compare this proposal against the codebase and architectural intent. Identify any deltas and select the optimal path.]"
    except Exception as e:
        log.warning(f"[ShadowExecution] Failed: {e}")
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

    MAX_TOOL_ITERATIONS = 5
    HEARTBEAT_INTERVAL = 15   # seconds between heartbeat log lines
    HUNG_THRESHOLD = 240      # seconds before a HUNG WARNING is emitted
    agent_messages = ollama_payload.get("messages", [])[:]  # shallow copy
    exec_data = None
    ans = ""
    loop_start = asyncio.get_event_loop().time()

    for agent_iter in range(MAX_TOOL_ITERATIONS):
        iter_start = asyncio.get_event_loop().time()
        log.info(f"[AgentLoop] Iteration {agent_iter + 1}/{MAX_TOOL_ITERATIONS} | "
                 f"total elapsed {iter_start - loop_start:.0f}s")

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
            vram_params = await get_vram_safe_params(selected_model)
            ollama_payload["options"] = vram_params
            ollama_payload["messages"] = agent_messages
            
            async with INFERENCE_LOCK:
                log.info(f"[Strategy 8] Inference Lock ACQUIRED for {selected_model} (Iter {agent_iter + 1})")
                resp = await call_ollama(ollama_payload, use_chat=True, timeout=300.0)
                log.info(f"[Strategy 8] Inference Lock RELEASED for {selected_model}")
                
            heartbeat_stop.set()
            await hb_task
            if resp.status_code != 200:
                return JSONResponse({"status": "ERROR", "message": "Brain offline."}, status_code=502)
            data = resp.json()
            ans = data.get("message", {}).get("content", "Error.")
            ollama_ms = (asyncio.get_event_loop().time() - iter_start) * 1000
            log.info(f"[AgentLoop] Ollama responded in {ollama_ms:.0f}ms — iter {agent_iter + 1}")
        except (httpx.TimeoutException, httpx.ConnectError):
            heartbeat_stop.set()
            await hb_task
            ans = "Jarvis is currently operating in low-latency mode due to a downstream service timeout. I am available for core operations, but complex reasoning may be delayed."
            log.warning(f"[AgentLoop] Ollama timeout/connect error on iter {agent_iter + 1}")
            return JSONResponse({"status": "SUCCESS", "message": ans, "degraded": True})

        # 8. Tool Execution — Intercept JSON blocks for execution
        log.info(f"[AgentLoop] Response length: {len(ans)}")
        
        tool_data = None
        
        # Strategy 1: Code fences
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
                            action = "WorkspaceFilePatchRequest" if agent_iter > 0 else "WorkspaceFileWriteRequest"
                            tool_data = {
                                "action": action,
                                "payload": {"path": "auto", "content": code_text}
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
            for nest_key in ("arguments", "payload", "args", "json"):
                if nest_key in tool_data and isinstance(tool_data[nest_key], dict):
                    log.info(f"[AgentLoop] Normalizing tool schema: hoisting '{nest_key}' to top level")
                    nested_vals = tool_data.pop(nest_key)
                    tool_data.update(nested_vals)
            
            mapping = {"offset": "offset_lines", "limit": "limit_lines"}
            for old_key, new_key in mapping.items():
                if old_key in tool_data and new_key not in tool_data:
                    log.info(f"[AgentLoop] Normalizing parameter: '{old_key}' -> '{new_key}'")
                    tool_data[new_key] = tool_data.pop(old_key)
        
        if tool_data is None:
            log.info(f"[AgentLoop] No valid JSON object or array found — final response at iteration {agent_iter + 1}")
            break
            
        if isinstance(tool_data, list) and len(tool_data) > 0:
            tool_data = tool_data[0]
            
        if not isinstance(tool_data, dict):
            log.info(f"[AgentLoop] Parsed JSON is not a dictionary — breaking loop")
            break

        log.info(f"[AgentLoop] Dispatching action: {json.dumps({k: v for k, v in tool_data.items() if k != 'user_context'}, indent=2)}")

        try:
            action = tool_data.get("action") or tool_data.get("tool") or tool_data.get("name") or tool_data.get("type")
            payload = tool_data.get("payload", {})
            if isinstance(tool_data, dict):
                for k, v in tool_data.items():
                    if k not in ("action", "payload", "tool", "name", "type") and k not in payload:
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
                "search": "WorkspaceSearchRequest"
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
                "storagefilereadrequest": (EXECUTION_SVC, "/execute/storage_file_read"),
                "storagefilewriterequest": (EXECUTION_SVC, "/execute/storage_file_write"),
                "systemlearningrequest": (EXECUTION_SVC, "/execute/learning"),
                "discoverysyncrequest": (EXECUTION_SVC, "/execute/discovery_sync"),
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
                agent_messages.append({"role": "assistant", "content": ans})
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
            agent_messages.append({"role": "assistant", "content": ans})
            agent_messages.append({
                "role": "user",
                "content": f"[FATAL ERROR IN TOOL EXECUTION]: {e}\n\nTraceback:\n{tb}\n\nPlease analyze this failure, adjust your approach or parameters, and continue the mission."
            })
            continue

    return JSONResponse({"status": "SUCCESS", "message": ans})


@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def chat_handler(request: Request, background_tasks: BackgroundTasks = None):
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
    selected_model = explicit_model or get_assistant_model()

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
            # Check if specialized coder model is available (hardcoded preference for qwen2.5-coder)
            selected_model = get_coding_model()
            log.info(f"[ChatHandler] Specialized coding task detected. Routing to: {selected_model}")

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

    # 3. Semantic Routing (Fast Path Detection)
    is_background_task = "### Task:" in query or "Generate 1-3 broad tags" in query or "Suggest 3-5 relevant follow-up questions" in query
    
    if is_background_task:
        intent, confidence = "none", 0.0
        is_fast_path = False
        log.info("[FastPath] Skipped for automated background task")
    else:
        intent, confidence = engine.classify(query)
        is_fast_path = engine.should_bypass_llm(confidence)
    
    # 4. Capability Check (Pre-flight)
    required_fields = INTENT_CAPABILITY_MAP.get(intent, [])
    missing_fields = [f for f in required_fields if not getattr(creds, f)]
    if missing_fields:
        log.warning(f"[CapabilityEnforcement] User {user_id} lacks {missing_fields} for intent {intent}")
        persona_prompt = f"The user asked '{query}' but lacks {missing_fields}. Explain that they must visit the Identity page."
        resp = await call_ollama({"model": selected_model, "prompt": persona_prompt, "stream": False}, use_chat=False)
        msg = resp.json().get("response", "Please set up your credentials in Identity.")
        if is_openai:
            return _make_openai_response(msg, selected_model, "redirection")
        return _make_ollama_response(msg, selected_model, "redirection")

    if is_fast_path:
        log.info(f"[FastPath] MATCHED: intent='{intent}' confidence={confidence}")
        # Fast-path is now restricted to non-parameterized system status or simple toggle triggers
        # Device control with parameters (like set_brightness or play_media) MUST go through the LLM for extraction
        endpoint_map = {
            "ha_status": "/health", # Placeholder for status check
            "storage_status": "/health",
        }
        endpoint = endpoint_map.get(intent)
        if endpoint:
            exec_payload = {
                "user_context": creds.model_dump(),
                "entity_id": "auto", # Smart resolution would happen in the execution service
                "action": "turn_on" if intent in ("turn_on", "set_brightness") else ("turn_off" if intent == "turn_off" else "play")
            }
            exec_res = await execute_command(endpoint, exec_payload)
            ans = exec_res.get("message", "Action completed.")
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
    try:
        collections = ["ha_entities", "nextcloud_files", "system_capabilities", "system_learnings"]
        for coll in collections:
            client = get_http_client()
            resp = await client.post(
                f"{RAG_SVC}/rag/search",
                json={"collection_name": coll, "query": query, "user_id": user_id, "k": 15},
                headers={"X-Internal-Secret": INTERNAL_SECRET, "Authorization": f"Bearer {INTERNAL_SECRET}"}
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
    admin_tag = " (ADMIN)" if creds.is_admin else ""
    user_info = f"Current User: {user_id}{admin_tag}"
    
    full_system = f"{system_instruction}\n\n{user_info}\n\n{long_term}\n\n### Capability Context\n{rag_context}{shadow_context}"
    
    final_query = query
    if any(k in query.lower() for k in ["scan", "index", "reindex", "storage", "/notes", "list", "find"]):
        final_query += (
            "\n\n[SYSTEM OVERRIDE: You ARE fully capable of executing this storage action. "
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

    # Detection of autonomous agent engagement
    is_autonomous = any(k in query.lower() for k in ["raven:", "<raven>", "raven mode", "ouroboros:", "jarvis:", "fix the", "patch the", "implement", "autonomous:"])
    
    if is_autonomous:
        # Specialized coding keywords to ensure the system override is descriptive
        final_query += (
            "\n\n[SYSTEM OVERRIDE: AUTONOMOUS DEVELOPER PROTOCOL]\n"
            "You are Raven, an autonomous system repair agent. "
            "You have access to Workspace tools to read and patch files.\n\n"
            "CRITICAL LIMIT: Your context window is limited to 12KB. "
            "When reading files larger than 8KB (like services/gateway/main.py), "
            "YOU MUST use 'summary_only=True' in WorkspaceFileReadRequest to map the file first. "
            "Then use 'offset_lines' and 'limit_lines' to read specific blocks. "
            "Do not attempt to read the entire file if it is large, as it will be truncated.\n\n"
            "Use 'ripgrep' or 'grep' first to find line numbers of functions you need to modify.\n"
            "You have direct access to the local Git workspace.\n"
            "CRITICAL: You must execute ONE step at a time! Output ONE tool call, wait for the system result, then output the next tool call.\n"
            "Follow the standard OODA loop:\n"
            "1. READ: Use `WorkspaceFileReadRequest` to inspect existing code. (STOP and wait for result)\n"
            "2. PATCH: Use `WorkspaceFilePatchRequest` to modify code. (STOP and wait for result)\n"
            "3. LINT: Use `WorkspaceLintRequest` to check the patched file for errors. (STOP and wait for result)\n"
            "   - .py files: runs black + flake8 automatically.\n"
            "   - .js/.ts files: runs eslint automatically.\n"
            "   - .yaml/.json files: runs yamllint / json.tool automatically.\n"
            "   - If lint FAILS, use another PATCH to fix the errors, then lint again.\n"
            "4. COMMIT: Use `GitOperationRequest` (action: 'commit') to save changes.\n\n"
            "[GIT & BRANCH RULES]\n"
            "- Branch: You may commit to the current working branch (like `microservices`) or create a new branch. NEVER push directly to `main` or `development`.\n"
            "- Pull Requests: If you need to merge changes to `main` or `development`, you must create a Pull Request.\n"
            "- Commit Messages: All autonomous commits MUST be prefixed with '[Jarvis Autonomous] '.\n\n"
            "[TOOL CALLING RULES]\n"
            "You MUST execute tools using a standard Markdown JSON block. Example:\n"
            "```json\n"
            "{\n"
            "  \"type\": \"WorkspaceFilePatchRequest\",\n"
            "  \"path\": \"services/execution/main.py\",\n"
            "  \"chunks\": [{\"old_text\": \"...\", \"new_text\": \"...\"}]\n"
            "}\n"
            "```\n"
            "Do NOT just talk about what you will do. Output the JSON tool call immediately."
        )

    vram_params = await get_vram_safe_params(selected_model)

    # 6. Raven Autonomous Loop (Strategy 7 & 8 implementation)
    if is_autonomous:
        log.info("[ShadowExecution] Routing to autonomous AgentLoop...")
        return await AgentLoop(final_query, selected_model, full_system, short_term, body.get("rag_user"), creds)

    ollama_payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": full_system}
        ] + short_term + [{"role": "user", "content": final_query}],
        "options": vram_params,
        "stream": should_stream
    }

    if should_stream:
        async def stream_generator():
            full_ans = ""
            suppressing = False
            async with INFERENCE_LOCK:
                log.info(f"[Strategy 8] Inference Lock ACQUIRED for {selected_model}")
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=ollama_payload) as resp:
                        async for line in resp.aiter_lines():
                            if not line: continue
                            try:
                                chunk = json.loads(line)
                                content = chunk.get("message", {}).get("content", "")
                                if content:
                                    full_ans += content
                                    # Detection of ANY code block OR raw background JSON to start suppression
                                    if not suppressing and ("```json" in full_ans or (full_ans.strip().startswith("{") and len(full_ans.strip()) > 10)):
                                        suppressing = True
                                    if not suppressing:
                                        if is_openai:
                                            yield f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}\n\n"
                                        else:
                                            yield line + "\n"
                                if chunk.get("done"): break
                            except: pass
                log.info(f"[Strategy 8] Inference Lock RELEASED for {selected_model}")
            
            # 8. Handle tool execution at the end of the stream
            log.info(f"[StreamGenerator] Full response length: {len(full_ans)}")
            log.info(f"[StreamGenerator] RAW RESPONSE: {full_ans}")
            if "```" in full_ans:
                log.info(f"[StreamGenerator] Block detected. Content preview: {full_ans[full_ans.find('```'):][:100]}...")
                
            # Detect tool block (either ```json or raw ``` if it looks like JSON)
            if "```json" in full_ans or ("```" in full_ans and "action" in full_ans and "payload" in full_ans):
                try:
                    tag = "```json" if "```json" in full_ans else "```"
                    start = full_ans.find(tag) + len(tag)
                    end = full_ans.find("```", start)
                    tool_json = full_ans[start:end].strip()
                    tool_data = json.loads(tool_json)
                    # Handle model wrapping tool call in an array
                    if isinstance(tool_data, list) and len(tool_data) > 0:
                        tool_data = tool_data[0]
                    
                    action = tool_data.get("action") or tool_data.get("tool") or tool_data.get("name") or tool_data.get("type")
                    payload = tool_data.get("payload", {})
                    # PIPELINE HARDENING: If model provides flat JSON or mixed keys, merge them into payload
                    if isinstance(tool_data, dict):
                        for k, v in tool_data.items():
                            if k not in ("action", "payload", "tool", "name", "type") and k not in payload:
                                if "path" in k.lower():
                                    payload["path"] = v
                                else:
                                    payload[k] = v
                    
                    if not action:
                        if "path" in payload:
                            if (payload.get("is_patch") or "chunks" in payload):
                                action = "WorkspaceFilePatchRequest"
                                log.info(f"[StreamToolExecution] Falling back to WorkspaceFilePatchRequest for flat JSON")
                            elif payload.get("content") is not None:
                                action = "WorkspaceFileWriteRequest"
                                log.info(f"[StreamToolExecution] Falling back to WorkspaceFileWriteRequest for flat JSON")
                            else:
                                action = "WorkspaceFileReadRequest"
                                log.info(f"[StreamToolExecution] Falling back to WorkspaceFileReadRequest for flat JSON (content was null)")
                    action_map = {
                        "lightcontrolrequest": (EXECUTION_SVC, "/execute/light"),
                        "light_control": (EXECUTION_SVC, "/execute/light"),
                        "mediaplayrequest": (EXECUTION_SVC, "/execute/media/play"),
                        "media_play": (EXECUTION_SVC, "/execute/media/play"),
                        "mediatransportrequest": (EXECUTION_SVC, "/execute/media/transport"),
                        "media_transport": (EXECUTION_SVC, "/execute/media/transport"),
                        "tvcastrequest": (EXECUTION_SVC, "/execute/tv_cast"),
                        "tv_cast": (EXECUTION_SVC, "/execute/tv_cast"),
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
                        "workspacefileaction": (WORKSPACE_RUNTIME_SVC, "/files/write"),
                        "workspacegitaction": (WORKSPACE_RUNTIME_SVC, "/git/commit"),
                        "workspacesyncaction": (WORKSPACE_RUNTIME_SVC, "/workflow/write-sync-commit"),
                        "filereadrequest": (WORKSPACE_RUNTIME_SVC, "/files/read"),
                        "filelistrequest": (WORKSPACE_RUNTIME_SVC, "/files/list"),
                        "filewriterequest": (WORKSPACE_RUNTIME_SVC, "/files/write"),
                        "pytestrequest": (WORKSPACE_RUNTIME_SVC, "/tests/pytest"),
                        "diffrequest": (WORKSPACE_RUNTIME_SVC, "/git/diff"),
                        "gitaddrequest": (WORKSPACE_RUNTIME_SVC, "/git/add"),
                        "gitcommitrequest": (WORKSPACE_RUNTIME_SVC, "/git/commit"),
                        "gitstatusrequest": (WORKSPACE_RUNTIME_SVC, "/git/status"),
                        "gitdiffrequest": (WORKSPACE_RUNTIME_SVC, "/git/diff"),
                        "gitbranchcreaterequest": (WORKSPACE_RUNTIME_SVC, "/git/branch/create"),
                        "gitpushrequest": (WORKSPACE_RUNTIME_SVC, "/git/push"),
                        "gitfetchrequest": (WORKSPACE_RUNTIME_SVC, "/git/fetch"),
                        "gitpullrequest": (WORKSPACE_RUNTIME_SVC, "/git/pull"),
                        "gitpull": (WORKSPACE_RUNTIME_SVC, "/git/pull"),
                        "gitrebaserequest": (WORKSPACE_RUNTIME_SVC, "/git/rebase"),
                        "storagestatusrequest": (STORAGE_SVC, "/status"),
                        "storagelistrequest": (STORAGE_SVC, "/providers/list"),
                        "storageindexrequest": (STORAGE_SVC, "/index/full"),
                        "workspace_file_read": (EXECUTION_SVC, "/execute/workspace_file_read"),
                        "workspacefilereadrequest": (EXECUTION_SVC, "/execute/workspace_file_read"),
                        "read": (EXECUTION_SVC, "/execute/workspace_file_read"),
                        "workspace_file_write": (EXECUTION_SVC, "/execute/workspace_file_write"),
                        "workspacefilewriterequest": (EXECUTION_SVC, "/execute/workspace_file_write"),
                        "write": (EXECUTION_SVC, "/execute/workspace_file_write"),
                        "storage_file_read": (EXECUTION_SVC, "/execute/storage_file_read"),
                        "storagefilereadrequest": (EXECUTION_SVC, "/execute/storage_file_read"),
                        "storage_file_write": (EXECUTION_SVC, "/execute/storage_file_write"),
                        "storagefilewriterequest": (EXECUTION_SVC, "/execute/storage_file_write"),
                        "systemlearningrequest": (EXECUTION_SVC, "/execute/learning"),
                    }
                    
                    # Normalize action name for lookup
                    lookup_action = action.lower().strip() if action else ""
                    
                    if lookup_action in ("storageindexrequest", "storagelistrequest"):
                        action_map[lookup_action] = (STORAGE_SVC, "/index/full" if lookup_action == "storageindexrequest" else "/providers/list")
                        payload = {
                            "provider": {
                                "kind": "nextcloud",
                                "settings": {
                                    "url": creds.nextcloud_url,
                                    "username": creds.nextcloud_user,
                                    "password": creds.nextcloud_pass
                                }
                            },
                            "path": (payload or {}).get("path", "/"),
                            "recursive": (payload or {}).get("recursive", True)
                        }
                    
                    if lookup_action in action_map:
                        svc_base, endpoint = action_map[lookup_action]
                        
                        # ALWAYS overwrite user context with real resolved credentials
                        # Filtered for compatibility with Execution schemas
                        payload["user_context"] = {
                            "user": creds.user,
                            "is_admin": creds.is_admin,
                            "ha_url": creds.ha_url,
                            "ha_token": creds.ha_token
                        }
                        
                        log.info(f"[StreamToolExecution] Triggering {action} with payload keys: {list(payload.keys())}")
                        log.debug(f"[StreamToolExecution] Full Payload: {payload}")
                        exec_resp = await get_http_client().post(
                            f"{svc_base}{endpoint}",
                            json=payload,
                            headers={"X-Internal-Secret": INTERNAL_SECRET},
                            timeout=120.0
                        )
                        
                        if exec_resp.status_code == 200:
                            exec_data = exec_resp.json()
                            exec_msg = exec_data.get("message", "Action completed.")
                            # PIPELINE HARDENING: Append detail (logs, code) for the agent to 'see'
                            detail = exec_data.get("detail")
                            if detail:
                                if isinstance(detail, dict):
                                    if "content" in detail:
                                        detail_txt = str(detail["content"])
                                    elif "logs" in detail:
                                        detail_txt = str(detail["logs"])
                                    elif "lines" in detail:
                                        detail_txt = "\n".join(detail["lines"][:100])
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
                        
                        update_text = f"\n\n**System Update**: {exec_msg}"
                        full_ans = full_ans[:full_ans.find("```json")].strip() + update_text
                        
                        if is_openai:
                            yield f"data: {json.dumps({'choices': [{'delta': {'content': update_text}}]})}\n\n"
                        else:
                            # Ollama format chunk
                            final_chunk = {"message": {"role": "assistant", "content": update_text}, "done": False}
                            yield json.dumps(final_chunk) + "\n"
                except Exception as e:
                    log.error(f"Streaming tool execution failed: {e}")

            if not is_background_task:
                await update_history(user_id, "user", query)
                await update_history(user_id, "assistant", full_ans)
            else:
                log.info(f"[Background] Skipping history update for task: {query[:50]}...")
                
            if not is_openai:
                yield json.dumps({"done": True}) + "\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream" if is_openai else "application/x-ndjson")

    # Non-streaming — Standard Response
    async with INFERENCE_LOCK:
        log.info(f"[Strategy 8] Inference Lock ACQUIRED for {selected_model}")
        resp = await call_ollama(ollama_payload, use_chat=True)
        log.info(f"[Strategy 8] Inference Lock RELEASED for {selected_model}")

    if resp.status_code != 200:
        return JSONResponse({"status": "ERROR", "message": "Brain offline."}, status_code=502)
    
    data = resp.json()
    ans = data.get("message", {}).get("content", "Error.")
    
    # Update History
    if not is_openai:
        await update_history(user_id, "user", query)
        await update_history(user_id, "assistant", ans)
    else:
        log.info(f"[Background] Skipping history update for task: {query[:50]}...")
    
    if background_tasks:
        background_tasks.add_task(extract_and_store_user_facts, user_id, short_term + [{"role": "user", "content": query}])

    resp_data = {
        "execution_result": exec_data,
        "intent": intent,
        "confidence": confidence if 'confidence' in locals() else 1.0
    }
    
    if is_openai:
        return _make_openai_response(ans, selected_model, intent, extra_fields=resp_data)
    
    base_resp = _make_ollama_response(ans, selected_model, intent)
    # Merge additional data into the Ollama response body
    if isinstance(base_resp, JSONResponse):
        final_content = json.loads(base_resp.body.decode())
        final_content.update(resp_data)
        return JSONResponse(status_code=base_resp.status_code, content=final_content)
    return base_resp
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
    async with get_http_client() as client:
        resp = await client.post(
            f"{IDENTITY_SVC}/api/users/{username}/password",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/auth/import/nextcloud")
async def proxy_import_nextcloud_users(request: Request):
    async with get_http_client() as client:
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
    await _resolve_identity_from_request(request)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{EXECUTION_SVC}/execute/timers",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
    return JSONResponse(status_code=resp.status_code, content=resp.json())


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
async def proxy_read_calendar(request: Request):
    payload = {"action": "read"}
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
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/read")
async def proxy_read_note(request: Request):
    body = await request.json()
    payload = {
        "action": "read",
        "title": body.get("title"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/append")
async def proxy_append_note(request: Request):
    body = await request.json()
    payload = {
        "action": "append",
        "title": body.get("title"),
        "content": body.get("content"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/notes/delete")
async def proxy_delete_note(request: Request):
    body = await request.json()
    payload = {
        "action": "delete",
        "title": body.get("title"),
    }
    return await _proxy_execution_with_identity(request, "/execute/note", payload)


@app.post("/api/communication/announcements")
async def proxy_send_announcement(request: Request):
    body = await request.json()
    payload = {
        "entity_id": body.get("entity_id"),
        "message": body.get("message"),
        "volume": body.get("volume", 0.6),
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
    except Exception as e:
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
async def get_workspaces_proxy():
    """Proxy to workspace runtime."""
    try:
        resp = await get_http_client().get(
            f"{WORKSPACE_RUNTIME_SVC}/workspaces",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return resp.json()
    except Exception as e:
        log.error(f"Workspaces proxy failed: {e}")
        return []

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
async def git_pull_workspace_proxy(request: Request):
    return await _proxy_workspace_runtime_json("POST", "/git/pull", request)

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
        user_id = creds_data.get("nextcloud_user") or creds_data.get("user", "default")
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
    
    async with get_http_client() as client:
        resp = await client.post(
            f"{RAG_SVC}/rag/purge/{collection_name}?user_id={user_id}",
            json=body.get("filter", {}),
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/admin/tests/smoke")
async def proxy_smoke_test(request: Request):
    async with get_http_client() as client:
        resp = await client.post(
            f"{WORKSPACE_RUNTIME_SVC}/api/admin/tests/smoke",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=65.0
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/admin/volumes")
async def proxy_admin_volumes(request: Request):
    creds_data = await _resolve_identity_from_request(request)
    if not creds_data.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")

    async with get_http_client() as client:
        resp = await client.post(
            f"{EXECUTION_SVC}/execute/volumes",
            json={"user_context": creds_data},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=120.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
# ---- Config Endpoints ----

@app.get("/api/config/models")
async def get_ollama_models():
    """Proxy to Ollama to list available tags."""
    OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                return {"status": "SUCCESS", "models": [m["name"] for m in data.get("models", [])]}
            return {"status": "ERROR", "message": f"Ollama returned {resp.status_code}"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

@app.get("/api/config")
async def get_gateway_config():
    return {"status": "SUCCESS", "config": CONFIG}

@app.post("/api/config")
async def update_gateway_config(new_config: dict):
    global CONFIG
    if "assistant_model" in new_config:
        CONFIG["assistant_model"] = new_config["assistant_model"]
    if "coding_model" in new_config:
        CONFIG["coding_model"] = new_config["coding_model"]
    if "librarian_model" in new_config:
        CONFIG["librarian_model"] = new_config["librarian_model"]
    log.info(f"Updated Gateway Config: {CONFIG}")
    return {"status": "SUCCESS", "config": CONFIG}
