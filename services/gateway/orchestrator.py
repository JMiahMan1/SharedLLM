# services/gateway/orchestrator.py
import asyncio
import json
import re
import logging
import httpx
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable, Awaitable
from gateway.schemas import ResolvedCredentials
from gateway.llm_providers import BaseLLMProvider, OllamaProvider, OpenRouterProvider

from gateway.config import (
    WORKSPACE_RUNTIME_SVC, INTERNAL_SECRET, EXECUTION_SVC, RAG_SVC,
    IDENTITY_SVC, STORAGE_SVC, OLLAMA_URL, CONTROL_PLANE_URL, OLLAMA_TIMEOUT,
)

log = logging.getLogger("gateway.orchestrator")

SINGLE_TURN_TOOL_ENDPOINTS: Dict[str, tuple[str, str]] = {
    "lightcontrolrequest": (EXECUTION_SVC, "/execute/light"),
    "mediaplayrequest": (EXECUTION_SVC, "/execute/media/play"),
    "mediatransportrequest": (EXECUTION_SVC, "/execute/media/transport"),
    "mediastatusrequest": (EXECUTION_SVC, "/execute/media/status"),
    "videoplayrequest": (EXECUTION_SVC, "/execute/video/play"),
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
    "executionlogrequest": (EXECUTION_SVC, "/execute/logs"),
    "audiobookshelfrequest": (EXECUTION_SVC, "/execute/audiobookshelf"),
    "documentbroadcastrequest": (EXECUTION_SVC, "/execute/composite/broadcast"),
    "nightmoderequest": (EXECUTION_SVC, "/execute/composite/night_mode"),
    "contextsearchrequest": (RAG_SVC, "/rag/search"),
    "haconfigrequest": (EXECUTION_SVC, "/execute/ha_config"),
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
- Home Assistant: LightControlRequest, MediaPlayRequest, MediaStatusRequest (for "what's playing"), LogbookRequest (for device logs)
- Verification: ExecutionLogRequest (use to verify a task was actually performed - filters by service/keyword)

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
    show_thinking = job_payload.get("show_thinking", False)
    
    log.info(f"[Orchestrator] Job model from payload: '{model}'")
    
    # 0. Query-based Model Override (e.g. "Raven use model qwen2.5:32b fix...")
    model_match = re.search(r"(?:use model|with model|run on model)\s+([a-zA-Z0-9.\-_:]+)", query, re.IGNORECASE)
    if model_match:
        model = model_match.group(1)
        log.info(f"[Orchestrator] Dynamic model override detected: {model}")
    
    log.info(f"[Orchestrator] Starting orchestration for query: {query[:50]}...")
    
    # 1. Retrieve Memory
    short_term = [] # Placeholder
    
    # 2. Context Injection (RAG + live HA state)
    rag_context = await _fetch_rag_context(query, user_id, creds)
    
    # 3. Autonomous Detection (Raven/Coding/Repair ONLY)
    # Raven runs in Workspaces and handles long-running or coding tasks.
    # Home Automation should NOT be treated as autonomous (no long-running loops)
    autonomy_signals = [
        "raven", "use raven", "audit", "repair", "self repair", "self-heal",
        "self fix", "deploy", "bootstrap", "develop", "fix the app",
        "fix the service", "fix the codebase", "agentic", "autonomous",
        "audit the codebase", "sync workspace", "pull latest", "convert them to",
        "review requirements", "check dependencies", "report any conflicts",
    ]
    # Also match queries starting with action verbs
    is_autonomous = any(k in query.lower() for k in autonomy_signals)
    if not is_autonomous:
        first_word = query.lower().split()[0] if query.split() else ""
        is_autonomous = first_word in ("fix", "repair", "audit", "deploy", "convert", "review", "check", "update", "refactor")
    
    # 4. Final Inference
    full_system = job_payload.get("system", "")
    if is_autonomous:
        from gateway.agent_loop import AgentLoop
        # Raven handles autonomous loops
        mission_id = job_payload.get("_mission_id")
        ans = await AgentLoop(query, model, full_system, short_term, user_id, creds, mission_id, rag_context=rag_context, show_thinking=show_thinking)
    else:
        # Librarian handles standard single-turn inference
        ans = await _single_turn_inference(query, model, full_system, rag_context, short_term, creds, chunk_callback, show_thinking=show_thinking)
        
    return ans

async def _fetch_rag_context(query: str, user_id: str, creds: Optional[ResolvedCredentials] = None) -> str:
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
                        # For HA entities, enrich with live state from HA
                        if coll == "ha_entities" and creds:
                            hits = await _enrich_entities_with_live_state(hits, creds)
                        
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

    # Proactively inject weather context for weather-related queries
    weather_keywords = ["weather", "forecast", "rain", "snow", "temperature", "outside", "humid", "wind", "storm", "sunny", "cloudy", "cold", "hot", "warm"]
    if any(kw in q for kw in weather_keywords) and creds:
        weather_ctx = await _fetch_weather_context(creds)
        if weather_ctx:
            rag_context += f"\n[WEATHER]\n{weather_ctx}\n"

    return rag_context.strip()


async def _fetch_weather_context(creds: ResolvedCredentials) -> str:
    """Dynamically discover weather entities from HA and return live forecast data."""
    
    ha_url = getattr(creds, "ha_url", None)
    ha_token = getattr(creds, "ha_token", None)
    if not ha_url or not ha_token:
        return ""
    
    try:
        # Fetch all entities to find weather domain
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{EXECUTION_SVC}/discovery/entities",
                params={"ha_url": ha_url, "ha_token": ha_token},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            entities = data.get("entities", []) if isinstance(data, dict) else []
        
        # Find weather domain entities
        weather_entities = [e for e in entities if e.get("entity_id", "").startswith("weather.")]
        if not weather_entities:
            return ""
        
        # Cache all states for future lookups
        from gateway.ha_state_cache import cache_all_states
        cache_all_states(entities)
        
        parts = []
        for e in weather_entities:
            eid = e.get("entity_id", "")
            friendly = e.get("attributes", {}).get("friendly_name", eid)
            state = e.get("state", "unknown")
            attrs = e.get("attributes", {})
            
            details = [f"{friendly} ({eid}): {state}"]
            for key in ["temperature", "humidity", "forecast", "wind_speed", "pressure", "dew_point", "uv_index", "precipitation"]:
                if key in attrs:
                    details.append(f"  {key}: {attrs[key]}")
            
            # Include forecast if available
            forecast = attrs.get("forecast", [])
            if forecast:
                forecast_items = []
                for f in forecast[:3]:  # Next 3 forecast periods
                    f_parts = []
                    if "datetime" in f:
                        f_parts.append(f["datetime"][:10])
                    if "condition" in f:
                        f_parts.append(f["condition"])
                    if "temperature" in f:
                        f_parts.append(f"{f['temperature']}°")
                    if "precipitation" in f:
                        f_parts.append(f"rain: {f['precipitation']}mm")
                    if f_parts:
                        forecast_items.append(" ".join(f_parts))
                if forecast_items:
                    details.append(f"  forecast: {' | '.join(forecast_items)}")
            
            parts.append("\n".join(details))
        
        return "\n".join(parts)
    except Exception as e:
        log.error(f"Failed to fetch weather context: {e}")
        return ""

async def _enrich_entities_with_live_state(hits: list, creds: ResolvedCredentials) -> list:
    """Fetch live HA state (Redis-cached) and merge with RAG entity metadata.
    
    entity_id is the stable join key between RAG metadata and live state.
    Even if friendly_name changes in HA, entity_id remains constant.
    """
    from gateway.ha_state_cache import get_cached_state, fetch_live_states
    
    ha_url = getattr(creds, "ha_url", None)
    ha_token = getattr(creds, "ha_token", None)
    if not ha_url or not ha_token:
        return hits
    
    # Collect entity_ids from RAG hits
    entity_ids = [h.get("entity_id", "") for h in hits if h.get("entity_id")]
    if not entity_ids:
        return hits
    
    # Try Redis cache first
    live_states: dict[str, str] = {}
    cache_misses = []
    for eid in entity_ids:
        cached = get_cached_state(eid)
        if cached is not None:
            live_states[eid] = cached
        else:
            cache_misses.append(eid)
    
    # On cache miss, fetch all live states and repopulate cache
    if cache_misses:
        entities = await fetch_live_states(EXECUTION_SVC, ha_url, ha_token, INTERNAL_SECRET)
        for e in entities:
            eid = e.get("entity_id", "")
            state = e.get("state", "unknown")
            if eid:
                live_states[eid] = state
    
    # Merge live state into RAG hits
    ACTIVE_STATES = {"on", "playing", "idle", "standby", "home", "cooling", "heating", "drying", "cleaning"}
    
    for h in hits:
        eid = h.get("entity_id", "")
        live_state = live_states.get(eid)
        if live_state:
            is_active = live_state.lower() in ACTIVE_STATES
            h["is_active"] = is_active
            h["live_state"] = live_state
            # Rewrite content with live state
            base = h["content"]
            if "Current State:" in base:
                state_label = f"[ACTIVE] {live_state}" if is_active else live_state
                base = base.replace("Current State:", f"Current State (live): {state_label} |")
            else:
                base += f" | Current State (live): {live_state}"
            h["content"] = base
    
    # Sort: active devices first
    hits = sorted(hits, key=lambda h: not h.get("is_active", False), reverse=True)
    return hits

async def _execute_single_tool(action: str, tool_data: dict, query: str, creds: ResolvedCredentials) -> str:
    """Execute a single tool call and return the result string."""
    if action == "controlplanerequest":
        payload = tool_data.get("payload", tool_data)
        service_name = payload.get("service_name")
        sub_action = payload.get("action", "restart")
        if not service_name:
            return "Error: service_name is required"
        cp_url = CONTROL_PLANE_URL or "http://control_plane:8008"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if sub_action == "restart":
                    resp = await client.post(f"{cp_url}/api/restart/{service_name}", headers={"X-Internal-Secret": INTERNAL_SECRET})
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
            
            if action == "contextsearchrequest":
                payload["user_id"] = creds.user or "default"
            
            if action == "announcementrequest" and not payload.get("entity_id") and not payload.get("device_name"):
                device_match = re.search(r"(?:on|to|via|at|using)\s+(?:the\s+)?([A-Z][A-Za-z\s]+?)(?:\s+(?:speaker|tv|device|display|cast|chrome))", query)
                if not device_match:
                    device_match = re.search(r"(?:on|to|via|at|using)\s+(?:the\s+)?((?:Office|Living Room|Loft|Bedroom|Kitchen|Bathroom)[A-Za-z\s]*?)(?:\b)", query)
                if device_match:
                    device_name = device_match.group(1).strip()
                    if not any(t in device_name.lower() for t in ["tv", "speaker", "display", "cast", "chrome"]):
                        type_match = re.search(r"(speaker|tv|display|cast|chrome)", query, re.IGNORECASE)
                        if type_match:
                            device_name += " " + type_match.group(1)
                    payload["device_name"] = device_name
                    log.info(f"[_execute_single_tool] Auto-resolved device_name='{device_name}' from query")
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{svc_base}{endpoint}", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
                if resp.status_code == 200:
                    result = resp.json()
                    if action == "executionlogrequest":
                        logs = result.get("detail", {}).get("logs", "")
                        if logs:
                            return f"{result.get('message', '')}\n\n```\n{logs}\n```"
                    if action == "contextsearchrequest":
                        results = result.get("results", [])
                        if not results:
                            return "No relevant context found. Try a different query or collection."
                        context_text = "\n".join([f"- {r.get('content', '')}" for r in results])
                        return f"Found {len(results)} relevant results:\n{context_text}"
                    if action == "haconfigrequest":
                        detail = result.get("detail", {})
                        if detail:
                            return f"{result.get('message', '')}\n\nDetail:\n{json.dumps(detail, indent=2)}"
                        return result.get("message", "Action completed successfully.")
                    return result.get("message", "Action completed successfully.")
                else:
                    return f"Tool execution failed ({resp.status_code}): {resp.text}"
        except Exception as e:
            log.error(f"Single-turn tool execution error: {e}")
            return f"I encountered an error while executing the tool: {e}"
    else:
        log.warning(f"[_execute_single_tool] Unsupported tool for single-turn: {action}")
        return f"I found a tool call for '{action}', but it is not supported in the standard path. Please ask Raven to perform this task."

async def _single_turn_inference(query: str, model: str, system_prompt: str, rag_context: str, history: List[Dict[str, str]], creds: ResolvedCredentials, chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None, show_thinking: bool = False) -> str:
    now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p %Z")
    system = f"{system_prompt.strip()}\n\nCurrent Date/Time: {now}\n\nSystem Capability Context:\n{SINGLE_TURN_TOOL_GUIDE}\n\nRetrieved Context:\n{rag_context}"
    log.info(f"[_single_turn_inference] RAG context length: {len(rag_context)} chars")
    if rag_context:
        log.info(f"[_single_turn_inference] RAG context preview: {rag_context[:300]}")
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": query}]

    log.info(f"[_single_turn_inference] Executing for model {model}")
    
    options = {"temperature": 0.0, "num_predict": 2048, "show_thinking": show_thinking}

    MAX_INFERENCE_RETRIES = 3
    MAX_TURNS = 3

    from gateway.agent_loop import extract_action_json

    for turn in range(MAX_TURNS):
        log.info(f"[_single_turn_inference] Turn {turn + 1}/{MAX_TURNS}")
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
                break
            except Exception as e:
                log.warning(f"[_single_turn_inference] Inference attempt {retry_count + 1} failed: {e}")
                if retry_count < MAX_INFERENCE_RETRIES - 1:
                    wait_time = 5 * (retry_count + 1)
                    log.info(f"[_single_turn_inference] Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    log.error(f"[_single_turn_inference] FATAL: All inference retries failed: {e}")
                    return f"I encountered an error while trying to generate a response (All retries failed): {e}"

        tool_data = extract_action_json(ans)
        if not tool_data:
            # No tool call — this is our final answer
            break

        # Normalize tool_data keys
        if "tool" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data.pop("tool")
        if "operation" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data.pop("operation")
        if "command" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data.pop("command")
        if "name" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data.pop("name")
        if "arguments" in tool_data and "payload" not in tool_data:
            tool_data["payload"] = tool_data.pop("arguments")
        if "parameters" in tool_data and "payload" not in tool_data:
            tool_data["payload"] = tool_data.pop("parameters")
        if "function" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data["function"].get("name", "")
            tool_data["payload"] = tool_data["function"].get("arguments", {})

        # Normalize hallucinated/shortened action names
        action_aliases = {
            "filewriterequest": "workspacefilewriterequest",
            "filereadrequest": "workspacefilereadrequest",
            "filepatchrequest": "workspacefilepatchrequest",
        }
        raw_action = tool_data.get("action", "").lower().strip()
        if raw_action in action_aliases:
            tool_data["action"] = action_aliases[raw_action]

        action = tool_data.get("action", "").lower().strip()
        log.info(f"[_single_turn_inference] Tool call detected: {action}")
        log.info(f"[_single_turn_inference] Raw LLM output: {ans[:500]}")
        log.info(f"[_single_turn_inference] Extracted tool_data: {tool_data}")

        # Append LLM's response to conversation
        messages.append({"role": "assistant", "content": ans})

        # Execute the tool
        tool_result = await _execute_single_tool(action, tool_data, query, creds)
        log.info(f"[_single_turn_inference] Tool result: {tool_result[:300] if tool_result else 'empty'}")

        # Post-write lint hook: auto-lint after file write/patch to catch syntax errors
        lintable_actions = {"workspacefilewriterequest", "workspacefilepatchrequest"}
        if action.lower() in lintable_actions and isinstance(tool_data.get("payload"), dict):
            payload = tool_data["payload"]
            file_path = payload.get("file_path", "") or payload.get("path", "") or payload.get("relative_path", "")
            if file_path:
                from gateway.agent_loop import run_post_write_lint
                user_ctx = payload.get("user_context") or creds.model_dump()
                lint_feedback = await run_post_write_lint(file_path, EXECUTION_SVC, INTERNAL_SECRET, log, user_ctx)
                if lint_feedback:
                    tool_result = f"{tool_result}\n\n{lint_feedback}"

        # Append tool result to conversation for next turn
        messages.append({"role": "user", "content": f"Tool result:\n{tool_result}"})

        # If this was the last turn, return the tool result directly
        if turn == MAX_TURNS - 1:
            return tool_result

    # Final answer processing — strip pure JSON if it wraps a readable field
    log.info(f"[_single_turn_inference] Final answer length: {len(ans)} chars, preview: {ans[:200]}")
    if ans.strip().startswith("{") and ans.strip().endswith("}"):
        try:
            parsed = json.loads(ans)
            for key in ["response", "answer", "message", "text", "content", "reply"]:
                if key in parsed and isinstance(parsed[key], str):
                    return parsed[key]
        except (json.JSONDecodeError, ValueError):
            pass
    return ans
