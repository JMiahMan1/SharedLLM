# services/gateway/main.py
import os
import logging
import json
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import re
import traceback

# --- Imports from internal modules ---
try:
    from .schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
    from .intent_engine import engine
    from .history import get_history, update_history, ping_redis
except (ImportError, ValueError):
    try:
        from gateway.schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
        from gateway.intent_engine import engine
        from gateway.history import get_history, update_history, ping_redis
    except ImportError:
        from intent_engine import engine
        from history import get_history, update_history, ping_redis

# --- Setup Logging ---
log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

# --- Configuration ---
IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
STORAGE_SVC = os.getenv("STORAGE_SVC_URL", "http://storage:8005")
LOGGING_SVC_URL = os.getenv("LOGGING_SVC_URL", "http://logging:8006")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
FAST_PATH_THRESHOLD = float(os.getenv("FAST_PATH_THRESHOLD", "0.85"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "60.0"))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:8b")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", DEFAULT_MODEL)
CODING_MODEL = os.getenv("CODING_MODEL", ASSISTANT_MODEL)
LIBRARIAN_MODEL = os.getenv("LIBRARIAN_MODEL", ASSISTANT_MODEL)

# --- Global Clients ---
_global_http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _global_http_client
    _global_http_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
    log.info("Gateway starting up...")
    engine.load()
    yield
    await _global_http_client.aclose()
    log.info("Gateway shutting down...")

app = FastAPI(title="SOA Intent Gateway", version="1.0.0", lifespan=lifespan)

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
        "logging": f"{LOGGING_SVC_URL}/health"
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
                rewritten = data.get("response", query).strip().strip('"')
                log.info(f"[Context] '{query}' -> '{rewritten}'")
                return rewritten
    except Exception:
        pass
    return query


