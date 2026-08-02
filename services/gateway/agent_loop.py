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

_LESSON_MARKERS = ("RULE:", "ROOT CAUSE:", "OUTCOME:", "CONFIDENCE:", "SUPERSEDES:")


def _parse_lesson_marker(text: str) -> dict:
    """Best-effort parse of structured lesson fields from free-form text.

    Accepts either `KEY: value` on one line or a `**KEY:** value` markdown
    form. Used so a model that returns prose with our conventional
    markers still yields a structured, reusable lesson.
    """
    out: dict = {}
    if not text:
        return out
    lowered = text.lower()
    import re as _re
    for key in ("supersedes",):
        m = _re.search(rf"{key}\s*[:\-]\s*([^\n]+)", lowered)
        if m:
            ids = _re.findall(r"[A-Za-z0-9_\-]+", m.group(1))
            out[key] = [i for i in ids if i not in ("none", "n/a", "na")]
    for key in ("rule", "root_cause", "outcome", "confidence"):
        # Marker may appear as RULE / ROOT CAUSE / ROOT_CAUSE (case + space/underscore
        # tolerant), optionally wrapped in markdown bold (**RULE:**).
        marker = key.replace("_", r"[ _]")
        m = _re.search(
            rf"(?:^|\n)\s*\*{{0,2}}\s*{marker}\s*[:\-]\s*\*{{0,2}}\s*([^\n]+)",
            text,
            _re.IGNORECASE,
        )
        if m:
            val = m.group(1).strip().strip("*").strip()
            if key == "confidence":
                with contextlib.suppress(TypeError, ValueError, AttributeError):
                    out[key] = float(_re.search(r"[0-9]*\.?[0-9]+", val).group(0))
            else:
                out[key] = val
    return out


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
    "workspacesettingsupdaterequest", "workspaceportexposerequest",
}


def _has_valid_workspace_id(value: object) -> bool:
    """True only for a non-empty, non-whitespace workspace id.

    A blank/whitespace id (``""``, ``"   "``, ``None``) must be treated as
    "unassigned" — the execution service rejects it with a 400
    ("No workspace_id provided"), so the agent-loop guard must catch it before
    the request is sent.
    """
    return bool(value is not None and str(value).strip())


# The static batching example the model is shown when it should plan ahead.
_BATCH_EXAMPLE = (
    "```json\n"
    '[ {"@type": "WorkspaceFileWriteRequest", "file_path": "a.py", "content": "..."}, '
    '{"@type": "WorkspaceFileWriteRequest", "file_path": "b.py", "content": "..."}, '
    '{"@type": "WorkspaceShellRequest", "command": "python -m pytest -q"} ]\n'
    "```"
)


def build_adaptive_guidance(
    *,
    workspace_id: object,
    files_written: int,
    last_status: str | None,
    elapsed_frac: float,
    repeating: bool,
) -> str:
    """Build the per-iteration guidance the PIPELINE injects to steer the LLM.

    This is state-aware (not a single static instruction): the pipeline knows
    what phase the mission is in and nudges the model toward the efficient next
    move. Pure function so it is unit-testable with plain inputs.

    Args:
        workspace_id: current adopted workspace id (blank/None => not created yet).
        files_written: how many files have been written so far.
        last_status: status of the previous tool result ("ERROR"/"LINT_ERRORS"/...).
        elapsed_frac: fraction of the time budget consumed (0.0-1.0+).
        repeating: True when the pipeline detected a no-progress repeat.
    """
    parts: list[str] = []

    # 1) A step just FAILED — highest priority: diagnose, don't blindly retry.
    if last_status in ("ERROR", "LINT_ERRORS"):
        parts.append(
            "The previous step FAILED (see LAST TOOL RESULT). Read the actual "
            "error, fix the ROOT CAUSE, and do NOT re-run the same command "
            "unchanged. If you have tried the same fix twice, call "
            "RavenRecallRequest(only='failed') to review what you already tried."
        )

    # 2) Detected no-progress loop — escalate to a different route.
    if repeating:
        parts.append(
            "You appear to be REPEATING a step with no new progress. Change "
            "approach: inspect the source, or call RavenRecallRequest to recall "
            "what worked before. Another identical attempt will fail the same way."
        )

    # 3) Budget awareness — past 70% of the time budget, converge and ship.
    if elapsed_frac >= 0.7:
        parts.append(
            "You are past 70% of your time budget. STOP polishing: commit and "
            "PUSH what already works to GitHub now, then emit your LEARNED "
            "lesson. A working pushed repo beats an unfinished perfect one."
        )

    # 4) Phase-based batching push (only when not failing/looping/out-of-time).
    if not parts:
        if not (workspace_id and str(workspace_id).strip()):
            parts.append(
                "Start the mission: emit a JSON ARRAY that begins with "
                "WorkspaceCreateRequest and chains the known setup steps "
                "(create workspace -> gh repo create -> WorkspaceSettingsUpdateRequest), "
                "so the whole setup runs from ONE turn."
            )
        else:
            parts.append(
                "Plan several INDEPENDENT steps ahead and emit them as ONE ordered "
                "JSON ARRAY of tool-call objects so they run in a single cycle "
                "(this is how you stay inside the time budget):\n"
                f"{_BATCH_EXAMPLE}\n"
                "Batch what does not depend on another step's OUTPUT (e.g. write "
                "all the source + test files at once). Only emit a SINGLE tool "
                "call when the next action needs a result you must read first "
                "(e.g. run tests, THEN fix the reported failure)."
            )
            if files_written == 0:
                parts.append(
                    "You have written NO files yet — batch your initial project "
                    "files (source, tests, README, pyproject/config) in one array."
                )

    return "\n\n".join(parts)


