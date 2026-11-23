"""Execution rides out identity being away instead of hanging.

2026-11-22: identity was recreated by an update and was unreachable for about
three minutes. Execution, starting then, retried its runtime config five times,
gave up for good (the settings were never loaded later), and on shutdown its
lifespan raised CancelledError ("Application shutdown failed. Exiting.") before
stopping its other loops, so docker had to kill it.
"""

import asyncio

import pytest

from services import config
from services.execution import main


@pytest.mark.asyncio
async def test_runtime_config_keeps_retrying_with_backoff_until_identity_answers(monkeypatch):
    answers = iter([False, False, False, True])
    calls = []

    async def resolve(**kwargs):
        calls.append(kwargs)
        return next(answers)

    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(config, "resolve_runtime_config", resolve)
    await config.keep_resolving_runtime_config(first_delay=2.0, max_delay=5.0, sleep=sleep)
    assert len(calls) == 4
    assert waits == [2.0, 4.0, 5.0, 5.0]
    assert all(c.get("quiet") for c in calls)  # one warning for the outage, not one per attempt


@pytest.mark.asyncio
async def test_resolve_reports_failure_instead_of_pretending(monkeypatch):
    monkeypatch.setattr(config, "_is_testing", lambda: False)

    class _Refused:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise ConnectionError("identity: Name or service not known")

        async def __aexit__(self, *exc):
            return False

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", _Refused)
    assert await config.resolve_runtime_config(max_retries=2, retry_delay=0, timeout=0.1) is False


@pytest.mark.asyncio
async def test_shutdown_stops_every_loop_even_though_cancelled_tasks_raise():
    stopped = []

    async def loop(name):
        try:
            await asyncio.sleep(3600)
        finally:
            stopped.append(name)

    tasks = [asyncio.create_task(loop(n)) for n in ("telemetry", "prune", "glance")]
    await asyncio.sleep(0)
    await main._stop_background(tasks)  # must not raise CancelledError
    assert sorted(stopped) == ["glance", "prune", "telemetry"]
    assert all(t.cancelled() for t in tasks)
