import os
import logging
import json
import asyncio
import httpx
import re
import redis.asyncio as redis
from datetime import datetime
from typing import Optional, Any, Dict, List, Callable, Awaitable

from gateway.history import REDIS_URL
from gateway.config import (
    OLLAMA_URL, IDENTITY_SVC, EXECUTION_SVC, WORKSPACE_RUNTIME_SVC, 
    STORAGE_SVC, RAG_SVC, INTERNAL_SECRET,
    RAVEN_MAX_TOTAL_SECONDS,
    RAVEN_HEARTBEAT_INTERVAL, RAVEN_HUNG_THRESHOLD
)
from gateway.schemas import ResolvedCredentials
from gateway.llm_providers import BaseLLMProvider, OpenRouterProvider

CREDENTIAL_PATTERNS = [
    re.compile(r'(?:api[_-]?key|apikey)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:token|auth[_-]?token|access[_-]?token)\s*[:=]\s*["\']?([A-Za-z0-9_\-\.]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:password|passwd|pass|secret)\s*[:=]\s*["\']?([^\s"\']{4,})["\']?', re.IGNORECASE),
    re.compile(r'(?:ha[_-]?token|home[_-]?assistant[_-]?token)\s*[:=]\s*["\']?([A-Za-z0-9_\-\.]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:abs[_-]?api[_-]?key|audiobookshelf[_-]?key)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:github[_-]?token|gitlab[_-]?token|git[_-]?token)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:nextcloud[_-]?pass|nc[_-]?pass)\s*[:=]\s*["\']?([^\s"\']{4,})["\']?', re.IGNORECASE),
    re.compile(r'(?<!\w)token=([A-Za-z0-9_\-\.]{8,})', re.IGNORECASE),
]

CREDENTIAL_KEYS = {
    "api_key", "apikey", "token", "auth_token", "access_token",
    "password", "passwd", "pass", "secret",
    "ha_token", "home_assistant_token",
    "abs_api_key", "audiobookshelf_key",
    "github_token", "gitlab_token", "git_token",
    "nextcloud_pass", "nc_pass",
    "abs_url", "audiobookshelf_url",
}

THINKING_PATTERNS = [
    re.compile(r'<think>.*?</think>', re.DOTALL),
    re.compile(r'<think>.*?</think>', re.DOTALL),
    re.compile(r'<thinking>.*?</thinking>', re.DOTALL),
]


def strip_thinking_blocks(text: str) -> str:
    """Remove thinking/reasoning blocks from LLM output."""
    result = text
    for pattern in THINKING_PATTERNS:
        result = pattern.sub('', result)
    return result.strip()


def sanitize_for_llm(obj: Any, depth: int = 0) -> Any:
    """Recursively sanitize any object to remove credentials before feeding to LLM."""
    if depth > 10:
        return "[REDACTED]"
    if isinstance(obj, str):
        result = obj
        for pattern in CREDENTIAL_PATTERNS:
            result = pattern.sub(lambda m: m.group(0).split(m.group(1))[0] + "[REDACTED]", result)
        return result
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if k.lower() in CREDENTIAL_KEYS:
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_for_llm(v, depth + 1)
        return sanitized
    if isinstance(obj, list):
        return [sanitize_for_llm(item, depth + 1) for item in obj]
    return obj

