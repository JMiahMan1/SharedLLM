# services/bible/narration.py
"""Speak a passage aloud.

The reader asks for a *passage*, never for arbitrary text: the script is built
here out of the imported verses, so the text-to-speech engine can never be used
as a general "say anything" service on our behalf. That is the whole reason this
module exists instead of a thin gateway proxy that forwarded whatever the UI
typed.

The engine in ``services.execution.tts`` already knows how to read Scripture: it
expands a reference into speech ("John 3:16" -> "John chapter 3, verse 16") and
pauses after it, and expands verse numbers and years into words. So the heading
we emit is the human reference and the engine narrates it properly.

Audio is cached in the same SQLite file as the corpus, keyed by translation,
passage and voice. Reading the same chapter twice must not synthesise it twice,
and the cached bytes disappear with the corpus they came from.

Both limits below are refusals rather than silent truncations: a reader who asks
for a 176-verse psalm would otherwise wait minutes for an audio file nobody
wants, and would have no way of knowing that the first ten verses were the only
ones they got.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import aiohttp
from sqlmodel import Session, select

from services.bible.corpus import fetch_passage
from services.bible.models import NarrationAudio, utcnow

log = logging.getLogger(__name__)

#: A chapter is the natural unit for listening. Above this the audio is longer
#: than anyone sits still for, so we say so rather than render it.
MAX_VERSES = 120
#: Roughly ten minutes of narration. The verse cap usually bites first.
MAX_CHARACTERS = 12_000
#: Below this there is nothing worth speaking; a "verse" that resolved to an
#: almost-empty string would otherwise be read out as near-silence. Measured on
#: the passage body, because the reference heading always adds length.
MIN_BODY_CHARACTERS = 2

AUDIO_MIME = "audio/wav"

#: How much text one chunk of narration may hold. The engine's own long-text
#: splitter re-phonemises an ever-growing span as it packs sentences, which
#: costs minutes on a whole chapter; the same chapter cut into ten pieces here
#: and handed over a piece at a time takes a tenth as long. So the split
#: happens here, where it is cheap, and every engine call stays on the fast
#: path. Roughly a minute of speech, near a thousand characters.
TARGET_CHUNK_CHARACTERS = 600
#: The first chunk is deliberately short. It is the one the reader waits for,
#: and everything else is packed to the target while they are already listening.
FIRST_CHUNK_CHARACTERS = 240

#: A split point: a paragraph break, or the space after a sentence end. Both
#: leave the punctuation on the piece that ends, so no chunk starts mid-sentence
#: more than the reader would notice as a natural pause.
_SPLIT = re.compile(r"\n{2,}|(?<=[.!?])\s+")

#: Names the model files rather than "the voice model" because the engine needs
#: three of them now: the ONNX model, the voice pack and the vocabulary. Which
#: one is missing is in the engine's own message; this sentence says who fixes it.
VOICE_DOWNLOAD_HINT = (
    "an administrator has to install the voice model files on this server"
)


class NarrationError(ValueError):
    """A passage cannot be narrated. The message says why and what to do."""


class NarrationUnavailable(RuntimeError):
    """The speech engine could not be reached, or could not speak.

    Kept apart from :class:`NarrationError` because the caller decides the
    status code: a too-long passage is the reader's problem (400) and a missing
    voice file is the operator's (503).
    """


@dataclass(frozen=True)
class Script:
    """A passage rendered as the plain text the speech engine reads."""

    reference: str
    body: str

    @property
    def text(self) -> str:
        """The heading, then the passage with a blank line between verses."""
        verses = self.body.split("\n")
        return self.reference + "\n\n" + "\n\n".join(verses)

    @property
    def characters(self) -> int:
        return len(self.text)


def cache_key(version: str, reference: str, voice: str) -> str:
    """A stable key for one narration of one passage in one voice."""
    digest = hashlib.sha256(f"{version}|{reference}|{voice}".encode()).hexdigest()
    return digest[:32]


def build_script(reference: str, verses: list[dict]) -> Script:
    """Turn verses into a narration script, or refuse with a readable reason.

    ``verses`` are the dicts ``corpus.fetch_passage`` returns. A reference that
    resolved to nothing is an error the reader needs to see, not an empty audio
    file that plays for zero seconds.
    """
    lines = [str(v.get("text") or "").strip() for v in verses]
    lines = [line for line in lines if line]
    if not lines:
        raise NarrationError(f"{reference} has no text in this translation to read aloud.")
    if len(lines) > MAX_VERSES:
        raise NarrationError(
            f"{reference} is {len(lines)} verses; reading aloud covers up to {MAX_VERSES} "
            "at a time. Select a shorter passage."
        )
    script = Script(reference=reference, body="\n".join(lines))
    if script.characters > MAX_CHARACTERS:
        raise NarrationError(
            f"{reference} is {script.characters:,} characters; reading aloud covers up to "
            f"{MAX_CHARACTERS:,} at a time. Select a shorter passage."
        )
    if len(script.body) < MIN_BODY_CHARACTERS:
        raise NarrationError(f"{reference} has too little text to read aloud.")
    return script


def cached(session: Session, version: str, reference: str, voice: str) -> NarrationAudio | None:
    """Return the stored narration for this passage and voice, if there is one."""
    row = session.get(NarrationAudio, cache_key(version, reference, voice))
    if row is not None and row.audio:
        return row
    return None


def store(
    session: Session,
    *,
    version: str,
    reference: str,
    voice: str,
    audio: bytes,
    verse_count: int,
    mime_type: str = AUDIO_MIME,
) -> NarrationAudio:
    """Remember a narration so the next play of the same passage is instant."""
    key = cache_key(version, reference, voice)
    row = session.get(NarrationAudio, key)
    if row is None:
        row = NarrationAudio(key=key)
        session.add(row)
    row.version_code = version
    row.reference = reference
    row.voice = voice
    row.verse_count = verse_count
    row.mime_type = mime_type
    row.audio = audio
    row.created_at = utcnow()
    session.commit()
    return row


def clear(session: Session, version: str | None = None) -> int:
    """Drop cached narration, for one translation or all of them."""
    if version:
        rows = session.exec(
            select(NarrationAudio).where(NarrationAudio.version_code == version)
        ).all()
    else:
        rows = session.exec(select(NarrationAudio)).all()
    for row in rows:
        session.delete(row)
    session.commit()
    return len(rows)


async def narrate(
    session: Session,
    *,
    version: str,
    spans: list,
    reference: str,
    voice: str | None,
    execution_url: str,
    internal_secret: str,
    timeout: float = 180.0,
) -> dict:
    """Return audio for a passage, reusing a cached narration when there is one.

    ``spans`` are the parsed :class:`~services.bible.refs.VerseSpan` objects and
    ``reference`` their display form, so the caller has already validated the
    reader's request. ``execution_url`` must be configured; when it is not we say
    so instead of quietly returning nothing, because a reader who pressed Play
    deserves to know why there is no sound.
    """
    key_voice = voice or "default"
    hit = cached(session, version, reference, key_voice)
    if hit is not None:
        return _payload(
            version=version,
            reference=reference,
            voice=key_voice,
            audio=hit.audio,
            mime_type=hit.mime_type or AUDIO_MIME,
            cached=True,
            verse_count=int(hit.verse_count),
        )

    verses = fetch_passage(session, version, spans)
    script = build_script(reference, verses)
    audio, mime_type = await _speak(
        script.text,
        voice=voice,
        execution_url=execution_url,
        internal_secret=internal_secret,
        timeout=timeout,
    )
    store(
        session,
        version=version,
        reference=reference,
        voice=key_voice,
        audio=audio,
        mime_type=mime_type,
        verse_count=len(verses),
    )
    return _payload(
        version=version,
        reference=reference,
        voice=key_voice,
        audio=audio,
        mime_type=mime_type,
        cached=False,
        verse_count=len(verses),
    )


def _payload(
    *,
    version: str,
    reference: str,
    voice: str,
    audio: bytes,
    mime_type: str,
    cached: bool,
    verse_count: int,
) -> dict:
    return {
        "version": version,
        "reference": reference,
        "voice": voice,
        "verse_count": verse_count,
        "cached": cached,
        "mime_type": mime_type,
        "length_bytes": len(audio),
        "audio_base64": base64.b64encode(audio).decode("utf-8"),
    }


@dataclass(frozen=True)
class Chunk:
    """One piece of a passage, spoken on its own and cached on its own."""

    index: int
    text: str

    @property
    def characters(self) -> int:
        return len(self.text)


def split_script(script: Script) -> list[Chunk]:
    """Cut a script into the pieces the reader hears one after another.

    Splitting here is a speed decision as much as a correctness one: the
    engine's own splitter re-phonemises an ever-growing span as it packs
    sentences, which is acceptable for a paragraph and ruinous for a chapter.
    Pieces are cut at paragraph and sentence boundaries so no chunk ends
    mid-word, and the first is short so sound starts while the rest is still
    being rendered.
    """
    pieces = [part.strip() for part in _SPLIT.split(script.text) if part.strip()]
    chunks: list[Chunk] = []
    current: list[str] = []
    size = 0
    limit = FIRST_CHUNK_CHARACTERS
    for piece in pieces:
        if current and size + len(piece) + 1 > limit:
            chunks.append(Chunk(index=len(chunks), text=" ".join(current)))
            current, size = [], 0
            limit = TARGET_CHUNK_CHARACTERS
        current.append(piece)
        size += len(piece) + 1
    if current:
        chunks.append(Chunk(index=len(chunks), text=" ".join(current)))
    return chunks


def chunk_key(version: str, reference: str, voice: str, chunk: Chunk) -> str:
    """A stable key for one chunk of one passage in one voice.

    The chunk's own text is part of the digest, so re-importing a translation
    -- or changing how the script is split -- can never leave audio sitting
    under a key that now means different words.
    """
    digest = hashlib.sha256(
        f"{version}|{reference}|{voice}|c{chunk.index}|{chunk.text}".encode()
    ).hexdigest()
    return digest[:32]


def _cached_keys(session: Session, keys: list[str]) -> set[str]:
    """Which of these keys are already spoken, in one query rather than N."""
    if not keys:
        return set()
    rows = session.exec(select(NarrationAudio.key).where(NarrationAudio.key.in_(keys))).all()
    return {str(key) for key in rows}


def _script_for(session: Session, version: str, spans: list, reference: str) -> Script:
    return build_script(reference, fetch_passage(session, version, spans))


def plan(
    session: Session,
    *,
    version: str,
    spans: list,
    reference: str,
    voice: str | None,
) -> dict:
    """What speaking this passage takes, and how much of it is already spoken.

    Nothing is synthesised here: the client asks first so it can start the first
    chunk immediately and fetch the rest while the reader is already listening.
    """
    key_voice = voice or "default"
    script = _script_for(session, version, spans, reference)
    chunks = split_script(script)
    keys = {chunk.index: chunk_key(version, reference, key_voice, chunk) for chunk in chunks}
    spoken = _cached_keys(session, list(keys.values()))
    return {
        "version": version,
        "reference": reference,
        "voice": key_voice,
        "count": len(chunks),
        "characters": script.characters,
        "cached_count": len(spoken),
        "all_cached": len(spoken) == len(chunks),
        "chunks": [
            {
                "index": chunk.index,
                "characters": chunk.characters,
                "cached": keys[chunk.index] in spoken,
            }
            for chunk in chunks
        ],
    }


async def narrate_chunk(
    session: Session,
    *,
    version: str,
    spans: list,
    reference: str,
    voice: str | None,
    index: int,
    execution_url: str,
    internal_secret: str,
    timeout: float = 180.0,
) -> dict:
    """Speak one chunk of a passage, or serve it from the cache.

    One chunk per request is what makes playback start quickly: the client asks
    for chunk 0, plays it, and fetches the next while the reader is listening.
    An index outside the passage is the caller's mistake and says so, rather
    than returning silence as if the passage had ended.
    """
    key_voice = voice or "default"
    chunks = split_script(_script_for(session, version, spans, reference))
    if index < 0 or index >= len(chunks):
        raise NarrationError(
            f"{reference} is {len(chunks)} parts of narration, so there is no part {index}."
        )
    chunk = chunks[index]
    hit = session.get(NarrationAudio, chunk_key(version, reference, key_voice, chunk))
    if hit is not None and hit.audio:
        return _chunk_payload(
            version=version,
            reference=reference,
            voice=key_voice,
            index=index,
            count=len(chunks),
            chunk=chunk,
            audio=hit.audio,
            mime_type=hit.mime_type or AUDIO_MIME,
            cached=True,
        )
    audio, mime_type = await _speak(
        chunk.text,
        voice=voice,
        execution_url=execution_url,
        internal_secret=internal_secret,
        timeout=timeout,
    )
    store_chunk(
        session,
        version=version,
        reference=reference,
        voice=key_voice,
        chunk=chunk,
        audio=audio,
        mime_type=mime_type,
    )
    return _chunk_payload(
        version=version,
        reference=reference,
        voice=key_voice,
        index=index,
        count=len(chunks),
        chunk=chunk,
        audio=audio,
        mime_type=mime_type,
        cached=False,
    )


def store_chunk(
    session: Session,
    *,
    version: str,
    reference: str,
    voice: str,
    chunk: Chunk,
    audio: bytes,
    mime_type: str = AUDIO_MIME,
) -> NarrationAudio:
    """Remember one spoken chunk, so the next play of this passage is instant."""
    key = chunk_key(version, reference, voice, chunk)
    row = session.get(NarrationAudio, key)
    if row is None:
        row = NarrationAudio(key=key)
        session.add(row)
    row.version_code = version
    row.reference = reference
    row.voice = voice
    row.chunk_index = chunk.index
    row.mime_type = mime_type
    row.audio = audio
    row.created_at = utcnow()
    session.commit()
    return row


def _chunk_payload(
    *,
    version: str,
    reference: str,
    voice: str,
    index: int,
    count: int,
    chunk: Chunk,
    audio: bytes,
    mime_type: str,
    cached: bool,
) -> dict:
    return {
        "version": version,
        "reference": reference,
        "voice": voice,
        "index": index,
        "count": count,
        "characters": chunk.characters,
        "cached": cached,
        "mime_type": mime_type,
        "length_bytes": len(audio),
        "audio_base64": base64.b64encode(audio).decode("utf-8"),
    }


async def _speak(
    text: str,
    *,
    voice: str | None,
    execution_url: str,
    internal_secret: str,
    timeout: float,
) -> tuple[bytes, str]:
    """Ask the execution service to speak, or explain why it could not."""
    if not execution_url:
        raise NarrationUnavailable(
            "The execution service is not configured, so Scripture cannot be read aloud. "
            "Set EXECUTION_SVC_URL (compose) or execution_svc_url (Identity global "
            "setting) and restart the bible service."
        )
    body: dict = {"user_context": {"user": "jarvis"}, "text": text}
    if voice:
        body["voice"] = voice
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{execution_url}/execute/tts",
                json=body,
                headers={"X-Internal-Secret": internal_secret},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                status = resp.status
                raw = await resp.text()
    except Exception as exc:  # noqa: BLE001 - the reader is told, not just the log
        raise NarrationUnavailable(f"The speech engine could not be reached: {exc}") from exc

    if status >= 400:
        raise NarrationUnavailable(_detail_of(raw) or f"The speech engine returned HTTP {status}.")

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise NarrationUnavailable(
            f"The speech engine returned something that is not JSON: {raw[:200]}"
        ) from exc

    if not isinstance(payload, dict):
        raise NarrationUnavailable("The speech engine returned an unexpected response.")
    if str(payload.get("status")) != "SUCCESS":
        raise NarrationUnavailable(_failure_message(payload))

    detail = payload.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    encoded = detail.get("audio_base64")
    if not encoded:
        message = str(payload.get("message") or "").strip()
        raise NarrationUnavailable(
            "The speech engine reported success but sent no audio."
            + (f" It said: {message}" if message else "")
        )
    try:
        audio = base64.b64decode(encoded, validate=True)
    except Exception as exc:  # noqa: BLE001 - reported to the reader
        raise NarrationUnavailable(
            f"The speech engine sent audio we could not decode: {exc}"
        ) from exc
    if not audio:
        raise NarrationUnavailable("The speech engine returned an empty audio file.")
    return audio, str(detail.get("mime_type") or AUDIO_MIME)


def _failure_message(payload: dict) -> str:
    """Turn the execution service's own message into something actionable.

    A missing voice model is the common case, and the raw message only names a
    file path on the server, which is not something a reader can act on. The
    fix is appended in terms of what to do about it rather than how to install it.
    """
    message = str(payload.get("message") or "The speech engine refused the request.")
    if "Kokoro" in message and "missing" in message:
        return f"Reading aloud is not set up here yet: {VOICE_DOWNLOAD_HINT}."
    return message


def _detail_of(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body.strip() or None
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return None