"""The trainer's one model call: small, bounded, and polite to the household.

``complete`` must trim an over-long prompt, enforce a wall clock, and -- before
it spends the GPU -- ask Alpaca for a slot. A host that is busy is a loud,
recorded failure; a host that has no queue endpoint at all (a bare llama.cpp
server) must not stop the trainer from working.
"""
from __future__ import annotations

import asyncio

import pytest

from services.gateway.trainer import alpaca, llm


class _Provider:
    def __init__(self, reply: str = "an answer", delay: float = 0.0):
        self.reply = reply
        self.delay = delay
        self.calls: list[dict] = []

    async def generate(self, model, messages, options=None, chunk_callback=None):
        self.calls.append({"model": model, "messages": messages, "options": options})
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


def test_a_long_prompt_keeps_its_head_and_tail():
    prompt = "HEAD " + ("x" * 5000) + " TAIL"

    trimmed, changed = llm.trim_prompt(prompt, limit=1000)

    assert changed is True
    assert len(trimmed) <= 1000 + len(llm._TRIM_MARKER)
    assert trimmed.startswith("HEAD")
    assert trimmed.endswith("TAIL")
    assert "middle of the request trimmed" in trimmed


def test_a_short_prompt_is_untouched():
    assert llm.trim_prompt("short prompt", limit=1000) == ("short prompt", False)


def test_the_model_choice_prefers_the_trainer_cost_knobs():
    assert llm.model_from_settings({"trainer_model": "a", "coding_model": "b"}) == "a"
    assert llm.model_from_settings({"coding_model": "b", "assistant_model": "c"}) == "b"
    assert llm.model_from_settings({}) == ""


async def test_a_completion_is_bounded_and_recorded(monkeypatch):
    provider = _Provider("an answer")

    async def resolve(model):
        return provider, "qwen3:8b", {"llm_local_url": "http://host:11434"}

    async def admit(settings):
        return None

    monkeypatch.setattr(llm, "_resolve", resolve)
    monkeypatch.setattr(llm, "_admit", admit)

    completion = await llm.complete("do the thing", system="be brief", max_tokens=64)

    assert completion.text == "an answer"
    assert completion.model == "qwen3:8b"
    assert provider.calls[0]["messages"][0] == {"role": "system", "content": "be brief"}
    assert provider.calls[0]["options"]["num_predict"] == 64
    assert "num_ctx" not in provider.calls[0]["options"], "forcing a context reloads the household model"


async def test_an_empty_answer_is_a_failure_not_a_result(monkeypatch):
    provider = _Provider("   ")

    async def resolve(model):
        return provider, "qwen3:8b", {}

    async def admit(settings):
        return None

    monkeypatch.setattr(llm, "_resolve", resolve)
    monkeypatch.setattr(llm, "_admit", admit)

    with pytest.raises(llm.LocalModelUnavailable):
        await llm.complete("do the thing")


async def test_a_slow_model_is_cut_off(monkeypatch):
    provider = _Provider("late", delay=5)

    async def resolve(model):
        return provider, "qwen3:8b", {}

    async def admit(settings):
        return None

    monkeypatch.setattr(llm, "_resolve", resolve)
    monkeypatch.setattr(llm, "_admit", admit)

    with pytest.raises(llm.LocalModelUnavailable) as excinfo:
        await llm.complete("do the thing", timeout=0.05)
    assert "did not answer" in str(excinfo.value)


async def test_a_busy_host_refuses_the_call_loudly(monkeypatch):
    async def busy(settings, timeout=45.0):
        return {"status": "timeout", "waited": timeout}

    monkeypatch.setattr(alpaca, "wait_for_slot", busy)

    with pytest.raises(llm.LocalModelUnavailable) as excinfo:
        await llm._admit({"llm_local_url": "http://host:11434", "trainer_slot_wait_seconds": "5"})

    assert "busy with the household" in str(excinfo.value)


async def test_a_host_without_a_queue_is_not_a_refusal(monkeypatch):
    async def no_endpoint(url, body, *, timeout=6.0):
        raise alpaca.AlpacaUnavailable(f"{url} answered 404", status=404)

    monkeypatch.setattr(alpaca, "post_json", no_endpoint)

    await llm._admit({"llm_local_url": "http://plain:8080"})  # must not raise


