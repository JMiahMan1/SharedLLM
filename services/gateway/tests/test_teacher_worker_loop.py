"""The worker runs the teacher pass on its own, unless Raven is suspended."""
from __future__ import annotations

import asyncio

import pytest

from services.gateway import agent_loop
from services.gateway import background_worker as bw
from services.gateway.trainer import teacher_auto


@pytest.fixture
def worker(monkeypatch):
    instance = bw.RavenWorker.__new__(bw.RavenWorker)
    instance.is_running = True
    instance._lease_token = "test-lease"
    claims: list[str] = []

    class _Queue:
        async def claim_loop(self, name, token, ttl):
            claims.append(name)
            return True

    instance.job_queue = _Queue()
    instance.claims = claims
    monkeypatch.setattr(teacher_auto, "START_DELAY_S", 0)
    return instance


async def _run_briefly(worker, seconds: float = 0.2) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(worker._teacher_loop(), timeout=seconds)
    worker.is_running = False


def _settings(**values):
    async def fake():
        return values

    return fake


async def test_the_pass_runs_with_the_settings_and_a_notifier(worker, monkeypatch):
    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", _settings(trainer_teacher_auto="true"))
    calls: list[dict] = []

    async def fake_pass(settings, **kwargs):
        calls.append({"settings": settings, **kwargs})
        return {}

    monkeypatch.setattr(teacher_auto, "run_pass", fake_pass)

    await _run_briefly(worker)

    assert len(calls) == 1
    assert calls[0]["settings"]["trainer_teacher_auto"] == "true"
    assert callable(calls[0]["notify"])
    assert worker.claims == ["trainer-teacher"]


async def test_a_suspended_raven_pauses_the_pass(worker, monkeypatch):
    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", _settings(raven_suspended="true"))
    calls: list = []

    async def fake_pass(settings, **kwargs):
        calls.append(settings)
        return {}

    monkeypatch.setattr(teacher_auto, "run_pass", fake_pass)

    await _run_briefly(worker)

    assert calls == [] and worker.claims == []


async def test_unreadable_settings_skip_the_pass(worker, monkeypatch):
    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", _settings())
    calls: list = []

    async def fake_pass(settings, **kwargs):
        calls.append(settings)
        return {}

    monkeypatch.setattr(teacher_auto, "run_pass", fake_pass)

    await _run_briefly(worker)

    assert calls == []
