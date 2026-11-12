"""The voice pipeline: every ESPHome voice satellite becomes a Jarvis endpoint.

Home Assistant's Assist ran this for the satellites; without a server that
subscribes to their voice requests they report that they cannot reach one. The
hub subscribes on every connection to a node whose ``voice_assistant_feature_flags``
include ``VOICE_ASSISTANT`` (:meth:`SatelliteVoice.attach`, called by the ESPHome
driver; a reconnect subscribes again on the new connection) and runs one turn per
wake word:

1. **Start.** The node detected its wake word itself and asks for a run
   (``handle_start``). Audio arrives over the API (``API_AUDIO``) or as UDP
   datagrams to a port the hub opens for the run. A node that wants the server to
   detect the wake word is told the hub has no wake word engine.
2. **Listen.** The audio streams to speech to text as it arrives, and an energy
   VAD (:class:`EnergyVAD`) decides when the person started and stopped talking.
   The silence that ends a command comes from the satellite's "Finished speaking
   detection" select (``relaxed`` 1.25 s, ``default`` 0.7 s, ``aggressive``
   0.25 s, as in HA). The node ending its audio also ends the command. Nothing
   heard within ``HOMEHUB_VOICE_NO_SPEECH_S`` is an error; ``HOMEHUB_VOICE_MAX_S``
   ends a command that never stops.
3. **Speech to text** over Wyoming (``HOMEHUB_VOICE_STT``, ``host:port``). Point it
   at execution's recognising proxy (``VOICE_STT_PORT``, 10310) rather than at
   Whisper itself: the proxy passes the audio to Whisper and records who spoke
   (``services/common/voice_speaker``), which is how the gateway answers as that
   person.
4. **Jarvis.** The transcript goes to the gateway's ``/api/chat`` exactly as HA's
   conversation agent sent it (``HOMEHUB_VOICE_JARVIS_URL``): no API key, so the
   gateway answers as the person the proxy recognised (only on a confident
   match) and otherwise as the non-admin household. The query is the transcript,
   unchanged, because the gateway finds the speaker by the transcript's words.
   ``voice_context`` names the satellite, its area and the conversation.
5. **Text to speech** with Jarvis's voice (``tts.make_clip``); the node fetches
   the URL. A node with its own speaker and no media player (``SPEAKER``) is also
   sent the audio itself over the API.

Each stage sends HA's ``VoiceAssistantEventType`` sequence (``RUN_START``,
``STT_START``, ``STT_VAD_START``/``END``, ``STT_END`` with the text,
``INTENT_START``/``END``, ``TTS_START`` with the text, ``TTS_END`` with the URL,
``RUN_END``), so the node's lights and screen follow along. Every failure is an
``ERROR`` event with a code and a plain message, then ``RUN_END``: nothing hangs.
A reply that asks a question sets ``continue_conversation`` and the node listens
again after speaking. ``assist_satellite.start_conversation`` speaks first through
the node's announcement call with ``start_conversation`` set; the node then
starts a run itself.

Transcripts and replies are logged at debug level only and never kept:
``GET /hub/voice`` (``voice_api.py``) shows timings, outcomes and errors.
Per satellite, ``voice_pipeline: false`` in the entry options turns it off.
"""
from __future__ import annotations

import array
import asyncio
import contextlib
import io
import json
import logging
import math
import os
import secrets
import statistics
import sys
import time
import wave
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from aioesphomeapi import (
    APIConnectionError,
    VoiceAssistantCommandFlag,
    VoiceAssistantEventType,
    VoiceAssistantFeature,
)

from services.homehub.core import UNAVAILABLE, DeviceUnavailable, EntityEntry, HubError, utcnow

log = logging.getLogger("homehub.voice")

RATE, WIDTH, CHANNELS = 16000, 2, 1  # what ESPHome satellites send: 16 kHz, 16-bit, mono
BYTES_PER_S = RATE * WIDTH * CHANNELS
STT_CONNECT_TIMEOUT_S = 5.0
ANNOUNCE_TIMEOUT_S = 120.0
#: The run's own guard over a Jarvis turn, past the HTTP timeout (which normally fires first).
JARVIS_GUARD_S = 5.0
#: How long a start_conversation's question is remembered for the run it starts.
PENDING_CONTEXT_S = 60.0
#: HA's "Finished speaking detection" (VadSensitivity) in seconds of silence.
FINISHED_SPEAKING_S = {"relaxed": 1.25, "default": 0.7, "aggressive": 0.25}
SATELLITE_FEATURE_ANNOUNCE, SATELLITE_FEATURE_START_CONVERSATION = 1, 2  # AssistSatelliteEntityFeature
#: Audio chunk size and pace when streaming a reply to a node's own speaker.
STREAM_CHUNK = 1024


