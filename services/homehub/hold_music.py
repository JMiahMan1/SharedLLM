"""Which hold music a voice satellite plays while Jarvis thinks, and the clips behind it.

A HomeHub setting (Devices > Setup > Voice hold music, ``/hub/voice/hold-music``), kept in the hub's
store under ``voice_hold_music``:

| ``source`` | What plays |
|---|---|
| ``gameshow`` (default) | ``data/hold_music_gameshow.wav``: an original game-show "thinking" loop composed once by
  MusicGen (``scripts/compose_hold_music.py``; its prompt and seed are in the ``.json`` beside it) |
| ``builtin`` | the soft I-vi-IV-V made in code (``voice_pipeline.hold_music_loop``), also the fallback |
| ``upload`` | a clip an admin uploaded (wav, mp3, ogg or m4a; at most 5 MB and 60 s), decoded once when it
  arrives and kept as a mono WAV beside the hub's database (``hub.data_dir``), never served back |

The setting also holds ``lines``: the playful lines said before the music (empty means the defaults).

Whatever the source, the clip is made the stream's own rate once (mono, 16-bit), brought to the built-in tune's
loudness (``TARGET_DBFS`` RMS, not peak), its end crossfaded into its start so it loops with no seam, and cached.
A clip that is missing or cannot be decoded is never silence: the built-in tune plays and the reason is logged
once.
"""
from __future__ import annotations

import io
import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from services.homehub.core import HubError

log = logging.getLogger("homehub.voice.hold_music")

KV_KEY = "voice_hold_music"
SOURCES = ("gameshow", "builtin", "upload")
SOURCE_LABELS = {"gameshow": "Game-show thinking", "builtin": "Built-in tune", "upload": "Upload your own"}
DEFAULT_SOURCE = "gameshow"
#: The shipped game-show clip and its recipe (prompt, seed), made by scripts/compose_hold_music.py.
GAMESHOW_CLIP = Path(__file__).resolve().parent / "data" / "hold_music_gameshow.wav"
#: An uploaded clip, decoded to mono 16-bit WAV, beside the hub's database.
UPLOAD_FILE = "voice_hold_music.wav"
UPLOAD_TYPES = ("wav", "mp3", "ogg", "m4a")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CLIP_S = 60.0
MIN_CLIP_S = 2.0
#: Uploads are kept at most at this rate (they are resampled to the stream's rate when played).
MAX_STORED_RATE = 48000
#: The loop's end is blended into its start over this long.
LOOP_CROSSFADE_S = 0.5
#: The music's loudness, RMS: the built-in tune's, well under speech (Kokoro speaks at about -18 to -14 dBFS).
TARGET_DBFS = -24.0
MAX_LINES = 20
MAX_LINE_CHARS = 160
FFMPEG_TIMEOUT_S = 60.0


