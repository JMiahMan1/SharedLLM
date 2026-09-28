import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# The URL and the shared secret both come from conftest, which pins them before
# any module is imported. A literal in this file would be a second source of
# truth that only agrees when collection order happens to be favourable.

mock_redis = MagicMock()
sys.modules["redis"] = mock_redis
sys.modules["redis.asyncio"] = mock_redis
sys.modules["fastembed"] = MagicMock()
sys.modules["intent_engine"] = MagicMock()
sys.modules["background_worker"] = MagicMock()

AUTH = {"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload


def _patch_client(monkeypatch, handler):
    class _Client:
        def __getattr__(self, name):
            async def call(*args, **kwargs):
                return handler(*args, **kwargs)

            return call

    client = _Client()
    monkeypatch.setattr("services.gateway.main.get_http_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_music_generation_returns_a_playable_data_url(monkeypatch):
    from services.gateway import main

    captured = {}

    def handler(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Response(200, {"audio_b64": "AAAA", "mime": "audio/wav"})

    _patch_client(monkeypatch, handler)

    response = await main.music_generate_proxy(_FakeRequest({"prompt": "a calm piano tune", "duration_s": 6}))
    result = _body(response)

    assert response.status_code == 200
    assert result["status"] == "SUCCESS"
    assert result["audio_url"] == "data:audio/wav;base64,AAAA"
    assert captured["url"] == "http://audio.test:8082/api/music"
    assert captured["json"]["prompt"] == "a calm piano tune"
    assert captured["json"]["duration_s"] == 6


@pytest.mark.asyncio
async def test_music_requires_a_prompt(monkeypatch):
    from services.gateway import main

    async def handler(*a, **k):
        raise AssertionError("must not call the backend without a prompt")

    _patch_client(monkeypatch, handler)
    response = await main.music_generate_proxy(_FakeRequest({"prompt": "   "}))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_music_fails_loudly_when_the_backend_is_unconfigured(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "ALPACA_AUDIO_URL", "")

    async def handler(*a, **k):
        raise AssertionError("must not probe a host that was never configured")

    _patch_client(monkeypatch, handler)
    response = await main.music_generate_proxy(_FakeRequest({"prompt": "tune"}))
    assert response.status_code == 503
    assert "ALPACA_AUDIO_URL" in _body(response)["message"]


@pytest.mark.asyncio
async def test_backend_refusal_is_passed_through(monkeypatch):
    from services.gateway import main

    _patch_client(monkeypatch, lambda *a, **k: _Response(500, {"error": "model failed to load"}))

    response = await main.music_generate_proxy(_FakeRequest({"prompt": "tune"}))
    # The upstream status is passed through, not flattened to 502.
    assert response.status_code == 500
    assert _body(response)["message"] == "model failed to load"


@pytest.mark.asyncio
async def test_backend_silence_is_reported_not_hidden(monkeypatch):
    from services.gateway import main

    _patch_client(monkeypatch, lambda *a, **k: _Response(200, {"mime": "audio/wav"}))

    response = await main.music_generate_proxy(_FakeRequest({"prompt": "tune"}))
    assert response.status_code == 502
    assert "no audio" in _body(response)["message"]


def _body(response):
    return json.loads(bytes(response.body).decode())


class _FakeRequest:
    def __init__(self, body):
        self._body = body
        self.headers = dict(AUTH)

    async def json(self):
        return self._body
