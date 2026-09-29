"""Last-known-good fallback for configuration fetched from a dependency.

The pattern this encodes, first used by Execution's entity-permission lookup:

    A remote config service is down. Failing every caller closed turns a
    dependency outage into a full outage. Failing them all open quietly
    discards a decision the operator deliberately made. Instead, keep the last
    value that was actually read and serve that when the fetch fails.

This is only appropriate for *configuration* — settings, permissions,
capability lists — where the value is (a) expensive or impossible to recompute
locally, (b) stable between changes, and (c) safe to serve slightly stale.
Never use it for anything an operator expects a failure to surface.

What it deliberately does NOT do:

  * Time out. There is no TTL: a stale value is replaced the instant the next
    successful fetch lands, so an entry's age is exactly the duration of the
    most recent outage. A TTL would add a second failure mode with no benefit.
  * Serve an empty default. A miss is a miss, and the caller decides what "I
    don't know" means for its own safety posture.
  * Swallow the error. The loader reports that it degraded; only the *value*
    is cached, never the failure.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

log = logging.getLogger("common.last_known_good")

T = TypeVar("T")


class LastKnownGoodCache(Generic[T]):
    """A bounded, per-key cache of the last successfully fetched value.

    Bounded by ``max_entries`` with least-recently-inserted eviction, so a
    caller that keys on unbounded input (usernames, tokens) cannot grow it
    without limit. Not thread-safe by design: use one instance per event loop.
    """

    def __init__(self, name: str, max_entries: int = 512) -> None:
        self._name = name
        self._max_entries = max_entries
        self._entries: "OrderedDict[str, T]" = OrderedDict()

    def _evict_if_needed(self) -> None:
        while len(self._entries) >= self._max_entries:
            self._entries.popitem(last=False)

    def remember(self, key: str, value: T) -> None:
        self._evict_if_needed()
        self._entries[key] = value

    def recall(self, key: str) -> T | None:
        return self._entries.get(key)

    def forget(self, key: str | None = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)

    async def fetch_or_recall(
        self,
        key: str,
        load: Callable[[], Awaitable[T]],
    ) -> tuple[T | None, bool]:
        """Fetch ``key``, falling back to the last good value on failure.

        Returns ``(value, is_stale)``. ``is_stale`` is True only when the fetch
        failed and a previously remembered value is being served, so the caller
        can log or surface the degradation itself.
        """
        try:
            value = await load()
        except Exception as e:  # noqa: BLE001 - the point is to survive any failure
            stale = self.recall(key)
            if stale is not None:
                log.warning(
                    "%s: fetch failed for %r (%s); serving last known good value",
                    self._name, key, e,
                )
            else:
                log.error(
                    "%s: fetch failed for %r (%s) and no last known good value exists",
                    self._name, key, e,
                )
            return stale, stale is not None

        self.remember(key, value)
        return value, False
