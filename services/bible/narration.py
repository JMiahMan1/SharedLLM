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

VOICE_DOWNLOAD_HINT = (
    "an administrator has to install the voice model on this server"
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