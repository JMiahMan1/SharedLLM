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
import time
from contextlib import suppress

import aiohttp
import numpy as np

from services.common import voice_enroll, voice_learning, voice_speaker
from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, REDIS_URL
from services.execution import media_vocabulary, voiceprint

log = logging.getLogger("execution.wyoming_stt")
#: Every recognition outcome is logged at INFO (who, score, timing; never the
#: signature), and execution's root logger only shows warnings, so this one
#: says what it needs itself: without it nobody can tell why a voice was missed.
log.setLevel(logging.INFO)

#: "host:port" of the Wyoming speech-to-text server that does the transcribing,
#: or several, comma-separated, tried in order: the first that answers gets the
#: command, and every fallback is logged as a warning (a faster server on
#: another machine first, this host's own server after it).
UPSTREAMS = [u.strip() for u in os.getenv("VOICE_STT_UPSTREAM", "").split(",") if ":" in u.strip()]
UPSTREAM = ",".join(UPSTREAMS)
#: How long one upstream may take to accept a connection before the next is tried.
_CONNECT_TIMEOUT_S = 1.5
PORT = int(os.getenv("VOICE_STT_PORT", "10310"))
#: A spoken command is seconds long; past this the rest is not kept for matching.
_MAX_AUDIO_SECONDS = 30
#: Recognition must never hold up the reply: past this the transcript goes back
#: and the request is answered as if no one were recognised.
_IDENTIFY_WAIT_S = 2.0
#: While a teaching session is open the take is the whole point of the
#: command, so the transcript may wait longer for its signature.
_ENROLL_WAIT_S = 8.0
_IDENTIFY_TIMEOUT = aiohttp.ClientTimeout(total=3.0)
#: Learning is background work; it must not delay the transcript either.
_LEARN_TIMEOUT = aiohttp.ClientTimeout(total=5.0)
#: Keep background learning tasks referenced until they finish.
_LEARNING_TASKS: set[asyncio.Task] = set()


class Event:
    """One Wyoming event, with the exact bytes it arrived as (to pass on)."""

    def __init__(self, header: dict, data: dict, payload: bytes, raw: bytes):
        self.header = header
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


#: Where a ``transcribe`` event carries the library's names (``media_vocabulary.initial_prompt``).
#: Wyoming's Transcribe has a free-form ``context``; an engine that biases per request reads the
#: prompt from it. wyoming-faster-whisper 3.8.1 (docker-compose ``whisper``) does not: it only
#: takes a fixed ``--initial-prompt`` at start, so there the misheard-name correction in the media
#: handlers (``media_vocabulary.correct``) is what catches "brand in Lake".
HINTS_CONTEXT_KEY = "initial_prompt"


def with_name_hints(event: Event) -> bytes:
    """A ``transcribe`` event's bytes with the library's names added to its context.

    Anything else, an empty vocabulary, or a context that already has a prompt
    passes through byte for byte.
    """
    if event.type != "transcribe":
        return event.raw
    prompt = media_vocabulary.initial_prompt()
    context = event.data.get("context")
    context = dict(context) if isinstance(context, dict) else {}
    if not prompt or context.get(HINTS_CONTEXT_KEY):
        return event.raw
    context[HINTS_CONTEXT_KEY] = prompt
    data = {**event.data, "context": context}
    header = {k: v for k, v in event.header.items() if k not in ("data", "data_length", "payload_length")}
    header["data"] = data
    if event.payload:
        header["payload_length"] = len(event.payload)
    log.debug("[voice] %d library name(s) offered to speech to text", prompt.count(",") + 1)
    return (json.dumps(header) + "\n").encode() + event.payload


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
    """Identity's verdict on who is speaking, with the command's signature.

    None when the audio gives no signature (too little speech). Otherwise a
    verdict dict carrying ``embedding`` (for learning and teaching), the speech
    length and timings; ``matched`` is False when nobody has enrolled or Identity
    could not be asked, so a teaching session still gets its take.
    """
    started = time.monotonic()
    try:
        signature = await asyncio.to_thread(
            voiceprint.embed_samples, samples, voiceprint.COMMAND_MIN_SPEECH_SECONDS
        )
    except voiceprint.VoiceprintError as e:
        log.info("[voice] not matching this command: %s", e)
        return None
    # How far the speech stood above the room: a new voice is only suggested from a clean take.
    snr_db = voiceprint.quality(samples).get("snr_db")
    embedded = time.monotonic()
    verdict: dict = {"matched": False, "score": None}
    try:
        async with aiohttp.ClientSession() as client:
            resp = await client.post(
                f"{IDENTITY_SVC_URL}/api/voice/identify",
                json={"embedding": signature["embedding"], "model": signature["model"]},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=_IDENTIFY_TIMEOUT,
            )
            if resp.status == 404:  # nobody has enrolled a voice
                verdict["nobody_enrolled"] = True
            else:
                resp.raise_for_status()
                body = await resp.json()
                if isinstance(body, dict):
                    verdict = body
    except Exception as e:  # recognition is optional; the take may still teach
        log.warning("[voice] identity could not match this command: %s", e)
        verdict["error"] = str(e) or type(e).__name__
    # Identity only returns the verdict. The proxy needs the signature to offer
    # this take to a learning or teaching session without running the model twice.
    verdict["embedding"] = signature["embedding"]
    verdict["model"] = signature.get("model")
    verdict["speech_seconds"] = signature.get("speech_seconds")
    verdict["snr_db"] = snr_db
    verdict["embed_ms"] = round((embedded - started) * 1000)
    verdict["total_ms"] = round((time.monotonic() - started) * 1000)
    return verdict


