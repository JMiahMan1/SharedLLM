"""Raven sizes num_ctx from the local server's live context (GET /admin/context).

llm_local_max_ctx is only a cap; without a live answer it is the context, else
16384. Host names and models here are fake.
"""
from __future__ import annotations

import contextlib
import errno
import json
import logging

import aiohttp
import pytest

from services.gateway import agent_loop, local_ai

FAKE_BOX = "http://gpu-box.test:11434"
MODEL = "fake-coder:q4"


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status = status
        self._body = "" if body is None else json.dumps(body)

    async def text(self) -> str:
        return self._body


class _FakeClient:
    """Answers /admin/context and /api/ps from fixed replies; records every call."""

    def __init__(self, context: _Resp | Exception, ps_models: int = 1):
        self.context = context
        self.ps_models = ps_models
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url.endswith("/admin/context"):
            if isinstance(self.context, Exception):
                raise self.context
            return self.context
        if url.endswith("/api/ps"):
            return _Resp(200, {"models": [{"name": f"m{i}"} for i in range(self.ps_models)]})
        raise AssertionError(f"unexpected URL {url}")

    def context_calls(self) -> int:
        return sum(1 for url, _ in self.calls if url.endswith("/admin/context"))


@pytest.fixture(autouse=True)
def _clean():
    agent_loop._live_ctx_cache.clear()
    local_ai.LLM.reset()
    yield
    agent_loop._live_ctx_cache.clear()
    local_ai.LLM.reset()


def _use(monkeypatch, client: _FakeClient) -> _FakeClient:
    @contextlib.asynccontextmanager
    async def fake_shared():
        yield client

    monkeypatch.setattr(agent_loop, "shared_http_client", fake_shared)
    return client


def _loaded(n_ctx=65536, per_slot=65536, slots=2) -> _Resp:
    return _Resp(200, {"model": MODEL, "backend_model": "fake-coder--q4", "loaded": True,
                       "n_ctx": n_ctx, "n_ctx_per_slot": per_slot, "slots": slots})


async def _params(cap: str | None = None) -> dict:
    settings = {"llm_local_url": FAKE_BOX}
    if cap is not None:
        settings["llm_local_max_ctx"] = cap
    return await agent_loop.get_vram_safe_params(MODEL, settings)


async def test_live_context_without_a_cap_is_used_as_is(monkeypatch):
    client = _use(monkeypatch, _FakeClient(_loaded()))
    assert (await _params())["num_ctx"] == 65536
    assert (await _params(""))["num_ctx"] == 65536
    assert client.calls[0] == (f"{FAKE_BOX}/admin/context", {"model": MODEL})


async def test_the_setting_caps_the_live_context(monkeypatch):
    _use(monkeypatch, _FakeClient(_loaded()))
    assert (await _params("32768"))["num_ctx"] == 32768


async def test_a_cap_above_the_live_context_never_raises_it(monkeypatch):
    _use(monkeypatch, _FakeClient(_loaded(n_ctx=16384, per_slot=16384)))
    assert (await _params("32768"))["num_ctx"] == 16384


async def test_an_older_proxy_404_falls_back_to_the_setting_with_a_warning(monkeypatch, caplog):
    _use(monkeypatch, _FakeClient(_Resp(404)))
    with caplog.at_level(logging.WARNING):
        assert (await _params("32768"))["num_ctx"] == 32768
    assert any(r.levelno == logging.WARNING and "404" in r.getMessage() for r in caplog.records)


async def test_a_404_without_a_setting_uses_the_default(monkeypatch, caplog):
    _use(monkeypatch, _FakeClient(_Resp(404)))
    with caplog.at_level(logging.WARNING):
        assert (await _params())["num_ctx"] == 16384
    assert any(r.levelno == logging.WARNING and "default" in r.getMessage() for r in caplog.records)


