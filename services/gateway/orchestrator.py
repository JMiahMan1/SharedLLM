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
except (ImportError, ValueError):
    from schemas import ResolvedCredentials
    from llm_providers import BaseLLMProvider, OllamaProvider, OpenRouterProvider

log = logging.getLogger("gateway.orchestrator")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


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
    autonomy_signals = ["raven", "audit", "repair", "fix", "deploy", "bootstrap", "coding", "develop",
                        "push", "commit", "git", "patch", "workflow", "ci ", "pipeline",
                        "nextcloud", "storage", "upload", "download", "my files"]
    is_autonomous = any(k in query.lower() for k in autonomy_signals)
    
    # 4. Final Inference
    full_system = job_payload.get("system", "")
    if is_autonomous:
        try:
            from .agent_loop import AgentLoop
        except (ImportError, ValueError):
            from agent_loop import AgentLoop
        # Raven handles autonomous loops
        ans = await AgentLoop(query, model, full_system, short_term, user_id, creds)
    else:
        # Librarian handles standard single-turn inference
        ans = await _single_turn_inference(query, model, rag_context, short_term, creds, chunk_callback)
        
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

async def _single_turn_inference(query: str, model: str, rag_context: str, history: List[Dict[str, str]], creds: ResolvedCredentials, chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    settings = await get_llm_settings()
    provider = await get_provider(settings)

    system = f"You are the SharedLLM Assistant. Context:\n{rag_context}\n\nIf the user wants to control a device or perform a task, output a JSON block like: ```json\n{{\"action\": \"LightControlRequest\", \"payload\": {{\"entity_id\": \"light.xyz\", \"action\": \"turn_on\"}}}}\n```. If you execute a tool, do NOT include any other text in your response."
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": query}]

    log.info(f"[_single_turn_inference] Executing for model {model}")
    
    # --- RETRY LOGIC FOR MODEL SWITCHING ---
    MAX_INFERENCE_RETRIES = 3
    ans = ""
    
    for retry_count in range(MAX_INFERENCE_RETRIES):
        try:
            ans = await provider.generate(model, messages, chunk_callback=chunk_callback)
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
        
        # Comprehensive Tool Map (sync with agent_loop.py)
        action_map = {
            "lightcontrolrequest": "/execute/light",
            "mediaplayrequest": "/execute/media/play",
            "mediatransportrequest": "/execute/media/transport",
            "tvcastrequest": "/execute/tv_cast",
            "climaterequest": "/execute/climate",
            "securityrequest": "/execute/security",
            "announcementrequest": "/execute/announce",
            "haservicerequest": "/execute/ha_service",
            "calendarrequest": "/execute/calendar",
            "noterequest": "/execute/note",
            "timerrequest": "/execute/timer",
            "talkrequest": "/execute/talk",
            "websearchrequest": "/execute/web_search",
            "webreadrequest": "/execute/web_read",
            "discoverysyncrequest": "/execute/discovery_sync",
            "storageindexrequest": "/index/full",
            # Workspace / Git tools (Nextcloud + code)
            "workspacefilereadrequest": "/execute/workspace_file_read",
            "workspacefilewriterequest": "/execute/workspace_file_write",
            "workspacefilepatchrequest": "/execute/workspace_file_patch",
            "workspacesearchrequest": "/execute/workspace_search",
            "workspaceshellrequest": "/execute/workspace_shell",
            "gitoperationrequest": "/execute/git",
            "storagefilereadrequest": "/execute/storage_file_read",
            "storagefilewriterequest": "/execute/storage_file_write",
            "dockerlogsrequest": "/execute/docker_logs",
        }

        if action in action_map:
            endpoint = action_map[action]
            svc_base = STORAGE_SVC if "storage" in action or "index" in action else EXECUTION_SVC
            
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


