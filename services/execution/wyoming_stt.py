"""Speech-to-text for Home Assistant that also recognises who is speaking.

Home Assistant's voice satellites send their audio to a Wyoming speech-to-text
server. This is one: it passes every event through, unchanged, to the real
one (Home Assistant's Whisper, ``VOICE_STT_UPSTREAM``), so transcription is
exactly what it was. While Whisper works, it matches the same audio against
the family's enrolled voices (``voiceprint`` here, identity's
``/api/voice/identify``), and before the transcript goes back it records who
said it (``services/common/voice_speaker``). The gateway then answers the
chat request that follows as that person. Audio is never stored.

Wyoming is JSON lines: a header line with ``type`` and optional ``data``,
``data_length`` and ``payload_length``, followed by that many bytes of extra
JSON data and of binary payload (PCM for ``audio-chunk``).
"""
import asyncio
import json
import logging
import os
from contextlib import suppress

import aiohttp
import numpy as np

from services.common import voice_learning, voice_speaker
from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, REDIS_URL
from services.execution import voiceprint

log = logging.getLogger("execution.wyoming_stt")

#: "host:port" of the Wyoming speech-to-text server that does the transcribing.
UPSTREAM = os.getenv("VOICE_STT_UPSTREAM", "").strip()
PORT = int(os.getenv("VOICE_STT_PORT", "10310"))
#: A spoken command is seconds long; past this the rest is not kept for matching.
_MAX_AUDIO_SECONDS = 30
#: Recognition must never hold up the reply: past this the transcript goes back
#: and the request is answered as if no one were recognised.
_IDENTIFY_WAIT_S = 2.0
_IDENTIFY_TIMEOUT = aiohttp.ClientTimeout(total=3.0)
#: Learning is background work; it must not delay the transcript either.
_LEARN_TIMEOUT = aiohttp.ClientTimeout(total=5.0)
#: Keep background learning tasks referenced until they finish.
_LEARNING_TASKS: set[asyncio.Task] = set()


class Event:
    """One Wyoming event, with the exact bytes it arrived as (to pass on)."""

    def __init__(self, header: dict, data: dict, payload: bytes, raw: bytes):
        self.type = header.get("type")
        self.data = data
        self.payload = payload
        self.raw = raw


async def read_event(reader: asyncio.StreamReader) -> Event | None:
    line = await reader.readline()
    if not line:
        return None
    header = json.loads(line)
    raw = [line]
    data = dict(header.get("data") or {})
    if header.get("data_length"):
        extra = await reader.readexactly(header["data_length"])
        raw.append(extra)
        data.update(json.loads(extra))
    payload = b""
    if header.get("payload_length"):
        payload = await reader.readexactly(header["payload_length"])
        raw.append(payload)
    return Event(header, data, payload, b"".join(raw))


