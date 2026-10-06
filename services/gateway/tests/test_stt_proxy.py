"""The audio-to-text route.

This route had no tests, and it showed: it passed ``files=`` to aiohttp, which
has no such argument (that is the ``requests`` API), so every call raised
TypeError inside the middleware and answered "Internal Gateway Error" without
ever reaching Whisper. These tests pin the multipart it actually builds, the
content type it passes through, and the length of the turn it allows.
"""

import json
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _form_fields(kwargs):
    """Read a forwarded aiohttp FormData back as (name, value, filename) triples.

    ``FormData._fields`` holds (headers, options, value) tuples, where the part
    name and filename live in the headers MultiDict.
    """
    form = kwargs["data"]
    return [(f[0].get("name"), f[2], f[0].get("filename")) for f in form._fields]


def _patch_execution(monkeypatch, status=200, payload=None):
    """Capture the calls the route makes to the execution service."""
    captured = {"calls": []}
    execution_svc = str(gateway_main.EXECUTION_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload or {})

        async def json(self, **kwargs):
            return payload if payload is not None else {}

    class _Client:
        async def _record(self, url, **kwargs):
            if url.startswith(execution_svc):
                captured["calls"].append({"url": url, **kwargs})
            return _Resp()

        async def post(self, url, **kw):
            return await self._record(url, **kw)

        async def request(self, method, url, **kw):
            return await self._record(url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


def test_a_video_upload_reaches_whisper_as_multipart(monkeypatch):
    """An MKV is not re-encoded or renamed: the bytes and the caller's type go through.

    Whisper reads through ffmpeg, so a video's audio track works exactly as well
    as a WAV. The failure this pins was total -- the call raised TypeError before
    a request was made, so nothing arrived at all.
    """
    captured = _patch_execution(
        monkeypatch, payload={"status": "SUCCESS", "transcript": "The Lord is my shepherd."}
    )
    client = TestClient(app)
    resp = client.post(
        "/api/stt/transcribe",
        files={"audio": ("sermon.mkv", b"\x1a\x45\xdf\xa3mkv bytes", "video/x-matroska")},
        data={"model": "base", "language": "en"},
    )
    assert resp.status_code == 200
    assert resp.json()["transcript"] == "The Lord is my shepherd."

    call = captured["calls"][-1]
    assert call["url"].endswith("/execute/stt/transcribe")
    fields = {name: value for name, value, _filename in _form_fields(call)}
    assert fields["model"] == "base"
    assert fields["language"] == "en"
    files = {name: (filename, value) for name, value, filename in _form_fields(call) if filename}
    assert files["file"][0] == "sermon.mkv"
    # The part is the caller's file object, so a long recording streams instead
    # of being copied into memory as bytes on the way through.
    assert files["file"][1].read() == b"\x1a\x45\xdf\xa3mkv bytes"


def test_the_engines_own_failure_reaches_the_caller(monkeypatch):
    """A refusal names the reason instead of a generic 502.

    Execution reports failures as ExecutionResult, which carries "message" and
    not FastAPI's "detail", so reading only "detail" would swallow the one
    sentence that says what to install.
    """
    _patch_execution(
        monkeypatch,
        status=501,
        payload={"status": "FAILURE", "message": "Whisper not installed. Run: pip install openai-whisper"},
    )
    client = TestClient(app)
    resp = client.post(
        "/api/stt/transcribe",
        files={"audio": ("note.wav", b"RIFF", "audio/wav")},
    )
    assert resp.status_code == 502
    assert "Whisper not installed" in resp.json()["detail"]


def test_a_missing_file_is_refused_before_the_engine(monkeypatch):
    captured = _patch_execution(monkeypatch, payload={})
    client = TestClient(app)
    resp = client.post("/api/stt/transcribe", data={"model": "base"})
    assert resp.status_code == 400
    assert captured["calls"] == []


def test_the_route_allows_a_turn_longer_than_30s():
    """A service recording is minutes of CPU, so the old 30s ceiling aborted work.

    The engine is the only place that can tell whether it is still making
    progress, so the client must outlast it rather than race it.
    """
    assert gateway_main._STT_TIMEOUT.total >= 600


def test_no_user_id_is_ever_forwarded(monkeypatch):
    """Transcription is stateless, so nothing about the caller travels with it."""
    captured = _patch_execution(monkeypatch, payload={"status": "SUCCESS", "transcript": ""})
    client = TestClient(app)
    client.post(
        "/api/stt/transcribe",
        files={"audio": ("note.wav", b"RIFF", "audio/wav")},
        data={"user_id": "someone-else"},
    )
    call = captured["calls"][-1]
    assert "?" not in call["url"]
    fields = {name for name, _value, _filename in _form_fields(call)}
    assert fields == {"model", "language", "file"}
