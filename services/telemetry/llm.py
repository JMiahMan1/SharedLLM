"""One OpenAI-style chat call for telemetry: the health and power reports
(through the gateway) and content monitoring's second look (the household's
local model at ``llm_local_url``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp


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
    async with session.post(
        url, json=payload, headers=dict(headers or {}), timeout=aiohttp.ClientTimeout(total=timeout_s)
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"{what} failed HTTP {resp.status}: {body[:200]}")
        data = await resp.json(content_type=None)
    choices = (data or {}).get("choices") or []
    if not choices:
        raise RuntimeError(f"{what} returned no choices")
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError(f"{what} returned empty content")
    return content
