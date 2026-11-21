"""The gateway's per-loop HTTP client: self-healing and shutdown."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from services.gateway import http_client


@pytest.fixture(autouse=True)
def _clean_pool():
    http_client._http_clients.clear()
    http_client._fallback_http_client = None
    yield
    http_client._http_clients.clear()
    http_client._fallback_http_client = None


class _StaleSession:
    """What older aiohttp reports after an upstream disconnect: the session
    says it is open while its connector is closed."""

    closed = False
    connector = SimpleNamespace(closed=True)


@pytest.mark.asyncio
async def test_a_session_whose_connector_was_closed_is_replaced():
    stale = _StaleSession()
    http_client._http_clients[asyncio.get_running_loop()] = stale  # type: ignore[assignment]

    fresh = http_client.get_http_client()
    assert fresh is not stale
    assert not fresh.closed
    await http_client.close_all_clients()


@pytest.mark.asyncio
async def test_close_all_clients_closes_another_loops_session_on_that_loop():
    other_loop = asyncio.new_event_loop()
    ready = threading.Event()
    thread = threading.Thread(target=lambda: (ready.set(), other_loop.run_forever()), daemon=True)
    thread.start()
    ready.wait()
    try:
        async def make():
            return http_client.get_http_client()

        other_session = asyncio.run_coroutine_threadsafe(make(), other_loop).result(5)
        mine = http_client.get_http_client()
        closed_on: list[asyncio.AbstractEventLoop] = []
        real_close = other_session.close

        async def tracking_close():
            closed_on.append(asyncio.get_running_loop())
            await real_close()

        other_session.close = tracking_close  # type: ignore[method-assign]

        await http_client.close_all_clients()

        assert mine.closed
        for _ in range(50):
            if other_session.closed:
                break
            await asyncio.sleep(0.02)
        assert other_session.closed
        assert closed_on == [other_loop]
        assert http_client._http_clients == {}
    finally:
        other_loop.call_soon_threadsafe(other_loop.stop)
        thread.join(5)
        other_loop.close()


@pytest.mark.asyncio
async def test_close_all_clients_handles_a_stopped_loops_session():
    dead_loop = asyncio.new_event_loop()
    holder = {}

    async def make():
        holder["s"] = http_client._new_http_client()

    t = threading.Thread(target=lambda: dead_loop.run_until_complete(make()))
    t.start()
    t.join(5)
    dead_loop.close()
    session = holder["s"]
    http_client._http_clients[dead_loop] = session

    await http_client.close_all_clients()
    assert session.connector is None or session.connector.closed
    assert http_client._http_clients == {}