def to_samples(pcm: bytes, rate: int, width: int, channels: int) -> np.ndarray:
    """Little-endian PCM as 16 kHz mono float32, what the voice encoder takes."""
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[width]
    audio = np.frombuffer(pcm[: len(pcm) - len(pcm) % (width * channels)], dtype=f"<{np.dtype(dtype).str[1:]}")
    audio = audio.astype(np.float32) / float(np.iinfo(dtype).max)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != voiceprint.SAMPLE_RATE and len(audio):
        n = int(len(audio) * voiceprint.SAMPLE_RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


async def identify(samples: np.ndarray) -> dict | None:
    """Identity's verdict on who is speaking, or None when it cannot say."""
    try:
        signature = await asyncio.to_thread(
            voiceprint.embed_samples, samples, voiceprint.COMMAND_MIN_SPEECH_SECONDS
        )
    except voiceprint.VoiceprintError as e:
        log.info("[voice] not matching this command: %s", e)
        return None
    async with aiohttp.ClientSession() as client:
        resp = await client.post(
            f"{IDENTITY_SVC_URL}/api/voice/identify",
            json={"embedding": signature["embedding"], "model": signature["model"]},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=_IDENTIFY_TIMEOUT,
        )
        if resp.status == 404:  # nobody has enrolled a voice
            return None
        resp.raise_for_status()
        verdict = await resp.json()
        if not isinstance(verdict, dict):
            return None
        # Identity only returns the verdict. The proxy needs the signature to
        # offer this take to a learning session without running the model twice.
        verdict["embedding"] = signature["embedding"]
        verdict["speech_seconds"] = signature.get("speech_seconds")
        return verdict


async def _learn(redis, verdict: dict) -> None:
    """Offer a confidently matched command to that person's open learning session."""
    username = verdict.get("matched_username")
    embedding = verdict.get("embedding")
    score = verdict.get("score")
    if not username or not embedding or not isinstance(score, (int, float)):
        return
    if score < voice_learning.MIN_SCORE:
        return
    session_key = voice_learning.key(username)
    try:
        if voice_learning.remaining(await redis.get(session_key)) is None:
            return
        async with aiohttp.ClientSession() as client:
            resp = await client.post(
                f"{IDENTITY_SVC_URL}/api/voice/learn",
                json={
                    "username": username,
                    "embedding": embedding,
                    "speech_seconds": verdict.get("speech_seconds"),
                    "model": voiceprint.MODEL,
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=_LEARN_TIMEOUT,
            )
            body = await resp.json(content_type=None)
        if resp.status != 200:
            detail = body.get("detail") if isinstance(body, dict) else resp.status
            log.info("[voice] not learned for %s: %s", username, detail)
            return
        left = await redis.decr(session_key)
        if left <= 0:
            await redis.delete(session_key)
        log.info(
            "[voice] learned %s's voice (%s); %s sample(s) left in this session",
            username,
            body.get("status") if isinstance(body, dict) else "stored",
            max(0, left),
        )
    except Exception as e:  # learning is optional, never fatal
        log.warning("[voice] could not learn %s's voice: %s", username, e)


class _Utterance:
    """The audio of the command being transcribed on one connection."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.chunks: list[bytes] = []
        self.size = 0
        self.format: tuple[int, int, int] | None = None
        self.task: asyncio.Task | None = None

    def add(self, event: Event) -> None:
        rate, width, channels = (int(event.data.get(k) or d) for k, d in (("rate", 16000), ("width", 2), ("channels", 1)))
        self.format = self.format or (rate, width, channels)
        if self.size < rate * width * channels * _MAX_AUDIO_SECONDS:
            self.chunks.append(event.payload)
            self.size += len(event.payload)

    def start_matching(self) -> None:
        if self.chunks and self.format and self.task is None:
            samples = to_samples(b"".join(self.chunks), *self.format)
            self.task = asyncio.create_task(identify(samples))


async def _speaker(utterance: _Utterance) -> dict | None:
    if utterance.task is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(utterance.task), _IDENTIFY_WAIT_S)
    except TimeoutError:
        log.warning("[voice] recognition took over %.1fs; answering without it", _IDENTIFY_WAIT_S)
    except Exception as e:  # recognition is optional, never fatal
        log.warning("[voice] recognition failed: %s", e)
    return None


async def _handle(ha_reader, ha_writer, redis) -> None:
    host, port = UPSTREAM.rsplit(":", 1)
    try:
        up_reader, up_writer = await asyncio.open_connection(host, int(port))
    except OSError as e:
        log.error("[voice] speech-to-text server %s unreachable: %s", UPSTREAM, e)
        ha_writer.close()
        return
    utterance = _Utterance()

    async def from_ha() -> None:
        while (event := await read_event(ha_reader)) is not None:
            if event.type in ("transcribe", "audio-start"):
                if utterance.task is not None:
                    utterance.task.cancel()
                utterance.reset()
            elif event.type == "audio-chunk":
                utterance.add(event)
            elif event.type == "audio-stop":
                utterance.start_matching()
            up_writer.write(event.raw)
            await up_writer.drain()

    async def to_ha() -> None:
        while (event := await read_event(up_reader)) is not None:
            if event.type == "transcript":
                text = str(event.data.get("text") or "")
                verdict = await _speaker(utterance)
                key = voice_speaker.key(text)
                if verdict and verdict.get("matched") and key:
                    await redis.set(
                        key,
                        voice_speaker.encode(verdict["matched_username"], verdict.get("score")),
                        ex=voice_speaker.TTL_SECONDS,
                    )
                    log.info("[voice] recognised %s (score %s)", verdict["matched_username"], verdict.get("score"))
                    if verdict.get("embedding"):
                        task = asyncio.create_task(_learn(redis, verdict))
                        _LEARNING_TASKS.add(task)
                        task.add_done_callback(_LEARNING_TASKS.discard)
                elif verdict:
                    log.info("[voice] speaker not recognised (best score %s)", verdict.get("score"))
            ha_writer.write(event.raw)
            await ha_writer.drain()

    tasks = [asyncio.create_task(from_ha()), asyncio.create_task(to_ha())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        if utterance.task is not None:
            utterance.task.cancel()
        for writer in (up_writer, ha_writer):
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


async def serve() -> None:
    """Listen for Home Assistant until cancelled; does nothing when unconfigured."""
    if not UPSTREAM or ":" not in UPSTREAM:
        log.info("VOICE_STT_UPSTREAM not set; voice recognition for Home Assistant is off.")
        return
    import redis.asyncio as aioredis

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    async def handle(reader, writer):
        try:
            await _handle(reader, writer, redis)
        except Exception as e:  # one bad connection must not stop the server
            log.warning("[voice] connection ended: %s", e)

    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    log.info("Voice recognition speech-to-text on :%s, transcribing with %s", PORT, UPSTREAM)
    try:
        async with server:
            await server.serve_forever()
    finally:
        await redis.aclose()
