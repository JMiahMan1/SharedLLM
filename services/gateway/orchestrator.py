# services/gateway/orchestrator.py
import asyncio
import logging
import httpx
import os
import json
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

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
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
    "storagelistrequest": (EXECUTION_SVC, "/execute/storage_list"),
    "workspacebootstraprequest": (WORKSPACE_RUNTIME_SVC, "/workspaces/bootstrap"),
    "systemlearningrequest": (EXECUTION_SVC, "/execute/learning"),
    "discoverysyncrequest": (EXECUTION_SVC, "/execute/discovery_sync"),
    "storageindexrequest": (STORAGE_SVC, "/index/full"),
}

SINGLE_TURN_TOOL_GUIDE = """
Available tool schemas for standard chat include:
- LightControlRequest: turn lights or switches on/off.
- MediaPlayRequest: start audio or media playback on a target device.
- MediaTransportRequest: pause, resume, stop, next, or previous playback.
- TVCastRequest: cast video content to a display target.
- ClimateRequest: read temperatures or change HVAC/climate settings.
- SecurityRequest: inspect or change locks, alarms, and doors.
- AnnouncementRequest: speak or broadcast a message to household devices.
- HAServiceRequest: call a raw Home Assistant domain/service pair.
- CalendarRequest: create or inspect calendar items.
- NoteRequest: create or retrieve household notes.
- TimerRequest: create, cancel, or inspect timers.
- TalkRequest: send a conversational or TTS message to a device.
- WebSearchRequest and WebReadRequest: search or read public web pages.
- DockerLogsRequest and DockerComposeRequest: inspect or operate containers.
- GitOperationRequest: inspect or mutate git state.
- DeploymentRequest: restart, build, or stop services.
- CapabilityIndexRequest and VolumeInventoryRequest: inspect system capabilities and storage.
- WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceLintRequest, WorkspaceSearchRequest, WorkspaceShellRequest, WorkspaceBootstrapRequest: inspect and modify workspace state.
- StorageFileReadRequest, StorageFileWriteRequest, StorageListRequest, StorageIndexRequest: inspect and manage storage providers.
- SystemLearningRequest and DiscoverySyncRequest: record learnings and refresh discovered devices.

When a tool is appropriate, output a fenced JSON object with exactly:
```json
{"action":"SCHEMA_NAME","payload":{...}}
```
Do not say you lack access to tools when the request maps to one of these capabilities.
""".strip()


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
    if active_provider == "openrouter":
        return OpenRouterProvider(
            api_key=settings.get("llm_cloud_api_key", ""),
            base_url=settings.get("llm_cloud_url", "https://openrouter.ai/api/v1/chat/completions")
        )
    else:
        return OllamaProvider(
            base_url=settings.get("llm_local_url", OLLAMA_URL)
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
        ans = await AgentLoop(query, model, full_system, short_term, user_id, creds, mission_id)
    else:
        # Librarian handles standard single-turn inference
        ans = await _single_turn_inference(query, model, full_system, rag_context, short_term, creds, chunk_callback)
        
    return ans

async def _fetch_rag_context(query: str, user_id: str) -> str:
    rag_context = ""
    try:
        collections = ["ha_entities", "nextcloud_files", "system_capabilities", "system_learnings"]
        async with httpx.AsyncClient(timeout=10.0) as client:
            for coll in collections:
                resp = await client.post(
                    f"{RAG_SVC}/rag/search",
                    json={"collection_name": coll, "query": query, "user_id": user_id, "k": 10},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status_code == 200:
                    hits = resp.json().get("results", [])
                    if hits:
                        rag_context += f"\n[{coll.upper()}]\n" + "\n".join([h["content"] for h in hits])
    except Exception as e:
        log.error(f"RAG search failed: {e}")
    return rag_context

async def _single_turn_inference(query: str, model: str, system_prompt: str, rag_context: str, history: List[Dict[str, str]], creds: ResolvedCredentials, chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    system = f"{system_prompt.strip()}\n\nSystem Capability Context:\n{SINGLE_TURN_TOOL_GUIDE}\n\nRetrieved Context:\n{rag_context}"
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": query}]

    log.info(f"[_single_turn_inference] Executing for model {model}")
    
    # GBNF Grammar to force Natural Language + JSON format
    TOOL_GRAMMAR = (
        'root ::= content (json_block)?\n'
        'content ::= [^`]*\n'
        'json_block ::= "```json\\n" json_object "\\n```"\n'
        'json_object ::= "{" space "\\"action\\":" space string "," space "\\"payload\\":" space json_value space "}"\n'
        'json_value ::= json_object | json_array | string | number | "true" | "false" | "null"\n'
        'json_array ::= "[" space (json_value ("," space json_value)*)? space "]"\n'
        'string ::= "\\\"" ([^\\\"\\\\\\n] | "\\\\" [\\\"\\\\/bfnrt] | "\\\\u" [0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F])* "\\\""\n'
        'number ::= "-"? ([0-9] | [1-9][0-9]*) ("." [0-9]+)? ([eE] [+-]? [0-9]+)?\n'
        'space ::= [ \\t\\n\\r]*'
    )
    
    options = {"grammar": TOOL_GRAMMAR, "temperature": 0.0}

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
        
        if action in SINGLE_TURN_TOOL_ENDPOINTS:
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
