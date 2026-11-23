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
   A command starts on 300 ms of speech (the satellite's "Speech detection"
   select: ``sensitive`` 200 ms, ``default``, ``strict`` 450 ms with a higher
   gate), never on a chime or a knock; WebRTC's speech detector, when installed
   (``webrtcvad``, ``HOMEHUB_VOICE_VAD``), must also call it speech. A follow-up
   listen (the reply asked a question, or ``start_conversation`` spoke) skips
   its first ``HOMEHUB_VOICE_FOLLOWUP_HOLDOFF_S`` and starts on a 1.5x gate, so
   the satellite's own voice cannot start the next command.
   The silence that ends a command comes from the satellite's "Finished speaking
   detection" select (``relaxed`` 1.25 s, ``default`` 0.7 s, ``aggressive``
   0.25 s, as in HA). The node ending its audio also ends the command. Nothing
   heard within ``HOMEHUB_VOICE_NO_SPEECH_S`` is an error; ``HOMEHUB_VOICE_MAX_S``
   ends a command that never stops. The satellite's ``assist_satellite`` and
   these selects are registered when a node is adopted on the hub
   (:meth:`SatelliteVoice.ensure_entities`), as HA's integration did.
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
5. **Text to speech** with Jarvis's voice. The reply is spoken as it is written,
   as HA does with streaming TTS: ``RUN_START`` names a growing stream
   (``tts.ClipStream`` at ``/api/tts_stream/``, through the node's ffmpeg proxy);
   the gateway sends whole sentences that are safe to say early
   (``"speech_stream": true``, ``services/gateway/speech_stream.py``); each is
   synthesised and appended, and the first one sends ``INTENT_PROGRESS`` with
   ``tts_start_streaming``, on which the node starts playing that URL. The rest
   of the reply follows into the same stream and ``TTS_END`` names it (firmware
   that ignores ``INTENT_PROGRESS`` plays it then). An answer that streamed no
   sentence (a shortcut, a device command) is spoken whole (``tts.make_clip``)
   as before, and so is every reply to a node with its own speaker and no media
   player (``SPEAKER``), which is sent the audio itself over the API after
   ``TTS_END``. ``HOMEHUB_VOICE_STREAM=0`` turns streaming off. Each run logs
   ``stt``, ``first_audio`` (end of the command to the reply's first audio) and
   ``total``.

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
from typing import Any, ClassVar

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


@dataclass(frozen=True)
class SpeechDetection:
    """How much sound it takes to start a command (the satellite's "Speech detection" select)."""

    min_speech_s: float  # this much speech before a command starts (a chime or a knock is shorter)
    threshold_factor: float  # times the loudness gate
    webrtc_mode: int  # WebRTC VAD aggressiveness, 0 (lenient) to 3 (strict)


#: The satellite's "Speech detection" select. A start on 90 ms of sound let a chime or the end of a reply
#: start a command: 3 of 20 runs (2026-11-19) ended after about 1 s with no words recognised.
SPEECH_DETECTION = {
    "sensitive": SpeechDetection(0.2, 0.8, 1),
    "default": SpeechDetection(0.3, 1.0, 2),
    "strict": SpeechDetection(0.45, 1.4, 3),
}
#: A follow-up listen (after a reply that asked a question, or a start_conversation) begins right after the
#: satellite spoke: its first moments are not listened to, and the gate is this much higher until speech starts.
FOLLOW_UP_HOLDOFF_S = 0.5
FOLLOW_UP_THRESHOLD_FACTOR = 1.5
#: A run is a follow-up when the satellite's conversation asked to continue this recently.
FOLLOW_UP_WINDOW_S = 120.0
#: The select option for "listen for no wake word", as HA labels it.
NO_WAKE_WORD = "No wake word"
#: How long the node may take to report its wake words.
WAKE_WORD_TIMEOUT_S = 10.0
#: A satellite that does not answer the wake word request is reconnected after this long, at most
#: WAKE_WORD_RECONNECTS times an hour. echo-dot-assist (hassmic) takes one voice subscriber: when an
#: earlier connection (adoption, before the encryption key) still holds it, this one is ignored, the
#: request goes unanswered and a wake word starts nothing; a fresh connection is heard again.
WAKE_WORD_RECONNECT_S = 60.0
WAKE_WORD_RECONNECTS = 3
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
def _float_env(name: str, default: float, *, zero_ok: bool = False) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise HubError(f"{name} must be a number, not {raw!r}") from exc
    if value < 0 or (value == 0 and not zero_ok):
        raise HubError(f"{name} must be {'0 or more' if zero_ok else 'more than 0'}, not {raw!r}")
    return value


def _flag_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    return default if raw == "" else raw not in ("0", "false", "no", "off")


def _vad_engine_env() -> str:
    raw = (os.getenv("HOMEHUB_VOICE_VAD") or "").strip().lower() or "auto"
    if raw not in ("auto", "energy"):
        raise HubError(f"HOMEHUB_VOICE_VAD must be auto or energy, not {raw!r}")
    return raw


@dataclass
class VoiceSettings:
    """The hub's voice settings, read from the environment once when the hub starts (``current_settings()``)."""

    stt: str = ""
    jarvis_url: str = ""
    language: str = "en"
    tts_voice: str | None = None
    whisper_volume: float = 0.22
    whisper_tts_voice: str | None = None
    max_seconds: float = 15.0
    no_speech_seconds: float = 8.0
    stt_timeout: float = 30.0
    jarvis_timeout: float = 90.0
    continue_conversation: bool = True
    vad_min_rms: float = 40.0
    #: A fixed boost for the audio sent to speech to text, or None for automatic (``InputGain``).
    input_gain: float | None = None
    #: Speak the reply as the gateway writes it (a growing stream), not only after the whole answer.
    stream_replies: bool = True
    #: How long the start of a follow-up listen is not listened to (the end of the reply, the chime).
    followup_holdoff_s: float = FOLLOW_UP_HOLDOFF_S
    #: "auto" adds WebRTC's speech detector to the loudness gate when it is installed; "energy" never does.
    vad_engine: str = "auto"
    #: Proves to the gateway that the room context (voice_context) comes from the hub.
    internal_secret: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> VoiceSettings:
        return cls(
            stt=os.getenv("HOMEHUB_VOICE_STT", "").strip(),
            jarvis_url=os.getenv("HOMEHUB_VOICE_JARVIS_URL", "").strip().rstrip("/"),
            language=os.getenv("HOMEHUB_VOICE_LANGUAGE", "").strip() or "en",
            tts_voice=os.getenv("HOMEHUB_VOICE_TTS_VOICE", "").strip() or None,
            whisper_volume=_whisper_volume_env(),
            whisper_tts_voice=os.getenv("HOMEHUB_VOICE_WHISPER_TTS_VOICE", "").strip() or None,
            max_seconds=_float_env("HOMEHUB_VOICE_MAX_S", 15.0),
            no_speech_seconds=_float_env("HOMEHUB_VOICE_NO_SPEECH_S", 8.0),
            stt_timeout=_float_env("HOMEHUB_VOICE_STT_TIMEOUT_S", 30.0),
            jarvis_timeout=_float_env("HOMEHUB_VOICE_JARVIS_TIMEOUT_S", 90.0),
            continue_conversation=_flag_env("HOMEHUB_VOICE_CONTINUE", True),
            vad_min_rms=_float_env("HOMEHUB_VOICE_VAD_MIN_RMS", 40.0),
            input_gain=_gain_env("HOMEHUB_VOICE_INPUT_GAIN"),
            stream_replies=_flag_env("HOMEHUB_VOICE_STREAM", True),
            followup_holdoff_s=_float_env("HOMEHUB_VOICE_FOLLOWUP_HOLDOFF_S", FOLLOW_UP_HOLDOFF_S, zero_ok=True),
            vad_engine=_vad_engine_env(),
            internal_secret=os.getenv("INTERNAL_SECRET", "").strip(),
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
        if not self.internal_secret:
            out.append("INTERNAL_SECRET is not set: Jarvis still answers, but cannot be told which room the "
                       "satellite is in (the gateway only trusts voice_context from an internal caller)")
        return out

    def public(self) -> dict[str, Any]:
        return {"stt": self.stt or None, "jarvis_url": self.jarvis_url or None, "language": self.language,
                "tts_voice": self.tts_voice, "whisper_volume": self.whisper_volume,
                 "whisper_tts_voice": self.whisper_tts_voice, "max_seconds": self.max_seconds,
                "no_speech_seconds": self.no_speech_seconds, "stt_timeout_s": self.stt_timeout,
                "jarvis_timeout_s": self.jarvis_timeout, "continue_conversation": self.continue_conversation,
                "vad_min_rms": self.vad_min_rms,
                "input_gain": "auto" if self.input_gain is None else self.input_gain,
                "stream_replies": self.stream_replies, "followup_holdoff_s": self.followup_holdoff_s,
                "vad_engine": self.vad_engine,
                "speech_detector": "webrtc+energy" if speech_classifier(self.vad_engine, 2) else "energy",
                "problems": self.problems()}


def _gain_env(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in ("", "auto"):
        return None
    return _float_env(name, 1.0)


def _whisper_volume_env() -> float:
    raw = os.getenv("HOMEHUB_VOICE_WHISPER_VOLUME")
    if raw is None or str(raw).strip() == "":
        return 0.22
    raw = str(raw).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise HubError(f"HOMEHUB_VOICE_WHISPER_VOLUME must be a number, not {raw!r}") from exc
    if not (0.0 < value <= 1.0):
        raise HubError(f"HOMEHUB_VOICE_WHISPER_VOLUME must be > 0 and <= 1, not {raw!r}")
    return value


def _settings_at_startup() -> VoiceSettings | HubError:
    try:
        return VoiceSettings.from_env()
    except HubError as exc:
        log.error("voice settings are invalid, so satellites cannot run: %s", exc)
        return exc


# Settings come from the environment once, when the hub starts; changing one means a restart.
_STARTUP_SETTINGS = _settings_at_startup()


def current_settings() -> VoiceSettings:
    """The voice settings the hub started with; an invalid one raises the error it was found with."""
    if isinstance(_STARTUP_SETTINGS, HubError):
        raise HubError(str(_STARTUP_SETTINGS))
    return _STARTUP_SETTINGS


def _parse_address(value: str) -> tuple[str, int] | None:
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isdigit():
        return None
    return host.strip("[]"), int(port)


# -- end of speech ----------------------------------------------------------------
class EnergyVAD:
    """Speech start and end from the loudness of 30 ms frames, against the room's own noise floor.

    A frame is loud when its RMS is ``ratio`` times the noise floor and at least ``min_rms`` (a
    quiet room still hisses; ``HOMEHUB_VOICE_VAD_MIN_RMS``). A command starts once ``min_speech_s``
    of speech frames arrived within twice that time (a dip between syllables does not reset it; a
    chime, a knock or the tail of the satellite's own reply is shorter) and ends after ``silence_s``
    of frames that are not loud. The floor is the median of the last second of frames that were not
    loud, so a voice never becomes the floor and a person who starts talking at once is still heard;
    it starts at ``min_rms / ratio``.

    The floor also follows the quietest tenth of the last three seconds, whatever they were taken
    for. Learning only from frames under the threshold could never learn a room louder than
    ``min_rms`` (a TV, music): every frame was "speech", the floor stayed at its start, and every
    run listened its whole 15 s (2026-11-17). Words have gaps, so speech does not lift it.

    ``classifier`` (WebRTC's speech detector, ``speech_classifier``) must also call a loud frame
    speech before it counts toward the start, which keeps tones and clatter from starting a command;
    the end is still loudness alone, so a voice the detector doubts is never cut off.
    ``start_factor`` raises the gate for the start only (a follow-up listen right after a reply).
    """

    FRAME_MS = 30
    FLOOR_FRAMES = 33  # one second of non-speech frames
    RECENT_FRAMES = 100  # three seconds of all frames

    def __init__(self, silence_s: float, *, rate: int = RATE, ratio: float = 2.5, min_rms: float = 250.0,
                 min_speech_s: float = 0.3, start_factor: float = 1.0,
                 classifier: Callable[[bytes], bool] | None = None) -> None:
        self.frame_bytes = rate * WIDTH * self.FRAME_MS // 1000
        self.silence_frames = max(1, math.ceil(silence_s * 1000 / self.FRAME_MS))
        self.ratio, self.min_rms, self.start_factor = ratio, min_rms, start_factor
        self.start_frames = max(1, math.ceil(min_speech_s * 1000 / self.FRAME_MS))
        self.classifier = classifier
        # Within twice that time: the detector misses some frames of real speech, and syllables have gaps.
        self._window: deque[bool] = deque(maxlen=2 * self.start_frames)
        self._quiet_levels: deque[float] = deque([min_rms / ratio] * self.FLOOR_FRAMES, maxlen=self.FLOOR_FRAMES)
        self._recent: deque[float] = deque(maxlen=self.RECENT_FRAMES)
        self._buf = b""
        self._quiet = 0
        self.in_speech = False
        self.speech_frames = 0
        self.frames = 0
        self.rejected_frames = 0  # loud frames the classifier said were not speech
        self.peak_rms = 0.0

    @property
    def engine(self) -> str:
        return "webrtc+energy" if self.classifier is not None else "energy"

    @property
    def floor(self) -> float:
        quiet = statistics.median(self._quiet_levels)
        if len(self._recent) < self.FLOOR_FRAMES:
            return quiet
        room = sorted(self._recent)[len(self._recent) // 10]
        return max(quiet, room)

    def heard(self) -> str:
        """What arrived, for a "no speech" error: none at all and too quiet need different fixes."""
        if not self.frames:
            return "no audio arrived from the satellite"
        out = (f"{self.frames * self.FRAME_MS / 1000:.1f} s of audio arrived; the loudest moment was "
               f"{self.peak_rms:.0f} RMS, speech needs {self.start_threshold():.0f} "
               f"(HOMEHUB_VOICE_VAD_MIN_RMS, or the satellite's volume multiplier)")
        if self.rejected_frames:
            out += f"; {self.rejected_frames * self.FRAME_MS / 1000:.1f} s of loud sound did not sound like speech"
        return out

    def threshold(self) -> float:
        return max(self.min_rms, self.floor * self.ratio)

    def start_threshold(self) -> float:
        return self.threshold() * self.start_factor

    @staticmethod
    def rms(frame: bytes) -> float:
        samples = array.array("h")
        samples.frombytes(frame[: len(frame) - len(frame) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    def _is_speech(self, frame: bytes) -> bool:
        classifier = self.classifier
        if classifier is None:
            return True
        try:
            return bool(classifier(frame))
        except Exception as exc:  # a detector that fails must not stop the satellite hearing anyone
            log.warning("the speech detector failed (%s); using loudness alone", exc)
            self.classifier = None
            return True

    def process(self, pcm: bytes) -> list[str]:
        """Feed audio; returns the edges it crossed, ``"start"`` and/or ``"end"``, in order."""
        self._buf += pcm
        events: list[str] = []
        while len(self._buf) >= self.frame_bytes:
            frame, self._buf = self._buf[: self.frame_bytes], self._buf[self.frame_bytes:]
            level = self.rms(frame)
            self._recent.append(level)
            self.frames += 1
            self.peak_rms = max(self.peak_rms, level)
            loud = level >= self.threshold()
            if not loud:
                self._quiet_levels.append(level)
            if not self.in_speech:
                speech = level >= self.start_threshold() and self._is_speech(frame)
                if level >= self.start_threshold() and not speech:
                    self.rejected_frames += 1
                self._window.append(speech)
                heard = sum(self._window)
                if heard >= self.start_frames:
                    self.in_speech, self._quiet = True, 0
                    self.speech_frames += heard
                    self._window.clear()
                    events.append("start")
            elif loud:
                self._quiet = 0
                self.speech_frames += 1
            else:
                self._quiet += 1
                if self._quiet >= self.silence_frames:
                    events.append("end")
                    self.in_speech = False
                    break
        return events


_webrtc_missing_logged = False


def speech_classifier(engine: str, mode: int) -> Callable[[bytes], bool] | None:
    """WebRTC's speech detector for 30 ms frames of 16 kHz audio, or None (loudness alone).

    None when ``engine`` is ``"energy"`` or ``webrtcvad`` is not installed (logged once, so the
    fallback is seen). Silero would be better at telling a voice from a TV, but it needs a model file
    the hub image does not ship; WebRTC's detector is a few kilobytes of C with no model to fetch.
    """
    global _webrtc_missing_logged
    if engine == "energy":
        return None
    try:
        import webrtcvad  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        if not _webrtc_missing_logged:
            _webrtc_missing_logged = True
            log.warning("webrtcvad is not installed: voice satellites detect speech by loudness alone")
        return None
    vad = webrtcvad.Vad(max(0, min(3, int(mode))))
    return lambda frame: bool(vad.is_speech(frame, RATE))


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


class Reply(str):
    """Jarvis's answer, with the voice to say it in when the gateway chose one:
    the recognised speaker's own, else the satellite owner's (their settings).
    None is Jarvis's voice. A plain ``str`` subclass, so a reply is still text."""

    voice: str | None = None
    #: Streamed replies: how many sentences were already said, and what is left to say after them.
    streamed: int = 0
    rest: str | None = None


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


def _chat_headers(settings: VoiceSettings) -> dict[str, str]:
    return {"X-Internal-Secret": settings.internal_secret} if settings.internal_secret else {}


def _reply_from_answer(status: int, data: Any) -> Reply:
    """The reply in a whole (not streamed) gateway answer, or the error it is.

    One failure is said aloud rather than chimed: the gateway's "my thinking brain
    is offline" answer (``offline`` set, 503). It is written for a person to hear,
    and says what still works.
    """
    if isinstance(data, dict) and data.get("offline") and chat_reply_text(data):
        out = Reply(chat_reply_text(data) or "")
        out.voice = _voice_of(data)
        return out
    if status != 200 or (isinstance(data, dict) and str(data.get("status") or "").upper() == "ERROR"):
        detail = (data.get("message") or data.get("detail") or data.get("error")) if isinstance(data, dict) else data
        raise PipelineError("intent-failed", f"Jarvis could not answer (HTTP {status}): {detail}")
    reply = chat_reply_text(data)
    if not reply:
        raise PipelineError("intent-failed", "Jarvis answered with no text")
    out = Reply(reply)
    out.voice = _voice_of(data)
    return out


def _voice_of(data: Any) -> str | None:
    voice = data.get("voice") if isinstance(data, dict) else None
    return voice.strip() if isinstance(voice, str) and voice.strip() else None


async def ask_jarvis(settings: VoiceSettings, text: str, context: dict[str, Any]) -> str:
    """One conversation turn, through the same ``/api/chat`` HA's conversation agent used."""
    url = f"{settings.jarvis_url}/api/chat"
    body = {"query": text, "stream": False, "voice_context": context}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=settings.jarvis_timeout)) as s, \
                s.post(url, json=body, headers=_chat_headers(settings)) as r:
            data = await r.json(content_type=None)
            status = r.status
    except TimeoutError as exc:
        raise PipelineError("intent-timeout", f"Jarvis did not answer within {settings.jarvis_timeout:g} s") from exc
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        raise PipelineError("intent-failed", f"Jarvis at {settings.jarvis_url} is unreachable: {exc}") from exc
    return _reply_from_answer(status, data)


#: Called with each sentence the gateway streams and the voice to say it in.
OnSentence = Callable[[str, str | None], Awaitable[None]]


async def read_speech_stream(lines: Any, on_sentence: OnSentence) -> Reply:
    """A ``speech_stream`` answer (NDJSON lines, as bytes or text): each sentence to ``on_sentence``.

    Returns the whole reply with ``rest`` (what is left to say after the streamed sentences) and
    ``streamed`` (how many were). An answer that streamed no sentences (a shortcut answered at once,
    a device command, a gateway without speech streaming) returns its text with ``streamed`` 0.
    """
    content: list[str] = []
    voice: str | None = None
    streamed = 0
    async for raw in lines:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        voice = _voice_of(obj) or voice
        if obj.get("error"):
            raise PipelineError("intent-failed", f"Jarvis could not answer: {obj['error']}")
        text = chat_reply_text(obj) or ""
        if obj.get("speech") and text:
            streamed += 1
            await on_sentence(text, voice)
            continue
        if not obj.get("done"):
            if text:
                content.append(text)
            continue
        if text.startswith("[ERROR]:"):
            raise PipelineError("intent-failed", f"Jarvis could not answer: {text[8:].strip()}")
        if text:
            content.append(text)
        whole = str(obj.get("reply") or "").strip() or "".join(content).strip()
        out = Reply(whole)
        out.voice = voice
        out.streamed = streamed
        out.rest = str(obj["speech_rest"]).strip() if "speech_rest" in obj else (None if streamed else whole)
        if not whole and not streamed:
            raise PipelineError("intent-failed", "Jarvis answered with no text")
        return out
    raise PipelineError("intent-failed", "Jarvis's answer ended before it was finished")


async def ask_jarvis_stream(settings: VoiceSettings, text: str, context: dict[str, Any],
                            on_sentence: OnSentence) -> Reply:
    """One conversation turn with the reply streamed a sentence at a time (the gateway's ``speech_stream``).

    The gateway sends only sentences that are safe to say before the answer is finished
    (``services/gateway/speech_stream.py``); a shortcut answer comes back whole, as JSON.
    """
    url = f"{settings.jarvis_url}/api/chat"
    body = {"query": text, "stream": True, "speech_stream": True, "voice_context": context}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=settings.jarvis_timeout)) as s, \
                s.post(url, json=body, headers=_chat_headers(settings)) as r:
            kind = (r.headers.get("Content-Type") or "").lower()
            if r.status != 200 or ("json" in kind and "ndjson" not in kind):
                return _reply_from_answer(r.status, await r.json(content_type=None))
            return await read_speech_stream(r.content, on_sentence)
    except TimeoutError as exc:
        raise PipelineError("intent-timeout", f"Jarvis did not answer within {settings.jarvis_timeout:g} s") from exc
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        raise PipelineError("intent-failed", f"Jarvis at {settings.jarvis_url} is unreachable: {exc}") from exc


async def make_speech(text: str, voice: str | None) -> str:
    from services.homehub import tts

    return await tts.make_clip(text, voice)


async def synthesize_speech(text: str, voice: str | None) -> bytes:
    from services.homehub import tts

    return await tts.synthesize(text, voice)


def open_reply_stream() -> tuple[str, Any]:
    from services.homehub import tts

    return tts.open_stream()


@dataclass
class Backends:
    """What a run talks to; tests swap these for fakes.

    ``ask_stream``, ``synth`` and ``open_stream`` stream a reply as it is written; with any of them
    unset a run waits for the whole answer, as before.
    """

    open_stt: Callable[[VoiceSettings], Any] = lambda s: WyomingSTT(s.stt, s.language)
    ask: Callable[[VoiceSettings, str, dict[str, Any]], Awaitable[str]] = ask_jarvis
    speak: Callable[[str, str | None], Awaitable[str]] = make_speech
    clip_audio: Callable[[str], bytes | None] = field(default=lambda url: _clip_bytes(url))
    ask_stream: Callable[[VoiceSettings, str, dict[str, Any], OnSentence], Awaitable[Reply]] | None = None
    synth: Callable[[str, str | None], Awaitable[bytes]] | None = None
    open_stream: Callable[[], tuple[str, Any]] | None = None


def _clip_bytes(url: str) -> bytes | None:
    from services.homehub import tts

    token = url.rsplit("/", 1)[-1].removesuffix(".wav")
    return tts.clip(token)


BACKENDS = Backends(ask_stream=ask_jarvis_stream, synth=synthesize_speech, open_stream=open_reply_stream)


# -- audio helpers ------------------------------------------------------------------
def _gain(pcm: bytes, multiplier: float) -> bytes:
    if not pcm or abs(multiplier - 1.0) < 1e-3:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    for i, s in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(s * multiplier)))
    return samples.tobytes()


class InputGain:
    """Brings quiet microphone audio up to a level speech to text hears well, without clipping.

    A far or soft voice reaches the hub at a few dozen RMS (about -57 dBFS), where Whisper misses
    words. Automatic: the gain aims the loudest recent audio at ``TARGET_RMS``; it drops at once when
    the audio gets louder and rises back slowly (``RELEASE_PER_S``), never past ``MAX_GAIN`` and never
    so far that a sample clips. Fixed (``HOMEHUB_VOICE_INPUT_GAIN``): that multiplier, clipped.
    Only speech to text gets the boost: the loudness gate compares speech with the room's noise, and
    a gain that moves during speech would squash that ratio.
    """

    TARGET_RMS = 3000.0  # about -20 dBFS, a clear voice close to a microphone
    MAX_GAIN = 16.0  # +24 dB; more mostly lifts the room's hiss
    RELEASE_PER_S = 0.7  # the level estimate falls ~30% a second once the audio gets quieter
    PEAK_LIMIT = 32000

    def __init__(self, fixed: float | None) -> None:
        self.fixed = fixed
        self.level = 0.0
        self.gain = fixed if fixed is not None else 1.0
        self.max_applied = self.gain

    def apply(self, pcm: bytes) -> bytes:
        samples = array.array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        if not samples:
            return pcm
        peak = max(abs(s) for s in samples) or 1
        if self.fixed is not None:
            gain = self.fixed
        else:
            rms = math.sqrt(sum(s * s for s in samples) / len(samples))
            self.level = max(rms, self.level * self.RELEASE_PER_S ** (len(samples) / RATE))
            gain = max(1.0, min(self.MAX_GAIN, self.TARGET_RMS / max(self.level, 1.0)))
            gain = max(1.0, min(gain, self.PEAK_LIMIT / peak))
        self.gain = gain
        self.max_applied = max(self.max_applied, gain)
        if abs(gain - 1.0) < 1e-3:
            return pcm
        for i, s in enumerate(samples):
            samples[i] = max(-32768, min(32767, int(s * gain)))
        if sys.byteorder == "big":
            samples.byteswap()
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
    heard: str | None = None  # what arrived when the loudness gate heard no speech
    levels: dict[str, Any] | None = None  # microphone levels, to tune the gate and the boost from
    run_end_sent: bool = False
    aborted: bool = False
    #: A follow-up listen: the satellite had just spoken (a reply that asked a question, start_conversation).
    follow_up: bool = False
    #: How the reply reached the satellite: "stream" (sentence by sentence as written) or "whole".
    reply_mode: str | None = None
    #: Sentences spoken before the whole answer was in.
    streamed_sentences: int = 0
    #: Whether the person spoke quietly (whispered) for this run.
    whispered: bool = False
    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.monotonic())

    def timings_ms(self) -> dict[str, int]:
        """Each stage's time; ``first_audio`` is from the end of the command to the reply's first audio,
        what the person waits through, and ``total`` the whole run."""
        m, out = self.marks, {}
        spans = (("listen", "listen", "listened"), ("stt", "listened", "transcribed"),
                 ("intent", "intent", "answered"), ("tts", "tts", "spoken"), ("first_audio", "listened", "first_audio"))
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
                "heard": self.heard, "levels": self.levels, "follow_up": self.follow_up,
                "reply_mode": self.reply_mode, "streamed_sentences": self.streamed_sentences,
                 "whispered": self.whispered,
                "timings_ms": self.timings_ms()}