class ClipError(HubError):
    """An upload that cannot be used, with the HTTP status that says why (400, 413 or 415)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# -- the setting ----------------------------------------------------------------
def _store(hub: Any) -> Any:
    return getattr(hub, "store", None)


def _upload_path(hub: Any) -> Path | None:
    data_dir = getattr(hub, "data_dir", None)
    return Path(data_dir) / UPLOAD_FILE if data_dir else None


def _doc(hub: Any) -> dict[str, Any]:
    store = _store(hub)
    if store is None:
        return {}
    try:
        doc = store.get_kv(KV_KEY) or {}
    except Exception as exc:  # the music is a courtesy: a store that cannot be read means the defaults
        _warn_once(f"store:{exc}", "the hold music setting could not be read, so the defaults apply: %s", exc)
        return {}
    return doc if isinstance(doc, dict) else {}


def settings(hub: Any) -> dict[str, Any]:
    """The setting as the settings page shows it."""
    doc = _doc(hub)
    source = doc.get("source") if doc.get("source") in SOURCES else DEFAULT_SOURCE
    lines = [str(x) for x in doc.get("lines") or [] if str(x).strip()]
    upload = doc.get("upload") if isinstance(doc.get("upload"), dict) else None
    path = _upload_path(hub)
    if upload is not None and (path is None or not path.is_file()):
        upload = {**upload, "missing": True}
    return {"source": source, "sources": [{"id": s, "label": SOURCE_LABELS[s]} for s in SOURCES],
            "lines": lines, "upload": upload,
            "limits": {"types": list(UPLOAD_TYPES), "max_bytes": MAX_UPLOAD_BYTES, "max_seconds": MAX_CLIP_S,
                       "max_lines": MAX_LINES, "max_line_chars": MAX_LINE_CHARS}}


def _clean_lines(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ClipError("lines must be a list of sentences")
    lines = [" ".join(x.split()) for x in raw if x.strip()]
    if len(lines) > MAX_LINES:
        raise ClipError(f"At most {MAX_LINES} hold lines")
    long = [x for x in lines if len(x) > MAX_LINE_CHARS]
    if long:
        raise ClipError(f"A hold line must be at most {MAX_LINE_CHARS} characters: {long[0][:40]!r}...")
    return lines


def save_settings(hub: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Change ``source`` and/or ``lines``; "upload" needs a clip uploaded first."""
    store = _store(hub)
    if store is None:
        raise ClipError("The hub has no store to keep the setting in", 503)
    doc = dict(_doc(hub))
    if "source" in body:
        source = body["source"]
        if source not in SOURCES:
            raise ClipError(f"source must be one of {', '.join(SOURCES)}")
        if source == "upload" and not settings(hub)["upload"]:
            raise ClipError("Upload a clip first")
        doc["source"] = source
    if "lines" in body:
        doc["lines"] = _clean_lines(body["lines"])
    store.set_kv(KV_KEY, doc)
    _forget()
    return settings(hub)


