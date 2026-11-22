"""Jarvis notices: the one way a service asks telemetry to notify someone.

Telemetry's ``POST /api/telemetry/notify`` is the only publisher (see
docs/PUSH.md for the fields). Every sender builds a notice with ``notice()``
and hands it to ``send()``; nothing else in a service talks to that endpoint.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from services.common.notice_links import LinkError, check, link, link_from_href

__all__ = ["LinkError", "callback_action", "family", "family_or_none", "link", "link_from_href", "notice", "send", "send_many",
           "view_action"]

log = logging.getLogger("notify")

_TIMEOUT = aiohttp.ClientTimeout(total=10)


def view_action(target: str | dict[str, Any], label: str = "Open") -> dict[str, Any]:
    """A button that opens a page: a ``link()`` (or an in-app address, checked
    the same way), or an ``https://`` page the phone opens in the browser."""
    if isinstance(target, dict):
        return {"label": label, "type": "view", "url": check(target)["href"]}
    if str(target).startswith("https://"):
        return {"label": label, "type": "view", "url": target}
    return {"label": label, "type": "view", "url": link_from_href(target)["href"]}


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
    link: dict[str, Any] | None = None,
    url: str | None = None,
    priority: int | None = None,
    tag: str | None = None,
    actions: list[dict] | None = None,
    once: str | None = None,
    data: dict | None = None,
    kind: str | None = None,
    audience: str = "all",
    replace: bool = False,
    signal: bool = False,
) -> dict[str, Any]:
    """The JSON body telemetry's notify takes.

    ``link`` is where a tap goes, built with ``link()`` (services/common/
    notice_links.py): the page and the one item on it. Every notice has one; a
    notice without a target, or with one no page reads, raises ``LinkError``
    here, where the sender's test sees it. ``url`` (an in-app address) is the
    older form, read into a link the same way.

    ``audience`` is ``"all"`` (everyone, the default) or ``"parents"``: telemetry
    refuses to deliver a parents-only notice (updates, system and ops notices)
    to a minor. Any other value is rejected by telemetry.

    ``replace`` (with a ``tag``) also drops earlier unread notices with that tag
    from the person's notification list, as the phone already replaces them.

    ``signal`` makes it a data-only message for the person's devices (the
    phone acts on its ``kind`` and shows nothing): telemetry publishes it to
    the push stream only, never to the notification list or web push. An app
    too old to know the ``kind`` shows it as an ordinary notice, so the title
    and body must still read well on their own.
    """
    if link is None and url is None:
        raise LinkError(f"a {kind or category} notice needs a link (where a tap goes)")
    target = check(link) if link is not None else link_from_href(str(url))
    payload: dict[str, Any] = {
        "user": user,
        "category": category,
        "kind": kind or category,
        "title": title,
        "body": body,
        "data": {**(data or {}), "link": target, "url": target["href"]},
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
    if replace and tag:
        payload["replace"] = True
    if signal:
        payload["signal"] = True
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


async def family_or_none(*, client=None, identity: str | None = None, secret: str | None = None) -> list[str] | None:
    """The family list, or None when identity cannot tell.

    Callers that must never act on an empty guess (inviting nobody) use this;
    most callers use ``family``, which degrades to an empty list instead.
    """
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
        return None
    if not isinstance(users, list):
        return None
    return sorted(str(u["username"]) for u in users
                  if isinstance(u, dict) and u.get("username") and not u.get("is_system_default"))


async def family(*, client=None, identity: str | None = None, secret: str | None = None) -> list[str]:
    """Every family member's username but the system account: who "everyone" is."""
    users = await family_or_none(client=client, identity=identity, secret=secret)
    return users or []