class _StreamSpeaker:
    """Speaks a reply's sentences into one growing stream (``tts.ClipStream``) as the gateway sends them.

    Sentences are spoken in order, one synthesis at a time, while the gateway keeps writing; each one's
    audio joins the stream the moment it is made. The first one tells the node to start playing
    (``INTENT_PROGRESS`` with ``tts_start_streaming``, what HA sends with streaming TTS), so the person
    hears the answer's start while the rest is still being written.
    """

    def __init__(self, satellite: SatelliteVoice, run: Run, clip_stream: Any) -> None:
        self.satellite, self.run, self.stream = satellite, run, clip_stream
        self.node_url = ""
        self.default_voice: str | None = None
        self.sentences = 0  # given by the gateway (the rest at the end is not counted)
        self._queue: asyncio.Queue[tuple[str, str | None] | None] = asyncio.Queue()
        self._started = False
        self._task = asyncio.get_running_loop().create_task(self._work(), name=f"voice:{satellite.name}:speak")

    async def say(self, text: str, voice: str | None) -> None:
        """One sentence from the gateway (``on_sentence``), or the rest of the reply at the end."""
        if self._task.done():
            self._task.result()  # synthesis already failed: say so now, not after the whole answer
        if self.run.reply_mode is None:
            self.sentences += 1
        await self._queue.put((text, voice or self.default_voice))

    async def _work(self) -> None:
        synth = BACKENDS.synth
        assert synth is not None
        while (item := await self._queue.get()) is not None:
            text, voice = item
            try:
                audio = await synth(text, voice)
                self.stream.append(audio)
            except (HubError, OSError) as exc:
                raise PipelineError("tts-failed", f"Jarvis's voice could not speak the reply: {exc}") from exc
            if not self._started:
                self._started = True
                self.run.mark("first_audio")
                self.satellite._event(self.run, VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_PROGRESS,
                                      {"tts_start_streaming": "1"})
                self.satellite._publish_satellite("responding")

    async def finish(self) -> None:
        """Every sentence said into the stream, and the stream ended (the node's player then finishes)."""
        await self._queue.put(None)
        try:
            await self._task
        finally:
            self.stream.close()

    async def abort(self) -> None:
        if not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self.stream.close()


