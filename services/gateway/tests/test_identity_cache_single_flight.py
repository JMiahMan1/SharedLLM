"""Concurrent identity lookups for one caller share a single request to identity (gateway/cache.py).

A page load sends many requests at once. Each used to miss the cold cache and ask identity
separately, so identity did first-use work (making a person's Music Assistant account) several
times in the same instant. Every key below is made up.
"""
from __future__ import annotations

import asyncio

import pytest

from services.gateway import cache


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.invalidate_identity()
    yield
    cache.invalidate_identity()


async def test_concurrent_misses_share_one_fetch():
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"username": "robin"}

    results = await asyncio.gather(*(cache.get_cached_identity({"api_key": "k-test"}, fetch) for _ in range(5)))
    assert calls == 1
    assert results == [{"username": "robin"}] * 5


async def test_different_callers_are_fetched_separately():
    seen: list[str] = []

    def fetcher(key):
        async def fetch():
            seen.append(key)
            await asyncio.sleep(0.01)
            return {"key": key}
        return fetch

    a, b = await asyncio.gather(cache.get_cached_identity({"api_key": "k-a"}, fetcher("k-a")),
                                cache.get_cached_identity({"api_key": "k-b"}, fetcher("k-b")))
    assert (a, b) == ({"key": "k-a"}, {"key": "k-b"}) and sorted(seen) == ["k-a", "k-b"]


async def test_a_failure_is_shared_and_not_cached():
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        raise RuntimeError("identity said no")

    results = await asyncio.gather(*(cache.get_cached_identity({"api_key": "k-bad"}, fail) for _ in range(3)),
                                   return_exceptions=True)
    assert calls == 1 and all(isinstance(r, RuntimeError) for r in results)

    async def ok():
        return {"username": "robin"}

    assert await cache.get_cached_identity({"api_key": "k-bad"}, ok) == {"username": "robin"}


async def test_a_cancelled_fetch_lets_a_waiter_fetch_for_itself():
    started = asyncio.Event()

    async def hang():
        started.set()
        await asyncio.sleep(10)

    async def ok():
        return {"username": "robin"}

    leader = asyncio.create_task(cache.get_cached_identity({"api_key": "k-c"}, hang))
    await started.wait()
    waiter = asyncio.create_task(cache.get_cached_identity({"api_key": "k-c"}, ok))
    await asyncio.sleep(0)
    leader.cancel()
    assert await waiter == {"username": "robin"}
    with pytest.raises(asyncio.CancelledError):
        await leader