# --- HARDENED OLLAMA PROVIDER ---
class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def generate(
        self,
        model: str,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> str:
        opts = options or {}
        show_thinking = opts.get("show_thinking", False)
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,  # Hardened: Always stream
            "options": opts
        }

        full_content = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            log.info(f"[OllamaProvider-Hardened] Calling {self.base_url}/api/chat for model {model}")
            if not chunk_callback:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                raw_text = resp.text.strip()
                if not raw_text:
                    return ""
                
                if "\n" in raw_text:
                    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                    content = ""
                    for line in lines:
                        try:
                            data = json.loads(line)
                            if "error" in data:
                                raise RuntimeError(f"Provider error: {data['error']}")
                            msg = data.get("message", {})
                            chunk = msg.get("content") or ""
                            content += chunk
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
                    # Only fall back to thinking if content is completely empty
                    if not content.strip():
                        for line in lines:
                            try:
                                data = json.loads(line)
                                msg = data.get("message", {})
                                thinking = msg.get("thinking") or ""
                                content += thinking
                            except json.JSONDecodeError:
                                continue
                    # Strip thinking blocks unless explicitly requested
                    if not show_thinking:
                        content = strip_thinking_blocks(content)
                    return content
                
                try:
                    data = json.loads(raw_text)
                    if "error" in data:
                        raise RuntimeError(f"Provider error: {data['error']}")
                    msg = data.get("message", {})
                    content = msg.get("content") or ""
                    # Only fall back to thinking if content is completely empty
                    if not content.strip():
                        content = msg.get("thinking") or ""
                    # Strip thinking blocks unless explicitly requested
                    if not show_thinking:
                        content = strip_thinking_blocks(content)
                    return content

                except json.JSONDecodeError as e:
                    log.error(f"[OllamaProvider-Hardened] Failed to parse JSON: {raw_text[:100]}... Error: {e}")
                    return ""

            # Streaming path (used by AgentLoop)
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    clean_line = line.strip()
                    if not clean_line:
                        continue
                    try:
                        chunk_json = json.loads(clean_line)
                        if "error" in chunk_json:
                            raise RuntimeError(f"Provider error: {chunk_json['error']}")
                        content = chunk_json.get("message", {}).get("content") or ""
                        # Only include thinking if explicitly requested
                        if not content and show_thinking:
                            content = chunk_json.get("message", {}).get("thinking") or ""
                        if content:
                            full_content += content
                            await chunk_callback(content)
                        if chunk_json.get("done"):
                            break

                    except RuntimeError:
                        raise  # Let provider errors propagate to AgentLoop retry logic
                    except Exception as e:
                        log.error(f"Error parsing streaming chunk: {e} | Raw line: {line!r}")
        # Strip thinking blocks from final content unless explicitly requested
        if not show_thinking:
            full_content = strip_thinking_blocks(full_content)
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
    "storagefilewriterequest", "storagelistrequest", "workspacebootstraprequest", 
    "systemlearningrequest", "discoverysyncrequest", "storageindexrequest",
    "dockercomposerequest", "identityrequest", "identitymanagerequest", "controlplanerequest", "restart_service",
    "audiobookshelfrequest", "llminforequest",
    # Aliases and Hallucination-prefixed tools
    "git_status", "git_diff", "git_log", "git_add", "git_commit", "git_push", "git_pull", "git_sync",
    "workspace_file_read", "workspace_file_write", "workspace_file_patch",
    "status", "diff", "add", "commit", "push", "pull", "log"
}

def extract_action_json(text: str) -> dict | None:
    """Extracts the first JSON object found in the text, with MoE-safe fallback and log-stripping."""
    if not text:
        return None
    
    text = re.sub(r"^INFO:.*?\n", "", text, flags=re.MULTILINE)
    
    # Priority 1: Properly fenced JSON block (allow optional closing fence)
    match = re.search(r"```json\s*(\{.*?\})(?:\s*```|$)", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Priority 2: Outer-most braces (robust fallback)
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace+1]
        try:
            return json.loads(candidate)
        except Exception:
            try:
                cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
                return json.loads(cleaned)
            except Exception:
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
                fetched = {item["key"]: item["value"] for item in resp.json()}
                for k, v in list(fetched.items()):
                    if v in ["auto", ""]:
                        fetched[k] = None
                return fetched
    except Exception as e:
        log.error(f"[AgentLoop] Failed to fetch dynamic LLM settings: {e}")
    return {}