def describe(verdict: dict | None) -> str:
    """One log line for a recognition outcome: who, how sure, how long. Never the signature."""
    if verdict is None:
        return "speaker not recognised: no signature for this command"
    timing = (
        f"{verdict.get('speech_seconds')}s of speech; "
        f"embed {verdict.get('embed_ms')} ms, total {verdict.get('total_ms')} ms"
    )
    if verdict.get("matched"):
        return (
            f"recognised {verdict.get('matched_username')} (score {verdict.get('score')}, "
            f"threshold {verdict.get('threshold')}, margin {verdict.get('margin')}; {timing})"
        )
    if verdict.get("nobody_enrolled"):
        return f"speaker not recognised: nobody has enrolled a voice ({timing})"
    if verdict.get("error"):
        return f"speaker not recognised: identity failed ({verdict['error']}; {timing})"
    candidates = verdict.get("candidates") or []
    ranked = ", ".join(
        f"{c.get('username')} {c.get('score')}" for c in candidates[:3] if isinstance(c, dict)
    ) or "no candidates"
    return (
        f"speaker not recognised: best {ranked} (threshold {verdict.get('threshold')}, "
        f"margin {verdict.get('margin')}; {timing})"
    )


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


async def _speaker(utterance: _Utterance, wait_s: float = _IDENTIFY_WAIT_S) -> dict | None:
    if utterance.task is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(utterance.task), wait_s)
    except TimeoutError:
        log.warning("[voice] recognition took over %.1fs; answering without it", wait_s)
    except Exception as e:  # recognition is optional, never fatal
        log.warning("[voice] recognition failed: %s", e)
    return None


async def _teaching(redis) -> bool:
    """Whether any satellite has a teaching session open (``voice_enroll``)."""
    try:
        return bool(await redis.get(voice_enroll.OPEN_KEY))
    except Exception as e:  # teaching is optional, never fatal
        log.warning("[voice] could not check for a teaching session: %s", e)
        return False


async def _keep_take(redis, text: str, verdict: dict | None) -> None:
    """Keep this command's signature for the gateway's teaching session, briefly."""
    key = voice_enroll.take_key(text)
    take = voice_enroll.encode_take(verdict or {})
    if not key or not take:
        return
    try:
        await redis.set(key, take, ex=voice_enroll.TAKE_TTL_SECONDS)
    except Exception as e:
        log.warning("[voice] could not keep the take for teaching: %s", e)


async def _keep_candidate(redis, text: str, verdict: dict) -> None:
    """Keep an unrecognised but close command's signature for a few seconds, so the
    gateway can offer it as a candidate sample when its satellite is assigned to
    one person (identity decides; an admin approves). Never logged."""
    if not voice_speaker.worth_offering(verdict):
        return
    key = voice_speaker.candidate_key(text)
    take = voice_enroll.encode_take(verdict)
    if not key or not take:
        return
    try:
        await redis.set(key, take, ex=voice_speaker.TTL_SECONDS)
    except Exception as e:  # a candidate is optional
        log.warning("[voice] could not keep a candidate sample: %s", e)


