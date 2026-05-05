# services/gateway/main.py
import os
import logging
import json
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import re
import traceback
from datetime import datetime

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
        return res

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
        return res

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
    from .schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
    from .intent_engine import engine
    from .history import get_history, update_history, ping_redis
    from .prompts import CODE_HELPER_SYSTEM_INSTRUCTION, LIBRARIAN_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT
except (ImportError, ValueError):
    try:
      from gateway.schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
      from gateway.intent_engine import engine
      from gateway.history import get_history, update_history, ping_redis
      from gateway.prompts import CODE_HELPER_SYSTEM_INSTRUCTION, LIBRARIAN_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT
    except ImportError:
      from schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
      from intent_engine import engine
      from history import get_history, update_history, ping_redis
      from prompts import CODE_HELPER_SYSTEM_INSTRUCTION, LIBRARIAN_SYSTEM_INSTRUCTION, MEDIA_TROUBLESHOOTING_PROMPT

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
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "60.0"))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:8b")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", DEFAULT_MODEL)
CODING_MODEL = os.getenv("CODING_MODEL", ASSISTANT_MODEL)
LIBRARIAN_MODEL = os.getenv("LIBRARIAN_MODEL", ASSISTANT_MODEL)

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

# --- Global Clients ---
_global_http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _global_http_client
    _global_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=5.0),
        transport=httpx.AsyncHTTPTransport(retries=3)
    )
    log.info("Gateway starting up...")
    engine.load()
    yield
    await _global_http_client.aclose()
    log.info("Gateway shutting down...")

app = FastAPI(title="Jarvis OS Gateway", version="1.0.0", lifespan=lifespan)

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

    if ping_redis():
      results["services"]["redis"] = "OK"
    else:
      results["services"]["redis"] = "UNREACHABLE"
      all_ok = False

    if not all_ok:
      results["status"] = "NOT_READY"
    return results

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
        payload = {"model": ASSISTANT_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.0}}
        resp = await _global_http_client.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=5.0)
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
      return CODING_MODEL
    if any(token in q for token in LIBRARIAN_SIGNALS):
      return LIBRARIAN_MODEL
    return ASSISTANT_MODEL


def select_system_instruction_for_query(query: str, selected_model: str) -> str:
    q = (query or "").lower()
    if any(token in q for token in CODING_SIGNALS):
      return CODE_HELPER_SYSTEM_INSTRUCTION
    if selected_model == LIBRARIAN_MODEL or any(token in q for token in LIBRARIAN_SIGNALS):
      return LIBRARIAN_SYSTEM_INSTRUCTION
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
    client = _global_http_client
    if client is None:
      raise RuntimeError("Gateway HTTP client is not initialized")

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

    available = [item for item in workspaces if isinstance(item, dict) and item.get("available")]
    if workspace_id:
      for item in available:
          if item.get("id") == workspace_id:
              return item
      return None

    for item in available:
        if str(item.get("scope") or "user") == "user":
            return item
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


async def call_ollama(payload: dict, use_chat: bool = True) -> httpx.Response:
    endpoint = "/api/chat" if use_chat else "/api/generate"
    return await _global_http_client.post(
      f"{OLLAMA_URL}{endpoint}",
      json=payload,
      timeout=OLLAMA_TIMEOUT,
    )


