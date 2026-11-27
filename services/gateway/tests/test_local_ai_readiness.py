"""The local AI is up only when it can serve a model, not when its proxy merely answers.

Measured before this: the GPU box rebooted without its GPU, so the alpaca proxy
answered HTTP while llama-server behind it could not start. The breaker closed
on "any answer", ``/api/status/local-ai`` said "up", and a chat waited 54 s for
the offline message. The proxy says which it is on ``GET /admin/props``: 200 when
llama-server answered it, its own 502 ("Failed to fetch props") when it could
not. Host names here are fake.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.common.backend_health import BackendUnavailable
from services.gateway import local_ai
from services.gateway import main as gateway_main
from services.gateway.tests.test_local_ai_outage import FAKE_BOX, _ask, _no_leak, queued  # noqa: F401

BACKEND_GONE = b'{"detail": "Failed to fetch props: All connection attempts failed"}'
LOADING = b'{"error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}}'


class ProxyProps:
    """Stands in for the proxy's /admin/props: answers with whatever the test sets."""

    def __init__(self, status: int, body: bytes = b"{}"):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, url: str, timeout: float):
        self.calls.append(url)
        return local_ai.proxy_ready(self.status, self.body)


@pytest.fixture
def proxy(monkeypatch):
    props = ProxyProps(200, b'{"n_ctx": 8192}')
    local_ai.LLM.set_url(FAKE_BOX)
    monkeypatch.setattr(local_ai.LLM, "_probe", props)
    return props


# ------------------------------------------------------------ the proxy's signal


def test_the_probe_asks_the_proxy_about_its_model_server():
    assert local_ai.READY_PATH == "/admin/props"
    assert local_ai.LLM.probe_path == "/admin/props"


@pytest.mark.parametrize(("status", "body", "ready"), [
    (200, b'{"n_ctx": 8192}', True),
    (502, BACKEND_GONE, False),  # the proxy could not reach llama-server
    (504, b"", False),
    (503, LOADING, True),  # llama-server's own answer while a model loads: it is there
    (400, b'{"error": "model required"}', True),
])
def test_the_proxy_answer_is_judged_on_its_model_server(status, body, ready):
    verdict = local_ai.proxy_ready(status, body)
    assert verdict.ok is ready and verdict.answered is True


def test_a_proxy_without_the_route_is_judged_as_before_and_says_so_once(caplog):
    local_ai._warned_unjudged.discard(404)
    assert local_ai.proxy_ready(404, b'{"detail": "Not Found"}').ok is True
    assert local_ai.proxy_ready(404, b'{"detail": "Not Found"}').ok is True
    assert sum("no readiness route" in r.message for r in caplog.records) == 1


# ------------------------------------------------------------ proxy up, backend down


def test_proxy_up_backend_down_keeps_it_down_and_the_status_says_so(proxy, monkeypatch):
    # The box was off (breaker open); it comes back with the proxy but no llama-server.
    proxy.status, proxy.body = 502, BACKEND_GONE
    local_ai.LLM.mark_down("ClientConnectorError: Cannot connect to host gpu-box.test:11434")
    assert local_ai.LLM.probe_once() is False
    assert proxy.calls == [f"{FAKE_BOX}/admin/props"]
    assert local_ai.LLM.is_down()

    monkeypatch.setattr(gateway_main, "_require_identity_from_request",
                        AsyncMock(return_value={"user": "alex", "is_admin": False}))
    data = asyncio.run(gateway_main.local_ai_status(object()))
    assert data["local_ai"]["state"] == "down" and data["local_ai"]["offline"] is True
    _no_leak(str(data))


def test_proxy_up_backend_down_chat_fails_fast(proxy, monkeypatch, queued):  # noqa: F811
    proxy.status, proxy.body = 502, BACKEND_GONE
    local_ai.LLM.mark_down("ClientConnectorError: Cannot connect to host gpu-box.test:11434")
    local_ai.LLM.probe_once()
    resp, took = _ask(monkeypatch)
    assert took < 2.0
    assert resp.status_code == 503
    assert resp.json()["error_code"] == "local_ai_offline"
    assert queued == []
    _no_leak(resp.text)


