"""A Raven mission crash: the worker registers a mission with its id alone.

Live traceback this pins (gateway, 2026-11-21):

    File "/app/services/gateway/background_worker.py", line 880, in _process_inference_job
        await self.job_queue.register_raven_mission(mission_id)
    TypeError: InferenceJobQueue.register_raven_mission() missing 1 required
               positional argument: 'pause_event'

The pause event was never used -- preemption is signalled through Redis -- so
demanding one was a signature that could only fail. These tests keep the one-arg
call working while still tolerating callers that pass an event.
"""
from __future__ import annotations

import asyncio

import pytest

from services.gateway import messaging


@pytest.fixture
def queue():
    return messaging.InferenceJobQueue("redis://localhost:6379/0")


@pytest.fixture(autouse=True)
def _clean_mission():
    """Leave the module-level mission marker as it was found."""
    previous = messaging._current_raven_mission
    yield
    messaging._current_raven_mission = previous


async def test_registering_a_mission_needs_only_its_id(queue):
    await queue.register_raven_mission(42)

    assert await queue.get_current_raven_mission() == 42


async def test_a_caller_may_still_pass_a_pause_event(queue):
    await queue.register_raven_mission(7, asyncio.Event())

    assert await queue.get_current_raven_mission() == 7


async def test_unregistering_clears_the_mission(queue):
    await queue.register_raven_mission(42)

    await queue.unregister_raven_mission(42)

    assert await queue.get_current_raven_mission() is None


async def test_unregistering_a_different_mission_leaves_the_current_one(queue):
    """A stale job finishing must not deregister the mission that replaced it."""
    await queue.register_raven_mission(99)

    await queue.unregister_raven_mission(42)

    assert await queue.get_current_raven_mission() == 99
