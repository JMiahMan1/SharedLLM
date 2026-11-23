"""Verify SharedLLM gateway propagates canonical messaging.py priorities to Alpaca."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from services.gateway import llm_providers
from services.gateway.messaging import (
    PRIORITY_CRITICAL,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
)
from services.gateway.trainer import llm as trainer_llm


class _FakeResp:
    status = 200

    def __init__(self, body: str):
        self._body = body

    async def text(self) -> str:
        return self._body

    def raise_for_status(self) -> None:
        return None


@pytest.mark.asyncio
async def test_ollama_provider_forwards_priority_headers_and_bypasses_client_poll_for_high_critical(monkeypatch):
    posted: list[dict] = []

    class _Client:
        async def post(self, url, json=None, headers=None, timeout=None):
            posted.append({"url": url, "json": json, "headers": dict(headers or {})})
            return _FakeResp('{"message":{"content":"ok"},"done":true}')

    @asynccontextmanager
    async def _fake_client():
        yield _Client()

    import services.gateway.main as gw_main

    monkeypatch.setattr(gw_main, "shared_http_client", _fake_client)
    provider = llm_providers.OllamaProvider("http://alpaca:11434")
    wait_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(provider, "_wait_for_slot", wait_mock)

    # HIGH (voice = 100) enters Alpaca's priority queue directly without local sleep-polling
    res_high = await provider._generate(
        "kat",
        [{"role": "user", "content": "turn on lights"}],
        options={"priority": PRIORITY_HIGH, "request_source": "voice/assistant"},
    )
    assert res_high == "ok"
    wait_mock.assert_not_awaited()
    assert posted[-1]["headers"] == {"X-Request-Source": "voice/assistant", "X-Priority": "100"}
    assert "priority" not in posted[-1]["json"]["options"]
    assert "request_source" not in posted[-1]["json"]["options"]

    # CRITICAL (SOS = 200) also bypasses local sleep-polling
    await provider._generate(
        "kat",
        [{"role": "user", "content": "sos"}],
        options={"priority": PRIORITY_CRITICAL, "request_source": "shared-llm/emergency"},
    )
    wait_mock.assert_not_awaited()
    assert posted[-1]["headers"] == {"X-Request-Source": "shared-llm/emergency", "X-Priority": "200"}

    # NORMAL (50) and LOW (0) still respect _wait_for_slot and forward their X-Priority
    await provider._generate(
        "kat",
        [{"role": "user", "content": "chat"}],
        options={"priority": PRIORITY_NORMAL, "request_source": "shared-llm/app"},
    )
    assert wait_mock.await_count == 1
    assert posted[-1]["headers"] == {"X-Request-Source": "shared-llm/app", "X-Priority": "50"}

    await provider._generate(
        "kat",
        [{"role": "user", "content": "train"}],
        options={"priority": PRIORITY_LOW, "request_source": "shared-llm/trainer"},
    )
    assert wait_mock.await_count == 2
    assert posted[-1]["headers"] == {"X-Request-Source": "shared-llm/trainer", "X-Priority": "0"}


@pytest.mark.asyncio
async def test_trainer_complete_marks_requests_low_priority_and_trainer_source(monkeypatch):
    seen_opts: list[dict] = []

    class _FakeProvider:
        async def generate(self, model, messages, options=None, chunk_callback=None):
            seen_opts.append(dict(options or {}))
            return "trained"

    monkeypatch.setattr(
        trainer_llm,
        "_resolve",
        AsyncMock(return_value=(_FakeProvider(), "kat:iq4", {"trainer_wait_for_slot": "false"})),
    )

    out = await trainer_llm.complete("question")
    assert out.text == "trained"
    assert seen_opts[0]["priority"] == PRIORITY_LOW
    assert seen_opts[0]["request_source"] == "shared-llm/trainer"
