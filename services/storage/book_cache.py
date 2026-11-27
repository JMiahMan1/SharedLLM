"""Opened library books kept in memory between requests, bounded by bytes.

A chapter read used to download the book from Nextcloud and parse it on every
request (27 s for one PDF chapter, the same on an immediate repeat). Here each
book is opened once and kept, keyed by the library and the book, and checked
against the file's signature (its WebDAV ETag, size and modification time), so a
changed file is read again and an unchanged one never is.

The cache is a least-recently-used list bounded by an estimate of the bytes each
entry holds (``CALIBRE_BOOK_CACHE_MB``). Loads take a per-key lock, so two
requests for the same book while it downloads share the one download.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

log = logging.getLogger("storage.book_cache")


class ByteLRU:
    """``key -> (signature, value)``, least recently used dropped first past ``max_bytes``."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(int(max_bytes), 0)
        self._items: OrderedDict[Hashable, tuple[Any, Any, int]] = OrderedDict()
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self.total_bytes = 0
        self.loads = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._items

    def get(self, key: Hashable, signature: Any) -> Any | None:
        """The value kept for ``key`` if it was stored under ``signature``, else ``None``."""
        item = self._items.get(key)
        if item is None:
            return None
        if item[0] != signature:
            self.discard(key)
            return None
        self._items.move_to_end(key)
        return item[1]

    def put(self, key: Hashable, signature: Any, value: Any, size: int) -> None:
        """Keep ``value``; an entry larger than the whole budget is not kept at all."""
        self.discard(key)
        size = max(int(size), 0)
        if size > self.max_bytes:
            log.info("[book cache] %s (%d bytes) is larger than the cache (%d bytes); not kept",
                     key, size, self.max_bytes)
            return
        self._items[key] = (signature, value, size)
        self.total_bytes += size
        self._evict()

    def resize(self, key: Hashable, size: int) -> None:
        """Account for an entry that grew (a PDF chapter's pages read into it)."""
        item = self._items.get(key)
        if item is None:
            return
        size = max(int(size), 0)
        self.total_bytes += size - item[2]
        self._items[key] = (item[0], item[1], size)
        if size > self.max_bytes:
            self.discard(key)
            return
        self._evict(keep=key)

    def resize_value(self, value: Any, size: int) -> None:
        """``resize`` for whichever entry holds ``value`` (if it is still kept)."""
        for key, item in list(self._items.items()):
            if item[1] is value:
                self.resize(key, size)
                return

    def discard(self, key: Hashable) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            self.total_bytes -= item[2]

    def clear(self) -> None:
        self._items.clear()
        self._locks.clear()
        self.total_bytes = 0

    def _evict(self, keep: Hashable | None = None) -> None:
        while self.total_bytes > self.max_bytes and self._items:
            oldest = next(iter(self._items))
            if oldest == keep and len(self._items) == 1:
                break
            if oldest == keep:
                self._items.move_to_end(keep)
                continue
            self.discard(oldest)

    async def get_or_load(
        self, key: Hashable, signature: Any, load: Callable[[], Awaitable[tuple[Any, int]]],
    ) -> tuple[Any, bool]:
        """``(value, was_cached)``: the kept value, or ``load()``'s, loaded once however many ask."""
        value = self.get(key, signature)
        if value is not None:
            return value, True
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                value = self.get(key, signature)
                if value is not None:
                    return value, True
                value, size = await load()
                self.loads += 1
                self.put(key, signature, value, size)
                return value, False
        finally:
            if not lock.locked() and self._locks.get(key) is lock:
                self._locks.pop(key, None)


def _default_max_bytes() -> int:
    from services.config import CALIBRE_BOOK_CACHE_MB

    return int(float(CALIBRE_BOOK_CACHE_MB) * 1024 * 1024)


#: The storage service's one book cache.
BOOKS = ByteLRU(_default_max_bytes())
