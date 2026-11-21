"""The trainer's background loop must stay out of the way unless asked.

The local model is shared with the household, so the cycle is off by default and
paused by ``raven_suspended``. These tests pin that: nothing is claimed and no
model call is made when the interval is 0 or when Raven is suspended, and the
bounded cycle runs exactly once per interval when it is enabled.
"""
from __future__ import annotations

import asyncio

import pytest

from services.gateway import agent_loop
from services.gateway import background_worker as bw
from services.gateway import trainer as trainer_pkg  # noqa: F401  (imports the package)
from services.gateway.trainer import facilitator


@pytest.fixture
def worker():
    """A worker with just what the loop touches: no Redis, no event loop thread."""
    instance = bw.RavenWorker.__new__(bw.RavenWorker)
    instance.is_running = True
    instance._lease_token = "test-lease"
    claims: list[tuple] = []

    class _Queue:
        async def claim_loop(self, name, token, ttl):
            claims.append((name, token, ttl))
            return True

    instance.job_queue = _Queue()
    instance.claims = claims
    return instance


def _settings(**values):
    async def fake():
        return values

    return fake


async def _run_briefly(worker: bw.RavenWorker, seconds: float = 0.2) -> None:
    """Run the loop for a moment; it is a while-True loop, so cancellation is the exit."""
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(worker._trainer_loop(), timeout=seconds)
    worker.is_running = False


async def test_the_cycle_is_off_when_the_interval_is_zero(worker, monkeypatch):
    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", _settings(trainer_cycle_interval_minutes="0"))
    calls: list[dict] = []

    async def fake_cycle(**kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(facilitator, "cycle", fake_cycle)

    await _run_briefly(worker)

    assert calls == []
    assert worker.claims == [], "an idle trainer must not claim the loop lease"


async def test_suspending_raven_also_pauses_the_trainer(worker, monkeypatch):
    monkeypatch.setattr(
        agent_loop,
        "get_dynamic_llm_settings",
        _settings(trainer_cycle_interval_minutes="30", raven_suspended="true"),
    )
    calls: list[dict] = []

    async def fake_cycle(**kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(facilitator, "cycle", fake_cycle)

    await _run_briefly(worker)

    assert calls == []


async def test_an_enabled_trainer_runs_one_bounded_cycle(worker, monkeypatch):
    monkeypatch.setattr(
        agent_loop,
        "get_dynamic_llm_settings",
        _settings(trainer_cycle_interval_minutes="1", raven_suspended="false"),
    )
    calls: list[dict] = []

    async def fake_cycle(**kwargs):
        calls.append(kwargs)
        return {
            "eval": {"totals": {"cases": 6, "passed": 2}},
            "proposals": [{"id": "p1"}],
            "lessons": ["lesson-a"],
        }

    monkeypatch.setattr(facilitator, "cycle", fake_cycle)

    await _run_briefly(worker, seconds=0.3)

    assert len(calls) == 1, "one cycle per interval, not a loop of cycles"
    assert calls[0]["limit"] == 6
    assert calls[0]["budget_s"] == 240.0
    assert worker.claims and worker.claims[0][0] == "trainer"