#: Runs kept per satellite, newest first, in the hub's store so a restart or deploy keeps them.
HISTORY_KEEP = 20
HISTORY_KV = "voice_runs:{entry_id}"


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
        #: Finished runs as their public records (no audio, no transcript), newest first; kept in the store.
        self.history: list[dict[str, Any]] = []
        self.last_error: dict[str, Any] | None = None
        self._unsub: Callable[[], None] | None = None
        self._run: Run | None = None
        self._task: asyncio.Task | None = None
        self._audio: asyncio.Queue | None = None
        self._udp: asyncio.DatagramTransport | None = None
        self._udp_proto: _UDPAudio | None = None
        self._audio_settings: Any = None
        self._pending: tuple[float, dict[str, Any]] | None = None
        #: The conversation the last reply asked to continue, and when that run ended: the node's next run
        #: in it is a follow-up listen that starts right after the satellite spoke.
        self._continued: tuple[str, float] | None = None
        self._tasks: set[asyncio.Task] = set()
        #: The node's own wake words: id -> phrase, what is active, and why they could not be read.
        self.wake_words: dict[str, str] = {}
        self.active_wake_words: list[str] = []
        self.max_active_wake_words = 0
        self.wake_word_error: str | None = None
        #: When this satellite was reconnected for an unanswered wake word request (the last hour's).
        self._wake_reconnects: list[float] = []
        self.log = logging.getLogger(f"homehub.voice.{driver.device_name or driver.host}")
        self._restore_history()

    # -- run history ----------------------------------------------------------------
    @property
    def _history_key(self) -> str | None:
        entry = getattr(self.driver, "entry", None)
        return HISTORY_KV.format(entry_id=entry.entry_id) if entry is not None else None

    def _restore_history(self) -> None:
        store, key = getattr(self.hub, "store", None), self._history_key
        if store is None or key is None:
            return
        doc = store.get_kv(key) or {}
        self.runs = int(doc.get("runs") or 0)
        self.history = list(doc.get("history") or [])[:HISTORY_KEEP]

    def _record(self, run: Run) -> None:
        self.history.insert(0, run.public())
        del self.history[HISTORY_KEEP:]
        store, key = getattr(self.hub, "store", None), self._history_key
        if store is None or key is None:
            return
        try:
            store.set_kv(key, {"runs": self.runs, "history": self.history})
        except Exception as exc:  # the run happened; only its record could not be kept
            self.log.warning("voice satellite %s: run %s could not be saved to the store: %s",
                             self.name, run.id, exc)

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

    def _select_state(self, suffix: str, options: dict[str, Any]) -> str:
        sel = self._local(suffix)
        st = self.hub.states.get(sel.entity_id) if sel is not None else None
        state = str(st.state) if st is not None else ""
        return state if state in options else "default"

    def silence_seconds(self) -> float:
        """The satellite's "Finished speaking detection": seconds of silence that end a command."""
        return FINISHED_SPEAKING_S[self._select_state("vad_sensitivity", FINISHED_SPEAKING_S)]

    def speech_detection(self) -> SpeechDetection:
        """The satellite's "Speech detection": how much sound it takes to start a command."""
        return SPEECH_DETECTION[self._select_state("speech_detection", SPEECH_DETECTION)]

    # -- the satellite's own entities (HA's esphome integration made them) -------------------------
    #: unique_id suffix -> (domain, entity name, entity category); the assist_satellite is the device's main entity.
    LOCAL_ENTITIES: ClassVar[dict[str, tuple[str, str | None, str | None]]] = {
        "assist_satellite": ("assist_satellite", None, None),
        "wake_word": ("select", "Wake word", "config"),
        "wake_word_2": ("select", "Wake word 2", "config"),
        "vad_sensitivity": ("select", "Finished speaking detection", "config"),
        "speech_detection": ("select", "Speech detection", "config"),
    }
    #: The selects the hub keeps (their options), as against the node's own wake word.
    SETTING_SELECTS: ClassVar[dict[str, dict[str, Any]]] = {
        "vad_sensitivity": FINISHED_SPEAKING_S, "speech_detection": SPEECH_DETECTION}

    def ensure_entities(self, device_info: Any) -> list[EntityEntry]:
        """Register the satellite's entities a node adopted on the hub (not imported from HA) has none of.

        HA's esphome integration created them for every voice satellite: the ``assist_satellite`` (the
        target execution finds for announcements, intercom and timers, by its area), the wake word and
        "Finished speaking detection" selects; the hub adds "Speech detection". Ids follow HA's
        ``{MAC}-{key}``, so an imported satellite keeps its own and nothing is registered twice; an
        entity a person disabled stays disabled.
        """
        from services.homehub.discovery import norm_mac

        mac = norm_mac(str(getattr(device_info, "mac_address", "") or ""))
        if mac is None:
            return []
        mac_upper = ":".join(mac[i:i + 2] for i in range(0, 12, 2)).upper()
        connections = [["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]]
        title = (getattr(device_info, "friendly_name", "") or getattr(device_info, "name", "")
                 or self.driver.entry.title)
        device = None
        added = []
        local = getattr(self.driver, "local_entities", None)
        for key, (domain, name, category) in self.LOCAL_ENTITIES.items():
            uid = f"{mac_upper}-{key}"
            ent = self.driver.registry_entity(domain, uid)
            if ent is None:
                if device is None:
                    device = self.driver.ensure_device(title, connections=connections)
                info = {"entity_category": category} if category else {}
                ent = self.driver.ensure_entity(domain, uid, name, device=device, **info)
                added.append(ent)
            if isinstance(local, list) and all(e.entity_id != ent.entity_id for e in local):
                local.append(ent)
        if added:
            self.log.info("voice satellite %s: registered %s", self.name, ", ".join(e.entity_id for e in added))
        return added

    def select_option(self, entity: EntityEntry, option: str) -> None:
        """A choice on one of the hub's satellite settings ("Finished speaking detection", "Speech detection")."""
        key = (entity.unique_id or "").rsplit("-", 1)[-1]
        options = self.SETTING_SELECTS.get(key)
        if options is None:
            raise HubError(f"{entity.entity_id} is not one of the satellite's settings")
        if option not in options:
            raise HubError(f"{option!r} is not an option of {entity.entity_id}: {', '.join(options)}")
        self.hub.publish(entity, option, {"options": list(options)})
        self.log.info("voice satellite %s: %s set to %s", self.name, entity.entity_id, option)

    # -- connection ------------------------------------------------------------------
    def attach(self, client: Any, device_info: Any) -> bool:
        """Subscribe on a new connection when the node is a satellite and its pipeline is on."""
        self._cancel_run("the node reconnected")
        self._unsubscribe()
        self.client = client
        self.flags = int(getattr(device_info, "voice_assistant_feature_flags", 0) or 0)
        if not self.is_satellite:
            return False
        try:
            self.ensure_entities(device_info)
        except Exception:  # its entities are a convenience; the satellite must still be heard
            self.log.exception("voice satellite %s: registering its entities failed", self.name)
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
        self._spawn(self.load_wake_words(), "wake words")
        return True

    def _spawn(self, coro: Any, what: str) -> None:
        task = asyncio.get_running_loop().create_task(coro, name=f"voice:{self.name}:{what}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- wake words (HA's "Wake word" select) ----------------------------------------------------
    async def load_wake_words(self) -> None:
        """Read which wake words the node offers and which it listens for, and publish the select.

        The node keeps its active wake word itself (HA used to set it); a node that listens
        for a phrase nobody says looks like a broken wake word, so this is shown, not assumed.
        """
        client = self.client
        if client is None:
            return
        try:
            cfg = await client.get_voice_assistant_configuration(WAKE_WORD_TIMEOUT_S)
        except Exception as exc:
            self.wake_word_error = f"the node did not report its wake words: {exc}"
            self.log.warning("voice satellite %s: %s", self.name, self.wake_word_error)
            self._publish_wake_word_select()
            self._spawn(self._reconnect_for_wake_words(client), "wake word reconnect")
            return
        self.wake_words = {str(w.id): str(w.wake_word) for w in (cfg.available_wake_words or [])}
        self.active_wake_words = [str(w) for w in (cfg.active_wake_words or [])]
        self.max_active_wake_words = int(getattr(cfg, "max_active_wake_words", 0) or 0)
        self.wake_word_error = None
        active = [self.wake_words.get(w, w) for w in self.active_wake_words]
        if self.wake_words:
            self.log.info("voice satellite %s: listens for %s (offers %s)", self.name,
                          ", ".join(repr(a) for a in active) or "no wake word",
                          ", ".join(repr(w) for w in self.wake_words.values()))
        if not self.wake_words:
            # Firmware whose wake word model is built in (micro_wake_word without selectable models) lists
            # none here; it still wakes on its own phrase, which only its config or log names.
            self.log.info("voice satellite %s: its wake word is fixed in its firmware (it lists none to choose)",
                          self.name)
        elif not active:
            self.log.warning("voice satellite %s: none of its wake words is active, so it will not wake; choose "
                             "one with its Wake word select", self.name)
        self._publish_wake_word_select()

    async def _reconnect_for_wake_words(self, client: Any) -> None:
        """Drop the connection that got no answer, so the driver reconnects and asks again (bounded)."""
        now = time.monotonic()
        self._wake_reconnects = [t for t in self._wake_reconnects if now - t < 3600]
        if len(self._wake_reconnects) >= WAKE_WORD_RECONNECTS:
            self.log.warning("voice satellite %s: still no wake words after %d reconnects this hour; leaving it",
                             self.name, WAKE_WORD_RECONNECTS)
            return
        await asyncio.sleep(WAKE_WORD_RECONNECT_S)
        if self.client is not client or not self.wake_word_error:
            return  # it reconnected meanwhile, or the answer came
        self._wake_reconnects.append(time.monotonic())
        self.log.warning("voice satellite %s: reconnecting, since a node that ignores the wake word request "
                         "also ignores its wake word (attempt %d of %d this hour)",
                         self.name, len(self._wake_reconnects), WAKE_WORD_RECONNECTS)
        with contextlib.suppress(Exception):
            await client.disconnect()

    def _publish_wake_word_select(self) -> None:
        sel = self._local("wake_word")
        if sel is None:
            return
        if self.wake_word_error or not self.wake_words:
            self.hub.publish(sel, UNAVAILABLE, {"reason": self.wake_word_error or (
                "the wake word is fixed in this satellite's firmware; there is none to choose")})
            return
        options = {"options": [*self.wake_words.values(), NO_WAKE_WORD]}
        active = [self.wake_words[w] for w in self.active_wake_words if w in self.wake_words]
        self.hub.publish(sel, active[0] if active else NO_WAKE_WORD, options)
        second = self._local("wake_word_2")
        if second is not None:
            if self.max_active_wake_words < 2:
                self.hub.publish(second, UNAVAILABLE, {"reason": "this satellite listens for one wake word at a time"})
            else:
                self.hub.publish(second, active[1] if len(active) > 1 else NO_WAKE_WORD, options)

    async def set_wake_word(self, phrase: str, slot: int = 0) -> None:
        """Make the node listen for ``phrase`` (one of its own wake words), or none.

        ``slot`` 0 is the "Wake word" select, 1 is "Wake word 2": a satellite that listens for two at once
        (``max_active_wake_words`` >= 2, hassmic's Echos) hears both, each starting its own run. The other
        slot's word is kept; picking the same word in both leaves one.
        """
        if self.client is None:
            raise DeviceUnavailable(f"ESPHome {self.name} is not connected")
        if slot and self.max_active_wake_words < 2:
            raise HubError(f"{self.name} listens for one wake word at a time")
        wid = None
        if phrase != NO_WAKE_WORD:
            wid = next((i for i, w in self.wake_words.items() if w == phrase), None)
            if wid is None:
                raise HubError(f"{phrase!r} is not one of {self.name}'s wake words: {list(self.wake_words.values())}")
        slots = [w for w in self.active_wake_words if w in self.wake_words][:2]
        slots += [None] * (2 - len(slots))
        slots[slot] = wid
        ids: list[str] = []
        for w in slots:
            if w is not None and w not in ids:
                ids.append(w)
        await self.client.set_voice_assistant_configuration(ids)
        self.log.info("voice satellite %s: wake word%s set to %r", self.name, " 2" if slot else "", phrase)
        await self.load_wake_words()

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
        """The hub's satellite settings ("Finished speaking detection", "Speech detection"), as last chosen."""
        for key, options in self.SETTING_SELECTS.items():
            sel = self._local(key)
            if sel is None:
                continue
            cur = self.hub.states.get(sel.entity_id)
            state = cur.state if cur is not None and cur.state in options else "default"
            self.hub.publish(sel, state, {"options": list(options)})

    # -- the node's requests ------------------------------------------------------------
    async def _handle_start(self, conversation_id: str, flags: int, audio_settings: Any,
                            wake_word_phrase: str | None, *_: Any) -> int | None:
        self._cancel_run("a new run started")
        run = Run(id=secrets.token_hex(4), conversation_id=conversation_id or secrets.token_hex(8),
                  wake_word=wake_word_phrase, follow_up=self._is_follow_up(conversation_id))
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

    def _is_follow_up(self, conversation_id: str | None) -> bool:
        """The node listens again right after speaking: a reply asked a question, or start_conversation spoke."""
        now = time.monotonic()
        if self._pending is not None and now - self._pending[0] <= PENDING_CONTEXT_S:
            return True
        continued, self._continued = self._continued, None
        return (continued is not None and bool(conversation_id) and continued[0] == conversation_id
                and now - continued[1] <= FOLLOW_UP_WINDOW_S)

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
        speaker: _StreamSpeaker | None = None
        try:
            stream = self._open_reply_stream()
            # HA's RUN_START names the URL of the reply's growing stream; the node plays it when told the
            # first audio is in (INTENT_PROGRESS with tts_start_streaming).
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START,
                        {"url": stream[1]} if stream is not None else {})
            if stream is not None:
                speaker = _StreamSpeaker(self, run, stream[0])
                speaker.node_url = stream[1]
            if flags & int(VoiceAssistantCommandFlag.USE_WAKE_WORD):
                raise PipelineError("wake-engine-missing", "The hub has no wake word engine; set the satellite's "
                                    "wake word engine location to On device")
            try:
                settings = current_settings()
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
            run.whispered = self._is_whispered()
            try:
                async with asyncio.timeout(settings.stt_timeout):
                    text = await stt.finish()
            except TimeoutError as exc:
                raise PipelineError("stt-timeout", f"Speech to text gave no transcript within "
                                    f"{settings.stt_timeout:g} s") from exc
            run.mark("transcribed")
            if not text:
                raise PipelineError("stt-no-text-recognized", "No words were recognised"
                                    + (f": {run.heard}" if run.heard else ""))
            self.log.debug("voice satellite %s run %s heard: %s", self.name, run.id, text)
            self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_STT_END, {"text": text})

            whisper_voice = settings.whisper_tts_voice if getattr(run, "whispered", False) and settings.whisper_tts_voice else None
            async with self._whisper_volume(run, settings):
                self._publish_satellite("processing")
                self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_START, {})
                run.mark("intent")
                try:
                    async with asyncio.timeout(settings.jarvis_timeout + JARVIS_GUARD_S):
                        if speaker is not None and BACKENDS.ask_stream is not None:
                            speaker.default_voice = whisper_voice or settings.tts_voice
                            async def _on_sentence(text: str, voice: str | None) -> None:
                                if whisper_voice is not None:
                                    voice = whisper_voice
                                await speaker.say(text, voice)
                            reply = await BACKENDS.ask_stream(settings, text, self._context(run), _on_sentence)
                        else:
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
                if speaker is not None and speaker.sentences:
                    await self._finish_streamed_reply(run, reply, speaker, settings)
                else:
                    if speaker is not None:
                        await speaker.abort()  # nothing was streamed (a shortcut answer): the whole reply, as before
                    await self._speak_whole_reply(run, settings, reply)
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
            if speaker is not None:
                await speaker.abort()
            if stt is not None:
                with contextlib.suppress(Exception):
                    await stt.close()
        self._finish(run, settings)

    # -- the reply -----------------------------------------------------------------------
    def _open_reply_stream(self) -> tuple[Any, str] | None:
        """A growing stream for this run's reply, and the URL the node plays it from; None to wait for it whole.

        Only for a node that plays replies by URL (its media player). A node with its own speaker
        (``SPEAKER``) is sent the audio over the API after TTS_END, as before: its firmware plays
        API audio only once the TTS stage has ended.
        """
        try:
            settings = current_settings()
        except HubError:
            return None
        if (not settings.stream_replies or settings.problems() or BACKENDS.ask_stream is None
                or BACKENDS.synth is None or BACKENDS.open_stream is None
                or self.flags & int(VoiceAssistantFeature.SPEAKER)):
            return None
        try:
            url, clip_stream = BACKENDS.open_stream()
        except HubError as exc:
            self.log.warning("voice satellite %s: the reply cannot be streamed (%s); it is spoken whole", self.name, exc)
            return None
        return clip_stream, self._node_url(url)

    async def _finish_streamed_reply(self, run: Run, reply: str, speaker: _StreamSpeaker, settings: Any = None) -> None:
        """The first sentences are playing already: say the rest into the same stream, then end it."""
        run.reply_mode = "stream"
        run.streamed_sentences = speaker.sentences
        rest = getattr(reply, "rest", None)
        if rest:
            voice = getattr(reply, "voice", None)
            wtv = None
            if settings is not None:
                wtv = getattr(settings, "whisper_tts_voice", None)
            if wtv is None and getattr(run, "whispered", False):
                try:
                    wtv = current_settings().whisper_tts_voice
                except HubError:
                    wtv = None
            voice = (wtv if getattr(run, "whispered", False) and wtv
                     else voice or (settings.tts_voice if settings is not None else None))
            await speaker.say(rest, voice)
        await speaker.finish()
        run.mark("spoken")
        # Firmware that started the stream on INTENT_PROGRESS does not play this again; older firmware
        # that ignores INTENT_PROGRESS plays it now, whole.
        self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END, {"url": speaker.node_url})

    async def _speak_whole_reply(self, run: Run, settings: VoiceSettings, reply: str) -> None:
        run.reply_mode = "whole"
        try:
            # The person's own voice when the gateway chose one, else the hub's
            # setting, else (None) Jarvis's.
            voice = getattr(reply, "voice", None)
            voice = (settings.whisper_tts_voice if getattr(run, "whispered", False) and settings.whisper_tts_voice
                     else voice or settings.tts_voice)
            url = await BACKENDS.speak(reply, voice)
        except (HubError, OSError) as exc:
            raise PipelineError("tts-failed", f"Jarvis's voice could not speak the reply: {exc}") from exc
        run.mark("spoken")
        run.mark("first_audio")
        # The node fetches the reply in its own announcement format (the hub's ffmpeg proxy), never the
        # 22050 Hz original it would have to resample while it plays; streamed audio uses the original.
        self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END, {"url": self._node_url(url)})
        if self.flags & int(VoiceAssistantFeature.SPEAKER):
            await self._stream_reply(run, url)

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
        if run.outcome == "ok" and run.continue_conversation:
            self._continued = (run.conversation_id, time.monotonic())
        self._record(run)
        t = run.timings_ms()
        reply = (f"; reply {run.reply_mode}" + (f" ({run.streamed_sentences} sentences early)"
                                                 if run.streamed_sentences else "")) if run.reply_mode else ""
        self.log.info("voice satellite %s run %s: %s%s; ended by %s%s%s; %s", self.name, run.id, run.outcome,
                      f" ({run.error['code']})" if run.error else "", run.ended_by or "-",
                      "; follow-up" if run.follow_up else "", reply,
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
        detection = self.speech_detection()
        factor = detection.threshold_factor * (FOLLOW_UP_THRESHOLD_FACTOR if run.follow_up else 1.0)
        vad = EnergyVAD(self.silence_seconds(), min_rms=settings.vad_min_rms, min_speech_s=detection.min_speech_s,
                        start_factor=factor, classifier=speech_classifier(settings.vad_engine, detection.webrtc_mode))
        # A follow-up starts as the satellite stops speaking: its first moments (the reply's tail, the
        # listening chime) go to neither the gate nor speech to text.
        holdoff = int(settings.followup_holdoff_s * BYTES_PER_S) if run.follow_up else 0
        holdoff -= holdoff % 2
        loop = asyncio.get_running_loop()
        started = loop.time()
        gain = float(getattr(self._audio_settings, "volume_multiplier", 1.0) or 1.0)
        boost = InputGain(settings.input_gain)
        while True:
            now = loop.time()
            limit = settings.max_seconds if vad.speech_frames else settings.no_speech_seconds
            remaining = started + limit - now
            if remaining <= 0:
                # Quiet speech (far from the microphone) can stay under the loudness gate, so a run that
                # received audio still asks speech to text; only no words at all is "no speech".
                run.ended_by = "max_duration" if vad.speech_frames else "no_speech_timeout"
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
            if holdoff > 0:
                skipped = min(holdoff, len(chunk))
                holdoff -= skipped
                chunk = chunk[skipped:]
                if not chunk:
                    continue
            chunk = _gain(chunk, gain)
            await stt.send(boost.apply(chunk))
            for edge in vad.process(chunk):
                if edge == "start":
                    self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START, {})
                else:
                    run.ended_by = "vad"
            if run.ended_by == "vad":
                break
        if not vad.speech_frames:
            if not vad.frames:
                raise PipelineError("stt-no-text-recognized", f"No speech was heard: {vad.heard()}")
            run.heard = vad.heard()
        run.speech_ms = vad.speech_frames * EnergyVAD.FRAME_MS
        run.levels = {"peak_rms": round(vad.peak_rms), "noise_floor": round(vad.floor),
                      "speech_threshold": round(vad.start_threshold()), "input_gain": round(boost.max_applied, 1),
                      "detector": vad.engine, "not_speech_ms": vad.rejected_frames * EnergyVAD.FRAME_MS}
        self._event(run, VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, {})

    def _satellite_device_id(self) -> str | None:
        ent = self.satellite_entity()
        if ent is not None and getattr(ent, "device_id", None):
            return ent.device_id
        for e in self.driver.entities:
            if getattr(e, "device_id", None):
                return e.device_id
        return None

    def _is_whispered(self) -> bool:
        dev = self._satellite_device_id()
        for ent in self.hub.entities.values():
            if dev is not None and getattr(ent, "device_id", None) != dev:
                continue
            if ent.domain != "binary_sensor":
                continue
            fields = " ".join(str(x or "") for x in (getattr(ent, "unique_id", None),
                                                     ent.entity_id, getattr(ent, "original_name", None),
                                                     getattr(ent, "name", None))).lower()
            if "last_request_whispered" not in fields and "last request whispered" not in fields:
                continue
            st = self.hub.states.get(ent.entity_id)
            if st is not None and str(st.state).lower() == "on":
                return True
        return False

    def _whisper_volume_target(self) -> tuple[Any, float | None, float, Any]:
        dev = self._satellite_device_id()
        candidates = []
        for ent in self.hub.entities.values():
            if dev is not None and getattr(ent, "device_id", None) != dev:
                continue
            if ent.domain not in ("number", "media_player"):
                continue
            fields = " ".join(str(x or "") for x in (getattr(ent, "unique_id", None),
                                                     ent.entity_id, getattr(ent, "original_name", None),
                                                     getattr(ent, "name", None))).lower()
            if ent.domain == "number":
                if "assistant_volume" not in fields and "assistant volume" not in fields:
                    continue
                candidates.append(("number", ent))
            else:
                candidates.append(("media", ent))
        # prefer assistant_volume number if available
        number_ent = None
        media_ent = None
        for kind, ent in candidates:
            if kind == "number" and number_ent is None:
                number_ent = ent
            if kind == "media" and media_ent is None:
                media_ent = ent
        ent = number_ent if number_ent is not None else media_ent
        if ent is None:
            return None, None, 1.0, None
        if ent.domain == "media_player":
            st = self.hub.states.get(ent.entity_id)
            attrs = dict(st.attributes or {}) if st is not None else {}
            raw = attrs.get("volume_level")
            try:
                cur = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                cur = None
            return ent, cur, 1.0, cur
        st = self.hub.states.get(ent.entity_id)
        if st is None:
            return ent, None, 1.0, None
        try:
            val = float(st.state)
        except (TypeError, ValueError):
            return ent, None, 1.0, None
        attrs = dict(getattr(st, "attributes", None) or {})
        max_attr = attrs.get("max")
        try:
            max_val = float(max_attr) if max_attr is not None else None
        except (TypeError, ValueError):
            max_val = None
        scale = 100.0 if ((max_val is not None and max_val > 1) or val > 1) else 1.0
        normalized = val / scale if scale else val
        return ent, normalized, scale, val

    async def _apply_volume(self, entity: Any, value: float) -> None:
        if entity.domain == "number":
            await self.hub.call_service("number", "set_value",
                                        {"entity_id": entity.entity_id, "value": value})
        else:
            await self.hub.call_service("media_player", "volume_set",
                                        {"entity_id": entity.entity_id, "volume_level": value})

    @contextlib.asynccontextmanager
    async def _whisper_volume(self, run: Run, settings: Any) -> Any:
        if not getattr(run, "whispered", False):
            yield
            return
        entity, current, scale, raw_original = self._whisper_volume_target()
        lowered = False
        if entity is not None and current is not None and current > settings.whisper_volume:
            try:
                await self._apply_volume(entity, round(settings.whisper_volume * scale, 2))
                lowered = True
            except Exception as exc:
                self.log.warning("voice satellite %s: could not duck volume for whisper: %s", self.name, exc)
        try:
            yield
        finally:
            if lowered:
                try:
                    await self._apply_volume(entity, raw_original if raw_original is not None else current)
                except Exception as exc:
                    self.log.warning("voice satellite %s: could not restore volume after whisper: %s", self.name, exc)

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
        if getattr(run, "whispered", False):
            ctx["whispered"] = True
        return ctx

    def _node_url(self, url: str) -> str:
        """``url`` as the node should fetch it: converted to its announcement format when it asks for one."""
        convert = getattr(self.driver, "media_url_for", None)
        return convert(url, announcement=True) if convert is not None else url

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
        voice = current_settings()
        problems = voice.problems()
        if problems:
            raise HubError("start_conversation cannot hear the answer: " + "; ".join(problems))
        message = str(data.get("start_message") or "")
        media_id = str(data.get("start_media_id") or "")
        if not media_id:
            if not message:
                raise HubError("assist_satellite.start_conversation needs a start_message or a start_media_id")
            media_id = await BACKENDS.speak(message, voice.tts_voice)
        extra: dict[str, Any] = {"started_by": "start_conversation"}
        if message:
            extra["asked"] = message
        if data.get("extra_system_prompt"):
            extra["extra_system_prompt"] = str(data["extra_system_prompt"])
        self._pending = (time.monotonic(), extra)
        preannounce = data.get("preannounce", True) is not False
        preannounce_id = str(data.get("preannounce_media_id") or "") if preannounce else ""
        try:
            await self.client.send_voice_assistant_announcement_await_response(
                self._node_url(media_id), ANNOUNCE_TIMEOUT_S, text=message,
                preannounce_media_id=self._node_url(preannounce_id) if preannounce_id else "",
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
            "speech_detection": self._select_state("speech_detection", SPEECH_DETECTION),
            "min_speech_s": self.speech_detection().min_speech_s,
            "reply": "whole" if self.flags & int(VoiceAssistantFeature.SPEAKER) else "stream",
            "wake_words": {"available": list(self.wake_words.values()),
                           "active": [self.wake_words.get(w, w) for w in self.active_wake_words],
                           "max_active": self.max_active_wake_words, "error": self.wake_word_error},
            "running": self._run.id if self._run is not None else None,
            "runs": self.runs, "last_run": self.history[0] if self.history else None,
            "history": self.history,
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


__all__ = ["BACKENDS", "SPEECH_DETECTION", "Backends", "EnergyVAD", "InputGain", "PipelineError", "SatelliteVoice",
           "SpeechDetection", "VoiceSettings", "WyomingSTT", "ask_jarvis", "ask_jarvis_stream", "chat_reply_text",
           "current_settings", "read_speech_stream", "satellites", "speech_classifier", "to_satellite_pcm"]