class PipelineError(Exception):
    """One run's failure: ``code`` is HA's error code, the message is for people."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# -- settings -------------------------------------------------------------------
def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise HubError(f"{name} must be a number, not {raw!r}") from exc
    if value <= 0:
        raise HubError(f"{name} must be more than 0, not {raw!r}")
    return value


@dataclass
class VoiceSettings:
    """The hub's voice settings, read from the environment for every run (a change needs no restart)."""

    stt: str = ""
    jarvis_url: str = ""
    language: str = "en"
    tts_voice: str | None = None
    max_seconds: float = 15.0
    no_speech_seconds: float = 8.0
    stt_timeout: float = 30.0
    jarvis_timeout: float = 90.0
    continue_conversation: bool = True
    vad_min_rms: float = 250.0

    @classmethod
    def from_env(cls) -> VoiceSettings:
        return cls(
            stt=os.getenv("HOMEHUB_VOICE_STT", "").strip(),
            jarvis_url=os.getenv("HOMEHUB_VOICE_JARVIS_URL", "").strip().rstrip("/"),
            language=os.getenv("HOMEHUB_VOICE_LANGUAGE", "").strip() or "en",
            tts_voice=os.getenv("HOMEHUB_VOICE_TTS_VOICE", "").strip() or None,
            max_seconds=_float_env("HOMEHUB_VOICE_MAX_S", 15.0),
            no_speech_seconds=_float_env("HOMEHUB_VOICE_NO_SPEECH_S", 8.0),
            stt_timeout=_float_env("HOMEHUB_VOICE_STT_TIMEOUT_S", 30.0),
            jarvis_timeout=_float_env("HOMEHUB_VOICE_JARVIS_TIMEOUT_S", 90.0),
            continue_conversation=os.getenv("HOMEHUB_VOICE_CONTINUE", "1").strip().lower()
            not in ("0", "false", "no", "off"),
            vad_min_rms=_float_env("HOMEHUB_VOICE_VAD_MIN_RMS", 250.0),
        )

    def problems(self) -> list[str]:
        """What keeps a run from working, each naming the setting to fix."""
        out = []
        if not self.stt:
            out.append("HOMEHUB_VOICE_STT is not set (the Wyoming speech to text server as host:port; "
                       "execution's recognising proxy, VOICE_STT_PORT)")
        elif not _parse_address(self.stt):
            out.append(f"HOMEHUB_VOICE_STT must be host:port, not {self.stt!r}")
        if not self.jarvis_url:
            out.append("HOMEHUB_VOICE_JARVIS_URL is not set (the gateway's address, which serves /api/chat)")
        return out

    def public(self) -> dict[str, Any]:
        return {"stt": self.stt or None, "jarvis_url": self.jarvis_url or None, "language": self.language,
                "tts_voice": self.tts_voice, "max_seconds": self.max_seconds,
                "no_speech_seconds": self.no_speech_seconds, "stt_timeout_s": self.stt_timeout,
                "jarvis_timeout_s": self.jarvis_timeout, "continue_conversation": self.continue_conversation,
                "vad_min_rms": self.vad_min_rms,
                "problems": self.problems()}


def _parse_address(value: str) -> tuple[str, int] | None:
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isdigit():
        return None
    return host.strip("[]"), int(port)


