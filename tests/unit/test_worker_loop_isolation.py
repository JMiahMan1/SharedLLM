"""Regression tests for the Raven worker event-loop isolation fix.

Root cause of the old "gateway event-loop saturation": the Raven background
worker ran its loops on the SAME asyncio event loop as the FastAPI API, so a
long-running Raven mission competed with /api/raven/missions for loop turns.

The fix runs the worker on its OWN event loop (dedicated thread) and gives
each event loop its own aiohttp client. These tests pin the per-loop client
caching so a future refactor can't silently reintroduce a shared global that
would thrash/leak when two loops call in.
"""
import asyncio

from services.gateway import agent_loop


async def _get_client() -> object:
    # get_http_client() resolves the client for the CURRENT running loop.
    return agent_loop.get_http_client()


def _client_for_new_loop():
    return asyncio.run(_get_client())


def test_get_http_client_is_per_loop():
    # Two distinct loops must each get their own client instance.
    c1 = _client_for_new_loop()
    c2 = _client_for_new_loop()
    assert c1 is not c2
    assert not c1.closed
    assert not c2.closed


def test_get_http_client_is_stable_within_a_loop():
    # The same loop must reuse its client (no thrash / leak).
    async def _two():
        a = agent_loop.get_http_client()
        b = agent_loop.get_http_client()
        return a, b

    a, b = asyncio.run(_two())
    assert a is b