async def test_an_unanswered_slot_question_is_not_permission(monkeypatch):
    async def down(url, body, *, timeout=6.0):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(alpaca, "post_json", down)

    with pytest.raises(llm.LocalModelUnavailable, match="free slot"):
        await llm._admit({"llm_local_url": "http://host:11434"})


async def test_a_busy_host_is_reported_as_busy_not_broken(monkeypatch):
    async def busy(url, body, *, timeout=6.0):
        return {"status": "timeout", "waited": 45.0}

    monkeypatch.setattr(alpaca, "post_json", busy)

    with pytest.raises(llm.LocalModelBusy):
        await llm._admit({"llm_local_url": "http://host:11434"})


async def test_the_polite_wait_can_be_turned_off(monkeypatch):
    called: list[float] = []

    async def busy(settings, timeout=45.0):
        called.append(timeout)
        return {"status": "timeout"}

    monkeypatch.setattr(alpaca, "wait_for_slot", busy)

    await llm._admit({"llm_local_url": "http://host:11434", "trainer_wait_for_slot": "false"})

    assert called == [], "the setting must be honoured before any network call"


INVENTORY = {
    "models": [
        {"name": "qwen3:8b", "router_status": "loaded"},
        {"name": "coder:14b"},
        {"name": "flux-image:q8"},
    ]
}


def _inventory(monkeypatch, payload=INVENTORY, error: Exception | None = None):
    async def fake_get(url, *, timeout=6.0):
        assert url.endswith("/admin/models")
        if error:
            raise error
        return payload

    monkeypatch.setattr(alpaca, "get_json", fake_get)


HOST = {"llm_local_url": "http://llm-host:11434"}


async def test_resolve_uses_the_trainer_choice_from_the_inventory(monkeypatch):
    _inventory(monkeypatch)

    assert await llm.resolve_model({**HOST, "coding_model": "coder:14b"}) == "coder:14b"
    assert await llm.resolve_model(HOST) == "qwen3:8b", "no preference: the loaded text model"


async def test_resolve_honours_a_requested_model_and_checks_it(monkeypatch):
    _inventory(monkeypatch)

    assert await llm.resolve_model(HOST, "coder:14b") == "coder:14b"
    with pytest.raises(llm.LocalModelUnavailable, match="not installed"):
        await llm.resolve_model(HOST, "missing:1b")


@pytest.mark.parametrize(
    "settings, requested",
    [({**HOST, "trainer_model": "flux-image:q8"}, None), (HOST, "flux-image:q8")],
)
async def test_resolve_refuses_a_picture_model(monkeypatch, settings, requested):
    _inventory(monkeypatch)

    with pytest.raises(llm.LocalModelUnavailable, match="picture model"):
        await llm.resolve_model(settings, requested)


async def test_resolve_refuses_a_missing_trainer_model(monkeypatch):
    _inventory(monkeypatch)

    with pytest.raises(llm.LocalModelUnavailable, match="trainer_model"):
        await llm.resolve_model({**HOST, "trainer_model": "gone:7b", "coding_model": "coder:14b"})


async def test_resolve_refuses_when_the_inventory_cannot_be_read(monkeypatch):
    _inventory(monkeypatch, error=alpaca.AlpacaUnavailable("answered 500", status=500))

    with pytest.raises(llm.LocalModelUnavailable, match="Could not list"):
        await llm.resolve_model({**HOST, "coding_model": "coder:14b"})


async def test_resolve_on_a_plain_server_uses_the_setting_but_never_an_image_model(monkeypatch):
    _inventory(monkeypatch, error=alpaca.AlpacaUnavailable("answered 404", status=404))

    assert await llm.resolve_model({**HOST, "coding_model": "coder:14b"}) == "coder:14b"
    with pytest.raises(llm.LocalModelUnavailable, match="picture model"):
        await llm.resolve_model({**HOST, "coding_model": "sdxl-turbo"})
