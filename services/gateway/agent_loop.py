import asyncio
import base64
import contextlib
import json
import logging
import re
import shlex
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any, cast

import aiohttp
import redis.asyncio as redis

from services.gateway.config import (
    ALPACA_SD_URL,
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

# Tool actions that operate INSIDE a workspace. When no workspace is assigned yet
# (a project mission), every one of these must be blocked until Raven acquires a
# dedicated workspace via WorkspaceCreateRequest — otherwise it would silently
# operate in the Default Workspace / WORKSPACE_ROOT.
WORKSPACE_TOOL_ACTIONS = {
    "workspacefilereadrequest", "workspacefilewriterequest",
    "workspacefilepatchrequest", "workspaceshellrequest",
    "workspacesearchrequest", "workspacelintrequest",
    "gitoperationrequest", "workspacebootstraprequest",
    "workspacesettingsupdaterequest",
}

# Heuristics that identify a *system-maintenance* mission — i.e. one that edits or
# fixes SharedLLM's own source/logs (e.g. "Raven fix the errors appearing in the
# logs"). These run in the Default Workspace. Anything that builds or creates a
# new project must return False so Raven acquires a dedicated workspace instead of
# polluting the Default Workspace.
_MAINTENANCE_PATTERNS = [
    re.compile(r"\bfix (the )?(error|log|bug|issue)s?\b", re.I),
    re.compile(r"\berrors? (in|appearing|from|within)\b", re.I),
    re.compile(r"\bdebug\b.*\b(gateway|server|platform|sharedllm|this repo|the codebase)\b", re.I),
    re.compile(r"\b(repair|patch|fix)\b.*\b(sharedllm|gateway|the server|the platform|this repo)\b", re.I),
    re.compile(r"\bsharedllm\b.*\b(fix|error|bug|log|repair|patch)\b", re.I),
]


def is_system_maintenance_task(query: str | None) -> bool:
    """Return True when the mission is system maintenance on SharedLLM itself.

    Such missions should run in the user's Default Workspace (so Raven edits
    SharedLLM's own code there). Anything that builds/creates a new project must
    return False so Raven is forced to acquire a dedicated workspace.
    """
    if not query:
        return False
    q = query.lower()
    # Strong "build something new" signals => definitely NOT maintenance.
    if any(k in q for k in ("build", "create a", "new ", "space shooter", "scaffold", "make a game", "make an app", "website")):
        return False
    return any(p.search(query) for p in _MAINTENANCE_PATTERNS)


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

class MissionKilledError(Exception):
    """Raised when a mission's kill flag is set mid-inference.

    Lets the agent loop short-circuit a long-running LLM stream and
    terminate the mission cleanly instead of hanging until the kill
    flag is (belatedly) checked at the top of the next iteration.
    """


class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str, timeout: float | aiohttp.ClientTimeout = 600.0):
        self.base_url = base_url.rstrip("/")
        # `total` caps the whole request; `sock_read` caps the gap between
        # successive chunks so a stream that stops producing data (e.g. a
        # wedged upstream) raises instead of blocking forever.
        if isinstance(timeout, aiohttp.ClientTimeout):
            self.timeout = timeout
        else:
            read_to = min(float(timeout), 120.0) if timeout else 120.0
            self.timeout = aiohttp.ClientTimeout(total=timeout or None, sock_read=read_to)

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
        async with shared_http_client() as client:
            log.info(f"[OllamaProvider-Hardened] Calling {self.base_url}/api/chat for model {model}")
            if not chunk_callback:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload, headers={"X-Request-Source": "shared-llm/app"}, timeout=self.timeout)
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
            async with client.post(f"{self.base_url}/api/chat", json=payload, headers={"X-Request-Source": "shared-llm/app"}, timeout=self.timeout) as response:
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
    "workspacecreaterequest", "workspacesettingsupdaterequest",
    "systemlearningrequest", "discoverysyncrequest", "storageindexrequest",
    "dockercomposerequest", "identityrequest", "identitymanagerequest", "controlplanerequest", "restart_service",
    "audiobookshelfrequest", "llminforequest", "contextsearchrequest", "haconfigrequest",
    "entitysearchrequest", "logbookrequest", "executionlogrequest",
    "documentbroadcastrequest", "nightmoderequest", "ttsrequest", "storagetexttorequest",
    "ghrequest",
    "ravenrecallrequest",
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


def detect_repetitive_action(recent: list[tuple[str, bool]], window: int = 8) -> bool:
    """True when the last ``window`` actions share ONE signature (success OR fail).

    Catches an agent "stuck in a loop" that repeats the same step without making
    distinct progress — even when every call succeeds (e.g. re-running the same
    read-only command 20 times). Used to allow long missions while still halting
    runaway repetition.
    """
    if len(recent) < window:
        return False
    last = recent[-window:]
    return len({sig for sig, _ in last}) == 1


# How many identical-result shell runs in a row count as a stuck loop.
NO_PROGRESS_WINDOW = 4


def normalize_shell_goal(command: str) -> str:
    """Collapse the volatile parts of a shell command so that

        SDL_VIDEODRIVER=dummy python main.py --selftest 2>&1
    and
        SDL_VIDEODRIVER=dummy python main.py --selftest 2>/tmp/e; echo EXIT=$?; cat /tmp/e | tail -30

    map to the SAME goal key. We only care about the executable + script + args
    that decide *what* runs, not redirections or exit-code/inspection wrappers —
    otherwise a re-run with a slightly different wrapper evades loop detection.
    """
    if not command:
        return ""
    s = command
    # 1) Drop inspection/exit-code probes FIRST — before env-strip, because
    #    `echo EXIT=$?` would otherwise look like an env assignment to step 3
    #    and survive. These wrappers must not change the normalized goal.
    s = re.sub(r';\s*echo\s+EXIT=.*$', '', s)
    s = re.sub(r';\s*cat\s+\S+(?:\s*\|\s*tail\s*-\d+)?', '', s)
    # 2) Drop redirections (stdout/stderr).
    s = re.sub(r'2>&1|2>/dev/null|&>\s*/?\S*|2>\s*/?\S+|>\s*/?\S+', ' ', s)
    # 3) Drop leading env-var assignments: FOO=bar BAZ=1
    s = re.sub(r'(?:\s|^)[A-Z][A-Z0-9_]*=[^\s"\']+', ' ', s)
    # 4) Collapse whitespace and lowercase.
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s[:160]


def outcome_digest(exec_data) -> str:
    """Short, stable fingerprint of a tool result so we can tell whether a
    re-run produced *identical* output (no progress) vs a new error.

    For a shell run this is the tail of the combined stdout/stderr — the part
    that usually holds the actual error (e.g. a NameError traceback). An empty
    digest means the command produced no meaningful output (a clean pass).
    """
    if not isinstance(exec_data, dict):
        return "na"
    msg = exec_data.get("message") or exec_data.get("detail") or ""
    msg = re.sub(r'\s+', ' ', str(msg)).strip()
    return msg[-200:]


def detect_no_progress(outcomes: list[tuple[str, str]], window: int = NO_PROGRESS_WINDOW) -> bool:
    """True when the last ``window`` shell runs share ONE goal AND produced
    IDENTICAL (non-empty) output. Catches the "exit-0 but never makes progress"
    loop that ``detect_repetitive_failure`` (which needs all-fail) misses — e.g.
    a ``--selftest`` that crashes before printing GAME_OK yet still returns a
    successful shell exit, so the agent re-runs it forever. The non-empty
    requirement avoids flagging clean, repeated passes (empty output).
    """
    if len(outcomes) < window:
        return False
    last = outcomes[-window:]
    if any(not out for _, out in last):
        return False
    goals = {g for g, _ in last}
    outs = {o for _, o in last}
    return len(goals) == 1 and len(outs) == 1


