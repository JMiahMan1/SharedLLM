"""Secure OpenAI client-tool passthrough for the local model server.

OpenCode and similar clients bring their *own* tools and drive the model
themselves. Sending those requests through Jarvis's agentic loop would execute
Jarvis's 66 tools and break the client. This module forwards a genuine OpenAI
``/v1/chat/completions`` request straight to the local backend, preserving SSE
``tool_calls`` deltas, ``reasoning_content`` and ``tool_choice``.

The gate is deliberately narrow (all must hold):

* the request is the OpenAI route,
* the caller presented a real API key that Identity already verified
  (never a keyless household/kiosk fallback),
* the caller supplied OpenAI tools, or explicitly opted in with
  ``x-jarvis-passthrough: 1`` / ``x-request-source: opencode``,
* the body does not ask for Jarvis's agentic or SharedLLM tool modes,
* the model is a concrete local model, never a virtual household alias.

Forwarding ``X-Request-Source`` keeps OpenCode at LOW priority in
alpaca-proxy, behind voice and chat.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

log = logging.getLogger("gateway.openai_passthrough")

#: Virtual household aliases are resolved by Jarvis before a model is chosen.
#: They must never be proxied: the backend does not know them.
VIRTUAL_MODEL_ALIASES = frozenset(
    {
        "jarvis",
        "assistant",
        "default",
        "auto",
        "coding",
        "coder",
        "librarian",
        "rag",
        "telemetry",
        "health",
        "fitness",
        "report",
    }
)

#: A local model id (optionally with an Ollama-style tag), never a path or URL.
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")

#: Jarvis-only body fields that must never be forwarded to the model server.
_INTERNAL_BODY_KEYS = frozenset(
    {
        "api_key",
        "user_id",
        "rag_user",
        "voice_id",
        "device_id",
        "anonymous",
        "household",
        "grounded",
        "agentic",
        "sharedllm_tools",
        "query",
        "speech_stream",
        "show_thinking",
        "system",
        "voice_context",
        "workspace_id",
        "intent",
        "intent_confidence",
        "turn_note",
    }
)


def safe_model_name(model: str | None) -> bool:
    """A concrete local model id, not a virtual alias."""
    value = str(model or "").strip()
    if not value or not _SAFE_MODEL_RE.match(value):
        return False
    if "://" in value or value.startswith(("/", ".")) or ".." in value or value.count("/") > 1:
        return False
    bare = value[:-7] if value.endswith(":latest") else value
    return bare.lower() not in VIRTUAL_MODEL_ALIASES


def has_explicit_opt_in(request: Request) -> bool:
    """The client asked for passthrough with tools it owns."""
    headers = getattr(request, "headers", None) or {}
    return (
        str(headers.get("x-jarvis-passthrough") or "").strip() == "1"
        or str(headers.get("x-request-source") or "").strip().lower() == "opencode"
    )


def should_passthrough(
    *,
    request: Request,
    body: dict[str, Any],
    presented_key: str,
    selected_model: str,
    explicit_model: str = "",
    is_openai: bool,
    voice_context: dict[str, Any] | None = None,
) -> bool:
    """The complete, fail-closed gate for the client-tool passthrough."""
    if not is_openai or not str(presented_key or "").strip():
        return False
    if voice_context:
        return False
    if body.get("agentic") or body.get("sharedllm_tools"):
        return False
    if not safe_model_name(selected_model):
        return False
    if explicit_model and not safe_model_name(explicit_model):
        return False
    tools = body.get("tools")
    return bool(isinstance(tools, list) and tools) or has_explicit_opt_in(request)


def completions_url(base_url: str) -> str:
    """The backend's OpenAI chat-completions URL from a configured base."""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1/chat/completions"):
        return base
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def outbound_payload(body: dict[str, Any], *, selected_model: str) -> dict[str, Any]:
    """A clean OpenAI payload: no Jarvis identity, no internal-only keys."""
    payload = {key: value for key, value in (body or {}).items() if key not in _INTERNAL_BODY_KEYS}
    if not payload.get("model"):
        payload["model"] = selected_model
    return payload


def request_source(request: Request) -> str:
    """Forward the caller's source; default OpenCode to the LOW-priority lane."""
    headers = getattr(request, "headers", None) or {}
    return str(headers.get("x-request-source") or "").strip() or "opencode"


async def forward_chat_completion(
    request: Request,
    body: dict[str, Any],
    *,
    base_url: str,
    selected_model: str,
    source: str | None = None,
) -> Any:
    """Proxy one OpenAI chat request, streaming SSE or returning JSON."""
    url = completions_url(base_url)
    if not url:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "The local model server is not configured.", "type": "server_error"}},
        )
    payload = outbound_payload(body, selected_model=selected_model)
    headers = {
        "Content-Type": "application/json",
        "X-Request-Source": source or request_source(request),
        "X-Jarvis-Passthrough": "1",
    }
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10.0, sock_read=600.0)
    session = aiohttp.ClientSession(timeout=timeout)
    try:
        upstream = await session.post(url, json=payload, headers=headers)
    except Exception as exc:
        await session.close()
        log.warning("[openai-passthrough] local backend did not answer: %s", exc)
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"The local model server did not answer: {type(exc).__name__}",
                    "type": "upstream_error",
                }
            },
        )
    content_type = str(upstream.headers.get("Content-Type") or "")
    streaming = bool(payload.get("stream")) and "text/event-stream" in content_type.lower()
    if upstream.status >= 400:
        raw = await upstream.text()
        upstream.release()
        await session.close()
        try:
            detail = json.loads(raw) if raw.strip() else {}
        except ValueError:
            detail = {"error": {"message": raw[:500], "type": "upstream_error"}}
        return JSONResponse(status_code=upstream.status, content=detail)
    if not streaming:
        try:
            data = await upstream.json(content_type=None)
        except Exception:
            raw = await upstream.text()
            upstream.release()
            await session.close()
            return JSONResponse(status_code=502, content={"error": {"message": raw[:500], "type": "upstream_error"}})
        upstream.release()
        await session.close()
        return JSONResponse(content=data, media_type=content_type or "application/json")

    async def stream_body():
        try:
            async for chunk in upstream.content.iter_any():
                if chunk:
                    yield chunk
        except Exception as exc:  # client disconnect or upstream reset
            log.debug("[openai-passthrough] stream ended: %s", exc)
        finally:
            upstream.release()
            await session.close()

    return StreamingResponse(
        stream_body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
