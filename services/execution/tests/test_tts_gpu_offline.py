"""While the GPU box's audio server is off, replies are spoken here at once."""
from __future__ import annotations

import pytest

from services.execution import tts


@pytest.fixture
def speech_box(monkeypatch):
    monkeypatch.setattr(tts, "speech_server", lambda: "http://gpu-box.test:8082")
    yield tts.SPEECH_HEALTH
    tts.SPEECH_HEALTH.reset()


async def test_a_down_box_is_not_called(speech_box, monkeypatch):
    import aiohttp

    def must_not_open(*a, **kw):
        raise AssertionError("called the audio server while it is known to be down")

    monkeypatch.setattr(aiohttp, "ClientSession", must_not_open)
    speech_box.mark_down("refused")
    assert await tts._speak_on_alpaca("Hello there.", "af_heart") is None


async def test_a_connect_failure_opens_it_once(speech_box, monkeypatch, caplog):
    import errno

    import aiohttp

    key = aiohttp.client_reqrep.ConnectionKey("gpu-box.test", 8082, False, True, None, None, None)

    class _Session:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, *a, **kw):
            raise aiohttp.ClientConnectorError(key, OSError(errno.EHOSTUNREACH, "No route to host"))

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    caplog.set_level("WARNING")
    assert await tts._speak_on_alpaca("Hello there.", "af_heart") is None
    assert speech_box.is_down()
    assert await tts._speak_on_alpaca("Hello again.", "af_heart") is None
    loud = [r for r in caplog.records if r.levelno >= 30]
    assert len(loud) == 1  # the breaker's one "unreachable" line, no per-reply warning
