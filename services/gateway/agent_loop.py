import os
import logging
import json
import asyncio
import httpx
import re
from typing import Optional, Any, Dict, List, Callable, Awaitable
from fastapi.responses import JSONResponse

try:
    from .config import (
        OLLAMA_URL, IDENTITY_SVC, EXECUTION_SVC, WORKSPACE_RUNTIME_SVC, 
        STORAGE_SVC, INTERNAL_SECRET, OLLAMA_TIMEOUT
    )
    from .schemas import ResolvedCredentials
    from .messaging import INFERENCE_LOCK
    from .llm_providers import BaseLLMProvider, OpenRouterProvider
except (ImportError, ValueError):
    from config import (
        OLLAMA_URL, IDENTITY_SVC, EXECUTION_SVC, WORKSPACE_RUNTIME_SVC, 
        STORAGE_SVC, INTERNAL_SECRET, OLLAMA_TIMEOUT
    )
    from schemas import ResolvedCredentials
    from messaging import INFERENCE_LOCK
    from llm_providers import BaseLLMProvider, OpenRouterProvider

# --- HARDENED OLLAMA PROVIDER OVERRIDE ---
class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,  # Hardened: Always stream to survive proxy-side parsing bugs
            "options": options or {}
        }

        full_content = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            log.info(f"[OllamaProvider-Hardened] Calling {self.base_url}/api/chat for model {model}")
            if not chunk_callback:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                
                # Harden: Strip keep-alive spaces and handle potential multi-line/streamed JSON
                raw_text = resp.text.strip()
                if not raw_text:
                    return ""
                
                # If the response contains multiple JSON objects (NDJSON), take the last one or merge
                if "\n" in raw_text:
                    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                    content = ""
                    for line in lines:
                        try:
                            data = json.loads(line)
                            
                            # Handle errors from proxy/Ollama
                            if "error" in data:
                                content += f" [PROVIDER ERROR: {data['error']}] "
                            
                            content += data.get("message", {}).get("content") or ""
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
                    return content
                
                try:
                    data = json.loads(raw_text)
                    if "error" in data:
                        return f" [PROVIDER ERROR: {data['error']}] "
                    return data.get("message", {}).get("content") or ""
                except json.JSONDecodeError as e:
                    log.error(f"[OllamaProvider-Hardened] Failed to parse JSON: {raw_text[:100]}... Error: {e}")
                    return ""

            # Streaming
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk_json = json.loads(line)
                        if "error" in chunk_json:
                            full_content += f" [PROVIDER ERROR: {chunk_json['error']}] "
                        
                        content = chunk_json.get("message", {}).get("content") or ""
                        if content:
                            full_content += content
                            await chunk_callback(content)
                        if chunk_json.get("done"):
                            break
                    except Exception:
                        # Silently skip keep-alives or malformed chunks
                        continue
        return full_content

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
                raw_text = resp.text.strip()
                if not raw_text:
                    return params
                
                try:
                    data = json.loads(raw_text)
                    models = data.get("models", [])
                    if len(models) > 1:
                        safe_ctx = max(2048, max_ctx // 2)
                        params["num_ctx"] = safe_ctx
                        log.info(f"[AgentLoop] VRAM PRESSURE. Scaling context down to {safe_ctx}.")
                except json.JSONDecodeError:
                    log.warning(f"[AgentLoop] Failed to parse VRAM status (api/ps) from {local_url}")
    except Exception:
        pass
    return params


async def get_provider(settings: dict) -> BaseLLMProvider:
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

async def execute_inference(provider: BaseLLMProvider, model: str, messages: list, options: dict) -> dict:
    """Delegates inference to the specified provider."""
    content = await provider.generate(model, messages, options=options)
    return {"message": {"role": "assistant", "content": content}}

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
    # 1. Fetch dynamic settings and resolve active provider/model
    settings = await get_dynamic_llm_settings()
    provider = await get_provider(settings)
    active_provider_name = settings.get("active_llm_provider", "ollama")

    # 2. Resolve Role-Based Model (Coder/Assistant) if selected_model is generic or "auto"
    if selected_model in ["auto", "assistant", "coder"]:
        if active_provider_name == "openrouter":
            if "coder" in selected_model or "fix" in query.lower() or "repair" in query.lower():
                selected_model = settings.get("cloud_coding_model", "anthropic/claude-3.5-sonnet")
            else:
                selected_model = settings.get("cloud_assistant_model", "google/gemini-2.0-flash-001")
        else:
            if "coder" in selected_model or "fix" in query.lower() or "repair" in query.lower():
                selected_model = settings.get("ollama_coding_model", "qwen2.5-coder:7b")
            else:
                selected_model = settings.get("ollama_assistant_model", "qwen3.5:9b")

    log.info(f"[AgentLoop] Active Provider: {active_provider_name} | Model: {selected_model}")

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

            # --- RETRY LOGIC FOR MODEL SWITCHING ---
            MAX_INFERENCE_RETRIES = 3
            inference_success = False
            
            for retry_count in range(MAX_INFERENCE_RETRIES):
                try:
                    dynamic_settings = await get_dynamic_llm_settings()
                    vram_params = await get_vram_safe_params(selected_model, dynamic_settings)
                    ollama_payload["options"] = vram_params
                    
                    log.info(f"[AgentLoop] Executing inference (Attempt {retry_count + 1}/{MAX_INFERENCE_RETRIES}) for {selected_model}")
                    data = await execute_inference(
                        provider,
                        selected_model,
                        ollama_payload["messages"],
                        ollama_payload.get("options", {})
                    )
                    
                    ans = data.get("message", {}).get("content", "Error.")
                    inference_success = True
                    break # Success!
                except Exception as e:
                    log.warning(f"[AgentLoop] Inference attempt {retry_count + 1} failed: {e}")
                    if retry_count < MAX_INFERENCE_RETRIES - 1:
                        wait_time = 5 * (retry_count + 1)
                        log.info(f"[AgentLoop] Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        raise e # Re-raise on final failure

            heartbeat_stop.set()
            await hb_task
            log.info(f"[AgentLoop] Inference completed in {(asyncio.get_event_loop().time() - iter_start)*1000:.0f}ms — iter {agent_iter + 1}")
            log.info(f"[AgentLoop] Response content: {ans[:200]}...")
        except Exception as e:
            heartbeat_stop.set()
            await hb_task
            log.error(f"[AgentLoop] FATAL: All inference retries failed on iter {agent_iter + 1}: {e}")
            return f"SYSTEM ERROR: Inference failed after multiple retries. Detail: {e}. Please check the LLM provider status."

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

        action_name = tool_data.get("action", "").lower()
        if action_name not in ALLOWED_TOOLS:
            log.warning(f"[AgentLoop] Unknown action: {action_name}")
            valid_list = ", ".join(sorted(list(ALLOWED_TOOLS)))
            error_msg = (
                f"SCHEMA ERROR: Unknown action '{action_name}'. "
                f"You MUST use one of the following valid tool names: {valid_list}. "
                "Correct your JSON and try again."
            )
            action_log.append(f"ITERATION {iter_num}: {error_msg}")
            exec_data = {"status": "ERROR", "message": error_msg}
            continue

        log.info(f"[AgentLoop] Dispatching action: {action_name}")

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
                "ShellCommand": "WorkspaceShellRequest",
                "WebRead": "WebReadRequest",
                "Browse": "WebReadRequest",
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
                    "api_key": creds.api_key,
                    "ha_url": creds.ha_url,
                    "ha_token": creds.ha_token,
                    "nextcloud_url": creds.nextcloud_url,
                    "nextcloud_user": creds.nextcloud_user,
                    "nextcloud_pass": creds.nextcloud_pass,
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
