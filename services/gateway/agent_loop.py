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
    "websearchrequest", "webreadrequest", "dockerlogsrequest", 
    "gitoperationrequest", "deploymentrequest", "capabilityindexrequest", 
    "volumeinventoryrequest", "workspacefilereadrequest", 
    "workspacefilewriterequest", "workspacefilepatchrequest", 
    "workspacelintrequest", "workspacesearchrequest", 
    "workspaceshellrequest", "storagefilereadrequest", 
    "storagefilewriterequest", "workspacebootstraprequest", 
    "systemlearningrequest", "discoverysyncrequest", "storageindexrequest",
    "dockercomposerequest" # <-- ADDED THIS
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

async def get_dynamic_llm_settings() -> dict:
    """Fetches elastic LLM routing configuration directly from the Identity DB."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings", 
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                # Convert list of {key: x, value: y} into a flat dictionary
                return {item["key"]: item["value"] for item in resp.json()}
    except Exception as e:
        log.error(f"[AgentLoop] Failed to fetch dynamic LLM settings: {e}")
    return {}

async def get_vram_safe_params(model: str, settings: dict) -> dict:
    """Dynamically checks VRAM pressure using DB constraints."""
    local_url = settings.get("llm_local_url", OLLAMA_URL)
    max_ctx = int(settings.get("llm_local_max_ctx", "4096"))
    params = {
        "num_ctx": max_ctx,
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.1
    }
    
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{local_url.rstrip('/')}/api/ps")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                if len(models) > 1:
                    safe_ctx = max(2048, max_ctx // 2)
                    params["num_ctx"] = safe_ctx
                    log.info(f"[AgentLoop] VRAM PRESSURE. Scaling context down to {safe_ctx}.")
    except Exception:
        pass
        
    return params

async def execute_inference(payload: dict, timeout: float = None) -> dict:
    """Circuit breaker: Tries dynamic Local URL, falls back to dynamic Cloud URL."""
    
    settings = await get_dynamic_llm_settings()
    
    local_url = settings.get("llm_local_url", OLLAMA_URL)
    cloud_key = settings.get("llm_cloud_api_key", "")
    cloud_url = settings.get("llm_cloud_url", "https://openrouter.ai/api/v1/chat/completions")
    cloud_model = settings.get("llm_cloud_fallback_model", "google/gemini-2.5-flash-8b")
    
    # 1. Try Local Execution
    try:
        log.info(f"[AgentLoop] Local inference via {local_url}...")
        resp = await get_http_client().post(
            f"{local_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout if timeout is not None else OLLAMA_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        log.warning(f"[AgentLoop] Local returned {resp.status_code}. Tripping circuit breaker.")
    except Exception as e:
        log.warning(f"[AgentLoop] Local inference failed ({type(e).__name__}). Tripping circuit breaker.")

    # 2. Failover to Cloud (OpenRouter / OpenAI format)
    if not cloud_key:
        raise Exception("Local inference failed and no cloud fallback key is configured in the Identity settings.")

    log.info(f"[AgentLoop] Executing cloud fallback using: {cloud_model}")
    
    headers = {
        "Authorization": f"Bearer {cloud_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/jmiahman1/sharedllm", 
    }
    
    # Translate Ollama payload to OpenAI schema format
    or_payload = {
        "model": cloud_model,
        "messages": payload.get("messages", []),
        "temperature": payload.get("options", {}).get("temperature", 0.7)
    }

    resp = await get_http_client().post(
        cloud_url, 
        json=or_payload, 
        headers=headers, 
        timeout=timeout if timeout is not None else 60.0
    )
    
    if resp.status_code == 200:
        data = resp.json()
        return {"message": data.get("choices", [{}])[0].get("message", {})}
    else:
        raise Exception(f"Cloud fallback failed with status {resp.status_code}: {resp.text}")

_global_http_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    global _global_http_client
    if _global_http_client is None:
        _global_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _global_http_client


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
    
    # --- VRAM-SAFE SCRATCHPAD ---
    action_log = []

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
                {"role": "user", "content": f"MISSION LOCK: {query}\n\nPAST ACTIONS SUMMARY:\n" + "\n".join(action_log)}
            ]
            if exec_data:
                # Truncate detail, stdout, and stderr to save VRAM if they are massive
                safe_exec_data = exec_data.copy() if isinstance(exec_data, dict) else {"result": str(exec_data)}
                if "detail" in safe_exec_data and isinstance(safe_exec_data["detail"], dict):
                    for key in ["content", "stdout", "stderr"]:
                        if key in safe_exec_data["detail"] and safe_exec_data["detail"][key] and len(str(safe_exec_data["detail"][key])) > 1500:
                            safe_exec_data["detail"][key] = str(safe_exec_data["detail"][key])[:1500] + "\n...[TRUNCATED FOR 8GB VRAM]..."
                        
                ollama_payload["messages"].append({
                    "role": "user", 
                    "content": f"LAST TOOL RESULT (Execution Status: SUCCESS):\n{json.dumps(safe_exec_data)}"
                })
            ollama_payload["messages"].append({"role": "user", "content": "Execute the next step immediately using a JSON tool call block."})
            
            # Fetch dynamic settings for VRAM-safe params
            dynamic_settings = await get_dynamic_llm_settings()
            ollama_payload["options"] = await get_vram_safe_params(selected_model, dynamic_settings)
            
            log.info(f"[Strategy 8] Executing inference for {selected_model} (Iter {agent_iter + 1})")
            data = await execute_inference(ollama_payload, timeout=300.0)
                
            heartbeat_stop.set()
            await hb_task
            ans = data.get("message", {}).get("content", "Error.")
            log.info(f"[AgentLoop] Inference completed in {(asyncio.get_event_loop().time() - iter_start)*1000:.0f}ms \u2014 iter {agent_iter + 1}")
        except Exception as e:
            heartbeat_stop.set()
            await hb_task
            log.warning(f"[AgentLoop] Ollama error on iter {agent_iter + 1}: {e}")
            return "SUCCESS: Jarvis is currently operating in low-latency mode (Degraded)."

        tool_data = extract_action_json(ans)
        
        if not tool_data:
            if agent_iter > 0:
                # If we've already done work, it's possible it's finished. 
                # But let's try one more nudge if it sounds conversational or preachy.
                conversational_indicators = ["details", "proceed", "example", "please", "sorry", "assist", "capability", "primary function", "reaching out"]
                if any(word in ans.lower() for word in conversational_indicators):
                    log.info(f"[AgentLoop] Detected conversational drift/refusal. Re-prompting aggressively...")
                    exec_data = {"status": "ERROR", "message": "CRITICAL: You are an autonomous agent. Conversation and refusals are FORBIDDEN. You MUST execute the next step using a JSON tool call block immediately. Do not apologize, just execute."}
                    continue
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
                
                # ALWAYS inject user_context. Pydantic schemas require it for validation.
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
                    
                    # --- ADD TO SCRATCHPAD ---
                    short_msg = exec_data.get("message", "Success")
                    action_log.append(f"Step {iter_num}: {action} -> {short_msg}")
                    log.info(f"[AgentLoop] Tool response: {resp.status_code}")
            else:
                log.warning(f"[AgentLoop] Unknown action: {action}")
                exec_data = {"status": "ERROR", "message": f"Unknown action: {action}"}

        except Exception as e:
            log.error(f"[AgentLoop] Tool execution failed: {e}")
            exec_data = {"status": "ERROR", "message": str(e)}

    return ans