def no_progress_directive(stage: int, iter_num: int, goal: str, out: str) -> str:
    """Build the escalating loop-control directive for a detected no-progress loop.

    Three stages (never a blind abort):
      stage 1 PROBE     — stop re-running; read the error + source; make a DISTINCT fix.
      stage 2 REDIRECT  — steer to a different debug route (web search, recall its
                          own history, re-read source) instead of another blind attempt.
    stage 3 is a hard abort handled separately by the caller.
    """
    goal_s = goal[:90]
    out_s = out[:60]
    if stage == 1:
        return (
            f"ITERATION {iter_num}: LOOP PROBE — you have run the same command "
            f"({goal_s!r}) repeatedly and the output has not changed "
            f"(fingerprint: {out_s!r}). Re-running it will NOT help. "
            f"STOP and diagnose: use RavenRecallRequest (only='shell' or only='failed') "
            f"to inspect prior runs, READ the source file involved and the captured "
            f"error above, identify the root cause, and make a DISTINCT fix — not "
            f"another identical run."
        )
    if stage == 2:
        return (
            f"ITERATION {iter_num}: LOOP REDIRECT — still looping on {goal_s!r} "
            f"with identical output ({out_s!r}). Another identical attempt will "
            f"fail the same way. Take a DIFFERENT route to diagnose:\n"
            f"  1. websearchrequest — search the web for the exact error / how the "
            f"library or API actually works (e.g. the F821 undefined name, the missing "
            f"import, or the correct raylib/pygame call). External docs beat guessing.\n"
            f"  2. RavenRecallRequest (only='failed') — review what you already tried and "
            f"what the prior errors were so you don't repeat them.\n"
            f"  3. WorkspaceFileReadRequest — re-read the actual source file (not just "
            f"patch from memory) and the captured error above, then make a DISTINCT fix.\n"
            f"Do NOT run {goal_s!r} again until you have changed the underlying code "
            f"based on what you learned from the web/history."
        )
    return ""


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
        async with shared_http_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=3.0),
            )
            if resp.status == 200:
                fetched = {item["key"]: item["value"] for item in await resp.json()}
                for k, v in list(fetched.items()):
                    if not v:
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
            "num_predict": 8192,
            "temperature": 0.1,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "thinking": False,
        }
    max_ctx = int(settings.get("llm_local_max_ctx", "4096"))
    params = {
        "num_predict": 8192,  # large enough for full file writes, not just JSON tool calls
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
        "thinking": False,  # Disable thinking blocks to get content faster
    }

    try:
        async with shared_http_client() as client:
            resp = await client.get(f"{local_url.rstrip('/')}/api/ps", timeout=aiohttp.ClientTimeout(total=3.0))
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


async def execute_inference_with_kill(
    provider: BaseLLMProvider,
    model: str,
    messages: list,
    options: dict,
    mission_id: int | None,
    chunk_callback: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    """Run ``execute_inference`` but abort the instant a kill flag is set.

    The base provider only checks the kill flag *between* iterations, so a
    long-running (or wedged) LLM stream can block a cancel indefinitely.
    Here the stream runs as a task while a watcher polls the Redis kill key
    every couple of seconds and cancels the task the moment it is set, so a
    ``cancel`` takes effect during generation instead of at the next loop.
    """
    if not mission_id:
        return await execute_inference(provider, model, messages, options, chunk_callback=chunk_callback)

    inf_task = asyncio.create_task(
        execute_inference(provider, model, messages, options, chunk_callback=chunk_callback)
    )

    async def _kill_watch() -> None:
        while not inf_task.done():
            try:
                if await _is_kill_flag_set(mission_id):
                    log.warning(f"[AgentLoop] KILL flag detected for mission {mission_id} during inference — cancelling stream.")
                    inf_task.cancel()
                    return
            except Exception:
                pass
            await asyncio.sleep(2.0)

    watch = asyncio.create_task(_kill_watch())
    try:
        return await inf_task
    except asyncio.CancelledError:
        # Cancelled by the watcher → confirm the flag and abort the mission.
        try:
            flagged = await _is_kill_flag_set(mission_id)
        except Exception:
            flagged = True
        if flagged:
            raise MissionKilledError(f"Mission {mission_id} killed during inference") from None
        raise
    finally:
        watch.cancel()
        # NOTE: CancelledError is a BaseException (not Exception) in Python 3.8+,
        # so `suppress(Exception)` would NOT swallow the cancellation raised by
        # awaiting the just-cancelled watcher — leaking it as a spurious
        # "user abort". Suppress CancelledError explicitly.
        with contextlib.suppress(asyncio.CancelledError):
            await watch




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


@contextlib.asynccontextmanager
async def shared_http_client() -> AsyncIterator[aiohttp.ClientSession]:
    """Borrow the module-pooled client without closing it on exit.

    Use instead of ``async with aiohttp.ClientSession()`` for internal calls.
    Per-call timeouts must be passed on the request (e.g. ``client.get(..., timeout=...)``).
    """
    yield get_http_client()


_stream_redis = None
_redis_cmd: "redis.Redis | None" = None  # shared command connection (GET/SET only; never used for pub/sub)


async def _get_redis_cmd() -> "redis.Redis":
    """Return a shared Redis connection for non-pub/sub commands.

    Reusing one connection avoids the per-iteration ``redis.from_url()`` churn
    that the old kill/pause poll introduced.
    """
    global _redis_cmd
    if _redis_cmd is None:
        _redis_cmd = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_cmd


# Sentinel values that the control plane may write to a kill/pause flag to mean
# "not active". Because Redis returns these as non-empty strings
# (``decode_responses=True``), a naive ``if flag:`` would treat "false"/"0" as
# truthy and abort the mission. Always route flag reads through this helper.
_KILL_INACTIVE_VALUES = {"0", "false", "none", "null", "off", "no", ""}


async def _redis_flag_active(key: str) -> bool:
    """Return True only if a Redis flag key holds an active (non-sentinel) value.

    Safe against the classic Python-Redis string-eval bug: a flag holding
    "false" or "0" is treated as NOT active.
    """
    val = await (await _get_redis_cmd()).get(key)
    if not val:
        return False
    return str(val).strip().lower() not in _KILL_INACTIVE_VALUES


async def _is_kill_flag_set(mission_id: int) -> bool:
    """Return True only if the kill flag is explicitly set to an active value."""
    return await _redis_flag_active(f"raven:mission:kill:{mission_id}")


async def _await_mission_resume(
    mission_id: int,
    stream_event: Callable[[str, str], Awaitable[Any]],
) -> bool:
    """Block until a mission is resumed (or killed) via Redis pub/sub.

    Returns ``True`` if a KILL arrived while paused (caller should terminate),
    ``False`` once the mission is resumed (or was never actually paused).

    Uses subscribe-then-recheck to avoid a lost-wakeup race: the pause key is
    re-read *after* subscribing, so a RESUME published before subscribe is
    observed, and any signal published after subscribe is delivered by listen().

    ``stream_event`` is passed in (it is a closure nested inside AgentLoop).
    """
    pause_key = f"raven:mission:pause:{mission_id}"
    kill_key = f"raven:mission:kill:{mission_id}"
    if not await _redis_flag_active(pause_key):
        return False

    r_ps = redis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r_ps.pubsub()
    await pubsub.subscribe(pause_key, kill_key)
    try:
        # Re-check after subscribe to avoid lost-wakeup race
        if not await _redis_flag_active(pause_key):
            return False
        log.warning(f"[AgentLoop] MISSION PAUSED for {mission_id}. Waiting for resume signal.")
        await stream_event("system", "Mission paused — waiting for LLM access to become available.")
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            if message.get("channel") == kill_key or message.get("data") == "KILL":
                log.warning(f"[AgentLoop] MISSION KILL SIGNAL RECEIVED while paused for {mission_id}. Terminating.")
                await stream_event("system", "Mission terminated by user.")
                return True
            if message.get("data") == "RESUMED":
                break
        log.info(f"[AgentLoop] Mission {mission_id} resumed.")
        await stream_event("system", "Mission resumed.")
        return False
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(pause_key, kill_key)
        with contextlib.suppress(Exception):
            await pubsub.close()
        with contextlib.suppress(Exception):
            await r_ps.close()

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


async def resolve_mission_workspace(
    user_id: str,
    assigned_workspace_id: str | None = None,
    query: str | None = None,
) -> dict | None:
    """Resolve (or create) the workspace an agentic Raven mission should run in.

    - If ``assigned_workspace_id`` is given, resolve/bootstrap and return it.
    - Otherwise, if the mission is *system maintenance* on SharedLLM itself
      (editing/fixing its own code or logs), return the user's **Default
      Workspace** so Raven works there.
    - Otherwise (a project / new-build mission) return ``None`` — do NOT fall back
      to the Default Workspace. Raven must acquire a dedicated workspace via
      ``WorkspaceCreateRequest`` (protocol Step 0). This keeps the Default
      Workspace reserved for system maintenance only.

    Workspaces are just sandboxed directories for running commands; they do NOT
    require a git repository unless one is explicitly requested (``create_repo``).
    """
    async def _resolve_one(wid: str) -> dict | None:
        try:
            async with shared_http_client() as client:
                res = await client.post(
                    f"{WORKSPACE_RUNTIME_SVC}/workspace/resolve",
                    json={"workspace_id": wid, "user_context": {"user": user_id, "is_admin": False}},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10.0),
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
            async with shared_http_client() as client:
                boot = await client.post(
                    f"{WORKSPACE_RUNTIME_SVC}/workspaces/bootstrap",
                    json={"workspace_id": assigned_workspace_id, "rag_user": user_id},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=20.0),
                )
                if boot.status == 200:
                    return (await boot.json()).get("workspace")
        except Exception as e:
            log.warning(f"[workspace] bootstrap {assigned_workspace_id} failed: {e}")
        return None

    # No assigned workspace. System-maintenance missions (fixing SharedLLM's own
    # code/logs) run in the user's Default Workspace. Everything else (building or
    # creating a new project) must NOT use the Default Workspace — return None so
    # Raven is forced to acquire a dedicated sandbox via WorkspaceCreateRequest.
    if is_system_maintenance_task(query):
        try:
            async with shared_http_client() as client:
                lst = await client.get(
                    f"{WORKSPACE_RUNTIME_SVC}/workspaces",
                    params={"rag_user": user_id},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10.0),
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

    # Project / new-build mission: no workspace pre-assigned. Returning None (rather
    # than the Default Workspace) forces Raven to create its own dedicated workspace
    # and prevents it from operating in the shared Default Workspace.
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
        async with shared_http_client() as client:
            res = await client.post(
                f"{WORKSPACE_RUNTIME_SVC}/workspace/resolve",
                json={"workspace_id": ws_id, "user_context": {"user": "default", "is_admin": True}},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if res.status == 200:
                ws = (await res.json()).get("workspace") or {}
                if not ws.get("is_default") and str(ws.get("scope") or "").lower() != "system":
                    return False
    except Exception:
        pass
    return True


def _extract_repo_name_from_cmd(cmd: str | None) -> str | None:
    """Extract the repository name from a ``gh repo create <name> [flags]`` shell
    command (Raven usually runs ``gh`` via the shell, not the dedicated tool)."""
    if not cmd:
        return None
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    value_flags = {
        "--source", "-s", "--description", "-d", "--homepage", "-h",
        "--team", "-t", "--template", "--license", "-l", "--gitignore",
    }
    for i, t in enumerate(toks):
        if t == "create" and i >= 1 and toks[i - 1] == "repo":
            j = i + 1
            while j < len(toks):
                a = toks[j]
                if a.startswith("-"):
                    if a in value_flags and j + 1 < len(toks):
                        j += 2
                        continue
                    j += 1
                    continue
                return a
            return None
    return None


async def _shell_out(workspace_id: str, uc: dict, command: str) -> str | None:
    """Run a shell command in the workspace and return trimmed stdout, or None."""
    try:
        async with shared_http_client() as client:
            res = await client.post(
                f"{EXECUTION_SVC}/execute/workspace_shell",
                json={"workspace_id": workspace_id, "command": command, "user_context": uc},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=30.0),
            )
            if res.status != 200:
                return None
            out = (await res.json()).get("message", "") or ""
            return "\n".join(ln.strip() for ln in out.splitlines() if ln.strip())
    except Exception:
        return None


