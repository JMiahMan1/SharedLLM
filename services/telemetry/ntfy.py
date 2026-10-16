"""Publishing Jarvis notices to the ntfy push server (docs/PUSH.md).

Telemetry is the only publisher. Each notice goes to the person's topic as
one ntfy message whose ``message`` field is the notice itself as JSON, so the
phone has everything in one field; title, priority and tags are set too for
anything reading ntfy directly.

Best effort: a failure is logged once (not again until a publish succeeds)
and never raised, because the in-app outbox already holds the notice.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from services.config import NTFY_TOKEN, NTFY_URL

log = logging.getLogger("telemetry.ntfy")

#: ntfy turns a longer message into an attachment, and attachments are off.
MESSAGE_LIMIT = 4096

_failing = False


def _failed(reason: str) -> bool:
    global _failing
    if not _failing:
        log.warning("ntfy publish failed (%s); notices stay in the outbox until it recovers", reason)
    _failing = True
    return False


def payload(topic: str, notice: dict[str, Any]) -> dict[str, Any]:
    """The ntfy JSON publish body for one notice."""
    return {
        "topic": topic,
        "message": json.dumps(notice, separators=(",", ":")),
        "title": str(notice.get("title") or "")[:120],
        "priority": int(notice.get("priority") or 3),
        "tags": [str(notice.get("category") or "general")],
    }


async def publish(session: aiohttp.ClientSession, topic: str, notice: dict[str, Any]) -> bool:
    """Publish one notice to ``topic``. Returns whether ntfy accepted it."""
    global _failing
    if not NTFY_TOKEN:
        return _failed("NTFY_TOKEN is not set")
    body = payload(topic, notice)
    if len(body["message"].encode()) > MESSAGE_LIMIT:
        log.warning("Notice %s is too large for ntfy (%d bytes); not published",
                    notice.get("id"), len(body["message"].encode()))
        return False
    try:
        resp = await session.post(
            f"{NTFY_URL}/",
            json=body,
            headers={"Authorization": f"Bearer {NTFY_TOKEN}"},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        async with resp:
            if resp.status != 200:
                return _failed(f"HTTP {resp.status}: {(await resp.text())[:200]}")
    except Exception as e:
        return _failed(f"{type(e).__name__}: {e}")
    if _failing:
        log.info("ntfy publish works again")
    _failing = False
    return True
