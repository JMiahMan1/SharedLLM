"""The phone's words reach Whisper the way the gateway sends them.

A live take is transcribed every 1.5 s while the person is still talking, so
three things have to hold: the `model`/`language` the gateway puts in the form
are read as form fields (a plain parameter is a query string, which silently
ignored them), Whisper is loaded once instead of per request, and the container
the phone actually recorded is what Whisper is handed (a webm take called .wav
is not decodable).
"""
from __future__ import annotations

import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.execution.main as execution


class FakeWhisper:
    """A Whisper that records what it was asked to do."""

    def __init__(self) -> None:
        self.loads: list[str] = []
        self.calls: list[dict] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = types.ModuleType("whisper")

        def load_model(name: str) -> FakeWhisper:
            self.loads.append(name)
            return self

        module.load_model = load_model
        monkeypatch.setitem(sys.modules, "whisper", module)

    def transcribe(self, path: str, language: str | None = None) -> dict:
        with open(path, "rb") as handle:
            body = handle.read()
        self.calls.append({"suffix": path[path.rindex(".") :], "body": body, "language": language})
        return {"text": "  what is the capital of france  "}


@pytest.fixture
def whisper(monkeypatch: pytest.MonkeyPatch) -> FakeWhisper:
    fake = FakeWhisper()
    fake.install(monkeypatch)
    # The cache is process-wide: a fake constructed per test must not be answered
    # by the previous one's model.
    execution._WHISPER_MODELS.clear()
    return fake


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.post("/execute/stt/transcribe")(execution.transcribe_audio)
    return TestClient(app)


def _post(client: TestClient, *, name: str, kind: str, data: dict | None = None):
    return client.post(
        "/execute/stt/transcribe",
        files={"file": (name, b"\x1aE\xdf\xa3fake-audio", kind)},
        data=data or {},
    )


def test_the_model_and_language_in_the_form_are_used(client, whisper):
    response = _post(client, name="take.webm", kind="audio/webm", data={"model": "tiny", "language": "es"})

    assert response.status_code == 200, response.text
    assert response.json()["transcript"] == "what is the capital of france"
    assert whisper.loads == ["tiny"], "the form's model was ignored, so the default was loaded"
    assert whisper.calls[0]["language"] == "es"


def test_a_request_without_a_model_uses_the_default(client, whisper):
    response = _post(client, name="take.wav", kind="audio/wav")

    assert response.status_code == 200
    assert whisper.loads == ["base"]


def test_the_model_is_loaded_once_for_every_request(client, whisper):
    """A reload per partial is minutes of disk I/O on the same event loop."""
    for _ in range(2):
        assert _post(client, name="take.webm", kind="audio/webm", data={"model": "tiny"}).status_code == 200

    assert whisper.loads == ["tiny"]


def test_the_recorded_container_is_kept(client, whisper):
    """The phone records webm; naming it .wav makes Whisper refuse the take."""
    _post(client, name="take.webm", kind="audio/webm")
    _post(client, name="take.wav", kind="audio/wav")
    _post(client, name="blob", kind="audio/mp4")
    _post(client, name="blob", kind="application/octet-stream")

    assert [call["suffix"] for call in whisper.calls] == [".webm", ".wav", ".m4a", ".wav"]


def test_the_audio_is_passed_through_unchanged(client, whisper):
    body = b"\x1aE\xdf\xa3" + bytes(range(64))
    response = client.post(
        "/execute/stt/transcribe",
        files={"file": ("take.webm", body, "audio/webm")},
        data={"model": "tiny"},
    )

    assert response.status_code == 200
    assert whisper.calls[0]["body"] == body