def guidance_branch(
    *,
    workspace_id: object,
    last_status: str | None,
    elapsed_frac: float,
    repeating: bool,
) -> str:
    """Return a short tag naming which guidance branch is active this turn.

    Mirrors the priority order in build_adaptive_guidance() so logs can honestly
    report what the pipeline told the model, making batching/convergence effects
    measurable instead of inferred. Pure + unit-testable.
    """
    if last_status in ("ERROR", "LINT_ERRORS"):
        return "fail"
    if repeating:
        return "repeat"
    if elapsed_frac >= 0.7:
        return "budget"
    if not (workspace_id and str(workspace_id).strip()):
        return "create_ws"
    return "batch"


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
    def __init__(self, base_url: str, timeout: float | aiohttp.ClientTimeout = 300.0):
        self.base_url = base_url.rstrip("/")
        # `total` caps the whole request; `sock_read` caps the gap between
        # successive chunks so a stream that stops producing data (e.g. a
        # wedged upstream) raises instead of blocking for minutes. `connect`
        # fails fast if the host is briefly unreachable so callers can retry.
        #
        # BUGFIX (observed live): a hard sock_read=60 cap is WRONG for
        # large-context inference. During the prompt-eval / prefill phase the
        # model legitimately emits NO chunks for a long time (prefilling tens of
        # thousands of tokens on num_ctx=32k–64k can take well over a minute
        # before the first token streams). The old `min(to, 60)` fired sock_read
        # mid-prefill, the inner streaming loop retried (range(3)), and each retry
        # RESTARTED the whole request → re-prefilled → timed out again. This is
        # exactly why raising num_ctx made missions *slower* and produced the
        # tell-tale 30s-quantised "inference completed in 150006ms / 240011ms"
        # stalls: they were stacked prefill timeouts + retries, not real
        # generation. `total` already bounds the whole request, so sock_read only
        # needs to catch a *truly* wedged stream — make it a large fraction of
        # total (min 180s) instead of a tiny fixed cap.
        if isinstance(timeout, aiohttp.ClientTimeout):
            self.timeout = timeout
        else:
            to = float(timeout) if timeout else 300.0
            read_to = max(180.0, to * 0.8)
            self.timeout = aiohttp.ClientTimeout(
                total=to, connect=10.0, sock_connect=15.0, sock_read=read_to
            )

    async def _wait_for_model(
        self, client: aiohttp.ClientSession, model: str, timeout: float = 120.0
    ) -> None:
        """Poll /api/ps until the target model appears in loaded models.
        
        Prevents 404s when the model is cold-loaded — /api/chat returns 404
        until Ollama has finished loading the weights into VRAM.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                resp = await client.get(
                    f"{self.base_url}/api/ps",
                    timeout=aiohttp.ClientTimeout(total=3.0),
                )
                if resp.status == 200:
                    data = await resp.json()
                    loaded = data.get("models") or data.get("slots") or []
                    for entry in loaded:
                        if isinstance(entry, dict) and entry.get("model", "").endswith(model):
                            log.info(
                                f"[OllamaProvider-Hardened] Model {model} loaded "
                                f"({entry.get('size', 0) / 1e9:.1f} GB)"
                            )
                            return
                        elif isinstance(entry, str) and entry == model:
                            log.info(f"[OllamaProvider-Hardened] Model {model} loaded")
                            return
                # Check slots — if available, model may already be loaded
                slots = data.get("slots") or data.get("active_requests", {})
                if isinstance(slots, dict):
                    for req_model in slots:
                        if isinstance(req_model, str) and req_model.endswith(model):
                            log.info(f"[OllamaProvider-Hardened] Model {model} active in slots")
                            return
                    if isinstance(data.get("slots"), dict) and data["slots"].get("available", 0) > 0:
                        log.info(
                            f"[OllamaProvider-Hardened] No active slots, but slot pool available — model may be preloaded"
                        )
                        return
            except Exception:
                pass
            await asyncio.sleep(2)
        log.warning(
            f"[OllamaProvider-Hardened] Timed out waiting for model {model} "
            f"after {timeout}s — attempting request anyway"
        )

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
            # Wait for model to be loaded before attempting streaming request.
            # This prevents 404s caused by cold-start (model weights not yet
            # in VRAM) — /api/chat returns 404 until loading completes.
            await self._wait_for_model(client, model, timeout=120.0)

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
            # Streaming path (used by AgentLoop). Retry transient connection
            # errors (e.g. a brief upstream flap) before handing off to the
            # AgentLoop's own retry, so one blip doesn't burn a whole
            # attempt waiting on the 300s timeout.
            last_err: Exception | None = None
            for _attempt in range(3):
                buf = ""
                try:
                    async with client.post(
                        f"{self.base_url}/api/chat", json=payload,
                        headers={"X-Request-Source": "shared-llm/app"},
                        timeout=self.timeout,
                    ) as response:
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
                                if not content and show_thinking:
                                    content = chunk_json.get("message", {}).get("thinking") or ""
                                if content:
                                    buf += content
                                    await chunk_callback(content)
                                if chunk_json.get("done"):
                                    break
                            except RuntimeError:
                                raise
                            except Exception as e:
                                log.error(f"Error parsing streaming chunk: {e} | Raw line: {clean_line!r}")
                    full_content = buf
                    break  # success
                except RuntimeError:
                    raise  # provider errors are fatal -> AgentLoop retry
                except (TimeoutError, aiohttp.ClientError) as e:
                    last_err = e
                    log.warning(
                        f"[OllamaProvider-Hardened] stream attempt "
                        f"{_attempt + 1}/3 failed: {e}; retrying"
                    )
                    await asyncio.sleep(3 * (_attempt + 1))
            else:
                if last_err:
                    raise last_err
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
    "websearchrequest", "webreadrequest", "webscraperrequest", "codesearchrequest", "dockerlogsrequest",
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
    "workspaceportexposerequest", "workspace_expose_port", "expose_port", "port_expose",
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
    "create_note", "delete_note",
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


# File/workspace tool types whose payloads MUST carry a real path + (for writes)
# real content. These are the calls most often emitted as a truncated bag-of-words
# dict when generation is cut mid-file-write; executing such garbage poisons the
# workspace, so every extraction path funnels through this gate.
_STRUCTURED_TOOL_ACTIONS = (
    "WorkspaceFileWriteRequest",
    "WorkspaceFilePatchRequest",
    "WorkspaceFileReadRequest",
)


def _valid_structured_tool(obj: dict) -> bool:
    """Return True only if a file-write/read/patch call has a usable payload.

    Rejects the live-observed failure mode where a truncated generation emits a
    flat bag-of-words dict like {"file_path":":", "envdiff":"/core.py",
    "content":":"} — file_path is a stub (":"/"."/"/") and content is a 1-char
    stub or a dict of words. Such calls must never be executed; the caller should
    steer the model to re-emit a well-formed call instead.
    """
    if not isinstance(obj, dict):
        return False
    action = obj.get("action") or obj.get("@type")
    if action not in _STRUCTURED_TOOL_ACTIONS:
        return True
    fp = obj.get("file_path")
    if not isinstance(fp, str) or not fp.strip() or fp.strip() in (":", ".", "/"):
        return False
    if action in ("WorkspaceFileWriteRequest", "WorkspaceFilePatchRequest"):
        content = obj.get("content")
        if content is None:
            return False
        if isinstance(content, dict) or not isinstance(content, str):
            return False
        if len(content.strip()) < 5:
            return False
    return True


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
    if isinstance(func, list):
        # Some clients emit "function": [...] (the tool_calls array shape); take
        # the first element so we don't later call .get() on a list and crash
        # with "'list' object has no attribute 'get'".
        func = func[0] if func and isinstance(func[0], dict) else None
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

    # --- STRUCTURAL VALIDATION (RC: reject malformed tool calls, do NOT execute) ---
    # Observed live: with a truncated generation the model emitted e.g.
    #   {"file_path":":", "envdiff":"/core.py", "content":":", ...}  (a flat bag-of-
    #   words dict). The old code accepted it (content is truthy; file_path is a
    #   non-empty string) and executed a write to path ":" — poisoning the
    #   workspace with garbage and never producing a real file, so the mission
    #   looped re-emitting garbage instead of converging. A tool call this broken
    #   must be rejected (return None) so the agent loop steers the model to
    #   re-emit a well-formed call rather than acting on nonsense.
    if not _valid_structured_tool(obj):
        return None

    return obj if obj.get("action") else None


# Valid git verbs accepted by the GitOperationRequest schema (both the gateway
# and execution copies share this exact set).
_GIT_VALID_ACTIONS = {
    "status", "diff", "add", "commit", "pull", "push", "log", "fetch",
    "reset", "branch", "checkout", "clean", "show", "init", "remote",
    "remote_add", "repo_create", "repo_clone", "gh_noop",
}

# Tool-type names that should never be forwarded as the git `action` verb.
_GIT_TOOL_TYPE_NAMES = {"gitoperationrequest", "gitoperation", "gitop", "git"}


def _normalize_git_payload_action(payload: dict) -> dict:
    """Guarantee a GitOperationRequest payload carries a VALID git verb.

    The model sometimes emits the tool call with the `action` field set to the
    tool *type* name (``"GitOperationRequest"``) instead of the actual git verb
    (``add``/``commit``/...). That produces a 422 from the execution service.

    This resolves it by:
      1. accepting any verb already present and valid,
      2. promoting an explicit verb carried in ``git_action``/``operation``/outer
         ``action`` when the current value is only the tool type name,
      3. inferring the verb from payload shape (commit_message -> commit, etc.),
      4. falling back to a harmless ``status`` (read-only) when nothing else fits,
         so the call never 422s and the model gets real feedback instead of a
         schema crash.
    """
    if not isinstance(payload, dict):
        return payload

    # Normalize repo-visibility flags regardless of verb resolution below:
    # models routinely emit `isPrivate`/`public`/`visibility` and the schema
    # silently ignores unknown keys, which would otherwise create a PUBLIC repo.
    _normalize_git_visibility(payload)

    raw = payload.get("action")
    current = str(raw).strip().lower() if raw is not None else ""
    outer_action = str(payload.get("_outer_action") or "").strip().lower()

    # Already a valid verb — nothing more to do.
    if current in _GIT_VALID_ACTIONS:
        return payload

    # The current value is the tool type name (or empty). Try to find the real
    # verb from other fields the model may have used.
    candidates = [
        payload.get("git_action"),
        payload.get("operation"),
        outer_action,
    ]
    for cand in candidates:
        cand = str(cand).strip().lower() if cand is not None else ""
        if cand in _GIT_VALID_ACTIONS:
            payload["action"] = cand
            return payload
        # e.g. "git_commit" / "GitOperationRequest:commit"
        if cand and ":" in cand:
            tail = cand.rsplit(":", 1)[-1]
            if tail in _GIT_VALID_ACTIONS:
                payload["action"] = tail
                return payload
        if cand.startswith("git_") and cand[4:] in _GIT_VALID_ACTIONS:
            payload["action"] = cand[4:]
            return payload

    # Infer from payload shape.
    if payload.get("commit_message") or payload.get("message"):
        payload["action"] = "commit"
    elif payload.get("source_path") or (payload.get("repo_url") and payload.get("repo_name")):
        payload["action"] = "repo_create"
    elif payload.get("remote_name") and payload.get("repo_url"):
        payload["action"] = "remote_add"
    elif payload.get("log_count") is not None:
        payload["action"] = "log"
    elif payload.get("repo_url") and not payload.get("repo_name"):
        payload["action"] = "remote_add"
    else:
        # Safe read-only default so the step still executes and the model can
        # self-correct rather than the whole mission aborting on a 422.
        payload["action"] = "status"

    return payload


# Field names (besides the canonical `private`) the model may use to express
# repository visibility.
_VISIBILITY_KEYS = ("isPrivate", "is_private", "isPublic", "is_public", "public", "visibility")


def _normalize_git_visibility(payload: dict) -> None:
    """Coerce repo-visibility variants into the handler's ``private`` bool."""
    if not isinstance(payload, dict):
        return
    action = str(payload.get("action") or "").strip().lower()
    if action not in ("repo_create", "remote_add"):
        return

    # Canonical `private` already present and not None -> respect it.
    if "private" in payload and payload["private"] is not None:
        explicit = payload["private"]
        payload["private"] = bool(explicit) if not isinstance(explicit, str) else explicit.strip().lower() not in ("false", "0", "no", "public")
        return

    # `public: true` => private False; `public: false` => private True.
    if "public" in payload and payload["public"] is not None:
        pub = payload["public"]
        is_public = bool(pub) if not isinstance(pub, str) else pub.strip().lower() not in ("false", "0", "no")
        payload["private"] = not is_public
        return

    # `isPrivate` / `is_private` => direct bool.
    for key in ("isPrivate", "is_private"):
        if key in payload and payload[key] is not None:
            val = payload[key]
            payload["private"] = bool(val) if not isinstance(val, str) else val.strip().lower() not in ("false", "0", "no")
            return

    # `visibility`: "private"|"internal"|"public".
    if "visibility" in payload and payload["visibility"] is not None:
        vis = str(payload["visibility"]).strip().lower()
        payload["private"] = vis != "public"
        return


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


# Cap on how many times the JSON-repair fallback may recurse. Without this, a
# persistently-unparseable model output (common with the local 35B model) drove
# unbounded recursion and a RecursionError that killed the entire mission job
# before any tool action (e.g. a git commit/push) could run.
_EXTRACT_MAX_REPAIR_DEPTH = 8


def extract_action_json(text: str, _depth: int = 0) -> dict | None:
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

    # Priority 3b: Repair control chars leaked inside JSON strings (models
    # occasionally emit unescaped newlines/tabs inside a tool-call payload).
    # NOTE: _repair_json_control_chars uses re.sub, which returns a *new* string
    # object (different identity) on every match even when the replacement text
    # is identical. The previous identity check (`repaired is not text`) was
    # therefore always True, causing infinite self-recursion. Compare by CONTENT
    # (`!=`) and bound the recursion depth so an unparseable payload degrades to
    # None (the caller re-prompts) instead of crashing the mission.
    repaired = _repair_json_control_chars(text)
    if repaired and repaired != text and _depth < _EXTRACT_MAX_REPAIR_DEPTH:
        norm = extract_action_json(repaired, _depth + 1)
        if norm:
            return norm

    return None


def extract_action_batch(text: str) -> list[dict] | None:
    """Extract a *batch* of tool calls (a JSON array) from model output.

    When Raven emits an array of tool-call objects — e.g. a proven multi-step
    command chain assembled from its memory/history — the whole batch is executed
    from a SINGLE inference, which is the core lever that keeps autonomous builds
    from burning 1800s re-deriving every individual step. Returns the list of
    normalized tool dicts, or ``None`` when the response is not a tool-call array.
    """
    if not text:
        return None

    candidates: list[str] = []

    # Priority 1: fenced JSON array (```json [ ... ] ``` or ``` [ ... ] ```)
    for m in re.finditer(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL):
        candidates.append(m.group(1))

    # Priority 2: a bare top-level JSON array (first '[' .. last ']')
    first = text.find("[")
    last = text.rfind("]")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first : last + 1])

    for raw in candidates:
        parsed = None
        # Attempt 1: strict parse. Attempt 2: strip trailing commas.
        # Attempt 3: repair raw control chars (newlines/tabs) that leaked inside
        # string values — REAL file content always contains literal newlines, so
        # without this repair every batch of file-writes silently fails to parse
        # and the loop falls back to a single tool call. This mirrors the repair
        # already applied on the single-object path (extract_action_json).
        for attempt in (
            lambda s: s,
            lambda s: re.sub(r",\s*([\]}])", r"\1", s),
            lambda s: _repair_json_control_chars(s),
            lambda s: _repair_json_control_chars(re.sub(r",\s*([\]}])", r"\1", s)),
        ):
            try:
                candidate_parsed = json.loads(attempt(raw))
            except Exception:
                continue
            if isinstance(candidate_parsed, list) and candidate_parsed:
                parsed = candidate_parsed
                break
        if not isinstance(parsed, list) or not parsed:
            continue
        batch = []
        for item in parsed:
            norm = _normalize_tool(item)
            # Structurally-valid calls only: a truncated bag-of-words stub
            # (e.g. {"file_path": ":", "content": ":"}) in a multi-call array
            # must not become batch[0], or the whole batch is rejected and the
            # valid calls that followed it are never executed.
            if norm and (norm.get("action") or norm.get("@type")) and _valid_structured_tool(norm):
                batch.append(norm)
        if batch:
            return batch
    return None


