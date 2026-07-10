import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

import aiohttp
import redis.asyncio as redis

from services.gateway.config import (
    CONTROL_PLANE_URL,
    EXECUTION_SVC,
    IDENTITY_SVC,
    INTERNAL_SECRET,
    RAG_SVC,
    RAVEN_HEARTBEAT_INTERVAL,
    RAVEN_HUNG_THRESHOLD,
    RAVEN_MAX_TOTAL_SECONDS,
    REDIS_URL,
    STORAGE_SVC,
    WORKSPACE_RUNTIME_SVC,
)
from services.gateway.llm_providers import BaseLLMProvider, OpenRouterProvider
from services.gateway.prompts import PROMPT_RAVEN_PLAN, PROMPT_RAVEN_REFLECTION, load_prompt
from services.gateway.schemas import ResolvedCredentials

CREDENTIAL_PATTERNS = [
    re.compile(r'(?:api[_-]?key|apikey)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:token|auth[_-]?token|access[_-]?token)\s*[:=]\s*["\']?([A-Za-z0-9_\-\.]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:password|passwd|pass|secret)\s*[:=]\s*["\']?([^\s"\']{4,})["\']?', re.IGNORECASE),
    re.compile(r'(?:ha[_-]?token|home[_-]?assistant[_-]?token)\s*[:=]\s*["\']?([A-Za-z0-9_\-\.]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:abs[_-]?api[_-]?key|audiobookshelf[_-]?key)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:github[_-]?token|gitlab[_-]?token|git[_-]?token)\s*[:=]\s*["\']?([A-Za-z0-9_\-]{8,})["\']?', re.IGNORECASE),
    re.compile(r'(?:nextcloud[_-]?pass|nc[_-]?pass)\s*[:=]\s*["\']?([^\s"\']{4,})["\']?', re.IGNORECASE),
    re.compile(r'(?<!\w)token=([A-Za-z0-9_\-\.]{8,})', re.IGNORECASE),
    re.compile(r'Bearer\s+([A-Za-z0-9._\-]+)', re.IGNORECASE),
    re.compile(r'(ghp_[A-Za-z0-9]+)'),
    re.compile(r'(glpat-[A-Za-z0-9\-_]+)'),
    re.compile(r'(github_pat_[A-Za-z0-9_]+)'),
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
            def _redact(m):
                try:
                    secret = m.group(1)
                    return m.group(0).replace(secret, "[REDACTED]")
                except IndexError:
                    return "[REDACTED]"
            result = pattern.sub(_redact, result)
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
    def __init__(self, base_url: str, timeout: float | aiohttp.ClientTimeout = 600.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if isinstance(timeout, aiohttp.ClientTimeout) else aiohttp.ClientTimeout(total=timeout)

    async def generate(
        self,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        chunk_callback: Callable[[str], Awaitable[None]] | None = None
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
        async with aiohttp.ClientSession(headers={"X-Request-Source": "shared-llm/app"}, timeout=self.timeout) as client:
            log.info(f"[OllamaProvider-Hardened] Calling {self.base_url}/api/chat for model {model}")
            if not chunk_callback:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                raw_text = (await resp.text()).strip()
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
            async with client.post(f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for raw_line in response.content:
                    clean_line = raw_line.decode("utf-8", errors="replace").strip()
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
                        log.error(f"Error parsing streaming chunk: {e} | Raw line: {clean_line!r}")
        # Strip thinking blocks from final content unless explicitly requested
        if not show_thinking:
            full_content = strip_thinking_blocks(full_content)
        return full_content

log = logging.getLogger("gateway.agent_loop")

ALLOWED_TOOLS = {
    "lightcontrolrequest", "mediaplayrequest", "mediatransportrequest", "mediastatusrequest",
    "tvcastrequest", "videoplayrequest", "climaterequest", "securityrequest",
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
    "audiobookshelfrequest", "llminforequest", "contextsearchrequest", "haconfigrequest",
    "entitysearchrequest", "logbookrequest", "executionlogrequest",
    "documentbroadcastrequest", "nightmoderequest", "ttsrequest", "storagetexttorequest",
    "ghrequest",
    # Aliases and Hallucination-prefixed tools
    "git_status", "git_diff", "git_log", "git_add", "git_commit", "git_push", "git_pull", "git_sync",
    "workspace_file_read", "workspace_file_write", "workspace_file_patch",
    "status", "diff", "add", "commit", "push", "pull", "log",
    # Common LLM hallucination patterns (tool_name, function_name, command)
    "tool_name", "function_name", "command", "operation", "target",
    # Parameter hallucination patterns
    "parameters", "request", "input",
    # Search/query variations
    "search_query", "query_text", "response_format",
    # Additional Git operations
    "git_branch", "git_checkout", "git_reset", "git_stash", "git_blame",
    "git_show", "git_revert", "git_tag", "git_remote",
    # Workspace variations
    "workspace_list", "workspace_delete", "workspace_create",
    # Storage variations
    "storage_delete", "storage_move", "storage_rename", "storage_copy",
    # Browser variations
    "browser_screenshot", "browser_click", "browser_type", "browser_navigate",
    # Media variations
    "media_search", "media_library", "media_queue", "media_playlist",
    # Docker variations
    "docker_ps", "docker_exec", "docker_stop", "docker_start", "docker_restart", "docker_build",
    # Home Assistant variations
    "ha_call_service", "ha_get_state", "ha_set_state", "ha_get_entities",
    # Note variations
    "note_create", "note_delete", "note_list", "note_update",
    # Calendar variations
    "calendar_create", "calendar_delete", "calendar_list", "calendar_update",
    # Security variations
    "security_lock", "security_unlock", "security_arm", "security_disarm",
    # Climate variations
    "climate_set_temperature", "climate_set_mode", "climate_toggle",
    # Light variations
    "light_on", "light_off", "light_brightness", "light_color",
    # Timer variations
    "timer_start", "timer_stop", "timer_pause", "timer_resume",
    # Talk variations
    "talk_speak", "talk_stop", "talk_silence",
    # Learning variations
    "learn_add", "learn_remove", "learn_forget",
    # Intercom variations
    "intercom_start", "intercom_end", "intercom_list",
    # Telemetry variations
    "telemetry_enroll", "telemetry_unenroll", "telemetry_list", "telemetry_query",
    # Identity variations
    "identity_list", "identity_update", "identity_delete",
    # Deployment variations
    "deploy_restart", "deploy_status", "deploy_logs",
    # Diagnostics variations
    "diagnostics_check", "diagnostics_report", "diagnostics_screenshot"
}

def _extract_json_with_brace_depth(text: str, start: int = 0) -> dict | list | None:
    """Extract JSON from text using brace-depth tracking for accurate nesting."""
    if not text:
        return None
    depth = 0
    start_brace = -1
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '{' or ch == '[':
            if depth == 0:
                start_brace = i
            depth += 1
        elif ch == '}' or ch == ']':
            depth -= 1
            if depth == 0 and start_brace != -1:
                candidate = text[start_brace:i+1]
                try:
                    return json.loads(candidate)
                except Exception:
                    try:
                        cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
                        return json.loads(cleaned)
                    except Exception:
                        pass
                break
    return None


def _deep_find(node: object, key: str) -> object:
    """Recursively search a (possibly nested) dict/list for the first ``key``."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _deep_find(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _deep_find(item, key)
            if found is not None:
                return found
    return None


def _normalize_tool(obj: dict) -> dict | None:
    """Ensure a tool-call dict carries an 'action' discriminator, inferring it when absent."""
    if not isinstance(obj, dict):
        return None
    obj = dict(obj)

    # Top-level OpenAI-style tool_calls array
    if isinstance(obj.get("tool_calls"), list) and obj["tool_calls"]:
        first = obj["tool_calls"][0]
        if isinstance(first, dict):
            obj = {**first}

    # Unwrap CapabilityRequest-style wrappers {capability: X, request: {...}}
    if "capability" in obj and isinstance(obj.get("request"), dict):
        inner = dict(obj["request"])
        cap = obj.get("capability")
        if cap:
            inner.setdefault("@type", cap)
            inner.setdefault("action", cap)
        obj = inner

    # OpenAI-style nested function call (fire whenever a 'function' key is present,
    # regardless of the surrounding 'type')
    func = obj.get("function")
    if isinstance(func, dict):
        name = func.get("name") or func.get("action")
        args = func.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"command": args}
        if isinstance(args, dict):
            obj.update(args)
        if name:
            obj["@type"] = name
            obj["action"] = name
        obj.pop("function", None)
        obj.pop("type", None)
        obj.pop("id", None)

    # Hoist common nested payload keys (incl. 'params', used by some clients)
    for nest_key in ("arguments", "payload", "args", "json", "tool_call", "parameters", "request", "params"):
        if nest_key in obj and isinstance(obj[nest_key], dict):
            obj.update(obj.pop(nest_key))

    # Last-resort: pull command/commands out of any deeper nesting so the shell
    # handler never sees "Neither 'command' nor 'commands' provided".
    for key in ("command", "commands"):
        if key not in obj:
            _found = _deep_find(obj, key)
            if _found is not None:
                obj[key] = _found

    # Normalize path-like keys to file_path for write/read schemas
    if "file_path" not in obj:
        for pk in ("path", "relative_path", "filepath"):
            if pk in obj:
                obj["file_path"] = obj.pop(pk)
                break

    # Set action from any known discriminator
    if "action" not in obj:
        if "@type" in obj:
            obj["action"] = obj["@type"]
        elif "tool" in obj and isinstance(obj["tool"], str):
            obj["action"] = obj["tool"]
        elif "type" in obj and isinstance(obj["type"], str) and obj["type"] not in ("function",):
            obj["action"] = obj["type"]

    # Infer action from payload shape when no discriminator exists
    if "action" not in obj:
        if "command" in obj or "shell" in obj:
            obj["action"] = "WorkspaceShellRequest"
        elif "chunks" in obj or ("old_text" in obj and "new_text" in obj):
            obj["action"] = "WorkspaceFilePatchRequest"
        elif "content" in obj and ("path" in obj or "relative_path" in obj or "file_path" in obj):
            obj["action"] = "WorkspaceFileWriteRequest"
        elif "path" in obj or "relative_path" in obj or "file_path" in obj:
            obj["action"] = "WorkspaceFileReadRequest"
        elif "query" in obj and ("repo_url" in obj or "workspace_id" in obj):
            obj["action"] = "GitOperationRequest"

    return obj if obj.get("action") else None


def _extract_tool_candidates(text: str) -> list:
    """Collect all plausible tool-call dicts from arbitrary model output."""
    candidates: list = []

    # OpenAI-style {"tool_calls": [{"function": {"name":..., "arguments":...}}]}
    tc = re.search(r'"tool_calls"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if tc:
        block = tc.group(1)
        for fn in re.finditer(r'"function"\s*:\s*\{.*?\}', block, re.DOTALL):
            try:
                candidates.append(json.loads(fn.group(0)))
            except Exception:
                name = re.search(r'"name"\s*:\s*"([^"]+)"', fn.group(0))
                args = re.search(r'"arguments"\s*:\s*(\{.*?\}|\"[^\"]*\")', fn.group(0), re.DOTALL)
                d = {}
                if name:
                    d["function"] = {"name": name.group(1)}
                if args:
                    raw = args.group(1)
                    if raw.startswith('"'):
                        d["function"] = {**d.get("function", {}), "arguments": raw.strip('"')}
                    else:
                        with contextlib.suppress(Exception):
                            d["function"] = {**d.get("function", {}), "arguments": json.loads(raw)}
                if d:
                    candidates.append({"type": "function", **d})

    # XML-style <tool_call><function=NAME><parameter=KEY>VAL</parameter></tool_call>
    for m in re.finditer(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL):
        block = m.group(1)
        func_m = re.search(r"<function=([^>]+)>", block)
        if func_m:
            params: dict = {}
            for pm in re.finditer(r"<parameter=([^>]+)>(.*?)</parameter>", block, re.DOTALL):
                params[pm.group(1).strip()] = pm.group(2).strip()
            candidates.append({"@type": func_m.group(1).strip(), **params})

    # Any JSON object carrying a tool discriminator (@type/action/tool/type)
    for m in re.finditer(r'\{\s*"(@type|action|tool|type)"\s*:\s*"[^"]+"[^}]*\}', text, re.DOTALL):
        with contextlib.suppress(Exception):
            candidates.append(json.loads(m.group(0)))

    # Brace-depth tracking (handles bare dicts and arrays)
    result = _extract_json_with_brace_depth(text)
    if isinstance(result, dict):
        candidates.append(result)
    elif isinstance(result, list):
        candidates.extend([i for i in result if isinstance(i, dict)])

    return candidates


def extract_action_json(text: str) -> dict | None:
    """Extract and normalize the first tool call from arbitrary model output."""
    if not text:
        return None

    text = re.sub(r"^INFO:.*?\n", "", text, flags=re.MULTILINE)

    # Priority 1: Properly fenced JSON block (allow optional closing fence)
    match = re.search(r"```json\s*(\{.*?\})(?:\s*```|$)", text, re.DOTALL)
    if match:
        try:
            return _normalize_tool(json.loads(match.group(1)))
        except Exception:
            try:
                return _normalize_tool(json.loads(re.sub(r",\s*([\]}])", r"\1", match.group(1))))
            except Exception:
                pass

    # Priority 1b: Unquoted fenced block (``` without json tag)
    match = re.search(r"```\s*\n\s*(\{.*?\})", text, re.DOTALL)
    if match:
        try:
            return _normalize_tool(json.loads(match.group(1)))
        except Exception:
            try:
                return _normalize_tool(json.loads(re.sub(r",\s*([\]}])", r"\1", match.group(1))))
            except Exception:
                pass

    # Priority 2: Collect all candidates (OpenAI tool_calls, XML, discriminator dicts, brace-depth)
    for cand in _extract_tool_candidates(text):
        norm = _normalize_tool(cand)
        if norm:
            return norm

    # Priority 3: Outer-most braces (legacy fallback)
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            return _normalize_tool(json.loads(candidate))
        except Exception:
            try:
                return _normalize_tool(json.loads(re.sub(r",\s*([\]}])", r"\1", candidate)))
            except Exception:
                pass

    return None


# --- HARNESS GUARDS (patterns borrowed from OpenCode-orchestrator / Hermes) ---
# These are pure functions so the gating logic can be unit-tested without a live
# model or any downstream service.

def action_signature(action_name: str, payload: dict | None) -> str:
    """Stable signature of a dispatched tool action for repetition detection.

    Combines the canonical action name with the most identifying payload field
    (file path or shell command) so the loop can tell apart 'wrote game.py' from
    'wrote README.md' and from 'ran ruff' — which matters for stagnation checks.
    """
    action_name = (action_name or "").lower()
    if not isinstance(payload, dict):
        payload = {}
    key = (
        payload.get("file_path")
        or payload.get("path")
        or payload.get("relative_path")
        or payload.get("command")
        or payload.get("commands")
        or payload.get("query")
        or ""
    )
    if isinstance(key, (list, tuple)):
        key = " ".join(str(k) for k in key)
    return f"{action_name}::{key!r}"


def detect_repetitive_failure(recent: list[tuple[str, bool]], window: int = 3) -> bool:
    """True when the last ``window`` actions share one signature and ALL failed.

    Mirrors OpenCode-orchestrator stagnation detection: stop blind retries once
    the same step keeps failing and escalate instead of looping forever.
    """
    if len(recent) < window:
        return False
    last = recent[-window:]
    sigs = {sig for sig, _ in last}
    return len(sigs) == 1 and all(not ok for _, ok in last)


_VERIFY_COMMAND_HINTS = (
    "test", "lint", "pytest", "ruff", "mypy", "eslint", "prettier",
    "npm test", "npm run", "go test", "cargo test", "tox", "flake8", "tsc",
)


def is_verification_action(action_name: str, payload: dict | None) -> bool:
    """True when an action constitutes verification (lint/test) of produced code.

    Language-agnostic: covers the WorkspaceLint tool and any shell command that
    runs a project's linter/test runner (ruff, pytest, eslint, npm test, ...).
    """
    action_name = (action_name or "").lower()
    if action_name == "workspacelintrequest":
        return True
    if action_name == "workspaceshellrequest" and isinstance(payload, dict):
        cmd = str(payload.get("command") or payload.get("commands") or "").lower()
        return any(hint in cmd for hint in _VERIFY_COMMAND_HINTS)
    return False


def pending_verification(written: set[str], verified: set[str]) -> list[str]:
    """Files that were written but never linted/tested before finishing."""
    return sorted(set(written) - set(verified))

async def get_dynamic_llm_settings() -> dict:
    """Fetches elastic LLM routing configuration directly from the Identity DB."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status == 200:
                fetched = {item["key"]: item["value"] for item in await resp.json()}
                for k, v in list(fetched.items()):
                    if v in ["auto", ""]:
                        fetched[k] = None
                return fetched
    except Exception as e:
        log.error(f"[AgentLoop] Failed to fetch dynamic LLM settings: {e}")
    return {}

async def get_vram_safe_params(model: str, settings: dict) -> dict:
    """Dynamically checks VRAM pressure using DB constraints."""
    local_url = settings.get("llm_local_url", "")
    if not local_url:
        log.warning("[AgentLoop] llm_local_url not configured; returning default VRAM-safe params")
        return {
            "num_predict": 1024,
            "temperature": 0.1,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "thinking": False,
        }
    max_ctx = int(settings.get("llm_local_max_ctx", "4096"))
    params = {
        "num_predict": 1024,  # Allow sufficient tokens for full JSON tool calls
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
        "thinking": False,  # Disable thinking blocks to get content faster
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as client:
            resp = await client.get(f"{local_url.rstrip('/')}/api/ps")
            if resp.status == 200:
                raw_text = (await resp.text()).strip()
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
    from services.gateway.config import OLLAMA_TIMEOUT
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
        local_url = settings.get("llm_local_url", "")
        if not local_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        return OllamaProvider(
            base_url=local_url,
            timeout=timeout
        )

async def execute_inference(provider: BaseLLMProvider, model: str, messages: list, options: dict, chunk_callback: Callable[[str], Awaitable[None]] | None = None) -> dict:
    """Delegates inference to the specified provider."""
    content = await provider.generate(model, messages, options=options, chunk_callback=chunk_callback)
    return {"message": {"role": "assistant", "content": content}}

_original_async_client = aiohttp.ClientSession
_global_http_client: aiohttp.ClientSession | None = None
_global_http_client_loop: asyncio.AbstractEventLoop | None = None

def get_http_client() -> aiohttp.ClientSession:
    global _global_http_client, _global_http_client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _global_http_client is None or _global_http_client_loop != current_loop:
        _global_http_client = _original_async_client(
            timeout=aiohttp.ClientTimeout(300.0, connect=30.0),
            connector=aiohttp.TCPConnector(limit=100),
        )
        _global_http_client_loop = current_loop
    return _global_http_client


_stream_redis = None

def should_persist_learning(result: str) -> bool:
    """
    Prevent meaningless results from being added to RAG.
    We don't want the LLM to learn that reading a file or failing a tool call is success.
    """
    if not result or result.strip() in ("None", "", "null"):
        return False
    result_lower = result.lower().strip()

    # Reject if the result is empty or just whitespace after stripping
    if len(result_lower) < 3:
        return False

    # Lint results with "issues found" are meaningful — the LLM found real problems.
    # Only reject if it's a tool execution error, not a diagnostic finding.
    if "lint issues found" in result_lower or "lint passed" in result_lower:
        return True

    # Reject pure error/exception strings that indicate infrastructure failures
    # rather than actual mission output from the LLM
    failure_indicators = [
        "tool execution failed",
        "traceback (most recent call last)",
    ]
    for indicator in failure_indicators:
        if indicator in result_lower:
            return False

    # Reject messages that start with "Error:" or "Failed:" — these are
    # infrastructure/tool error messages, not meaningful LLM summaries
    if re.match(r'^\s*(error|exception|failed)\s*:', result_lower):
        return False

    # Reject HTTP status codes standing alone (not embedded in natural language)
    if result_lower in ("400", "422", "500", "502", "503", "504"):
        return False

    # Reject pure schema/validation error wrappers
    if "schema error (422)" in result_lower:
        return False

    read_only_patterns = ["read ", "lines from"]
    if all(p in result_lower for p in read_only_patterns):
        return False
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "action" in parsed and "payload" in parsed and "result" not in parsed:
            return False
    except (json.JSONDecodeError, ValueError):
        pass
    return True


async def resolve_mission_workspace(user_id: str, assigned_workspace_id: str | None = None) -> dict | None:
    """Resolve (or create) the workspace an agentic Raven mission should run in.

    - If ``assigned_workspace_id`` is given, resolve/bootstrap and return it.
    - Otherwise reuse an available user-scoped workspace, or create a new DEFAULT
      workspace for the user if none exists.

    Workspaces are just sandboxed directories for running commands; they do NOT
    require a git repository unless one is explicitly requested (``create_repo``).
    """
    async def _resolve_one(wid: str) -> dict | None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client:
                res = await client.post(
                    f"{WORKSPACE_RUNTIME_SVC}/workspace/resolve",
                    json={"workspace_id": wid, "user_context": {"user": user_id, "is_admin": False}},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                )
                if res.status == 200:
                    return (await res.json()).get("workspace")
        except Exception as e:
            log.warning(f"[workspace] resolve {wid} failed: {e}")
        return None

    if assigned_workspace_id:
        ws = await _resolve_one(assigned_workspace_id)
        if ws:
            return ws
        # Not found — try to bootstrap it (creates the repo only if requested).
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20.0)) as client:
                boot = await client.post(
                    f"{WORKSPACE_RUNTIME_SVC}/workspaces/bootstrap",
                    json={"workspace_id": assigned_workspace_id, "rag_user": user_id},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                )
                if boot.status == 200:
                    return (await boot.json()).get("workspace")
        except Exception as e:
            log.warning(f"[workspace] bootstrap {assigned_workspace_id} failed: {e}")
        return None

    # No assigned workspace: do NOT invent a workspace name. The gateway only
    # surfaces an existing workspace the user already flagged as their default (via
    # the ``is_default`` flag — never by name), or any available user-scoped
    # workspace Raven may reuse. Otherwise Raven decides — via the
    # WorkspaceCreateRequest tool — whether it needs its own dedicated sandbox.
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client:
            lst = await client.get(
                f"{WORKSPACE_RUNTIME_SVC}/workspaces",
                params={"rag_user": user_id},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
            )
            workspaces = (await lst.json()).get("workspaces", []) if lst.status == 200 else []
    except Exception as e:
        log.warning(f"[workspace] list failed: {e}")
        workspaces = []
    for item in workspaces:
        if isinstance(item, dict) and item.get("is_default") and str(item.get("scope") or "user") == "user":
            return item
    for item in workspaces:
        if isinstance(item, dict) and item.get("available") and str(item.get("scope") or "user") == "user":
            return item
    return None


async def _is_blocked_for_repo(
    ws_id: str | None,
    created_workspaces: set[str],
    starting_ws_id: str | None,
) -> bool:
    """Return True if a NEW repository must NOT be created in ``ws_id``.

    A repo may only be created inside a workspace Raven acquired for itself via
    WorkspaceCreateRequest during this mission (tracked in ``created_workspaces``).
    Everything else — the mission's starting/default workspace, shared/system
    workspaces, or any workspace we cannot prove is Raven-created — is blocked.
    The check fails CLOSED: if we cannot confirm the workspace is Raven-created,
    repo creation is forbidden. Detection is by tracked id, never by name.
    """
    if not ws_id:
        return True
    if ws_id in created_workspaces:
        return False
    if ws_id == starting_ws_id:
        return True
    # Unknown workspace: attempt a metadata lookup to confirm it is a dedicated,
    # non-default user workspace. Any failure (e.g. auth/resolve error) fails closed.
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client:
            res = await client.post(
                f"{WORKSPACE_RUNTIME_SVC}/workspace/resolve",
                json={"workspace_id": ws_id, "user_context": {"user": "default", "is_admin": True}},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
            )
            if res.status == 200:
                ws = (await res.json()).get("workspace") or {}
                if not ws.get("is_default") and str(ws.get("scope") or "").lower() != "system":
                    return False
    except Exception:
        pass
    return True


async def AgentLoop(query: str, selected_model: str, full_system: str, short_term: list, rag_user: str, creds: ResolvedCredentials, mission_id: int | None = None, rag_context: str = "", show_thinking: bool = False, workspace_id: str | None = None) -> Any:
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
            await _stream_redis.rpush(f"raven:mission:history:{mission_id}", msg_str)  # type: ignore[reportGeneralTypeIssues]
            await _stream_redis.expire(f"raven:mission:history:{mission_id}", 86400)
            await _stream_redis.publish(f"raven:mission:stream:{mission_id}", msg_str)
        except Exception as e:
            log.warning(f"Failed to stream event: {e}")

    # 0. Resolve the workspace this agentic mission runs in.
    # If the task assigned an existing workspace we use it; otherwise we resolve (or
    # fall back to) a workspace. Raven itself decides — via the WorkspaceCreateRequest
    # tool described in its protocol — whether a mission needs its own dedicated
    # sandbox, or should reuse an existing one. The gateway only supplies the means.
    try:
        if workspace_id:
            _ws = await resolve_mission_workspace(rag_user, workspace_id)
        else:
            _ws = await resolve_mission_workspace(rag_user, None)
    except Exception as e:
        log.warning(f"[AgentLoop] workspace resolve failed: {e}")
        _ws = None
    if _ws:
        workspace_id = _ws.get("id") or workspace_id
        _ws_path = _ws.get("resolved_path") or ""
        if _ws_path:
            full_system += (
                f"\n\n[WORKSPACE CONTEXT]\n"
                f"You are operating inside workspace '{workspace_id}'.\n"
                f"Absolute workspace path on disk: {_ws_path}\n"
                f"Write files relative to this path (e.g. 'game.py' for the root) or use "
                f"the absolute path above. Shell commands already run inside this workspace "
                f"directory — do NOT prepend 'cd' to it."
            )

    # The workspace this mission started in (typically the user's flagged default).
    # A NEW repository must never be created here. Raven acquires a dedicated sandbox
    # via WorkspaceCreateRequest; those ids are tracked in `created_workspaces` so the
    # repo-creation guardrail can allow only Raven-created workspaces (fail-closed).
    starting_ws_id = workspace_id
    created_workspaces: set[str] = set()

    # 1. Fetch dynamic settings and resolve active provider/model
    settings = await get_dynamic_llm_settings()
    provider = await get_provider(settings)
    active_provider_name = settings.get("active_llm_provider", "ollama")

    # Log relevant model settings for debugging
    log.info(f"[AgentLoop] Settings: active_provider={active_provider_name}, "
             f"assistant_model={settings.get('assistant_model')}, "
             f"coding_model={settings.get('coding_model')}")

    # 2. Resolve Role-Based Model (Coder/Assistant) if selected_model is generic or "auto"
    original_model = selected_model
    # Role aliases that should resolve to a configured model rather than being
    # treated as an explicit model name. "coding" (the settings key) and synonyms
    # all map to coding_model; assistant/chat map to assistant_model.
    ROLE_ASSISTANT = {"auto", "assistant", "chat"}
    ROLE_CODER = {"coder", "coding", "code", "repair", "raven", "dev", "developer", "technical"}
    if selected_model in ROLE_ASSISTANT or selected_model in ROLE_CODER:
        tech_keywords = ["coder", "fix", "repair", "audit", "mission", "raven", "development", "git", "workspace"]
        is_technical = (selected_model in ROLE_CODER) or any(word in query.lower() for word in tech_keywords)

        selected_model = settings.get("coding_model") or "" if is_technical else settings.get("assistant_model") or ""
        log.info(f"[AgentLoop] Model resolved from '{original_model}' to '{selected_model}' (is_technical={is_technical})")
    else:
        log.info(f"[AgentLoop] Using explicit model: '{selected_model}' (not a role alias)")

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
            {"role": "system", "content": enhanced_system},
            *short_term,
            {"role": "user", "content": query},
        ],
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
    generated_plan = ""

    # --- VRAM-SAFE SCRATCHPAD ---
    action_log = []

    # --- HARNESS GUARDS (OpenCode-orchestrator / Hermes patterns) ---
    # These let the runtime — not the model — decide when work is truly done.
    #  * _recent_actions: (signature, succeeded) for stagnation detection.
    #  * _written_files / _verified_files: enforce lint/test before finishing.
    _recent_actions: list[tuple[str, bool]] = []
    _written_files: set[str] = set()
    _verified_files: set[str] = set()
    _verification_nudge_sent = False
    _stagnation_nudge_sent = False

    # --- PLANNING PHASE ---
    try:
        raven_plan = await load_prompt(get_http_client(), PROMPT_RAVEN_PLAN)
        plan_prompt = [
            {"role": "system", "content": raven_plan},
            {"role": "user", "content": f"Mission: {query}\n\nCreate a concise execution plan:"}
        ]
        plan_data = await execute_inference(provider, selected_model, plan_prompt, {"temperature": 0.1, "num_predict": 512})
        generated_plan = plan_data.get("message", {}).get("content", "").strip()
        if generated_plan:
            action_log.append(f"PLAN GENERATED:\n{generated_plan}")
            log.info(f"[AgentLoop] Planning phase complete. Plan:\n{generated_plan[:500]}")
            await stream_event("system", f"Plan generated ({generated_plan.count(chr(10)) + 1} steps)")
    except Exception as e:
        log.warning(f"[AgentLoop] Planning phase failed (continuing without plan): {e}")
        generated_plan = ""

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
        actions_seen: dict[str, int] = {}
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

    def _compact_conversation(conv: list[dict], keep_last: int = 6, threshold: int = 12000) -> list[dict]:
        """Keep a running, compacted conversation so the model retains learned context
        across a long mission instead of only seeing the last tool result each turn.

        When the total context exceeds `threshold`, older turns are folded into the
        system prompt (preserved learnings) while the most recent `keep_last` turns are
        kept verbatim. This prevents the model from 'forgetting' earlier decisions while
        bounding token usage / llama.cpp cache thrashing.
        """
        total = sum(len(m.get("content", "")) for m in conv)
        if total <= threshold:
            return conv
        system_msgs = [m for m in conv if m.get("role") == "system"]
        body = [m for m in conv if m.get("role") != "system"]
        if len(body) <= keep_last:
            return conv
        older = body[:-keep_last]
        recent = body[-keep_last:]
        note_lines = []
        for m in older:
            c = m.get("content", "") or ""
            note_lines.append(f"[{m.get('role', 'user')}] {c[:240]}{'…' if len(c) > 240 else ''}")
        compact_note = (
            "COMPACTED EARLIER CONTEXT (preserved learnings — do not re-derive):\n"
            + "\n".join(note_lines)
        )
        base_system = system_msgs[0]["content"] if system_msgs else ""
        new_system = {"role": "system", "content": base_system + "\n\n" + compact_note}
        return [new_system, *recent]

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

    # Persistent, compacted conversation history. This lets the model retain learned
    # context (decisions, constraints, prior tool results) across a long mission
    # instead of only ever seeing the single most-recent tool result each turn.
    conversation: list[dict] = [
        {"role": "system", "content": enhanced_system},
        {"role": "user", "content": (
            f"MISSION LOCK: {query}"
            + (f"\n\nEXECUTION PLAN:\n{generated_plan}" if generated_plan else "")
        )},
    ]

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
                r_kill = redis.from_url(REDIS_URL, decode_responses=True)
                kill_flag = await r_kill.get(f"raven:mission:kill:{mission_id}")
                if kill_flag:
                    log.warning(f"[AgentLoop] MISSION KILL SIGNAL RECEIVED for {mission_id}. Terminating.")
                    await stream_event("system", "Mission terminated by user.")
                    await _clear_checkpoint()
                    return "MISSION TERMINATED: User requested cancellation via control plane."
            except Exception as e:
                log.error(f"[AgentLoop] Error checking mission kill flag: {e}")

        # --- PAUSE FOR LLM ACCESS (Redis polling) ---
        if mission_id:
            try:
                r_pause = redis.from_url(REDIS_URL, decode_responses=True)
                pause_count = 0
                while await r_pause.get(f"raven:mission:pause:{mission_id}"):
                    if pause_count == 0:
                        log.warning(f"[AgentLoop] MISSION PAUSED for {mission_id}. Waiting for resume signal.")
                        await stream_event("system", "Mission paused — waiting for LLM access to become available.")
                    pause_count += 1
                    await asyncio.sleep(5)
                    if pause_count % 12 == 0:
                        log.info(f"[AgentLoop] Still paused ({pause_count * 5}s elapsed)")
                if pause_count > 0:
                    log.info(f"[AgentLoop] Mission {mission_id} resumed after {pause_count * 5}s pause.")
                    await stream_event("system", f"Mission resumed after {pause_count * 5}s pause.")
                await r_pause.close()
            except Exception as e:
                log.error(f"[AgentLoop] Error checking mission pause flag: {e}")

        await stream_event("system", f"Agent loop iteration {iter_num}/{MAX_TOOL_ITERATIONS} started.")
        log.info(f"[AgentLoop] Iteration {iter_num}/{MAX_TOOL_ITERATIONS} | total elapsed {elapsed_total:.0f}s")

        heartbeat_stop = asyncio.Event()

        async def _heartbeat(iter_n: int, t0: float, heartbeat_stop: asyncio.Event = heartbeat_stop) -> None:
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
            # Compress action-log context to prevent token bloat
            ctx_summary, ctx_recent = await _compress_context()

            user_content = ""
            if ctx_summary:
                user_content += ctx_summary + "\n\n"
            user_content += "RECENT ACTIONS:\n" + ctx_recent
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

                user_content += f"\n\nLAST TOOL RESULT:\n{exec_json}"
            user_content += "\n\nExecute the next step immediately using a JSON tool call block."

            # Maintain a running, compacted conversation so the model retains learned
            # context across the mission instead of only seeing the last tool result.
            conversation.append({"role": "user", "content": user_content})
            conversation = _compact_conversation(conversation)
            ollama_payload["messages"] = conversation

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

                    inference_options = ollama_payload.get("options", {})
                    if not isinstance(inference_options, dict):
                        inference_options = {}
                    data = await execute_inference(
                        provider,
                        selected_model,
                        cast(list, ollama_payload["messages"]),
                        inference_options,
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
            # Record this turn so the model retains context in subsequent iterations.
            if ans and ans.strip():
                conversation.append({"role": "assistant", "content": ans})
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
                # OpenCode-orchestrator idle-boundary rule: the RUNTIME decides
                # "done" — not the model. If files were written but never verified
                # (linted/tested), force one verification step before finishing.
                if not _verification_nudge_sent:
                    unverified = pending_verification(_written_files, _verified_files)
                    if unverified:
                        _verification_nudge_sent = True
                        log.warning(f"[AgentLoop] Unverified writes {unverified}; nudging for lint/test before finish.")
                        action_log.append(
                            f"ITERATION {iter_num}: You changed files ({', '.join(unverified)}) "
                            f"but have NOT run lint/test on them. Before finishing, run the "
                            f"language-appropriate linter/tests (e.g. ruff + pytest for Python, "
                            f"eslint for JS/TS) and cite the results. Do not consider the mission "
                            f"complete until verification passes."
                        )
                        continue
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

        # ── Tool Name Resolution Pipeline ──────────────────────────────────────
        # 1. Normalize: strip underscores/spaces, lowercase → canonical form
        # 2. Short-alias lookup for semantic names (read_file, shell, etc.)
        # 3. Regex pattern matching for common hallucinations
        # 4. Fuzzy match fallback with helpful error message
        raw_action = str(tool_data.get("action") or tool_data.get("operation") or "")
        action_name = re.sub(r'[\s_]+', '', raw_action).lower()

        if action_name not in ALLOWED_TOOLS:
            short_action = raw_action.lower().strip()

            # ── Tier 1: Exact short aliases (semantic names that don't normalize) ──
            action_map_aliases = {
                "read_file": "workspacefilereadrequest",
                "write_file": "workspacefilewriterequest",
                "filewriterequest": "workspacefilewriterequest",
                "patch_file": "workspacefilepatchrequest",
                "lint_file": "workspacelintrequest",
                "ripgrep": "workspacesearchrequest",
                "grep": "workspacesearchrequest",
                "search": "workspacesearchrequest",
                "shell": "workspaceshellrequest",
                "run": "workspaceshellrequest",
                "shellcommand": "workspaceshellrequest",
                "browse": "webreadrequest",
                "webread": "webreadrequest",
                "edit_file": "workspacefilepatchrequest",
                "file_patch": "workspacefilepatchrequest",
                "apply_patches": "workspacefilepatchrequest",
                "status": "gitoperationrequest",
                "add": "gitoperationrequest",
                "commit": "gitoperationrequest",
                "push": "gitoperationrequest",
                "pull": "gitoperationrequest",
                "diff": "gitoperationrequest",
                "log": "gitoperationrequest",
                "restart_service": "controlplanerequest",
            }
            if short_action in action_map_aliases:
                action_name = action_map_aliases[short_action]

            # ── Tier 2: Regex patterns for common hallucinations ──
            if action_name not in ALLOWED_TOOLS:
                regex_aliases = [
                    (r'.*workspace.*shell.*', "workspaceshellrequest"),
                    (r'.*workspace.*search.*', "workspacesearchrequest"),
                    (r'.*workspace.*lint.*', "workspacelintrequest"),
                    (r'.*workspace.*read.*', "workspacefilereadrequest"),
                    (r'.*workspace.*write.*', "workspacefilewriterequest"),
                    (r'.*workspace.*patch.*', "workspacefilepatchrequest"),
                    (r'.*workspace.*bootstrap.*', "workspacebootstraprequest"),
                    (r'.*storage.*file.*read.*', "storagefilereadrequest"),
                    (r'.*storage.*file.*write.*', "storagefilewriterequest"),
                    (r'.*storage.*list.*', "storagelistrequest"),
                    (r'.*storage.*index.*', "storageindexrequest"),
                    (r'.*git.*operation.*', "gitoperationrequest"),
                    (r'.*git.*status.*', "gitoperationrequest"),
                    (r'.*git.*commit.*', "gitoperationrequest"),
                    (r'.*git.*push.*', "gitoperationrequest"),
                    (r'.*git.*pull.*', "gitoperationrequest"),
                    (r'.*light.*control.*', "lightcontrolrequest"),
                    (r'.*media.*play.*', "mediaplayrequest"),
                    (r'.*media.*status.*', "mediastatusrequest"),
                    (r'.*media.*transport.*', "mediatransportrequest"),
                    (r'.*video.*play.*', "videoplayrequest"),
                    (r'.*tv.*cast.*', "tvcastrequest"),
                    (r'.*climate.*', "climaterequest"),
                    (r'.*security.*', "securityrequest"),
                    (r'.*announcement.*', "announcementrequest"),
                    (r'.*ha.*service.*', "haservicerequest"),
                    (r'.*calendar.*', "calendarrequest"),
                    (r'.*note.*', "noterequest"),
                    (r'.*timer.*', "timerrequest"),
                    (r'.*talk.*', "talkrequest"),
                    (r'.*web.*search.*', "websearchrequest"),
                    (r'.*web.*read.*', "webreadrequest"),
                    (r'.*docker.*log.*', "dockerlogsrequest"),
                    (r'.*docker.*compose.*', "dockercomposerequest"),
                    (r'.*deployment.*', "deploymentrequest"),
                    (r'.*capability.*index.*', "capabilityindexrequest"),
                    (r'.*volume.*inventory.*', "volumeinventoryrequest"),
                    (r'.*system.*learning.*', "systemlearningrequest"),
                    (r'.*discovery.*sync.*', "discoverysyncrequest"),
                    (r'.*identity.*', "identityrequest"),
                    (r'.*audiobookshelf.*', "audiobookshelfrequest"),
                    (r'.*llm.*info.*', "llminforequest"),
                    (r'.*context.*search.*', "contextsearchrequest"),
                    (r'.*ha.*config.*', "haconfigrequest"),
                    (r'.*control.*plane.*', "controlplanerequest"),
                    (r'.*restart.*service.*', "controlplanerequest"),
                    (r'.*entity.*search.*', "entitysearchrequest"),
                    (r'.*logbook.*', "logbookrequest"),
                    (r'.*execution.*log.*', "executionlogrequest"),
                ]
                for pattern, target in regex_aliases:
                    if re.match(pattern, short_action):
                        action_name = target
                        break

            # ── Tier 3: Fuzzy match fallback ──
            if action_name not in ALLOWED_TOOLS:
                import difflib
                matches = difflib.get_close_matches(action_name, list(ALLOWED_TOOLS), n=1, cutoff=0.6)
                if matches:
                    action_name = matches[0]
                    log.info(f"[AgentLoop] Fuzzy-matched '{raw_action}' → '{action_name}'")
                else:
                    # Build a helpful tool table for the LLM
                    tool_categories = {
                        "Workspace Tools": ["workspacefilereadrequest", "workspacefilewriterequest", "workspacefilepatchrequest", "workspacelintrequest", "workspacesearchrequest", "workspaceshellrequest", "workspacebootstraprequest"],
                        "Git Tools": ["gitoperationrequest"],
                        "Storage Tools": ["storagefilereadrequest", "storagefilewriterequest", "storagelistrequest", "storageindexrequest"],
                        "Media Tools": ["mediaplayrequest", "mediatransportrequest", "mediastatusrequest", "videoplayrequest"],
                        "Web Tools": ["websearchrequest", "webreadrequest"],
                        "Docker Tools": ["dockerlogsrequest", "dockercomposerequest"],
                        "HA Tools": ["lightcontrolrequest", "haservicerequest", "climate", "securityrequest", "announcementrequest", "entitysearchrequest", "logbookrequest", "executionlogrequest", "haconfigrequest"],
                        "Other": ["calendarrequest", "noterequest", "timerrequest", "talkrequest", "tvcastrequest", "systemlearningrequest", "discoverysyncrequest", "identityrequest", "identitymanagerequest", "audiobookshelfrequest", "llminforequest", "contextsearchrequest", "deploymentrequest", "capabilityindexrequest", "volumeinventoryrequest", "controlplanerequest"],
                    }
                    tool_table = "\n".join(f"  {cat}: {', '.join(tools)}" for cat, tools in tool_categories.items())
                    closest = difflib.get_close_matches(action_name, list(ALLOWED_TOOLS), n=3, cutoff=0.4)
                    closest_str = f" Closest matches: {', '.join(closest)}." if closest else ""
                    error_msg = (
                        f"SCHEMA ERROR: Unknown tool '{raw_action}'.{closest_str}\n"
                        f"Available tools by category:\n{tool_table}\n"
                        f"Use the EXACT tool name from the list above."
                    )
                    log.warning(f"[AgentLoop] Unknown action: {action_name} (raw: {raw_action})")
                    action_log.append(f"ITERATION {iter_num}: {error_msg}")
                    exec_data = {"status": "ERROR", "message": error_msg}
                    continue

        await stream_event("action", f"Executing Tool: {action_name}")
        log.info(f"[AgentLoop] Dispatching action: {action_name}")

        try:
            action = action_name
            payload = tool_data.get("payload", tool_data)

            action_map = {
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
                "deploymentrequest": (EXECUTION_SVC, "/execute/deploy"),
                "capabilityindexrequest": (EXECUTION_SVC, "/execute/index_capabilities"),
                "volumeinventoryrequest": (EXECUTION_SVC, "/execute/volumes"),
                "workspacefilereadrequest": (EXECUTION_SVC, "/execute/workspace_file_read"),
                "workspacefilewriterequest": (EXECUTION_SVC, "/execute/workspace_file_write"),
                "workspacefilepatchrequest": (EXECUTION_SVC, "/execute/workspace_file_patch"),
                "workspacelintrequest": (EXECUTION_SVC, "/execute/workspace_lint"),
                "workspacesearchrequest": (EXECUTION_SVC, "/execute/workspace_search"),
                "workspaceshellrequest": (EXECUTION_SVC, "/execute/workspace_shell"),
                "workspacecreaterequest": (WORKSPACE_RUNTIME_SVC, "/workspaces"),
                "workspacesettingsupdaterequest": (WORKSPACE_RUNTIME_SVC, "/workspaces/{workspace_id}"),
                "storagefilereadrequest": (EXECUTION_SVC, "/execute/storage_file_read"),
                "storagefilewriterequest": (EXECUTION_SVC, "/execute/storage_file_write"),
                "storagelistrequest": (EXECUTION_SVC, "/execute/storage_list"),
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
                "entitysearchrequest": (EXECUTION_SVC, "/execute/entity_search"),
                "logbookrequest": (EXECUTION_SVC, "/execute/ha_logbook"),
                "executionlogrequest": (EXECUTION_SVC, "/execute/logs"),
                "documentbroadcastrequest": (EXECUTION_SVC, "/execute/composite/broadcast"),
                "nightmoderequest": (EXECUTION_SVC, "/execute/composite/night_mode"),
                "ttsrequest": (EXECUTION_SVC, "/execute/tts"),
                "storagetexttorequest": (STORAGE_SVC, "/text_to_audio"),
                "networkdevicescanrequest": (EXECUTION_SVC, "/execute/network_scan"),
                "ghrequest": (EXECUTION_SVC, "/execute/gh"),
                "controlplanerequest": (CONTROL_PLANE_URL, "/api/restart/{service_name}"),
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

                # Special handling: WorkspaceSettingsUpdateRequest PATCHes an existing
                # workspace's settings (repo_url, git_remote, default_branch, display_name,
                # ...). The target id lives in the payload; build the PATCH URL from it and
                # strip it from the body sent to workspace_runtime.
                if lookup_action == "workspacesettingsupdaterequest" and isinstance(payload, dict):
                    _ws_id = payload.pop("workspace_id", None) or payload.pop("id", None) or workspace_id
                    if _ws_id:
                        svc_base = WORKSPACE_RUNTIME_SVC
                        endpoint = f"/workspaces/{_ws_id}"

                # WorkspaceCreateRequest: the runtime's `Workspace` model rejects extra
                # keys (e.g. user_context) and REQUIRES `id` + `display_name`. Build a
                # clean payload so creation always succeeds and we can adopt the real id.
                _skip_user_context = False
                if lookup_action == "workspacecreaterequest" and isinstance(payload, dict):
                    _wid = payload.get("id") or payload.get("workspace_id")
                    if not _wid:
                        _wid = uuid.uuid4().hex[:16]
                    _display = (
                        payload.get("display_name")
                        or payload.get("name")
                        or payload.get("displayName")
                        or str(_wid)
                    )
                    payload = {
                        "id": str(_wid),
                        "display_name": str(_display),
                        "scope": payload.get("scope") or "user",
                        "owner_user": creds.user,
                        "description": payload.get("description") or "",
                        "is_default": False,
                    }
                    _skip_user_context = True

                # ALWAYS inject user_context. Pydantic schemas require it for validation.
                # (Skipped for WorkspaceCreateRequest — the runtime's Workspace model
                # rejects the extra key, which would 422 and break adoption.)
                if not _skip_user_context:
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

                # Force workspace-scoped tool calls into the mission's CURRENT working
                # workspace. Once Raven creates its own workspace (via
                # WorkspaceCreateRequest) and we adopt it as `workspace_id`, all
                # subsequent file/shell/git operations run there — overriding any id the
                # model happens to send. This keeps Raven inside the sandbox it created
                # even when the model doesn't reliably carry the id across turns.
                if workspace_id and isinstance(payload, dict):
                    _ws_actions = {
                        "workspacefilereadrequest", "workspacefilewriterequest",
                        "workspacefilepatchrequest", "workspaceshellrequest",
                        "workspacesearchrequest", "workspacelintrequest",
                        "gitoperationrequest", "workspacebootstraprequest",
                        "workspacesettingsupdaterequest",
                    }
                    if lookup_action in _ws_actions:
                        payload["workspace_id"] = workspace_id

                # GUARDRAIL: creating a NEW repository must happen in a dedicated
                # workspace Raven acquired for itself — never the default/shared/system
                # workspace. If Raven attempts `gh repo create` (or similar) there, fail
                # loudly so it creates a workspace first instead of polluting a shared one.
                _skip_post = False
                _creates_repo = False
                if isinstance(payload, dict):
                    _cmd = " ".join(str(payload.get(k, "")) for k in ("command", "action", "repo_url", "operation"))
                    if "gh repo create" in _cmd or "gh repo fork" in _cmd:
                        _creates_repo = True
                if _creates_repo:
                    _eff_ws = (payload.get("workspace_id") if isinstance(payload, dict) else None) or workspace_id
                    if await _is_blocked_for_repo(_eff_ws, created_workspaces, starting_ws_id):
                        exec_data = {
                            "status": "ERROR",
                            "message": (
                                "GUARDRAIL: You tried to create a new repository from a "
                                "default/shared/system workspace. This is forbidden. Call "
                                "WorkspaceCreateRequest FIRST to acquire a dedicated workspace "
                                "(e.g. {\"@type\": \"WorkspaceCreateRequest\", \"id\": \"raven-<project>\", "
                                "\"display_name\": \"...\"}), capture its id, and pass that id as "
                                "workspace_id in this and every following tool call. Then retry."
                            ),
                        }
                        _skip_post = True

                if not _skip_post:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120.0)) as client:
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
                        log.info(f"[AgentLoop] Tool response: {resp.status}")

                        if resp.status == 422:
                            try:
                                error_detail = (await resp.json()).get("detail", "Validation failed")
                                # Sanitize error detail before exposing to LLM — 422 responses
                                # echo back the full request payload including credentials
                                error_detail = sanitize_for_llm(error_detail)
                                msg = f"SCHEMA ERROR (422): {error_detail}. Ensure you are using the correct field names (e.g. 'action', 'message') instead of 'command' or 'commit_message'."
                            except Exception:
                                msg = f"SCHEMA ERROR (422): {await resp.text()}. Check your field names."
                            exec_data = {"status": "ERROR", "message": msg}
                        else:
                            exec_data = await resp.json()
                            # Adopt a freshly-created workspace as the mission's working
                            # workspace so later file/git tool calls run inside it.
                            if lookup_action == "workspacecreaterequest" and isinstance(exec_data, dict):
                                _created = (exec_data.get("workspace") or {}).get("id")
                                if _created:
                                    workspace_id = str(_created)
                                    created_workspaces.add(workspace_id)
                                    log.info(f"[AgentLoop] Adopted newly created workspace: {workspace_id}")

                    # Sanitize execution results before any downstream use
                    exec_data = sanitize_for_llm(exec_data)

                    short_msg = exec_data.get("message", "Success")
                    await stream_event("result_success", short_msg)
                    # Build action log entry with detail for data-producing tools
                    detail = exec_data.get("detail", {}) if isinstance(exec_data, dict) else {}
                    if detail and not isinstance(detail, str):
                        # For read-only tools with useful detail data, include a truncated summary
                        detail_parts = []
                        if "branch_line" in detail:
                            detail_parts.append(detail["branch_line"].strip())
                        if "raw_stdout" in detail:
                            raw = detail["raw_stdout"].strip()
                            if raw:
                                detail_parts.append(raw[:500])
                        if detail.get("porcelain"):
                            for p in detail["porcelain"][:10]:
                                detail_parts.append(p.strip())
                        if detail_parts:
                            action_log.append(f"Step {iter_num}: {action} -> {short_msg} | Details: " + "\n".join(detail_parts))
                        else:
                            action_log.append(f"Step {iter_num}: {action} -> {short_msg}")
                    elif isinstance(detail, str) and detail.strip():
                        action_log.append(f"Step {iter_num}: {action} -> {short_msg} | {detail.strip()[:300]}")
                    else:
                        action_log.append(f"Step {iter_num}: {action} -> {short_msg}")
                    # Track successful tool executions (non-ERROR responses)
                    if isinstance(exec_data, dict) and exec_data.get("status") != "ERROR":
                        successful_tool_calls += 1
                        sig = action_signature(action_name, payload)
                        _recent_actions.append((sig, True))
                        if len(_recent_actions) > 12:
                            _recent_actions = _recent_actions[-12:]
                        if lookup_action in ("workspacefilewriterequest", "workspacefilepatchrequest") \
                                and isinstance(payload, dict):
                            fp = payload.get("file_path") or payload.get("path", "")
                            if fp:
                                _written_files.add(fp)
                        if is_verification_action(lookup_action, payload):
                            # A verification run clears the "unverified" state for
                            # every written file (language-agnostic lint/test).
                            _verified_files.update(_written_files)
                    else:
                        sig = action_signature(action_name, payload)
                        _recent_actions.append((sig, False))
                        if len(_recent_actions) > 12:
                            _recent_actions = _recent_actions[-12:]

                        # --- POST-WRITE LINT HOOK ---
                        lintable_actions = {"workspacefilewriterequest", "workspacefilepatchrequest"}
                        if lookup_action in lintable_actions and isinstance(payload, dict):
                            file_path = payload.get("file_path") or payload.get("path", "")
                            if file_path:
                                lint_feedback = await run_post_write_lint(file_path, EXECUTION_SVC, INTERNAL_SECRET, log, payload.get("user_context"))
                                if lint_feedback:
                                    await stream_event("result_error", lint_feedback)
                                    exec_data = {
                                        "status": "LINT_ERRORS",
                                        "message": lint_feedback,
                                        "file_path": file_path,
                                    }

                    # Checkpoint state after successful tool execution
                    await _save_checkpoint(iter_num)
            else:
                log.warning(f"[AgentLoop] Unknown action: {action}")
                exec_data = {"status": "ERROR", "message": f"Unknown action: {action}"}

        except Exception as e:
            await stream_event("result_error", str(e))
            log.error(f"[AgentLoop] Tool execution failed: {e}")
            exec_data = {"status": "ERROR", "message": str(e)}

        # --- STAGNATION ESCALATION (OpenCode: stop blind retries, re-plan/ASK) ---
        # If the same step keeps failing, nudge once to change approach; if it
        # keeps failing after the nudge, terminate instead of looping forever.
        if detect_repetitive_failure(_recent_actions, window=3):
            if not _stagnation_nudge_sent:
                _stagnation_nudge_sent = True
                log.warning("[AgentLoop] Stagnation detected: same action failed 3x. Forcing re-plan.")
                action_log.append(
                    f"ITERATION {iter_num}: The SAME step has FAILED 3 times in a row. "
                    f"Stop repeating it. Re-plan: try a different approach or tool, or ask for "
                    f"clarification. Do not issue the identical failing call again."
                )
            else:
                log.error("[AgentLoop] Stagnation persists after re-plan nudge. Terminating.")
                ans = "ERROR: Same step failed repeatedly after re-planning. Aborting to avoid an infinite loop."
                await _clear_checkpoint()
                break


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

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30.0)) as client:
                resp = await client.post(
                    f"{EXECUTION_SVC}/execute/learning",
                    json=payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                )
                if resp.status != 200:
                    log.warning(f"[AgentLoop] Learning persistence failed: {resp.status} {resp.text}")
        except Exception as e:
            log.warning(f"[AgentLoop] Learning persistence skipped: {e}")

    # --- SUMMARIZATION PHASE ---
    # Trigger if we have successful tool calls OR if the model output is purely JSON/messy
    is_messy = "was was was" in ans or "```json" in ans or (ans.strip().startswith("{") and ans.strip().endswith("}"))
    ans_is_empty = not ans or ans.strip() in ("", "None", "null", "{}", "[]", "}")
    if (successful_tool_calls > 0 or is_messy) and (
        extract_action_json(ans) or len(ans.strip()) < 30 or is_messy
    ):
        # If the last response still looks like a tool call or is very short/messy, force a clean summary
        log.info("[AgentLoop] Finalizing with clean summarization phase...")
        if ans_is_empty:
            # LLM response was empty — build summary from action log only
            action_summary = "\n".join(action_log)
            summary_prompt = [
                {"role": "system", "content": "You are Raven. Summarize the mission result for the user in clean, natural language. Do NOT use JSON. Do NOT repeat yourself. Be concise. State what was accomplished based on the actions taken."},
                {"role": "user", "content": f"Mission: {query}\n\nActions taken:\n{action_summary}\n\nThe LLM did not produce a final response, but the following actions were completed successfully. Summarize what was accomplished."}
            ]
        else:
            summary_prompt = [
                {"role": "system", "content": "You are Raven. Summarize the mission result for the user in clean, natural language. Do NOT use JSON. Do NOT repeat yourself. Be concise. Do NOT say the mission failed unless the tool execution itself reported an error."},
                {"role": "user", "content": f"Mission: {query}\n\nActions taken:\n" + "\n".join(action_log) + f"\n\nRaw output: {ans}\n\nPlease provide the final clean summary now:"}
            ]
            try:
                data = await execute_inference(provider, selected_model, summary_prompt, {"temperature": 0.0})
                ans = data.get("message", {}).get("content", ans)
            except Exception as e:
                log.warning(f"[AgentLoop] Summarization phase failed: {e}")
                from services.gateway.orchestrator import strip_json_from_response
                ans = strip_json_from_response(ans)

    # --- POST-MISSION REFLECTION ---
    reflection_summary = ""
    if action_log and successful_tool_calls > 0:
        try:
            raven_reflection = await load_prompt(get_http_client(), PROMPT_RAVEN_REFLECTION)
            reflection_prompt = [
                {"role": "system", "content": raven_reflection},
                {"role": "user", "content": f"Mission: {query}\n\nPlan:\n{generated_plan}\n\nActions taken:\n" + "\n".join(action_log) + f"\n\nFinal result: {ans}\n\nProvide your reflection:"}
            ]
            reflection_data = await execute_inference(provider, selected_model, reflection_prompt, {"temperature": 0.1})
            reflection_summary = reflection_data.get("message", {}).get("content", "").strip()
            if reflection_summary:
                action_log.append(f"REFLECTION:\n{reflection_summary}")
                log.info(f"[AgentLoop] Mission reflection:\n{reflection_summary[:500]}")
        except Exception as e:
            log.warning(f"[AgentLoop] Reflection phase failed: {e}")

    if action_log and not (isinstance(exec_data, dict) and exec_data.get("status") == "ERROR"):
        if should_persist_learning(ans):
            learning_summary = "\n".join([
                f"Query: {query}",
                f"Actions: {' | '.join(action_log)}",
                f"Final answer: {ans}",
            ])
            await _persist_learning(learning_summary)
        else:
            log.info(f"[AgentLoop] Skipping RAG learning persistence — result appears meaningless: {ans[:100]}")

    if mission_id and full_audit_log:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client:
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


