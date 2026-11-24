"""One OpenAI-style chat call for telemetry: the health and power reports
(through the gateway) and content monitoring's second look (the household's
local model at ``llm_local_url``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp

from services.common.backend_health import BackendHealth, Settings

#: The household's GPU box as telemetry reaches it directly (the local model and
#: the vision model at ``llm_local_url`` / ``vision_ocr_proxy_url``), for the
#: Guardian checks that must never leave the house. While it is off a check fails
#: at once (``BackendUnavailable``) and is retried later by its caller, instead of
#: each photo waiting out a connect timeout and logging a warning.
LOCAL_MODEL = BackendHealth(
    "local_model", "The local model", probe_path="/", message="The local model is offline right now.",
    settings=Settings.from_env(),
)


async def chat_content(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    timeout_s: float,
    headers: Mapping[str, str] | None = None,
    what: str = "the model",
) -> str:
    """POST ``payload`` to a ``/v1/chat/completions`` URL and return the first
    choice's text. Raises RuntimeError (naming ``what``) on an HTTP error, no
    choices or empty content, so callers decide how to fall back."""
    # A connect limit of its own: a machine that is off is found in seconds, not
    # after the whole ``timeout_s`` a slow model is allowed.
    timeout = aiohttp.ClientTimeout(total=timeout_s, sock_connect=LOCAL_MODEL.settings.connect_timeout)
    data, status, body = await _post(session, url, payload, headers, timeout)
    if status != 200:
        raise RuntimeError(f"{what} failed HTTP {status}: {body[:200]}")
    return _first_choice(data, what)


async def local_chat_content(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    timeout_s: float,
    what: str = "the model",
    *,
    background: bool = False,
) -> str:
    """``chat_content`` for the household's GPU box, through ``LOCAL_MODEL``'s breaker.

    Every call names itself to alpaca-proxy (``X-Request-Source``); without it the proxy
    saw ``unknown-origin`` at normal priority, so a phone's photo backlog swapped the
    household's chat model out from under voice questions. ``background`` marks work
    that can wait (``X-Priority: low``): voice pre-empts it and it queues behind people.

    Raises ``BackendUnavailable`` at once while the machine is known to be off,
    and when this call could not connect (which marks it off, logged once). Any
    other failure is raised as ``chat_content`` raises it.
    """
    LOCAL_MODEL.set_url(url.split("/v1/", 1)[0])
    LOCAL_MODEL.check()
    headers = {"X-Request-Source": "shared-llm/guardian"}
    if background:
        headers["X-Priority"] = "low"
    try:
        if what == "the model":
            content = await chat_content(session, url, payload, timeout_s, headers=headers)
        else:
            content = await chat_content(session, url, payload, timeout_s, what=what, headers=headers)
    except Exception as exc:
        if LOCAL_MODEL.record_failure(exc):
            raise LOCAL_MODEL.unavailable() from exc
        raise
    LOCAL_MODEL.record_success()
    return content


async def _post(session, url, payload, headers, timeout):
    async with session.post(url, json=payload, headers=dict(headers or {}), timeout=timeout) as resp:
        if resp.status != 200:
            return None, resp.status, await resp.text()
        return await resp.json(content_type=None), 200, ""


def _first_choice(data: Any, what: str) -> str:
    choices = (data or {}).get("choices") or []
    if not choices:
        raise RuntimeError(f"{what} returned no choices")
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError(f"{what} returned empty content")
    return content
