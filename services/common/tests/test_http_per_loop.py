"""services.common.http: one pooled session per running event loop.

RavenWorker runs its own loop in a thread and sends notices through
services.common.notify, which uses get_client(). A session belongs to the loop
that created it, so the two loops must not share one, and neither may close
the other's.
"""
import asyncio
import threading

import pytest

from services.common import http


@pytest.fixture(autouse=True)
def _fresh_pool():
    http._clients.clear()
    yield
    http._clients.clear()


def _session_in_new_thread_loop(stop: threading.Event, ready: dict) -> None:
    """Run a loop in a thread, take its session, and keep the loop alive."""
    async def main():
        ready["session"] = http.get_client()._session
        ready["loop"] = asyncio.get_running_loop()
        ready["event"].set()
        while not stop.is_set():
            await asyncio.sleep(0.01)
        ready["closed_at_end"] = ready["session"].closed
        ready["same_at_end"] = http.get_client()._session is ready["session"]

    asyncio.run(main())


def test_two_loops_get_their_own_session_and_keep_it():
    stop = threading.Event()
    ready: dict = {"event": threading.Event()}
    worker = threading.Thread(target=_session_in_new_thread_loop, args=(stop, ready))

    async def main():
        mine = http.get_client()._session
        worker.start()
        assert ready["event"].wait(5)
        theirs = ready["session"]
        assert theirs is not mine
        # Asking again on this loop neither replaces nor closes either session.
        assert http.get_client()._session is mine
        await asyncio.sleep(0)
        assert not mine.closed and not theirs.closed
        stop.set()
        await asyncio.to_thread(worker.join, 5)
        await http.close_client()
        assert mine.closed

    asyncio.run(main())
    assert ready["closed_at_end"] is False
    assert ready["same_at_end"] is True


def test_tls_and_insecure_are_separate_on_one_loop():
    async def main():
        verified = http.get_client()._session
        insecure = http.get_client_insecure()._session
        assert verified is not insecure
        assert http.get_client()._session is verified
        await http.close_client()
        assert verified.closed and insecure.closed

    asyncio.run(main())


def test_a_finished_loop_is_forgotten():
    async def take():
        return http.get_client()._session

    old = asyncio.run(take())
    assert any(loop is not None and loop.is_closed() for _, loop in http._clients)

    async def main():
        fresh = http.get_client()._session
        assert fresh is not old
        assert all(loop is None or not loop.is_closed() for _, loop in http._clients)
        await http.close_client()

    asyncio.run(main())


def test_a_session_with_a_closed_connector_is_replaced():
    async def main():
        first = http.get_client()._session
        await first.connector.close()
        second = http.get_client()._session
        assert second is not first
        await http.close_client()

    asyncio.run(main())