def hold_lines(hub: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """The lines said before the music: the setting's, else ``fallback`` (HOMEHUB_VOICE_HOLD_LINES or the defaults)."""
    lines = tuple(settings(hub)["lines"]) if hub is not None else ()
    return lines or fallback


# -- decoding -------------------------------------------------------------------
def sniff_type(data: bytes, filename: str = "") -> str:
    """The upload's type from its first bytes, checked against its name; raises a 415 for anything else."""
    head = data[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        kind = "wav"
    elif head[:4] == b"OggS":
        kind = "ogg"
    elif head[4:8] == b"ftyp":
        kind = "m4a"
    elif head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        kind = "mp3"
    else:
        raise ClipError(f"Use a {', '.join(UPLOAD_TYPES[:-1])} or {UPLOAD_TYPES[-1]} file", 415)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext and ext not in UPLOAD_TYPES:
        raise ClipError(f"Use a {', '.join(UPLOAD_TYPES[:-1])} or {UPLOAD_TYPES[-1]} file", 415)
    if ext and ext != kind and {ext, kind} != {"m4a", "mp4"}:
        raise ClipError(f"The file is named .{ext} but holds {kind} audio", 415)
    return kind


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """A WAV (8, 16, 24 or 32-bit PCM, any channels) as mono float samples in -1..1 and its rate."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ClipError(f"The WAV file could not be read: {exc}") from exc
    if not frames or rate <= 0:
        raise ClipError("The WAV file has no audio")
    if width == 1:
        x = (np.frombuffer(frames, np.uint8).astype(np.float64) - 128) / 128
    elif width == 2:
        x = np.frombuffer(frames, "<i2").astype(np.float64) / 32768
    elif width == 3:
        b = np.frombuffer(frames[: len(frames) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float64) / (1 << 23)
    elif width == 4:
        x = np.frombuffer(frames, "<i4").astype(np.float64) / 2 ** 31
    else:
        raise ClipError(f"Unsupported WAV sample width: {width} bytes")
    x = x[: len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
    return x, rate


def decode(data: bytes, kind: str) -> tuple[np.ndarray, int]:
    """Any upload type as mono float samples and its rate: WAV here, the rest through ffmpeg (in the hub's image)."""
    if kind == "wav":
        try:
            return read_wav(data)
        except ClipError:
            pass  # a WAV this reader does not know (float, ADPCM): ffmpeg reads those
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ClipError(f"This hub cannot decode {kind} files (ffmpeg is not installed); upload a WAV")
    with tempfile.TemporaryDirectory(prefix="hold-music-") as tmp:
        src, out = Path(tmp) / f"in.{kind}", Path(tmp) / "out.wav"
        src.write_bytes(data)  # a file, not a pipe: an m4a's index can be at its end
        try:
            proc = subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
                 "-c:a", "pcm_s16le", "-t", str(MAX_CLIP_S + 1), str(out)],
                capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ClipError("Decoding the file took too long") from exc
        if proc.returncode != 0 or not out.is_file():
            why = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["no audio"]
            raise ClipError(f"The file could not be decoded: {why[0][:200]}")
        return read_wav(out.read_bytes())


# -- shaping --------------------------------------------------------------------
def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """A windowed-sinc low-pass (``cutoff`` as a fraction of the sample rate), so a downsample does not alias."""
    taps = 63
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same")


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(x) == 0:
        return x
    if dst < src:
        x = _lowpass(x, 0.5 * dst / src * 0.95)
    n = max(1, round(len(x) * dst / src))
    return np.interp(np.arange(n) * (src / dst), np.arange(len(x)), x)


def make_loop(x: np.ndarray, rate: int, crossfade_s: float = LOOP_CROSSFADE_S) -> np.ndarray:
    """Fold the last ``crossfade_s`` into the first, so played back to back there is no seam.

    The tail fades out over the head fading in (equal power), and is then dropped: the loop's last sample is the
    one that came just before the tail's first, which the blended head now starts with.
    """
    n = int(rate * crossfade_s)
    if n <= 0 or len(x) < 3 * n:
        return x
    body = x[: len(x) - n].copy()
    tail = x[len(x) - n:]
    t = (np.arange(n) + 0.5) / n
    body[:n] = body[:n] * np.sin(0.5 * np.pi * t) + tail * np.cos(0.5 * np.pi * t)
    return body


#: A loop is cut to whole bars of this many beats, so the beat does not stumble at the seam.
BEATS_PER_BAR = 4
#: Tempo search range, and how clearly the tempo must stand out before the clip is cut to bars.
TEMPO_MIN_BPM = 70.0
TEMPO_MAX_BPM = 200.0
TEMPO_MIN_CONFIDENCE = 3.5
#: Onsets must stand out from the clip's level, or a steady tone or noise would be given a "beat".
ONSET_MIN_VARIATION = 0.1
_ONSET_HOP = 256


def _onsets(x: np.ndarray) -> np.ndarray:
    """Onset strength per hop: the positive rise of the short-term level."""
    frames = len(x) // _ONSET_HOP
    if frames < 4:
        return np.zeros(0)
    level = np.sqrt(np.mean(x[: frames * _ONSET_HOP].reshape(frames, _ONSET_HOP) ** 2, axis=1))
    rise = np.maximum(0.0, np.diff(level, prepend=level[0]))
    return rise - rise.mean()


def _beat_scores(onsets: np.ndarray, period: float, phases: np.ndarray) -> np.ndarray:
    """How strongly onsets land on a beat grid of ``period`` hops, for every phase at once.

    Linear interpolation between hops; only the beats every phase has inside the clip are counted, so
    the scores compare like with like. One numpy pass per period keeps a clip's first load fast.
    """
    beats = int((len(onsets) - 1 - phases.max()) // period)
    if beats < 4:
        return np.full(len(phases), -np.inf)
    t = phases[:, None] + period * np.arange(beats)[None, :]
    i = t.astype(int)
    f = t - i
    return np.mean(onsets[i] * (1 - f) + onsets[i + 1] * f, axis=1)


def estimate_beat(x: np.ndarray, rate: int) -> tuple[float, float, float] | None:
    """(bpm, first beat in samples, confidence), or None when the clip has no clear steady beat.

    A coarse tempo comes from the onset autocorrelation; it is then refined, with the beat phase, by
    fitting a beat grid to the onsets over the whole clip, since a loop of 48 beats shows even a
    fraction of a BPM as a stumble at the seam.
    """
    on = _onsets(x)
    if len(on) < 16:
        return None
    level = float(np.sqrt(np.mean(x ** 2)))
    if level <= 1e-9 or float(on.std()) / level < ONSET_MIN_VARIATION:
        return None
    hop_s = _ONSET_HOP / rate
    lo = max(1, int(60 / TEMPO_MAX_BPM / hop_s))
    hi = min(len(on) - 1, int(60 / TEMPO_MIN_BPM / hop_s) + 1)
    if hi <= lo + 1:
        return None
    ac = np.correlate(on, on, "full")[len(on) - 1:]
    band = ac[lo:hi]
    if band.size == 0 or np.mean(np.abs(band)) <= 0:
        return None
    confidence = float(band.max() / (np.mean(np.abs(band)) + 1e-12))
    if confidence < TEMPO_MIN_CONFIDENCE:
        return None
    lag = lo + int(np.argmax(band))
    best = (float("-inf"), float(lag), 0.0)
    for period in np.linspace(lag * 0.97, lag * 1.03, 121):
        phases = np.arange(0.0, period, 0.5)
        scores = _beat_scores(on, float(period), phases)
        k = int(np.argmax(scores))
        if scores[k] > best[0]:
            best = (float(scores[k]), float(period), float(phases[k]))
    _, period, phase = best
    return 60.0 / (period * hop_s), phase * _ONSET_HOP, confidence


def bar_aligned(x: np.ndarray, rate: int, crossfade_s: float = LOOP_CROSSFADE_S) -> np.ndarray:
    """Cut ``x`` so that, after ``make_loop`` folds the crossfade away, the loop is whole bars long.

    It starts on the first beat and keeps as many whole bars as fit, plus the crossfade tail. A clip
    with no clear steady beat (speech, ambient pads) is returned unchanged.
    """
    beat = estimate_beat(x, rate)
    if beat is None:
        return x
    bpm, start, _ = beat
    bar = BEATS_PER_BAR * 60.0 * rate / bpm
    n = int(rate * crossfade_s)
    s = round(start)
    bars = int((len(x) - s - n) // bar)
    if bars < 2:
        return x
    length = round(bars * bar)
    return x[s: s + length + n]


def _trim_silence(x: np.ndarray, rate: int, floor_db: float = -50.0) -> np.ndarray:
    """Leading and trailing near-silence off, so the loop is not a gap every time round."""
    win = max(1, rate // 50)
    frames = len(x) // win
    if frames < 2:
        return x
    rms = np.sqrt(np.mean(x[: frames * win].reshape(frames, win) ** 2, axis=1))
    loud = np.nonzero(rms > 10 ** (floor_db / 20))[0]
    if len(loud) == 0:
        return x
    return x[loud[0] * win: (loud[-1] + 1) * win]


def normalise(x: np.ndarray, dbfs: float = TARGET_DBFS) -> np.ndarray:
    """RMS to ``dbfs`` (not peak); the rare peak past full scale is rounded off softly, not clipped."""
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms <= 1e-9:
        raise ClipError("The clip is silent")
    y = x * (10 ** (dbfs / 20) / rms)
    knee = 0.8
    over = np.abs(y) > knee
    if over.any():
        y = y.copy()
        y[over] = np.sign(y[over]) * (knee + (1 - knee) * np.tanh((np.abs(y[over]) - knee) / (1 - knee)))
    return y


def prepare(x: np.ndarray, rate: int, out_rate: int, dbfs: float = TARGET_DBFS, loop: bool = True) -> bytes:
    """Mono float samples at ``rate`` -> 16-bit little-endian PCM at ``out_rate``, at ``dbfs`` RMS.

    With ``loop`` (how every clip is played) it is cut to whole bars and its end folded into its start.
    ``loop=False`` is for a stored clip: it is looped once, when it is loaded, never twice.
    """
    y = resample(_trim_silence(x, rate), rate, out_rate)
    if loop:
        y = make_loop(bar_aligned(y, out_rate), out_rate)
    y = normalise(y, dbfs)
    return np.clip(np.round(y * 32767), -32768, 32767).astype("<i2").tobytes()


def _wav_bytes(x: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())
    return buf.getvalue()


# -- uploads --------------------------------------------------------------------
def check_upload(data: bytes, filename: str = "") -> tuple[np.ndarray, int, str]:
    """Validate and decode an upload: (mono samples, rate, type). Raises ClipError with 400, 413 or 415."""
    if not data:
        raise ClipError("The file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ClipError(f"The file is {len(data) / 1048576:.1f} MB; the most is {MAX_UPLOAD_BYTES // 1048576} MB", 413)
    kind = sniff_type(data, filename)
    x, rate = decode(data, kind)
    seconds = len(x) / rate
    if seconds > MAX_CLIP_S:
        raise ClipError(f"The clip is longer than {MAX_CLIP_S:g} s; trim it and upload it again")
    if seconds < MIN_CLIP_S:
        raise ClipError(f"The clip is shorter than {MIN_CLIP_S:g} s")
    if float(np.sqrt(np.mean(x ** 2))) <= 1e-5:
        raise ClipError("The clip is silent")
    return x, rate, kind


def store_upload(hub: Any, data: bytes, filename: str = "") -> dict[str, Any]:
    """Keep an uploaded clip (decoded once, as a mono WAV in the hub's data) and make it the hold music."""
    store, path = _store(hub), _upload_path(hub)
    if store is None or path is None:
        raise ClipError("The hub has no data folder to keep the clip in", 503)
    x, rate, kind = check_upload(data, filename)
    if rate > MAX_STORED_RATE:
        x, rate = resample(x, rate, MAX_STORED_RATE), MAX_STORED_RATE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_wav_bytes(x, rate))
    os.replace(tmp, path)
    name = os.path.basename(filename.replace("\\", "/"))[:120] or f"clip.{kind}"
    doc = dict(_doc(hub))
    doc["upload"] = {"name": name, "type": kind, "seconds": round(len(x) / rate, 1), "bytes": len(data),
                     "uploaded_at": time.time()}
    doc["source"] = "upload"
    store.set_kv(KV_KEY, doc)
    _forget()
    log.info("hold music: an uploaded clip (%s, %.1f s) is now the hold music", kind, len(x) / rate)
    return settings(hub)


def delete_upload(hub: Any) -> dict[str, Any]:
    """Remove the uploaded clip; if it was playing, the game-show clip plays instead."""
    store, path = _store(hub), _upload_path(hub)
    if store is None:
        raise ClipError("The hub has no store to keep the setting in", 503)
    if path is not None:
        path.unlink(missing_ok=True)
    doc = dict(_doc(hub))
    doc.pop("upload", None)
    if doc.get("source") == "upload":
        doc["source"] = DEFAULT_SOURCE
    store.set_kv(KV_KEY, doc)
    _forget()
    return settings(hub)


# -- what plays -----------------------------------------------------------------
_CACHE: dict[tuple[str, str, int], bytes] = {}
_WARNED: set[str] = set()


def _forget() -> None:
    _CACHE.clear()
    _WARNED.clear()


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    log.warning(msg, *args)


def _clip_file(hub: Any, source: str) -> Path | None:
    return GAMESHOW_CLIP if source == "gameshow" else _upload_path(hub) if source == "upload" else None


def loop_for(hub: Any, rate: int, builtin: Callable[[int], bytes]) -> tuple[bytes, str]:
    """The hold music for a stream at ``rate``: ``(pcm, source)``, the built-in tune when the clip cannot play."""
    source = settings(hub)["source"] if hub is not None else DEFAULT_SOURCE
    path = _clip_file(hub, source)
    if path is None:
        return builtin(rate), "builtin"
    try:
        version = str(path.stat().st_mtime_ns)
    except OSError:
        _warn_once(f"missing:{source}:{path}", "hold music: the %s clip is missing (%s), so the built-in tune plays",
                   SOURCE_LABELS[source], path)
        return builtin(rate), "builtin"
    key = (str(path), version, rate)
    pcm = _CACHE.get(key)
    if pcm is None:
        try:
            x, src_rate = read_wav(path.read_bytes())
            pcm = prepare(x, src_rate, rate)
        except (ClipError, OSError, ValueError) as exc:
            _warn_once(f"bad:{key}", "hold music: the %s clip could not be decoded (%s), so the built-in tune plays",
                       SOURCE_LABELS[source], exc)
            return builtin(rate), "builtin"
        _CACHE[key] = pcm
    return pcm, source


def rms_dbfs(pcm: bytes) -> float:
    x = np.frombuffer(pcm, "<i2").astype(np.float64)
    return 20 * math.log10(max(1e-9, float(np.sqrt(np.mean(x ** 2)))) / 32767)