# -- end of speech ----------------------------------------------------------------
class EnergyVAD:
    """Speech start and end from the loudness of 30 ms frames, against the room's own noise floor.

    A frame is speech when its RMS is ``ratio`` times the noise floor and at least ``min_rms`` (a
    quiet room still hisses; ``HOMEHUB_VOICE_VAD_MIN_RMS``). Speech starts after ``start_frames``
    speech frames in a row and ends after ``silence_s`` of frames that are not. The floor is the
    median of the last second of frames that were not speech, so a voice never becomes the floor and
    a person who starts talking at once is still heard; it starts at ``min_rms / ratio``.
    """

    FRAME_MS = 30
    FLOOR_FRAMES = 33  # one second of non-speech frames

    def __init__(self, silence_s: float, *, rate: int = RATE, ratio: float = 2.5, min_rms: float = 250.0,
                 start_frames: int = 3) -> None:
        self.frame_bytes = rate * WIDTH * self.FRAME_MS // 1000
        self.silence_frames = max(1, math.ceil(silence_s * 1000 / self.FRAME_MS))
        self.ratio, self.min_rms, self.start_frames = ratio, min_rms, start_frames
        self._quiet_levels: deque[float] = deque([min_rms / ratio] * self.FLOOR_FRAMES, maxlen=self.FLOOR_FRAMES)
        self._buf = b""
        self._run = 0
        self._quiet = 0
        self.in_speech = False
        self.speech_frames = 0

    @property
    def floor(self) -> float:
        return statistics.median(self._quiet_levels)

    @staticmethod
    def rms(frame: bytes) -> float:
        samples = array.array("h")
        samples.frombytes(frame[: len(frame) - len(frame) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    def process(self, pcm: bytes) -> list[str]:
        """Feed audio; returns the edges it crossed, ``"start"`` and/or ``"end"``, in order."""
        self._buf += pcm
        events: list[str] = []
        while len(self._buf) >= self.frame_bytes:
            frame, self._buf = self._buf[: self.frame_bytes], self._buf[self.frame_bytes:]
            level = self.rms(frame)
            speech = level >= max(self.min_rms, self.floor * self.ratio)
            if not speech:
                self._quiet_levels.append(level)
            if not self.in_speech:
                self._run = self._run + 1 if speech else 0
                if self._run >= self.start_frames:
                    self.in_speech, self._quiet = True, 0
                    self.speech_frames += self._run
                    events.append("start")
            elif speech:
                self._quiet = 0
                self.speech_frames += 1
            else:
                self._quiet += 1
                if self._quiet >= self.silence_frames:
                    events.append("end")
                    self.in_speech, self._run = False, 0
                    break
        return events


# -- the three services a run uses -----------------------------------------------------
class WyomingSTT:
    """A Wyoming ``transcribe`` session: audio streams in as it arrives, the transcript comes back at the end."""

    def __init__(self, address: str, language: str) -> None:
        parsed = _parse_address(address)
        if parsed is None:
            raise PipelineError("stt-provider-missing", f"HOMEHUB_VOICE_STT must be host:port, not {address!r}")
        self.address = address
        self.host, self.port = parsed
        self.language = language
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), STT_CONNECT_TIMEOUT_S)
        except (OSError, TimeoutError) as exc:
            raise PipelineError("stt-provider-unavailable", f"Speech to text at {self.address} did not answer "
                                f"({exc or type(exc).__name__}); check HOMEHUB_VOICE_STT") from exc
        fmt = {"rate": RATE, "width": WIDTH, "channels": CHANNELS}
        await self._write("transcribe", {"language": self.language})
        await self._write("audio-start", fmt)

    async def _write(self, kind: str, data: dict[str, Any], payload: bytes = b"") -> None:
        if self._writer is None:
            raise PipelineError("stt-stream-failed", "speech to text is not connected")
        body = json.dumps(data).encode()
        header: dict[str, Any] = {"type": kind, "data_length": len(body)}
        if payload:
            header["payload_length"] = len(payload)
        try:
            self._writer.write(json.dumps(header).encode() + b"\n" + body + payload)
            await self._writer.drain()
        except (OSError, ConnectionError) as exc:
            raise PipelineError("stt-stream-failed", f"Speech to text at {self.address} dropped the audio: "
                                f"{exc}") from exc

    async def send(self, pcm: bytes) -> None:
        await self._write("audio-chunk", {"rate": RATE, "width": WIDTH, "channels": CHANNELS}, pcm)

    async def finish(self) -> str:
        await self._write("audio-stop", {})
        reader = self._reader
        assert reader is not None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    raise PipelineError("stt-stream-failed",
                                        f"Speech to text at {self.address} closed without a transcript")
                header = json.loads(line)
                data = dict(header.get("data") or {})
                if header.get("data_length"):
                    data.update(json.loads(await reader.readexactly(int(header["data_length"]))))
                if header.get("payload_length"):
                    await reader.readexactly(int(header["payload_length"]))
                if header.get("type") == "transcript":
                    return str(data.get("text") or "").strip()
                if header.get("type") == "error":
                    raise PipelineError("stt-stream-failed",
                                        f"Speech to text failed: {data.get('text') or data.get('code') or data}")
        except (OSError, ConnectionError, asyncio.IncompleteReadError, ValueError) as exc:
            raise PipelineError("stt-stream-failed", f"Speech to text at {self.address} failed: {exc}") from exc

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def chat_reply_text(body: Any) -> str | None:
    """The reply in a gateway ``/api/chat`` answer (Ollama, OpenAI or plain ``message``)."""
    if not isinstance(body, dict):
        return None
    message = body.get("message")
    content = message.get("content") if isinstance(message, dict) else message
    choices = body.get("choices")
    openai = None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        openai = (choices[0].get("message") or {}).get("content")
    for text in (body.get("response"), content, openai):
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


async def ask_jarvis(settings: VoiceSettings, text: str, context: dict[str, Any]) -> str:
    """One conversation turn, through the same ``/api/chat`` HA's conversation agent used."""
    url = f"{settings.jarvis_url}/api/chat"
    body = {"query": text, "stream": False, "voice_context": context}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=settings.jarvis_timeout)) as s, \
                s.post(url, json=body) as r:
            data = await r.json(content_type=None)
            status = r.status
    except TimeoutError as exc:
        raise PipelineError("intent-timeout", f"Jarvis did not answer within {settings.jarvis_timeout:g} s") from exc
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        raise PipelineError("intent-failed", f"Jarvis at {settings.jarvis_url} is unreachable: {exc}") from exc
    if status != 200 or (isinstance(data, dict) and str(data.get("status") or "").upper() == "ERROR"):
        detail = (data.get("message") or data.get("detail")) if isinstance(data, dict) else data
        raise PipelineError("intent-failed", f"Jarvis could not answer (HTTP {status}): {detail}")
    reply = chat_reply_text(data)
    if not reply:
        raise PipelineError("intent-failed", "Jarvis answered with no text")
    return reply


