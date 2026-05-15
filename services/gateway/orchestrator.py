# services/gateway/orchestrator.py
import asyncio
import re
import logging
import httpx
import os
from typing import Any, Dict, List, Optional, Callable, Awaitable
try:
    from .schemas import ResolvedCredentials
    from .llm_providers import BaseLLMProvider, OllamaProvider, OpenRouterProvider
    from .config import WORKSPACE_RUNTIME_SVC
except (ImportError, ValueError):
    from schemas import ResolvedCredentials
    from llm_providers import BaseLLMProvider, OllamaProvider, OpenRouterProvider
    from config import WORKSPACE_RUNTIME_SVC

log = logging.getLogger("gateway.orchestrator")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
STORAGE_SVC = os.getenv("STORAGE_SVC_URL", "http://storage:8005")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

SINGLE_TURN_TOOL_ENDPOINTS: Dict[str, tuple[str, str]] = {
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
    "dockercomposerequest": (EXECUTION_SVC, "/execute/docker"),
    "gitoperationrequest": (EXECUTION_SVC, "/execute/git"),
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
    "storagelistrequest": (EXECUTION_SVC, "/execute/storage_list"),
    "workspacebootstraprequest": (WORKSPACE_RUNTIME_SVC, "/workspaces/bootstrap"),
    "systemlearningrequest": (EXECUTION_SVC, "/execute/learning"),
    "discoverysyncrequest": (EXECUTION_SVC, "/execute/discovery_sync"),
    "storageindexrequest": (STORAGE_SVC, "/index/full"),
    "logbookrequest": (EXECUTION_SVC, "/execute/ha_logbook"),
}

SINGLE_TURN_TOOL_GUIDE = """
# CORE PROTOCOLS
1. **JSON ONLY**: You MUST output ONLY the JSON block. No preamble, no natural language.
2. **STRICT SCHEMA**: Your JSON must use lowercase "action" and "payload".
3. **ONE ACTION**: Only one action per turn.

# VALID TOOLS (MANDATORY NAMES)
- Git: GitOperationRequest (actions: status, add, commit, push, pull, diff)
- Docker: DockerLogsRequest, DockerComposeRequest
- File: WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest
- Storage: StorageFileReadRequest, StorageFileWriteRequest, StorageListRequest, StorageIndexRequest
- Home Assistant: LightControlRequest, MediaPlayRequest, LogbookRequest (for device logs)

# OUTPUT FORMAT (MANDATORY)
```json
{
  "action": "TOOL_NAME",
  "payload": {
    "key": "value"
  }
}
```
"""