async def test_a_model_not_loaded_falls_back_to_the_setting_with_a_warning(monkeypatch, caplog):
    not_loaded = _Resp(200, {"model": MODEL, "backend_model": None, "loaded": False,
                             "n_ctx": None, "n_ctx_per_slot": None, "slots": None,
                             "note": "not loaded"})
    client = _use(monkeypatch, _FakeClient(not_loaded))
    with caplog.at_level(logging.WARNING):
        assert (await _params("32768"))["num_ctx"] == 32768
    assert any(r.levelno == logging.WARNING and "not loaded" in r.getMessage() for r in caplog.records)
    # Not cached: the next mission asks again, so it sees the model once it loads.
    await _params("32768")
    assert client.context_calls() == 2


async def test_per_slot_is_preferred_over_the_total(monkeypatch):
    _use(monkeypatch, _FakeClient(_loaded(n_ctx=65536, per_slot=32768)))
    assert (await _params())["num_ctx"] == 32768


async def test_the_total_is_used_when_per_slot_is_null(monkeypatch):
    _use(monkeypatch, _FakeClient(_loaded(n_ctx=40960, per_slot=None)))
    assert (await _params())["num_ctx"] == 40960


async def test_a_second_call_within_60_s_uses_the_cache(monkeypatch):
    client = _use(monkeypatch, _FakeClient(_loaded()))
    assert (await _params())["num_ctx"] == 65536
    assert (await _params())["num_ctx"] == 65536
    assert client.context_calls() == 1


async def test_the_cache_expires_after_60_s(monkeypatch):
    client = _use(monkeypatch, _FakeClient(_loaded()))
    clock = {"t": 1000.0}
    monkeypatch.setattr(agent_loop.time, "monotonic", lambda: clock["t"])
    await _params()
    clock["t"] += 61
    await _params()
    assert client.context_calls() == 2


async def test_breaker_down_makes_no_call(monkeypatch):
    local_ai.LLM.set_url(FAKE_BOX)
    local_ai.LLM.mark_down("ClientConnectorError: Cannot connect to host gpu-box.test")

    @contextlib.asynccontextmanager
    async def must_not_call():
        raise AssertionError("called the box while the breaker is open")
        yield

    monkeypatch.setattr(agent_loop, "shared_http_client", must_not_call)
    assert (await _params("32768"))["num_ctx"] == 32768


async def test_an_unreachable_box_opens_the_breaker_and_skips_the_vram_check(monkeypatch):
    key = aiohttp.client_reqrep.ConnectionKey("gpu-box.test", 11434, False, True, None, None, None)
    refused = aiohttp.ClientConnectorError(key, OSError(errno.EHOSTUNREACH, "Connect call failed"))
    local_ai.LLM.set_url(FAKE_BOX)
    client = _use(monkeypatch, _FakeClient(refused))
    assert (await _params("32768"))["num_ctx"] == 32768
    assert local_ai.LLM.is_down()
    assert [url for url, _ in client.calls] == [f"{FAKE_BOX}/admin/context"]


async def test_two_loaded_models_still_halve_the_live_context(monkeypatch):
    _use(monkeypatch, _FakeClient(_loaded(), ps_models=2))
    assert (await _params())["num_ctx"] == 32768


async def test_fuel_budget_follows_num_ctx(monkeypatch):
    _use(monkeypatch, _FakeClient(_loaded()))
    live = await _params()
    agent_loop._live_ctx_cache.clear()
    _use(monkeypatch, _FakeClient(_Resp(404)))
    fallback = await _params()
    assert (live["num_ctx"], fallback["num_ctx"]) == (65536, 16384)

    conv = [{"role": "system", "content": "You are Raven."}]
    for i in range(24):
        role = "assistant" if i % 2 else "user"
        conv.append({"role": role, "content": f"turn {i} " + "word " * 400})
    used = agent_loop._conversation_token_budget(conv)
    # Fits the live budget (65536 - 3072 - 1024) but not the 16384 fallback's.
    assert 0.7 * (16384 - 3072 - 1024) < used < 0.7 * (65536 - 3072 - 1024)
    assert agent_loop._compact_conversation(conv, live) == conv
    assert agent_loop._compact_conversation(conv, fallback) != conv