async def _autowire_created_repo(workspace_id: str, creds: ResolvedCredentials, repo_cmd: str | None = None) -> None:
    """After a successful ``gh repo create`` inside a workspace, fetch the new
    remote and bind it to the workspace settings (repo_url / git_remote /
    default_branch). This guarantees the workspace's "Source Repository" is
    populated even if the model forgets to call WorkspaceSettingsUpdateRequest.

    The repo URL is resolved from the ``gh repo create <name>`` command itself
    (via ``gh repo view <name> --json url``), NOT from ``git remote get-url
    origin`` — at ``gh repo create`` time the workspace git remote has NOT been
    added yet (that happens later, right before ``git push``), so reading the
    remote would return nothing and the bind would silently no-op, leaving every
    later ``git push`` refused by the per-workspace guardrail.

    Best-effort: any failure is logged and ignored so it never blocks the mission.
    """
    try:
        async with shared_http_client() as client:
            res = await client.post(
                f"{WORKSPACE_RUNTIME_SVC}/workspace/resolve",
                json={"workspace_id": workspace_id, "user_context": {"user": creds.user, "is_admin": creds.is_admin}},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if res.status != 200:
                return
            ws = (await res.json()).get("workspace") or {}
            if ws.get("repo_url"):
                return  # already wired
        uc = {
            "user": creds.user,
            "is_admin": creds.is_admin,
            "api_key": creds.api_key,
            "github_token": creds.github_token,
            "git_token": creds.git_token,
        }

        url: str | None = None
        name = _extract_repo_name_from_cmd(repo_cmd)
        if name:
            # Resolve the freshly-created repo's HTTPS URL via gh (the repo now
            # exists on GitHub). Retry once to absorb GitHub propagation lag.
            out = await _shell_out(workspace_id, uc, f"gh repo view {name} --json url -q .url")
            if not out:
                import asyncio
                await asyncio.sleep(2.0)
                out = await _shell_out(workspace_id, uc, f"gh repo view {name} --json url -q .url")
            if out:
                url = out.strip().strip('"').strip("'")
        if not url:
            # Fallback: read whatever remote is configured now.
            out = await _shell_out(workspace_id, uc, "git remote get-url origin 2>/dev/null")
            if out:
                url = out.strip().strip('"').strip("'")

        if not url:
            return
        async with shared_http_client() as client:
            await client.patch(
                f"{WORKSPACE_RUNTIME_SVC}/workspaces/{workspace_id}",
                json={"repo_url": url, "git_remote": "origin", "default_branch": "main"},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=15.0),
            )
        log.info(f"[AgentLoop] Auto-wired repo {url} to workspace {workspace_id}")
    except Exception as e:
        log.warning(f"[AgentLoop] Auto-wire repo failed for {workspace_id}: {e}")