async def _keep_new_voice(redis, text: str, verdict: dict) -> bool:
    """Keep the signature of a command that sounds like nobody enrolled for a few
    seconds (``voice_speaker.looks_new``), so the gateway, which knows the
    satellite, can count it as a possible new voice. Never logged. Returns whether
    it was kept."""
    if not voice_speaker.looks_new(verdict):
        return False
    key = voice_speaker.new_voice_key(text)
    take = voice_enroll.encode_take(verdict)
    if not key or not take:
        return False
    try:
        await redis.set(key, take, ex=voice_speaker.TTL_SECONDS)
    except Exception as e:  # a new-voice hint is optional
        log.warning("[voice] could not keep a new-voice take: %s", e)
        return False
    return True


async def _connect_upstream():
    """(reader, writer) of the first speech-to-text server that answers, else None."""
    for i, upstream in enumerate(UPSTREAMS):
        host, port = upstream.rsplit(":", 1)
        try:
            conn = await asyncio.wait_for(asyncio.open_connection(host, int(port)), _CONNECT_TIMEOUT_S)
        except (TimeoutError, OSError) as e:
            nxt = UPSTREAMS[i + 1] if i + 1 < len(UPSTREAMS) else None
            if nxt:
                log.warning("[voice] speech-to-text server %s unreachable (%s); using %s", upstream, e or "timeout", nxt)
            else:
                log.error("[voice] speech-to-text server %s unreachable: %s", upstream, e or "timeout")
            continue
        return conn
    return None


async def _handle(ha_reader, ha_writer, redis) -> None:
    conn = await _connect_upstream()
    if conn is None:
        ha_writer.close()
        return
    up_reader, up_writer = conn
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
            up_writer.write(with_name_hints(event))
            await up_writer.drain()

    async def to_ha() -> None:
        while (event := await read_event(up_reader)) is not None:
            if event.type == "transcript":
                text = str(event.data.get("text") or "")
                teaching = await _teaching(redis)
                had_audio = utterance.task is not None
                verdict = await _speaker(utterance, _ENROLL_WAIT_S if teaching else _IDENTIFY_WAIT_S)
                if had_audio:
                    log.info("[voice] %s", describe(verdict))
                if teaching:
                    await _keep_take(redis, text, verdict)
                key = voice_speaker.key(text)
                if verdict and key and not verdict.get("nobody_enrolled") and not verdict.get("error"):
                    # Matched or not, so the gateway can log the decision and the two best scores.
                    matched = verdict.get("matched_username") if verdict.get("matched") else None
                    await redis.set(
                        key,
                        voice_speaker.encode(matched, verdict.get("score"), verdict),
                        ex=voice_speaker.TTL_SECONDS,
                    )
                    await _keep_candidate(redis, text, verdict)
                    if await _keep_new_voice(redis, text, verdict):
                        log.info("[voice] this voice is far from every enrolled one: offered as a possible new voice")
                if verdict and verdict.get("matched") and key and verdict.get("embedding"):
                    task = asyncio.create_task(_learn(redis, verdict))
                    _LEARNING_TASKS.add(task)
                    task.add_done_callback(_LEARNING_TASKS.discard)
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


def _warm_up() -> None:
    try:
        seconds = voiceprint.warm_up()
        log.info("[voice] speaker encoder ready in %.1fs", seconds)
    except Exception as e:  # the first command then loads it instead
        log.warning("[voice] could not warm up the speaker encoder: %s", e)


async def serve() -> None:
    """Listen for Home Assistant until cancelled; does nothing when unconfigured."""
    if not UPSTREAMS:
        log.info("VOICE_STT_UPSTREAM not set; voice recognition for Home Assistant is off.")
        return
    import redis.asyncio as aioredis

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    # The encoder loads on first use (about 3 s on the server's CPUs, measured),
    # which made the first command after every restart miss its 2 s budget.
    warm = asyncio.create_task(asyncio.to_thread(_warm_up))
    _LEARNING_TASKS.add(warm)
    warm.add_done_callback(_LEARNING_TASKS.discard)

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
