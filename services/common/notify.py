"""Jarvis notices: the one way a service asks telemetry to notify someone.

Telemetry's ``POST /api/telemetry/notify`` is the only publisher (see
docs/PUSH.md for the fields). Every sender builds a notice with ``notice()``
and hands it to ``send()``; nothing else in a service talks to that endpoint.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger("notify")

_TIMEOUT = aiohttp.ClientTimeout(total=10)


def view_action(url: str, label: str = "Open") -> dict[str, Any]:
    """A button that opens ``url`` in the app."""
    return {"label": label, "type": "view", "url": url}


def callback_action(label: str, callback: str, params: dict | None = None, *, reply: bool = False) -> dict[str, Any]:
    """A button the phone answers with ``POST /api/push/action`` on the gateway.

    ``reply`` first asks for text inline in the notification.
    """
    return {"label": label, "type": "reply" if reply else "callback", "callback": callback, "params": params or {}}


def notice(
    user: str,
    category: str,
    title: str,
    body: str = "",
    *,
    url: str | None = None,
    priority: int | None = None,
    tag: str | None = None,
    actions: list[dict] | None = None,
    once: str | None = None,
    data: dict | None = None,
    kind: str | None = None,
    audience: str = "all",
) -> dict[str, Any]:
    """The JSON body telemetry's notify takes. ``url`` is where a tap goes.

    ``audience`` is ``"all"`` (everyone, the default) or ``"parents"``: telemetry
    refuses to deliver a parents-only notice (updates, system and ops notices)
    to a minor. Any other value is rejected by telemetry.
    """
    payload: dict[str, Any] = {
        "user": user,
        "category": category,
        "kind": kind or category,
        "title": title,
        "body": body,
        "data": {**(data or {}), **({"url": url} if url else {})},
    }
    if priority is not None:
        payload["priority"] = max(1, min(5, int(priority)))
    if tag:
        payload["tag"] = tag
    if actions:
        payload["actions"] = actions[:3]
    if once:
        payload["once"] = once
    if audience and audience != "all":
        payload["audience"] = audience
    return payload


async def send(payload: dict[str, Any], *, client=None, telemetry: str | None = None,
               secret: str | None = None) -> bool:
    """Deliver one notice; True when telemetry took it.

    ``client``, ``telemetry`` and ``secret`` default to the shared pooled
    client and the configured service address and secret; a caller with its
    own (the gateway's chat poller) passes them. Never raises: a failed
    notice is logged and the sender carries on.
    """
    if telemetry is None or secret is None:
        from services import config

        telemetry = telemetry or config.TELEMETRY_SVC_URL
        secret = secret or config.INTERNAL_SECRET
    if client is None:
        from services.common.http import get_client

        client = get_client()
    try:
        async with client.post(f"{telemetry.rstrip('/')}/api/telemetry/notify", json=payload,
                               headers={"X-Internal-Secret": secret}, timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                return True
            log.warning("Notice %s to %s failed: HTTP %s", payload.get("category"), payload.get("user"), resp.status)
    except Exception as e:
        log.warning("Notice %s to %s failed: %s", payload.get("category"), payload.get("user"), e)
    return False


async def send_many(users, make, **kw) -> int:
    """``send(make(user))`` for each user; how many were taken."""
    sent = 0
    for user in users:
        sent += int(await send(make(user), **kw))
    return sent


async def family(*, client=None, identity: str | None = None, secret: str | None = None) -> list[str]:
    """Every family member's username but the system account: who "everyone" is."""
    if identity is None or secret is None:
        from services import config

        identity = identity or config.IDENTITY_SVC_URL
        secret = secret or config.INTERNAL_SECRET
    if client is None:
        from services.common.http import get_client

        client = get_client()
    try:
        async with client.get(f"{identity.rstrip('/')}/api/users", headers={"X-Internal-Secret": secret},
                              timeout=_TIMEOUT) as resp:
            users = await resp.json(content_type=None) if resp.status == 200 else None
    except Exception as e:
        log.warning("Listing the family failed: %s", e)
        return []
    if not isinstance(users, list):
        return []
    return sorted(str(u["username"]) for u in users
                  if isinstance(u, dict) and u.get("username") and not u.get("is_system_default"))
