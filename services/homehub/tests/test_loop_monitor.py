"""The loop monitor: a stalled loop names what was running, a queued thread pool is reported, both rate-limited."""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time

import pytest

from services.homehub import loop_monitor
from services.homehub.loop_monitor import LoopMonitor


def _block_the_loop_for(seconds: float) -> None:
    time.sleep(seconds)  # what a synchronous call on the loop does to every device request


async def _run_for(monitor: LoopMonitor, body) -> None:
    task = asyncio.create_task(monitor.run())
    await asyncio.sleep(0.15)  # the heartbeat and the watchdog are going
    try:
        await body()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_stall_is_reported_with_the_code_that_held_the_loop(caplog):
    caplog.set_level(logging.WARNING, logger="homehub.loop")
    monitor = LoopMonitor(stall_s=0.15, beat_s=0.05, probe_every_s=3600)

    async def body():
        _block_the_loop_for(0.5)
        await asyncio.sleep(0.2)

    await _run_for(monitor, body)
    assert monitor.stats["stalls"] == 1
    assert monitor.stats["worst_stall_s"] >= 0.3
    text = caplog.text
    assert "event loop stalled" in text
    assert "_block_the_loop_for" in text  # the stack, taken while the loop was stuck


async def test_stall_warnings_are_rate_limited_and_count_the_ones_held_back(caplog):
    caplog.set_level(logging.WARNING, logger="homehub.loop")
    monitor = LoopMonitor(stall_s=0.1, beat_s=0.05, probe_every_s=3600, report_every_s=3600)

    async def body():
        for _ in range(3):
            _block_the_loop_for(0.3)
            await asyncio.sleep(0.15)

    await _run_for(monitor, body)
    assert monitor.stats["stalls"] == 3
    assert caplog.text.count("event loop stalled") == 1
    # The next one after the quiet period says how many were not logged.
    monitor.report_every_s = 0
    monitor.stalled(0.4)
    assert "2 more like it" in caplog.text


async def test_a_busy_loop_that_keeps_up_says_nothing(caplog):
    caplog.set_level(logging.WARNING, logger="homehub.loop")
    monitor = LoopMonitor(stall_s=0.2, beat_s=0.05, probe_every_s=3600)

    async def body():
        for _ in range(20):
            _block_the_loop_for(0.01)
            await asyncio.sleep(0.01)

    await _run_for(monitor, body)
    assert monitor.stats["stalls"] == 0
    assert "stalled" not in caplog.text


async def test_a_saturated_default_pool_is_reported(caplog):
    caplog.set_level(logging.WARNING, logger="homehub.loop")
    loop = asyncio.get_running_loop()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(pool)
    release = threading.Event()
    held = loop.run_in_executor(None, release.wait)  # the only thread is busy
    loop.call_later(0.4, release.set)
    monitor = LoopMonitor(pool_warn_s=0.2)
    waited = await monitor.probe_pools()
    await held
    assert waited["executor"] >= 0.3
    assert "default thread pool took" in caplog.text
    assert monitor.snapshot()["pool_waits"] == 1


async def test_a_probe_that_never_runs_is_reported_as_more_than_its_timeout(caplog):
    caplog.set_level(logging.WARNING, logger="homehub.loop")
    monitor = LoopMonitor(pool_warn_s=0.05, probe_timeout_s=0.1)

    async def never() -> None:
        await asyncio.sleep(10)

    monitor._resolver_job = never  # type: ignore[method-assign]
    waited = await monitor.probe_pools()
    assert waited["resolver"] == float("inf")
    assert "resolver pool took more than 0s" in caplog.text
    assert monitor.snapshot()["pools_s"]["resolver"] is None


def test_the_resolver_probe_runs_on_uvloop():
    uvloop = pytest.importorskip("uvloop")
    loop = uvloop.new_event_loop()
    try:
        assert loop_monitor.loop_kind(loop) == "uvloop"
        waited = loop.run_until_complete(LoopMonitor().probe_pools())
    finally:
        loop.close()
    assert waited["resolver"] < 1.0 and waited["executor"] < 1.0


async def test_health_reports_the_loop(hub_factory):
    from fastapi.testclient import TestClient

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store

    hub, _ = hub_factory()
    store = Store(":memory:")
    runtime = Runtime(hub, store)
    runtime.loop_monitor = LoopMonitor()
    runtime.loop_monitor.stalled(0.7)
    app = build_app(runtime, Auth(store, "secret"))
    body = TestClient(app).get("/health").json()
    assert body["loop"]["stalls"] == 1 and body["loop"]["worst_stall_s"] == 0.7
