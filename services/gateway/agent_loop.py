import os
import logging
import json
import asyncio
import httpx
import re
from typing import Optional, Any, Dict, List
from fastapi.responses import JSONResponse

try:
    from .config import (
        OLLAMA_URL, EXECUTION_SVC, WORKSPACE_RUNTIME_SVC, 
        STORAGE_SVC, INTERNAL_SECRET, OLLAMA_TIMEOUT
    )
    from .schemas import ResolvedCredentials
    from .messaging import INFERENCE_LOCK
except (ImportError, ValueError):
    from config import (
        OLLAMA_URL, EXECUTION_SVC, WORKSPACE_RUNTIME_SVC, 
        STORAGE_SVC, INTERNAL_SECRET, OLLAMA_TIMEOUT
    )
    from schemas import ResolvedCredentials
    from messaging import INFERENCE_LOCK

log = logging.getLogger("gateway.agent_loop")

ALLOWED_TOOLS = {
    "lightcontrolrequest", "mediaplayrequest", "mediatransportrequest", 
    "tvcastrequest", "climaterequest", "securityrequest", 
    "announcementrequest", "haservicerequest", "calendarrequest", 
    "noterequest", "timerrequest", "talkrequest", 
    "websearchrequest", "webreadrequest", "dockerlogsrequest", "dockercomposerequest",
    "gitoperationrequest", "deploymentrequest", "capabilityindexrequest", 
    "volumeinventoryrequest", "workspacefilereadrequest", 
    "workspacefilewriterequest", "workspacefilepatchrequest", 
    "workspacelintrequest", "workspacesearchrequest", 
    "workspaceshellrequest", "storagefilereadrequest", 
    "storagefilewriterequest", "workspacebootstraprequest", 
    "systemlearningrequest", "discoverysyncrequest", "storageindexrequest"
}

def extract_action_json(text: str) -> dict | None:
    """Extracts the first JSON object found in the text, with MoE-safe fallback and log-stripping."""
    if not text:
        return None
    
    text = re.sub(r"^INFO:.*?\n", "", text, flags=re.MULTILINE)
    
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except:
            pass

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace+1]
        try:
            return json.loads(candidate)
        except:
            try:
                cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
                return json.loads(cleaned)
            except:
                pass
    
    return None

