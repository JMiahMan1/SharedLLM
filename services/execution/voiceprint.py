"""Voice signatures, computed on this machine.

A signature is the 256-dimensional GE2E speaker embedding from Resemblyzer:
its weights ship inside the package (17 MB), it runs on the CPU torch this
image already carries for Whisper, and nothing leaves the server. Identity
stores each person's enrollment samples and matches against them; this module
only turns audio into a vector.

The audio is decoded with ffmpeg, so whatever a browser's MediaRecorder hands
over (WebM/Opus, MP4/AAC on Safari) or a workspace clip is in, it arrives as
16 kHz mono float32. ``preprocess_wav`` then trims silence with WebRTC VAD and
normalises loudness, and what is left is what the speech length is measured on:
a clip of mostly silence must not enroll as a voice.
"""

import subprocess
import threading

import numpy as np

MODEL = "resemblyzer-ge2e-256"
SAMPLE_RATE = 16000
#: Below this much actual speech the embedding is mostly noise. Calibrated on
#: Kokoro voices: one sentence (~3 s) already separates speakers cleanly, and
#: 2 s leaves room for a short phrase without accepting a cough.
MIN_SPEECH_SECONDS = 2.0
MAX_AUDIO_BYTES = 25 * 1024 * 1024
_DECODE_TIMEOUT_S = 60

_encoder = None
_encoder_lock = threading.Lock()


class VoiceprintError(ValueError):
    """The audio cannot produce a signature, in words a person can act on."""


def _get_encoder():
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            from resemblyzer import VoiceEncoder

            _encoder = VoiceEncoder("cpu", verbose=False)
        return _encoder


def decode(audio: bytes) -> np.ndarray:
    """Any container ffmpeg reads, as 16 kHz mono float32 samples."""
    if not audio:
        raise VoiceprintError("The recording is empty.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceprintError(
            f"The recording is {len(audio) // 1048576} MB; the most a voice sample can be is "
            f"{MAX_AUDIO_BYTES // 1048576} MB."
        )
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"],
            input=audio,
            capture_output=True,
            timeout=_DECODE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise VoiceprintError("Decoding the recording took too long.") from None
    if proc.returncode != 0:
        reason = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise VoiceprintError(f"The recording could not be decoded: {reason[-1] if reason else 'unknown format'}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def embed(audio: bytes) -> dict:
    """The speaker signature of a recording: ``{embedding, speech_seconds, model}``.

    Blocking (ffmpeg + CPU inference); async callers run it in a thread.
    """
    from resemblyzer import preprocess_wav

    samples = decode(audio)
    speech = preprocess_wav(samples, source_sr=SAMPLE_RATE)
    speech_seconds = len(speech) / SAMPLE_RATE
    if speech_seconds < MIN_SPEECH_SECONDS:
        raise VoiceprintError(
            f"Only {speech_seconds:.1f}s of speech was heard; at least {MIN_SPEECH_SECONDS:.0f}s is needed. "
            "Speak a full sentence close to the microphone."
        )
    vector = _get_encoder().embed_utterance(speech)
    return {
        "embedding": [round(float(x), 6) for x in vector],
        "speech_seconds": round(speech_seconds, 2),
        "model": MODEL,
    }
