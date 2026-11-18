"""Home Assistant sentence triggers, asked before Jarvis answers a chat or voice turn.

An automation with ``trigger: conversation`` in the device hub owns its sentences ("movie time",
"I'm leaving for {place}"). ``/api/chat`` asks the hub first (``POST /hub/conversation/triggers``
at ``HOMEHUB_URL`` with the internal secret, services/homehub/conversation.py): when a sentence
matched, the automations ran and their ``set_conversation_response`` is the reply, and no model
is asked. Voice satellites reach Jarvis through ``/api/chat`` too, so their sentences are matched
here with the satellite named (``voice_context.satellite``), which becomes the trigger's device.

When the hub cannot be asked, the failure is logged as a warning on every turn and Jarvis answers
the turn itself: a hub outage must not silence chat, and the log says why a sentence did nothing.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.sentence_triggers")

#: The hub waits up to 10 s for the automations a sentence ran (CONVERSATION_WAIT_S); a little more here.
_TIMEOUT = aiohttp.ClientTimeout(total=13, connect=2)


async def ask_hub(text: str, voice_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The hub's reply for a sentence an automation owns (``{"response", "automations"}``), else None."""
    from services.common.hub_login import homehub_url
    from services.gateway.main import shared_http_client

    base = homehub_url()
    if not base:
        # The HomeHub integration is off: there are no sentence triggers to ask about.
        return None
    ctx = voice_context or {}
    body = {"text": text, "satellite_id": ctx.get("satellite"), "conversation_id": ctx.get("conversation_id")}
    try:
        async with shared_http_client() as client, client.post(
            f"{base}/hub/conversation/triggers", json={k: v for k, v in body.items() if v},
            headers={"Authorization": f"Bearer {INTERNAL_SECRET}"}, timeout=_TIMEOUT,
        ) as resp:
            data = await resp.json(content_type=None)
            status = resp.status
    except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
        log.warning("[sentence_triggers] the hub could not be asked (%s); Jarvis answers this turn",
                    type(exc).__name__)
        return None
    if status != 200 or not isinstance(data, dict):
        detail = data.get("detail") if isinstance(data, dict) else None
        log.warning("[sentence_triggers] the hub answered HTTP %s (%s); Jarvis answers this turn", status, detail)
        return None
    if not data.get("matched"):
        return None
    response = data.get("response")
    if not isinstance(response, str) or not response.strip():
        response = "Done"  # the automations ran; asking Jarvis now could run the same request twice
    log.info("[sentence_triggers] sentence handled by %s", ", ".join(map(str, data.get("automations") or [])))
    return {"response": response.strip(), "automations": list(data.get("automations") or [])}