async def get_vram_safe_params(model: str, settings: dict) -> dict:
    """Dynamically checks VRAM pressure using DB constraints."""
    local_url = settings.get("llm_local_url", OLLAMA_URL)
    max_ctx = int(settings.get("llm_local_max_ctx", "4096"))
    params = {
        "num_predict": 1024,  # Allow sufficient tokens for full JSON tool calls
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
        "thinking": False,  # Disable thinking blocks to get content faster
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
                        # 35B models need at least 4k context to handle complex system prompts + tool history
                        safe_ctx = max(4096, max_ctx // 2)
                        params["num_ctx"] = safe_ctx
                        log.info(f"[AgentLoop] VRAM PRESSURE. Scaling context down to {safe_ctx}.")
                    else:
                        # If only one model is loaded, give it the full requested context
                        params["num_ctx"] = max_ctx
                except json.JSONDecodeError:
                    log.warning(f"[AgentLoop] Failed to parse VRAM status (api/ps) from {local_url}")
    except Exception:
        pass
    return params


async def get_provider(settings: dict) -> BaseLLMProvider:
    """Instantiates the correct provider based on settings."""
    from gateway.config import OLLAMA_TIMEOUT
    active_provider = settings.get("active_llm_provider", "ollama")
    timeout = float(settings.get("ollama_timeout", str(OLLAMA_TIMEOUT)))
    if active_provider == "openrouter":
        return OpenRouterProvider(
            api_key=settings.get("llm_cloud_api_key", ""),
            base_url=settings.get("llm_cloud_url", "https://openrouter.ai/api/v1/chat/completions"),
            timeout=timeout
        )
    else:
        # Both ollama and llama_server use the same /api/chat compatible endpoint
        return OllamaProvider(
            base_url=settings.get("llm_local_url", OLLAMA_URL),
            timeout=timeout
        )

async def execute_inference(provider: BaseLLMProvider, model: str, messages: list, options: dict, chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> dict:
    """Delegates inference to the specified provider."""
    content = await provider.generate(model, messages, options=options, chunk_callback=chunk_callback)
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


_stream_redis = None

async def AgentLoop(query: str, selected_model: str, full_system: str, short_term: list, rag_user: str, creds: ResolvedCredentials, mission_id: Optional[int] = None, rag_context: str = "", show_thinking: bool = False) -> Any:
    full_audit_log = []
    
    async def stream_event(event_type: str, data: str):
        if not mission_id:
            return
        import time
        msg_obj = {"type": event_type, "data": data, "timestamp": time.time()}
        full_audit_log.append(msg_obj)
        global _stream_redis
        if not _stream_redis:
            _stream_redis = redis.from_url(REDIS_URL, decode_responses=True)
        try:
            msg_str = json.dumps(msg_obj)
            await _stream_redis.rpush(f"raven:mission:history:{mission_id}", msg_str)
            await _stream_redis.expire(f"raven:mission:history:{mission_id}", 86400)
            await _stream_redis.publish(f"raven:mission:stream:{mission_id}", msg_str)
        except Exception as e:
            log.warning(f"Failed to stream event: {e}")

    # 1. Fetch dynamic settings and resolve active provider/model
    settings = await get_dynamic_llm_settings()
    provider = await get_provider(settings)
    active_provider_name = settings.get("active_llm_provider", "ollama")
    
    # Log relevant model settings for debugging
    log.info(f"[AgentLoop] Settings: active_provider={active_provider_name}, "
             f"ollama_assistant_model={settings.get('ollama_assistant_model')}, "
             f"ollama_coding_model={settings.get('ollama_coding_model')}, "
             f"assistant_model={settings.get('assistant_model')}, "
             f"coding_model={settings.get('coding_model')}")
 
    # 2. Resolve Role-Based Model (Coder/Assistant) if selected_model is generic or "auto"
    original_model = selected_model
    if selected_model in ["auto", "assistant", "coder"]:
        tech_keywords = ["coder", "fix", "repair", "audit", "mission", "raven", "development", "git", "workspace"]
        is_technical = any(word in query.lower() for word in tech_keywords) or "coder" in selected_model
        
        if active_provider_name == "openrouter":
            if is_technical:
                selected_model = settings.get("cloud_coding_model")
            else:
                selected_model = settings.get("cloud_assistant_model")
        else:
            # Both ollama and llama_server use the same model settings
            if is_technical:
                selected_model = settings.get("ollama_coding_model") or settings.get("coding_model")
            else:
                selected_model = settings.get("ollama_assistant_model") or settings.get("assistant_model")
        log.info(f"[AgentLoop] Model resolved from '{original_model}' to '{selected_model}' (is_technical={is_technical})")
    else:
        log.info(f"[AgentLoop] Using explicit model: '{selected_model}' (not auto/assistant/coder)")
                
    # Fail fast if config is missing or invalid
    if not selected_model or selected_model == "auto":
        error_msg = f"No valid model configured for {active_provider_name}. Please configure it in the UI."
        await stream_event("result_error", error_msg)
        return error_msg

    log.info(f"[AgentLoop] Active Provider: {active_provider_name} | Model: {selected_model}")

    # 3. Detect model capabilities (thinking/reasoning support) and configure accordingly
    model_name_lower = selected_model.lower()
    is_thinking_capable = any(kw in model_name_lower for kw in ["qwen3", "qwen2.5", "deepseek-r1", "qwq"])
    
    # 4. Enhance system prompt with current date/time and RAG context
    now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p %Z")
    enhanced_system = f"{full_system}\n\nCurrent Date/Time: {now}\n\nRetrieved Context:\n{rag_context}"
    
    ollama_payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": enhanced_system}
        ] + short_term + [{"role": "user", "content": query}],
        "stream": False,
        "options": {
            "enable_thinking": is_thinking_capable and show_thinking,
            "include_reasoning": is_thinking_capable and show_thinking,
            "show_thinking": show_thinking,
            "temperature": 0.0
        },
    }


    MAX_TOOL_ITERATIONS = 30
    loop_start = asyncio.get_event_loop().time()
    exec_data = None
    ans = ""
    successful_tool_calls = 0

    # --- VRAM-SAFE SCRATCHPAD ---
    action_log = []

    # --- CHECKPOINT/RESUME ---
    start_iteration = 0
    if mission_id:
        try:
            r_cp = redis.from_url(REDIS_URL, decode_responses=True)
            cp_raw = await r_cp.get(f"raven:checkpoint:{mission_id}")
            if cp_raw:
                cp = json.loads(cp_raw)
                start_iteration = cp.get("iteration", 0)
                action_log = cp.get("action_log", [])
                exec_data = cp.get("last_exec_data")
                successful_tool_calls = cp.get("successful_tool_calls", 0)
                log.info(f"[AgentLoop] Resuming mission {mission_id} from iteration {start_iteration} (restored {len(action_log)} action log entries)")
            await r_cp.close()
        except Exception as e:
            log.warning(f"[AgentLoop] Failed to load checkpoint for mission {mission_id}: {e}")

    async def _compress_context() -> tuple[str, str]:
        """Compress action_log into a summary + recent entries to prevent context bloat.
        
        When action_log exceeds COMPRESS_THRESHOLD chars, older entries are summarized
        into a compact header. Only the most recent entries are kept in full.
        
        Returns: (compressed_summary, recent_entries_joined)
        """
        COMPRESS_THRESHOLD = 3000  # chars before triggering compression
        KEEP_RECENT = 6  # number of recent entries to keep in full
        
        full_log = "\n".join(action_log)
        if len(full_log) <= COMPRESS_THRESHOLD:
            return "", full_log
        
        # Summarize older entries
        older = action_log[:-KEEP_RECENT]
        recent = action_log[-KEEP_RECENT:]
        
        # Build compact summary of older entries
        actions_seen = {}
        for entry in older:
            # Extract action name from "Step N: action_name -> result" or "ITERATION N: ..."
            if ": " in entry:
                action_part = entry.split(": ", 1)[1].split(" ->")[0].strip()
                actions_seen[action_part] = actions_seen.get(action_part, 0) + 1
        
        summary_parts = [f"Earlier steps ({len(older)} iterations completed):"]
        for action, count in sorted(actions_seen.items(), key=lambda x: -x[1]):
            summary_parts.append(f"  - {action}: {count}x")
        
        summary = "\n".join(summary_parts)
        recent_joined = "\n".join(recent)
        
        log.info(f"[AgentLoop] Context compressed: {len(older)} older entries summarized, {len(recent)} recent kept")
        return summary, recent_joined

    async def _save_checkpoint(iter_num: int) -> None:
        if not mission_id:
            return
        try:
            r_cp = redis.from_url(REDIS_URL, decode_responses=True)
            cp_data = {
                "iteration": iter_num,
                "action_log": action_log[-20:],
                "last_exec_data": exec_data,
                "successful_tool_calls": successful_tool_calls,
                "updated_at": asyncio.get_event_loop().time(),
            }
            await r_cp.setex(
                f"raven:checkpoint:{mission_id}",
                RAVEN_MAX_TOTAL_SECONDS + 60,
                json.dumps(cp_data),
            )
            await r_cp.close()
        except Exception as e:
            log.warning(f"[AgentLoop] Failed to save checkpoint at iter {iter_num}: {e}")

    async def _clear_checkpoint() -> None:
        if not mission_id:
            return
        try:
            r_cp = redis.from_url(REDIS_URL, decode_responses=True)
            await r_cp.delete(f"raven:checkpoint:{mission_id}")
            await r_cp.close()
        except Exception as e:
            log.warning(f"[AgentLoop] Failed to clear checkpoint for mission {mission_id}: {e}")

    for agent_iter in range(start_iteration, MAX_TOOL_ITERATIONS):
        iter_num = agent_iter + 1
        iter_start = asyncio.get_event_loop().time()
        
        # --- HARD TIMEOUT CHECK ---
        elapsed_total = iter_start - loop_start
        if elapsed_total > RAVEN_MAX_TOTAL_SECONDS:
            log.error(f"[AgentLoop] HARD TIMEOUT after {elapsed_total:.0f}s at iteration {iter_num}")
            ans = f"ERROR: Raven job exceeded time limit of {RAVEN_MAX_TOTAL_SECONDS}s. Partial result: {ans or 'No output yet'}"
            await _clear_checkpoint()
            break
        
        # --- HARD KILL SWITCH (Redis polling) ---
        if mission_id:
            try:
                # Resolve redis instance for kill-switch check
                r_kill = redis.from_url(REDIS_URL, decode_responses=True)
                kill_flag = await r_kill.get(f"raven:mission:kill:{mission_id}")
                if kill_flag:
                    log.warning(f"[AgentLoop] MISSION KILL SIGNAL RECEIVED for {mission_id}. Terminating.")
                    await stream_event("system", "Mission terminated by user.")
                    await _clear_checkpoint()
                    return "MISSION TERMINATED: User requested cancellation via control plane."
            except Exception as e:
                log.error(f"[AgentLoop] Error checking mission kill flag: {e}")
        
        await stream_event("system", f"Agent loop iteration {iter_num}/{MAX_TOOL_ITERATIONS} started.")
        log.info(f"[AgentLoop] Iteration {iter_num}/{MAX_TOOL_ITERATIONS} | total elapsed {elapsed_total:.0f}s")

        heartbeat_stop = asyncio.Event()

        async def _heartbeat(iter_n: int, t0: float) -> None:
            while not heartbeat_stop.is_set():
                await asyncio.sleep(RAVEN_HEARTBEAT_INTERVAL)
                if heartbeat_stop.is_set():
                    break
                elapsed = asyncio.get_event_loop().time() - t0
                if elapsed > RAVEN_HUNG_THRESHOLD:
                    log.warning(f"[AgentLoop] ⚠ HUNG WARNING — iter {iter_n} waiting {elapsed:.0f}s for Ollama")
                else:
                    log.info(f"[AgentLoop] ♥ heartbeat — iter {iter_n} | waiting for Ollama {elapsed:.0f}s")

        hb_task = asyncio.create_task(_heartbeat(agent_iter + 1, iter_start))

        try:
            # Compress context to prevent token bloat and llama.cpp cache thrashing
            ctx_summary, ctx_recent = await _compress_context()
            
            mission_header = f"MISSION LOCK: {query}"
            if ctx_summary:
                mission_header += f"\n\n{ctx_summary}"
            
            ollama_payload["messages"] = [
                {"role": "system", "content": enhanced_system},
                {"role": "user", "content": f"{mission_header}\n\nRECENT ACTIONS:\n{ctx_recent}"}
            ]
            if exec_data:
                # Truncate detail, stdout, and stderr to save VRAM if they are massive
                safe_exec_data = exec_data.copy() if isinstance(exec_data, dict) else {"result": str(exec_data)}
                if "detail" in safe_exec_data and isinstance(safe_exec_data["detail"], dict):
                    for key in ["content", "stdout", "stderr"]:
                        if key in safe_exec_data["detail"] and safe_exec_data["detail"][key] and len(str(safe_exec_data["detail"][key])) > 500:
                            safe_exec_data["detail"][key] = str(safe_exec_data["detail"][key])[:500] + "\n...[TRUNCATED]..."
                
                # Sanitize credentials from execution results before feeding to LLM
                safe_exec_data = sanitize_for_llm(safe_exec_data)
                
                # Hard limit on total exec_data size
                exec_json = json.dumps(safe_exec_data)
                if len(exec_json) > 2000:
                    exec_json = exec_json[:2000] + "\n...[TRUNCATED FOR CONTEXT WINDOW]..."
                        
                ollama_payload["messages"].append({
                    "role": "user", 
                    "content": f"LAST TOOL RESULT:\n{exec_json}"
                })
            ollama_payload["messages"].append({"role": "user", "content": "Execute the next step immediately using a JSON tool call block."})

            # Hard limit on total message size to prevent llama.cpp cache thrashing
            total_chars = sum(len(m.get("content", "")) for m in ollama_payload["messages"])
            if total_chars > 12000:
                log.warning(f"[AgentLoop] Context too large ({total_chars} chars), forcing compression")
                # Keep only system + mission header + last 3 recent entries
                ollama_payload["messages"] = [
                    ollama_payload["messages"][0],  # system
                    ollama_payload["messages"][1],  # mission header
                    {"role": "user", "content": "Continue with the next step."}
                ]

            # --- RETRY LOGIC ---
            MAX_INFERENCE_RETRIES = 3
            
            for retry_count in range(MAX_INFERENCE_RETRIES):
                try:
                    # Stick to the requested model for all attempts
                    model_to_use = selected_model
                    dynamic_settings = await get_dynamic_llm_settings()
                    vram_params = await get_vram_safe_params(model_to_use, dynamic_settings)
                    ollama_payload["options"] = vram_params
                    log.info(f"[AgentLoop] Inference options: {vram_params}")
                    log.info(f"[AgentLoop] Executing inference (Attempt {retry_count + 1}/{MAX_INFERENCE_RETRIES}) for {model_to_use}")
                    
                    async def chunk_logger(chunk: str):
                        await stream_event("reasoning", chunk)

                    data = await execute_inference(
                        provider, 
                        selected_model, 
                        ollama_payload["messages"], 
                        ollama_payload.get("options", {}),
                        chunk_callback=chunk_logger
                    )
                    
                    # Handle thinking-capable models: some models put their entire response
                    # in the thinking/reasoning block when content is empty.
                    msg = data.get("message", {})
                    ans = msg.get("content", "") or msg.get("thinking", "") or msg.get("reasoning", "") or "Error."
                    if not ans or not ans.strip():
                        log.warning(f"[AgentLoop] Empty output from model on attempt {retry_count + 1}; treating as failure.")
                        raise Exception("Empty model output")
                    # inference_success = True
                    break  # Success!
                except Exception as e:
                    log.warning(f"[AgentLoop] Inference attempt {retry_count + 1} failed: {e}")
                    if retry_count < MAX_INFERENCE_RETRIES - 1:
                        wait_time = 5 * (retry_count + 1)
                        log.info(f"[AgentLoop] Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        raise e  # Re-raise on final failure

            heartbeat_stop.set()
            await hb_task
            log.info(f"[AgentLoop] Inference completed in {(asyncio.get_event_loop().time() - iter_start)*1000:.0f}ms — iter {agent_iter + 1}")
            log.info(f"[AgentLoop] Response content: {ans[:200]}...")
        except Exception as e:
            heartbeat_stop.set()
            await hb_task
            log.error(f"[AgentLoop] FATAL: All inference retries failed on iter {agent_iter + 1}: {e}")
            await _clear_checkpoint()
            return f"SYSTEM ERROR: Inference failed after multiple retries. Detail: {e}. Please check the LLM provider status."

        tool_data = extract_action_json(ans)
        
        # Normalize alternative schemas: { "name": "...", "parameters": {...} } → { "action": "...", "payload": {...} }
        if tool_data:
            # Handle "tool", "operation", or "command" keys used as "action"
            if "tool" in tool_data and "action" not in tool_data:
                tool_data["action"] = tool_data.pop("tool")
            if "operation" in tool_data and "action" not in tool_data:
                tool_data["action"] = tool_data.pop("operation")
            if "command" in tool_data and "action" not in tool_data:
                tool_data["action"] = tool_data.pop("command")
            
            # Handle standard "name" (OpenAI/Ollama format)
            if "name" in tool_data and "action" not in tool_data:
                tool_data["action"] = tool_data.pop("name")
            
            # Handle "arguments" or "parameters" keys used as "payload"
            if "arguments" in tool_data and "payload" not in tool_data:
                tool_data["payload"] = tool_data.pop("arguments")
            if "parameters" in tool_data and "payload" not in tool_data:
                tool_data["payload"] = tool_data.pop("parameters")
            
            # Handle "function" nesting (Legacy OpenAI format)
            if "function" in tool_data and "action" not in tool_data:
                tool_data["action"] = tool_data["function"].get("name", "")
                tool_data["payload"] = tool_data["function"].get("arguments", {})

            log.info(f"[AgentLoop] Normalized Tool Data: {tool_data}")
        
        # Validation: If we don't have a valid action at this point, we MUST re-prompt.
        if not tool_data or not tool_data.get("action"):
            # Blank/whitespace-only response is a failure — re-prompt
            if not ans or not ans.strip():
                log.warning(f"[AgentLoop] Empty response on iter {iter_num}; re-prompting...")
                action_log.append(f"ITERATION {iter_num}: Model returned empty response — forcing correction.")
                continue

            # If tool_data exists but has no action, it might be a malformed JSON that parsed but is missing keys
            if tool_data and not tool_data.get("action"):
                log.warning(f"[AgentLoop] Tool data missing 'action' key: {tool_data}")
                action_log.append(f"ITERATION {iter_num}: Your JSON is missing the 'action' key. Use one of the provided tool names.")
                continue

            # If it's just yapping without a JSON block
            log.warning(f"[AgentLoop] No valid tool call found in textual response (iter {iter_num})")
            
            if agent_iter > 0 and successful_tool_calls > 0:
                log.info(f"[AgentLoop] Agent provided textual answer after {successful_tool_calls} successful tool call(s). Terminating loop.")
                break
             
            if agent_iter >= 3:
                log.error(f"[AgentLoop] No valid tool calls after {agent_iter + 1} iterations. Terminating to prevent runaway.")
                ans = "ERROR: Agent failed to produce valid tool calls after multiple attempts. Last response: " + (ans[:200] if ans else "empty")
                await _clear_checkpoint()
                break
                
            log.info(f"[AgentLoop] Re-prompting for autonomous tool execution (iter {agent_iter + 1})...")
            action_log.append(f"ITERATION {iter_num}: Your response did not contain a valid JSON tool call. Every mission step MUST be a tool call.")
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

        await stream_event("action", f"Executing Tool: {action_name}")
        log.info(f"[AgentLoop] Dispatching action: {action_name}")

        try:
            # Normalize action name for better matching
            action = str(tool_data.get("action") or tool_data.get("operation") or "").lower()
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
                "git_status": "GitOperationRequest",
                "gitdiff": "GitOperationRequest",
                "git_diff": "GitOperationRequest",
                "gitlog": "GitOperationRequest",
                "git_log": "GitOperationRequest",
                "gitpull": "GitOperationRequest",
                "git_pull": "GitOperationRequest",
                "git_add": "GitOperationRequest",
                "git_commit": "GitOperationRequest",
                "git_push": "GitOperationRequest",
                "git_sync": "GitOperationRequest",
                "status": "GitOperationRequest",
                "add": "GitOperationRequest",
                "commit": "GitOperationRequest",
                "push": "GitOperationRequest",
                "pull": "GitOperationRequest",
                "diff": "GitOperationRequest",
                "log": "GitOperationRequest",
                "gitoperationrequest": "GitOperationRequest",
                "edit_file": "WorkspaceFilePatchRequest",
                "file_patch": "WorkspaceFilePatchRequest",
                "apply_patches": "WorkspaceFilePatchRequest",
                "workspace_file_read": "WorkspaceFileReadRequest",
                "workspace_file_write": "WorkspaceFileWriteRequest",
                "workspace_file_patch": "WorkspaceFilePatchRequest"
            }
            
            if action in action_map_aliases:
                action = action_map_aliases[action].lower()

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
                "identityrequest": (EXECUTION_SVC, "/execute/identity"),
                "identitymanagerequest": (EXECUTION_SVC, "/execute/identity/manage"),
                "audiobookshelfrequest": (EXECUTION_SVC, "/execute/audiobookshelf"),
                "llminforequest": (EXECUTION_SVC, "/execute/llm/info"),
                "contextsearchrequest": (RAG_SVC, "/rag/search"),
                "haconfigrequest": (EXECUTION_SVC, "/execute/ha_config"),
            }

            lookup_action = action.lower().strip() if action else ""

            if lookup_action in action_map:
                svc_base, endpoint = action_map[lookup_action]
                
                # RECOVERY: If the LLM sent a nested payload for a GitOperationRequest but forgot the inner 'action',
                # we inject it here using the original action name (e.g. 'status', 'add', etc.)
                if lookup_action == "gitoperationrequest" and isinstance(payload, dict) and "action" not in payload:
                    # 'action' variable at this point is likely "GitOperationRequest" (mapped)
                    # We want the original one from the tool call
                    orig_action = str(tool_data.get("action") or tool_data.get("operation") or "").lower()
                    if orig_action.startswith("git_"):
                        orig_action = orig_action.replace("git_", "")
                    payload["action"] = orig_action

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
                    "github_token": creds.github_token,
                    "gitlab_token": creds.gitlab_token,
                    "git_token": creds.git_token,
                }

                async with httpx.AsyncClient(timeout=120.0) as client:
                    # Secure Log Redaction
                    log_payload = json.loads(json.dumps(payload)) # Deep copy
                    def redact(d):
                        if isinstance(d, dict):
                            for k, v in d.items():
                                if k in ["github_token", "gitlab_token", "git_token", "api_key", "ha_token", "nextcloud_pass"]:
                                    d[k] = "[REDACTED]"
                                else:
                                    redact(v)
                        elif isinstance(d, list):
                            for item in d:
                                redact(item)
                    redact(log_payload)
                    
                    await stream_event("action_payload", json.dumps(log_payload, indent=2))
                    log.info(f"[AgentLoop] Sending payload to {endpoint}: {json.dumps(log_payload)}")
                    resp = await client.post(f"{svc_base}{endpoint}", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET})
                    log.info(f"[AgentLoop] Tool response: {resp.status_code}")
                    
                    if resp.status_code == 422:
                        try:
                            error_detail = resp.json().get("detail", "Validation failed")
                            msg = f"SCHEMA ERROR (422): {error_detail}. Ensure you are using the correct field names (e.g. 'action', 'message') instead of 'command' or 'commit_message'."
                        except Exception:
                            msg = f"SCHEMA ERROR (422): {resp.text}. Check your field names."
                        exec_data = {"status": "ERROR", "message": msg}
                    else:
                        exec_data = resp.json()

                    short_msg = exec_data.get("message", "Success")
                    await stream_event("result_success", short_msg)
                    action_log.append(f"Step {iter_num}: {action} -> {short_msg}")
                    # Track successful tool executions (non-ERROR responses)
                    if isinstance(exec_data, dict) and exec_data.get("status") != "ERROR":
                        successful_tool_calls += 1

                    # Checkpoint state after successful tool execution
                    await _save_checkpoint(iter_num)
            else:
                log.warning(f"[AgentLoop] Unknown action: {action}")
                exec_data = {"status": "ERROR", "message": f"Unknown action: {action}"}

        except Exception as e:
            await stream_event("result_error", str(e))
            log.error(f"[AgentLoop] Tool execution failed: {e}")
            exec_data = {"status": "ERROR", "message": str(e)}

    async def _persist_learning(summary: str) -> None:
        try:
            tags = ["raven", "autonomous", "repair"]
            if "workspace" in query.lower():
                tags.append("workspace")
            if "git" in query.lower():
                tags.append("git")
            if "deployment" in query.lower() or "restart" in query.lower():
                tags.append("deployment")

            payload = {
                "user_context": creds.model_dump(),
                "topic": f"Raven repair: {query[:80]}",
                "content": summary,
                "tags": list(dict.fromkeys(tags)),
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{EXECUTION_SVC}/execute/learning",
                    json=payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                )
                if resp.status_code != 200:
                    log.warning(f"[AgentLoop] Learning persistence failed: {resp.status_code} {resp.text}")
        except Exception as e:
            log.warning(f"[AgentLoop] Learning persistence skipped: {e}")

    # --- SUMMARIZATION PHASE ---
    # Trigger if we have successful tool calls OR if the model output is purely JSON/messy
    is_messy = "was was was" in ans or "```json" in ans or (ans.strip().startswith("{") and ans.strip().endswith("}"))
    if successful_tool_calls > 0 or is_messy:
        # If the last response still looks like a tool call or is very short/messy, force a clean summary
        if extract_action_json(ans) or len(ans.strip()) < 30 or is_messy:
            log.info("[AgentLoop] Finalizing with clean summarization phase...")
            summary_prompt = [
                {"role": "system", "content": "You are Raven. Summarize the mission result for the user in clean, natural language. Do NOT use JSON. Do NOT repeat yourself. Be concise."},
                {"role": "user", "content": f"Mission: {query}\n\nActions taken:\n" + "\n".join(action_log) + f"\n\nRaw output: {ans}\n\nPlease provide the final clean summary now:"}
            ]
            try:
                data = await execute_inference(provider, selected_model, summary_prompt, {"temperature": 0.0})
                ans = data.get("message", {}).get("content", ans)
            except Exception as e:
                log.warning(f"[AgentLoop] Summarization phase failed: {e}")
                if ans.strip().startswith("{") and ans.strip().endswith("}"):
                    try:
                        parsed = json.loads(ans)
                        for key in ["response", "answer", "message", "text"]:
                            if key in parsed and isinstance(parsed[key], str):
                                ans = parsed[key]
                                break
                    except (json.JSONDecodeError, ValueError):
                        pass

    if action_log and not (isinstance(exec_data, dict) and exec_data.get("status") == "ERROR"):
        learning_summary = "\n".join([
            f"Query: {query}",
            f"Actions: {' | '.join(action_log)}",
            f"Final answer: {ans}",
        ])
        await _persist_learning(learning_summary)

    if mission_id and full_audit_log:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.patch(
                    f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                    json={"output_log": json.dumps(full_audit_log)},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
        except Exception as e:
            log.warning(f"[AgentLoop] Failed to persist output_log for mission {mission_id}: {e}")

    # Clear checkpoint on successful completion
    await _clear_checkpoint()

    return ans
