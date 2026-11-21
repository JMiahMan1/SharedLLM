"""How many people the household has -- asked of Identity, never guessed.

The planner sizes a garden by "how many are we feeding", and that number is the
household roster, which Identity owns (``GET /api/internal/household``, which
leaves out the stack's own system account). Garden keeps no copy of it: the
profile stores only an explicit override a person typed
(``GardenProfile.family_size_override``), and everything else asks Identity.

There is no fallback number. With Identity unreachable and no override,
:func:`size_or_raise` raises :class:`HouseholdUnavailable` and the route answers
503 saying so, instead of planting for a family size someone made up.
"""
from __future__ import annotations

import logging
import time

import aiohttp

from services import config

log = logging.getLogger("garden.household")

#: Identity answers in milliseconds on the compose network.
TIMEOUT_S = 3.0

#: A roster changes when someone is added or removed, so a few minutes of cache
#: keeps a page of planner calls to one request without hiding that.
_CACHE_TTL_S = 300.0

#: ``(read at, members)``; ``None`` until the first successful read.
_cache: tuple[float, int] | None = None

#: What a person reads when the size cannot be known.
UNAVAILABLE_DETAIL = (
    "The household size comes from Identity, which did not answer. "
    "Try again shortly, or set a family size on the garden profile."
)


class HouseholdUnavailable(RuntimeError):
    """Identity could not say how many people are in the household."""


def invalidate() -> None:
    """Forget the cached roster (a member was added in this process)."""
    global _cache
    _cache = None


async def size() -> int | None:
    """How many people are in the household, or ``None`` when Identity cannot say."""
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
                log.warning("[household] Identity answered HTTP %s for the roster", resp.status)
                return None
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        log.warning("[household] Identity did not answer for the roster: %s", exc)
        return None
    try:
        members = int((data or {}).get("members") or 0)
    except (TypeError, ValueError):
        log.warning("[household] Identity's roster had no usable member count: %r", data)
        return None
    if members < 1:
        log.warning("[household] Identity reports an empty household")
        return None
    _cache = (now, members)
    return members


async def size_or_raise() -> int:
    """The household's size from Identity; :class:`HouseholdUnavailable` otherwise."""
    members = await size()
    if members is None:
        raise HouseholdUnavailable(UNAVAILABLE_DETAIL)
    return members


async def family_size(override: int | None) -> int:
    """An explicit override if a person set one, else Identity's roster (or raise)."""
    if override:
        return int(override)
    return await size_or_raise()