async def get_llm_settings() -> Dict[str, str]:
    """Fetches LLM configuration from the Identity service."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                return {item["key"]: item["value"] for item in resp.json()}
    except Exception as e:
        log.error(f"Failed to fetch dynamic LLM settings: {e}")
    return {}


async def get_provider(settings: Dict[str, str]) -> BaseLLMProvider:
    """Instantiates the correct provider based on settings."""
    active_provider = settings.get("active_llm_provider", "ollama")
    timeout = float(settings.get("ollama_timeout", os.getenv("OLLAMA_TIMEOUT", "600")))
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
    Legacy-compatible inference seam for tests and direct provider calls.
    The underlying provider is still resolved dynamically from Identity settings.
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

async def process_full_orchestration(job_payload: Dict[str, Any], chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    """
    Handles the full Jarvis orchestration pipeline:
    Decompose -> Memory -> RAG -> Inference -> Tools -> Update.
    """
    query = job_payload["query"]
    user_id = job_payload["creds"]["user"]
    creds = ResolvedCredentials(**job_payload["creds"])
    model = job_payload["model"]
    
    # 0. Query-based Model Override (e.g. "Raven use model qwen2.5:32b fix...")
    model_match = re.search(r"(?:use model|with model|run on model)\s+([a-zA-Z0-9.\-_:]+)", query, re.IGNORECASE)
    if model_match:
        model = model_match.group(1)
        log.info(f"[Orchestrator] Dynamic model override detected: {model}")
    
    log.info(f"[Orchestrator] Starting orchestration for query: {query[:50]}...")
    
    # 1. Retrieve Memory
    short_term = [] # Placeholder
    
    # 2. Context Injection (RAG)
    rag_context = await _fetch_rag_context(query, user_id)
    
    # 3. Autonomous Detection (Raven/Coding/Repair ONLY)
    # Raven runs in Workspaces and handles long-running or coding tasks.
    # Home Automation should NOT be treated as autonomous (no long-running loops)
    autonomy_signals = [
        "raven", "use raven", "audit", "repair", "self repair", "self-heal",
        "self fix", "deploy", "bootstrap", "develop", "fix the app",
        "fix the service", "fix the codebase", "agentic", "autonomous"
    ]
    is_autonomous = any(k in query.lower() for k in autonomy_signals)
    
    # 4. Final Inference
    full_system = job_payload.get("system", "")
    if is_autonomous:
        try:
            from .agent_loop import AgentLoop
        except (ImportError, ValueError):
            from agent_loop import AgentLoop
        # Raven handles autonomous loops
        mission_id = job_payload.get("_mission_id")
        ans = await AgentLoop(query, model, full_system, short_term, user_id, creds, mission_id, rag_context=rag_context)
    else:
        # Librarian handles standard single-turn inference
        ans = await _single_turn_inference(query, model, full_system, rag_context, short_term, creds, chunk_callback)
        
    return ans

async def _fetch_rag_context(query: str, user_id: str) -> str:
    rag_context = ""
    try:
        # Prioritize collections based on query intent
        q = query.lower()
        collections = ["ha_entities", "nextcloud_files", "system_capabilities", "system_learnings"]
        
        # Adjust priorities: if it looks like a coding/sys task, prioritize capabilities and files
        if any(token in q for token in ["file", "code", "git", "workspace", "fix", "repair"]):
            collections = ["system_capabilities", "nextcloud_files", "system_learnings", "ha_entities"]
        
        # Context constraints
        MAX_TOTAL_HITS = 20
        MAX_HITS_PER_COLL = 8
        MAX_CHARS_PER_HIT = 2000
        TOTAL_CHARS_LIMIT = 15000 # Approx 4k tokens
        
        total_hits = 0
        total_chars = 0
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            for coll in collections:
                if total_hits >= MAX_TOTAL_HITS or total_chars >= TOTAL_CHARS_LIMIT:
                    break
                    
                resp = await client.post(
                    f"{RAG_SVC}/rag/search",
                    json={"collection_name": coll, "query": query, "user_id": user_id, "k": MAX_HITS_PER_COLL},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status_code == 200:
                    hits = resp.json().get("results", [])
                    if hits:
                        coll_added = False
                        for h in hits:
                            content = h["content"]
                            if len(content) > MAX_CHARS_PER_HIT:
                                content = content[:MAX_CHARS_PER_HIT] + "... [TRUNCATED]"
                            
                            if total_chars + len(content) > TOTAL_CHARS_LIMIT:
                                break
                            
                            if not coll_added:
                                rag_context += f"\n[{coll.upper()}]\n"
                                coll_added = True
                                
                            rag_context += f"- {content}\n"
                            total_chars += len(content)
                            total_hits += 1
                            
                            if total_hits >= MAX_TOTAL_HITS:
                                break
    except Exception as e:
        log.error(f"RAG search failed: {e}")
    return rag_context.strip()

async def _single_turn_inference(query: str, model: str, system_prompt: str, rag_context: str, history: List[Dict[str, str]], creds: ResolvedCredentials, chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    system = f"{system_prompt.strip()}\n\nSystem Capability Context:\n{SINGLE_TURN_TOOL_GUIDE}\n\nRetrieved Context:\n{rag_context}"
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": query}]

    log.info(f"[_single_turn_inference] Executing for model {model}")
    
    # No grammar constraint - model produces JSON naturally via system prompt
    options = {"temperature": 0.0}

    # --- RETRY LOGIC FOR MODEL SWITCHING ---
    MAX_INFERENCE_RETRIES = 3
    ans = ""

    for retry_count in range(MAX_INFERENCE_RETRIES):
        try:
            data = await call_ollama(
                {
                    "model": model,
                    "messages": messages,
                    "options": options,
                    "chunk_callback": chunk_callback,
                },
                use_chat=True,
            )
            ans = data.get("message", {}).get("content", "")
            break # Success!
        except Exception as e:
            log.warning(f"[_single_turn_inference] Inference attempt {retry_count + 1} failed: {e}")
            if retry_count < MAX_INFERENCE_RETRIES - 1:
                wait_time = 5 * (retry_count + 1)
                log.info(f"[_single_turn_inference] Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                log.error(f"[_single_turn_inference] FATAL: All inference retries failed: {e}")
                return f"I encountered an error while trying to generate a response (All retries failed): {e}"

    # Tool Extraction
    try:
        from .agent_loop import extract_action_json
    except (ImportError, ValueError):
        from agent_loop import extract_action_json

    tool_data = extract_action_json(ans)
    if tool_data:
        action = tool_data.get("action", "").lower().strip()
        log.info(f"[_single_turn_inference] Tool call detected: {action}")
        
        if action == "controlplanerequest":
            payload = tool_data.get("payload", tool_data)
            service_name = payload.get("service_name")
            sub_action = payload.get("action", "restart")
            if not service_name:
                return "Error: service_name is required"
            CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control_plane:8008")
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    if sub_action == "restart":
                        resp = await client.post(f"{CONTROL_PLANE_URL}/api/restart/{service_name}", headers={"X-Internal-Secret": INTERNAL_SECRET})
                    else:
                        resp = await client.get(f"{CONTROL_PLANE_URL}/api/status/{service_name}", headers={"X-Internal-Secret": INTERNAL_SECRET})
                    
                    if resp.status_code == 200:
                        return f"Control Plane '{sub_action}' succeeded on {service_name}: {resp.text}"
                    return f"Control Plane error {resp.status_code}: {resp.text}"
            except Exception as e:
                log.error(f"Control Plane execution error: {e}")
                return f"Control Plane execution failed: {e}"
        
        elif action in SINGLE_TURN_TOOL_ENDPOINTS:
            svc_base, endpoint = SINGLE_TURN_TOOL_ENDPOINTS[action]
            
            try:
                payload = tool_data.get("payload", tool_data)
                payload["user_context"] = creds.model_dump()
                
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(f"{svc_base}{endpoint}", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
                    if resp.status_code == 200:
                        return resp.json().get("message", "Action completed successfully.")
                    else:
                        return f"Tool execution failed ({resp.status_code}): {resp.text}"
            except Exception as e:
                log.error(f"Single-turn tool execution error: {e}")
                return f"I encountered an error while executing the tool: {e}"
        else:
            log.warning(f"[_single_turn_inference] Unsupported tool for single-turn: {action}")
            return f"I found a tool call for '{action}', but it is not supported in the standard path. Please ask Raven to perform this task."

    return ans