def _next_batch_step(pending_batch: list[dict]) -> tuple[bool, dict | None]:
    """Pop the next step of an in-flight batched command chain.

    Returns ``(skip_inference, tool_data)``. When the queue is non-empty the next
    tool is dequeued and the caller must SKIP the LLM inference — the whole chain
    is driven by a single reasoning cycle. When empty, ``(False, None)`` signals a
    normal inference turn. Extracted from the agent loop so the queue semantics are
    unit-testable without standing up the full loop.
    """
    if pending_batch:
        return True, pending_batch.pop(0)
    return False, None


def _repair_json_control_chars(text: str) -> str:
    """Escape raw newlines/tabs/carriage-returns that leaked inside JSON string
    values so ``json.loads`` can parse otherwise-valid tool-call payloads.

    Only string *contents* are touched (matched between unescaped quotes), so
    structural whitespace outside strings is left intact. Best-effort only —
    callers must still validate the result.
    """
    def _esc_quoted(m: "re.Match[str]") -> str:
        s = m.group(0)
        # Protect any already-escaped sequences before inserting new escapes.
        s = s.replace("\\\\", "\x00")  # placeholder for a literal backslash
        s = s.replace("\\n", "\x01").replace("\\t", "\x02").replace("\\r", "\x03")
        s = s.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
        # restore protected escapes
        s = s.replace("\x01", "\\n").replace("\x02", "\\t").replace("\x03", "\\r")
        s = s.replace("\x00", "\\\\")
        return s

    return re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', _esc_quoted, text)


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


# Shell commands that carry no git intent and are safe to drop when they appear
# alongside intercepted git/gh ops (they only echo status / change dir).
_SHELL_NOISE_CMDS = {
    "true", "false", ":", "echo", "cat", "head", "tail", "less", "wc", "sort",
    "tee", "printf", "pwd", "ls", "sleep", "cd", "touch", "mkdir", "test", "[",
}


def _translate_shell_to_git_op(cmd: str) -> list[dict] | None:
    """Intercept RAW workspace-shell git/gh commands and re-route them through the
    credentialed, guard-railed ``GitOperationRequest`` tool.

    Returns a list of GitOperationRequest-compatible payload dicts (one per
    git/gh sub-command, so ``&&``/``;``/``||``-chained pipelines like
    ``git init && git remote add origin <url> && git fetch`` are fully honored),
    or ``None`` to let the command run as an ordinary shell command.

    Why: the autonomous loop's shell has NO git credentials, so a model that
    forgets to use the git tool and instead runs ``git push`` (or ``gh repo
    create``) in the shell can never authenticate and silently fails to publish.
    This is the durable backstop: even a flailing model gets correct,
    token-injected, per-workspace-scoped git behavior.
    """
    if not isinstance(cmd, str) or not cmd.strip():
        return None

    # `gh` runs NATIVELY inside the workspace sandbox. The sandbox has the `gh`
    # CLI installed and receives the user's GITHUB_TOKEN (and GIT_TOKEN) injected
    # into its environment at exec time, so `gh repo create`, `gh pr`, `gh api`,
    # etc. work directly in the shell. We must NOT intercept/route `gh` — the old
    # backstop assumed no `gh` binary and no-op'd every `gh` command, which broke
    # agent git/GitHub workflows. If the pipeline contains any `gh` command, run
    # the WHOLE pipeline as a native shell command (git, if present alongside,
    # authenticates via the injected token + git-credential helper).
    _gh_norm = re.sub(r"^\s*sudo\s+", "", cmd).strip()
    for _gh_piece in re.split(r"\s*(?:\|\||&&|;)\s*", _gh_norm):
        _gh_p = re.sub(r"^\s*sudo\s+", "", _gh_piece).strip()
        try:
            _gh_parts = shlex.split(_gh_p)
        except ValueError:
            continue
        if _gh_parts and _gh_parts[0] == "gh":
            return None

    # `git branch` (incl. rename `git branch -m master main`, `-M`, `-a`, `-v`)
    # is a LOCAL operation that does NOT need push credentials and, critically,
    # the git-op translator mangles it (it reads `parts[2]` as the branch name,
    # so `git branch -m master main` becomes action 'branch' path '-m' and the
    # real `command` is dropped — see mission 7). Run the WHOLE pipeline natively
    # in the shell so the branch rename (and any sibling git steps) execute as
    # written against the injected-token environment.
    _branch_norm = re.sub(r"^\s*sudo\s+", "", cmd).strip()
    for _br_piece in re.split(r"\s*(?:\|\||&&|;)\s*", _branch_norm):
        _br_p = re.sub(r"^\s*sudo\s+", "", _br_piece).strip()
        try:
            _br_parts = shlex.split(_br_p)
        except ValueError:
            continue
        if _br_parts and _br_parts[0] == "git" and len(_br_parts) > 1 and _br_parts[1] == "branch":
            return None

    # Split compound commands; trailing `|| true` / `&& true` guards are dropped.
    pieces = re.split(r"\s*(?:\|\||&&|;)\s*", cmd.strip())
    routed: list[dict] = []

    for raw in pieces:
        piece = re.sub(r"^\s*sudo\s+", "", raw).strip()
        # Drop shell redirections (e.g. `2>&1`, `>/dev/null`) that would confuse
        # the git CLI if passed through as positional arguments.
        piece = re.sub(r"\s*2>&1|\s*2>/dev/null|\s*&>[\s\S]*|\s*>/dev/null", "", piece).strip()
        if not piece:
            continue
        try:
            parts = shlex.split(piece)
        except ValueError:
            return None
        if not parts:
            continue
        bin_name = parts[0]
        sub = parts[1] if len(parts) > 1 else ""

        if bin_name == "git":
            r = _translate_git_parts(parts, sub)
            if r is not None:
                routed.append(r)
            # Unknown git subcommands (e.g. `git remote -v`, `git branch -a`)
            # are SKIPPED rather than aborting the whole pipeline, so a
            # sibling `git push` is still intercepted and routed through the
            # credentialed git tool. The skipped command's output is simply
            # not produced (the model can re-issue it as a standalone call).
            continue
        elif bin_name == "gh":
            # NOTE: `gh` is handled NATIVELY above (the whole pipeline is run as
            # a shell command when any `gh` is present, since the sandbox has the
            # `gh` CLI + injected GITHUB_TOKEN). This branch is only reachable if
            # a pipeline somehow contained `gh` without tripping the early
            # return — treated as a no-op so it cannot break the git pipeline.
            r = _translate_gh_parts(parts, sub)
            if r is not None:
                routed.append(r)
            continue
        elif bin_name in _SHELL_NOISE_CMDS:
            # Non-git noise (echo, ls, true, cat, ...) — safe to skip; irrelevant
            # to the git pipeline.
            continue
        else:
            # A real, non-git command mixed into the pipeline: we cannot run it
            # through the git tool, so don't intercept at all.
            return None

    return routed or None


# Git subcommands the execution git handler understands. Used to alias bare
# subcommand tool names (e.g. "branch") AND to recognize a git-op-shaped
# WorkspaceShellRequest payload so it can be re-routed through the
# credentialed git tool instead of failing at the raw shell (which has no git
# credentials and requires a `command` string).
_GIT_OP_SUBCOMMANDS = {
    "status", "st", "add", "commit", "ci", "push", "pull", "fetch", "log",
    "branch", "checkout", "co", "show", "init", "merge", "rebase", "stash",
    "reset", "clean", "tag", "clone", "switch", "restore", "mv", "rm",
    "remote", "diff",
}


