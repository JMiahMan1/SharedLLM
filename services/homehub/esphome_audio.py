"""Audio for ESPHome media players in the format each one asks for (HA's esphome ``ffmpeg_proxy``).

An ESPHome media player lists the formats it wants in ``MediaPlayerInfo.supported_formats``: a codec
(``flac``, ``mp3``, ``wav``, ``opus``), a sample rate, a channel count, a sample width and a purpose
(``0`` default playback, ``1`` announcements). HA never hands such a node the original URL: it hands
it ``/api/esphome/ffmpeg_proxy/<device>/<id>.<ext>``, where ffmpeg turns the source into exactly that
format. The node then only decodes; it never resamples. A small node fed something else (a 22050 Hz
WAV from a Piper voice, a 44.1 kHz MP3) has to resample in software while it plays, and an ESP32-S3
with 2 MB of PSRAM can stall long enough for its watchdog to reset it.

The hub does the same:

- ``choose_format(formats, announcement)`` follows HA: the first ``DEFAULT`` format, or for an
  announcement the first ``ANNOUNCEMENT`` format (which wins). A node that advertises none keeps the
  direct URL, as does a codec ffmpeg is not taught here.
- ``PROXY.create(device, source, fmt)`` registers an unguessable token for one source and returns the
  URL the node fetches. The token is the credential (like ``/api/tts_proxy/``), it expires after
  ``TTL_S`` (``CLIP_TTL_S`` for a spoken clip), and a device keeps at most ``MAX_PER_DEVICE`` of them.
  The source URL never appears in the proxy URL or in the hub's log, so credentials in it stay put.
- A spoken clip of the hub's own (``/api/tts_proxy/<token>.wav``) is converted once, from the bytes in
  memory, and the result is kept with the token: the node fetches a reply twice, and the 22050 Hz
  original stays as it is for every other kind of player.
- Any other ``http(s)`` source is streamed through ffmpeg as the node reads it. Fetching a token again
  stops the conversion still running for it (as HA does), a node that hangs up stops its ffmpeg, and
  ``stop_device`` stops every conversion of a node (stop, turn off, disconnect, unload). At most
  ``MAX_PROCS`` ffmpeg processes run at once; a request that cannot get one within
  ``SLOT_WAIT_S`` is answered 503.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("homehub.esphome.audio")

PURPOSE_DEFAULT = 0
PURPOSE_ANNOUNCEMENT = 1
PURPOSE_NAMES = {PURPOSE_DEFAULT: "default", PURPOSE_ANNOUNCEMENT: "announcement"}

#: Codec as the node names it -> (ffmpeg muxer, encoder or None for the muxer's default, content type).
CODECS: dict[str, tuple[str, str | None, str]] = {
    "flac": ("flac", None, "audio/flac"),
    "mp3": ("mp3", None, "audio/mpeg"),
    "wav": ("wav", None, "audio/wav"),
    "opus": ("ogg", "libopus", "audio/ogg"),
}
#: Sample width in bytes -> ffmpeg sample format, for the codecs that store samples as such.
SAMPLE_FMTS = {1: "u8", 2: "s16", 4: "s32"}
WAV_CODECS = {1: "pcm_u8", 2: "pcm_s16le", 4: "pcm_s32le"}

PATH_PREFIX = "/api/esphome/ffmpeg_proxy/"
_TTS_PATH = re.compile(r"/api/tts_proxy/([A-Za-z0-9_-]+)\.wav$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


#: Read once at start, like the hub's other settings.
FFMPEG = os.getenv("HOMEHUB_FFMPEG") or "ffmpeg"
MAX_PROCS = _int_env("HOMEHUB_ESPHOME_FFMPEG_MAX", 4)
TTL_S = float(_int_env("HOMEHUB_ESPHOME_PROXY_TTL_S", 6 * 3600))
CLIP_TTL_S = 600.0  # a spoken clip lives as long as the hub keeps the clip itself (tts.KEEP_S)
MAX_PER_DEVICE = 8
SLOT_WAIT_S = 5.0
CLIP_TIMEOUT_S = 60.0
CHUNK = 16384


@dataclass(frozen=True)
class AudioFormat:
    """One entry of a node's ``supported_formats``."""

    format: str
    sample_rate: int = 0
    num_channels: int = 0
    sample_bytes: int = 0
    purpose: int = PURPOSE_DEFAULT

    @classmethod
    def from_info(cls, f: Any) -> AudioFormat:
        purpose = getattr(f, "purpose", None)
        return cls(format=str(getattr(f, "format", "") or "").lower(),
                   sample_rate=int(getattr(f, "sample_rate", 0) or 0),
                   num_channels=int(getattr(f, "num_channels", 0) or 0),
                   sample_bytes=int(getattr(f, "sample_bytes", 0) or 0),
                   purpose=int(purpose) if purpose is not None else PURPOSE_DEFAULT)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AudioFormat:
        """A format as ``public()`` writes it, or with ``purpose`` as the node's number."""
        purpose = d.get("purpose", PURPOSE_DEFAULT)
        if isinstance(purpose, str):
            names = {v: k for k, v in PURPOSE_NAMES.items()}
            purpose = names[purpose] if purpose in names else int(purpose)
        return cls(format=str(d.get("format") or "").lower(), sample_rate=int(d.get("sample_rate") or 0),
                   num_channels=int(d.get("num_channels") or 0), sample_bytes=int(d.get("sample_bytes") or 0),
                   purpose=int(purpose if purpose is not None else PURPOSE_DEFAULT))

    def public(self) -> dict[str, Any]:
        return {"format": self.format, "sample_rate": self.sample_rate, "num_channels": self.num_channels,
                "sample_bytes": self.sample_bytes, "purpose": PURPOSE_NAMES.get(self.purpose, str(self.purpose))}

    @property
    def content_type(self) -> str:
        return CODECS[self.format][2]

    @property
    def extension(self) -> str:
        return self.format

    def ffmpeg_output_args(self) -> list[str]:
        """HA's arguments: container, rate, channels, sample width; no metadata, no cover art."""
        muxer, encoder, _ = CODECS[self.format]
        args = ["-vn", "-map_metadata", "-1"]
        if self.format == "wav":
            args += ["-c:a", WAV_CODECS.get(self.sample_bytes, "pcm_s16le")]
        elif encoder:
            args += ["-c:a", encoder]
        if self.sample_rate:
            args += ["-ar", str(self.sample_rate)]
        if self.num_channels:
            args += ["-ac", str(self.num_channels)]
        if self.format == "flac" and self.sample_bytes in SAMPLE_FMTS:
            args += ["-sample_fmt", SAMPLE_FMTS[self.sample_bytes]]
        return [*args, "-f", muxer]


