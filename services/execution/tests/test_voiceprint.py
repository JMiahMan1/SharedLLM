"""Voice signatures: what audio is refused, and in which words.

The model itself (Resemblyzer) is only in the execution image; the tests that
need it skip elsewhere, and the live check after deploy exercises it end to end.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from services.execution import voiceprint

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _tone(seconds: float) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
         "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
        capture_output=True, check=True,
    ).stdout


def test_an_empty_recording_is_refused():
    with pytest.raises(voiceprint.VoiceprintError, match="empty"):
        voiceprint.decode(b"")


def test_an_oversize_recording_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(voiceprint, "MAX_AUDIO_BYTES", 8)
    with pytest.raises(voiceprint.VoiceprintError, match="MB"):
        voiceprint.decode(b"\0" * 9)


@needs_ffmpeg
def test_something_that_is_not_audio_says_it_could_not_be_decoded():
    with pytest.raises(voiceprint.VoiceprintError, match="could not be decoded"):
        voiceprint.decode(b"this is a text file, not a recording")


@needs_ffmpeg
def test_audio_is_decoded_to_16k_mono():
    samples = voiceprint.decode(_tone(1.0))
    assert abs(len(samples) - 16000) < 400


@needs_ffmpeg
def test_a_clip_without_enough_speech_is_refused():
    pytest.importorskip("resemblyzer")
    with pytest.raises(voiceprint.VoiceprintError, match="of speech was heard"):
        voiceprint.embed(_tone(0.5))


def test_the_signature_route_is_registered():
    source = (Path(voiceprint.__file__).parent / "main.py").read_text()
    assert '@app.post("/voice/signature")' in source