async def run_post_write_lint(file_path: str, execution_svc: str | None, internal_secret: str, logger, user_context: dict | None = None) -> str | None:
    """
    Shared post-write lint hook. Returns lint feedback string on failure, None on success.
    """
    if not execution_svc:
        return None
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    lintable_exts = {"py", "js", "ts", "tsx", "sh", "bash", "json", "yaml", "yml"}
    if ext not in lintable_exts:
        return None

    logger.info(f"Post-write lint check for {file_path} (ext={ext})")
    try:
        lint_payload: dict[str, Any] = {"path": file_path}
        if user_context:
            lint_payload["user_context"] = user_context
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15.0)) as lint_client:
            lint_resp = await lint_client.post(
                f"{execution_svc}/execute/workspace_lint",
                json=lint_payload,
                headers={"X-Internal-Secret": internal_secret},
            )
            if lint_resp.status == 200:
                lint_data = await lint_resp.json()
                lint_passed = lint_data.get("detail", {}).get("passed", True)
                if lint_passed is False:
                    lint_msg = lint_data.get("message", "")
                    detail = lint_data.get("detail", {}) or {}
                    results = detail.get("results", []) if isinstance(detail, dict) else []
                    if results:
                        issue_lines = []
                        for r in results:
                            output = r.get("output", "")
                            if output:
                                issue_lines.extend(output.split("\n")[:8])
                        lint_feedback = f"LINT FAILED for {file_path}:\n" + "\n".join(issue_lines[:15])
                    else:
                        lint_feedback = f"LINT FAILED for {file_path}: {lint_msg}"
                    logger.warning(f"{lint_feedback}")
                    return lint_feedback
                else:
                    logger.info(f"Post-write lint clean for {file_path}")
    except Exception as lint_e:
        logger.warning(f"Post-write lint check failed: {lint_e}")
    return None