def supported_formats(info: Any) -> list[AudioFormat]:
    """A media player info's formats, in the node's order."""
    return [AudioFormat.from_info(f) for f in (getattr(info, "supported_formats", None) or [])]


def choose_format(formats: list[AudioFormat], announcement: bool) -> AudioFormat | None:
    """HA's choice: the first default format; for an announcement the first announcement format wins."""
    chosen: AudioFormat | None = None
    for f in formats:
        if chosen is None and f.purpose == PURPOSE_DEFAULT:
            chosen = f
        if announcement and f.purpose == PURPOSE_ANNOUNCEMENT:
            return f
    return chosen


def _redact(url: str) -> str:
    """A source for the log: scheme, host and path only (a query or user info may be a credential)."""
    try:
        p = urlsplit(url)
    except ValueError:
        return "<unparsable url>"
    return f"{p.scheme}://{p.hostname or ''}{p.path}"


@dataclass
class Conversion:
    token: str
    device: str
    fmt: AudioFormat
    expires: float
    source_url: str | None = None
    source_bytes: bytes | None = None
    converted: bytes | None = None
    proc: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProxyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class FFmpegProxy:
    """The tokens handed to nodes and the ffmpeg processes serving them."""

    def __init__(self, ffmpeg: str = FFMPEG, max_procs: int = MAX_PROCS) -> None:
        self.ffmpeg = ffmpeg
        self.max_procs = max_procs
        self._conversions: dict[str, Conversion] = {}
        self._slots: asyncio.Semaphore | None = None
        self._running: set[Any] = set()
        self._have_ffmpeg: bool | None = None

    # -- tokens -----------------------------------------------------------------------
    def available(self) -> bool:
        if self._have_ffmpeg is None:
            self._have_ffmpeg = shutil.which(self.ffmpeg) is not None
            if not self._have_ffmpeg:
                log.warning("ffmpeg (%s) is not installed: ESPHome media players get the original audio URLs",
                            self.ffmpeg)
        return self._have_ffmpeg

    def _prune(self) -> None:
        now = time.monotonic()
        # A token past its time takes no new fetches; a conversion still streaming keeps it until it ends.
        for t in [t for t, c in self._conversions.items() if c.expires < now and c.proc is None]:
            self._conversions.pop(t, None)

    def create(self, device: str, source: str, fmt: AudioFormat, base_url: str) -> str:
        """Register ``source`` for ``device`` in ``fmt``; returns the URL the node fetches."""
        if fmt.format not in CODECS:
            raise ValueError(f"no ffmpeg conversion for {fmt.format!r}")
        self._prune()
        token = secrets.token_urlsafe(24)
        conv = Conversion(token=token, device=device, fmt=fmt, expires=time.monotonic() + TTL_S)
        clip = _hub_clip(source)
        if clip is not None:
            conv.source_bytes, conv.expires = clip, time.monotonic() + CLIP_TTL_S
        else:
            conv.source_url = source
        # Oldest first (insertion order), idle ones before one still playing.
        mine = sorted((c for c in self._conversions.values() if c.device == device), key=lambda c: c.proc is not None)
        for old in mine[: max(0, len(mine) - MAX_PER_DEVICE + 1)]:
            self._drop(old)
        self._conversions[token] = conv
        log.debug("ffmpeg proxy %s for %s: %s -> %s", token[:6], device,
                  "spoken clip" if clip is not None else _redact(source), fmt.public())
        return f"{base_url}{PATH_PREFIX}{device}/{token}.{fmt.extension}"

    def lookup(self, device: str, name: str) -> Conversion | None:
        self._prune()
        token, _, ext = name.rpartition(".")
        if not _TOKEN.match(token or ""):
            return None
        conv = self._conversions.get(token)
        if conv is None or conv.device != device or ext != conv.fmt.extension or conv.expires < time.monotonic():
            return None
        return conv

    def _drop(self, conv: Conversion) -> None:
        self._conversions.pop(conv.token, None)
        _kill(conv.proc)

    def stop_device(self, device: str) -> None:
        """Stop every conversion still running for ``device`` (its tokens stay valid until they expire)."""
        for conv in self._conversions.values():
            if conv.device == device and conv.proc is not None:
                _kill(conv.proc)

    def running(self) -> int:
        return len(self._running)

    def reset(self) -> None:
        for conv in list(self._conversions.values()):
            self._drop(conv)
        self._conversions.clear()
        self._slots = None

    # -- ffmpeg -------------------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def _slot(self) -> AsyncIterator[None]:
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.max_procs)
        try:
            await asyncio.wait_for(self._slots.acquire(), SLOT_WAIT_S)
        except TimeoutError as exc:
            raise ProxyError(503, f"too many audio conversions at once ({self.max_procs})") from exc
        try:
            yield
        finally:
            self._slots.release()

    async def _spawn(self, args: list[str], *, stdin: Any) -> Any:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", *args,
                stdin=stdin, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError as exc:
            raise ProxyError(503, "ffmpeg is not installed on the hub") from exc
        self._running.add(proc)
        return proc

    async def clip_audio(self, conv: Conversion) -> bytes:
        """A spoken clip in the node's format, converted once and kept with the token."""
        async with conv.lock:
            if conv.converted is not None:
                return conv.converted
            if conv.source_bytes is None:
                raise ProxyError(404, "Not found")
            async with self._slot():
                conv.converted = await self._convert_bytes(conv.source_bytes, conv.fmt)
            conv.source_bytes = None  # the converted clip is all a refetch needs
            return conv.converted

    async def _convert_bytes(self, audio: bytes, fmt: AudioFormat) -> bytes:
        # A file, not a pipe, for the output: FLAC and WAV headers then carry the clip's real length.
        fd, path = tempfile.mkstemp(suffix="." + fmt.extension)
        os.close(fd)
        proc = None
        try:
            proc = await self._spawn(["-i", "pipe:0", *fmt.ffmpeg_output_args(), "-y", path],
                                     stdin=asyncio.subprocess.PIPE)
            try:
                _, err = await asyncio.wait_for(proc.communicate(audio), CLIP_TIMEOUT_S)
            except TimeoutError as exc:
                raise ProxyError(504, "ffmpeg took too long to convert the clip") from exc
            if proc.returncode != 0:
                log.warning("ffmpeg could not convert a spoken clip to %s: %s", fmt.public(),
                            err.decode(errors="replace").strip()[-300:])
                raise ProxyError(502, "the audio could not be converted")
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            if proc is not None:
                _kill(proc)
                self._running.discard(proc)
            with contextlib.suppress(OSError):
                os.unlink(path)

    async def open_stream(self, conv: Conversion) -> AsyncIterator[bytes]:
        """Start converting ``conv``'s URL; returns the converted bytes as they come.

        The first chunk is read before returning, so a source that cannot be read is an error status,
        not an empty 200. Fetching the same token again stops this conversion (as HA does).
        """
        if conv.source_url is None:
            raise ProxyError(404, "Not found")
        _kill(conv.proc)
        slot = self._slot()
        await slot.__aenter__()
        proc = None
        try:
            proc = await self._spawn(
                ["-protocol_whitelist", "http,https,tcp,tls,crypto", "-i", conv.source_url, *conv.fmt.ffmpeg_output_args(),
                 "-flush_packets", "1", "pipe:1"],  # each frame goes out as it is made
                stdin=asyncio.subprocess.DEVNULL)
            conv.proc = proc
            errors: list[bytes] = []
            drain = asyncio.ensure_future(_drain(proc.stderr, errors))
            first = await proc.stdout.read(CHUNK)
            if not first:
                await proc.wait()
                await asyncio.wait([drain], timeout=1.0)
                log.warning("ffmpeg could not read %s for %s: %s", _redact(conv.source_url), conv.device,
                            b"".join(errors).decode(errors="replace").strip()[-300:])
                raise ProxyError(502, "the source audio could not be read")
        except BaseException:
            if proc is not None:
                _kill(proc)
                self._running.discard(proc)
                if conv.proc is proc:
                    conv.proc = None
            await slot.__aexit__(None, None, None)
            raise

        async def body() -> AsyncIterator[bytes]:
            try:
                yield first
                while True:
                    chunk = await proc.stdout.read(CHUNK)
                    if not chunk:
                        break
                    yield chunk
            finally:
                _kill(proc)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), 2.0)
                drain.cancel()
                self._running.discard(proc)
                if conv.proc is proc:
                    conv.proc = None
                await slot.__aexit__(None, None, None)

        return body()