async def make_speech(text: str, voice: str | None) -> str:
    from services.homehub import tts

    return await tts.make_clip(text, voice)


@dataclass
class Backends:
    """What a run talks to; tests swap these for fakes."""

    open_stt: Callable[[VoiceSettings], Any] = lambda s: WyomingSTT(s.stt, s.language)
    ask: Callable[[VoiceSettings, str, dict[str, Any]], Awaitable[str]] = ask_jarvis
    speak: Callable[[str, str | None], Awaitable[str]] = make_speech
    clip_audio: Callable[[str], bytes | None] = field(default=lambda url: _clip_bytes(url))


def _clip_bytes(url: str) -> bytes | None:
    from services.homehub import tts

    token = url.rsplit("/", 1)[-1].removesuffix(".wav")
    return tts.clip(token)


BACKENDS = Backends()


# -- audio helpers ------------------------------------------------------------------
def _gain(pcm: bytes, multiplier: float) -> bytes:
    if not pcm or abs(multiplier - 1.0) < 1e-3:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    for i, s in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(s * multiplier)))
    return samples.tobytes()


def to_satellite_pcm(wav_bytes: bytes) -> bytes:
    """A WAV clip as 16 kHz mono 16-bit PCM, what a node's speaker takes over the API."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise PipelineError("tts-failed", f"the spoken clip is {width * 8}-bit; the satellite needs 16-bit")
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if channels > 1:
        samples = array.array("h", (sum(samples[i:i + channels]) // channels
                                    for i in range(0, len(samples) - channels + 1, channels)))
    if rate != RATE and samples:
        n = int(len(samples) * RATE / rate)
        step = (len(samples) - 1) / max(1, n - 1)
        out = array.array("h")
        for i in range(n):
            pos = i * step
            j = int(pos)
            frac = pos - j
            nxt = samples[min(j + 1, len(samples) - 1)]
            out.append(int(samples[j] + (nxt - samples[j]) * frac))
        samples = out
    return samples.tobytes()


class _UDPAudio(asyncio.DatagramProtocol):
    """The UDP audio path (nodes without ``API_AUDIO``): datagrams from the node, onto the run's queue."""

    def __init__(self, queue: asyncio.Queue, allowed: str | None) -> None:
        self.queue = queue
        self.allowed = allowed
        self.remote: tuple[str, int] | None = None
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.allowed and addr[0] != self.allowed:
            log.warning("voice audio from %s ignored: the run belongs to %s", addr[0], self.allowed)
            return
        if self.remote is None:
            self.remote = addr
        elif addr != self.remote:
            return
        self.queue.put_nowait(data)


# -- one satellite ----------------------------------------------------------------------
@dataclass
class Run:
    id: str
    conversation_id: str
    wake_word: str | None
    started: float = field(default_factory=time.monotonic)
    started_at: str = field(default_factory=lambda: utcnow().isoformat())
    marks: dict[str, float] = field(default_factory=dict)
    ended_by: str | None = None
    outcome: str = "running"
    error: dict[str, str] | None = None
    continue_conversation: bool = False
    speech_ms: int | None = None
    run_end_sent: bool = False
    aborted: bool = False

    def mark(self, name: str) -> None:
        self.marks[name] = time.monotonic()

    def timings_ms(self) -> dict[str, int]:
        m, out = self.marks, {}
        spans = (("listen", "listen", "listened"), ("stt", "listened", "transcribed"),
                 ("intent", "intent", "answered"), ("tts", "tts", "spoken"))
        for name, a, b in spans:
            if a in m and b in m:
                out[name] = round((m[b] - m[a]) * 1000)
        if "ended" in m:
            out["total"] = round((m["ended"] - self.started) * 1000)
        return out

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "started_at": self.started_at, "wake_word": self.wake_word,
                "outcome": self.outcome, "ended_by": self.ended_by, "error": self.error,
                "continue_conversation": self.continue_conversation, "speech_ms": self.speech_ms,
                "timings_ms": self.timings_ms()}


def feature_names(flags: int) -> list[str]:
    return [f.name.lower() for f in VoiceAssistantFeature if flags & int(f)]


