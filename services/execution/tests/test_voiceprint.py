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


def _speech_like(noise: float, seconds: float = 3.0):
    """Bursts of tone (speech-like on/off) over steady noise, as 16 kHz float32."""
    import numpy as np

    rate = 16000
    t = np.arange(int(seconds * rate)) / rate
    bursts = (np.sin(2 * np.pi * 2 * t) > 0).astype(np.float32)
    signal = 0.3 * np.sin(2 * np.pi * 220 * t) * bursts
    return (signal + noise * np.random.default_rng(0).standard_normal(len(t))).astype(np.float32)


def test_a_clean_recording_passes_the_enrollment_check():
    q = voiceprint.check_quality(_speech_like(0.001))
    assert q["snr_db"] > voiceprint.MIN_ENROLL_SNR_DB and q["clipped"] == 0


def test_a_noisy_recording_is_refused_in_plain_words():
    with pytest.raises(voiceprint.VoiceprintError, match="background noise"):
        voiceprint.check_quality(_speech_like(0.2))


def test_a_clipped_recording_is_refused():
    import numpy as np

    with pytest.raises(voiceprint.VoiceprintError, match="too loud"):
        voiceprint.check_quality(np.clip(_speech_like(0.001) * 10, -1, 1))


def test_commands_are_not_held_to_the_enrollment_check(monkeypatch):
    calls = []
    monkeypatch.setattr(voiceprint, "decode", lambda audio: _speech_like(0.2))
    monkeypatch.setattr(voiceprint, "embed_samples", lambda samples: calls.append(1) or {"embedding": [0.1]})
    assert "snr_db" not in voiceprint.embed(b"x")
    with pytest.raises(voiceprint.VoiceprintError):
        voiceprint.embed(b"x", enrollment=True)
    assert calls == [1]