async def get_vram_safe_params(model: str) -> dict:
    max_ctx = int(os.getenv("MAX_CONTEXT_WINDOW", "32768"))
    target_ctx = int(os.getenv("DEFAULT_CONTEXT_WINDOW", "12288"))
    
    external_indicators = ("openrouter.ai", "openai.com", "anthropic.com", "groq.com")
    if any(ind in OLLAMA_URL.lower() for ind in external_indicators):
        return {"num_ctx": max_ctx}

    params = {"num_ctx": target_ctx}
    log.info(f"[AgentLoop] Checking VRAM state at {OLLAMA_URL}/api/ps")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/ps")
            log.info(f"[AgentLoop] VRAM check status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                if len(models) > 1:
                    params["num_ctx"] = 4096
                    log.info(f"[Strategy 7] VRAM PRESSURE DETECTED ({len(models)} models active). Scaling context to 4096.")
            else:
                log.warning(f"[AgentLoop] VRAM check failed: {resp.status_code}")
    except Exception as e:
        log.warning(f"[AgentLoop] VRAM check exception: {e}")
    return params

_global_http_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    global _global_http_client
    if _global_http_client is None:
        _global_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _global_http_client

async def call_ollama(payload: dict, use_chat: bool = True, timeout: float = None) -> httpx.Response:
    endpoint = "/api/chat" if use_chat else "/api/generate"
    url = f"{OLLAMA_URL}{endpoint}"
    log.info(f"[AgentLoop] Calling Ollama: {url}")
    return await get_http_client().post(
      url,
      json=payload,
      timeout=timeout if timeout is not None else OLLAMA_TIMEOUT,
    )

async def AgentLoop(query: str, selected_model: str, full_system: str, short_term: list, rag_user: str, creds: ResolvedCredentials) -> Any:
    ollama_payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": full_system}
        ] + short_term + [{"role": "user", "content": query}],
        "stream": False
    }

    MAX_TOOL_ITERATIONS = 30
    HEARTBEAT_INTERVAL = 15
    HUNG_THRESHOLD = 240
    agent_messages = ollama_payload.get("messages", [])[:]
    exec_data = None
    ans = ""
    loop_start = asyncio.get_event_loop().time()

    for agent_iter in range(MAX_TOOL_ITERATIONS):
        iter_num = agent_iter + 1
        iter_start = asyncio.get_event_loop().time()
        
        log.info(f"[AgentLoop] Iteration {iter_num}/{MAX_TOOL_ITERATIONS} | total elapsed {iter_start - loop_start:.0f}s")

        heartbeat_stop = asyncio.Event()

        async def _heartbeat(iter_n: int, t0: float) -> None:
            while not heartbeat_stop.is_set():
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if heartbeat_stop.is_set():
                    break
                elapsed = asyncio.get_event_loop().time() - t0
                if elapsed > HUNG_THRESHOLD:
                    log.warning(f"[AgentLoop] \u26a0 HUNG WARNING \u2014 iter {iter_n} has been waiting {elapsed:.0f}s for Ollama response")
                else:
                    log.info(f"[AgentLoop] \u2665 heartbeat \u2014 iter {iter_n} | waiting for Ollama {elapsed:.0f}s")

        hb_task = asyncio.create_task(_heartbeat(agent_iter + 1, iter_start))

        try:
            vram_params = await get_vram_safe_params(selected_model)
            ollama_payload["options"] = vram_params
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
            
            log.info(f"[Strategy 8] Executing inference for {selected_model} (Iter {agent_iter + 1})")
            resp = await call_ollama(ollama_payload, use_chat=True, timeout=300.0)
                
            heartbeat_stop.set()
            await hb_task
            if resp.status_code != 200:
                return JSONResponse({"status": "ERROR", "message": "Brain offline."}, status_code=502)
            data = resp.json()
            ans = data.get("message", {}).get("content", "Error.")
            log.info(f"[AgentLoop] Ollama responded in {(asyncio.get_event_loop().time() - iter_start)*1000:.0f}ms \u2014 iter {agent_iter + 1}")
        except Exception as e:
            heartbeat_stop.set()
            await hb_task
            log.warning(f"[AgentLoop] Ollama error on iter {agent_iter + 1}: {e}")
            return JSONResponse({"status": "SUCCESS", "message": "Jarvis is currently operating in low-latency mode.", "degraded": True})

        tool_data = extract_action_json(ans)
        
        if not tool_data:
            if agent_iter > 0:
                log.info(f"[AgentLoop] Mission likely accomplished. Terminating loop.")
                break
            log.info(f"[AgentLoop] Re-prompting for autonomous tool execution...")
            continue

        log.info(f"[AgentLoop] Dispatching action: {tool_data.get('action')}")

        try:
            action = tool_data.get("action")
            payload = tool_data.get("payload", tool_data)
            
            action_map_aliases = {
                "read_file": "WorkspaceFileReadRequest",
                "write_file": "WorkspaceFileWriteRequest",
                "patch_file": "WorkspaceFilePatchRequest",
                "lint_file": "WorkspaceLintRequest",
                "ripgrep": "WorkspaceSearchRequest",
                "grep": "WorkspaceSearchRequest",
                "search": "WorkspaceSearchRequest",
                "shell": "WorkspaceShellRequest",
                "run": "WorkspaceShellRequest",
                "gitstatus": "GitOperationRequest",
                "gitdiff": "GitOperationRequest",
                "gitlog": "GitOperationRequest",
                "gitpull": "GitOperationRequest"
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

                async with httpx.AsyncClient(timeout=120.0) as client:
                    log.info(f"[AgentLoop] Sending payload to {endpoint}: {json.dumps(payload)}")
                    resp = await client.post(f"{svc_base}{endpoint}", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
                    exec_data = resp.json()
                    log.info(f"[AgentLoop] Tool response: {resp.status_code}")
            else:
                log.warning(f"[AgentLoop] Unknown action: {action}")
                exec_data = {"status": "ERROR", "message": f"Unknown action: {action}"}

        except Exception as e:
            log.error(f"[AgentLoop] Tool execution failed: {e}")
            exec_data = {"status": "ERROR", "message": str(e)}

    return ans