class SatelliteVoice:
    """The voice pipeline of one ESPHome node, kept across its connections."""

    def __init__(self, driver: Any) -> None:
        self.driver = driver
        self.hub = driver.hub
        self.client: Any = None
        self.flags = 0
        self.subscribed = False
        self.runs = 0
        self.last_run: Run | None = None
        self.last_error: dict[str, Any] | None = None
        self._unsub: Callable[[], None] | None = None
        self._run: Run | None = None
        self._task: asyncio.Task | None = None
        self._audio: asyncio.Queue | None = None
        self._udp: asyncio.DatagramTransport | None = None
        self._udp_proto: _UDPAudio | None = None
        self._audio_settings: Any = None
        self._pending: tuple[float, dict[str, Any]] | None = None
        self._tasks: set[asyncio.Task] = set()
        self.log = logging.getLogger(f"homehub.voice.{driver.device_name or driver.host}")

    # -- what it is -----------------------------------------------------------------
    @property
    def name(self) -> str:
        return str(self.driver.device_name or self.driver.host)

    @property
    def enabled(self) -> bool:
        return self.driver.entry.options.get("voice_pipeline", True) is not False

    @property
    def is_satellite(self) -> bool:
        return bool(self.flags & int(VoiceAssistantFeature.VOICE_ASSISTANT))

    @property
    def can_converse(self) -> bool:
        return self.subscribed and bool(self.flags & int(VoiceAssistantFeature.START_CONVERSATION))

    def satellite_entity(self) -> EntityEntry | None:
        return next((e for e in self.driver.entities if e.domain == "assist_satellite"), None)

    def _local(self, suffix: str) -> EntityEntry | None:
        return next((e for e in self.driver.entities if (e.unique_id or "").endswith(f"-{suffix}")
                     and "/" not in (e.unique_id or "")), None)

    def area(self) -> tuple[str | None, str | None]:
        ent = self.satellite_entity() or next(iter(self.driver.entities), None)
        if ent is None:
            return None, None
        area_id = self.hub.entity_area_id(ent.entity_id)
        area = self.hub.areas.get(area_id or "")
        return area_id, (area.name if area is not None else None)

    def silence_seconds(self) -> float:
        sel = self._local("vad_sensitivity")
        st = self.hub.states.get(sel.entity_id) if sel is not None else None
        return FINISHED_SPEAKING_S.get(str(st.state) if st is not None else "", FINISHED_SPEAKING_S["default"])

    # -- connection ------------------------------------------------------------------
    def attach(self, client: Any, device_info: Any) -> bool:
        """Subscribe on a new connection when the node is a satellite and its pipeline is on."""
        self._cancel_run("the node reconnected")
        self._unsubscribe()
        self.client = client
        self.flags = int(getattr(device_info, "voice_assistant_feature_flags", 0) or 0)
        if not self.is_satellite:
            return False
        if not self.enabled:
            self.log.info("voice satellite %s: the pipeline is turned off for it (voice_pipeline: false)", self.name)
            self._publish_satellite("idle")
            return False
        api_audio = bool(self.flags & int(VoiceAssistantFeature.API_AUDIO))
        self._unsub = client.subscribe_voice_assistant(
            handle_start=self._handle_start, handle_stop=self._handle_stop,
            handle_audio=self._handle_audio if api_audio else None,
            handle_announcement_finished=self._handle_announcement_finished)
        self.subscribed = True
        self.log.info("voice satellite %s: listening for its wake word (%s; audio over %s)", self.name,
                      ", ".join(feature_names(self.flags)), "the API" if api_audio else "UDP")
        self._publish_vad_select()
        self._publish_satellite("idle")
        return True

    def detach(self) -> None:
        """The connection is gone (or the entry unloads): stop any run, show the satellite unavailable."""
        self._cancel_run("the node disconnected")
        self._unsubscribe()
        self.client = None
        ent = self.satellite_entity()
        if ent is not None and self.is_satellite:
            cur = self.hub.states.get(ent.entity_id)
            self.hub.publish(ent, UNAVAILABLE, dict(cur.attributes) if cur else {})

    def _unsubscribe(self) -> None:
        unsub, self._unsub = self._unsub, None
        self.subscribed = False
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()

    def _publish_satellite(self, state: str) -> None:
        ent = self.satellite_entity()
        if ent is None:
            return
        features = 0
        if self.flags & int(VoiceAssistantFeature.ANNOUNCE):
            features |= SATELLITE_FEATURE_ANNOUNCE
        if self.can_converse:
            features |= SATELLITE_FEATURE_START_CONVERSATION
        self.hub.publish(ent, state, {"supported_features": features})

    def _publish_vad_select(self) -> None:
        sel = self._local("vad_sensitivity")
        if sel is None:
            return
        cur = self.hub.states.get(sel.entity_id)
        state = cur.state if cur is not None and cur.state in FINISHED_SPEAKING_S else "default"
        self.hub.publish(sel, state, {"options": list(FINISHED_SPEAKING_S)})

    # -- the node's requests ------------------------------------------------------------
    async def _handle_start(self, conversation_id: str, flags: int, audio_settings: Any,
                            wake_word_phrase: str | None, *_: Any) -> int | None:
        self._cancel_run("a new run started")
        run = Run(id=secrets.token_hex(4), conversation_id=conversation_id or secrets.token_hex(8),
                  wake_word=wake_word_phrase)
        self._run = run
        self._audio = asyncio.Queue()
        self._audio_settings = audio_settings
        port = 0
        if not self.flags & int(VoiceAssistantFeature.API_AUDIO):
            try:
                port = await self._open_udp(self._audio)
            except OSError as exc:
                self.log.error("voice satellite %s: could not open a UDP port for its audio: %s", self.name, exc)
                return None
        self._task = asyncio.create_task(self._run_pipeline(run, int(flags or 0)),
                                         name=f"voice:{self.name}:{run.id}")
        return port

    async def _open_udp(self, queue: asyncio.Queue) -> int:
        allowed = None
        with contextlib.suppress(OSError):
            import socket

            allowed = await asyncio.to_thread(socket.gethostbyname, self.driver.host)
        if allowed is None:
            self.log.warning("voice satellite %s: %s did not resolve; accepting the run's audio from the first "
                             "sender", self.name, self.driver.host)
        transport, proto = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _UDPAudio(queue, allowed), local_addr=("0.0.0.0", 0))
        self._udp, self._udp_proto = transport, proto
        return int(transport.get_extra_info("sockname")[1])

    async def _handle_stop(self, abort: bool) -> None:
        run = self._run
        if run is None:
            return
        if abort:
            run.aborted = True
            self.log.info("voice satellite %s: the node stopped run %s", self.name, run.id)
            self._cancel_run("the node stopped it")
        elif self._audio is not None:
            self._audio.put_nowait(None)  # the node ended its audio

    async def _handle_audio(self, data: bytes, *_: Any) -> None:
        if self._audio is not None:
            self._audio.put_nowait(data)

    async def _handle_announcement_finished(self, finished: Any) -> None:
        self.log.debug("voice satellite %s: announcement finished (%s)", self.name,
                       getattr(finished, "success", None))

    def _cancel_run(self, why: str) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            self.log.info("voice satellite %s: run cancelled: %s", self.name, why)
            task.cancel()
        self._close_udp()

    def _close_udp(self) -> None:
        transport, self._udp, self._udp_proto = self._udp, None, None
        if transport is not None:
            transport.close()

    # -- events ------------------------------------------------------------------
    def _event(self, run: Run, kind: VoiceAssistantEventType, data: dict[str, str] | None = None) -> None:
        client = self.client
        if client is None:
            raise PipelineError("satellite-disconnected", f"{self.name} disconnected during the run")
        try:
            client.send_voice_assistant_event(kind, data if data is not None else {})
        except APIConnectionError as exc:
            raise PipelineError("satellite-disconnected", f"{self.name} disconnected during the run: {exc}") from exc
        if kind == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            run.run_end_sent = True

    # -- the run -------------------------------------------------------------------------
    async def _run_pipeline(self, run: Run, flags: int) -> None:
        settings: VoiceSettings | None = None
        stt = None
        try:
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START, {})
            if flags & int(VoiceAssistantCommandFlag.USE_WAKE_WORD):
                raise PipelineError("wake-engine-missing", "The hub has no wake word engine; set the satellite's "
                                    "wake word engine location to On device")
            try:
                settings = VoiceSettings.from_env()
            except HubError as exc:
                raise PipelineError("pipeline-not-configured", str(exc)) from exc
            problems = settings.problems()
            if problems:
                raise PipelineError("stt-provider-missing" if "STT" in problems[0] else "intent-not-supported",
                                    "; ".join(problems))
            self._check_tts()
            self._publish_satellite("listening")
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_STT_START, {})
            stt = BACKENDS.open_stt(settings)
            await stt.open()
            run.mark("listen")
            await self._listen(run, settings, stt)
            run.mark("listened")
            try:
                async with asyncio.timeout(settings.stt_timeout):
                    text = await stt.finish()
            except TimeoutError as exc:
                raise PipelineError("stt-timeout", f"Speech to text gave no transcript within "
                                    f"{settings.stt_timeout:g} s") from exc
            run.mark("transcribed")
            if not text:
                raise PipelineError("stt-no-text-recognized", "No words were recognised")
            self.log.debug("voice satellite %s run %s heard: %s", self.name, run.id, text)
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_STT_END, {"text": text})

            self._publish_satellite("processing")
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_START, {})
            run.mark("intent")
            try:
                async with asyncio.timeout(settings.jarvis_timeout + JARVIS_GUARD_S):
                    reply = await BACKENDS.ask(settings, text, self._context(run))
            except TimeoutError as exc:
                raise PipelineError("intent-timeout",
                                    f"Jarvis did not answer within {settings.jarvis_timeout:g} s") from exc
            run.mark("answered")
            self.log.debug("voice satellite %s run %s reply: %s", self.name, run.id, reply)
            run.continue_conversation = settings.continue_conversation and reply.rstrip().endswith("?")
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END, {
                "conversation_id": run.conversation_id,
                "continue_conversation": "1" if run.continue_conversation else "0"})

            self._publish_satellite("responding")
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START, {"text": reply})
            run.mark("tts")
            try:
                url = await BACKENDS.speak(reply, settings.tts_voice)
            except (HubError, OSError) as exc:
                raise PipelineError("tts-failed", f"Jarvis's voice could not speak the reply: {exc}") from exc
            run.mark("spoken")
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END, {"url": url})
            if self.flags & int(VoiceAssistantFeature.SPEAKER):
                await self._stream_reply(run, url)
            run.outcome = "ok"
        except asyncio.CancelledError:
            # Superseded, stopped by the node or disconnected: the node is no longer in this run, and a
            # RUN_END now would end the run it is in.
            run.outcome = "aborted" if run.aborted else "cancelled"
            run.run_end_sent = True
            self._finish(run, settings)
            raise
        except PipelineError as exc:
            self._fail(run, exc)
        except Exception as exc:  # a bug must still end the run on the node, and be seen
            self.log.exception("voice satellite %s run %s failed", self.name, run.id)
            self._fail(run, PipelineError("pipeline-error", f"The hub's voice pipeline failed: {exc}"))
        finally:
            if stt is not None:
                with contextlib.suppress(Exception):
                    await stt.close()
        self._finish(run, settings)

    def _fail(self, run: Run, exc: PipelineError) -> None:
        run.outcome = "error"
        run.error = {"code": exc.code, "message": exc.message}
        self.last_error = {**run.error, "run": run.id, "at": utcnow().isoformat()}
        self.log.warning("voice satellite %s run %s: %s (%s)", self.name, run.id, exc.message, exc.code)
        if exc.code != "satellite-disconnected":
            with contextlib.suppress(PipelineError):
                self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_ERROR,
                            {"code": exc.code, "message": exc.message})

    def _finish(self, run: Run, settings: VoiceSettings | None) -> None:
        if "ended" in run.marks:
            return
        run.mark("ended")
        if not run.run_end_sent and self.client is not None:
            with contextlib.suppress(PipelineError):
                self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, {})
        if self._run is run:
            self._run = None
            self._audio = None
            self._close_udp()
        self.runs += 1
        self.last_run = run
        t = run.timings_ms()
        self.log.info("voice satellite %s run %s: %s%s; ended by %s; %s", self.name, run.id, run.outcome,
                      f" ({run.error['code']})" if run.error else "", run.ended_by or "-",
                      " ".join(f"{k}={v}ms" for k, v in t.items()) or "no timings")
        if self.client is not None and self.subscribed:
            self._publish_satellite("idle")

    def _check_tts(self) -> None:
        from services.homehub import tts

        try:
            tts._base_url()
        except HubError as exc:
            raise PipelineError("tts-not-supported", str(exc)) from exc

    async def _listen(self, run: Run, settings: VoiceSettings, stt: Any) -> None:
        """Stream the node's audio to speech to text until the person stops talking."""
        queue = self._audio
        assert queue is not None
        vad = EnergyVAD(self.silence_seconds(), min_rms=settings.vad_min_rms)
        loop = asyncio.get_running_loop()
        started = loop.time()
        gain = float(getattr(self._audio_settings, "volume_multiplier", 1.0) or 1.0)
        while True:
            now = loop.time()
            limit = settings.max_seconds if vad.speech_frames else settings.no_speech_seconds
            remaining = started + limit - now
            if remaining <= 0:
                if not vad.speech_frames:
                    raise PipelineError("stt-no-text-recognized", f"No speech was heard within {limit:g} s")
                run.ended_by = "max_duration"
                break
            try:
                # asyncio.timeout, not wait_for: on 3.11 wait_for drops a cancel that lands with a chunk.
                async with asyncio.timeout(remaining):
                    chunk = await queue.get()
            except TimeoutError:
                continue
            if chunk is None:
                run.ended_by = "satellite"
                break
            chunk = _gain(chunk, gain)
            await stt.send(chunk)
            for edge in vad.process(chunk):
                if edge == "start":
                    self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START, {})
                else:
                    run.ended_by = "vad"
            if run.ended_by == "vad":
                break
        if not vad.speech_frames and run.ended_by != "max_duration":
            raise PipelineError("stt-no-text-recognized", "No speech was heard")
        run.speech_ms = vad.speech_frames * EnergyVAD.FRAME_MS
        self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, {})

    def _context(self, run: Run) -> dict[str, Any]:
        ent = self.satellite_entity()
        area_id, area = self.area()
        ctx: dict[str, Any] = {
            "source": "homehub_satellite",
            "satellite": ent.entity_id if ent is not None else None,
            "satellite_name": self.hub.friendly_name(ent) if ent is not None else self.name,
            "device_name": self.name,
            "area_id": area_id, "area": area,
            "conversation_id": run.conversation_id,
            "wake_word": run.wake_word,
        }
        if self._pending is not None:
            at, extra = self._pending
            self._pending = None
            if time.monotonic() - at <= PENDING_CONTEXT_S:
                ctx.update(extra)
        return ctx

    async def _stream_reply(self, run: Run, url: str) -> None:
        """A node with its own speaker gets the reply's audio itself, paced a little faster than real time."""
        audio = BACKENDS.clip_audio(url)
        if not audio:
            raise PipelineError("tts-failed", "the spoken reply expired before it could be sent")
        pcm = to_satellite_pcm(audio)
        self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_TTS_STREAM_START, {})
        udp_target = self._udp_proto.remote if self._udp_proto is not None else None
        for i in range(0, len(pcm), STREAM_CHUNK):
            chunk = pcm[i:i + STREAM_CHUNK]
            if self.flags & int(VoiceAssistantFeature.API_AUDIO):
                client = self.client
                if client is None:
                    raise PipelineError("satellite-disconnected", f"{self.name} disconnected during the reply")
                client.send_voice_assistant_audio(chunk)
            elif self._udp is not None and udp_target is not None:
                self._udp.sendto(chunk, udp_target)
            await asyncio.sleep(len(chunk) / BYTES_PER_S * 0.9)
        self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_TTS_STREAM_END, {})

    # -- assist_satellite.start_conversation ----------------------------------------------
    async def start_conversation(self, data: dict[str, Any]) -> None:
        """Jarvis speaks first through the node's announcement call; the node then listens and starts a run."""
        if not self.is_satellite or not self.flags & int(VoiceAssistantFeature.START_CONVERSATION):
            raise HubError(f"assist_satellite.start_conversation needs the node's voice pipeline with start "
                           f"conversation; {self.name} does not offer it (firmware feature START_CONVERSATION)")
        if not self.enabled:
            raise HubError(f"the voice pipeline is turned off for {self.name} (voice_pipeline: false)")
        if not self.subscribed or self.client is None:
            raise DeviceUnavailable(f"ESPHome {self.name} is not connected")
        problems = VoiceSettings.from_env().problems()
        if problems:
            raise HubError("start_conversation cannot hear the answer: " + "; ".join(problems))
        message = str(data.get("start_message") or "")
        media_id = str(data.get("start_media_id") or "")
        if not media_id:
            if not message:
                raise HubError("assist_satellite.start_conversation needs a start_message or a start_media_id")
            media_id = await BACKENDS.speak(message, VoiceSettings.from_env().tts_voice)
        extra: dict[str, Any] = {"started_by": "start_conversation"}
        if message:
            extra["asked"] = message
        if data.get("extra_system_prompt"):
            extra["extra_system_prompt"] = str(data["extra_system_prompt"])
        self._pending = (time.monotonic(), extra)
        preannounce = data.get("preannounce", True) is not False
        try:
            await self.client.send_voice_assistant_announcement_await_response(
                media_id, ANNOUNCE_TIMEOUT_S, text=message,
                preannounce_media_id=str(data.get("preannounce_media_id") or "") if preannounce else "",
                start_conversation=True)
        except APIConnectionError as exc:
            raise DeviceUnavailable(f"ESPHome {self.name}: {exc}") from exc
        except TimeoutError as exc:
            raise DeviceUnavailable(f"ESPHome {self.name} did not finish speaking") from exc

    # -- observability ---------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        ent = self.satellite_entity()
        st = self.hub.states.get(ent.entity_id) if ent is not None else None
        area_id, area = self.area()
        return {
            "entry_id": self.driver.entry.entry_id, "title": self.driver.entry.title, "device_name": self.name,
            "entity_id": ent.entity_id if ent is not None else None, "state": st.state if st else None,
            "area_id": area_id, "area": area, "connected": self.client is not None,
            "features": feature_names(self.flags),
            "audio": ("api" if self.flags & int(VoiceAssistantFeature.API_AUDIO) else "udp")
            if self.is_satellite else None,
            "enabled": self.enabled, "subscribed": self.subscribed,
            "finished_speaking_s": self.silence_seconds(),
            "running": self._run.id if self._run is not None else None,
            "runs": self.runs, "last_run": self.last_run.public() if self.last_run else None,
            "last_error": self.last_error,
        }


def satellites(hub: Any) -> list[dict[str, Any]]:
    """Every ESPHome node that is (or, while offline, has an entity that says it is) a voice satellite."""
    out = []
    for drv in list(hub.drivers.values()):
        voice = getattr(drv, "voice", None)
        if not isinstance(voice, SatelliteVoice):
            continue
        if voice.is_satellite or voice.satellite_entity() is not None:
            out.append(voice.status())
    return sorted(out, key=lambda s: s["title"] or "")


__all__ = ["BACKENDS", "Backends", "EnergyVAD", "PipelineError", "SatelliteVoice", "VoiceSettings",
           "WyomingSTT", "ask_jarvis", "chat_reply_text", "satellites", "to_satellite_pcm"]
