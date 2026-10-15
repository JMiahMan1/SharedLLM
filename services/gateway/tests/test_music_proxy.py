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
async def test_songs_fail_loudly_when_the_backend_is_unconfigured(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "ALPACA_AUDIO_URL", "")

    async def handler(*a, **k):
        raise AssertionError("must not probe a host that was never configured")

    _patch_client(monkeypatch, handler)
    response = await main.music_song_start(_FakeRequest({"prompt": "tune"}))
    assert response.status_code == 503
    assert "ALPACA_AUDIO_URL" in _body(response)["message"]


@pytest.mark.asyncio
async def test_a_song_needs_a_prompt(monkeypatch):
    from services.gateway import main

    async def handler(*a, **k):
        raise AssertionError("must not call the backend without a prompt")

    _patch_client(monkeypatch, handler)
    assert (await main.music_song_start(_FakeRequest({"prompt": "  "}))).status_code == 422


@pytest.mark.asyncio
async def test_a_song_is_queued_and_answers_with_a_job(monkeypatch):
    from services.gateway import main

    captured = {}

    def handler(url, json=None, timeout=None):
        captured["url"], captured["json"] = url, json
        return _Response(202, {"id": "abc123def456", "status": "queued", "position": 2})

    _patch_client(monkeypatch, handler)
    response = await main.music_song_start(
        _FakeRequest({"prompt": "folk", "duration_s": 120, "structure": "song", "ignored": 1})
    )
    body = _body(response)
    assert response.status_code == 200 and body["job"]["position"] == 2
    assert captured["url"] == "http://audio.test:8082/api/music/jobs"
    assert captured["json"] == {"prompt": "folk", "duration_s": 120, "structure": "song"}


@pytest.mark.asyncio
async def test_a_finished_song_comes_back_playable(monkeypatch):
    from services.gateway import main

    _patch_client(
        monkeypatch,
        lambda url, timeout=None: _Response(200, {"id": "abc123", "status": "done", "audio_b64": "AAAA", "mime": "audio/wav", "meta": {"duration_s": 118}}),
    )
    body = _body(await main.music_song_status(_FakeRequest({}), "abc123"))
    assert body["job"]["audio_url"] == "data:audio/wav;base64,AAAA"
    assert "audio_b64" not in body["job"] and body["job"]["meta"]["duration_s"] == 118


@pytest.mark.asyncio
async def test_a_song_refusal_keeps_the_backend_reason(monkeypatch):
    from services.gateway import main

    _patch_client(monkeypatch, lambda *a, **k: _Response(400, {"error": "unknown structure 'polka-rap'"}))
    response = await main.music_song_start(_FakeRequest({"prompt": "x", "structure": "polka-rap"}))
    assert response.status_code == 400 and "polka-rap" in _body(response)["message"]


@pytest.mark.asyncio
async def test_a_failed_song_is_reported_as_a_job_so_the_page_can_say_why(monkeypatch):
    from services.gateway import main

    _patch_client(
        monkeypatch,
        lambda url, timeout=None: _Response(200, {"id": "abc123", "status": "error", "error": "music generation failed: OOM"}),
    )
    response = await main.music_song_status(_FakeRequest({}), "abc123")
    body = _body(response)
    assert response.status_code == 200
    assert body["job"]["status"] == "error" and "OOM" in body["job"]["error"]


@pytest.mark.asyncio
async def test_a_malformed_song_id_never_reaches_the_backend(monkeypatch):
    from services.gateway import main

    def handler(*a, **k):
        raise AssertionError("must not forward a path-like id")

    _patch_client(monkeypatch, handler)
    response = await main.music_song_status(_FakeRequest({}), "../health")
    assert response.status_code == 404


def _body(response):
    return json.loads(bytes(response.body).decode())


class _FakeRequest:
    def __init__(self, body):
        self._body = body
        self.headers = dict(AUTH)

    async def json(self):
        return self._body
