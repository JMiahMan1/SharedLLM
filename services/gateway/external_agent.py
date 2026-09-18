"""
External agentic chat loop for SharedLLM.

Lets any standard OpenAI- or Ollama-compatible chat client (OpenAI SDK,
Ollama tool-calling, OpenWebUI) drive the FULL Raven tool surface with
thinking visible — so chat-window conversations double as Raven
teaching/learning training data.

Flow per turn (bounded multi-turn):
    1. POST messages + tools to Ollama /api/chat (non-streaming) with
       ``think`` enabled when requested.
    2. Capture ``thinking`` alongside ``content``.
    3. While the model returns ``tool_calls`` (and budget remains): resolve
       each call via tool_registry.resolve_tool_call, execute it against the
       backend service, append the assistant tool_calls + ``role="tool"``
       results, repeat.
    4. Return final content + thinking + a full tool_trace.

Every turn is logged with an [ExternalAgent] prefix in a stable structured
shape so transcripts can be harvested for training.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import aiohttp

from services.gateway.config import (
    ALPACA_SD_URL,
    CONTROL_PLANE_URL,
    EXECUTION_SVC,
    INTERNAL_SECRET,
    OLLAMA_URL,
    RAG_SVC,
    STORAGE_SVC,
    WORKSPACE_RUNTIME_SVC,
)
from services.gateway.tool_registry import (
    SVC_ALPACA_SD,
    SVC_CONTROL_PLANE,
    SVC_EXECUTION,
    SVC_GATEWAY,
    SVC_RAG,
    SVC_STORAGE,
    SVC_WORKSPACE,
    resolve_tool_call,
)

log = logging.getLogger("gateway")

GATEWAY_SELF_URL = "http://gateway:11435"
_TOOL_TIMEOUT = aiohttp.ClientTimeout(total=180.0)


def _service_base(service: str) -> str:
    if service == SVC_EXECUTION:
        return EXECUTION_SVC
    if service == SVC_WORKSPACE:
        return WORKSPACE_RUNTIME_SVC
    if service == SVC_ALPACA_SD:
        return ALPACA_SD_URL
    if service == SVC_RAG:
        return RAG_SVC
    if service == SVC_STORAGE:
        return STORAGE_SVC
    if service == SVC_CONTROL_PLANE:
        return CONTROL_PLANE_URL
    if service == SVC_GATEWAY:
        return GATEWAY_SELF_URL
    raise ValueError(f"Unknown tool service: {service}")


async def _execute_resolved(
    session: aiohttp.ClientSession,
    *,
    method: str,
    service: str,
    path: str,
    body: dict,
    api_key: str | None,
) -> dict:
    """POST/GET a resolved tool call against its backend service."""
    url = f"{_service_base(service)}{path}"
    headers: dict[str, str] = {}
    if service in (SVC_EXECUTION, SVC_WORKSPACE, SVC_RAG, SVC_STORAGE, SVC_CONTROL_PLANE):
        headers["X-Internal-Secret"] = INTERNAL_SECRET
    elif service == SVC_GATEWAY and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        if method == "GET":
            async with session.get(url, headers=headers, timeout=_TOOL_TIMEOUT) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"raw": await resp.text()}
                return {"status": resp.status, "body": data}
        async with session.post(url, json=body, headers=headers, timeout=_TOOL_TIMEOUT) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = {"raw": await resp.text()}
            return {"status": resp.status, "body": data}
    except Exception as e:
        return {"status": 0, "body": {"error": f"{type(e).__name__}: {e}"}}


def _parse_tool_calls(message: dict) -> list[dict]:
    """Extract Ollama/OpenAI-style tool_calls from an assistant message."""
    calls = message.get("tool_calls") or []
    parsed: list[dict] = []
    for tc in calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or ""
        args = fn.get("arguments") or tc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except Exception:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {"_raw": args}
        parsed.append({"name": name, "arguments": args, "id": tc.get("id"), "source": "native"})
    return parsed


_TEXT_CALL_RE = re.compile(r"call:(?:default_api:)?([A-Za-z][A-Za-z0-9_]*)\s*\{", re.DOTALL)


def _parse_text_tool_calls(content: str) -> list[dict]:
    """Fallback: some GGUF tool templates emit a text protocol like
    ``call:default_api:ToolName{...json args...}`` instead of native
    tool_calls. Parse those lines into executable calls (Raven's agent_loop
    does the same kind of content-mining for non-tool-emitting models)."""
    parsed: list[dict] = []
    if not content:
        return parsed
    for m in _TEXT_CALL_RE.finditer(content):
        name = m.group(1)
        start = m.end() - 1  # opening brace
        depth = 0
        end = None
        for i in range(start, min(len(content), start + 4000)):
            ch = content[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        raw = content[start:end] if end else "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                args = {}
        except Exception:
            args = {}
        parsed.append({"name": name, "arguments": args, "id": None, "source": "text_protocol"})
    return parsed


def _strip_text_calls(content: str) -> str:
    """Remove executed text-protocol call spans so the final chat answer is clean."""
    if not content:
        return content
    spans: list[tuple[int, int]] = []
    for m in _TEXT_CALL_RE.finditer(content):
        start = m.start()
        depth = 0
        end = None
        for i in range(m.end() - 1, min(len(content), m.end() + 3999)):
            ch = content[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        spans.append((start, end or m.end()))
    for start, end in reversed(spans):
        content = content[:start] + content[end:]
    return re.sub(r"\n{3,}", "\n\n", content).strip()


async def run_external_agent(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    creds: dict,
    think: bool = False,
    max_iterations: int = 6,
    request_timeout: float = 600.0,
    ollama_url: str | None = None,
) -> dict:
    """Run a bounded agentic tool loop against Ollama and return the outcome.

    Returns {"content", "thinking", "tool_trace", "iterations", "model"}.
    ``tool_trace`` entries: {iteration, name, arguments, status, summary}.
    """
    base = (ollama_url or OLLAMA_URL or "").rstrip("/")
    if not base:
        return {
            "content": "[ExternalAgent] Ollama URL not configured (llm_local_url).",
            "thinking": "",
            "tool_trace": [],
            "iterations": 0,
            "model": model,
        }
    t0 = time.time()
    user = creds.get("user", "default")
    api_key = creds.get("api_key")
    user_context = {
        "user": user,
        "is_admin": bool(creds.get("is_admin", False)),
        **({"api_key": api_key} if api_key else {}),
    }
    convo: list[dict] = [dict(m) for m in messages if isinstance(m, dict)]
    if tools and not any(m.get("role") == "system" and (m.get("content") or "").strip() for m in convo):
        convo.insert(0, {
            "role": "system",
            "content": (
                "You are Raven, the SharedLLM autonomous assistant. You have function "
                "tools — use them to complete the user's task. When a task needs a "
                "tool, emit native tool_calls (never describe the call in prose). "
                "If you cannot emit native tool_calls, write one call per line as "
                "call:ToolName{\"arg\": value} and it will be executed for you. "
                "After a tool result arrives, use it: either call the next tool or "
                "give the final answer. Keep thinking concise."
            ),
        })
    tool_trace: list[dict] = []
    thinking_parts: list[str] = []
    content = ""
    coercions = 0

    timeout = aiohttp.ClientTimeout(total=request_timeout)
    async with aiohttp.ClientSession() as session:
        for iteration in range(1, max_iterations + 1):
            payload: dict[str, Any] = {
                "model": model,
                "messages": convo,
                "stream": False,
                "think": bool(think),
            }
            if tools:
                payload["tools"] = tools
            log.info(
                "[ExternalAgent] iter %d/%d model=%s user=%s think=%s tools=%d msgs=%d",
                iteration, max_iterations, model, user, think, len(tools or []), len(convo),
            )
            try:
                async with session.post(f"{base}/api/chat", json=payload, timeout=timeout) as resp:
                    if resp.status != 200:
                        err = (await resp.text())[:500]
                        log.warning("[ExternalAgent] Ollama HTTP %s: %s", resp.status, err)
                        content = f"[ExternalAgent] LLM backend error {resp.status}: {err}"
                        break
                    data = await resp.json()
            except Exception as e:
                log.warning("[ExternalAgent] Ollama call failed: %r", e)
                content = f"[ExternalAgent] LLM backend unreachable: {type(e).__name__}: {e}"
                break

            message = data.get("message") or {}
            content = message.get("content") or ""
            thinking = message.get("thinking") or ""
            if thinking:
                thinking_parts.append(thinking)
            calls = _parse_tool_calls(message)
            text_source = False
            if not calls:
                calls = _parse_text_tool_calls(content)
                text_source = bool(calls)
            log.info(
                "[ExternalAgent] iter %d: content_chars=%d thinking_chars=%d tool_calls=%s%s",
                iteration, len(content), len(thinking), [c["name"] for c in calls],
                " (text_protocol)" if text_source else "",
            )
            if not calls:
                # No callable emitted (prose-only or thinking-only) while tools
                # are available: coerce at most twice with a strict format
                # instruction, then give up and return what we have.
                if tools and coercions < 2 and iteration < max_iterations:
                    coercions += 1
                    log.info("[ExternalAgent] iter %d: no tool call emitted, coercing (%d/2)", iteration, coercions)
                    prior = (content or thinking)[:1500]
                    if prior:
                        convo.append({"role": "assistant", "content": prior})
                    convo.append({"role": "user", "content": (
                        "Your reply contained no executable tool call. Reply now with "
                        "ONLY one tool-call line and no other prose, e.g. "
                        "call:ToolName{\"arg\": value} using one of the provided tools."
                    )})
                    continue
                break

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            native_calls = [c for c in calls if c.get("source") != "text_protocol"]
            if native_calls:
                assistant_msg["tool_calls"] = [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in native_calls
                ]
            convo.append(assistant_msg)
            for c in calls:
                name, args = c["name"], c["arguments"]
                try:
                    resolved = resolve_tool_call(
                        name, args,
                        workspace_id=args.get("workspace_id"),
                        user_context=user_context,
                    )
                    result = await _execute_resolved(
                        session, method=resolved.method, service=resolved.service,
                        path=resolved.path, body=resolved.json, api_key=api_key,
                    )
                    status = result.get("status", 0)
                    ok = 200 <= status < 300
                except ValueError as e:
                    result = {"status": 0, "body": {"error": str(e)}}
                    ok = False
                summary = json.dumps(result.get("body"), default=str)[:2000]
                tool_trace.append({
                    "iteration": iteration,
                    "name": name,
                    "arguments": args,
                    "source": c.get("source") or "native",
                    "status": result.get("status"),
                    "ok": ok,
                    "summary": summary,
                })
                log.info(
                    "[ExternalAgent] tool %s status=%s ok=%s summary=%.300s",
                    name, result.get("status"), ok, summary,
                )
                convo.append({
                    "role": "tool",
                    "content": summary,
                    **({"tool_call_id": c["id"]} if c.get("id") else {}),
                })

    elapsed = time.time() - t0
    log.info(
        "[ExternalAgent] done model=%s user=%s iters_used=%d tools_executed=%d elapsed=%.1fs",
        model, user, min(iteration, max_iterations), len(tool_trace), elapsed,
    )
    if any(t.get("source") == "text_protocol" for t in tool_trace):
        content = _strip_text_calls(content)
    return {
        "content": content,
        "thinking": "\n".join(thinking_parts),
        "tool_trace": tool_trace,
        "iterations": iteration,
        "model": model,
    }