def normalize_audit_log(audit_log: list[dict]) -> list[dict]:
    normalized = []

    current_action = None
    current_action_payload = None

    for ev in audit_log:
        ev_type = ev.get("type")
        ev_data = ev.get("data")
        timestamp = ev.get("timestamp")

        if ev_type == "action":
            tool_name = str(ev_data).replace("Executing Tool: ", "").strip()
            current_action = tool_name

        elif ev_type == "action_payload":
            try:
                current_action_payload = json.loads(ev_data) if isinstance(ev_data, str) else ev_data
            except Exception:
                current_action_payload = ev_data

        elif ev_type in ("result_success", "result_error"):
            summary_msg = f"Executed {current_action}"

            if current_action == "workspacefilewriterequest" and isinstance(current_action_payload, dict):
                path = current_action_payload.get("path") or current_action_payload.get("relative_path") or "file"
                content = current_action_payload.get("content") or ""
                lines_count = len(str(content).splitlines())
                summary_msg = f"Wrote {path} ({lines_count} lines)"

            elif current_action == "workspaceshellrequest" and isinstance(current_action_payload, dict):
                cmd = current_action_payload.get("command") or ""
                summary_msg = f"Shell command: `{cmd[:60]}`"

            elif current_action == "workspacegitcommitrequest" and isinstance(current_action_payload, dict):
                msg = current_action_payload.get("message") or ""
                summary_msg = f"Git commit: \"{msg[:50]}\""

            elif current_action == "workspacegitpushrequest":
                summary_msg = "Git push"

            elif current_action == "workspacegitpullrequest":
                summary_msg = "Git pull"

            elif current_action == "workspacesearchrequest" and isinstance(current_action_payload, dict):
                q = current_action_payload.get("query") or ""
                summary_msg = f"Search Query: `{q}`"

            is_success = (ev_type == "result_success")
            res_str = str(ev_data).strip()
            if not is_success:
                summary_msg += f" (failed: {res_str[:120]})"
            else:
                try:
                    res_json = json.loads(res_str)
                    if isinstance(res_json, dict):
                        if "lint" in res_json:
                            passed = all(item.get("passed", True) for item in res_json["lint"])
                            summary_msg += f" (Lint: {'passed' if passed else 'failed'})"
                        elif "pytest" in res_json:
                            passed = res_json["pytest"].get("passed", False)
                            summary_msg += f" (Tests: {'passed' if passed else 'failed'})"
                except Exception:
                    pass

            normalized.append({
                "type": ev_type,
                "data": summary_msg,
                "timestamp": timestamp,
                "raw_type": ev_type,
                "raw_data": res_str[:400],
                "tool": current_action,
                "payload": str(current_action_payload)[:400] if current_action_payload else None
            })

            current_action = None
            current_action_payload = None

        elif ev_type == "system":
            normalized.append({
                "type": "system",
                "data": ev_data,
                "timestamp": timestamp
            })

        elif ev_type == "reasoning":
            pass

    return normalized


