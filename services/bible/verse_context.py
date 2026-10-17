# services/bible/verse_context.py
"""Plain-language context for the Verse of the Day.

The Verse of the Day card shows one verse. That is enough to read, but not
always enough to understand: who is speaking, what just happened, and where the
verse sits in the chapter are all outside the single line on screen. This module
asks Jarvis for that context once, stores the answer, and serves it to every
surface that shows the same verse.

Two rules shape it:

* **The passage is the evidence.** The chapter around the verse and the study
  notes already installed for it are put in the prompt. The model may explain
  well-known background, but it is told to say when something is not clear
  rather than invent a name, a date or a quotation.
* **Context is not a devotional.** It answers "what is going on here", not "what
  should this mean to me today". The daily devotional is a separate source and
  keeps its own voice.

A note is cached by translation and verse, not by date. The same verse can come
up again in a later year, and writing it twice would spend the same model time
for the same words.
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import Session

from . import corpus, study, study_ask, votd
from .models import VerseContext, utcnow
from .refs import VerseSpan

log = logging.getLogger("bible.verse_context")

#: How far either side of the verse the prompt reaches. Enough to show the
#: paragraph a verse lives in, without turning one note into a chapter summary.
WINDOW_VERSES = 8

#: The daily loop is a safety net, not a scheduler: the on-demand route fills a
#: missing note immediately, and midnight is allowed to be a little late.
INTERVAL_SECONDS = 30 * 60

#: A failed pass (for example the gateway is still restarting) is retried
#: quickly before settling back into the half-hourly cadence.
RETRY_SECONDS = 60

CONTEXT_SYSTEM_PROMPT = (
    "You are a careful Bible study helper for a family. Explain the context of "
    "the verse you are given: who is speaking or writing and to whom, what is "
    "happening in the surrounding passage, where the verse sits in the chapter "
    "and the book, and the background or themes that help a reader understand "
    "it. Use the passage and study notes given to you as your evidence. You may "
    "explain well-known background, but do not invent names, dates, quotations "
    "or claims that the text does not support; if something is not clear, say "
    "so. Do not give a devotional application, a prayer, or personal advice. "
    "Write two or three short paragraphs in plain language."
)


def key(version: str, osis: str, chapter: int, verse: int) -> str:
    """The canonical cache key: one note per translation and verse."""
    return f"{version}:{osis}:{chapter}:{verse}"


def _key_for(verse: dict) -> str:
    return key(
        str(verse.get("version") or ""),
        str(verse.get("osis") or ""),
        int(verse.get("chapter") or 0),
        int(verse.get("verse") or 0),
    )


def cached(session: Session, verse: dict) -> VerseContext | None:
    """The stored note for this verse, if one has been written."""
    if not verse or verse.get("error"):
        return None
    return session.get(VerseContext, _key_for(verse))


def payload(row: VerseContext) -> dict:
    """The API shape of a stored context note."""
    return {
        "key": row.key,
        "version": row.version_code,
        "edition": row.edition_code,
        "osis": row.osis,
        "chapter": row.chapter,
        "verse": row.verse,
        "reference": row.reference,
        "text": row.text,
        "body": row.body,
        "model": row.model,
        "verses_used": row.verses_used,
        "notes_used": row.notes_used,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


def cached_payload(session: Session, verse: dict) -> dict | None:
    row = cached(session, verse)
    return payload(row) if row is not None else None


def _window(session: Session, verse: dict) -> tuple[list[dict], VerseSpan]:
    """The verses around the VOTD and the span that covers them."""
    version = str(verse["version"])
    osis = str(verse["osis"])
    chapter = int(verse["chapter"])
    number = int(verse["verse"])
    rows = corpus.fetch_chapter(session, version, osis, chapter)
    first = max(1, number - WINDOW_VERSES)
    last = min(max(row["verse"] for row in rows), number + WINDOW_VERSES)
    window = [row for row in rows if first <= row["verse"] <= last]
    span = VerseSpan(
        book=osis,
        chapter_start=chapter,
        verse_start=first,
        chapter_end=chapter,
        verse_end=last,
    )
    return window, span


def _verse_lines(verses: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in verses:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        reference = str(row.get("reference") or "").strip()
        lines.append(f"{reference} {text}" if reference else text)
    return lines


def _note_lines(notes: list[dict]) -> list[str]:
    lines: list[str] = []
    for note in notes[: study_ask.MAX_NOTES]:
        body = str(note.get("body") or "").strip()
        if not body:
            continue
        if len(body) > study_ask.MAX_NOTE_CHARACTERS:
            body = body[: study_ask.MAX_NOTE_CHARACTERS].rstrip() + " ..."
        reference = str(note.get("reference") or "").strip()
        kind = str(note.get("kind") or "note").strip()
        label = f"{reference} ({kind})" if reference else kind
        lines.append(f"{label}: {body}")
    return lines


def build_prompt(*, verse: dict, window: list[dict], notes: list[dict]) -> str:
    """Assemble the verse, its surroundings and its notes for the model."""
    reference = str(verse.get("reference") or "").strip()
    text = str(verse.get("text") or "").strip()
    if not text:
        raise study_ask.StudyAskError("The verse has no text to explain.")

    lines = [
        f"Verse of the Day: {reference}",
        "",
        "The verse:",
        f"{reference} {text}" if reference else text,
        "",
        "The surrounding passage:",
        *_verse_lines(window),
    ]
    note_lines = _note_lines(notes)
    if note_lines:
        lines += ["", "Study notes for this passage:", *note_lines]
    lines += [
        "",
        "Write the context for the verse of the day. Do not repeat the whole "
        "passage or give an application.",
    ]
    return "\n".join(lines)


async def generate(
    session: Session,
    verse: dict,
    *,
    gateway_url: str,
    internal_secret: str,
    username: str = "default",
) -> dict:
    """Ask the model for the context note and return the fields to store."""
    window, span = _window(session, verse)
    edition = corpus.default_edition(session, str(verse["version"]))
    records = study.notes_for_span(
        session,
        str(verse["version"]),
        span,
        kinds=study.DEFAULT_KINDS,
        edition=edition,
    )
    notes = [record.as_dict() for record in records]
    prompt = build_prompt(verse=verse, window=window, notes=notes)
    body = await study_ask.ask(
        prompt=prompt,
        user=username,
        gateway_url=gateway_url,
        internal_secret=internal_secret,
        system=CONTEXT_SYSTEM_PROMPT,
    )
    return {
        "body": body,
        "model": "assistant",
        "edition_code": edition,
        "verses_used": len(window),
        "notes_used": len(notes),
    }


async def ensure(
    session: Session,
    verse: dict,
    *,
    gateway_url: str,
    internal_secret: str,
    refresh: bool = False,
    username: str = "default",
) -> VerseContext:
    """Return the cached note, or write one now.

    A refresh keeps the old row until the new answer has arrived: a model that
    fails must not erase a note the reader could still read.
    """
    existing = cached(session, verse)
    if existing is not None and not refresh:
        return existing

    made = await generate(
        session,
        verse,
        gateway_url=gateway_url,
        internal_secret=internal_secret,
        username=username,
    )
    row = existing or VerseContext(key=_key_for(verse))
    row.version_code = str(verse["version"])
    row.edition_code = str(made.get("edition_code") or "")
    row.osis = str(verse["osis"])
    row.chapter = int(verse["chapter"])
    row.verse = int(verse["verse"])
    row.reference = str(verse.get("reference") or "")
    row.text = str(verse.get("text") or "")
    row.body = str(made["body"])
    row.model = str(made.get("model") or "")
    row.verses_used = int(made.get("verses_used") or 0)
    row.notes_used = int(made.get("notes_used") or 0)
    row.generated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    log.info("[Bible] wrote verse context for %s (%s)", row.reference, row.version_code)
    return row


async def ensure_daily_loop(
    engine_factory,
    gateway_url: str,
    internal_secret: str,
    *,
    interval: int = INTERVAL_SECONDS,
) -> None:
    """Keep today's Verse of the Day context warm.

    The first pass runs at startup, so a restart fills a missing note without
    waiting for somebody to press Get context. A failure is logged and retried
    after a minute, backing off to the normal interval; it never stops the
    service or another reader's request.
    """
    retry = RETRY_SECONDS
    while True:
        try:
            with Session(engine_factory()) as session:
                verse = votd.pick(session)
                existing = cached(session, verse)
                if existing is None:
                    await ensure(
                        session,
                        verse,
                        gateway_url=gateway_url,
                        internal_secret=internal_secret,
                    )
        except asyncio.CancelledError:
            raise
        except LookupError as exc:
            log.info("[Bible] verse context waiting for a corpus: %s", exc)
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad pass
            log.warning("[Bible] verse context pass failed: %s: %s", type(exc).__name__, exc)
        else:
            retry = RETRY_SECONDS
            await asyncio.sleep(interval)
            continue
        await asyncio.sleep(retry)
        retry = min(interval, retry * 2)
