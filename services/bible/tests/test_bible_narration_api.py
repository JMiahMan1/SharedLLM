"""The read-aloud endpoints as the reader's client sees them.

The speech engine is stubbed at the HTTP boundary, so these tests cover the
status codes and messages the UI depends on: a passage too long to read is the
reader's problem, a missing voice file is the operator's, and neither arrives as
a blank audio player.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

import services.bible.main as bible_main
from services.bible import narration
from services.bible.models import NarrationAudio

pytestmark = pytest.mark.unit

SECRET = "test-secret"
RIFF = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16


class _Response:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Engine:
    """Replays a queue of responses and records what was asked for."""

    def __init__(self, *responses: _Response):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json or {}))
        if not self.responses:
            raise AssertionError("the speech engine was called more times than expected")
        return self.responses.pop(0)

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, {}))
        if not self.responses:
            raise AssertionError("the speech engine was called more times than expected")
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _audio(payload: bytes = RIFF, mime: str = "audio/wav") -> _Response:
    return _Response(
        200,
        json.dumps(
            {
                "status": "SUCCESS",
                "message": "TTS generated successfully",
                "service": "tts",
                "detail": {
                    "audio_base64": base64.b64encode(payload).decode("utf-8"),
                    "mime_type": mime,
                    "length_bytes": len(payload),
                },
            }
        ),
    )


def _failure(message: str) -> _Response:
    return _Response(200, json.dumps({"status": "FAILURE", "message": message, "service": "tts"}))


def _patch_engine(monkeypatch, engine: _Engine | None):
    def _factory(*args, **kwargs):
        return engine

    monkeypatch.setattr(narration.aiohttp, "ClientSession", _factory)
    monkeypatch.setattr(bible_main.aiohttp, "ClientSession", _factory)


def test_a_passage_is_narrated(loaded_client: TestClient, monkeypatch):
    engine = _Engine(_audio())
    _patch_engine(monkeypatch, engine)
    resp = loaded_client.get("/narration", params={"ref": "John 3", "version": "kjv"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == "kjv"
    assert body["reference"] == "John 3"
    assert body["cached"] is False
    assert body["mime_type"] == "audio/wav"
    assert base64.b64decode(body["audio_base64"]) == RIFF
    assert engine.calls[0][0].endswith("/execute/tts")


def test_a_second_request_is_served_from_the_cache(loaded_client: TestClient, monkeypatch, loaded):
    engine = _Engine(_audio())
    _patch_engine(monkeypatch, engine)
    first = loaded_client.get("/narration", params={"ref": "John 3", "version": "kjv"}).json()
    second = loaded_client.get("/narration", params={"ref": "John 3", "version": "kjv"}).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["audio_base64"] == first["audio_base64"]
    assert len(engine.calls) == 1
    assert loaded.get(NarrationAudio, narration.cache_key("kjv", "John 3", "default")) is not None


def test_the_voice_reaches_the_engine(loaded_client: TestClient, monkeypatch):
    engine = _Engine(_audio())
    _patch_engine(monkeypatch, engine)
    resp = loaded_client.get("/narration", params={"ref": "John 3", "version": "kjv", "voice": "am_adam"})
    assert resp.status_code == 200
    assert engine.calls[0][1]["voice"] == "am_adam"


def test_a_passage_that_is_too_long_to_read_is_a_400(loaded_client: TestClient, monkeypatch):
    engine = _Engine()
    _patch_engine(monkeypatch, engine)
    monkeypatch.setattr(narration, "MAX_VERSES", 2)
    resp = loaded_client.get("/narration", params={"ref": "Gen 1", "version": "kjv"})
    assert resp.status_code == 400
    assert "Select a shorter passage" in resp.json()["detail"]
    assert engine.calls == [], "an over-long passage must not be sent to the speech engine"


def test_a_missing_voice_file_is_a_503_that_names_who_fixes_it(loaded_client: TestClient, monkeypatch):
    _patch_engine(
        monkeypatch,
        _Engine(_failure("TTS generation failed: Kokoro voices missing: /app/models/voices-v1.0.bin")),
    )
    resp = loaded_client.get("/narration", params={"ref": "John 3", "version": "kjv"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "not set up here yet" in detail
    assert "administrator" in detail
    assert "/execute/tts/download" not in detail


def test_an_unreachable_speech_engine_is_a_503(loaded_client: TestClient, monkeypatch):
    class _Broken:
        def post(self, *args, **kwargs):
            raise OSError("Connection refused")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    _patch_engine(monkeypatch, _Broken())
    resp = loaded_client.get("/narration", params={"ref": "John 3", "version": "kjv"})
    assert resp.status_code == 503
    assert "Connection refused" in resp.json()["detail"]


def test_an_unconfigured_execution_service_is_a_503_that_names_the_setting(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "EXECUTION_SVC_URL", "")
    resp = loaded_client.get("/narration", params={"ref": "John 3", "version": "kjv"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "EXECUTION_SVC_URL" in detail
    assert "execution_svc_url" in detail


def test_an_unconfigured_execution_service_also_stops_voices(loaded_client: TestClient, monkeypatch):
    monkeypatch.setattr(bible_main, "EXECUTION_SVC_URL", "")
    resp = loaded_client.get("/voices")
    assert resp.status_code == 503
    assert "EXECUTION_SVC_URL" in resp.json()["detail"]


def test_a_bad_reference_is_a_400_before_the_engine_is_touched(loaded_client: TestClient, monkeypatch):
    engine = _Engine()
    _patch_engine(monkeypatch, engine)
    resp = loaded_client.get("/narration", params={"ref": "Foo 1:1", "version": "kjv"})
    assert resp.status_code == 400
    assert engine.calls == []


def test_an_unimported_translation_is_a_400(loaded_client: TestClient, monkeypatch):
    engine = _Engine()
    _patch_engine(monkeypatch, engine)
    resp = loaded_client.get("/narration", params={"ref": "John 3", "version": "nkjv"})
    assert resp.status_code == 400
    assert "nkjv" in resp.json()["detail"]
    assert engine.calls == []


def test_the_default_translation_is_used_when_none_is_named(loaded_client: TestClient, monkeypatch):
    engine = _Engine(_audio())
    _patch_engine(monkeypatch, engine)
    resp = loaded_client.get("/narration", params={"ref": "John 3"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == "kjv"


def test_an_empty_corpus_points_at_the_admin_page(client: TestClient, monkeypatch):
    _patch_engine(monkeypatch, _Engine())
    resp = client.get("/narration", params={"ref": "John 3"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "Admin > Bible" in detail
    assert "import_corpus" not in detail


def test_voices_are_listed(loaded_client: TestClient, monkeypatch):
    engine = _Engine(_Response(200, json.dumps({"status": "SUCCESS", "voices": ["af_heart", "am_adam"]})))
    _patch_engine(monkeypatch, engine)
    resp = loaded_client.get("/voices")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"voices": ["af_heart", "am_adam"], "count": 2}
    assert engine.calls[0][0].endswith("/execute/tts/voices")


def test_a_voice_list_that_is_not_a_list_is_a_503(loaded_client: TestClient, monkeypatch):
    _patch_engine(monkeypatch, _Engine(_Response(200, json.dumps({"voices": "af_heart"}))))
    resp = loaded_client.get("/voices")
    assert resp.status_code == 503
    assert "unexpected response" in resp.json()["detail"]


def test_a_voice_list_that_is_not_json_is_a_503(loaded_client: TestClient, monkeypatch):
    _patch_engine(monkeypatch, _Engine(_Response(200, "<html>oops</html>")))
    resp = loaded_client.get("/voices")
    assert resp.status_code == 503
    assert "not JSON" in resp.json()["detail"]


def test_an_upstream_voice_list_error_is_a_503(loaded_client: TestClient, monkeypatch):
    _patch_engine(monkeypatch, _Engine(_Response(403, '{"detail": "Forbidden"}')))
    resp = loaded_client.get("/voices")
    assert resp.status_code == 503
    assert "HTTP 403" in resp.json()["detail"]


def test_reading_aloud_needs_the_internal_secret(loaded_client: TestClient):
    assert loaded_client.get("/narration", params={"ref": "John 3"}, headers={"X-Internal-Secret": ""}).status_code == 403
    assert loaded_client.get("/voices", headers={"X-Internal-Secret": "guessed"}).status_code == 403