class _HangingProxy:
    """The proxy as it behaves with llama-server gone: /api/ps never answers in time
    (it is waiting on the server), and /api/chat would hang far longer."""

    def __init__(self, chat_answer: str | None = None):
        self.gets = 0
        self.posts = 0
        self.chat_answer = chat_answer

    async def get(self, url, **kw):
        self.gets += 1
        raise TimeoutError()

    async def post(self, url, **kw):
        self.posts += 1
        if self.chat_answer is None:
            raise AssertionError("the chat was sent to a proxy whose model server is gone")
        resp = MagicMock()
        resp.status = 200
        resp.text = AsyncMock(return_value=json.dumps({"message": {"content": self.chat_answer}, "done": True}))
        return resp


def _provider_with(monkeypatch, fake):
    from services.gateway.llm_providers import OllamaProvider

    class _Ctx:
        async def __aenter__(self):
            return fake

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Ctx())
    return OllamaProvider(base_url=FAKE_BOX, timeout=600)


async def test_a_breaker_that_says_up_opens_on_the_first_chat_and_fails_it_fast(proxy, monkeypatch):
    # A gateway restarted while llama-server was dead: the breaker starts "up".
    proxy.status, proxy.body = 502, BACKEND_GONE
    fake = _HangingProxy()
    provider = _provider_with(monkeypatch, fake)

    started = time.monotonic()
    with pytest.raises(BackendUnavailable):
        await provider.generate("assistant", [{"role": "user", "content": "hi"}])
    assert time.monotonic() - started < 1.0
    assert local_ai.LLM.is_down()
    assert fake.posts == 0  # the chat itself never went out to wait
    assert "model server unreachable" in local_ai.LLM.detail

    with pytest.raises(BackendUnavailable):
        await provider.generate("assistant", [{"role": "user", "content": "again"}])
    assert fake.gets == 1  # known down: nothing tried


# ------------------------------------------------------------ backend loading


async def test_a_model_loading_is_not_an_outage(proxy, monkeypatch):
    # /api/ps is slow while a model loads after a swap; llama-server answers the proxy (503 loading).
    proxy.status, proxy.body = 503, LOADING
    fake = _HangingProxy(chat_answer="Here you go.")
    provider = _provider_with(monkeypatch, fake)
    answer = await provider.generate("assistant", [{"role": "user", "content": "hi"}])
    assert answer == "Here you go."
    assert not local_ai.LLM.is_down()
    assert fake.posts == 1


def test_one_slow_answer_does_not_open_it_even_if_the_probe_is_silent(proxy, monkeypatch):
    def silent(url, timeout):
        from services.common.backend_health import ProbeResult

        return ProbeResult(False, answered=False, reason="no answer (TimeoutError)")

    monkeypatch.setattr(local_ai.LLM, "_probe", silent)
    local_ai.LLM.record_failure(TimeoutError())
    local_ai.LLM.probe_once()
    assert not local_ai.LLM.is_down()  # one silence is a busy moment; a second, spaced, would open it


# ------------------------------------------------------------ backend ready


def test_backend_ready_closes_it(proxy, monkeypatch):
    local_ai.LLM.mark_down("background probe found the proxy up but its model server unreachable")
    proxy.status, proxy.body = 200, b'{"n_ctx": 8192}'
    assert local_ai.LLM.probe_once() is True
    assert not local_ai.LLM.is_down()
    assert local_ai.status_snapshot()["local_ai"]["state"] == "up"


def test_pictures_have_their_own_breaker():
    # llama-server being gone says nothing about sd-server behind the same proxy.
    assert local_ai.IMAGES is not local_ai.LLM
    local_ai.LLM.set_url(FAKE_BOX)
    local_ai.LLM.mark_down("found the proxy up but its model server unreachable")
    local_ai.IMAGES.check()  # does not raise
