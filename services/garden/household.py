"""How many people the household has -- asked of Identity, never guessed.

The planner sizes a garden by "how many are we feeding", and that number is the
household roster, which Identity owns (``GET /api/internal/household``). Garden
used to write ``7`` at every place it needed a family size, so adding a member
left the planner planting for the old family and nothing said so.

Like the Home Hub rooms client, this degrades instead of raising: with Identity
unreachable :func:`size` returns ``None`` and the caller uses
:data:`LAST_RESORT` -- one documented number in one place, rather than a literal
repeated at four call sites.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from services import config

log = logging.getLogger("garden.household")

#: What to plan for when Identity cannot be reached. A fallback, not the source.
LAST_RESORT = 7

#: Identity answers in milliseconds on the compose network.
TIMEOUT_S = 3.0

#: A roster changes when someone is added or removed, so a few minutes of cache
#: keeps a page of planner calls to one request without hiding that.
_CACHE_TTL_S = 300.0

#: ``(read at, members)``; ``None`` until the first successful read.
_cache: tuple[float, int] | None = None


def invalidate() -> None:
    """Forget the cached roster (a member was added in this process)."""
    global _cache
    _cache = None


async def size() -> int | None:
    """How many people are in the household, or ``None`` when Identity is away."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_S:
        return _cache[1]
    url = f"{config.IDENTITY_SVC_URL.rstrip('/')}/api/internal/household"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)
        ) as client, client.get(
            url, headers={"X-Internal-Secret": config.INTERNAL_SECRET}
        ) as resp:
            if resp.status != 200:
                log.info("[household] Identity answered %s; using %s", resp.status, LAST_RESORT)
                return None
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        log.info("[household] Identity did not answer (%s); using %s", exc, LAST_RESORT)
        return None
    try:
        members = int((data or {}).get("members") or 0)
    except (TypeError, ValueError):
        return None
    if members < 1:
        return None
    _cache = (now, members)
    return members


async def size_or_last_resort() -> int:
    """The household's size, or :data:`LAST_RESORT` when it cannot be read."""
    return await size() or LAST_RESORT