async def troubleshoot_media_failure(query: str, failure: str) -> dict | None:
    prompt = (
      f"{MEDIA_TROUBLESHOOTING_PROMPT}\n"
      f"User request: {query}\n"
      f"Failure: {failure}"
    )
    try:
      resp = await call_ollama(
          {"model": ASSISTANT_MODEL, "prompt": prompt, "stream": False},
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

async def fetch_ha_entities(creds: dict) -> list:
    try:
      resp = await _global_http_client.get(
          f"{EXECUTION_SVC}/discovery/entities",
          params={"ha_url": creds.get("ha_url"), "ha_token": creds.get("ha_token")},
          headers={"X-Internal-Secret": INTERNAL_SECRET},
          timeout=5.0
      )
      if resp.status_code != 200:
          log.warning(f"Failed to fetch entities: {resp.status_code}")
          return []

      data = resp.json()
      entities = data.get("entities", []) if isinstance(data, dict) else []
      if entities:
          # Async sync task
          asyncio.create_task(_global_http_client.post(
            f"{RAG_SVC}/rag/sync/ha",
            json={"entities": entities, "user_id": creds.get("user", "admin")},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
          ))
      return entities
    except Exception as e:
      log.error(f"Entity discovery error: {e}")
      return []

async def fetch_device_history(creds: dict, entity_id: str, days: int = 1) -> list:
    try:
      resp = await _global_http_client.get(
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
      resp = await _global_http_client.post(
          f"{EXECUTION_SVC}{endpoint}",
          json=payload,
          headers={"X-Internal-Secret": INTERNAL_SECRET},
          timeout=30.0
      )
      data = resp.json()
      if not isinstance(data, dict):
          return {"status": "FAILURE", "message": str(data)}
      return data
    except Exception as e:
      return {"status": "FAILURE", "message": str(e)}

# --- Chat Handler ---
@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def chat_handler(request: Request):
    client = _global_http_client
    # 1. Resolve Identity
    try:
        body = await request.json()
    except:
        body = {}
    if not isinstance(body, dict): body = {}
    
    # Standardized API flags
    is_openai = "/v1/chat/completions" in str(request.url)
    should_stream = body.get("stream", False)
    explicit_model = str(body.get("model") or "").strip()
    explicit_model_requested = bool(explicit_model)
    selected_model = explicit_model or ASSISTANT_MODEL

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

    creds = await resolve_identity(body)
    if not isinstance(creds, dict):
        return JSONResponse({"status": "ERROR", "message": "Identity resolution failed."}, status_code=500)
    
    user_id = creds.get("user", "admin")
    history = await get_history(user_id)
    real_entities = await fetch_ha_entities(creds)
    
    await emit_log("INFO", f"Chat request from {user_id}", {"query": query, "entities_count": len(real_entities)})

    # 2. Contextualize & Decompose
    refined_query = await contextualize_query(query, history)
    sub_commands = await decompose_command_query(refined_query)
    media_query, _ = extract_media_request(refined_query)
    media_transport_command = extract_media_transport_command(refined_query)
    is_video_request = is_likely_video_request(refined_query)
    is_code_request = is_coding_query(refined_query)
    wants_workspace_readme = wants_workspace_readme_generation(refined_query)
    explicit_action_request = has_explicit_action_request(refined_query)
    wants_status_followup = requests_status_followup(refined_query)

    if wants_workspace_readme:
        if not explicit_model_requested:
            selected_model = CODING_MODEL
        return await generate_workspace_readme_via_coding_model(
            body=body,
            user_id=user_id,
            refined_query=refined_query,
            selected_model=selected_model,
            should_stream=should_stream,
            is_openai=is_openai,
        )
    
    # 3. Fast Path (Semantic Routing)
    intent, confidence = engine.classify(refined_query)
    log.info(f"Intent Classification: query='{refined_query}' intent='{intent}' confidence={confidence}")
    
    if media_transport_command:
        intent = "media_transport"
        confidence = 1.0

    if is_code_request and intent not in ("unknown", "code_orchestrate"):
        log.info(f"Bypassing fast-path intent '{intent}' for coding query.")
        intent = "unknown"
        confidence = 0.0

    if confidence >= FAST_PATH_THRESHOLD:
        log.info(f"[FastPath] MATCHED: intent='{intent}' confidence={confidence}")
        
        if intent == "index_storage":
            await emit_log("INFO", "Triggering full library index...")
            async with httpx.AsyncClient(timeout=300.0) as client:
                try:
                    idx_payload = {
                        "provider": {
                            "kind": "nextcloud",
                            "settings": {
                                "url": creds.get("nextcloud_url"),
                                "username": creds.get("nextcloud_user"),
                                "password": creds.get("nextcloud_pass")
                            }
                        },
                        "path": "/",
                        "recursive": True
                    }
                    resp = await client.post(
                        f"{STORAGE_SVC}/index/full",
                        json=idx_payload,
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    if resp.status_code == 200:
                        msg = "I have started indexing your library in the background."
                        if is_openai:
                            return _make_openai_response(msg, selected_model, "index_storage", stream=should_stream)
                        return _make_ollama_response(msg, selected_model, "index_storage", stream=should_stream)
                except Exception as e:
                    log.error(f"Index trigger failed: {e}")
                    return JSONResponse({"status": "ERROR", "message": "The storage service is not responding."}, status_code=502)

        # Simple routing map
        endpoint_map = {
            "turn_on": "/execute/light",
            "turn_off": "/execute/light",
            "play_media": "/execute/media/play",
            "media_transport": "/execute/media/transport",
            "pause_media": "/execute/media/transport",
            "open_garage": "/execute/security",
            "close_garage": "/execute/security",
            "toggle": "/execute/light",
            "set_brightness": "/execute/light"
        }

        endpoint = endpoint_map.get(intent)
        
        # Override: Status queries should NOT be fast-pathed as actions
        status_keywords = {"state", "status", "how is", "what is", "tell me about", "check"}
        if (
            any(kw in refined_query.lower() for kw in status_keywords)
            and intent in {"turn_on", "turn_off"}
            and not explicit_action_request
        ):
            endpoint = None
            log.info(f"Overriding {intent} fast-path for status inquiry.")

        if intent == "sync_ha":
            # Manual HA Sync
            sync_res = await client.post(f"{RAG_SVC}/rag/sync/ha", json={"entities": real_entities, "user_id": user_id}, timeout=30.0, headers={"X-Internal-Secret": INTERNAL_SECRET})
            if sync_res.status_code == 200:
                data = sync_res.json()
                msg = f"Successfully reingested {data.get('count', 0)} devices. Found {data.get('new_count', 0)} new devices."
            else:
                msg = "Failed to reingest devices. Check RAG logs."
            if is_openai:
                return _make_openai_response(msg, selected_model, intent, stream=should_stream)
            return _make_ollama_response(msg, selected_model, intent, stream=should_stream)

        if intent == "ha_status":
            # Status & New Devices Check
            status_res = await client.get(f"{RAG_SVC}/rag/ha/status", params={"user_id": user_id}, headers={"X-Internal-Secret": INTERNAL_SECRET})
            new_res = await client.get(f"{RAG_SVC}/rag/ha/new", params={"user_id": user_id}, headers={"X-Internal-Secret": INTERNAL_SECRET})
            
            msg = "Home Assistant Status Check:\n"
            if status_res.status_code == 200:
                s_data = status_res.json().get("data", {})
                ts = s_data.get("timestamp", 0)
                from datetime import datetime
                dt_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else "Never"
                msg += f"- Last Sync: {dt_str}\n- Total Devices: {s_data.get('count', 0)}\n"
            
            if new_res.status_code == 200:
                n_data = new_res.json().get("devices", [])
                if n_data:
                    msg += f"\nRecent New Devices ({len(n_data)}):\n"
                    for dev in n_data[:5]:
                        msg += f"- {dev.get('friendly_name')} ({dev.get('entity_id')}) in {dev.get('area')}\n"
                else:
                    msg += "\nNo new devices detected in the last 24 hours."
            
            if is_openai:
                return _make_openai_response(msg, selected_model, intent, stream=should_stream)
            return _make_ollama_response(msg, selected_model, intent, stream=should_stream)

        if intent == "code_orchestrate":
            return await orchestrate_code_change(body, user_id, refined_query, selected_model, should_stream, is_openai)

        if intent == "storage_status":
            # Library Indexing Status Check
            stats_res = await client.get(f"{RAG_SVC}/rag/stats", params={"user_id": user_id}, headers={"X-Internal-Secret": INTERNAL_SECRET})
            
            msg = "Library Indexing Status:\n"
            if stats_res.status_code == 200:
                s_data = stats_res.json().get("stats", {})
                nc_stats = s_data.get("nextcloud_files", {})
                count = nc_stats.get("count", 0)
                previews = nc_stats.get("latest_previews", [])
                
                msg += f"- Total Indexed Chunks: {count}\n"
                if count > 0 and previews:
                    msg += "\nMost Recent Files Indexed:\n"
                    for p in previews[:5]:
                        msg += f"- {p}\n"
                else:
                    msg += "- No files have been indexed yet or the scan is still in progress."
            else:
                msg = "Failed to retrieve storage stats. Check RAG logs."
                
            if is_openai:
                return _make_openai_response(msg, selected_model, intent, stream=should_stream)
            return _make_ollama_response(msg, selected_model, intent, stream=should_stream)

        if intent == "ha_status":
            status_res = await client.get(f"{RAG_SVC}/rag/ha/status", params={"user_id": user_id}, headers={"X-Internal-Secret": INTERNAL_SECRET})
            msg = "Home Assistant Status:\n"
            if status_res.status_code == 200:
                s_data = status_res.json().get("data", {})
                ts = s_data.get("timestamp", 0)
                timestamp = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else "Never"
                msg += f"- Last Sync: {timestamp}\n"
                msg += f"- Total Entities: {s_data.get('count', 0)}\n"
                msg += f"- New in Last Sync: {s_data.get('new_count', 0)}\n"
            else:
                msg = "No recent Home Assistant sync data found. Try 'reindex my devices'."
            
            if is_openai:
                return _make_openai_response(msg, selected_model, intent, stream=should_stream)
            return _make_ollama_response(msg, selected_model, intent, stream=should_stream)

        # Extract parameters for fast-path
        brightness_pct = None
        if intent == "set_brightness":
            b_match = re.search(r"(\d+)\s*%", refined_query)
            if b_match:
                brightness_pct = int(b_match.group(1))
            else:
                # Try plain number
                b_match = re.search(r"(\d+)\s*(?:percent|brightness)", refined_query)
                if b_match:
                    brightness_pct = int(b_match.group(1))

        if endpoint:
            target_entity = "auto"
            query_normalized = refined_query.lower().replace("-", " ")
            for e in real_entities:
                friendly_name = (e.get("attributes") or {}).get("friendly_name") or ""
                fname_normalized = friendly_name.lower().replace("-", " ")
                eid = e.get("entity_id", "").lower()
                if fname_normalized and fname_normalized in query_normalized:
                    if "media" in intent and eid.startswith("media_player."):
                        target_entity = e["entity_id"]
                        break
                    if ("light" in intent or "turn" in intent or "toggle" in intent) and eid.startswith("light."):
                        target_entity = e["entity_id"]
                        break

            if target_entity == "auto":
                if "media" in intent:
                    players = [e for e in real_entities if e['entity_id'].startswith('media_player.')]
                    if players: target_entity = players[0]['entity_id']
                elif "light" in intent or "turn" in intent or "toggle" in intent:
                    lights = [e for e in real_entities if e['entity_id'].startswith('light.')]
                    if lights: target_entity = lights[0]['entity_id']

            if intent in {"play_media", "media_transport", "pause_media"} and (media_query or media_transport_command) and not is_video_request:
                target_entity = resolve_media_target(refined_query, real_entities)

            exec_payload = {
                "user_context": creds,
                "entity_id": target_entity,
                "action": "turn_on" if intent in ("turn_on", "set_brightness") else ("turn_off" if intent == "turn_off" else ("toggle" if intent == "toggle" else "play"))
            }
            if brightness_pct is not None:
                exec_payload["brightness_pct"] = brightness_pct

            if intent == "play_media":
                if media_query:
                    exec_payload["query"] = media_query
                    exec_payload["media_content_type"] = "artist"
                else:
                    exec_payload["media_content_id"] = "http://stream.radioparadise.com/flac"
                    exec_payload["media_content_type"] = "music"
            elif intent in {"media_transport", "pause_media"}:
                exec_payload["command"] = media_transport_command

            prior_entity = next(
                (
                    entity for entity in real_entities
                    if isinstance(entity, dict) and entity.get("entity_id") == target_entity
                ),
                None,
            )
            prior_state = prior_entity.get("state") if isinstance(prior_entity, dict) else None
            exec_res = await execute_command(endpoint, exec_payload)
            response_message = exec_res.get("message", "Executed")

            if wants_status_followup and target_entity != "auto":
                refreshed_entity = None
                for _ in range(4):
                    await asyncio.sleep(0.75)
                    refreshed_entities = await fetch_ha_entities(creds)
                    refreshed_entity = next(
                        (
                            entity for entity in refreshed_entities
                            if isinstance(entity, dict) and entity.get("entity_id") == target_entity
                        ),
                        None,
                    )
                    if not refreshed_entity:
                        continue
                    if prior_state is None or refreshed_entity.get("state") != prior_state:
                        break

                if refreshed_entity:
                    attrs = refreshed_entity.get("attributes") or {}
                    friendly_name = attrs.get("friendly_name") or target_entity
                    current_state = refreshed_entity.get("state", "unknown")
                    response_message = (
                        f"{response_message} Current status of {friendly_name} is {current_state}."
                    )

            if is_openai:
                return _make_openai_response(response_message, selected_model, intent, stream=should_stream)
            return _make_ollama_response(response_message, selected_model, intent, stream=should_stream)

    # 4. Context Injection (RAG + Storage + Logs + History)
    rag_context = ""
    results = [] 
    
    try:
        if not explicit_model_requested:
            selected_model = select_model_for_query(refined_query)
        q_lower = refined_query.lower()
        is_coding_task = is_coding_query(refined_query) or (explicit_model_requested and selected_model == CODING_MODEL)
        is_librarian_task = is_librarian_query(refined_query)

        if is_coding_task:
            rag_context += (
                "\nCode Workspace Context:\n"
                "- No live local Git workspace is attached to this gateway path.\n"
                "- Any storage-backed repository context below is non-authoritative companion material.\n"
                "- For authoritative code state, diffs, tests, and branch history, prefer a mapped local checkout.\n"
            )

        # Optimize HA Sync: Only sync if not done recently
        sync_key = f"ha_sync_{user_id}"
        last_sync = getattr(app.state, sync_key, 0)
        import time
        if time.time() - last_sync > 3600:
            try:
                await client.post(f"{RAG_SVC}/rag/sync/ha", json={"entities": real_entities, "user_id": user_id}, timeout=10.0)
                setattr(app.state, sync_key, time.time())
            except Exception as e:
                log.warning(f"Background HA sync failed: {e}")

        # Prepare parallel tasks
        tasks = []
        task_names = []

        # Task 1: HA Entity Search
        ha_keywords = [
            r"\bstatus\b", r"\bstate\b", r"\bdevice\b", r"\bhome\b", r"\bsensor\b", r"\blight\b", r"\bswitch\b", 
            r"\bdoor\b", r"\block\b", r"\btemp\b", r"\bhumidity\b", r"\bbattery\b", r"\blamp\b", r"\bpiano\b",
            r"\btv\b", r"\bplug\b", r"\bfan\b", r"\bclimate\b"
        ]
        if any(re.search(kw, q_lower) for kw in ha_keywords):
            tasks.append(client.post(
                f"{RAG_SVC}/rag/search",
                json={"query": refined_query, "user_id": user_id, "collection_name": "ha_entities", "k": 10},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            ))
            task_names.append("ha_rag")

        # Task 2: Log Context
        log_keywords = [r"\blog\b", r"\blogs\b", r"\bhealth\b", r"\bstatus\b", r"\bissue\b", r"\berror\b", r"\bbroken\b"]
        if any(re.search(kw, q_lower) for kw in log_keywords):
            tasks.append(client.get(f"{LOGGING_SVC_URL}/logs", params={"limit": 5}))
            task_names.append("logs")

        # Task 3: NextCloud RAG Search
        if is_librarian_task:
            tasks.append(client.post(
                f"{RAG_SVC}/rag/search",
                json={"query": refined_query, "user_id": user_id, "collection_name": "nextcloud_files", "k": 5},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            ))
            task_names.append("nc_rag")
            
            # Task 4: NextCloud Real-time Search
            tasks.append(client.post(
                f"{STORAGE_SVC}/providers/search",
                params={"query": refined_query},
                json={
                    "provider": {"kind": "nextcloud", "settings": {"url": creds.get("nextcloud_url"), "username": creds.get("nextcloud_user"), "password": creds.get("nextcloud_pass")}},
                    "path": "/", "recursive": False
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            ))
            task_names.append("nc_realtime")

        # Task 5: Code-adjacent document search
        if is_coding_task:
            tasks.append(client.post(
                f"{RAG_SVC}/rag/search",
                json={"query": refined_query, "user_id": user_id, "collection_name": "nextcloud_files", "k": 5},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            ))
            task_names.append("code_rag")

            if should_search_storage_for_code_query(refined_query):
                tasks.append(client.post(
                    f"{STORAGE_SVC}/providers/search",
                    params={"query": refined_query},
                    json={
                        "provider": {"kind": "nextcloud", "settings": {"url": creds.get("nextcloud_url"), "username": creds.get("nextcloud_user"), "password": creds.get("nextcloud_pass")}},
                        "path": "/", "recursive": False
                    },
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                ))
                task_names.append("code_realtime")

        # Task 6: System Capability Search (Self-Awareness)
        # Always search for relevant capabilities to provide schemas/intents
        tasks.append(client.post(
            f"{RAG_SVC}/rag/search",
            json={"query": refined_query, "user_id": "default", "collection_name": "system_capabilities", "k": 5},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        ))
        task_names.append("cap_rag")

        # Execute parallel tasks
        if tasks:
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for name, resp in zip(task_names, responses):
                if isinstance(resp, Exception):
                    log.error(f"Context task {name} failed: {resp}")
                    continue
                
                if not isinstance(resp, httpx.Response) or resp.status_code != 200:
                    continue

                try:
                    data = resp.json()
                    if name == "ha_rag":
                        results = data.get("results", [])
                        if isinstance(results, list):
                            lines = [str(r.get("content")) for r in results if isinstance(r, dict) and r.get("content")]
                            if lines:
                                rag_context += "\nRelevant Device Context:\n" + "\n".join(lines)
                    elif name == "logs":
                        if isinstance(data, list):
                            log_text = "\n".join([f"[{l.get('timestamp')}] {l.get('service')}: {l.get('message')}" for l in data if isinstance(l, dict)])
                            rag_context += f"\n\nLatest Application Logs:\n{log_text}"
                    elif name == "nc_rag":
                        file_results = data.get("results", [])
                        if isinstance(file_results, list):
                            file_lines = []
                            for r in file_results:
                                if not isinstance(r, dict): continue
                                meta = r.get("metadata", {})
                                name_val = meta.get("name", "file")
                                path = meta.get("path", "unknown")
                                content = str(r.get("content", ""))[:200]
                                file_lines.append(f"- {name_val} ({path}): {content}...")
                            if file_lines:
                                rag_context += f"\n\nRelevant NextCloud Content:\n" + "\n".join(file_lines)
                    elif name == "code_rag":
                        file_results = data.get("results", [])
                        if isinstance(file_results, list):
                            file_lines = []
                            for r in file_results:
                                if not isinstance(r, dict):
                                    continue
                                meta = r.get("metadata", {})
                                name_val = meta.get("name", "file")
                                path = meta.get("path", "unknown")
                                content = str(r.get("content", ""))[:200]
                                file_lines.append(f"- {name_val} ({path}): {content}...")
                            if file_lines:
                                rag_context += (
                                    "\n\nRepository-Adjacent Storage Context (Non-Authoritative):\n"
                                    + "\n".join(file_lines)
                                )
                    elif name == "nc_realtime":
                        matches = data.get("matches", [])
                        if isinstance(matches, list) and matches:
                            storage_text = "\n".join([f"- {m.get('name', 'file')} (Path: {m.get('path', 'unknown')})" for m in matches if isinstance(m, dict)])
                            rag_context += f"\n\nNextCloud Files found (Real-time):\n{storage_text}"
                    elif name == "code_realtime":
                        matches = data.get("matches", [])
                        if isinstance(matches, list) and matches:
                            storage_text = "\n".join([f"- {m.get('name', 'file')} (Path: {m.get('path', 'unknown')})" for m in matches if isinstance(m, dict)])
                            rag_context += (
                                "\n\nRepository-Adjacent Storage Matches (Discovery Only):\n"
                                f"{storage_text}"
                            )
                    elif name == "cap_rag":
                        cap_results = data.get("results", [])
                        if isinstance(cap_results, list) and cap_results:
                            cap_lines = [str(r.get("content")) for r in cap_results if isinstance(r, dict) and r.get("content")]
                            if cap_lines:
                                rag_context += "\n\n### System Capability Context (Self-Awareness):\n" + "\n".join(cap_lines)
                except Exception as pe:
                    log.error(f"Error parsing {name} response: {pe}")

        # Post-process: History for HA entities
        # (This is still serial but only for 3 items)
        if results:
            for r in results[:3]:
                if not isinstance(r, dict): continue
                eid = r.get("metadata", {}).get("entity_id")
                if eid:
                    try:
                        hist = await fetch_device_history(creds, eid)
                        if hist:
                            hist_text = "\n".join([f"- {h.get('last_changed', 'unknown')}: {h.get('state', 'unknown')}" for h in hist[-5:] if isinstance(h, dict)])
                            rag_context += f"\n\n### Device Usage History ({eid}):\n{hist_text}"
                    except: pass
                            
    except Exception as e:
        log.error(f"Context injection orchestration failed: {e}")

    await emit_log("INFO", f"Context gathered for {user_id}", {"query": refined_query, "context_len": len(rag_context), "context_preview": rag_context[:200]})

    # 5. Proxy to Ollama (Slow Path)
    try:
        try:
            await client.post(f"{STORAGE_SVC}/index/pause", headers={"X-Internal-Secret": INTERNAL_SECRET})
        except: pass

        system_instruction = select_system_instruction_for_query(refined_query, selected_model)
        if explicit_model_requested and selected_model == CODING_MODEL:
            system_instruction = CODE_HELPER_SYSTEM_INSTRUCTION

        ollama_payload = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": system_instruction}
            ] + history + [{"role": "user", "content": f"{'CODE CONTEXT' if is_coding_task else 'CONTEXT'}:\n{rag_context}\n\nQUERY: {refined_query}"}],
            "stream": should_stream
        }

        if should_stream:
            async def stream_generator():
                full_answer = ""
                try:
                    import time
                    async with client.stream(
                        "POST", f"{OLLAMA_URL}/api/chat", json=ollama_payload, timeout=None
                    ) as resp:
                        if resp.status_code != 200:
                            yield json.dumps({"error": "Ollama error"})
                            return
                        
                        async for line in resp.aiter_lines():
                            if not line: continue
                            try:
                                chunk = json.loads(line)
                                if not isinstance(chunk, dict): continue
                                
                                content = chunk.get("message", {}).get("content", "")
                                if content:
                                    full_answer += content
                                    if is_openai:
                                        openai_chunk = {
                                            "id": f"chatcmpl-{int(time.time())}",
                                            "object": "chat.completion.chunk",
                                            "created": int(time.time()),
                                            "model": selected_model,
                                            "choices": [{"delta": {"content": content}, "index": 0, "finish_reason": None}]
                                        }
                                        yield f"data: {json.dumps(openai_chunk)}\n\n"
                                    else:
                                        yield line + "\n"
                                
                                if chunk.get("done"):
                                    if is_openai:
                                        yield "data: [DONE]\n\n"
                                    break
                            except Exception as e:
                                log.error(f"Stream chunk error: {e}")
                finally:
                    # Update history and resume indexer after stream ends
                    if full_answer:
                        await update_history(user_id, "user", query)
                        await update_history(user_id, "assistant", full_answer)
                    try:
                        await client.post(f"{STORAGE_SVC}/index/resume", headers={"X-Internal-Secret": INTERNAL_SECRET})
                    except: pass
            
            return StreamingResponse(stream_generator(), media_type="text/event-stream" if is_openai else "application/x-ndjson")

        # Non-streaming Path
        resp = await call_ollama(ollama_payload, use_chat=True)
        if resp.status_code == 404:
            resp = await call_ollama({"model": selected_model, "prompt": refined_query, "stream": False}, use_chat=False)
            
        if resp.status_code != 200:
            return JSONResponse({"status": "ERROR", "message": "The brain is currently unavailable."}, status_code=502)
            
        data = resp.json()
        answer = ""
        if not isinstance(data, dict): 
            answer = str(data)
        else:
            msg_obj = data.get("message")
            if isinstance(msg_obj, dict): 
                answer = msg_obj.get("content", "")
            else: 
                answer = str(data.get("response", "I encountered an error."))
        
        final_answer = answer if answer else "I received an empty response from the brain."

        # 6. Parse and execute any tool calls in the LLM response
        show_technical = any(token in query.lower() for token in ("show json", "technical", "debug", "schema"))
        tool_match = re.search(r"(\{.*\"action\":\s*\"(.*?)\".*\})", final_answer, re.DOTALL)
        if tool_match:
            try:
                potential_json = tool_match.group(1)
                # Clean up markdown if present
                potential_json = potential_json.strip().strip("```json").strip("```").strip()
                
                tool_call = json.loads(potential_json)
                action_name = tool_call.get("action")
                payload = tool_call.get("payload", {})
                
                # Map action name to endpoint
                action_to_endpoint = {
                    "LightControlRequest": "/execute/light",
                    "MediaPlayRequest": "/execute/media/play",
                    "MediaTransportRequest": "/execute/media/transport",
                    "NoteRequest": "/execute/note",
                    "TimerRequest": "/execute/timer",
                    "CalendarRequest": "/execute/calendar",
                    "AnnouncementRequest": "/execute/announce",
                    "TVCastRequest": "/execute/tv_cast",
                    "HAServiceRequest": "/execute/ha_service",
                    # Workspace Runtime Mappings
                    "WorkspaceFileAction": "/files/write",
                    "WorkspaceGitAction": "/git/status",
                    "WorkspaceSyncAction": "/sync/nextcloud",
                    "index_storage": "internal",
                    "ha_status": "internal"
                }
                
                if action_name in ("index_storage", "ha_status"):
                     log.info(f"[SlowPath] Intercepted internal tool call: {action_name}")
                     
                     # Actual execution logic
                     if action_name == "index_storage":
                         async with httpx.AsyncClient(timeout=300.0) as client:
                             try:
                                 path = payload.get("path", "/")
                                 idx_payload = {
                                     "provider": {"kind": "nextcloud", "settings": {
                                         "url": creds.get("nextcloud_url"),
                                         "username": creds.get("nextcloud_user"),
                                         "password": creds.get("nextcloud_pass")
                                     }},
                                     "path": path, "recursive": True
                                 }
                                 await client.post(f"{STORAGE_SVC}/index/full", json=idx_payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
                                 exec_msg = f"I have started indexing '{path}' in the background."
                             except Exception as e:
                                 exec_msg = f"Failed to trigger index: {e}"
                     else: # ha_status
                         status_res = await client.get(f"{RAG_SVC}/rag/ha/status", params={"user_id": user_id}, headers={"X-Internal-Secret": INTERNAL_SECRET})
                         if status_res.status_code == 200:
                             s_data = status_res.json().get("data", {})
                             ts = s_data.get("timestamp", 0)
                             timestamp = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else "Never"
                             exec_msg = f"HA Status: Last sync at {timestamp}. {s_data.get('count', 0)} entities tracked."
                         else:
                             exec_msg = "No HA sync data found."

                     # Strip JSON and update final_answer
                     if not show_technical:
                         clean_answer = re.sub(r"```json.*?```", "", final_answer, flags=re.DOTALL)
                         if tool_match.group(0) in clean_answer:
                             clean_answer = clean_answer.replace(tool_match.group(0), "")
                         clean_answer = clean_answer.strip()
                         final_answer = f"{exec_msg}\n\n{clean_answer}" if clean_answer else exec_msg
                     else:
                         final_answer = f"[EXECUTED: {action_name}]\n{exec_msg}\n\n{final_answer}"
                     endpoint = None # Skip the execute_command block
                else:
                    endpoint = action_to_endpoint.get(action_name)

                if endpoint:
                    log.info(f"[SlowPath] Executing tool call: {action_name}")
                    # Ensure user_context is in payload
                    if "user_context" not in payload:
                        payload["user_context"] = creds
                    
                    exec_res = await execute_command(endpoint, payload)
                    if exec_res.get("status") == "SUCCESS":
                        exec_msg = exec_res.get('message', 'Action completed.')
                        # Combine with LLM's natural language preamble if any
                        if not show_technical:
                            clean_answer = re.sub(r"```json.*?```", "", final_answer, flags=re.DOTALL)
                            if tool_match.group(0) in clean_answer:
                                clean_answer = clean_answer.replace(tool_match.group(0), "")
                            clean_answer = clean_answer.strip()
                            final_answer = f"{exec_msg}\n\n{clean_answer}"
                        else:
                            final_answer = f"[EXECUTED: {action_name}]\n{exec_msg}\n\n{final_answer}"
                    else:
                        final_answer = f"I tried to perform the action, but it failed: {exec_res.get('message')}"
            except Exception as te:
                log.warning(f"Failed to parse tool call: {te}")

        await update_history(user_id, "user", query)
        await update_history(user_id, "assistant", final_answer)

        if is_openai:
            return _make_openai_response(final_answer, selected_model, debug_context=rag_context if body.get("debug") else None, stream=should_stream)
        return _make_ollama_response(final_answer, selected_model, debug_context=rag_context if body.get("debug") else None, stream=should_stream)
        
    except Exception as e:
        log.error(f"LLM Proxy Error: {e}")
        return JSONResponse({"status": "ERROR", "message": str(e)}, status_code=500)
    finally:
        try:
            await client.post(f"{STORAGE_SVC}/index/resume", headers={"X-Internal-Secret": INTERNAL_SECRET})
        except: pass
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

@app.post("/api/generate")
async def proxy_generate(request: Request):
    try:
        body = await request.json()
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
            if resp.status_code != 200:
                return JSONResponse({"status": "ERROR", "message": resp.text}, status_code=resp.status_code)
            data = resp.json()
            if not isinstance(data, dict):
                return {"status": "ERROR", "message": str(data)}
            return data
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