def _is_git_op_shell_payload(payload) -> dict | None:
    """Detect a ``workspaceshellrequest`` whose payload is actually a mis-emitted
    git operation (e.g. ``{"action": "branch", "path": "-M"}`` with no ``command``).

    Such calls would otherwise be POSTed to the raw shell, which has no git
    credentials and lacks a ``command`` key, so the step fails. Re-routing them
    through the credentialed ``gitoperationrequest`` tool (which understands the
    ``action``/``path``/``commit_message`` shape) makes them authenticate + succeed.

    Returns a normalized git-op dict, or ``None`` when the payload is a real shell
    command / a legitimate WorkspaceShellRequest / not git-shaped.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("command") or payload.get("commands"):
        return None
    if str(payload.get("@type") or "").lower() == "workspaceshellrequest":
        return None
    act = str(payload.get("action") or "").strip().lower()
    if act in _GIT_OP_SUBCOMMANDS:
        _norm = dict(payload)
        _norm["action"] = act
        return _norm
    return None


def _route_workspace_shell_to_git(lookup_action: str, payload):
    """Apply the workspace-shell -> git interception.

    Returns ``(lookup_action, payload, git_batch)``.

    - A raw shell ``git``/``gh`` command is translated into one or more
      credentialed ``gitoperationrequest`` ops (``git_batch`` carries the
      extra ops to fan out after the first POST).
    - A git-op-shaped payload mis-emitted as a shell call (e.g.
      ``{"action": "branch", "path": "-M"}`` with no ``command``) is also
      re-routed to the credentialed git tool so it authenticates + succeeds.
    """
    if lookup_action != "workspaceshellrequest" or not isinstance(payload, dict):
        return lookup_action, payload, None
    _shell_cmd = payload.get("command") or ""
    _routed = _translate_shell_to_git_op(_shell_cmd)
    if _routed:  # non-empty list of git-op payloads
        return "gitoperationrequest", _routed[0], _routed
    _git_op = _is_git_op_shell_payload(payload)
    if _git_op:
        return "gitoperationrequest", _git_op, None
    return lookup_action, payload, None


def _translate_git_parts(parts: list[str], sub: str) -> dict | None:
    """Translate a single parsed ``git <sub> ...`` command into a git-op payload."""
    if sub in ("status", "st", ""):
        return {"action": "status"}
    if sub == "diff":
        path = parts[2] if len(parts) > 2 else "."
        return {"action": "diff", "path": path}
    if sub in ("add",):
        path = " ".join(parts[2:]) if len(parts) > 2 else "."
        return {"action": "add", "path": path}
    if sub in ("commit", "ci"):
        msg = _git_commit_message_from_parts(parts)
        return {"action": "commit", "commit_message": msg or "chore: update"}
    if sub == "push":
        return {"action": "push", "branch": _git_branch_from_parts(parts, "push")}
    if sub == "pull":
        return {"action": "pull", "branch": _git_branch_from_parts(parts, "pull")}
    if sub == "fetch":
        return {"action": "fetch"}
    if sub == "log":
        return {"action": "log", "log_count": _git_log_count(parts)}
    if sub in ("branch",):
        name = parts[2] if len(parts) > 2 else "."
        return {"action": "branch", "path": name}
    if sub in ("checkout", "co"):
        if len(parts) < 3:
            return None
        return {"action": "checkout", "path": parts[2]}
    if sub == "show":
        path = parts[2] if len(parts) > 2 else "."
        return {"action": "show", "path": path}
    if sub == "init":
        return {"action": "init"}
    if sub == "remote" and len(parts) > 2 and parts[2] == "add":
        if len(parts) >= 5:
            return {"action": "remote_add", "remote_name": parts[3], "repo_url": parts[4]}
        return None
    if sub == "remote":
        # `git remote -v` / `git remote get-url origin` etc. -> show remotes.
        return {"action": "remote"}
    # Unknown git subcommand: return None so the caller SKIPS it (rather than
    # aborting the whole pipeline) — a sibling `git push` is still intercepted.
    return None


def _translate_gh_repo_create(parts: list[str]) -> dict | None:
    """Translate ``gh repo create <name> [flags]`` into a repo_create payload."""
    repo_name = _gh_repo_create_name(parts)
    if not repo_name:
        return None
    private = "--private" in parts and "--public" not in parts
    description = _gh_flag_value(parts, "--description", "-d")
    return {
        "action": "repo_create",
        "repo_name": repo_name,
        "private": private,
        "description": description,
    }


def _translate_gh_parts(parts: list[str], sub: str) -> dict | None:
    """Translate a single parsed ``gh <sub> ...`` command into a git-op payload.

    The sandbox has no ``gh`` CLI, so every ``gh`` command the model issues
    there would fail. We honor the credentialed-safe subset and turn the rest
    into informational no-ops:

      - ``gh repo create <name>``  -> repo_create (existing behavior)
      - ``gh repo clone <url>``    -> repo_clone (the workspace is ALREADY a
            git repo bound to its GitHub remote, so cloning is redundant; the
            git handler reports the existing origin instead of failing)
      - any other ``gh ...``        -> gh_noop (informational no-op that tells
            the model to use GitOperationRequest / WorkspaceFileWriteRequest)
    """
    if sub == "repo" and len(parts) > 2 and parts[2] == "create":
        return _translate_gh_repo_create(parts)
    if sub == "repo" and len(parts) > 2 and parts[2] == "clone":
        # First positional arg after `clone` is the clone target (url or owner/repo).
        target = ""
        for tok in parts[3:]:
            if tok.startswith("-"):
                continue
            target = tok
            break
        return {"action": "repo_clone", "repo_url": target}
    # `gh repo view`, `gh auth status`, `gh pr`, `gh api`, ...: no-op.
    return {"action": "gh_noop", "gh_command": " ".join(parts[1:])}


def _git_commit_message_from_parts(parts: list[str]) -> str | None:
    """Extract the message from ``git commit -m "msg"`` / ``--message "msg"``."""
    for i, tok in enumerate(parts):
        if tok in ("-m", "--message", "-F", "--file") and i + 1 < len(parts):
            return parts[i + 1]
    # Bare ``git commit <msg>`` (single token, no -m): treat remainder as message.
    if len(parts) > 2:
        return " ".join(parts[2:])
    return None


def _git_branch_from_parts(parts: list[str], verb: str) -> str:
    """Extract the branch from ``git push/pull [options] [remote] [branch]``."""
    # Drop leading options (-u, --set-upstream, --force, -f, --tags, etc.)
    rest = [p for p in parts[2:] if not p.startswith("-")]
    # Pattern: remote branch  OR  remote local:remote  OR  branch
    if len(rest) >= 2:
        # git push origin main  -> branch is last token (or right side of refspec)
        last = rest[-1]
        if ":" in last:
            last = last.split(":")[-1]
        return last
    if len(rest) == 1:
        return rest[0]
    return "microservices"


def _git_log_count(parts: list[str]) -> int:
    for tok in parts:
        m = re.match(r"-(\d+)$", tok)
        if m:
            return int(m.group(1))
        m = re.match(r"--max-count=(\d+)$", tok)
        if m:
            return int(m.group(1))
    return 10


def _gh_repo_create_name(parts: list[str]) -> str | None:
    """Extract the repo name from ``gh repo create <name> [flags]``."""
    value_flags = {"--source", "-s", "--description", "-d", "--homepage", "-h",
                   "--team", "-t", "--template", "--license", "-l", "--gitignore"}
    i = 3  # skip ["repo", "create"]
    while i < len(parts):
        a = parts[i]
        if a.startswith("-"):
            if a in value_flags and i + 1 < len(parts):
                i += 2
                continue
            i += 1
            continue
        return a
    return None


def _gh_flag_value(parts: list[str], *flags: str) -> str | None:
    for i, tok in enumerate(parts):
        if tok in flags and i + 1 < len(parts):
            return parts[i + 1]
    return None


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
    max_ctx = int(settings.get("llm_local_max_ctx", "16384"))
    # P1: ALWAYS honor the configured context as the baseline. The previous
    # behaviour only set num_ctx inside the successful /api/ps branch, so any
    # transient failure to reach Ollama silently fell back to Ollama's tiny
    # default context (context starvation -> 40-60 iterations). Now num_ctx is
    # set up front and only scaled DOWN when VRAM pressure is confirmed.
    params = {
        # num_predict is carved OUT of num_ctx by Ollama, so an oversized output
        # reservation starves the INPUT budget. 8192 left only ~3k tokens for the
        # code+history after the ~5k-token system prompt, so the model could not
        # see a test file and its implementation together to fix import mismatches.
        # 4096 still fits a full file write and doubles the input budget for free
        # (no VRAM cost — num_predict does not change resident VRAM).
        # num_predict=4096 + 65536 ctx: a 4096-token gen took 180-270s, so only
        # ~17 iters fit the 1920s mission cap -> timeout before converging. 2048
        # halved per-infer time (~90-135s) but OBSERVED LIVE: 2048 is too small to
        # finish writing a real module in ONE generation, so the model truncated
        # mid-file, fell back to prose garbage (rejected by the malformed-call
        # gate), and looped until the wall-clock cap. 3072 is the measured
        # compromise: large enough that cli.py/tests/core.py complete in one turn
        # (no truncation -> no prose fallback), but per-infer stays ~120-180s so
        # ~10-12 iters fit the cap — enough to write the lib, test, and push.
        "num_predict": 3072,
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
        "thinking": False,  # Disable thinking blocks to get content faster
        "num_ctx": max_ctx,
    }
    if not local_url:
        log.warning("[AgentLoop] llm_local_url not configured; returning default VRAM-safe params")
        # No local URL => not an Ollama/local model; drop the context hint so we
        # don't misconfigure a cloud provider.
        params.pop("num_ctx", None)
        return params

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
                    # else: keep the configured max_ctx baseline
                except json.JSONDecodeError:
                    log.warning(f"[AgentLoop] Failed to parse VRAM status (api/ps) from {local_url}; using configured num_ctx={max_ctx}")
    except Exception as e:
        # Never let a transient VRAM check drop the context to Ollama's default.
        log.warning(f"[AgentLoop] VRAM status check failed ({e!r}); using configured num_ctx={max_ctx}")
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
# One HTTP client per event loop (see main.get_http_client for rationale). The
# Raven worker runs on its own event loop, so this module's client must be
# cached per-loop rather than as a single global that would thrash/leak when
# both the API loop and the worker loop call in.
_http_clients: dict[asyncio.AbstractEventLoop, aiohttp.ClientSession] = {}
_fallback_http_client: aiohttp.ClientSession | None = None


def _http_client_dead(client: "aiohttp.ClientSession | None") -> bool:
    """A session can report ``closed == False`` while its underlying connector
    has been closed (e.g. after a transient upstream disconnect). Reusing such a
    session raises ``AssertionError: Connector is closed`` on the next request,
    which is not a ``ClientError`` and therefore escapes normal error handling.
    Treat a closed/missing connector as a dead client so it gets recreated."""
    if client is None or client.closed:
        return True
    connector = client.connector
    return connector is None or getattr(connector, "closed", False)


def get_http_client() -> aiohttp.ClientSession:
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is None:
        global _fallback_http_client
        if _http_client_dead(_fallback_http_client):
            _fallback_http_client = _original_async_client(
                timeout=aiohttp.ClientTimeout(300.0, connect=30.0),
                connector=aiohttp.TCPConnector(limit=100),
            )
        return _fallback_http_client

    client = _http_clients.get(current_loop)
    if _http_client_dead(client):
        client = _original_async_client(
            timeout=aiohttp.ClientTimeout(300.0, connect=30.0),
            connector=aiohttp.TCPConnector(limit=100),
        )
        _http_clients[current_loop] = client
    return client


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

# Phrases that indicate the model believes the mission is genuinely done. When a
# no-tool text reply contains one of these AND the mission has already produced
# successful tool calls, the runtime terminates cleanly instead of nudging the
# (correctly-finished) agent to keep emitting tool calls for up to
# MAX_IDLE_NUDGES turns — which wastes iterations and confuses the model into a
# "complete -> re-prompt -> complete" loop.
COMPLETION_INDICATORS = [
    "mission complete",
    "mission is complete",
    "task complete",
    "task is complete",
    "i have completed",
    "the mission is done",
    "mission accomplished",
    "successfully created",
    "has been successfully created",
    "has been completed",
    "the task is complete",
]


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

    # Reject summaries that explicitly report the mission did NOT complete.
    # A build/implementation mission that ends with "Status: Incomplete -
    # interrupted by time limit" or "❌ Game code not written" / "❌ repo
    # creation not completed" is a failure report, not meaningful success
    # output, and must NOT be reported as a `completed` mission (false
    # positive). Genuine successes describe what was built/pushed, not what
    # was not.
    incompletion_indicators = [
        "incomplete",
        "interrupted by time limit",
        "interrupted by a time",
        "interrupted by a system timeout",
        "interrupted by system timeout",
        "interrupted by the timeout",
        "push was interrupted",
        "pushing to github was interrupted",
        "hit the time limit",
        "time limit exceeded",
        "timed out",
        "not completed",
        "was not completed",
        "not written",
        "not implemented",
        "not created",
        "not committed",
        "not pushed",
        "remains to be",
        "sync remains",
        "still remains",
        "only the repository",
        "did not complete",
        "did not produce",
        "did not finish",
        "was not finished",
        "no code was",
        "no files were",
    ]
    if any(ind in result_lower for ind in incompletion_indicators):
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
                    json={"workspace_id": assigned_workspace_id, "rag_user": user_id, "create_if_missing": True},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=20.0),
                )
                if boot.status == 200:
                    return (await boot.json()).get("workspace")
        except Exception as e:
            log.warning(f"[workspace] bootstrap {assigned_workspace_id} failed: {e}")
        return None

    # No assigned_workspace_id explicitly passed. Check if mission query mentions an existing workspace or requests a specific workspace ID.
    if query:
        try:
            async with shared_http_client() as client:
                lst = await client.get(
                    f"{WORKSPACE_RUNTIME_SVC}/workspaces",
                    params={"rag_user": user_id},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if lst.status == 200:
                    all_ws = (await lst.json()).get("workspaces", [])
                    for item in all_ws:
                        if isinstance(item, dict):
                            wid = item.get("id")
                            if wid and wid != "default" and wid in query:
                                log.info(f"[workspace] Auto-detected existing workspace '{wid}' referenced in mission query")
                                return item
        except Exception as e:
            log.warning(f"[workspace] Auto-detect mentioned workspace failed: {e}")

        # Extract explicitly specified workspace ID candidate (e.g. raven-curriculum-12345) from prompt text
        import re
        matches = re.findall(r"\b(raven-[a-zA-Z0-9_-]+)\b", query)
        for cand in matches:
            if cand != "default" and len(cand) >= 5:
                ws = await _resolve_one(cand)
                if ws:
                    log.info(f"[workspace] Auto-detected workspace '{cand}' from query regex match")
                    return ws
                try:
                    async with shared_http_client() as client:
                        boot = await client.post(
                            f"{WORKSPACE_RUNTIME_SVC}/workspaces/bootstrap",
                            json={"workspace_id": cand, "rag_user": user_id, "create_if_missing": True},
                            headers={"X-Internal-Secret": INTERNAL_SECRET},
                            timeout=aiohttp.ClientTimeout(total=20.0),
                        )
                        if boot.status == 200:
                            ws = (await boot.json()).get("workspace")
                            if ws:
                                log.info(f"[workspace] Auto-bootstrapped workspace '{cand}' from query regex match")
                                return ws
                except Exception as e:
                    log.warning(f"[workspace] Auto-bootstrap candidate '{cand}' failed: {e}")

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
            # The shell result's actual command output lives in `detail.stdout`,
            # NOT in `message` (which is just a status string like
            # "Command executed successfully: ..."). Reading `message` would
            # wire garbage into repo_url and break the workspace<->repo binding.
            data = await res.json()
            detail = data.get("detail") or {}
            out = detail.get("stdout", "") or ""
            return "\n".join(ln.strip() for ln in out.splitlines() if ln.strip())
    except Exception:
        return None


async def _autowire_created_repo(
    workspace_id: str,
    creds: ResolvedCredentials,
    repo_cmd: str | None = None,
    exec_data: dict | None = None,
) -> None:
    """After a successful ``gh repo create`` inside a workspace, fetch the new
    remote and bind it to the workspace settings (repo_url / git_remote /
    default_branch). This guarantees the workspace's "Source Repository" is
    populated even if the model forgets to call WorkspaceSettingsUpdateRequest.

    The repo URL is taken from the ``repo_create`` tool RESULT first (the git
    handler returns the created clone URL), which is authoritative and avoids a
    second GitHub round-trip. Only if that is missing do we fall back to
    ``gh repo view <name>`` (retry once to absorb propagation lag), and finally
    to the workspace's configured git remote.

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
        # 1) Prefer the URL the repo_create tool already returned.
        if isinstance(exec_data, dict):
            _ed = exec_data.get("detail") if isinstance(exec_data.get("detail"), dict) else exec_data
            url = (_ed or {}).get("repo_url") or exec_data.get("repo_url")

        # 2) Fallback: resolve via gh repo view (retry once for propagation lag).
        if not url:
            name = _extract_repo_name_from_cmd(repo_cmd)
            if name:
                out = await _shell_out(workspace_id, uc, f"gh repo view {name} --json url -q .url")
                if not out:
                    import asyncio
                    await asyncio.sleep(2.0)
                    out = await _shell_out(workspace_id, uc, f"gh repo view {name} --json url -q .url")
                if out:
                    url = out.strip().strip('"').strip("'")

        # 3) Last resort: whatever remote is configured now.
        if not url:
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

            # Persist the tool payload as VALID JSON (not a Python `repr`, which
            # uses single quotes and is un-parseable by json.loads). This is what
            # the history-reconstruction path re-ingests; if it is stored as a
            # repr (or dropped) the reconstruction emits `{"action": ...,
            # "payload": null}` assistant turns that poison the model into
            # mirroring the malformed `null` payload — the exact failure seen on
            # "Tweak or Fix Results" refinements.
            if current_action_payload is not None and not isinstance(current_action_payload, str):
                _payload_store = json.dumps(current_action_payload)
            elif current_action_payload is not None:
                _payload_store = current_action_payload
            else:
                _payload_store = None
            normalized.append({
                "type": ev_type,
                "data": summary_msg,
                "timestamp": timestamp,
                "raw_type": ev_type,
                "raw_data": res_str[:400],
                "tool": current_action,
                "payload": _payload_store
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
        if mission_id and workspace_id:
            try:
                async with shared_http_client() as client:
                    await client.patch(
                        f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                        json={"workspace_id": workspace_id},
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    )
            except Exception as e:
                log.warning(f"[AgentLoop] Failed to patch workspace_id to mission {mission_id}: {e}")
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

    # P2: Pre-compute VRAM-safe inference params ONCE per mission instead of an
    # HTTP round-trip to the LLM on every iteration and every retry (a 30-60
    # iteration mission with 3 retries each was making ~180 redundant calls).
    # Settings and VRAM posture are stable for the duration of a single mission.
    mission_vram_params = await get_vram_safe_params(selected_model, settings)
    log.info(f"[AgentLoop] Cached mission VRAM params: {mission_vram_params}")

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
    # Lower than the historical 12 so a chatty model fails fast (triggering the
    # worker's same-family retry) instead of yapping until the hard time cap.
    MAX_IDLE_NUDGES = 6
    loop_start = asyncio.get_event_loop().time()
    exec_data = None
    ans = ""
    successful_tool_calls = 0
    # Queue of tool calls remaining from a batched inference (a proven command
    # chain). One LLM inference can enqueue many steps; each subsequent turn runs
    # one queued step WITHOUT a new inference, so the whole chain costs a single
    # reasoning cycle instead of one per step.
    pending_batch: list[dict] = []
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
        # Thinking-capable models default to emitting reasoning; a thinking-only
        # reply would strip to an empty plan (the provider only surfaces
        # thinking when show_thinking is requested), so keep the plan phase
        # direct like the summary/reflection phases.
        plan_data = await execute_inference(
            provider,
            selected_model,
            plan_prompt,
            {"temperature": 0.1, "num_predict": 512, "enable_thinking": False, "include_reasoning": False},
        )
        generated_plan = plan_data.get("message", {}).get("content", "").strip()
        if generated_plan:
            action_log.append(f"PLAN GENERATED:\n{generated_plan}")
            log.info(f"[AgentLoop] Planning phase complete. Plan:\n{generated_plan[:500]}")
            await stream_event(
                "system",
                f"PLAN GENERATED:\n{generated_plan[:2000]}",
            )
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
                # Restore adopted workspace + written files so guidance/phase and
                # payload workspace_id survive a resume (see _save_checkpoint).
                _cp_ws = cp.get("workspace_id")
                if _cp_ws:
                    workspace_id = _cp_ws
                _cp_files = cp.get("written_files")
                if isinstance(_cp_files, list):
                    _written_files.update(_cp_files)
                log.info(f"[AgentLoop] Resuming mission {mission_id} from iteration {start_iteration} (restored {len(action_log)} action log entries)")
            # NOTE: do NOT close r_cp — it is the module-level shared singleton
            # returned by _get_redis_cmd(). Closing it here would set the
            # connection dead while _redis_cmd still points to it (no None
            # reset), causing all subsequent Redis ops to fail.
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

    # Runtime hard wall-clock cap for a single mission. Operational config MUST be
    # read from the Config DB (GlobalSetting `raven_max_total_seconds`), NOT from
    # .env/env — the runtime only reads .env during seeding. Falls back to the
    # module default if the setting is absent. Mirrors `raven_max_iterations` above.
    try:
        raven_max_total = int(str((settings or {}).get("raven_max_total_seconds", RAVEN_MAX_TOTAL_SECONDS)).strip())
    except (ValueError, TypeError):
        raven_max_total = RAVEN_MAX_TOTAL_SECONDS
    if raven_max_total <= 0:
        raven_max_total = float("inf")  # 0 = no lifetime cap

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

    def _compact_conversation(conv: list[dict], keep_last: int = 10, threshold: int = 48000) -> list[dict]:
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
                # Persist the adopted workspace + files-written so a RESUMED mission
                # keeps its build phase. Without this, workspace_id reset to None on
                # resume and the adaptive guidance wrongly reported the 'create_ws'
                # phase forever (never giving batch/budget guidance), and blank
                # workspace_id could leak into payloads.
                "workspace_id": workspace_id,
                "written_files": sorted(_written_files),
                "updated_at": asyncio.get_event_loop().time(),
            }
            await r_cp.setex(
                f"raven:checkpoint:{mission_id}",
                (raven_max_total if raven_max_total != float("inf") else RAVEN_MAX_TOTAL_SECONDS) + 60,
                json.dumps(cp_data),
            )
            # NOTE: do NOT close r_cp — shared singleton; see checkpoint-load comment.
        except Exception as e:
            log.warning(f"[AgentLoop] Failed to save checkpoint at iter {iter_num}: {e}")

    async def _clear_checkpoint() -> None:
        if not mission_id:
            return
        try:
            r_cp = await _get_redis_cmd()
            await r_cp.delete(f"raven:checkpoint:{mission_id}")
            # NOTE: do NOT close r_cp — shared singleton; see checkpoint-load comment.
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
                                # The normalized audit log carries the tool payload
                                # on the *result* event (action_payload events are
                                # dropped during summarization). Prefer it so the
                                # reconstructed "previous tool call" round-trips with
                                # its real arguments instead of `null`, which would
                                # otherwise teach the model to emit `payload: null`.
                                _pv = ev.get("payload")
                                if isinstance(_pv, str):
                                    try:
                                        _pv = json.loads(_pv)
                                    except Exception:
                                        _pv = current_payload
                                elif _pv is None:
                                    _pv = current_payload
                                if not isinstance(_pv, dict):
                                    _pv = current_payload if isinstance(current_payload, dict) else {}
                                tool_json = {"action": tool_name, "payload": _pv}
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
        if elapsed_total > raven_max_total:
            log.error(f"[AgentLoop] HARD TIMEOUT after {elapsed_total:.0f}s at iteration {iter_num}")
            ans = f"ERROR: Raven job exceeded time limit of {raven_max_total}s. Partial result: {ans or 'No output yet'}"
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

        # ── Batched command-chain continuation ──────────────────────────────────
        # If a previous inference returned an array of tool calls (a proven
        # command chain), the remainder lives in `pending_batch`. Execute the next
        # queued step WITHOUT a new LLM inference — the whole chain is driven by a
        # single reasoning cycle instead of one per step.
        skip_inference, tool_data = _next_batch_step(pending_batch)
        _guidance_branch = "batch_continuation" if skip_inference else "unknown"
        if skip_inference:
            log.info(f"[AgentLoop] Batch continuation: executing queued tool {tool_data.get('action')!r} ({len(pending_batch)} remaining)")

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
                # Keep tool output large enough that the model can actually SEE full
                # test failures / tracebacks — the whole point of a big context. The
                # old 500/2000-char caps hid the exact pytest error lines the model
                # needed to fix, which caused endless "run tests -> can't see the
                # failure -> guess -> re-run" loops. Bounded, but generous.
                _PER_FIELD_CAP = 6000
                _TOTAL_CAP = 12000
                safe_exec_data = exec_data.copy() if isinstance(exec_data, dict) else {"result": str(exec_data)}
                if "detail" in safe_exec_data and isinstance(safe_exec_data["detail"], dict):
                    for key in ["content", "stdout", "stderr"]:
                        val = safe_exec_data["detail"].get(key)
                        if val and len(str(val)) > _PER_FIELD_CAP:
                            # Keep the TAIL: pytest prints the failing assertions and
                            # the short summary at the END, which is what we need.
                            s = str(val)
                            safe_exec_data["detail"][key] = (
                                "...[TRUNCATED HEAD]...\n" + s[-_PER_FIELD_CAP:]
                            )

                # Sanitize credentials from execution results before feeding to LLM
                safe_exec_data = sanitize_for_llm(safe_exec_data)

                # Hard limit on total exec_data size (bounded, but large enough for
                # a full traceback).
                exec_json = json.dumps(safe_exec_data)
                if len(exec_json) > _TOTAL_CAP:
                    exec_json = exec_json[:_TOTAL_CAP] + "\n...[TRUNCATED FOR CONTEXT WINDOW]..."

                user_content += f"\n\nLAST TOOL RESULT:\n{exec_json}"
            # PIPELINE-DRIVEN ADAPTIVE GUIDANCE: steer the LLM toward the efficient
            # next move based on live mission state (phase, last failure, budget,
            # repetition) instead of a single static instruction. This is what
            # makes long autonomous builds converge inside the time budget.
            try:
                _last_status = (exec_data.get("status") if isinstance(exec_data, dict) else None)
            except Exception:
                _last_status = None
            _elapsed_frac = (elapsed_total / raven_max_total) if raven_max_total else 0.0
            _repeating = detect_no_progress(_recent_shell_runs, window=NO_PROGRESS_WINDOW)
            _guidance = build_adaptive_guidance(
                workspace_id=workspace_id,
                files_written=len(_written_files),
                last_status=_last_status,
                elapsed_frac=_elapsed_frac,
                repeating=_repeating,
            )
            _guidance_branch = guidance_branch(
                workspace_id=workspace_id,
                last_status=_last_status,
                elapsed_frac=_elapsed_frac,
                repeating=_repeating,
            )
            if _guidance:
                user_content += "\n\n" + _guidance

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
                    # P2: reuse the per-mission cached VRAM params (computed once
                    # above) rather than re-querying the LLM every retry.
                    vram_params = mission_vram_params
                    ollama_payload["options"] = vram_params
                    log.info(f"[AgentLoop] Inference options: {vram_params}")
                    log.info(f"[AgentLoop] Executing inference (Attempt {retry_count + 1}/{MAX_INFERENCE_RETRIES}) for {model_to_use}")

                    async def chunk_logger(chunk: str):
                        await stream_event("reasoning", chunk)

                    inference_options = ollama_payload.get("options", {})
                    if not isinstance(inference_options, dict):
                        inference_options = {}
                    if skip_inference:
                        # Continue a batched command chain: the next step is already
                        # dequeued into `tool_data`. No new LLM inference is performed.
                        data = {"message": {"content": f"[batch] {tool_data.get('action')}"}}
                    else:
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
            # Structural preview: the first non-whitespace char reveals whether the
            # model returned an object ('{') or a batch array ('['). Honest signal
            # for diagnosing batching without dumping full (huge) file content.
            _stripped = ans.lstrip()
            _shape = _stripped[:1] if _stripped else ""
            log.info(
                f"[AgentLoop] Reply shape: first_char={_shape!r} "
                f"len={len(ans)} has_json_array={'[' in ans[:50]}"
            )
            # Record this turn so the model retains context in subsequent iterations.
            if ans and ans.strip() and not skip_inference:
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

        if skip_inference:
            # tool_data was already dequeued from pending_batch at iteration start;
            # it is the next step of an in-flight proven-command chain.
            pass
        else:
            # A single inference may return a *batch* of tool calls (a proven command
            # chain assembled from memory/history). Execute the first immediately and
            # queue the rest — the whole chain runs from THIS one inference instead of
            # burning a reasoning cycle per step.
            batch = extract_action_batch(ans)
            if batch:
                tool_data = batch.pop(0)
                pending_batch.extend(batch)
                log.info(f"[AgentLoop] Batching {len(batch) + 1} tool calls from one inference.")
            else:
                tool_data = extract_action_json(ans)
            if not isinstance(tool_data, dict):
                tool_data = None

            # Honest per-iteration observability: which guidance branch the pipeline
            # showed the model this turn, and how many tool calls it returned. Makes
            # batching/convergence measurable from logs instead of inferred.
            _returned_calls = (len(batch) + 1) if batch else (1 if tool_data else 0)
            log.info(
                f"[AgentLoop] Guidance branch={_guidance_branch!r} | "
                f"model returned {_returned_calls} tool call(s)"
            )

            # FALLBACK: the local 35B model frequently emits a numbered prose
            # plan ("1. WorkspaceCreateRequest ... 2. GitOperationRequest action
            # 'push' ...") instead of JSON. Recover those tool calls so Raven
            # actually executes work instead of "completing" with 0 tool calls.
            if not tool_data:
                from services.gateway.prose_tools import extract_action_prose

                prose = extract_action_prose(ans)
                if prose:
                    tool_data = prose.pop(0)
                    pending_batch.extend(prose)
                    log.info(
                        f"[AgentLoop] Recovered {len(prose) + 1} prose tool call(s) "
                        f"from non-JSON model output."
                    )

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

            # Structural gate: every extraction path (batch, single, prose
            # recovery, pending_batch continuation) must funnel through the same
            # validation. A malformed file-write/read/patch call (e.g. a
            # truncated bag-of-words dict with file_path ":" and content ":") is
            # REJECTED here so it is never dispatched to the execution service.
            # We steer the model to re-emit a well-formed call instead.
            if not _valid_structured_tool(tool_data):
                log.warning(
                    f"[AgentLoop] Rejected malformed tool call (action="
                    f"{tool_data.get('action') or tool_data.get('@type')}, "
                    f"file_path={tool_data.get('file_path')!r}): re-prompting for "
                    f"a well-formed call."
                )
                action_log.append(
                    f"ITERATION {iter_num}: Your tool call was malformed "
                    f"(missing/invalid file_path or content). Emit a single valid "
                    f"JSON object with a real file_path and complete content, e.g. "
                    f'{{"@type":"WorkspaceFileWriteRequest","file_path":"envdiff/core.py",'
                    f'"content":"..."}}.'
                )
                tool_data = None

            if tool_data:
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

            # EARLY COMPLETION: the model has already produced successful tool
            # calls and now replies with a clear "I'm done" statement (e.g.
            # "mission complete", "successfully created <repo>"). Treat this as a
            # genuine completion signal and terminate cleanly — do NOT keep
            # nudging a correctly-finished agent to emit more tool calls, which
            # would loop it into "complete → re-prompt → complete" for
            # MAX_IDLE_NUDGES turns.
            if successful_tool_calls > 0 and ans:
                _ans_l = ans.lower()
                if any(ind in _ans_l for ind in COMPLETION_INDICATORS):
                    log.info(f"[AgentLoop] Completion signal detected in text reply after {successful_tool_calls} tool call(s). Terminating mission as complete.")
                    await _clear_checkpoint()
                    break

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
                # STALLED-COMPLETION: the model made real progress, everything it
                # wrote is verified, and it has now produced two consecutive
                # text-only replies without a tool call even after nudging.
                # It is finished (regression: model wrote answer.md, verified it,
                # but never uttered a COMPLETION_INDICATOR phrase, so the
                # early-completion check above never fired and it looped until
                # MAX_IDLE_NUDGES -> runaway ERROR). A verified stall is success:
                # the final text reply IS the deliverable summary.
                # Extra guards: a real artifact must exist in the workspace
                # (_written_files non-empty) and no queued batch call may be
                # pending — otherwise a stall after a rejected/malformed write
                # would "complete" a mission whose deliverable was never created
                # (regression: flyer mission stalled after the write call was
                # rejected as malformed and the mission ended without a PDF).
                if (
                    _consecutive_no_tool >= 2
                    and _written_files
                    and not pending_verification(_written_files, _verified_files)
                    and not pending_batch
                ):
                    log.info(f"[AgentLoop] {_consecutive_no_tool} consecutive no-tool replies after {successful_tool_calls} successful tool call(s) with a verified artifact and nothing queued — treating as completion.")
                    await _clear_checkpoint()
                    break
                # The mission is multi-step; a plan-as-text reply is NOT "done".
                # Nudge the agent to keep executing tool calls instead of ending
                # the loop after the first one. Only give up once it has stalled
                # for MAX_IDLE_NUDGES consecutive no-tool replies (runaway guard).
                if _consecutive_no_tool >= MAX_IDLE_NUDGES:
                    log.error(f"[AgentLoop] {_consecutive_no_tool} consecutive no-tool replies after {successful_tool_calls} tool call(s). Terminating to prevent runaway.")
                    ans = (
                        "ERROR: Agent stalled — produced no tool calls for several turns after making progress. "
                        "Last response: " + (ans[:200] if ans else "empty")
                        + "\n\nPOSSIBLE CAUSES:\n"
                        "- The model got confused by its own output and needs a fresh prompt\n"
                        "- A tool call was syntactically invalid and was silently ignored\n"
                        "- The model ran out of context or hit a quality ceiling\n\n"
                        "RECOMMENDED FIX: Retry the mission with a more specific, step-by-step prompt. "
                        "Break the work into smaller subtasks (e.g. 'Step 1: create the workspace, Step 2: write the file, Step 3: run the test'). "
                        "If the issue persists, increase the model size or reduce the mission scope."
                    )
                    await _clear_checkpoint()
                    break
                log.warning(f"[AgentLoop] Textual reply after progress (idle {_consecutive_no_tool}/{MAX_IDLE_NUDGES}); nudging to continue with tool calls.")
                action_log.append(
                    f"ITERATION {iter_num}: You described the next step but did not emit a tool "
                    f"call. The mission is NOT complete — you must keep executing it with tool "
                    f"calls (e.g. WorkspaceShellRequest to run `gh repo create`, "
                    f"WorkspaceFileWriteRequest to write files, then git add/commit/push). Emit "
                    f"the next concrete tool call now; do not stop at a description. "
                    f"If you have genuinely finished everything the mission asked for, "
                    f"end your reply with exactly: Mission complete."
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

        # ── Git verb fast-path ──
        # The model emits git verbs like `repo_create` / `git_commit`; after
        # underscore-stripping these become `repocreate` / `gitcommit`, which are
        # NOT in ALLOWED_TOOLS. The Tier-3 fuzzy matcher would then corrupt them
        # (e.g. `repocreate` fuzzy-matches `note_create` at ratio 0.667 and the
        # call fails as "Unknown action: note_create"). Route any recognized git
        # verb to `gitoperationrequest` up front so it is never fuzzy-hijacked.
        if action_name in _GIT_VALID_ACTIONS or action_name in {
            "git" + v for v in _GIT_VALID_ACTIONS
        } or action_name in {v.replace("_", "") for v in _GIT_VALID_ACTIONS}:
            action_name = "gitoperationrequest"

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
                "branch": "gitoperationrequest",
                "remote": "gitoperationrequest",
                "checkout": "gitoperationrequest",
                "fetch": "gitoperationrequest",
                "merge": "gitoperationrequest",
                "rebase": "gitoperationrequest",
                "stash": "gitoperationrequest",
                "reset": "gitoperationrequest",
                "tag": "gitoperationrequest",
                "clone": "gitoperationrequest",
                "switch": "gitoperationrequest",
                "restore": "gitoperationrequest",
                "show": "gitoperationrequest",
                "init": "gitoperationrequest",
                "restart_service": "controlplanerequest",
                "recall": "ravenrecallrequest",
                "ravenrecall": "ravenrecallrequest",
                "missionhistory": "ravenrecallrequest",
                # Note CRUD hallucinations -> NoteRequest (the only note tool)
                "note_create": "noterequest",
                "note_delete": "noterequest",
                "note_list": "noterequest",
                "note_update": "noterequest",
                "create_note": "noterequest",
                "delete_note": "noterequest",
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
                        "Web Tools": ["websearchrequest", "webreadrequest", "webscraperrequest"],
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
            # ROBUSTNESS: the model sometimes emits a tool call whose payload is
            # null/missing or a JSON *string* (e.g. {"action": "WorkspaceShellRequest",
            # "payload": null}). Assigning into ``payload`` later (user_context,
            # workspace_id, ...) then raises
            #   TypeError: 'NoneType' object does not support item assignment
            # which aborts the whole step. Coerce to a dict so the tool still
            # dispatches (the execution service will return a clean schema error
            # rather than crashing the loop).
            if not isinstance(payload, dict):
                if isinstance(payload, str):
                    try:
                        _parsed = json.loads(payload)
                        payload = _parsed if isinstance(_parsed, dict) else {"value": payload}
                    except (json.JSONDecodeError, ValueError):
                        payload = {"value": payload}
                elif payload is None:
                    payload = {}
                else:
                    payload = {"value": payload}

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
                "webscraperrequest": (EXECUTION_SVC, "/execute/web_scraper"),
                "codesearchrequest": (EXECUTION_SVC, "/execute/code_search"),
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
                "workspaceportexposerequest": (WORKSPACE_RUNTIME_SVC, "/ports/expose"),
                "workspace_expose_port": (WORKSPACE_RUNTIME_SVC, "/ports/expose"),
                "expose_port": (WORKSPACE_RUNTIME_SVC, "/ports/expose"),
                "port_expose": (WORKSPACE_RUNTIME_SVC, "/ports/expose"),
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

            # INTERCEPTION: route raw workspace-shell git/gh commands through the
            # credentialed, guard-railed git tool. The shell has no git creds, so
            # a model that forgets to use GitOperationRequest (and runs
            # `git push` / `gh repo create` directly) would never authenticate.
            # This is the durable backstop that makes "commit and push" reliable
            # regardless of how the model phrased the request.
            _git_batch = None
            if lookup_action == "workspaceshellrequest" and isinstance(payload, dict):
                _new_action, payload, _git_batch = _route_workspace_shell_to_git(lookup_action, payload)
                if _new_action != lookup_action:
                    log.info("[AgentLoop] Intercepted shell git/gh command -> git tool")
                    # Route the PRIMARY step through the git endpoint (not the
                    # shell endpoint). Without this, `lookup_action` stays
                    # `workspaceshellrequest`, the first git op is POSTed to
                    # /execute/workspace_shell with no `command`, and the shell
                    # handler rejects it ("Neither command nor commands
                    # provided"). Only the secondary batch steps fanned out to
                    # /execute/git, so `add`/`commit` silently broke.
                    lookup_action = _new_action

            if lookup_action in action_map:
                svc_base, endpoint = action_map[lookup_action]

                # RECOVERY: The model sometimes sends a GitOperationRequest whose
                # `action` field holds the *tool type name* ("GitOperationRequest")
                # instead of the actual git verb (add/commit/...). That 422s at the
                # execution service. Normalize it to a valid verb before dispatch.
                if lookup_action == "gitoperationrequest" and isinstance(payload, dict):
                    if "_outer_action" not in payload:
                        payload["_outer_action"] = tool_data.get("action") or tool_data.get("operation") or ""
                    _normalize_git_payload_action(payload)
                    payload.pop("_outer_action", None)

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
                    _uc = {
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
                    if isinstance(payload, dict):
                        payload["user_context"] = _uc
                    # Fan-out payloads (compound git pipelines) also need creds
                    # and the mission workspace id so each step runs scoped.
                    if _git_batch:
                        for _b in _git_batch:
                            if isinstance(_b, dict):
                                _b.setdefault("user_context", _uc)


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
                    if _git_batch:
                        for _b in _git_batch:
                            if isinstance(_b, dict):
                                _b.setdefault("workspace_id", workspace_id)

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
                            _rr = await _get_redis_cmd()
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
                #
                # Treat a blank/whitespace workspace_id the same as unassigned: the model
                # sometimes emits its own `"workspace_id": ""` before a workspace exists,
                # which would otherwise slip past a `is None` check and 400 at the
                # execution service ("No workspace_id provided"). Strip such blanks from
                # the payload so this guard reliably catches them.
                _loop_ws_ok = _has_valid_workspace_id(workspace_id)
                if isinstance(payload, dict):
                    _pl_ws = payload.get("workspace_id")
                    if _pl_ws is not None and not str(_pl_ws).strip():
                        payload.pop("workspace_id", None)
                _pl_ws_ok = isinstance(payload, dict) and _has_valid_workspace_id(payload.get("workspace_id"))
                if (not _loop_ws_ok and not _pl_ws_ok) and lookup_action in WORKSPACE_TOOL_ACTIONS and lookup_action != "workspacecreaterequest":
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
                        # WorkspaceSettingsUpdateRequest must PATCH the existing
                        # workspace (workspace_runtime only exposes PATCH /workspaces/{id},
                        # not POST) — POSTing there returns 405. Every other
                        # action is a POST.
                        _http_method = (
                            "patch" if lookup_action == "workspacesettingsupdaterequest" else "post"
                        )
                        resp = await getattr(client, _http_method)(
                            f"{svc_base}{endpoint}",
                            json=payload,
                            headers={"X-Internal-Secret": INTERNAL_SECRET},
                            timeout=aiohttp.ClientTimeout(total=120.0),
                        )
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
                            # The execution service may return an empty body or a
                            # literal `null` (e.g. 204 / no content). Coerce that to
                            # a safe dict so the downstream git-batch merge (which
                            # assigns into exec_data["message"]/@"status") cannot
                            # raise `TypeError: 'NoneType' object does not support
                            # item assignment`.
                            if not isinstance(exec_data, dict):
                                exec_data = {"status": "SUCCESS", "message": "", "detail": {}}
                            # Auto-wire the git 'origin' remote whenever a repo_url is
                            # bound via WorkspaceSettingsUpdateRequest. workspace_runtime
                            # only STORES repo_url — it never runs `git remote add` — so
                            # without this the workspace has no origin and a later
                            # `git push` fails with "Cannot determine the push target
                            # repository". The model had been shell-hacking this (observed
                            # live, mission 6: it wrote a raven_memory.md lesson about it).
                            # Reuse the proven GitOperationRequest remote_add handler
                            # (idempotent, token-injected) so the create-repo -> push flow
                            # is reliable without the model remembering to wire it.
                            if _http_method == "patch" and resp.status < 400 and isinstance(payload, dict):
                                _new_repo = (payload.get("repo_url") or "").strip()
                                if _new_repo:
                                    try:
                                        async with shared_http_client() as _rclient:
                                            _rr = await _rclient.post(
                                                f"{EXECUTION_SVC}/execute/git",
                                                json={
                                                    "action": "remote_add",
                                                    "remote_name": "origin",
                                                    "repo_url": _new_repo,
                                                    "workspace_id": workspace_id,
                                                    "user_context": payload.get("user_context") or {},
                                                },
                                                headers={"X-Internal-Secret": INTERNAL_SECRET},
                                                timeout=aiohttp.ClientTimeout(total=60.0),
                                            )
                                            _rj = await _rr.json()
                                            log.info(
                                                f"[AgentLoop] Auto-wired git origin for "
                                                f"{workspace_id}: {_rj.get('status')}"
                                            )
                                    except Exception as _re:
                                        log.warning(
                                            f"[AgentLoop] Auto remote_add failed "
                                            f"(model can still wire via shell): {_re}"
                                        )
                            # Fan out any additional git ops from a compound
                            # intercepted shell command (e.g.
                            # `git init && git remote add origin <url> && git fetch`)
                            # so the full git pipeline runs through the credentialed tool.
                            if _git_batch and len(_git_batch) > 1:
                                for _gp in _git_batch[1:]:
                                    try:
                                        async with shared_http_client() as _fclient:
                                            _fr = await _fclient.post(
                                                f"{EXECUTION_SVC}/execute/git",
                                                json=_gp,
                                                headers={"X-Internal-Secret": INTERNAL_SECRET},
                                                timeout=aiohttp.ClientTimeout(total=120.0),
                                            )
                                            _fd = await _fr.json()
                                    except Exception as _fe:
                                        _fd = {"status": "ERROR", "message": f"git batch step failed: {_fe}"}
                                    log.info(f"[AgentLoop] Git batch step {_gp.get('action')}: {_fd.get('status')}")
                                    exec_data["message"] = (
                                        exec_data.get("message", "") + " | " + _fd.get("message", "")
                                    ).strip(" |")
                                    if _fd.get("status") != "SUCCESS":
                                        exec_data["status"] = _fd.get("status", "FAILURE")
                                    if isinstance(_fd.get("detail"), dict):
                                        exec_data.setdefault("detail", {})[_gp.get("action", "step")] = _fd["detail"]
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
                                    await _autowire_created_repo(workspace_id, creds, _repo_cmd, exec_data)

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

                        # --- POST-WRITE LINT HOOK ---
                        # MUST be in the success branch: lint only makes sense when
                        # the write itself succeeded. Running it in the `else`
                        # (failure) branch was a bug — lint never fired after a
                        # clean write. Re-run here immediately after tracking the
                        # write; if lint fails we retroactively downgrade the result
                        # so the success counter / stagnation logic see it correctly.
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
                                    # Retroactively un-count this write: it produced lint
                                    # errors so it is not a successful tool execution.
                                    successful_tool_calls -= 1
                                    sig2 = action_signature(action_name, payload)
                                    _recent_actions.append((sig2, False))
                                    if len(_recent_actions) > 12:
                                        _recent_actions = _recent_actions[-12:]
                                else:
                                    # A clean post-write lint satisfies the
                                    # static-check verification requirement for this
                                    # file, so it no longer counts as "unverified"
                                    # at the finish gate.
                                    _verified_files.add(file_path)
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
                        # A step FAILED. If we are mid-batch, abandon the rest of
                        # the queued chain: later steps were planned assuming this
                        # one succeeded, so running them blindly would compound the
                        # error. Draining forces a fresh inference next iteration so
                        # the model re-plans from the actual failure.
                        if pending_batch:
                            log.warning(
                                f"[AgentLoop] Batched step failed; discarding "
                                f"{len(pending_batch)} queued step(s) to force re-plan."
                            )
                            pending_batch.clear()

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


    async def _persist_learning(
            summary: str,
            reflection: str = "",
            rule: str = "",
            root_cause: str = "",
            outcome: str = "success",
            confidence: float = 0.5,
            supersedes: list[str] | None = None,
        ) -> None:
        try:
            ql = query.lower()
            tags = ["raven", "autonomous", "learning"]
            if "workspace" in ql:
                tags.append("workspace")
            if "git" in ql:
                tags.append("git")
            if "python" in ql:
                tags.append("python")
            if "javascript" in ql or "typescript" in ql or "node" in ql:
                tags.append("javascript")
            if "go" in ql:
                tags.append("go")
            if "rust" in ql:
                tags.append("rust")
            if "deploy" in ql or "restart" in ql:
                tags.append("deployment")
            if "fix" in ql or "repair" in ql or "bug" in ql:
                tags.append("repair")
            if "web" in ql or "search" in ql or "browser" in ql:
                tags.append("web")
            if "search" in ql or "find" in ql or "research" in ql:
                tags.append("search")
            if "scrape" in ql or "scraper" in ql or "price" in ql or "pricing" in ql:
                tags.append("scrape")

            # Parse structured fields out of a free-form reflection when the
            # model did not fill them explicitly.
            parsed = _parse_lesson_marker(reflection or summary)
            rule = (rule or parsed.get("rule") or "").strip()
            root_cause = (root_cause or parsed.get("root_cause") or "").strip()
            if not outcome or outcome not in ("success", "failure", "partial"):
                outcome = parsed.get("outcome") or "success"
            try:
                confidence = float(confidence if confidence else parsed.get("confidence") or 0.5)
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))
            supersedes = supersedes or parsed.get("supersedes") or []

            # The persisted `content` is the evidence/narrative; the
            # transferable takeaway lives in `rule` as a first-class field.
            content = (reflection.strip() or summary.strip())
            topic = f"Raven lesson: {query[:80]}"
            payload = {
                "user_context": creds.model_dump(),
                "topic": topic,
                "content": content,
                "rule": rule,
                "root_cause": root_cause,
                "outcome": outcome,
                "confidence": confidence,
                "tags": list(dict.fromkeys(tags)),
                "supersedes": list(dict.fromkeys(supersedes)),
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

            # BRIDGE: keep the workspace's local raven_memory.md journal in sync
            # with the global system_learnings store. system_learnings is what
            # cross-workspace missions retrieve, but raven_memory.md is the
            # human-readable, auditable journal for THIS workspace — and the
            # training curriculum's success criteria checks it. Persisting here
            # (not relying on the model's prompt instruction) guarantees the two
            # layers never diverge, and gives repeatable tasks in the same
            # workspace a durable, file-backed memory.
            if workspace_id and (reflection or rule):
                try:
                    _rule_line = f"\n**RULE:** {rule}" if rule else ""
                    _cause_line = f"\n**ROOT CAUSE:** {root_cause}" if root_cause else ""
                    _outcome_line = f"\n**OUTCOME:** {outcome} (conf {confidence:.2f})"
                    _entry = (
                        f"\n## {datetime.now().isoformat(timespec='seconds')}\n\n"
                        f"{reflection.strip() or summary.strip()}"
                        f"{_rule_line}{_cause_line}{_outcome_line}\n"
                    )
                    async with shared_http_client() as client:
                        _wr = await client.post(
                            f"{EXECUTION_SVC}/execute/workspace_file_write",
                            json={
                                "workspace_id": workspace_id,
                                "path": "raven_memory.md",
                                "content": _entry,
                                "append": True,
                                "user_context": creds.model_dump(),
                            },
                            headers={"X-Internal-Secret": INTERNAL_SECRET},
                            timeout=aiohttp.ClientTimeout(total=30.0),
                        )
                        if _wr.status != 200:
                            log.warning(f"[AgentLoop] raven_memory.md append failed: {_wr.status} {_wr.text}")
                except Exception as _me:
                    log.warning(f"[AgentLoop] raven_memory.md append skipped: {_me}")
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
            # LLM response was empty — build summary from action log only.
            # BUG FIX: previously execute_inference was never called in this branch;
            # summary_prompt was built and silently discarded, always falling through
            # to the static fallback. Now we actually call inference here too.
            summary_prompt = [
                {"role": "system", "content": "You are Raven. Summarize the mission result for the user in clean, natural language. Do NOT use JSON. Do NOT repeat yourself. Be concise. State what was accomplished based on the actions taken."},
                {"role": "user", "content": f"Mission: {query}\n\nActions taken:\n{bounded_log}\n\nThe LLM did not produce a final response, but the following actions were completed successfully. Summarize what was accomplished. Output the summary directly as your response — do not draft, plan, or repeat phrases like 'I will write'."}
            ]
            try:
                async with asyncio.timeout(30.0):
                    data = await execute_inference(provider, selected_model, summary_prompt, {"temperature": 0.0, "enable_thinking": False})
                    ans = data.get("message", {}).get("content", "").strip() or ans
            except Exception as e:
                log.warning(f"[AgentLoop] Summarization phase (empty-ans path) failed: {e}")
        else:
            summary_prompt = [
                {"role": "system", "content": "You are Raven. Summarize the mission result for the user in clean, natural language. Do NOT use JSON. Do NOT repeat yourself. Be concise. Do NOT say the mission failed unless the tool execution itself reported an error."},
                {"role": "user", "content": f"Mission: {query}\n\nActions taken:\n{bounded_log}\n\nRaw output: {ans}\n\nPlease provide the final clean summary now: Output it directly as your response — do not draft, plan, or repeat phrases like 'I will write'."}
            ]
            try:
                async with asyncio.timeout(30.0):
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
    _lesson_rule = ""
    _lesson_cause = ""
    _lesson_outcome = "success"
    _lesson_confidence = 0.5
    if action_log and successful_tool_calls > 0:
        try:
            raven_reflection = await load_prompt(get_http_client(), PROMPT_RAVEN_REFLECTION)
            reflection_prompt = [
                {"role": "system", "content": raven_reflection},
                {"role": "user", "content": (
                    f"Mission: {query}\n\nPlan:\n{generated_plan}\n\n"
                    f"Actions taken:\n{bounded_log}\n\nFinal result: {ans}\n\n"
                    "Reflect and EXTRACT A REUSABLE LESSON. Reply ONLY with these "
                    "fields, one per line, no preamble:\n"
                    "RULE: <when <situation>, do <action> — the transferable takeaway>\n"
                    "ROOT CAUSE: <why the naive/previous approach failed or what was misunderstood>\n"
                    "OUTCOME: <success | partial | failure>\n"
                    "CONFIDENCE: <0.0-1.0; 1.0 = verified-applied, 0.5 = plausible, 0.2 = uncertain>\n"
                    "Then, after a blank line, write 1-3 sentences of evidence/context."
                )},
            ]
            reflection_data = await execute_inference(provider, selected_model, reflection_prompt, {"temperature": 0.1, "enable_thinking": False})
            reflection_summary = reflection_data.get("message", {}).get("content", "").strip()
            if reflection_summary:
                _parsed = _parse_lesson_marker(reflection_summary)
                _lesson_rule = _parsed.get("rule", "")
                _lesson_cause = _parsed.get("root_cause", "")
                _lesson_outcome = _parsed.get("outcome") or "success"
                if _lesson_outcome not in ("success", "partial", "failure"):
                    _lesson_outcome = "success"
                try:
                    _lesson_confidence = float(_parsed.get("confidence") or 0.5)
                except (TypeError, ValueError):
                    _lesson_confidence = 0.5
                _lesson_confidence = max(0.0, min(1.0, _lesson_confidence))
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
                # Honest outcome: a mission whose final answer still reports a
                # failure/incomplete is a negative lesson, not a win.
                _honest_outcome = _lesson_outcome
                _low = (ans or "").lower()
                if re.search(r"\b(failed|error|incomplete|cannot|unable)\b", _low) and "success" not in _low:
                    _honest_outcome = "failure"
                await _persist_learning(
                    learning_summary,
                    reflection=reflection_summary,
                    rule=_lesson_rule,
                    root_cause=_lesson_cause,
                    outcome=_honest_outcome,
                    confidence=_lesson_confidence,
                )
        else:
            log.info(f"[AgentLoop] Skipping RAG learning persistence — result appears meaningless: {ans[:100]}")

        # --- APPLY ENFORCEMENT (honest reuse signal) ---
        # The mission prompt instructs the model to cite lesson ids it applies
        # via `Apply: [id]`. If it did, bump applied_count on those
        # lessons — the real "this lesson was useful" metric, distinct
        # from mere retrieval (usage_count).
        try:
            import re as _re
            _cited = _re.findall(
                r"apply\s*[:=]?\s*\[?(lesson-[a-z0-9_\-]+)\]?",
                (ans or "")
                + "\n" + (generated_plan or "")
                + "\n" + "\n".join(action_log[-15:]),
                _re.IGNORECASE,
            )
            _ids: set[str] = set()
            for _chunk in _cited:
                for _id in _re.findall(r"[A-Za-z0-9_\-]{6,}", _chunk):
                    _ids.add(_id)
            if _ids:
                async with shared_http_client() as client:
                    for _id in _ids:
                        try:
                            await client.patch(
                                f"{RAG_SVC}/rag/learning/{_id}/applied",
                                headers={"X-Internal-Secret": INTERNAL_SECRET},
                                timeout=aiohttp.ClientTimeout(total=10.0),
                            )
                        except Exception as _me:
                            log.debug(f"[AgentLoop] applied-bump failed for {_id}: {_me}")
                log.info(f"[AgentLoop] Marked lessons applied: {sorted(_ids)}")
        except Exception as e:
            log.debug(f"[AgentLoop] Apply-citation parse skipped: {e}")

    if mission_id and full_audit_log:
        try:
            summarized_log = normalize_audit_log(full_audit_log)
            last_llm_reply = None
            for ev in reversed(full_audit_log):
                if ev.get("type") == "reasoning":
                    txt = (ev.get("data") or "").strip()
                    if txt:
                        last_llm_reply = txt[:4000]
                        break
            patch_body = {"output_log": json.dumps(summarized_log)}
            if last_llm_reply is not None:
                patch_body["last_llm_reply"] = last_llm_reply
            async with shared_http_client() as client:
                await client.patch(
                    f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                    json=patch_body,
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
    # Keep this in sync with the extensions the lint handler actually supports
    # (services/execution/handlers/workspace.py). If a language's compiler is
    # missing from the sandbox, the handler reports `verified=False` rather than
    # lying about a clean pass — see below.
    lintable_exts = {
        "py", "js", "jsx", "mjs", "ts", "tsx",
        "sh", "bash", "go", "rs", "c", "h", "cpp", "cc", "cxx", "hpp",
        "java", "rb", "lua", "php", "json", "yaml", "yml",
    }
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
                detail = lint_data.get("detail", {}) or {}
                lint_passed = detail.get("passed", True) if isinstance(detail, dict) else True
                lint_verified = detail.get("verified", True) if isinstance(detail, dict) else True
                results = detail.get("results", []) if isinstance(detail, dict) else []

                if lint_passed is False:
                    lint_msg = lint_data.get("message", "")
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

                # UNVERIFIED: the handler ran but NO checker was installed, so we
                # have no real signal. Surface this so the model cannot trust a
                # phantom "clean" and so a missing toolchain gets flagged in CI.
                if not lint_verified:
                    skipped = [r.get("tool") for r in results if r.get("skipped")]
                    warn = (
                        f"LINT UNVERIFIED for {file_path}: no code checker ("
                        + ", ".join(skipped or ["?"])
                        + ") is installed in the sandbox. This code was NOT actually "
                        + "verified — install the language toolchain or rely on CI to catch errors."
                    )
                    logger.warning(warn)
                    return warn

                logger.info(f"Post-write lint clean for {file_path}")
    except Exception as lint_e:
        logger.warning(f"Post-write lint check failed: {lint_e}")
    return None
