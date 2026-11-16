"""Piper voices for Jarvis, spoken by a Wyoming Piper server.

Home Assistant spoke with Piper (its add-on, voice ``en_US-hfc_male-medium``).
The ``piper`` compose service runs the same engine (``rhasspy/wyoming-piper``)
on this host, and Jarvis can speak with any voice installed there. A Piper
voice is named ``piper:<voice>`` wherever a voice is taken (the default voice
setting, ``/execute/tts``, ``tts.speak``), so it can never be mistaken for a
Kokoro voice or a cloned one.

Piper runs fast on a CPU (a sentence in well under a second here), which is
why it also suits the voice satellites.

``PIPER_TTS`` (``host:port``) names the server and is read once, at start.
Unset, a Piper voice fails with a message that says so; it never falls back to
another voice.

Wyoming is JSON lines (see ``wyoming_stt``): ``describe`` is answered with an
``info`` event listing the installed voices, and ``synthesize`` with
``audio-start``, ``audio-chunk``s of PCM and ``audio-stop``.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import wave

from services.execution.wyoming_stt import read_event

PREFIX = "piper:"
#: ``host:port`` of the Wyoming Piper server. Read once, at start.
SERVER = os.getenv("PIPER_TTS", "").strip()
#: The Piper server's voice folder, mounted read-only. The server describes every voice in its
#: catalog as installed and downloads one on first use, so only voices with a model here are
#: offered: picking a voice must never fetch one from the internet. Read once, at start.
VOICES_DIR = os.getenv("PIPER_VOICES_DIR", "").strip()
#: A reply is a few sentences; a long passage still finishes well inside this.
SYNTH_TIMEOUT_S = 60.0
DESCRIBE_TIMEOUT_S = 5.0


class PiperError(RuntimeError):
    """Piper could not speak: not configured, not answering, or the voice is not installed."""


def is_piper_voice(voice: str | None) -> bool:
    return bool(voice) and str(voice).startswith(PREFIX)


def voice_name(voice: str) -> str:
    return voice[len(PREFIX):].strip()


def _address() -> tuple[str, int]:
    if not SERVER:
        raise PiperError("Piper voices need PIPER_TTS (host:port of the Wyoming Piper server) in .env")
    host, _, port = SERVER.rpartition(":")
    if not host or not port.isdigit():
        raise PiperError(f"PIPER_TTS must be host:port, not {SERVER!r}")
    return host, int(port)


def _event(kind: str, data: dict | None = None) -> bytes:
    header: dict = {"type": kind}
    if data:
        body = json.dumps(data).encode()
        header["data_length"] = len(body)
        return json.dumps(header).encode() + b"\n" + body
    return json.dumps(header).encode() + b"\n"


async def _connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host, port = _address()
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), DESCRIBE_TIMEOUT_S)
    except (OSError, TimeoutError) as exc:
        raise PiperError(f"the Piper server at {SERVER} did not answer: {exc}") from exc


def _present(name: str) -> bool:
    """Whether the voice's model is in the voice folder (always, when no folder is configured)."""
    if not VOICES_DIR:
        return True
    return os.path.isfile(os.path.join(VOICES_DIR, f"{name}.onnx"))


async def list_voices() -> list[str]:
    """The Piper voices on this host, each as ``piper:<name>``."""
    reader, writer = await _connect()
    try:
        writer.write(_event("describe"))
        await writer.drain()

        async def info() -> dict:
            while True:
                event = await read_event(reader)
                if event is None:
                    raise PiperError("the Piper server closed the connection without describing itself")
                if event.type == "info":
                    return event.data

        data = await asyncio.wait_for(info(), DESCRIBE_TIMEOUT_S)
    finally:
        writer.close()
    names: list[str] = []
    for program in data.get("tts") or []:
        for voice in program.get("voices") or []:
            if voice.get("installed", True) and voice.get("name") and _present(str(voice["name"])):
                names.append(PREFIX + str(voice["name"]))
    return sorted(set(names))


async def synthesize(text: str, voice: str) -> bytes:
    """``text`` spoken by the Piper voice ``voice`` (``piper:<name>``), as WAV."""
    name = voice_name(voice)
    if not name:
        raise PiperError("a Piper voice needs a name after 'piper:'")
    if not _present(name):
        raise PiperError(f"the Piper voice {name!r} is not installed (put {name}.onnx and its .onnx.json in data/piper)")
    reader, writer = await _connect()
    try:
        writer.write(_event("synthesize", {"text": text, "voice": {"name": name}}))
        await writer.drain()

        async def collect() -> bytes:
            pcm = bytearray()
            fmt: tuple[int, int, int] | None = None
            while True:
                event = await read_event(reader)
                if event is None:
                    raise PiperError("the Piper server closed the connection before it finished speaking")
                if event.type == "audio-start" or (event.type == "audio-chunk" and fmt is None):
                    fmt = (int(event.data.get("rate", 22050)), int(event.data.get("width", 2)),
                           int(event.data.get("channels", 1)))
                if event.type == "audio-chunk":
                    pcm.extend(event.payload)
                elif event.type == "audio-stop":
                    break
                elif event.type == "error":
                    raise PiperError(f"Piper could not speak with {name!r}: {event.data.get('text') or event.data}")
            if not pcm or fmt is None:
                raise PiperError(f"Piper returned no audio for {name!r}")
            out = io.BytesIO()
            with wave.open(out, "wb") as wav:
                wav.setframerate(fmt[0])
                wav.setsampwidth(fmt[1])
                wav.setnchannels(fmt[2])
                wav.writeframes(bytes(pcm))
            return out.getvalue()

        try:
            return await asyncio.wait_for(collect(), SYNTH_TIMEOUT_S)
        except TimeoutError as exc:
            raise PiperError(f"Piper took longer than {SYNTH_TIMEOUT_S:.0f} s to speak") from exc
    finally:
        writer.close()
