"""Whispering, heard from the command's own audio.

A whisper is speech without the voice: the vocal folds do not vibrate, so there
is no pitch, and what is left is breath noise shaped by the mouth. Three things
follow, and each is cheap to measure on the 30 ms frames the hub already cuts:

* **level**: a whisper is much quieter than the same person talking to the same
  satellite. The comparison is with that satellite's usual speech level, a
  running average of its normal (not whispered) commands kept in the hub's store
  (``LevelBaseline``), because the absolute level depends on the room, the
  distance and the microphone.
* **voicing**: the share of speech frames with a clear pitch period (normalised
  autocorrelation of at least ``VOICED_R`` at a lag between 2.5 and 14 ms, 70 to
  400 Hz). Talking is mostly voiced; whispering almost never is. The median
  harmonics-to-noise ratio of those frames is reported with it.
* **spectral tilt**: energy at 2 to 5 kHz against 100 Hz to 1 kHz. Voiced speech
  puts most of its energy in the low harmonics; a whisper has none there, so the
  balance moves up. It is measured and logged but does not decide: on the test
  clips spoken and whispered tilts overlapped (-22 to -11 dB against -16 to
  -4 dB), while voicing and the harmonics-to-noise ratio never did.

The thresholds come from measuring Kokoro speech (ten voices, three commands
each) against the same clips whispered (their LPC envelope driven by noise
instead of the voice, 12 to 24 dB quieter), each with room noise; the numbers
are in docs/ECHO_SATELLITES.md ("Whisper mode"). Pitchless (few voiced frames
and a low harmonics-to-noise ratio) and quieter than usual together is a
whisper; neither alone is (a voice from across the room is quiet but voiced; a
whisper right at the microphone is pitchless but not quieter, and is answered
at the normal volume).

Only numbers leave this module: no audio is kept or logged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

RATE = 16000
FRAME = 480  # 30 ms, the VAD's frame
#: Each frame's pitch is looked for over 40 ms around it: a 14 ms period needs room to repeat.
WINDOW = 640
MIN_LAG, MAX_LAG = RATE // 400, RATE // 70  # 400 Hz down to 70 Hz
#: A frame is voiced when its normalised autocorrelation reaches this at a pitch lag.
VOICED_R = 0.45
#: Fewer speech frames than this (0.3 s) says nothing either way.
MIN_SPEECH_FRAMES = 10
#: Speech frames: within this many dB of the loud end of the command, and clearly over the room.
SPEECH_RANGE_DB = 25.0
OVER_FLOOR = 2.5

# -- the decision (measured; see the module docstring) -------------------------------------
#: Synthetic whispered clips had at most 0.25 of their speech frames voiced, spoken ones (near or far)
#: at least 0.55. Real whispers at the Office Echo (2026-11-25) were 0.36 to 0.43 voiced, and one at
#: 0.43 was missed, so the bar sits just under the spoken floor.
MAX_VOICED_RATIO = 0.50
#: The median harmonics-to-noise ratio of synthetic whispered clips was -2.7 dB or lower, of real
#: ones -1.6 to -2.5 dB, of spoken ones 0.0 dB or higher.
MAX_HNR_DB = -1.0
#: How much quieter than the satellite's usual speech counts as whispering, by default.
#: Whispered clips sat 12 to 24 dB under their spoken level; a voice from across the room is
#: also quieter, which is why level alone never decides. Real whispers sat 8 to 20 dB under, one of
#: them 7.9 dB, so the default leaves room below 8.
DEFAULT_LEVEL_DROP_DB = 6.0
#: Normal commands a satellite needs before its usual level is trusted; until then voicing and
#: tilt decide alone.
BASELINE_MIN_RUNS = 3
#: Each normal command moves the usual level this much of the way (an exponential average).
BASELINE_ALPHA = 0.2


@dataclass(frozen=True)
class Features:
    """What a command sounded like, as numbers only."""

    level_db: float  # median speech frame level, dBFS
    voiced_ratio: float  # share of speech frames with a pitch
    hnr_db: float  # median harmonics-to-noise ratio of the speech frames
    tilt_db: float  # energy at 2-5 kHz over 100 Hz-1 kHz
    speech_frames: int

    def public(self) -> dict[str, Any]:
        return {"level_db": round(self.level_db, 1), "voiced": round(self.voiced_ratio, 2),
                "hnr_db": round(self.hnr_db, 1), "tilt_db": round(self.tilt_db, 1),
                "speech_s": round(self.speech_frames * FRAME / RATE, 2)}


def _samples(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64)


def _pitch_strength(window: np.ndarray) -> float:
    """The best normalised autocorrelation at a pitch lag (0: none, 1: perfectly periodic)."""
    x = window - window.mean()
    n = len(x)
    if n <= MAX_LAG + 1 or not np.any(x):
        return 0.0
    size = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(x, size)
    ac = np.fft.irfft(spec * np.conj(spec), size)[: MAX_LAG + 1]
    energy = np.concatenate(([0.0], np.cumsum(x * x)))
    lags = np.arange(MIN_LAG, MAX_LAG + 1)
    head = energy[n - lags]  # x[0 : n-lag]
    tail = energy[n] - energy[lags]  # x[lag : n]
    denom = np.sqrt(head * tail)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(denom > 0, ac[lags] / denom, 0.0)
    return float(max(0.0, r.max()))


def measure(pcm: bytes, rate: int = RATE) -> Features | None:
    """The command's whisper features, or None when there is too little speech to say.

    ``pcm`` is 16-bit mono little-endian at ``rate`` (the satellites send 16 kHz).
    """
    if rate != RATE:
        raise ValueError(f"whisper features are measured at {RATE} Hz, not {rate}")
    x = _samples(pcm)
    frames = len(x) // FRAME
    if frames < MIN_SPEECH_FRAMES:
        return None
    blocks = x[: frames * FRAME].reshape(frames, FRAME)
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    floor = max(float(np.percentile(rms, 10)), 1.0)
    loud = float(np.percentile(rms, 95))
    gate = max(floor * OVER_FLOOR, loud * 10 ** (-SPEECH_RANGE_DB / 20))
    idx = np.nonzero(rms >= gate)[0]
    if len(idx) < MIN_SPEECH_FRAMES:
        return None
    level = float(np.median(rms[idx]))
    strengths = []
    pad = (WINDOW - FRAME) // 2
    for i in idx:
        start = max(0, i * FRAME - pad)
        strengths.append(_pitch_strength(x[start:start + WINDOW]))
    r = np.clip(np.array(strengths), 1e-4, 1 - 1e-4)
    voiced = float(np.mean(r >= VOICED_R))
    hnr = float(np.median(10 * np.log10(r / (1 - r))))
    spec = np.abs(np.fft.rfft(blocks[idx] * np.hanning(FRAME), axis=1)) ** 2
    freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
    low = spec[:, (freqs >= 100) & (freqs < 1000)].sum()
    high = spec[:, (freqs >= 2000) & (freqs < 5000)].sum()
    tilt = 10 * math.log10(max(high, 1e-9) / max(low, 1e-9))
    return Features(level_db=20 * math.log10(level / 32768), voiced_ratio=voiced, hnr_db=hnr, tilt_db=tilt,
                    speech_frames=len(idx))


def decide(features: Features | None, usual_db: float | None, drop_db: float = DEFAULT_LEVEL_DROP_DB) -> bool:
    """Whispered: pitchless, and (once the satellite's usual level is known) quieter by ``drop_db``."""
    if features is None:
        return False
    if features.voiced_ratio > MAX_VOICED_RATIO or features.hnr_db > MAX_HNR_DB:
        return False
    return usual_db is None or features.level_db <= usual_db - drop_db


@dataclass
class LevelBaseline:
    """A satellite's usual speech level: an exponential average of its normal commands, in dBFS."""

    level_db: float | None = None
    runs: int = 0

    @property
    def usual_db(self) -> float | None:
        return self.level_db if self.runs >= BASELINE_MIN_RUNS else None

    def add(self, level_db: float) -> None:
        if self.level_db is None:
            self.level_db = level_db
        else:
            self.level_db += BASELINE_ALPHA * (level_db - self.level_db)
        self.runs += 1

    def to_dict(self) -> dict[str, Any]:
        return {"level_db": None if self.level_db is None else round(self.level_db, 2), "runs": self.runs}

    @classmethod
    def from_dict(cls, raw: Any) -> LevelBaseline:
        if not isinstance(raw, dict):
            return cls()
        level = raw.get("level_db")
        runs = raw.get("runs")
        ok = isinstance(level, (int, float)) and math.isfinite(level) and isinstance(runs, int) and runs >= 0
        return cls(float(level), runs) if ok else cls()


#: Where the hub keeps whisper mode's settings (its store), edited at ``PUT /hub/voice/settings``.
SETTINGS_KV = "voice_whisper_settings"


@dataclass(frozen=True)
class WhisperSettings:
    """Whisper mode, a HomeHub voice setting (on by default).

    ``volume`` is the satellite's volume (0 to 1) for a whispered command's reply, holding phrase and
    hold music; the previous volume comes back when the reply has played. ``level_drop_db`` is how
    much quieter than the satellite's usual speech a pitchless command must be to count as whispered.
    """

    enabled: bool = True
    volume: float = 0.22
    level_drop_db: float = DEFAULT_LEVEL_DROP_DB

    def public(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "volume": self.volume, "level_drop_db": self.level_drop_db}


#: The bounds a setting must be inside; outside them a whisper is either never heard or always.
LEVEL_DROP_RANGE = (3.0, 30.0)


def validate_settings(body: Any, current: WhisperSettings) -> WhisperSettings:
    """``current`` with the fields ``body`` names; ValueError naming the field that is wrong."""
    if not isinstance(body, dict):
        raise ValueError("The body must be a JSON object such as {\"enabled\": true, \"volume\": 0.2}")
    unknown = sorted(set(body) - {"enabled", "volume", "level_drop_db"})
    if unknown:
        raise ValueError(f"Unknown whisper setting(s): {', '.join(unknown)}")
    enabled, volume, drop = current.enabled, current.volume, current.level_drop_db
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ValueError("enabled must be true or false")
        enabled = body["enabled"]
    if "volume" in body:
        v = body["volume"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 < float(v) <= 1.0:
            raise ValueError("volume must be a number above 0 and at most 1 (the satellite's volume)")
        volume = round(float(v), 2)
    if "level_drop_db" in body:
        d = body["level_drop_db"]
        lo, hi = LEVEL_DROP_RANGE
        if isinstance(d, bool) or not isinstance(d, (int, float)) or not lo <= float(d) <= hi:
            raise ValueError(f"level_drop_db must be a number from {lo:g} to {hi:g} (dB)")
        drop = round(float(d), 1)
    return WhisperSettings(enabled=enabled, volume=volume, level_drop_db=drop)


def load_settings(store: Any, default_volume: float = 0.22) -> WhisperSettings:
    """The saved settings; anything missing or broken in the store is its default."""
    base = WhisperSettings(volume=default_volume)
    raw = store.get_kv(SETTINGS_KV) if store is not None else None
    if not isinstance(raw, dict):
        return base
    try:
        return validate_settings(raw, base)
    except ValueError:
        return base


__all__ = ["DEFAULT_LEVEL_DROP_DB", "Features", "LevelBaseline", "WhisperSettings", "decide", "load_settings", "measure",
           "validate_settings"]
