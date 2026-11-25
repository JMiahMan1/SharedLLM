"""Which book catalogues a person's lookups use: their integrations, read from identity.

Google Books and Open Library are integrations in Settings > Accounts &
Integrations (services/common/book_metadata.py). Identity resolves them for a
person (their own choices and key, else the household's on the system default
user) in ``/api/resolve``'s ``book_metadata``; this asks for that, once a minute
per person at most, so a change made in the settings applies within a minute
and nothing is read from the environment.

When identity cannot be asked the answer is ``{"error": ...}``: the lookup then
asks no catalogue and says why, instead of guessing settings.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

from services import config
from services.common import book_metadata
from services.storage import book_lookup

log = logging.getLogger(__name__)

CACHE_S = 60.0
TIMEOUT = aiohttp.ClientTimeout(total=10)
#: user ("" = the household) -> (expires, resolved settings, the key included)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

UNREADABLE = f"the catalogue integrations in {book_metadata.WHERE} could not be read (identity did not answer)"


async def identity_resolve(body: dict[str, Any]) -> dict[str, Any] | None:
    """Identity's ``POST /api/resolve``; None for a person it does not know. Raises when it cannot be asked.

    The answer holds secrets: never logged. The tests replace this."""
    async with aiohttp.ClientSession() as session, session.post(
        f"{config.IDENTITY_SVC_URL}/api/resolve", json=body,
        headers={"X-Internal-Secret": config.INTERNAL_SECRET}, timeout=TIMEOUT,
    ) as resp:
        if resp.status in (401, 404):
            return None
        if resp.status != 200:
            raise RuntimeError(f"identity answered HTTP {resp.status}")
        answer = await resp.json(content_type=None)
    return answer if isinstance(answer, dict) else None


async def settings_for(user: str | None) -> dict[str, Any]:
    """``book_metadata.resolve``'s answer for ``user`` (None or "": the household's), or ``{"error": ...}``."""
    who = (user or "").strip().lower()
    cached = _cache.get(who)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        answer = await identity_resolve({"rag_user": who} if who else {"household": True})
    except Exception as exc:
        log.warning("[lookup] identity could not be asked for %s's book catalogues (%s)", who or "the household",
                    type(exc).__name__)
        return {"error": UNREADABLE}
    if answer is None:
        return {"error": f"identity does not know {who!r}, so no catalogue integrations apply"}
    found = answer.get("book_metadata")
    if not isinstance(found, dict) or not isinstance(found.get("sources"), dict):
        # An identity from before the integrations: say so rather than guess.
        return {"error": f"identity sent no catalogue integrations; set them up in {book_metadata.WHERE}"}
    _cache[who] = (time.monotonic() + CACHE_S, found)
    return found


def forget() -> None:
    _cache.clear()


async def status_for(user: str | None) -> dict[str, Any]:
    """What the library page shows: each catalogue's state and why, and its last answer. Never the key."""
    found = await settings_for(user)
    if found.get("error"):
        return {"error": found["error"], "order": list(book_metadata.DEFAULT_ORDER), "sources": {}}
    out = book_metadata.public(found)
    for name, source in out["sources"].items():
        last = book_lookup.LAST_RESULT.get(name)
        if last:
            source["last"] = dict(last)
    return out