async def AgentLoop(query: str, selected_model: str, full_system: str, short_term: list, rag_user: str, creds: ResolvedCredentials, mission_id: int | None = None, rag_context: str = "", show_thinking: bool = False, workspace_id: str | None = None, history_log: str | None = None) -> Any:
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

    async def _record_loop_probe(iter_num: int, goal: str, outcome: str, directive: str) -> None:
        """Persist a structured, pullable record of a detected no-progress loop
        to raven:mission:loopstate:{mission_id} so an operator (or Raven on a
        resumed run) can inspect exactly what was looping and what the last error
        was. Best-effort: failures are logged, never raised."""
        if not mission_id:
            return
        try:
            import json as _json
            import time as _time
            r = await _get_redis_cmd()
            rec = {
                "type": "loop_probe",
                "iteration": iter_num,
                "goal": goal,
                "outcome_fingerprint": outcome,
                "directive": directive,
                "timestamp": _time.time(),
            }
            from collections.abc import Awaitable
            from typing import cast
            res1 = r.rpush(f"raven:mission:loopstate:{mission_id}", _json.dumps(rec))
            await cast(Awaitable[Any], res1)
            res2 = r.expire(f"raven:mission:loopstate:{mission_id}", 86400)
            await cast(Awaitable[Any], res2)
        except Exception as e:
            log.warning(f"[AgentLoop] loop-probe record failed: {e}")

    # 0. Resolve the workspace this agentic mission runs in.
    # If the task assigned an existing workspace we use it; otherwise we resolve (or
    # fall back to) a workspace. Raven itself decides — via the WorkspaceCreateRequest
    # tool described in its protocol — whether a mission needs its own dedicated
    # sandbox, or should reuse an existing one. The gateway only supplies the means.
    try:
        _ws = await resolve_mission_workspace(rag_user, workspace_id, query=query)
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
                f"Write files relative to this path. In file tools, set `relative_path` to the "
                f"file's path RELATIVE to the workspace root (e.g. 'game.py' or 'src/main.py'). "
                f"Do NOT use the `local_path` field (e.g. 'users/default/...') as a file path — "
                f"it is an internal identifier, not a location on disk. Shell commands already "
                f"run inside this workspace directory — pass an empty `cwd` (or '.'), and do NOT "
                f"'cd' into a 'users/...' path."
            )
    else:
        # No workspace was pre-assigned (a project / new-build mission). Inject an
        # explicit note so Raven follows protocol Step 0 and acquires a dedicated
        # workspace via WorkspaceCreateRequest before any file/shell/git operation
        # — and never operates in the Default Workspace (reserved for maintenance).
        full_system += (
            "\n\n[WORKSPACE NOTE]\n"
            "No workspace is pre-assigned to this mission. Per protocol Step 0, your VERY "
            "FIRST tool call MUST be WorkspaceCreateRequest to acquire a dedicated workspace "
            "(e.g. {\"@type\": \"WorkspaceCreateRequest\", \"id\": \"raven-<project>\", "
            "\"display_name\": \"...\"}). Capture the returned id and pass it as "
            "`workspace_id` in EVERY following WorkspaceFileWriteRequest / WorkspaceShellRequest "
            "/ WorkspaceSettingsUpdateRequest. Do NOT operate in the Default Workspace — it is "
            "reserved for system maintenance only."
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

    # 2. Resolve Role-Based Model (Coder/Assistant) if selected_model is a generic alias
    original_model = selected_model
    # Role aliases that should resolve to a configured model rather than being
    # treated as an explicit model name. "coding" (the settings key) and synonyms
    # all map to coding_model; assistant/chat map to assistant_model.
    ROLE_ASSISTANT = {"assistant", "chat"}
    ROLE_CODER = {"coder", "coding", "code", "repair", "raven", "dev", "developer", "technical"}
    if selected_model in ROLE_ASSISTANT or selected_model in ROLE_CODER:
        tech_keywords = ["coder", "fix", "repair", "audit", "mission", "raven", "development", "git", "workspace"]
        is_technical = (selected_model in ROLE_CODER) or any(word in query.lower() for word in tech_keywords)

        selected_model = settings.get("coding_model") or "" if is_technical else settings.get("assistant_model") or ""
        log.info(f"[AgentLoop] Model resolved from '{original_model}' to '{selected_model}' (is_technical={is_technical})")
    else:
        log.info(f"[AgentLoop] Using explicit model: '{selected_model}' (not a role alias)")

    # Fail fast if config is missing or invalid
    if not selected_model:
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


    MAX_TOOL_ITERATIONS = 60  # ceiling; overridable per-deployment via the
    # raven_max_iterations dynamic LLM setting. The loop also self-terminates on
    # stagnation (repeated failures) or on a detected action loop, so a higher
    # ceiling is safe — long missions are allowed as long as they keep making
    # distinct progress.
    # Consecutive textual (no-tool-call) responses tolerated before the loop
    # decides the agent is stuck and terminates. Without this, a single plan-as-
    # text reply after the first tool call ends the whole mission prematurely.
    MAX_IDLE_NUDGES = 6
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
    # (goal_sig, outcome_sig) for shell runs only — feeds detect_no_progress so
    # exit-0 loops (e.g. a --selftest that crashes before printing GAME_OK) are
    # caught even when the literal command string varies between iterations.
    _recent_shell_runs: list[tuple[str, str]] = []
    _written_files: set[str] = set()
    _verified_files: set[str] = set()
    _verification_nudge_sent = False
    _stagnation_nudge_sent = False
    _loop_nudge_sent = False
    _shell_loop_nudge_sent = False
    _shell_loop_diversify_sent = False
    _consecutive_no_tool = 0

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
            r_cp = await _get_redis_cmd()
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

    # Iteration ceiling — long missions allowed, but bounded and guarded by
    # stagnation/loop detection below. Overridable per-deployment.
    try:
        max_iterations = int(str((settings or {}).get("raven_max_iterations", MAX_TOOL_ITERATIONS)).strip())
    except (ValueError, TypeError):
        max_iterations = MAX_TOOL_ITERATIONS
    if max_iterations < 1:
        max_iterations = MAX_TOOL_ITERATIONS

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
            r_cp = await _get_redis_cmd()
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
            r_cp = await _get_redis_cmd()
            await r_cp.delete(f"raven:checkpoint:{mission_id}")
        except Exception as e:
            log.warning(f"[AgentLoop] Failed to clear checkpoint for mission {mission_id}: {e}")

    # Reconstruct history if resuming/refining from output_log or history_log
    prior_conversation_turns = []
    if mission_id:
        try:
            output_log_raw = history_log
            if not output_log_raw:
                async with shared_http_client() as ws_client:
                    resp = await ws_client.get(
                        f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    )
                    if resp.status == 200:
                        m_data = await resp.json()
                        output_log_raw = m_data.get("output_log")

            if output_log_raw and isinstance(output_log_raw, str):
                try:
                    audit_events = json.loads(output_log_raw)
                except Exception:
                    audit_events = []
                if audit_events:
                    log.info(f"[AgentLoop] Found existing output_log with {len(audit_events)} events for mission {mission_id}. Reconstructing history.")

                    current_tool = None
                    current_payload = None

                    for ev in audit_events:
                        ev_type = ev.get("raw_type") or ev.get("type")
                        ev_data = ev.get("raw_data") or ev.get("data")

                        if ev_type == "action":
                            current_tool = str(ev_data).replace("Executing Tool: ", "").strip()
                        elif ev_type == "action_payload":
                            payload_val = ev.get("payload") or ev_data
                            try:
                                current_payload = json.loads(payload_val) if isinstance(payload_val, str) else payload_val
                            except Exception:
                                current_payload = payload_val
                        elif ev_type in ("result_success", "result_error"):
                            tool_name = ev.get("tool") or current_tool
                            if tool_name:
                                tool_json = {"action": tool_name, "payload": current_payload}
                                prior_conversation_turns.append({"role": "assistant", "content": json.dumps(tool_json)})
                                prior_conversation_turns.append({"role": "user", "content": f"LAST TOOL RESULT:\n{ev_data}"})
                                # Also reconstruct action_log step
                                step_num = len(action_log) + 1
                                action_log.append(f"Step {step_num}: {tool_name} -> {str(ev_data)[:200]}")
                            current_tool = None
                            current_payload = None

                    # Cap reconstructed history to the last 20 tool/result pairs (40 turns)
                    if len(prior_conversation_turns) > 40:
                        prior_conversation_turns = prior_conversation_turns[-40:]
                    # Cap action_log too
                    if len(action_log) > 20:
                        action_log = action_log[-20:]
        except Exception as e:
            log.warning(f"[AgentLoop] Failed to reconstruct mission history: {e}")

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
    if prior_conversation_turns:
        conversation.extend(prior_conversation_turns)

    for agent_iter in range(start_iteration, max_iterations):
        iter_num = agent_iter + 1
        iter_start = asyncio.get_event_loop().time()

        # --- HARD TIMEOUT CHECK ---
        elapsed_total = iter_start - loop_start
        if elapsed_total > RAVEN_MAX_TOTAL_SECONDS:
            log.error(f"[AgentLoop] HARD TIMEOUT after {elapsed_total:.0f}s at iteration {iter_num}")
            ans = f"ERROR: Raven job exceeded time limit of {RAVEN_MAX_TOTAL_SECONDS}s. Partial result: {ans or 'No output yet'}"
            await _clear_checkpoint()
            break

        # --- HARD KILL SWITCH (Redis pub/sub) ---
        if mission_id:
            try:
                if await _is_kill_flag_set(mission_id):
                    log.warning(f"[AgentLoop] MISSION KILL SIGNAL RECEIVED for {mission_id}. Terminating.")
                    await stream_event("system", "Mission terminated by user.")
                    await _clear_checkpoint()
                    return "MISSION TERMINATED: User requested cancellation via control plane."
            except Exception as e:
                log.error(f"[AgentLoop] Error checking mission kill flag: {e}")

        # --- PAUSE FOR LLM ACCESS (Redis pub/sub; replaces 5s poll loop) ---
        if mission_id:
            try:
                killed_while_paused = await _await_mission_resume(mission_id, stream_event)
                if killed_while_paused:
                    await _clear_checkpoint()
                    return "MISSION TERMINATED: User requested cancellation via control plane."
            except Exception as e:
                log.error(f"[AgentLoop] Error in mission pause/resume handling: {e}")

        await stream_event("system", f"Agent loop iteration {iter_num}/{max_iterations} started.")
        log.info(f"[AgentLoop] Iteration {iter_num}/{max_iterations} | total elapsed {elapsed_total:.0f}s")

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
                    data = await execute_inference_with_kill(
                        provider,
                        selected_model,
                        cast(list, ollama_payload["messages"]),
                        inference_options,
                        mission_id=mission_id,
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
                except MissionKilledError:
                    raise  # Never retry a user-requested cancel
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
        except MissionKilledError:
            heartbeat_stop.set()
            await hb_task
            log.warning(f"[AgentLoop] Mission {mission_id} terminated by user during iter {agent_iter + 1}.")
            await stream_event("system", "Mission terminated by user.")
            await _clear_checkpoint()
            return "MISSION TERMINATED: User requested cancellation via control plane."
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
            _consecutive_no_tool += 1

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
                            f"but have NOT verified them. Before finishing, run the standard STATIC CHECK "
                            f"for the language you wrote and confirm it PASSES — do NOT consider the "
                            f"mission complete until it is clean:\n"
                            f"  - Python:        `ruff check .` (+ `python -m pyflakes .`). Fix EVERY issue, "
                            f"especially `F821`/`F405` 'undefined name' — that almost always means a MISSING "
                            f"IMPORT (add `from raylib import *` / `import raylib as rl`, or the right module). "
                            f"A static check catches these WITHOUT running the program; a runtime NameError "
                            f"means you shipped broken code.\n"
                            f"  - JS/TS:         `eslint .` (TS also: `tsc --noEmit`).\n"
                            f"  - Shell:         `shellcheck`.\n"
                            f"  - Go:            `gofmt -l .` + `go vet ./...`.\n"
                            f"  - Rust:          `rustfmt --check` (+ `cargo check`).\n"
                            f"  - C/C++:         `gcc -fsyntax-only` / `g++ -fsyntax-only`.\n"
                            f"  - Java:          `javac -d /dev/null File.java`.\n"
                            f"  - Ruby/Lua/PHP:  `ruby -c` / `luac -p` / `php -l`.\n"
                            f"  - JSON/YAML:     `python -m json.tool` / `yamllint`.\n"
                            f"Then run the real test/selftest (e.g. `pytest`, `npm test`, `--selftest`)."
                        )
                        continue
                # The mission is multi-step; a plan-as-text reply is NOT "done".
                # Nudge the agent to keep executing tool calls instead of ending
                # the loop after the first one. Only give up once it has stalled
                # for MAX_IDLE_NUDGES consecutive no-tool replies (runaway guard).
                if _consecutive_no_tool >= MAX_IDLE_NUDGES:
                    log.error(f"[AgentLoop] {_consecutive_no_tool} consecutive no-tool replies after {successful_tool_calls} tool call(s). Terminating to prevent runaway.")
                    ans = "ERROR: Agent stalled — produced no tool calls for several turns after making progress. Last response: " + (ans[:200] if ans else "empty")
                    await _clear_checkpoint()
                    break
                log.warning(f"[AgentLoop] Textual reply after progress (idle {_consecutive_no_tool}/{MAX_IDLE_NUDGES}); nudging to continue with tool calls.")
                action_log.append(
                    f"ITERATION {iter_num}: You described the next step but did not emit a tool "
                    f"call. The mission is NOT complete — you must keep executing it with tool "
                    f"calls (e.g. WorkspaceShellRequest to run `gh repo create`, "
                    f"WorkspaceFileWriteRequest to write files, then git add/commit/push). Emit "
                    f"the next concrete tool call now; do not stop at a description."
                )
                continue

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
                "recall": "ravenrecallrequest",
                "ravenrecall": "ravenrecallrequest",
                "missionhistory": "ravenrecallrequest",
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
                    (r'.*workspace.*create.*', "workspacecreaterequest"),
                    (r'.*workspace.*settings.*update.*', "workspacesettingsupdaterequest"),
                    (r'.*workspace.*setting.*', "workspacesettingsupdaterequest"),
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
                    (r'.*raven.*recall.*', "ravenrecallrequest"),
                    (r'.*mission.*history.*', "ravenrecallrequest"),
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
                        "Workspace Tools": ["workspacefilereadrequest", "workspacefilewriterequest", "workspacefilepatchrequest", "workspacelintrequest", "workspacesearchrequest", "workspaceshellrequest", "workspacebootstraprequest", "workspacecreaterequest", "workspacesettingsupdaterequest"],
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
                "imagegenerationrequest": (ALPACA_SD_URL, "/v1/images/generations"),
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
                # Defined unconditionally so it is also in scope for the post-call
                # auto-wire check at the end of this block (a first Create Workspace
                # call enters with `workspace_id is None`, so a guarded definition here
                # would raise NameError later).
                _ws_actions = {
                    "workspacefilereadrequest", "workspacefilewriterequest",
                    "workspacefilepatchrequest", "workspaceshellrequest",
                    "workspacesearchrequest", "workspacelintrequest",
                    "gitoperationrequest", "workspacebootstraprequest",
                    "workspacesettingsupdaterequest",
                }
                if workspace_id and isinstance(payload, dict) and lookup_action in _ws_actions:
                    payload["workspace_id"] = workspace_id

                # GUARDRAIL: creating a NEW repository must happen in a dedicated
                # workspace Raven acquired for itself — never the default/shared/system
                # workspace. If Raven attempts `gh repo create` (or similar) there, fail
                # loudly so it creates a workspace first instead of polluting a shared one.
                _skip_post = False
                _creates_repo = False

                # ImageGenerationRequest: generate an image via the alpaca Stable Diffusion
                # backend, then (optionally) save it straight into the workspace as a binary
                # file. The SD backend only understands prompt/model/size/n, so we strip the
                # workspace fields, call SD, decode the returned PNG, and write it via the
                # workspace_runtime binary file endpoint (content_base64). This keeps the
                # (large) image bytes server-side instead of round-tripping through the model.
                if lookup_action == "imagegenerationrequest" and isinstance(payload, dict):
                    _img_prompt = payload.get("prompt")
                    _img_model = payload.get("model")
                    _img_size = str(payload.get("size") or "512x512")
                    try:
                        _img_n = int(payload.get("n") or 1)
                    except (TypeError, ValueError):
                        _img_n = 1
                    _img_ws = payload.get("workspace_id") or workspace_id
                    _img_path = payload.get("relative_path")
                    if not _img_prompt:
                        exec_data = {"status": "ERROR", "message": "ImageGenerationRequest requires a 'prompt'."}
                    else:
                        try:
                            async with shared_http_client() as _c:
                                _sd = await _c.post(
                                    f"{ALPACA_SD_URL}/v1/images/generations",
                                    json={"prompt": _img_prompt, "model": _img_model, "size": _img_size, "n": _img_n},
                                    timeout=aiohttp.ClientTimeout(total=180.0),
                                )
                                _sd_data = await _sd.json()
                            _items = (_sd_data or {}).get("data") or []
                            _b64 = _items[0].get("b64_json") if _items else None
                            if not _b64 and _items and _items[0].get("url"):
                                async with shared_http_client() as _c2:
                                    _img_r = await _c2.get(_items[0]["url"])
                                    _b64 = base64.b64encode(await _img_r.read()).decode()
                            if not _b64:
                                exec_data = {"status": "ERROR", "message": f"Image generation returned no image: {_sd_data}"}
                            elif _img_ws and _img_path:
                                async with shared_http_client() as _c3:
                                    _save = await _c3.post(
                                        f"{WORKSPACE_RUNTIME_SVC}/files/write",
                                        json={
                                            "workspace_id": _img_ws,
                                            "relative_path": _img_path,
                                            "content_base64": _b64,
                                            "create_parents": True,
                                        },
                                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                                        timeout=aiohttp.ClientTimeout(total=30.0),
                                    )
                                    _save_data = await _save.json() if _save.status == 200 else {"status": "ERROR", "detail": f"save status {_save.status}"}
                                exec_data = {
                                    "status": "SUCCESS",
                                    "message": f"Generated image saved to {_img_path} ({len(_b64)} base64 bytes).",
                                    "detail": _save_data,
                                }
                            else:
                                exec_data = {
                                    "status": "SUCCESS",
                                    "message": f"Image generated ({len(_b64)} base64 bytes). Provide workspace_id + relative_path to save it to the workspace.",
                                    "detail": {"b64_len": len(_b64)},
                                }
                        except Exception as _e:
                            exec_data = {"status": "ERROR", "message": f"Image generation failed: {_e}"}
                    _skip_post = True

                # RavenBuildToolRequest: let Raven discover an existing tool,
                # chain existing tools together, or scaffold + run a brand-new tool.
                # Decision logic lives in tool_builder.decide() (existing -> chain -> build).
                if lookup_action == "ravenbuildtoolrequest" and isinstance(payload, dict):
                    _cap = str(
                        payload.get("capability")
                        or payload.get("capability_description")
                        or payload.get("description")
                        or payload.get("task")
                        or ""
                    ).strip()
                    try:
                        from services.gateway.tool_builder import decide, scaffold_source
                        _decision = decide(_cap)
                        if _decision.get("decision") == "build":
                            _slug = _decision.get("slug")
                            _rel = f"tools/{_slug}.py"
                            _src = scaffold_source(_cap, _slug)
                            # Write the scaffold into the mission workspace if one exists.
                            _bws = workspace_id or payload.get("workspace_id")
                            if _bws:
                                async with shared_http_client() as _bc:
                                    _w = await _bc.post(
                                        f"{WORKSPACE_RUNTIME_SVC}/files/write",
                                        json={
                                            "workspace_id": _bws,
                                            "relative_path": _rel,
                                            "content": _src,
                                            "create_parents": True,
                                        },
                                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                                        timeout=aiohttp.ClientTimeout(total=30.0),
                                    )
                                    if _w.status != 200:
                                        _decision["write_status"] = f"scaffold write failed: {_w.status}"
                                _decision["tool_path"] = _rel
                            else:
                                _decision["tool_path"] = None
                                _decision.setdefault(
                                    "write_status",
                                    "No workspace assigned yet; create one via WorkspaceCreateRequest, then re-issue this request to write the scaffold.",
                                )
                            exec_data = {
                                "status": "SUCCESS",
                                "decision": _decision,
                                "message": "Build decision computed. " + _decision.get("instruction", ""),
                            }
                        else:
                            exec_data = {
                                "status": "SUCCESS",
                                "decision": _decision,
                                "message": "Use the existing/chain tool(s) listed in 'decision'.",
                            }
                        _skip_post = True
                    except Exception as _e:
                        exec_data = {"status": "ERROR", "message": f"RavenBuildToolRequest failed: {_e}"}
                        _skip_post = True

                # RavenRecallRequest: let Raven introspect its OWN mission history so
                # it can self-diagnose loops (e.g. "I've run --selftest N times; what
                # succeeded before and is the error identical?"). Reads the pullable
                # raven:mission:history:{id} (and raven:mission:loopstate:{id}) we
                # already write every step, returning a capped, scoped summary — never
                # the raw firehose, which would blow the context window.
                if lookup_action == "ravenrecallrequest" and isinstance(payload, dict):
                    try:
                        _rk = max(1, min(int(payload.get("limit") or 15), 50))
                        _only = str(payload.get("only") or "").lower()
                        _rid = payload.get("mission_id") or mission_id
                        if not _rid:
                            exec_data = {"status": "ERROR", "message": "RavenRecallRequest requires an active mission_id."}
                        else:
                            _rr = redis.from_url(REDIS_URL, decode_responses=True)
                            _raw = await _rr.lrange(f"raven:mission:history:{_rid}", -60, -1)
                            _recall_steps: list[dict] = []
                            _recall_cur: dict | None = None
                            for _ent in _raw:
                                try:
                                    _o = json.loads(_ent)
                                except Exception:
                                    continue
                                _t = _o.get("type")
                                _d = _o.get("data", "") or ""
                                if _t == "action":
                                    if _recall_cur:
                                        _recall_steps.append(_recall_cur)
                                    _recall_cur = {"tool": str(_d).replace("Executing Tool: ", "").strip()}
                                elif _t == "action_payload":
                                    try:
                                        _p = json.loads(_d)
                                    except Exception:
                                        _p = {}
                                    if not isinstance(_p, dict):
                                        _p = {}
                                    if _recall_cur is None:
                                        _recall_cur = {}
                                    if "command" in _p:
                                        _recall_cur["command"] = _p["command"]
                                    _fp = _p.get("file_path") or _p.get("path") or _p.get("relative_path")
                                    if _fp:
                                        _recall_cur["file"] = _fp
                                    if "action" in _p:
                                        _recall_cur["git_action"] = _p["action"]
                                elif _t in ("result_success", "result_error"):
                                    if _recall_cur is None:
                                        _recall_cur = {}
                                    _recall_cur["status"] = "ERROR" if _t == "result_error" else "SUCCESS"
                                    _recall_cur["outcome"] = str(_d)[:300]
                                    _recall_steps.append(_recall_cur)
                                    _recall_cur = None
                            if _recall_cur:
                                _recall_steps.append(_recall_cur)
                            if _only == "shell":
                                _recall_steps = [s for s in _recall_steps if "command" in s or s.get("tool") == "workspaceshellrequest"]
                            elif _only == "failed":
                                _recall_steps = [s for s in _recall_steps if s.get("status") == "ERROR"]
                            if _only == "loop":
                                try:
                                    _lp = await _rr.lrange(f"raven:mission:loopstate:{_rid}", -20, -1)
                                    for _l in _lp:
                                        try:
                                            _lo = json.loads(_l)
                                        except Exception:
                                            continue
                                        _recall_steps.insert(0, {
                                            "tool": "LOOP_PROBE",
                                            "status": "LOOP",
                                            "outcome": _lo.get("directive", ""),
                                            "goal": _lo.get("goal", ""),
                                        })
                                except Exception:
                                    pass
                            _recall_steps = _recall_steps[-_rk:]
                            _summary = "\n".join(
                                f"- {s.get('tool', '?')}"
                                + (f" cmd={s['command']}" if s.get("command") else "")
                                + (f" file={s['file']}" if s.get("file") else "")
                                + f" -> {s.get('status', '?')}"
                                + (f" | {s['outcome']}" if s.get("outcome") else "")
                                for s in _recall_steps
                            ) or "(no matching history)"
                            exec_data = {
                                "status": "SUCCESS",
                                "message": f"Last {len(_recall_steps)} mission step(s){(' [' + _only + ']') if _only else ''}:\n{_summary}",
                                "history": _recall_steps,
                                "count": len(_recall_steps),
                            }
                    except Exception as _e:
                        exec_data = {"status": "ERROR", "message": f"RavenRecallRequest failed: {_e}"}
                    _skip_post = True

                # GUARD: project missions start with NO assigned workspace. Block every
                # workspace-scoped operation until Raven acquires a dedicated workspace via
                # WorkspaceCreateRequest, so it never silently operates in the Default
                # Workspace or WORKSPACE_ROOT. Only the create request itself is allowed.
                if workspace_id is None and lookup_action in WORKSPACE_TOOL_ACTIONS and lookup_action != "workspacecreaterequest":
                    exec_data = {
                        "status": "ERROR",
                        "message": (
                            "No workspace is assigned yet. Per protocol Step 0, your FIRST "
                            "action must be WorkspaceCreateRequest to acquire a dedicated "
                            "workspace, e.g. {\"@type\": \"WorkspaceCreateRequest\", "
                            "\"id\": \"raven-<project>\", \"display_name\": \"...\"}. Capture "
                            "the returned id and pass it as `workspace_id` in EVERY following "
                            "WorkspaceFileWriteRequest / WorkspaceShellRequest. Do NOT operate "
                            "in the Default Workspace."
                        ),
                    }
                    _skip_post = True
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
                    async with shared_http_client() as client:
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
                        resp = await client.post(f"{svc_base}{endpoint}", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=120.0))
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
                                # Fallback: even if the runtime returned an unexpected
                                # shape (e.g. idempotent "already existed" with a
                                # different envelope), adopt the id Raven requested so
                                # it always has a working sandbox to operate out of.
                                if not _created and isinstance(payload, dict):
                                    _created = payload.get("id") or payload.get("workspace_id")
                                if _created:
                                    workspace_id = str(_created)
                                    created_workspaces.add(workspace_id)
                                    log.info(f"[AgentLoop] Adopted newly created workspace: {workspace_id}")
                                    if mission_id:
                                        try:
                                            async with shared_http_client() as ws_client:
                                                await ws_client.patch(
                                                    f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                                                    json={"workspace_id": workspace_id},
                                                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                                                    timeout=aiohttp.ClientTimeout(total=5.0),
                                                )
                                                log.info(f"[AgentLoop] Saved workspace_id {workspace_id} to mission {mission_id}")
                                        except Exception as patch_ws_e:
                                            log.warning(f"[AgentLoop] Failed to save workspace_id to mission {mission_id}: {patch_ws_e}")

                            # Auto-wire the new repo to the workspace settings after a
                            # successful `gh repo create` (best-effort, model-independent),
                            # so the workspace's "Source Repository" is populated even if
                            # the model forgets to call WorkspaceSettingsUpdateRequest.
                            if (
                                workspace_id
                                and isinstance(exec_data, dict)
                                and exec_data.get("status") != "ERROR"
                                and lookup_action in _ws_actions
                            ):
                                _repo_cmd = " ".join(str(payload.get(k, "")) for k in ("command", "args", "action", "repo_url"))
                                if "gh repo create" in _repo_cmd or "repo create" in _repo_cmd or "repo_create" in _repo_cmd:
                                    await _autowire_created_repo(workspace_id, creds, _repo_cmd)

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
                    # Track successful tool executions (non-ERROR responses).
                    # NOTE: "LINT_ERRORS" is NOT a success — a file that fails the
                    # post-write lint (e.g. ruff F821 undefined name, syntax error)
                    # must NOT be counted as a verified/completed step, otherwise
                    # Raven can push code that a static checker would have caught
                    # instantly. Treating LINT_ERRORS as a failure keeps the
                    # verification gate real.
                    if isinstance(exec_data, dict) and exec_data.get("status") not in ("ERROR", "LINT_ERRORS"):
                        successful_tool_calls += 1
                        _consecutive_no_tool = 0
                        sig = action_signature(action_name, payload)
                        _recent_actions.append((sig, True))
                        if len(_recent_actions) > 12:
                            _recent_actions = _recent_actions[-12:]
                        if lookup_action == "workspaceshellrequest" and isinstance(payload, dict):
                            _recent_shell_runs.append(
                                (normalize_shell_goal(str(payload.get("command", ""))),
                                 outcome_digest(exec_data))
                            )
                            if len(_recent_shell_runs) > 16:
                                _recent_shell_runs = _recent_shell_runs[-16:]
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
                        if lookup_action == "workspaceshellrequest" and isinstance(payload, dict):
                            _recent_shell_runs.append(
                                (normalize_shell_goal(str(payload.get("command", ""))),
                                 outcome_digest(exec_data))
                            )
                            if len(_recent_shell_runs) > 16:
                                _recent_shell_runs = _recent_shell_runs[-16:]

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

        # --- LOOP DETECTION (repeated identical action, success OR fail) ---
        # Allows long missions, but halts a runaway "feel-around" loop where the
        # same step repeats with no distinct progress (e.g. re-running the same
        # read-only command). Nudge once; terminate if it continues.
        if detect_repetitive_action(_recent_actions, window=8):
            if not _loop_nudge_sent:
                _loop_nudge_sent = True
                log.warning("[AgentLoop] Action loop detected: same action repeated 8x. Forcing variation.")
                action_log.append(
                    f"ITERATION {iter_num}: The SAME action has repeated 8 times with no variation. "
                    f"You appear to be stuck in a loop. Change your approach, make distinct progress, "
                    f"or finish the mission. Do not issue the identical call again."
                )
            else:
                log.error("[AgentLoop] Action loop persists after nudge. Terminating.")
                ans = "ERROR: Detected an infinite loop (same action repeated). Aborting to avoid runaway."
                await _clear_checkpoint()
                break

        # --- NO-PROGRESS DETECTION (exit-0 selftest/build loops) ---
        # Catches an agent re-running the same command and getting IDENTICAL
        # output (e.g. a --selftest that crashes before printing GAME_OK but
        # still exits 0). detect_repetitive_failure needs all-fail and
        # detect_repetitive_action needs the identical literal command, so
        # neither fires here. Three escalating stages (never a blind abort):
        #   1) PROBE      — stop re-running; read error + source; make a DISTINCT fix.
        #   2) REDIRECT   — don't just give up: steer Raven to a DIFFERENT debug
        #                   route (web search, recall its own history, re-read source).
        #   3) TERMINATE  — hard cap: even after a probe + redirect it still loops.
        if detect_no_progress(_recent_shell_runs, window=NO_PROGRESS_WINDOW):
            _np_goal = _recent_shell_runs[-1][0]
            _np_out = _recent_shell_runs[-1][1]
            if not _shell_loop_nudge_sent:
                # STAGE 1 — PROBE
                _shell_loop_nudge_sent = True
                _np_directive = no_progress_directive(1, iter_num, _np_goal, _np_out)
                action_log.append(_np_directive)
                log.warning(f"[AgentLoop] No-progress loop detected (goal={_np_goal[:90]}); probing.")
                await _record_loop_probe(iter_num, _np_goal, _np_out, _np_directive)
            elif not _shell_loop_diversify_sent:
                # STAGE 2 — REDIRECT to a different debugging route (do NOT terminate yet).
                _shell_loop_diversify_sent = True
                _np_directive = no_progress_directive(2, iter_num, _np_goal, _np_out)
                action_log.append(_np_directive)
                log.warning(f"[AgentLoop] No-progress loop persists; redirecting to alternate debug route (goal={_np_goal[:90]}).")
                await _record_loop_probe(iter_num, _np_goal, _np_out, _np_directive)
            else:
                # STAGE 3 — hard cap: probe + redirect both failed to break the loop.
                log.error("[AgentLoop] No-progress loop persists after probe + route redirect. Terminating.")
                ans = "ERROR: Detected a no-progress loop (same command repeated with identical output despite a probe and a route redirect). Aborting to avoid runaway."
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

            async with shared_http_client() as client:
                resp = await client.post(
                    f"{EXECUTION_SVC}/execute/learning",
                    json=payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=30.0),
                )
                if resp.status != 200:
                    log.warning(f"[AgentLoop] Learning persistence failed: {resp.status} {resp.text}")
        except Exception as e:
            log.warning(f"[AgentLoop] Learning persistence skipped: {e}")

    # --- SUMMARIZATION PHASE ---
    # Trigger if we have successful tool calls OR if the model output is purely JSON/messy
    is_messy = "was was was" in ans or "```json" in ans or (ans.strip().startswith("{") and ans.strip().endswith("}"))
    ans_is_empty = not ans or ans.strip() in ("", "None", "null", "{}", "[]", "}")
    # Bound the action log so the summary/reflection prompts themselves cannot
    # exhaust the context window. A long, verbose mission log is the usual cause
    # of a missing final answer — the model runs out of space and returns nothing,
    # and feeding the whole log back uncompressed just repeats the failure.
    _ctx_s, _ctx_r = await _compress_context()
    bounded_log = (_ctx_s + "\n\n" + _ctx_r).strip() or "\n".join(action_log)
    if len(bounded_log) > 8000:
        bounded_log = bounded_log[:8000] + "\n...[truncated]"

    if (successful_tool_calls > 0 or is_messy) and (
        extract_action_json(ans) or len(ans.strip()) < 30 or is_messy
    ):
        # If the last response still looks like a tool call or is very short/messy, force a clean summary
        log.info("[AgentLoop] Finalizing with clean summarization phase...")

        if ans_is_empty:
            # LLM response was empty — build summary from action log only
            summary_prompt = [
                {"role": "system", "content": "You are Raven. Summarize the mission result for the user in clean, natural language. Do NOT use JSON. Do NOT repeat yourself. Be concise. State what was accomplished based on the actions taken."},
                {"role": "user", "content": f"Mission: {query}\n\nActions taken:\n{bounded_log}\n\nThe LLM did not produce a final response, but the following actions were completed successfully. Summarize what was accomplished. Output the summary directly as your response — do not draft, plan, or repeat phrases like 'I will write'."}
            ]
        else:
            summary_prompt = [
                {"role": "system", "content": "You are Raven. Summarize the mission result for the user in clean, natural language. Do NOT use JSON. Do NOT repeat yourself. Be concise. Do NOT say the mission failed unless the tool execution itself reported an error."},
                {"role": "user", "content": f"Mission: {query}\n\nActions taken:\n{bounded_log}\n\nRaw output: {ans}\n\nPlease provide the final clean summary now: Output it directly as your response — do not draft, plan, or repeat phrases like 'I will write'."}
            ]
            try:
                data = await execute_inference(provider, selected_model, summary_prompt, {"temperature": 0.0, "enable_thinking": False})
                ans = data.get("message", {}).get("content", ans)
            except Exception as e:
                log.warning(f"[AgentLoop] Summarization phase failed: {e}")
                from services.gateway.orchestrator import strip_json_from_response
                ans = strip_json_from_response(ans)

        # Last-resort: if summarization still produced nothing (e.g. context
        # exhaustion even on the summary call), synthesize a plain-text result
        # from the action log so the user always receives a final answer.
        if not ans or ans.strip() in ("", "None", "null", "{}", "[]"):
            _steps = [ln.split(": ", 1)[-1].split(" ->")[0][:120] for ln in action_log[-10:] if ": " in ln]
            ans = (
                f"Mission completed. {successful_tool_calls} tool call(s) executed across "
                f"{len(action_log)} logged steps.\n\nMost recent actions:\n"
                + "\n".join(f"- {s}" for s in _steps)
            )
            log.warning("[AgentLoop] Used static fallback summary (LLM summary empty).")

    # --- POST-MISSION REFLECTION ---
    reflection_summary = ""
    if action_log and successful_tool_calls > 0:
        try:
            raven_reflection = await load_prompt(get_http_client(), PROMPT_RAVEN_REFLECTION)
            reflection_prompt = [
                {"role": "system", "content": raven_reflection},
                {"role": "user", "content": f"Mission: {query}\n\nPlan:\n{generated_plan}\n\nActions taken:\n{bounded_log}\n\nFinal result: {ans}\n\nProvide your reflection: Output it directly as your response — do not draft, plan, or repeat phrases like 'I will write' or 'I'll write it now'."}
            ]
            reflection_data = await execute_inference(provider, selected_model, reflection_prompt, {"temperature": 0.1, "enable_thinking": False})
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
            summarized_log = normalize_audit_log(full_audit_log)
            async with shared_http_client() as client:
                await client.patch(
                    f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                    json={"output_log": json.dumps(summarized_log)},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10.0),
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
        async with shared_http_client() as lint_client:
            lint_resp = await lint_client.post(
                f"{execution_svc}/execute/workspace_lint",
                json=lint_payload,
                headers={"X-Internal-Secret": internal_secret},
                timeout=aiohttp.ClientTimeout(total=15.0),
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
