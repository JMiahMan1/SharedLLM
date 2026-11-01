"""What to call a member's phone or tablet in a sentence.

Some of the family carry tablets, not phones, so a notice that says "Noah's
phone" is wrong for them. Identity knows each member's devices; it answers
with their kinds (``device_kinds`` on the routes that need it, or the
``details`` of ``/api/internal/owned-devices/{username}``), and ``noun()``
turns those kinds into the word: "phone", "tablet", "phone or tablet" for a
member with both, and "device" when nothing is known.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import aiohttp

log = logging.getLogger("device_noun")

MOBILE_KINDS = ("phone", "tablet")
_TIMEOUT = aiohttp.ClientTimeout(total=3)


def mobile_kinds(kinds: Iterable[Any] | None) -> list[str]:
    """The phone and tablet kinds among ``kinds``, sorted, each once."""
    return sorted({str(k or "").strip().lower() for k in kinds or ()} & set(MOBILE_KINDS))


def kinds_of(details: Iterable[dict[str, Any]] | None) -> list[str]:
    """The kinds of the devices in owned-devices ``details`` that are not revoked."""
    return mobile_kinds(d.get("kind") for d in details or () if isinstance(d, dict) and not d.get("revoked"))


def noun(kinds: Iterable[Any] | None) -> str:
    """"phone", "tablet", "phone or tablet", or "device" when none is known."""
    found = mobile_kinds(kinds)
    if len(found) == 1:
        return found[0]
    return "phone or tablet" if found else "device"


async def kinds(user: str, *, client=None, identity: str | None = None, secret: str | None = None) -> list[str]:
    """A member's phone and tablet kinds, from identity; [] when it cannot say.

    Never raises: a notice is still worth sending with "device" in it.
    """
    if identity is None or secret is None:
        from services import config

        identity = identity or config.IDENTITY_SVC_URL
        secret = secret or config.INTERNAL_SECRET
    if client is None:
        from services.common.http import get_client

        client = get_client()
    try:
        async with client.get(f"{identity.rstrip('/')}/api/internal/owned-devices/{user}",
                              headers={"X-Internal-Secret": secret}, timeout=_TIMEOUT) as resp:
            data = await resp.json(content_type=None) if resp.status == 200 else None
    except Exception as e:
        log.warning("Reading %s's devices failed: %s", user, e)
        return []
    return kinds_of((data or {}).get("details") if isinstance(data, dict) else None)


async def noun_for(user: str, **kw) -> str:
    """``noun(await kinds(user))``."""
    return noun(await kinds(user, **kw))