async def _drain(stream: Any, into: list[bytes]) -> None:
    """Keep ffmpeg's stderr read (a full pipe would stall it); the last few KB explain a failure."""
    total = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        into.append(chunk)
        total += len(chunk)
        while total > 8192 and len(into) > 1:
            total -= len(into.pop(0))


def _kill(proc: Any) -> None:
    if proc is not None and proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


def _hub_clip(source: str) -> bytes | None:
    """The audio of one of the hub's own spoken clips (``/api/tts_proxy/<token>.wav``), while it is fresh."""
    try:
        path = urlsplit(source).path
    except ValueError:
        return None
    m = _TTS_PATH.search(path)
    if not m:
        return None
    from services.homehub import tts

    return tts.clip(m.group(1))


def proxied_url(device: str, url: str, formats: list[AudioFormat], *, announcement: bool) -> str:
    """The URL to hand an ESPHome media player for ``url``: the proxy in its format, or ``url`` itself.

    ``url`` stays as it is when the node advertises no format, the codec is not one ffmpeg is taught
    here, the source is not ``http(s)`` (ffmpeg must never read a hub file for a caller), ffmpeg is
    missing, or the hub has no address a speaker can reach.
    """
    if not url or not formats or PATH_PREFIX in url:  # already a proxy URL: never wrap it twice
        return url
    fmt = choose_format(formats, announcement)
    if fmt is None:
        return url
    if fmt.format not in CODECS:
        log.warning("ESPHome %s asks for %s audio, which the hub cannot convert to; sending the URL as it is",
                    device, fmt.format)
        return url
    if urlsplit(url).scheme not in ("http", "https") or not PROXY.available():
        return url
    from services.homehub import tts
    from services.homehub.core import HubError

    try:
        base = tts._base_url()
    except HubError as exc:
        log.warning("ESPHome %s: no hub address for the audio proxy (%s); sending the URL as it is", device, exc)
        return url
    return PROXY.create(device, url, fmt, base)


PROXY = FFmpegProxy()


def reset() -> None:
    PROXY.reset()


__all__ = ["PROXY", "AudioFormat", "FFmpegProxy", "ProxyError", "choose_format", "proxied_url",
           "supported_formats"]
