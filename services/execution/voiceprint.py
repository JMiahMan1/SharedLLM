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
#: How many 1.6 s windows a recording keeps. Enough to show what a voice sounds
#: like across a sentence without making every stored take large.
PARTIALS_MAX = 8
#: A spoken command ("turn off the kitchen lights") is often under 2 s. It is
#: still worth trying: a short clip scores lower, and identity's threshold and
#: margin then answer "not sure" rather than naming the wrong person.
COMMAND_MIN_SPEECH_SECONDS = 1.0
MAX_AUDIO_BYTES = 25 * 1024 * 1024
#: Enrollment quality (``quality``). Speech this close to the room's own noise
#: (loudest frames over quietest, in dB) gives a signature of the room as much
#: as of the voice; a recording this clipped is distorted.
MIN_ENROLL_SNR_DB = 12.0
MAX_CLIPPED_SHARE = 0.01
_FRAME = 480  # 30 ms at 16 kHz
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


def warm_up() -> float:
    """Load the encoder and the VAD now, so the first command does not pay for it.

    Returns how long it took, in seconds.
    """
    import time

    from resemblyzer import preprocess_wav

    started = time.monotonic()
    encoder = _get_encoder()
    noise = np.random.default_rng(0).standard_normal(SAMPLE_RATE).astype(np.float32) * 0.01
    preprocess_wav(noise, source_sr=SAMPLE_RATE)
    encoder.embed_utterance(noise)
    return time.monotonic() - started


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


def quality(samples: np.ndarray) -> dict:
    """How clean a recording is: ``snr_db`` (the loudest tenth of 30 ms frames
    over the quietest tenth) and ``clipped`` (share of samples at full scale)."""
    frames = len(samples) // _FRAME
    if frames < 10:
        return {"snr_db": None, "clipped": 0.0}
    rms = np.sqrt(np.mean(samples[: frames * _FRAME].reshape(frames, _FRAME) ** 2, axis=1) + 1e-12)
    loud, quiet = float(np.percentile(rms, 90)), float(np.percentile(rms, 10))
    clipped = float(np.mean(np.abs(samples) >= 0.99)) if len(samples) else 0.0
    return {"snr_db": round(20 * np.log10(loud / max(quiet, 1e-6)), 1), "clipped": round(clipped, 4)}


def check_quality(samples: np.ndarray) -> dict:
    """``quality``, or VoiceprintError saying what to change when an enrollment
    recording is too noisy or too loud to make a good profile."""
    q = quality(samples)
    if q["clipped"] > MAX_CLIPPED_SHARE:
        raise VoiceprintError("The recording is too loud and distorted. Hold the phone a little further away.")
    if q["snr_db"] is not None and q["snr_db"] < MIN_ENROLL_SNR_DB:
        raise VoiceprintError(
            f"There is too much background noise (speech only {q['snr_db']:.0f} dB above it). "
            "Record somewhere quieter, close to the microphone."
        )
    return q


def embed(audio: bytes, *, enrollment: bool = False) -> dict:
    """The speaker signature of a recording: ``{embedding, speech_seconds, model}``.

    ``enrollment`` also refuses a recording too noisy or clipped to make a good
    profile (``check_quality``) and reports its ``snr_db``.
    Blocking (ffmpeg + CPU inference); async callers run it in a thread.
    """
    samples = decode(audio)
    q = check_quality(samples) if enrollment else None
    signature = embed_samples(samples)
    if q is not None:
        signature["snr_db"] = q["snr_db"]
    return signature


def embed_samples(samples: np.ndarray, min_speech_seconds: float = MIN_SPEECH_SECONDS) -> dict:
    """The signature of 16 kHz mono float32 samples (see ``embed``)."""
    from resemblyzer import preprocess_wav

    speech = preprocess_wav(samples, source_sr=SAMPLE_RATE)
    speech_seconds = len(speech) / SAMPLE_RATE
    if speech_seconds < min_speech_seconds:
        raise VoiceprintError(
            f"Only {speech_seconds:.1f}s of speech was heard; at least {min_speech_seconds:.0f}s is needed. "
            "Speak a full sentence close to the microphone."
        )
    vector, partials = _get_encoder().embed_utterance(speech, return_partials=True)
    windows = [[round(float(x), 6) for x in w] for w in list(partials)[:PARTIALS_MAX]]
    return {
        "embedding": [round(float(x), 6) for x in vector],
        "partials": windows if len(windows) > 1 else [],
        "speech_seconds": round(speech_seconds, 2),
        "model": MODEL,
    }