def select_model_for_query(query: str) -> str:
    """Route obvious coding and librarian tasks to specialized models."""
    q = (query or "").lower()

    coding_signals = (
        "python", "javascript", "typescript", "node", "react", "fastapi", "sql", "regex",
        "docker", "dockerfile", "bash", "shell", "pytest", "bug", "fix", "refactor",
        "implement", "function", "class", "stack trace", "traceback", "code", "script",
        "compile", "syntax", "test", "unit test", "integration test", "git"
    )
    librarian_signals = (
        "summarize", "summary", "recap", "search my", "find in", "look up", "what do i have",
        "list my", "notes", "calendar", "documents", "document", "playlist", "playlists",
        "radio stations", "audiobook", "audiobooks", "library", "catalog", "catalogue",
        "files", "folders", "nextcloud", "storage", "cloud", "books", "book", "music",
        "photos", "photo", "images", "videos", "video", "code", "scripts"
    )

    if any(token in q for token in coding_signals):
        return CODING_MODEL
    if any(token in q for token in librarian_signals):
        return LIBRARIAN_MODEL
    return ASSISTANT_MODEL


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

    candidates = [e for e in entities if e.get("entity_id", "").startswith("media_player.")]
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
        "You are troubleshooting a failed music playback request.\n"
        "Return only JSON with keys: query, media_type.\n"
        "media_type must be one of: artist, search, music.\n"
        f"User request: {query}\n"
        f"Failure: {failure}\n"
        "Prefer the simplest library lookup that is most likely to succeed."
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
        raw = data.get("response", "").strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        data = json.loads(raw[start:end + 1])
        query_value = str(data.get("query") or "").strip()
        media_type = str(data.get("media_type") or "").strip().lower()
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
        
        entities = resp.json()
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
    body = await request.json()
    query = body.get("query")
    if not query and "messages" in body and isinstance(body["messages"], list) and len(body["messages"]) > 0:
        last_msg = body["messages"][-1]
        if isinstance(last_msg, dict):
            query = last_msg.get("content")
        else:
            query = str(last_msg)
    
    if not query:
        return JSONResponse({"status": "ERROR", "message": "No query"}, status_code=400)

    # 1. Resolve Identity & Context
    creds = await resolve_identity(body)
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
    
    # 3. Fast Path (Semantic Routing)
    intent, confidence = engine.classify(refined_query)
    if media_transport_command:
        intent = "media_transport"
        confidence = 1.0

    if confidence >= FAST_PATH_THRESHOLD:
        log.info(f"[FastPath] intent='{intent}' confidence={confidence}")
        
        log.info(f"Checking intent: '{intent}'")
        if intent == "index_storage":
            log.info("Matched index_storage intent")
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
                        msg = "I have started indexing your library in the background. It may take a moment to process everything."
                        await emit_log("SUCCESS", msg)
                        return JSONResponse({"status": "SUCCESS", "message": msg, "intent": "index_storage"})
                    else:
                        await emit_log("ERROR", f"Indexing failed: {resp.text}")
                        return JSONResponse({"status": "ERROR", "message": "I couldn't index your library at this time."}, status_code=502)
                except Exception as e:
                    log.error(f"Index trigger failed: {e}")
                    return JSONResponse({"status": "ERROR", "message": "The storage service is not responding."}, status_code=502)

        log.info(f"Searching for endpoint for intent: '{intent}'")
        # Simple routing map
        endpoint_map = {
            "turn_on": "/execute/light",
            "turn_off": "/execute/light",
            "play_media": "/execute/media/play",
            "media_transport": "/execute/media/transport",
            "pause_media": "/execute/media/transport",
            "open_garage": "/execute/security",
            "close_garage": "/execute/security"
        }
        
        endpoint = endpoint_map.get(intent)
        if endpoint:
            # Smart-ish entity resolution for stubs
            target_entity = "auto"
            
            # 1. Try to find a match in real_entities based on the query
            query_lower = refined_query.lower()
            for e in real_entities:
                friendly_name = (e.get("attributes") or {}).get("friendly_name") or ""
                fname_lower = friendly_name.lower()
                eid = e.get("entity_id", "").lower()
                
                # If name mentioned in query, and type matches intent
                if fname_lower and fname_lower in query_lower:
                    if "media" in intent and eid.startswith("media_player."):
                        target_entity = e["entity_id"]
                        break
                    if ("light" in intent or "turn" in intent) and eid.startswith("light."):
                        target_entity = e["entity_id"]
                        break
            
            # 2. Fallback to first available of type
            if target_entity == "auto":
                if "media" in intent:
                    players = [e for e in real_entities if e['entity_id'].startswith('media_player.')]
                    if players:
                        target_entity = players[0]['entity_id']
                elif "light" in intent or "turn" in intent:
                    lights = [e for e in real_entities if e['entity_id'].startswith('light.')]
                    if lights:
                        target_entity = lights[0]['entity_id']

            if intent in {"play_media", "media_transport", "pause_media"} and (media_query or media_transport_command) and not is_video_request:
                target_entity = resolve_media_target(refined_query, real_entities)

            exec_payload = {
                "user_context": creds,
                "entity_id": target_entity,
                "action": "turn_on" if intent == "turn_on" else ("turn_off" if intent == "turn_off" else "play")
            }
            
            # For media, add default content
            if intent == "play_media":
                if media_query:
                    exec_payload["query"] = media_query
                    exec_payload["media_content_type"] = "artist"
                else:
                    exec_payload["media_content_id"] = "http://stream.radioparadise.com/flac"
                    exec_payload["media_content_type"] = "music"
            elif intent in {"media_transport", "pause_media"}:
                if not media_transport_command:
                    return JSONResponse(
                        {"status": "ERROR", "message": "Could not determine media transport command."},
                        status_code=400,
                    )
                exec_payload["command"] = media_transport_command

            exec_res = await execute_command(endpoint, exec_payload)
            if intent == "play_media" and exec_res.get("status") == "FAILURE":
                fallback = await troubleshoot_media_failure(refined_query, exec_res.get("message", "unknown failure"))
                if fallback:
                    retry_payload = dict(exec_payload)
                    retry_payload["query"] = fallback["query"]
                    retry_payload["media_content_type"] = fallback["media_type"]
                    retry_payload.pop("media_content_id", None)
                    exec_res = await execute_command(endpoint, retry_payload)
            return JSONResponse({
                "status": "SUCCESS",
                "message": exec_res.get("message", "Executed"),
                "intent": intent,
                "confidence": confidence,
                "execution_result": exec_res
            })
    
    # 4. Context Injection (RAG + Storage)
    rag_context = ""
    try:
        selected_model = select_model_for_query(refined_query)
        q_lower = refined_query.lower()
        is_librarian_task = any(token in q_lower for token in (
            "summarize", "summary", "recap", "search my", "find in", "look up", "what do i have",
            "list my", "notes", "calendar", "documents", "document", "playlist", "playlists",
            "radio stations", "audiobook", "audiobooks", "library", "catalog", "catalogue",
            "files", "folders", "nextcloud", "storage", "cloud", "books", "book", "music",
            "photos", "photo", "images", "videos", "video", "code", "scripts"
        ))
        
        log.info(f"[ModelSelect] model='{selected_model}' query='{refined_query}' librarian={is_librarian_task}")
        
        # Use global client
        client = _global_http_client
        # HA Entity Context
        ha_keywords = [r"\bstatus\b", r"\bdevice\b", r"\bhome\b", r"\bsensor\b", r"\blight\b", r"\bswitch\b", r"\bdoor\b", r"\block\b", r"\btemp\b", r"\bhumidity\b", r"\bbattery\b"]
        if any(re.search(k, q_lower) for k in ha_keywords):
            rag_resp = await client.post(
                f"{RAG_SVC}/rag/search",
                json={
                    "query": refined_query,
                    "user_id": user_id,
                    "collection_name": "ha_entities",
                    "k": 5
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if rag_resp.status_code == 200:
                data = rag_resp.json()
                if isinstance(data, dict):
                    results = data.get("results", [])
                    if results:
                        context_lines = []
                        for r in results:
                            if isinstance(r, dict) and "content" in r:
                                context_lines.append(r["content"])
                        if context_lines:
                            rag_context = "Relevant Device Context:\n" + "\n".join(context_lines)
            
        # Storage Context for Librarian
        if is_librarian_task:
            # A. Semantic Content Search
            file_rag_resp = await client.post(
                f"{RAG_SVC}/rag/search",
                json={
                    "query": refined_query,
                    "user_id": user_id,
                    "collection_name": "nextcloud_files",
                    "k": 5
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if file_rag_resp.status_code == 200:
                data = file_rag_resp.json()
                if isinstance(data, dict):
                    file_results = data.get("results", [])
                    if file_results:
                        file_lines = []
                        for r in file_results:
                            if not isinstance(r, dict): continue
                            meta = r.get("metadata")
                            if isinstance(meta, dict):
                                name = meta.get("name", "file")
                                path = meta.get("path", "unknown")
                            else:
                                name, path = "file", "unknown"
                            content = str(r.get("content", ""))[:200]
                            file_lines.append(f"- {name} ({path}): {content}...")
                        
                        if file_lines:
                            file_text = "\n".join(file_lines)
                            rag_context += f"\n\nRelevant NextCloud Content:\n{file_text}"

                # B. Shallow Filename Search
                storage_resp = await client.post(
                    f"{STORAGE_SVC}/providers/search",
                    params={"query": refined_query},
                    json={
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
                    },
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if storage_resp.status_code == 200:
                    matches = storage_resp.json().get("matches", [])
                    if matches:
                        storage_text = "\n".join([f"- {m['name']} (Path: {m['path']})" for m in matches])
                        rag_context += f"\n\nNextCloud Files found (Real-time):\n{storage_text}"
                
        log_keywords = [r"\blog\b", r"\blogs\b", r"\bhealth\b", r"\bstatus\b", r"\bissue\b", r"\berror\b", r"\bbroken\b"]
        if any(re.search(k, q_lower) for k in log_keywords):
            try:
                log_resp = await client.get(f"{LOGGING_SVC_URL}/logs", params={"limit": 5})
                if log_resp.status_code == 200:
                    recent_logs = log_resp.json()
                    if isinstance(recent_logs, list):
                        log_entries = []
                        for l in recent_logs:
                            if isinstance(l, dict):
                                log_entries.append(f"[{l.get('timestamp')}] [{l.get('service')}] {l.get('message')}")
                        if log_entries:
                            log_text = "\n".join(log_entries)
                            rag_context += f"\n\n### Application Internal Logs:\n{log_text}"
            except: pass
            
        history_keywords = [r"\bhistory\b", r"\blast used\b", r"\bactivity\b", r"\brecently\b", r"\bturned on\b", r"\bturned off\b"]
        if any(re.search(k, q_lower) for k in history_keywords):
            try:
                # Fetch history for the top 3 relevant entities found in RAG
                for r in results[:3]:
                    if not isinstance(r, dict): continue
                    meta = r.get("metadata")
                    if not isinstance(meta, dict): continue
                    eid = meta.get("entity_id")
                    if eid:
                        hist = await fetch_device_history(creds, eid)
                        if hist:
                            hist_text = "\n".join([f"- {h['last_changed']}: {h['state']}" for h in hist[-5:]])
                            rag_context += f"\n\n### Device Usage History ({eid}):\n{hist_text}"
            except: pass
                            
    except Exception as e:
        err_detail = f"{type(e).__name__}: {str(e)}"
        log.error(f"Context injection failed: {err_detail}\n{traceback.format_exc()}")
        await emit_log("ERROR", f"Context injection failed: {err_detail}")

    log.info(f"Injected Context:\n{rag_context}")

    # 5. Proxy to Ollama (Slow Path)
    try:
        # Resource Prioritization: Pause Indexer
        try:
            await client.post(f"{STORAGE_SVC}/index/pause", headers={"X-Internal-Secret": INTERNAL_SECRET})
        except: pass

    await emit_log("INFO", f"Slow path triggered for: {refined_query}")
    
    # Try /api/chat first (Ollama standard)
    ollama_payload = {
        "model": selected_model,
        "messages": history + [{"role": "user", "content": f"{rag_context}\n\nQuery: {refined_query}"}],
        "stream": False
    }
    resp = await call_ollama(ollama_payload, use_chat=True)
    
    if resp.status_code == 404:
        # Fallback to /api/generate for older Ollama versions
        log.warning("Ollama /api/chat not found, falling back to /api/generate")
        gen_payload = {
            "model": selected_model,
            "prompt": f"{refined_query}", # Simplified
            "stream": False
        }
        resp = await call_ollama(gen_payload, use_chat=False)
        
    if resp.status_code != 200:
        err_msg = f"Ollama Error {resp.status_code}: {resp.text}"
        log.error(err_msg)
        return JSONResponse({"status": "ERROR", "message": "The brain is currently unavailable."}, status_code=502)
        
    data = resp.json()
    if not isinstance(data, dict):
        answer = str(data)
    else:
        msg_obj = data.get("message")
        if isinstance(msg_obj, dict):
            answer = msg_obj.get("content")
        else:
            answer = data.get("response", "I encountered an error.")
    
    if not answer:
        answer = "I received an empty response from the brain."
    # 6. Format Response
    final_answer = answer if answer else "I encountered an error while processing your request."
    
    # Save to history
    await update_history(user_id, "user", query)
    await update_history(user_id, "assistant", final_answer)
    
    # Log Success
    await emit_log("INFO", f"Chat successful: {query[:50]}...")

    # Detect if it was an OpenAI-style request
    is_openai = "/v1/chat/completions" in str(request.url)
    
    if is_openai:
        import time
        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": selected_model,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": final_answer
                    },
                    "finish_reason": "stop",
                    "index": 0
                }
            ]
        }
    
    return {"status": "SUCCESS", "message": final_answer}
    
except Exception as e:
    log.error(f"LLM Proxy Error: {e}")
    raise HTTPException(status_code=502, detail="Upstream LLM error")
finally:
    # Resource Prioritization: Resume Indexer
    try:
        await _global_http_client.post(f"{STORAGE_SVC}/index/resume", headers={"X-Internal-Secret": INTERNAL_SECRET})
    except: pass

# --- Ollama Proxy ---
@app.post("/api/generate")
async def proxy_generate(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
        return resp.json()

@app.get("/api/tags")
async def proxy_tags():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{OLLAMA_URL}/api/tags")
        return resp.json()

@app.get("/api/version")
async def proxy_version():
    return {"version": "0.1.32"}
