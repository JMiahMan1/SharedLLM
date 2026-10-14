"""SharedLLM `bible` service — the reading app.

Owns three things and nothing else:

1. **The corpus** — imported public-domain translations in SQLite, served
   offline so opening the app never waits on a network round trip.
2. **Per-reader state** — position, typographic preferences, marks, and the
   reading-event log the achievements are derived from.
3. **The daily rhythm** — verse of the day, devotional, streaks, achievements.

Everything else is somebody else's service: notes are Notes (Nextcloud), study
help is Jarvis through the gateway LLM proxy, stars are geo's ledger, family
sharing is Identity's ``UserActivitySharing``, chat cards go out through
execution. See docs/BIBLE_STUDY.md for the full map.

Fail-fast rule: nothing here invents a URL, a translation, or a default verse.
If a devotional source is not configured the response says which setting to set
instead of quietly showing a blank card.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import aiohttp
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import Session, SQLModel, create_engine, select

from services.config import (
    BIBLE_DATABASE_URL,
    BIBLE_DEVOTIONAL_DIR,
    BIBLE_IMPORT_DIR,
    BLB_BASE_URL,
    EXECUTION_SVC_URL,
    GATEWAY_INTERNAL_URL,
    GEO_SVC_URL,
    IDENTITY_SVC_URL,
    INTERNAL_SECRET,
    TELEMETRY_SVC_URL,
)
from services.bible import achievements as ach
from services.bible import books as book_table
from services.bible import (
    corpus,
    games,
    importer,
    library,
    memorize,
    metrics,
    migrations,
    plans,
    narration as narration_mod,
    provider_cache,
    providers,
    reminders,
    study,
    study_ask,
    votd,
)
from services.bible.corpus import CorpusError
from services.bible.devotionals import registry as devotional_registry
from services.bible.models import (
    utcnow,
    AchievementEarned,
    BibleBook,
    BibleVerse,
    BibleVersion,
    Devotional,
    GameScore,
    MemoryVerse,
    PlanEnrollment,
    ReadingEvent,
    UserBibleState,
    VerseMark,
)
from services.bible.providers import ProviderError, ProviderUnavailable
from services.bible.refs import ReferenceError, format_reference, parse_reference
from services.shared.info_endpoint import info_router

log = logging.getLogger(__name__)

_START_TIME = time.time()

DEFAULT_DATABASE_URL = "sqlite:////data/bible.db"


# ── secret verification (same contract as every other service) ───────────────


def _verify_internal_secret(header_secret: str | None, query_secret: str | None = None) -> bool:
    if not INTERNAL_SECRET:
        return True
    return header_secret == INTERNAL_SECRET or query_secret == INTERNAL_SECRET


@asynccontextmanager
async def lifespan(app: FastAPI):
    from services.config import resolve_runtime_config

    await resolve_runtime_config()
    engine = _db()  # app.state.engine is None until first use; tests set their own
    SQLModel.metadata.create_all(engine)
    try:
        applied = migrations.apply(engine)
    except migrations.MigrationError as exc:
        raise RuntimeError(str(exc)) from exc
    if applied["applied"] or applied["editions_created"]:
        log.info(
            "[Bible] schema updated columns=%s editions=%s",
            applied["applied"] or "-",
            applied["editions_created"] or "-",
        )
    log.info(
        "[Bible] ready db=%s devotional_dir=%s blb=%s",
        _database_url(),
        bool(BIBLE_DEVOTIONAL_DIR),
        bool(BLB_BASE_URL),
    )
    reminder_task = asyncio.create_task(reminders.reminder_loop(_db, TELEMETRY_SVC_URL, INTERNAL_SECRET))
    yield
    reminder_task.cancel()


app = FastAPI(title="SOA Bible Service", lifespan=lifespan)


@app.middleware("http")
async def require_internal_secret_middleware(request, call_next):
    """Reject anything that is not service-to-service.

    The reader is published on its own port as well as behind the gateway, so
    per-route checks are a class of bug waiting to happen. Only ``/health`` is
    exempt -- it is the container healthcheck and carries no Scripture.
    """
    if request.url.path.rstrip("/") in ("/health", ""):
        return await call_next(request)
    secret = request.headers.get("X-Internal-Secret") or request.query_params.get("x_internal_secret")
    if not _verify_internal_secret(secret):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


app.include_router(info_router)


# ── database ────────────────────────────────────────────────────────────────


def _database_url() -> str:
    return BIBLE_DATABASE_URL or DEFAULT_DATABASE_URL


def _engine():
    url = _database_url()
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return create_engine(url, pool_pre_ping=True, **kwargs)


app.state.engine = None  # replaced in lifespan; get_session() builds it lazily


def _db():
    if app.state.engine is None:
        app.state.engine = _engine()
    return app.state.engine


def get_session() -> Session:
    return Session(_db())


# ── health / info ───────────────────────────────────────────────────────────


@app.get("/health")
def health(session: Session = Depends(get_session)):
    """Liveness plus what is actually loaded.

    Reports the corpus plainly rather than returning 200 for a service that can
    serve nothing, because an empty reader is a support question.
    """
    try:
        summary = corpus.corpus_summary(session)
        summary["devotionals"] = corpus.count_rows(session, Devotional)
        editions = corpus.list_editions(session)
    except Exception as exc:  # noqa: BLE001 - health must not raise
        log.exception("[Bible] corpus summary failed")
        summary = {"error": str(exc)}
        editions = []
    return {
        "status": "ok",
        "service": "bible",
        "uptime": time.time() - _START_TIME,
        "versions": summary.get("versions", []),
        "verse_rows": summary.get("verse_rows", 0),
        "editions": [
            {
                "code": entry["code"],
                "version": entry["version"],
                "note_count": entry["note_count"],
            }
            for entry in editions
        ],
        "devotionals": summary.get("devotionals", 0),
        "devotional_sources": devotional_registry.describe_sources(),
    }


# ── corpus / reader ─────────────────────────────────────────────────────────


@app.get("/versions")
def versions(session: Session = Depends(get_session)):
    """Every translation we know about, installed or not.

    The version picker needs the uninstalled ones too: listing only what is
    loaded makes a missing ESV look like a bug, whereas listing it with how an
    administrator adds it makes it a task. ``message`` carries the reason
    nothing can be read, when that is the case, so the reader is never handed a
    command to run on the server.
    """
    try:
        catalogue = corpus.catalogue(session)
    except CorpusError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    installed = [entry for entry in catalogue if entry.get("installed")]
    return {
        "versions": catalogue,
        "message": "" if installed else _no_corpus_message(),
    }


@app.get("/editions")
def editions(
    version: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """Study Bibles available for a translation, installed or catalogued.

    Separate from ``/versions`` on purpose: the reader picks a translation once
    and a study Bible often more than once, and the two choices fail
    differently. Asking for an edition that is not installed is a 400 naming the
    command that installs it; asking for a translation that is not installed is
    also a 400, but for the whole translation.
    """
    try:
        resolved = _require_version(session, version) if version else _default_version(session)
        return {
            "version": resolved,
            "default": corpus.default_edition(session, resolved),
            "editions": corpus.edition_catalogue(session, resolved),
            "other_translations": study.versions_with_notes(session, exclude=resolved),
        }
    except CorpusError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/books")
def books(
    version: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    resolved = _require_version(session, version) if version else _default_version(session)
    return {
        "version": resolved,
        "books": corpus.list_books(session, resolved),
    }


def _require_version(session: Session, version: str) -> str:
    """Validate a requested translation, or fail as a 400 that names the options."""
    try:
        return corpus.require_version(session, version)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_edition(session: Session, version: str, edition: str | None) -> str:
    """Validate a requested study Bible, or fail as a 400 that names the options."""
    try:
        return corpus.require_edition(session, version, edition)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _default_version(session: Session) -> str:
    """The translation a reader with no saved preference gets.

    The manifest marks one translation ``primary`` (NKJV by default); when its
    text is not installed the richest installed translation wins. Never a
    hardcoded code.
    """
    resolved = corpus.default_version_code(session)
    if not resolved:
        raise HTTPException(status_code=503, detail=_no_corpus_message())
    return resolved


def _no_corpus_message() -> str:
    """What a reader is told when no translation is installed at all.

    The manifest marks a public-domain fallback, so reaching this means the
    fallback itself is missing, which an operator has to fix. It says where the
    fix happens; it never asks the reader to run anything.
    """
    fallback = corpus.fallback_code()
    named = f"The {fallback} translation" if fallback else "A public-domain translation"
    return (
        "No Bible text is installed on this server, so there is nothing to read yet. "
        f"{named} is meant to be available as the fallback -- an administrator needs to "
        "load it in Admin > Bible."
    )


@app.get("/passages")
def passages(
    ref: str = Query(..., description="Human reference, e.g. 'John 3:16' or 'Ps 23'"),
    version: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """Read Scripture. The only endpoint the reader blocks on."""
    resolved = _require_version(session, version) if version else _default_version(session)
    try:
        spans = parse_reference(ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    verses = corpus.fetch_passage(session, resolved, spans)
    return {
        "version": resolved,
        "requested": ref,
        "reference": format_reference(spans),
        "spans": [_span_payload(s) for s in spans],
        "verses": verses,
        "count": len(verses),
    }


@app.get("/study/notes")
def study_notes(
    ref: str = Query(..., description="Human reference, e.g. 'John 3:16'"),
    version: str | None = Query(default=None),
    edition: str | None = Query(default=None, description="Which study Bible explains the text"),
    kind: str | None = Query(default=None, description="Commentary, footnote, introduction or heading"),
    cross_version: bool = Query(
        default=False,
        description="Also return notes published against other translations",
    ),
    session: Session = Depends(get_session),
):
    """Study material published alongside the text, kept out of the reading path.

    Three independent choices, all explicit: which translation is being read,
    which study Bible is explaining it, and whether commentary written for a
    *different* translation may join in. The third is off by default and every
    note carries the translation and edition it belongs to, because a commentary
    on the NIV is not a commentary on the NKJV wording and must not read as one.

    Returns an empty ``notes`` list with an explanation when nothing is installed
    for the choice, so the reader can say so instead of showing an empty panel.
    """
    resolved = _require_version(session, version) if version else _default_version(session)
    try:
        spans = parse_reference(ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    wanted = study.DEFAULT_KINDS
    if kind:
        wanted = tuple(k.strip() for k in kind.split(",") if k.strip())
        unknown = [k for k in wanted if k not in study.NOTE_KINDS]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown study note kind {unknown[0]!r}; expected one of {', '.join(study.NOTE_KINDS)}",
            )

    chosen = _require_edition(session, resolved, edition)
    notes: list[dict] = []
    seen: set[tuple] = set()
    for span in spans:
        for record in study.notes_for_span(
            session, resolved, span, kinds=wanted, edition=chosen, cross_version=cross_version
        ):
            if record.identity in seen:
                continue
            seen.add(record.identity)
            notes.append(record.as_dict())
    kinds = study.available_kinds(
        session, resolved, edition=chosen, cross_version=cross_version
    )
    other = study.versions_with_notes(session, exclude=resolved)
    return {
        "version": resolved,
        "edition": chosen,
        "edition_name": _edition_name(session, chosen, resolved),
        "requested": ref,
        "reference": format_reference(spans),
        "kinds": sorted(wanted),
        "available_kinds": kinds,
        "cross_version": cross_version,
        "other_translations": other,
        "editions": study.editions_with_notes(session, resolved),
        "notes": notes,
        "count": len(notes),
        "note": None if notes else _no_notes_message(session, resolved, chosen, other, cross_version),
    }


class StudyAskPayload(BaseModel):
    question: str = Field(..., description="What the reader wants to know")
    ref: str = Field(..., description="The passage being read, e.g. 'John 3:16'")
    version: str | None = None
    edition: str | None = None
    cross_version: bool = False
    depth: str = Field(default="adult", pattern="^(child|teen|adult)$")


def _study_context(session: Session, payload: "StudyAskPayload") -> dict:
    """The passage, its notes and the assembled prompt for a study question.

    Raises the reader's mistakes as 400s (a bad reference, an empty or
    oversized question) before any model is called.
    """
    resolved = _require_version(session, payload.version) if payload.version else _default_version(session)
    try:
        spans = parse_reference(payload.ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    verses = corpus.fetch_passage(session, resolved, spans)
    chosen = _require_edition(session, resolved, payload.edition)

    notes: list[dict] = []
    seen: set[tuple] = set()
    for span in spans:
        for record in study.notes_for_span(
            session,
            resolved,
            span,
            kinds=study.DEFAULT_KINDS,
            edition=chosen,
            cross_version=payload.cross_version,
        ):
            if record.identity in seen:
                continue
            seen.add(record.identity)
            notes.append(record.as_dict())

    reference = format_reference(spans)
    try:
        prompt = study_ask.build_prompt(reference=reference, verses=verses, notes=notes, question=payload.question)
    except study_ask.StudyAskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"reference": reference, "version": resolved, "edition": chosen, "verses": verses, "notes": notes, "prompt": prompt}


@app.post("/study/ask/stream")
async def study_ask_stream_route(
    payload: StudyAskPayload,
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """The same grounded answer as ``/study/ask``, written as it arrives.

    NDJSON events: ``start`` (what the answer is built from), ``text``
    (a piece of the answer), then ``done`` (the whole answer and its reference
    chips) or ``error`` (the reason, never a partial answer passed off as whole).
    Mistakes in the request are still plain 400s before anything streams.
    """
    ctx = _study_context(session, payload)
    _record_event(session, username, "assistant_ask", ref=ctx["reference"])
    edition_name = _edition_name(session, ctx["edition"], ctx["version"])

    async def events():
        head = {
            "type": "start",
            "reference": ctx["reference"],
            "version": ctx["version"],
            "edition": ctx["edition"],
            "edition_name": edition_name,
            "depth": payload.depth,
            "verses_used": len(ctx["verses"]),
            "notes_used": len(ctx["notes"]),
        }
        yield json.dumps(head) + "\n"
        parts: list[str] = []
        try:
            async for piece in study_ask.ask_stream(
                prompt=ctx["prompt"],
                user=username,
                gateway_url=GATEWAY_INTERNAL_URL,
                internal_secret=INTERNAL_SECRET,
                depth=payload.depth,
            ):
                parts.append(piece)
                yield json.dumps({"type": "text", "text": piece}) + "\n"
        except study_ask.StudyAskUnavailable as exc:
            yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
            return
        answer = "".join(parts).strip()
        yield json.dumps({"type": "done", "answer": answer, "references": study_ask.find_references(answer)}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/study/ask")
async def study_ask_route(
    payload: StudyAskPayload,
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """Ask about the passage on screen, with that passage in front of the model.

    The reader's own question plus the verses they are reading plus whatever
    study notes are stored for those verses. Nothing else is fetched, so the
    answer can be checked against what is on the screen, and the same passage
    and study Bible choices they made for the notes apply here.

    A question the passage does not answer comes back as an answer saying so:
    the prompt asks for that rather than a guess, because a reader cannot tell
    a confident invention from the text afterwards.

    The reading event is recorded (``assistant_ask``) but never the question or
    the answer, because reading events are metadata only.
    """
    ctx = _study_context(session, payload)
    reference, resolved, chosen, verses, notes, prompt = (
        ctx["reference"], ctx["version"], ctx["edition"], ctx["verses"], ctx["notes"], ctx["prompt"]
    )
    try:
        answer = await study_ask.ask(
            prompt=prompt,
            user=username,
            gateway_url=GATEWAY_INTERNAL_URL,
            internal_secret=INTERNAL_SECRET,
            depth=payload.depth,
        )
    except study_ask.StudyAskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except study_ask.StudyAskUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _record_event(session, username, "assistant_ask", ref=reference)
    return {
        "reference": reference,
        "version": resolved,
        "edition": chosen,
        "edition_name": _edition_name(session, chosen, resolved),
        "question": payload.question,
        "answer": answer,
        "depth": payload.depth,
        "references": study_ask.find_references(answer),
        "verses_used": len(verses),
        "notes_used": len(notes),
    }


def _edition_name(session: Session, edition: str, version: str) -> str:
    for entry in corpus.list_editions(session, version):
        if entry["code"] == edition:
            return str(entry["name"])
    return edition


def _no_notes_message(
    session: Session,
    version: str,
    edition: str,
    other: list[dict],
    cross_version: bool,
) -> str:
    if not cross_version and other:
        names = ", ".join(f"{entry['edition_name']} ({entry['version_name']})" for entry in other[:3])
        return (
            f"{_edition_name(session, edition, version)} has no notes for this passage, but "
            f"this install has study notes for {names}. Turn on 'notes from other "
            "translations' to read them here."
        )
    if cross_version:
        return (
            "No study notes are installed for this passage in any translation. "
            "A study Bible has to be added in Admin > Bible."
        )
    return (
        f"{_edition_name(session, edition, version)} has no study notes installed. "
        "Public-domain text carries none, so this is expected unless a study Bible "
        "was added in Admin > Bible."
    )


def _span_payload(span) -> dict:
    return {
        "book": span.book,
        "book_name": book_table.BOOK_BY_OSIS.get(span.book, {}).get("name", span.book),
        "chapter_start": span.chapter_start,
        "chapter_end": span.resolved_chapter_end,
        "verse_start": span.verse_start,
        "verse_end": span.verse_end,
        "whole_book": span.whole_book,
        "display": span.display(),
    }


@app.get("/voices")
async def voices():
    """The voices the speech engine offers, so the reader can pick one.

    ``list_voices`` in the engine answers without touching the model files, so
    this succeeds even when narration cannot. That difference is deliberate: the
    reader should be able to see that voices exist before pressing Play and
    discovering they are not installed.
    """
    if not EXECUTION_SVC_URL:
        raise HTTPException(
            status_code=503,
            detail=(
                "The execution service is not configured, so Scripture cannot be read "
                "aloud. Set EXECUTION_SVC_URL (compose) or execution_svc_url (Identity "
                "global setting) and restart the bible service."
            ),
        )
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{EXECUTION_SVC_URL}/execute/tts/voices",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                raw = await resp.text()
    except Exception as exc:  # noqa: BLE001 - the reader is told, not just the log
        raise HTTPException(
            status_code=503, detail=f"The speech engine could not be reached: {exc}"
        ) from exc
    if resp.status >= 400:
        raise HTTPException(status_code=503, detail=f"The speech engine returned HTTP {resp.status}.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as parse_error:
        raise HTTPException(
            status_code=503,
            detail=f"The speech engine returned something that is not JSON: {raw[:200]}",
        ) from parse_error
    voices_list = payload.get("voices") if isinstance(payload, dict) else None
    if not isinstance(voices_list, list):
        raise HTTPException(status_code=503, detail="The speech engine returned an unexpected response.")
    return {"voices": [str(v) for v in voices_list], "count": len(voices_list)}


@app.get("/narration")
async def narration(
    ref: str = Query(..., description="Human reference, e.g. 'John 3:16' or 'Ps 23'"),
    version: str | None = Query(default=None),
    voice: str | None = Query(default=None, description="Voice id from /voices"),
    session: Session = Depends(get_session),
):
    """Narrate a passage, or say clearly why it cannot be narrated.

    Only passages are accepted: the script is built from the imported text, so
    this endpoint cannot be used to make the server say arbitrary words. Audio is
    cached per translation, reference and voice, so the second play is instant.
    """
    resolved = _require_version(session, version) if version else _default_version(session)
    try:
        spans = parse_reference(ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reference = format_reference(spans)
    try:
        return await narration_mod.narrate(
            session,
            version=resolved,
            spans=spans,
            reference=reference,
            voice=voice,
            execution_url=EXECUTION_SVC_URL,
            internal_secret=INTERNAL_SECRET,
        )
    except narration_mod.NarrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except narration_mod.NarrationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/narration/plan")
async def narration_plan(
    ref: str = Query(..., description="Human reference, e.g. 'John 3:16' or 'Ps 23'"),
    version: str | None = Query(default=None),
    voice: str | None = Query(default=None, description="Voice id from /voices"),
    session: Session = Depends(get_session),
):
    """How a passage will be spoken: how many pieces, and which are already cached.

    The reader's client asks this before it plays anything, so it can start the
    first piece immediately and fetch the rest while the reader is listening.
    Nothing is synthesised to answer it, so asking costs nothing.
    """
    resolved = _require_version(session, version) if version else _default_version(session)
    try:
        spans = parse_reference(ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reference = format_reference(spans)
    try:
        return narration_mod.plan(
            session,
            version=resolved,
            spans=spans,
            reference=reference,
            voice=voice,
        )
    except narration_mod.NarrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/narration/chunk")
async def narration_chunk(
    ref: str = Query(..., description="Human reference, e.g. 'John 3:16' or 'Ps 23'"),
    index: int = Query(..., ge=0, description="Which piece, from /narration/plan"),
    version: str | None = Query(default=None),
    voice: str | None = Query(default=None, description="Voice id from /voices"),
    session: Session = Depends(get_session),
):
    """Speak one piece of a passage, or serve it if it has been spoken before.

    A piece is small enough that sound starts while the next is still being
    rendered, and it is cached on its own, so a verse played after its chapter
    is already spoken costs nothing to speak again.
    """
    resolved = _require_version(session, version) if version else _default_version(session)
    try:
        spans = parse_reference(ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reference = format_reference(spans)
    try:
        return await narration_mod.narrate_chunk(
            session,
            version=resolved,
            spans=spans,
            reference=reference,
            voice=voice,
            index=index,
            execution_url=EXECUTION_SVC_URL,
            internal_secret=INTERNAL_SECRET,
        )
    except narration_mod.NarrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except narration_mod.NarrationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/search")
def search(
    q: str = Query(..., min_length=2),
    version: str | None = Query(default=None),
    book: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    resolved = _require_version(session, version) if version else _default_version(session)
    osis = None
    if book:
        osis = book_table.resolve_book(book)
        if not osis:
            raise HTTPException(status_code=400, detail=f"Unknown book {book!r}.")
    hits = corpus.search_verses(session, resolved, q, osis=osis, limit=limit)
    return {"version": resolved, "query": q, "results": hits, "count": len(hits)}


@app.get("/verse-of-day")
def verse_of_day(
    day: str | None = Query(default=None, description="ISO date; defaults to today"),
    version: str | None = Query(default=None),
    scope: str = Query(default="all", pattern="^(all|ot|nt)$"),
    session: Session = Depends(get_session),
):
    """The verse the dashboard widget and the Android home screen both show.

    Deterministic by date + version, so the two surfaces cannot disagree.
    """
    when = _parse_day(day)
    resolved_version = _require_version(session, version) if version else None
    try:
        return votd.pick(session, day=when, version=resolved_version, scope=scope)
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parse_day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{raw!r} is not an ISO date (YYYY-MM-DD).") from exc


# ── devotionals ─────────────────────────────────────────────────────────────


@app.get("/devotional")
def devotional(
    day: str | None = Query(default=None, description="ISO date; defaults to today"),
    work: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """Today's devotional, from the highest-priority configured source.

    Sources are a plugin list (see ``services/bible/devotionals``): BLB first
    (link-out to their daily devotionals), then a local directory of markdown
    files. A source that is not configured or that fails is reported in
    ``skipped`` with the setting to fix -- never a silent blank.
    """
    when = _parse_day(day)
    stored = _stored_devotional(session, when or date.today(), work)
    if stored:
        return {"source": "library", "entry": stored, "stored": True}
    return devotional_registry.daily(day=when, work=work)


def _stored_devotional(session: Session, when: date, work: str | None) -> dict | None:
    stmt = select(Devotional).where(Devotional.day_of_year == _doy(when))
    rows = list(session.exec(stmt))
    if work:
        rows = [r for r in rows if r.work == work]
    if not rows:
        return None
    row = sorted(rows, key=lambda r: (r.source != "library", r.source))[0]
    return {
        "source": row.source,
        "work": row.work,
        "title": row.title,
        "reference": row.reference,
        "text": row.text,
        "kind": row.kind,
        "url": row.url,
        "day_of_year": row.day_of_year,
    }


def _doy(when: date) -> int:
    return devotional_registry.day_of_year(when)


@app.get("/devotional/sources")
def devotional_sources():
    return {"sources": devotional_registry.describe_sources()}


# ── reading state ───────────────────────────────────────────────────────────


class MarkPayload(BaseModel):
    version_code: str = ""
    ref: str
    kind: str = Field(default="highlight", pattern="^(highlight|bookmark|note)$")
    color: str = ""
    note_path: str = ""
    note_preview: str = ""


@app.get("/marks")
def list_marks(
    username: str = Query(...),
    osis: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    stmt = select(VerseMark).where(VerseMark.username == username)
    rows = list(session.exec(stmt))
    if osis:
        rows = [r for r in rows if r.osis == osis]
    return {"marks": [_mark_payload(m) for m in _sorted_marks(rows)]}


def _sorted_marks(rows: list[VerseMark]) -> list[VerseMark]:
    order = book_table.BOOK_ORDER
    return sorted(rows, key=lambda m: (order.get(m.osis, 999), m.chapter, m.verse_start))


def _mark_payload(m: VerseMark) -> dict:
    return {
        "id": m.id,
        "version_code": m.version_code,
        "osis": m.osis,
        "book_name": book_table.BOOK_BY_OSIS.get(m.osis, {}).get("name", m.osis),
        "chapter": m.chapter,
        "verse_start": m.verse_start,
        "verse_end": m.verse_end,
        "kind": m.kind,
        "color": m.color,
        "note_path": m.note_path,
        "note_preview": m.note_preview,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


@app.put("/marks")
def put_mark(
    payload: MarkPayload,
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """Create or update the mark covering a reference.

    Idempotent on (username, osis, chapter, verse_start, kind) so double-tapping
    a verse in the reader does not stack two highlights. ``note`` is a kind of
    its own: a reader may write a note about a verse without wanting it
    highlighted, and the reader needs to know which verses have one so it can
    mark them. The note itself lives in Nextcloud; this row is the index.
    """
    spans = _require_one_span(payload.ref)
    span = spans[0]
    stmt = select(VerseMark).where(
        VerseMark.username == username,
        VerseMark.osis == span.book,
        VerseMark.chapter == span.chapter_start,
        VerseMark.verse_start == span.verse_start,
        VerseMark.kind == payload.kind,
    )
    existing = session.exec(stmt).first()
    now = utcnow()
    if existing is None:
        existing = VerseMark(
            username=username,
            version_code=payload.version_code,
            osis=span.book,
            chapter=span.chapter_start,
            verse_start=span.verse_start,
            verse_end=span.verse_end,
            kind=payload.kind,
            color=payload.color,
            note_path=payload.note_path,
            note_preview=payload.note_preview,
        )
        session.add(existing)
    else:
        existing.verse_end = span.verse_end
        existing.color = payload.color or existing.color
        existing.note_path = payload.note_path or existing.note_path
        existing.note_preview = payload.note_preview or existing.note_preview
        existing.version_code = payload.version_code or existing.version_code
        existing.updated_at = now
    session.commit()
    session.refresh(existing)
    _record_event(session, username, "mark_created", ref=payload.ref, value=1)
    return {"mark": _mark_payload(existing)}


@app.delete("/marks/{mark_id}")
def delete_mark(mark_id: int, username: str = Query(...), session: Session = Depends(get_session)):
    mark = session.get(VerseMark, mark_id)
    if mark is None or mark.username != username:
        raise HTTPException(status_code=404, detail="No such mark for this user.")
    session.delete(mark)
    session.commit()
    return {"deleted": mark_id}


def _require_one_span(ref: str):
    try:
        spans = parse_reference(ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if len(spans) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"A mark covers one passage; {ref!r} is {len(spans)} passages.",
        )
    if spans[0].whole_book or spans[0].verse_end is None:
        raise HTTPException(
            status_code=400,
            detail=f"Mark a chapter or a verse range, not a whole book or chapter ({ref!r}).",
        )
    return spans


class StatePayload(BaseModel):
    book: str | None = None
    chapter: int | None = Field(default=None, ge=1)
    verse: int | None = Field(default=None, ge=1)
    default_version: str | None = None
    default_edition: str | None = None
    favorite_version: str | None = None
    compare_version: str | None = None
    cross_version_notes: bool | None = None
    show_notes: bool | None = None
    font_scale: float | None = Field(default=None, ge=0.6, le=2.5)
    line_height: float | None = Field(default=None, ge=1.0, le=3.0)
    theme: str | None = Field(default=None, pattern="^(serif|sans)$")
    page: str | None = Field(default=None, pattern="^(dark|sepia|light)$")
    reminder_time: str | None = Field(default=None, pattern="^$|^([01][0-9]|2[0-3]):[0-5][0-9]$")
    read_aloud_voice: str | None = None
    split_view: str | None = Field(default=None, pattern="^(compare|parallel)$")


@app.get("/state")
def get_state(username: str = Query(...), session: Session = Depends(get_session)):
    state = session.get(UserBibleState, username)
    if state is None:
        return {"username": username, "position": None, "preferences": _default_preferences(session)}
    return {"username": username, "position": _state_position(state), "preferences": _preferences(state)}


def _state_position(state: UserBibleState) -> dict | None:
    if not state.last_book:
        return None
    return {"book": state.last_book, "chapter": state.last_chapter, "verse": state.last_verse}


def _default_preferences(session: Session) -> dict:
    version = _default_version(session)
    return {
        "default_version": version,
        "default_edition": corpus.default_edition(session, version),
        "favorite_version": "",
        "compare_version": "",
        "cross_version_notes": False,
        "show_notes": False,
        "font_scale": 1.0,
        "line_height": 1.6,
        "theme": "serif",
        "page": "dark",
        "reminder_time": "",
        "read_aloud_voice": "",
        "split_view": "compare",
    }


def _preferences(state: UserBibleState) -> dict:
    return {
        "default_version": state.default_version,
        "default_edition": state.default_edition,
        "favorite_version": state.favorite_version,
        "compare_version": state.compare_version,
        "cross_version_notes": state.cross_version_notes,
        "show_notes": state.show_notes,
        "font_scale": state.font_scale,
        "line_height": state.line_height,
        "theme": state.theme,
        "page": state.page,
        "reminder_time": state.reminder_time,
        "read_aloud_voice": state.read_aloud_voice,
        "split_view": state.split_view,
    }


@app.put("/state")
def put_state(
    payload: StatePayload,
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """Remember where the reader is and how they like their text.

    Called on every chapter change and on open, so it must stay cheap.
    """
    state = session.get(UserBibleState, username)
    if state is None:
        state = UserBibleState(username=username)
        session.add(state)
    if payload.book is not None:
        osis = book_table.resolve_book(payload.book)
        if not osis:
            raise HTTPException(status_code=400, detail=f"Unknown book {payload.book!r}.")
        entry = book_table.book(osis)
        if payload.chapter is not None and payload.chapter > entry["chapters"]:
            raise HTTPException(
                status_code=400,
                detail=f"{entry['name']} has {entry['chapters']} chapters, not {payload.chapter}.",
            )
        state.last_book = osis
        state.last_chapter = payload.chapter or 1
        state.last_verse = payload.verse or 1
    if payload.default_version is not None:
        state.default_version = _require_version(session, payload.default_version)
    if payload.default_edition is not None:
        state.default_edition = _require_edition(
            session, state.default_version, payload.default_edition
        )
    elif payload.default_version is not None:
        # The translation changed but no study Bible came with it. Keeping the old
        # one would leave a preference pointing at an edition that explains a
        # different translation, so it follows the translation instead of failing
        # on the reader's next study panel.
        state.default_edition = corpus.default_edition(session, state.default_version)
    if payload.favorite_version is not None:
        # An empty string is how the reader says "no favourite yet", so it is
        # cleared rather than run through _require_version, which would refuse
        # it as an unknown translation.
        state.favorite_version = (
            _require_version(session, payload.favorite_version) if payload.favorite_version else ""
        )
    if payload.compare_version is not None:
        # Blank turns the comparison off rather than falling back to some other
        # translation, so what the reader is reading stays what they chose. A
        # version identical to the one they are already reading would render the
        # same words twice, so it is refused rather than accepted as a no-op.
        if not payload.compare_version:
            state.compare_version = ""
        elif payload.compare_version == (state.default_version or _default_version(session)):
            raise HTTPException(
                400,
                "Pick a different translation to compare against, or turn the comparison off.",
            )
        else:
            state.compare_version = _require_version(session, payload.compare_version)
    if payload.cross_version_notes is not None:
        state.cross_version_notes = payload.cross_version_notes
    for attr in ("show_notes", "font_scale", "line_height", "theme", "page", "reminder_time", "read_aloud_voice", "split_view"):
        value = getattr(payload, attr)
        if value is not None:
            setattr(state, attr, value)
    state.last_read_at = utcnow()
    session.commit()
    session.refresh(state)
    return {"username": username, "position": _state_position(state), "preferences": _preferences(state)}


class EventPayload(BaseModel):
    kind: str = Field(..., pattern="^(app_open|chapter_read|chapter_complete|verse_tapped|search|note_created|plan_day_completed|plan_started|quiz_played|quiz_correct|memory_recall|game_played|assistant_ask|share_tap|blb_link_tap|devotional_opened)$")
    ref: str = ""
    value: int = 0


@app.post("/events")
def record_event(
    payload: EventPayload,
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """Log one thing the reader did. Metadata only, never content."""
    _record_event(session, username, payload.kind, ref=payload.ref, value=payload.value)
    return {"ok": True}


def _record_event(session: Session, username: str, kind: str, ref: str = "", value: int = 0) -> ReadingEvent:
    today = date.today().isoformat()
    row = ReadingEvent(username=username, kind=kind, ref=ref[:120], value=int(value), day=today)
    session.add(row)
    if kind == "app_open":
        state = session.get(UserBibleState, username)
        if state is not None:
            state.last_open_day = today
            session.add(state)
    session.commit()
    session.refresh(row)
    return row


# ── games ───────────────────────────────────────────────────────────────────


class GameAttempt(BaseModel):
    seed: str = Field(..., min_length=1, max_length=40)
    version: str | None = None
    answers: list[str | None] = Field(..., max_length=50)
    duration_ms: int = Field(default=0, ge=0, le=3_600_000)


def _game_version(session: Session, version: str | None) -> str:
    if version:
        return _require_version(session, version)
    code = _default_version(session)
    if not code:
        raise HTTPException(503, "No Bible text is installed, so there is nothing to build a game from. Load a translation in Admin > Bible.")
    return code


@app.get("/games")
def list_games():
    """The games on offer."""
    return {"modes": games.catalogue(), "today": games.today()}


@app.get("/games/leaderboard")
async def games_leaderboard(
    viewer: str = Query(...),
    day: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """Today's daily-quiz scores: the viewer's own, plus every family member
    who shares their Bible activity with them. A member who does not share is
    simply absent, exactly as the activity feed treats them."""
    when = day or games.today()
    rows = session.exec(
        select(GameScore).where(GameScore.mode == "daily", GameScore.day == when, GameScore.counted == True)  # noqa: E712
    ).all()
    entries = []
    for row in rows:
        if row.username != viewer and await _consent(row.username, viewer, "bible") is not True:
            continue
        entries.append(
            {
                "username": row.username,
                "correct": row.correct,
                "total": row.total,
                "duration_ms": row.duration_ms,
                "you": row.username == viewer,
            }
        )
    entries.sort(key=lambda e: (-e["correct"], e["duration_ms"]))
    return {"day": when, "entries": entries}


@app.get("/games/{mode}")
def get_game(
    mode: str,
    username: str = Query(...),
    seed: str | None = Query(default=None),
    version: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """A game's questions, without the answers."""
    code = _game_version(session, version)
    try:
        resolved = games.resolve_seed(mode, seed)
        questions = games.build(session, mode, resolved, code)
    except games.GameError as exc:
        raise HTTPException(400 if "Unknown game" in str(exc) or "seed" in str(exc) else 503, str(exc)) from exc
    spec = games.MODES[mode]
    played = None
    if spec.get("daily"):
        row = session.exec(
            select(GameScore).where(
                GameScore.username == username, GameScore.mode == mode, GameScore.day == resolved, GameScore.counted == True  # noqa: E712
            )
        ).first()
        if row is not None:
            played = {"correct": row.correct, "total": row.total, "duration_ms": row.duration_ms}
    return {
        "mode": mode,
        "name": spec["name"],
        "seed": resolved,
        "version": code,
        "seconds": spec.get("seconds"),
        "questions": [q.public() for q in questions],
        "played_today": played,
    }


@app.post("/games/{mode}/attempt")
async def attempt_game(
    mode: str,
    payload: GameAttempt,
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """Score an attempt by regenerating the game; the client never holds the key.

    The first daily-quiz attempt of the day counts: it goes on the family
    leaderboard and banks one star per right answer. Anything else is practice.
    """
    code = _game_version(session, payload.version)
    try:
        questions = games.build(session, mode, payload.seed, code)
        results = games.score(questions, payload.answers)
    except games.GameError as exc:
        raise HTTPException(400, str(exc)) from exc
    correct = sum(1 for r in results if r["correct"])
    daily = bool(games.MODES[mode].get("daily"))
    today = games.today()
    counted = (
        daily
        and payload.seed == today
        and session.exec(
            select(GameScore).where(
                GameScore.username == username, GameScore.mode == mode, GameScore.day == today, GameScore.counted == True  # noqa: E712
            )
        ).first()
        is None
    )
    session.add(
        GameScore(
            username=username,
            mode=mode,
            day=today,
            seed=payload.seed,
            version_code=code,
            correct=correct,
            total=len(questions),
            duration_ms=payload.duration_ms,
            counted=counted,
        )
    )
    session.commit()
    _record_event(session, username, "quiz_played", ref=mode, value=len(questions))
    if correct:
        _record_event(session, username, "quiz_correct", ref=mode, value=correct)

    if counted and correct:
        stars = await _award_stars(
            username, [{"stars": correct, "reason": "game", "detail": f"Bible daily quiz {correct}/{len(questions)}"}]
        )
    elif counted:
        stars = {"granted": 0, "status": "no right answers, so no stars this time"}
    elif daily:
        stars = {"granted": 0, "status": "practice: today's stars came from your first try"}
    else:
        stars = {"granted": 0, "status": "practice game: stars come from the daily quiz"}
    announced = None
    if counted:
        announced = await _share_card(
            username,
            {
                "message": f"{username} scored {correct}/{len(questions)} on today's Bible quiz",
                "card_kind": "game",
                "card_title": "Daily Bible Quiz",
                "card_detail": f"{correct} of {len(questions)} right",
                "card_stars": correct,
                "card_stats": [
                    {"label": "Score", "value": f"{correct}/{len(questions)}"},
                    {"label": "Time", "value": f"{round(payload.duration_ms / 1000)}s"},
                ],
            },
        )
    badges = await get_achievements(username=username, session=session)
    return {
        "announced": announced,
        "mode": mode,
        "correct": correct,
        "total": len(questions),
        "duration_ms": payload.duration_ms,
        "counted": counted,
        "results": results,
        "stars": stars,
        "newly_earned": badges["newly_earned"],
    }


# ── memorisation ────────────────────────────────────────────────────────────


class MemoryAdd(BaseModel):
    ref: str | None = Field(default=None, max_length=80)
    set_id: str | None = Field(default=None, max_length=60)
    version: str | None = None


class MemoryRecall(BaseModel):
    typed: str = Field(default="", max_length=4000)


def _reader_version(session: Session, username: str, version: str | None) -> str:
    """The translation to memorise in: the one asked for, else the reader's own."""
    if version:
        return _require_version(session, version)
    state = session.get(UserBibleState, username)
    if state is not None and state.default_version:
        try:
            return _require_version(session, state.default_version)
        except HTTPException:
            pass
    return _game_version(session, None)


def _memory_payload(row: MemoryVerse, today: str, text: str | None = None) -> dict:
    out = {
        "id": row.id,
        "ref": row.ref,
        "set_id": row.set_id,
        "version": row.version_code,
        "level": row.level,
        "stage": memorize.STAGES[min(row.level, memorize.MASTERED)],
        "mastered": row.level >= memorize.MASTERED,
        "due_on": row.due_on,
        "due": row.due_on <= today,
        "reviews": row.reviews,
        "lapses": row.lapses,
        "last_score": row.last_score,
    }
    if text is not None:
        out["text"] = text
        out["cloze"] = memorize.cloze(text, row.reviews)
        out["first_letters"] = memorize.first_letters(text)
    return out


def _my_memory(session: Session, username: str) -> list[MemoryVerse]:
    return list(
        session.exec(select(MemoryVerse).where(MemoryVerse.username == username).order_by(MemoryVerse.due_on, MemoryVerse.id))
    )


def _memory_overview(session: Session, username: str) -> dict:
    today = date.today().isoformat()
    rows = _my_memory(session, username)
    by_set: dict[str, int] = {}
    for row in rows:
        if row.set_id:
            by_set[row.set_id] = by_set.get(row.set_id, 0) + 1
    return {
        "verses": [_memory_payload(r, today) for r in rows],
        "due_count": sum(1 for r in rows if r.due_on <= today),
        "mastered": sum(1 for r in rows if r.level >= memorize.MASTERED),
        "sets": [
            {"id": s["id"], "name": s["name"], "blurb": s["blurb"], "count": len(s["refs"]), "added": by_set.get(s["id"], 0)}
            for s in memorize.load_sets()
        ],
    }


@app.get("/memorize")
def memory_overview(username: str = Query(...), session: Session = Depends(get_session)):
    """The reader's memory verses, what is due, and the built-in sets."""
    return _memory_overview(session, username)


@app.post("/memorize")
def memory_add(payload: MemoryAdd, username: str = Query(...), session: Session = Depends(get_session)):
    """Start memorising a verse, or every verse of a built-in set.

    A verse already on the reader's list is left where it is on the ladder.
    """
    if bool(payload.ref) == bool(payload.set_id):
        raise HTTPException(400, "Send either a verse reference or a memory set, not both.")
    version = _reader_version(session, username, payload.version)
    if payload.set_id:
        found = memorize.find_set(payload.set_id)
        if found is None:
            raise HTTPException(404, f"There is no memory set called {payload.set_id!r}.")
        refs, set_id = found["refs"], found["id"]
    else:
        refs, set_id = [payload.ref or ""], ""
    existing = {r.ref for r in _my_memory(session, username)}
    today = date.today().isoformat()
    added, skipped = [], []
    for ref in refs:
        try:
            span, display, _text = memorize.resolve(session, ref, version)
        except (memorize.MemoryError_, ReferenceError) as exc:
            if payload.ref:
                raise HTTPException(400, str(exc)) from exc
            skipped.append(f"{ref}: {exc}")
            continue
        if display in existing:
            continue
        session.add(
            MemoryVerse(
                username=username,
                ref=display,
                osis=span.book,
                chapter=span.chapter_start,
                verse_start=span.verse_start,
                verse_end=span.verse_end or span.verse_start,
                version_code=version,
                set_id=set_id,
                due_on=today,
            )
        )
        existing.add(display)
        added.append(display)
    session.commit()
    return {**_memory_overview(session, username), "added": added, "skipped": skipped}


@app.delete("/memorize/{verse_id}")
def memory_remove(verse_id: int, username: str = Query(...), session: Session = Depends(get_session)):
    row = session.get(MemoryVerse, verse_id)
    if row is None or row.username != username:
        raise HTTPException(404, "That verse is not on your memory list.")
    session.delete(row)
    session.commit()
    return _memory_overview(session, username)


@app.get("/memorize/due")
def memory_due(
    username: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    """Verses to practise now, each with its text and the help its stage gives."""
    today = date.today().isoformat()
    out = []
    for row in _my_memory(session, username):
        if row.due_on > today:
            continue
        try:
            _span, _display, text = memorize.resolve(session, row.ref, row.version_code)
        except (memorize.MemoryError_, ReferenceError, CorpusError):
            continue
        out.append(_memory_payload(row, today, text))
        if len(out) >= limit:
            break
    return {"due": out, "count": len(out)}


@app.post("/memorize/{verse_id}/recall")
async def memory_recall(
    verse_id: int,
    payload: MemoryRecall,
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """Grade one recall against the text and move the verse on the ladder."""
    row = session.get(MemoryVerse, verse_id)
    if row is None or row.username != username:
        raise HTTPException(404, "That verse is not on your memory list.")
    try:
        _span, _display, text = memorize.resolve(session, row.ref, row.version_code)
    except (memorize.MemoryError_, ReferenceError) as exc:
        raise HTTPException(503, f"{row.ref} cannot be read in {row.version_code}: {exc}") from exc
    result = memorize.grade(text, payload.typed)
    was = row.level
    row.level, row.due_on = memorize.advance(row.level, result["passed"])
    row.reviews += 1
    if not result["passed"]:
        row.lapses += 1
    row.last_score = result["score"]
    session.add(row)
    session.commit()
    session.refresh(row)
    _record_event(session, username, "memory_recall", ref=row.ref, value=1 if result["passed"] else 0)
    if was < memorize.MASTERED <= row.level:
        await _share_card(
            username,
            {
                "message": f"{username} memorised {row.ref}",
                "card_kind": "activity",
                "card_title": row.ref,
                "card_detail": text[:280],
                "card_stats": [{"label": "Reviews", "value": str(row.reviews)}],
            },
        )
    badges = await get_achievements(username=username, session=session)
    return {
        **result,
        "text": text,
        "verse": _memory_payload(row, date.today().isoformat()),
        "level_before": was,
        "newly_earned": badges["newly_earned"],
    }


# ── reading plans ───────────────────────────────────────────────────────────


def _plan_days(row: PlanEnrollment) -> list[int]:
    return sorted({int(d) for d in row.completed_days.split(",") if d.strip().isdigit()})


def _active_plan(session: Session, username: str, plan_id: str) -> PlanEnrollment | None:
    return session.exec(
        select(PlanEnrollment).where(
            PlanEnrollment.username == username, PlanEnrollment.plan_id == plan_id, PlanEnrollment.active == True  # noqa: E712
        )
    ).first()


def _plan_or_404(plan_id: str) -> dict:
    try:
        return plans.find(plan_id)
    except plans.PlanError as exc:
        raise HTTPException(404, str(exc)) from exc


def _enrollment_payload(row: PlanEnrollment) -> dict:
    plan = plans.find(row.plan_id)
    state = plans.progress(row.plan_id, row.started_on, _plan_days(row))
    current = state["current_day"]
    return {
        "plan_id": row.plan_id,
        "name": plan["name"],
        "started_on": row.started_on,
        "finished_on": row.finished_on or None,
        **state,
        "today": {"day": current, "readings": plans.day_readings(row.plan_id, current)} if current else None,
    }


@app.get("/plans")
def list_plans(username: str = Query(...), session: Session = Depends(get_session)):
    """The plan catalogue and the reader's plans in progress."""
    mine = session.exec(
        select(PlanEnrollment).where(PlanEnrollment.username == username, PlanEnrollment.active == True)  # noqa: E712
    ).all()
    return {"catalogue": plans.catalogue(), "mine": [_enrollment_payload(r) for r in mine]}


@app.get("/plans/{plan_id}")
def get_plan(plan_id: str, username: str = Query(...), session: Session = Depends(get_session)):
    """Every day of a plan, with what the reader has done."""
    plan = _plan_or_404(plan_id)
    row = _active_plan(session, username, plan_id)
    done = set(_plan_days(row)) if row else set()
    return {
        "id": plan["id"],
        "name": plan["name"],
        "blurb": plan["blurb"],
        "days": [
            {"day": i + 1, "readings": list(readings), "done": (i + 1) in done}
            for i, readings in enumerate(plans.schedule(plan_id))
        ],
        "enrollment": _enrollment_payload(row) if row else None,
    }


@app.get("/plans/{plan_id}/family")
async def plan_family(plan_id: str, viewer: str = Query(...), session: Session = Depends(get_session)):
    """Who else in the family is reading this plan, and how far along.

    Following the same plan is the reading group: there is nothing to join.
    Only members who share their Bible activity with the viewer appear, the
    same rule as the family feed; everyone else is simply absent.
    """
    _plan_or_404(plan_id)
    rows = session.exec(
        select(PlanEnrollment).where(
            PlanEnrollment.plan_id == plan_id, PlanEnrollment.active == True, PlanEnrollment.username != viewer  # noqa: E712
        )
    ).all()
    members = []
    for row in rows:
        if await _consent(row.username, viewer, "bible") is not True:
            continue
        state = plans.progress(plan_id, row.started_on, _plan_days(row))
        members.append(
            {
                "username": row.username,
                "completed": state["completed"],
                "percent": state["percent"],
                "behind": state["behind"],
                "finished": state["finished"],
            }
        )
    members.sort(key=lambda m: -m["completed"])
    return {"plan_id": plan_id, "members": members}


@app.post("/plans/{plan_id}/start")
def start_plan(plan_id: str, username: str = Query(...), session: Session = Depends(get_session)):
    """Start a plan today. Starting one already under way starts it over."""
    _plan_or_404(plan_id)
    existing = _active_plan(session, username, plan_id)
    if existing is not None:
        existing.active = False
        session.add(existing)
    row = PlanEnrollment(username=username, plan_id=plan_id, started_on=date.today().isoformat())
    session.add(row)
    session.commit()
    session.refresh(row)
    _record_event(session, username, "plan_started", ref=plan_id)
    return _enrollment_payload(row)


@app.post("/plans/{plan_id}/days/{day}/complete")
async def complete_plan_day(
    plan_id: str, day: int, username: str = Query(...), session: Session = Depends(get_session)
):
    """Mark a day read. Idempotent; finishing the last open day finishes the plan."""
    _plan_or_404(plan_id)
    row = _active_plan(session, username, plan_id)
    if row is None:
        raise HTTPException(404, "Start this plan before marking days read.")
    try:
        plans.day_readings(plan_id, day)
    except plans.PlanError as exc:
        raise HTTPException(400, str(exc)) from exc
    done = set(_plan_days(row))
    fresh = day not in done
    done.add(day)
    row.completed_days = ",".join(str(d) for d in sorted(done))
    state = plans.progress(plan_id, row.started_on, sorted(done))
    if state["finished"] and not row.finished_on:
        row.finished_on = date.today().isoformat()
    session.add(row)
    session.commit()
    session.refresh(row)
    if fresh:
        _record_event(session, username, "plan_day_completed", ref=f"{plan_id}:{day}", value=day)
    if fresh and row.finished_on == date.today().isoformat() and state["finished"]:
        plan = plans.find(plan_id)
        await _share_card(
            username,
            {
                "message": f"{username} finished {plan['name']}",
                "card_kind": "activity",
                "card_title": plan["name"],
                "card_detail": f"Every one of {plan['days']} days read",
                "card_stats": [{"label": "Days", "value": str(plan["days"])}],
            },
        )
    badges = await get_achievements(username=username, session=session)
    return {**_enrollment_payload(row), "newly_earned": badges["newly_earned"]}


@app.delete("/plans/{plan_id}/days/{day}")
def uncomplete_plan_day(plan_id: str, day: int, username: str = Query(...), session: Session = Depends(get_session)):
    """Undo a day marked read by mistake."""
    row = _active_plan(session, username, plan_id)
    if row is None:
        raise HTTPException(404, "You are not following this plan.")
    row.completed_days = ",".join(str(d) for d in _plan_days(row) if d != day)
    if row.finished_on and not plans.progress(plan_id, row.started_on, _plan_days(row))["finished"]:
        row.finished_on = ""
    session.add(row)
    session.commit()
    session.refresh(row)
    return _enrollment_payload(row)


@app.post("/plans/{plan_id}/catch-up")
def catch_up_plan(plan_id: str, username: str = Query(...), session: Session = Depends(get_session)):
    """Shift the plan so the next unread day is today; nothing read is lost."""
    row = _active_plan(session, username, plan_id)
    if row is None:
        raise HTTPException(404, "You are not following this plan.")
    state = plans.progress(plan_id, row.started_on, _plan_days(row))
    if state["next_day"] is None:
        raise HTTPException(400, "This plan is finished; there is nothing to catch up.")
    row.started_on = plans.catch_up_start(state["next_day"])
    session.add(row)
    session.commit()
    session.refresh(row)
    return _enrollment_payload(row)


@app.delete("/plans/{plan_id}")
def stop_plan(plan_id: str, username: str = Query(...), session: Session = Depends(get_session)):
    """Stop following a plan. The days already read still count."""
    row = _active_plan(session, username, plan_id)
    if row is None:
        raise HTTPException(404, "You are not following this plan.")
    row.active = False
    session.add(row)
    session.commit()
    return {"stopped": plan_id}


# ── stats / streaks / achievements ──────────────────────────────────────────


@app.get("/stats")
def stats(
    username: str = Query(...),
    days: int = Query(default=30, ge=1, le=400),
    session: Session = Depends(get_session),
):
    """The reading picture for one user, plus their day-by-day window."""
    picture = metrics.compute(session, username)
    return {
        "username": username,
        **picture,
        "window": metrics.window_counts(session, username, days),
    }


@app.get("/streaks")
def streaks(username: str = Query(...), session: Session = Depends(get_session)):
    picture = metrics.compute(session, username)
    return {
        "username": username,
        "read_streak_current": picture["read_streak_current"],
        "read_streak_longest": picture["read_streak_longest"],
        "open_streak_current": picture["open_streak_current"],
        "open_streak_longest": picture["open_streak_longest"],
        "days_read": picture["days_read"],
    }


@app.get("/achievements")
async def get_achievements(
    username: str = Query(...),
    session: Session = Depends(get_session),
):
    """Earned badges, what's next, and the total points.

    Newly earned badges are banked in ``AchievementEarned`` (so the date is
    stable), converted into geo stars, and announced to the family chat. Every
    one of those three is best-effort and reported: the badge itself is already
    committed.
    """
    definitions = ach.load_definitions()
    picture = metrics.compute(session, username)
    banked = {
        row.achievement_id: row.earned_on.isoformat()
        for row in session.exec(
            select(AchievementEarned).where(AchievementEarned.username == username)
        )
    }
    result = ach.evaluate(definitions, picture["metrics"], previously_earned=banked)

    fresh: list[ach.Earned] = []
    for item in result.earned:
        if item.achievement.id in banked:
            continue
        session.add(
            AchievementEarned(
                username=username,
                achievement_id=item.achievement.id,
                points=item.achievement.points,
                earned_on=date.fromisoformat(item.earned_on),
            )
        )
        fresh.append(item)
    if fresh:
        session.commit()

    stars = await _award_stars(username, ach.star_grants(fresh))
    announced = await _announce(username, fresh)
    return {
        "username": username,
        "points": ach.total_points(result.earned),
        "newly_earned": [_earned_payload(i) for i in fresh],
        "earned": [_earned_payload(i) for i in result.earned],
        "next_up": [_progress_payload(p) for p in result.next_up],
        "stars": stars,
        "pending_rules": sorted(metrics.PENDING_RULES),
        "announced": announced,
    }


def _earned_payload(item: ach.Earned) -> dict:
    return {
        "id": item.achievement.id,
        "name": item.achievement.name,
        "description": item.achievement.description,
        "points": item.points,
        "earned_on": item.earned_on,
    }


def _progress_payload(p: ach.Progress) -> dict:
    return {
        "id": p.achievement.id,
        "name": p.achievement.name,
        "description": p.achievement.description,
        "points": p.achievement.points,
        "current": p.current,
        "target": p.target,
        "remaining": p.remaining,
        "percent": p.percent,
        "unit": p.unit,
    }


async def _award_stars(username: str, grants: list[dict]) -> dict:
    """Bank points as stars in the geo ledger.

    Stars are recorded in geo first and any downstream write-through is geo's
    problem, not ours: a Skylight outage must not cost a reader their badge.
    A geo outage is logged and reported rather than swallowed.
    """
    if not grants:
        return {"granted": 0, "status": "nothing to award"}
    if not GEO_SVC_URL:
        log.warning("[Bible] GEO_SVC_URL is not resolved; %d star grant(s) not sent", len(grants))
        return {"granted": 0, "status": "geo service is not configured; points were banked but no stars were sent"}
    granted = 0
    failures: list[str] = []
    try:
        async with aiohttp.ClientSession() as client:
            for grant in grants:
                try:
                    async with client.post(
                        f"{GEO_SVC_URL}/api/geo/stars",
                        json={"user_id": username, **grant},
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=10.0),
                    ) as resp:
                        if resp.status >= 400:
                            failures.append(f"{grant['detail']}: HTTP {resp.status}")
                        else:
                            granted += int(grant["stars"])
                except Exception as exc:  # noqa: BLE001 - reported, not hidden
                    failures.append(f"{grant['detail']}: {exc}")
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        failures.append(str(exc))
    status = f"{granted} star(s) banked" if not failures else "; ".join(failures)
    return {"granted": granted, "status": status}


async def _post_cards(cards: list[dict]) -> dict:
    """Post typed cards to the family chat room.

    Reuses the card envelope the family hub already renders. ``card_kind``
    must be one of the envelope kinds the Talk handler accepts (``activity``,
    ``game``, ``creation``, ``system``); anything else is refused there. Off unless FAMILY_CHAT_TOKEN names a
    room, and a failure is reported rather than raised: whatever the card is
    about is already saved, and losing it to a chat outage would be worse than
    silence.
    """
    if not cards:
        return {"posted": 0, "status": "nothing to announce"}
    token = os.getenv("FAMILY_CHAT_TOKEN", "").strip()
    if not token:
        return {"posted": 0, "status": "FAMILY_CHAT_TOKEN is not set; no family chat card posted"}
    if not EXECUTION_SVC_URL:
        return {"posted": 0, "status": "execution service is not configured; no family chat card posted"}
    posted = 0
    failures: list[str] = []
    try:
        async with aiohttp.ClientSession() as client:
            for card in cards:
                body = {"action": "post_card", "token": token, **card}
                try:
                    async with client.post(
                        f"{EXECUTION_SVC_URL}/execute/talk",
                        json={"user_context": {"user": "jarvis"}, **body},
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=10.0),
                    ) as resp:
                        result = await resp.json(content_type=None) if resp.status < 400 else None
                        if resp.status >= 400:
                            failures.append(f"{card.get('card_title')}: HTTP {resp.status}")
                        elif isinstance(result, dict) and result.get("status") == "FAILURE":
                            # The handler refuses with a 200 and says why.
                            failures.append(f"{card.get('card_title')}: {result.get('message')}")
                        else:
                            posted += 1
                except Exception as exc:  # noqa: BLE001 - reported, never fatal
                    failures.append(f"{card.get('card_title')}: {exc}")
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        failures.append(str(exc))
    status = f"{posted} card(s) posted" if not failures else "; ".join(failures)
    return {"posted": posted, "status": status}


async def _announce(username: str, fresh: list[ach.Earned]) -> dict:
    """Tell the family's chat room when a reader earns a badge."""
    return await _post_cards(
        [
            {
                "message": f"{username} unlocked {item.achievement.name}",
                "card_kind": "activity",
                "card_title": item.achievement.name,
                "card_detail": item.achievement.description or None,
                "card_stars": item.points,
                "card_stats": [{"label": "Points", "value": str(item.points)}],
            }
            for item in fresh
        ]
    )


#: Stand-in viewer for "the whole family": only a circle-wide share admits it.
_FAMILY_VIEWER = "__family__"


async def _share_card(username: str, card: dict) -> dict:
    """Post an activity card only for a reader who shares Bible activity with
    the whole family. A score or a finished plan is the reader's to share; one
    who has not opted in is simply not announced."""
    if await _consent(username, _FAMILY_VIEWER, "bible") is not True:
        return {"posted": 0, "status": "not shared with the family"}
    return await _post_cards([card])


# ── family sharing ──────────────────────────────────────────────────────────


@app.get("/activity/summary")
async def activity_summary(
    username: str = Query(..., description="Reader whose summary is being read"),
    target: str = Query(..., description="Caller; cross-user reads need consent"),
    session: Session = Depends(get_session),
):
    """Reading totals for one reader, visible only with their consent.

    Mirrors geo's ``/api/geo/activity/summary`` contract exactly: a missing
    consent row is a 404, never an empty answer, so an unshared reader cannot be
    distinguished from a reader with nothing to show. Contents are counts and
    badge names only -- never what was read, marked, or asked.
    """
    if target != username:
        consent = await _consent(target, username, "bible")
        if consent is not True:
            raise HTTPException(
                status_code=404,
                detail="That reader has not shared their Bible activity.",
            )
    picture = metrics.compute(session, username)
    definitions = {d.id: d for d in ach.load_definitions()}
    earned = session.exec(
        select(AchievementEarned).where(AchievementEarned.username == username)
    ).all()
    return {
        "username": username,
        "days_read": picture["days_read"],
        "chapters_read": picture["metrics"]["chapters_total"],
        "books_read": picture["metrics"]["books_read"],
        "read_streak_current": picture["read_streak_current"],
        "read_streak_longest": picture["read_streak_longest"],
        "achievements": [
            {"id": row.achievement_id, "name": definitions[row.achievement_id].name}
            for row in earned
            if row.achievement_id in definitions
        ],
        "points": sum(row.points for row in earned),
    }


async def _consent(owner: str, viewer: str, scope: str) -> bool | None:
    """Whether ``viewer`` may see ``owner``'s activity in ``scope``.

    Returns ``None`` when Identity cannot be reached, so callers can refuse
    rather than guess: an unreachable consent service must never widen access.
    """
    rows = await _sharing_rows()
    if rows is None:
        return None
    data = next((r for r in rows if r.get("username") == owner), None)
    if data is None or not data.get("enabled"):
        return False
    if scope not in (data.get("share") or []):
        return False
    if data.get("audience") == "users":
        return viewer in (data.get("user_ids") or [])
    return True


@app.get("/activity/feed")
async def activity_feed(
    viewer: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    """Family reading activity, opt-in members only.

    Members who have not opted in are simply absent from the feed -- a feed is a
    roll call, and failing the whole request over one private reader would be a
    worse product than omitting them.
    """
    settings = await _sharing_settings()
    if not settings:
        return {"entries": [], "members": [], "note": "No one has shared Bible activity yet."}
    entries = []
    for row in settings:
        if row["username"] == viewer:
            entries.append(await activity_summary(username=row["username"], target=viewer, session=session))
            continue
        if not await _consent(row["username"], viewer, "bible"):
            continue
        entries.append(await activity_summary(username=row["username"], target=viewer, session=session))
    entries.sort(key=lambda e: (-e["read_streak_current"], -e["days_read"]))
    return {"entries": entries[:limit], "members": [e["username"] for e in entries[:limit]], "count": len(entries[:limit])}


async def _sharing_rows() -> list[dict] | None:
    """Every stored sharing preference, from Identity; ``None`` if unreachable.

    Identity answers ``{"users": [{"username", "enabled", "audience",
    "user_ids", "share"}, ...]}`` for everyone at once (the same payload geo
    reads); a user with no row has never opted in and is simply absent.
    """
    if not IDENTITY_SVC_URL:
        log.warning("[Bible] IDENTITY_SVC_URL is not resolved; sharing is unavailable")
        return None
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{IDENTITY_SVC_URL}/api/internal/activity-sharing",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                if resp.status >= 400:
                    log.warning("[Bible] sharing lookup failed: HTTP %s", resp.status)
                    return None
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001 - an unreachable consent service is not consent
        log.warning("[Bible] sharing lookup errored: %s", exc)
        return None
    rows = data.get("users") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        log.warning("[Bible] sharing lookup returned an unexpected shape")
        return None
    return [r for r in rows if isinstance(r, dict) and r.get("username")]


async def _sharing_settings() -> list[dict]:
    """Every sharing row that exists (empty when Identity cannot be reached)."""
    return await _sharing_rows() or []


@app.get("/blb/link")
def blb_link(ref: str = Query(...), tool: str = Query(default="")) -> dict:
    """Build a Blue Letter Bible deep link.

    blb.org has no API -- its official surface is web pages and the free
    ScriptTagger asset. We link out and never scrape, so the only thing to get
    right is a correct URL. An unset ``blb_base_url`` is a configuration error,
    not an invitation to guess ``blb.org``.
    """
    if not BLB_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail="blb_base_url is not configured. Set the BLB_BASE_URL environment variable "
            "or the blb_base_url setting to the Blue Letter Bible base URL.",
        )
    try:
        spans = parse_reference(ref)
    except ReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    first = spans[0]
    osis = first.book.lower()
    if first.whole_book:
        path = f"/{osis}"
    else:
        path = f"/{osis}/{first.chapter_start}/{first.verse_start}"
    if tool:
        path = f"/tools/{tool.strip('/').lower()}"
        if not first.whole_book:
            path = f"{path}/{osis}/{first.chapter_start}"
    return {"ref": ref, "url": f"{BLB_BASE_URL.rstrip('/')}{path}", "tool": tool or None}


@app.get("/daily")
def daily(username: str | None = Query(default=None), session: Session = Depends(get_session)):
    """One call that fills the home screen: verse, devotional, streaks, badges.

    The dashboard widget and the Android home screen both read this so neither
    has to make four round trips to paint one card.
    """
    verse = _verse_of_day(session)
    devotional_entry = devotional_registry.daily()
    payload: dict = {
        "day": date.today().isoformat(),
        "verse_of_day": verse,
        "devotional": devotional_entry,
        "sources": devotional_registry.describe_sources(),
    }
    if username:
        picture = metrics.compute(session, username)
        payload["streaks"] = {
            "read_streak_current": picture["read_streak_current"],
            "read_streak_longest": picture["read_streak_longest"],
            "open_streak_current": picture["open_streak_current"],
            "days_read": picture["days_read"],
            "chapters_read": picture["metrics"]["chapters_total"],
        }
        payload["position"] = picture["position"]
        # The plan line and the memory line on the home card: today's reading
        # for each plan in progress, and how many verses are due.
        enrolled = session.exec(
            select(PlanEnrollment).where(PlanEnrollment.username == username, PlanEnrollment.active == True)  # noqa: E712
        ).all()
        payload["plans"] = [
            {k: v for k, v in _enrollment_payload(row).items() if k in ("plan_id", "name", "percent", "behind", "today", "finished")}
            for row in enrolled
        ]
        today = date.today().isoformat()
        payload["memory_due"] = sum(1 for r in _my_memory(session, username) if r.due_on <= today)
    return payload


def _verse_of_day(session: Session) -> dict:
    try:
        return votd.pick(session)
    except LookupError as exc:
        return {"error": str(exc)}

# ── admin: installing translations and study Bibles ─────────────────────────


async def _live_settings() -> dict[str, str]:
    """Runtime settings from Identity, read when they are needed.

    ``resolve_runtime_config()`` only rewrites the module globals once, at
    startup, so a key an admin saves in the app would otherwise sit unused until
    the next restart. Anything that must react to a setting being changed at
    runtime reads it here instead. An unreachable Identity is reported and the
    caller falls back to the boot-time value rather than pretending it is empty.
    """
    if not IDENTITY_SVC_URL:
        log.warning("[Bible] IDENTITY_SVC_URL is not resolved; runtime settings are unavailable")
        return {}
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{IDENTITY_SVC_URL}/api/global-settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                if resp.status >= 400:
                    log.warning("[Bible] runtime settings lookup failed: HTTP %s", resp.status)
                    return {}
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001 - the boot-time value is the fallback
        log.warning("[Bible] runtime settings lookup errored: %s", exc)
        return {}
    rows = data.get("settings") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return {}
    return {
        str(row["key"]): str(row.get("value") or "")
        for row in rows
        if isinstance(row, dict) and row.get("key")
    }


async def _provider_registry() -> providers.ProviderRegistry:
    """Build the provider registry from the *live* configuration.

    Read at call time rather than at import time, because ``resolve_runtime_config()``
    rewrites these module globals at boot and an admin who has just set a key
    should not have to restart the service. The key is fetched from Identity so a
    key saved in the app works immediately; the environment value is the fallback
    for an install with no Identity, and a blank setting never clears a key that
    the environment already supplied.
    """
    import services.config as cfg

    settings = {"bible_api_key": cfg.BIBLE_API_KEY}
    live = await _live_settings()
    if "bible_api_key" in live:
        settings["bible_api_key"] = live["bible_api_key"] or cfg.BIBLE_API_KEY or ""
    try:
        entries = providers.load_provider_config()
    except CorpusError as exc:
        log.warning("translation providers unavailable: %s", exc)
        return providers.ProviderRegistry(entries=[])
    return providers.build_registry(entries, settings=settings)


def _import_dir() -> Path:
    """Where uploads and fetched provider text are kept.

    No default: an unset ``BIBLE_IMPORT_DIR`` is a 503 naming the setting,
    because writing Bible files somewhere arbitrary is how a container fills up
    with three copies of the same 16 MB e-book nobody can find.
    """
    import services.config as cfg

    directory = cfg.BIBLE_IMPORT_DIR
    if not str(directory or "").strip():
        raise HTTPException(
            status_code=503,
            detail=(
                "BIBLE_IMPORT_DIR is not set, so this service has nowhere to put an "
                "uploaded Bible. Set the bible_import_dir global setting (or the "
                "BIBLE_IMPORT_DIR environment variable) to a path on a mounted volume."
            ),
        )
    path = Path(str(directory)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


class ImportRequest(BaseModel):
    """An install request. Either ``provider`` or ``source_path`` must be set."""

    code: str = Field(..., description="Manifest code, e.g. 'esv' or 'nkjv'")
    provider: str = Field(default="", description="Provider code, e.g. 'api.bible'")
    provider_id: str = Field(default="", description="Which translation at that provider")
    source_path: str = Field(default="", description="A path on this service's filesystem")
    library_path: str = Field(
        default="",
        description="A file in the Nextcloud book library; fetched and then imported",
    )
    kind: str = Field(default="", description="json, pdf or epub; detected when blank")
    name: str = Field(default="")
    sha256: str = Field(default="")
    edition: str = Field(default="", description="Study Bible code; defaults to the translation")
    edition_name: str = Field(default="")
    publisher: str = Field(default="")
    rights_holder: str = Field(default="")
    import_notes: bool = Field(default=False)
    dry_run: bool = Field(
        default=False,
        description="Fetch one book to prove the parsing, then install nothing",
    )
    budget: int | None = Field(
        default=None,
        description="Refuse this run if it would need more than this many new requests",
    )


def _plan_from(payload: ImportRequest, *, source_path: str | None = None) -> importer.ImportPlan:
    kind = str(payload.kind or "").strip().lower()
    if kind and kind not in importer.KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"{payload.kind!r} is not a kind this service can import. Choose one of: "
            + ", ".join(importer.KINDS),
        )
    return importer.ImportPlan(
        code=str(payload.code or "").strip().lower(),
        kind=kind,
        name=str(payload.name or "").strip(),
        source_path=str(payload.source_path or source_path or "").strip(),
        library_path=str(payload.library_path or "").strip(),
        sha256=str(payload.sha256 or "").strip().lower(),
        edition=str(payload.edition or "").strip(),
        edition_name=str(payload.edition_name or "").strip(),
        publisher=str(payload.publisher or "").strip(),
        rights_holder=str(payload.rights_holder or "").strip(),
        import_notes=bool(payload.import_notes),
        provider=str(payload.provider or "").strip(),
        provider_id=str(payload.provider_id or "").strip(),
        dry_run=bool(payload.dry_run),
        budget=payload.budget,
    )


def _plan_response(report: importer.ImportReport) -> JSONResponse:
    """A refusal is a report, not an exception: show it and record it."""
    return JSONResponse(status_code=200 if report.ok else 422, content=report.as_dict())


def _admin_imports_report(
    registry: providers.ProviderRegistry, library_root: str
) -> dict:
    """The Admin > Bible page body. Runs in a worker thread with its own session.

    SQLite sessions are not safe to share between the event loop and a worker
    thread, so the session is opened here rather than injected, and the caller
    resolves the provider registry and the library root first because those need
    the network and the settings database.
    """
    with Session(_db()) as session:
        try:
            catalogue = corpus.catalogue(session)
            editions = corpus.edition_catalogue(session)
        except CorpusError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        directory = None
        import_dir_error = ""
        try:
            directory = str(_import_dir())
        except HTTPException as exc:
            import_dir_error = str(exc.detail)
        library_error = ""
        if not library_root:
            library_error = (
                f"The {library.LIBRARY_SETTING} global setting is not set, so the "
                f"Nextcloud book library is not available. Set it in Admin > Settings "
                f"to the folder that holds your Bibles, for example /Books/Text."
            )
        return {
            "default_version": corpus.default_version_code(session),
            "primary": corpus.primary_code(),
            "versions": catalogue,
            "editions": editions,
            "providers": registry.describe(),
            "kinds": list(importer.KINDS),
            "import_dir": directory,
            "import_dir_error": import_dir_error,
            "library_root": library_root,
            "library_setting": library.LIBRARY_SETTING,
            "library_error": library_error,
            "runs": importer.recent_runs(session),
        }


async def _library_root() -> str:
    """The folder in the Nextcloud library that holds Bibles.

    Read from the live settings so an operator who points ``calibre_library_path``
    at their Calibre shelf in Admin sees the shelf immediately. Blank is the
    operator's answer not yet given, never an invitation to guess a folder: a
    guessed shelf reads as an empty library and hides the real one.
    """
    import services.config as cfg

    live = await _live_settings()
    return str(live.get(library.LIBRARY_SETTING) or cfg.CALIBRE_LIBRARY_PATH or "").strip()


def _library_client(root: str) -> library.LibraryClient:
    """A client for the book library, via the storage service.

    Storage is asked for the bytes rather than reading WebDAV here, so the
    Nextcloud password stays in the one place that holds it. An unset storage URL
    is a 503 naming the setting instead of a library that mysteriously lists
    nothing.
    """
    from services.config import INTERNAL_SECRET, STORAGE_SVC_URL

    url = str(STORAGE_SVC_URL or "").strip()
    if not url:
        raise library.LibraryUnavailable(
            "storage_svc_url is not resolved, so the book library cannot be read. Set the "
            "storage_svc_url global setting (or STORAGE_SVC_URL) to the storage service."
        )
    return library.LibraryClient(storage_url=url, internal_secret=str(INTERNAL_SECRET or ""))


async def _library_listing(path: str) -> dict:
    """List one folder of the Nextcloud book library.

    Returns the folder, its parent and every entry it holds, including the files
    this service cannot read. Hiding those would make a shelf holding one
    unreadable e-book look like a shelf with a hole in it, and the reason belongs
    next to the file rather than in a log the operator never opens.
    """
    root = await _library_root()
    if not root:
        raise HTTPException(
            status_code=503,
            detail=(
                f"The {library.LIBRARY_SETTING} global setting is not set, so this "
                "service does not know which shelf of the book library to read. Set "
                "it in Admin > Settings to the folder that holds your Bibles, for "
                "example /Books/Text."
            ),
        )
    client = _library_client(root)
    try:
        listing = await client.browse(root=root, path=str(path or "").strip())
    except library.LibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except library.LibraryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "root": root,
        "setting": library.LIBRARY_SETTING,
        **listing.as_dict(),
    }


@app.get("/admin/library")
async def admin_library(path: str = Query(default="")):
    """List one folder of the Nextcloud book library, path in the query string."""
    return await _library_listing(path)


@app.post("/admin/library")
async def admin_library_post(request: Request):
    """The same listing as a POST, which is how the gateway asks for it.

    The operator picks the folder, so the path arrives as a JSON body rather
    than a query string; the GET form remains for curl and anything that
    already has the path encoded.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail='The body must be a JSON object such as {"path": "Author/Work"}.',
        ) from exc
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail='The body must be a JSON object such as {"path": "Author/Work"}.',
        )
    return await _library_listing(str(body.get("path") or ""))


@app.get("/admin/imports")
async def admin_imports():
    """Everything needed to install something: the catalogue, the providers, the history."""
    return await run_in_threadpool(
        _admin_imports_report, await _provider_registry(), await _library_root()
    )


@app.get("/admin/providers/{code}/translations")
async def admin_provider_translations(code: str):
    """Ask one provider what translations it can supply.

    Deliberately a separate call. Listing the catalogue must never need the
    network, so the providers appear on this page without any of them being
    reachable, and an unreachable provider produces one honest message in one
    place instead of a page that will not load.
    """
    registry = await _provider_registry()
    try:
        provider = registry.get(code)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        found = await provider.translations()
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "provider": provider.describe(),
        "translations": [item.as_dict() for item in found],
        "count": len(found),
    }


@app.get("/admin/providers/{code}/estimate")
async def admin_provider_estimate(code: str, translation_id: str = ""):
    """Say what installing this translation would cost before spending anything.

    A whole Bible is about 1,189 chapter requests on api.bible's free plan, and
    that plan allows about 5,000 a month, so the number of requests an install
    will use is the single most important thing to know before pressing the
    button. Everything already cached is counted as zero, which means a
    translation that has been fetched once can be re-installed forever for free.
    """
    registry = await _provider_registry()
    try:
        provider = registry.get(code)
        cache = importer.build_cache(provider, str(translation_id or "").strip() or "unknown")
        result = await provider.estimate(str(translation_id or "").strip(), cache=cache)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        budget = provider_cache.call_budget()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    result["budget"] = budget
    result["within_budget"] = budget is None or int(result["remaining"]) <= budget
    result["cache_enabled"] = cache.enabled
    if not cache.enabled:
        result["cache_warning"] = cache.reason
    return result


def _admin_import_run(plan: importer.ImportPlan, registry: providers.ProviderRegistry) -> JSONResponse:
    """Install a planned import. Runs in a worker thread with its own session."""
    with Session(_db()) as session:
        try:
            directory = _import_dir()
        except HTTPException as exc:
            if plan.provider:
                raise
            report = importer.ImportReport(
                status="failed", kind=plan.kind, code=plan.code, source=plan.source_label(), message=str(exc.detail)
            )
            importer._record(session, plan, report, 0)
            return _plan_response(report)
        report = importer.run(session, plan, registry=registry, import_dir=directory)
        return _plan_response(report)


@app.post("/admin/imports")
async def admin_import(payload: ImportRequest):
    """Install a translation from a file on disk or from an online provider.

    Deliberately returns the report with 200 on success and 422 on a refusal
    rather than raising: the refusal text is the thing the operator needs, and
    a 4xx body here would look like the request itself was malformed.
    """
    return await run_in_threadpool(_admin_import_run, _plan_from(payload), await _provider_registry())


def _admin_import_upload_run(plan: importer.ImportPlan, directory: str, label: str) -> JSONResponse:
    """Install a staged upload. Runs in a worker thread with its own session."""
    with Session(_db()) as session:
        report = importer.run(session, plan, import_dir=Path(directory))
        report.log.insert(0, label)
        return _plan_response(report)


@app.post("/admin/imports/upload")
async def admin_import_upload(
    file: UploadFile = File(...),
    code: str = Form(...),
    kind: str = Form(default=""),
    name: str = Form(default=""),
    sha256: str = Form(default=""),
    edition: str = Form(default=""),
    edition_name: str = Form(default=""),
    publisher: str = Form(default=""),
    rights_holder: str = Form(default=""),
    import_notes: bool = Form(default=False),
):
    """Store an uploaded Bible in the import directory and install it from there.

    The upload is written first and then handed to the same importer as any
    other file, so an upload and a ``source_path`` import produce identical
    reports and identical refusals.
    """
    directory = _import_dir()
    filename = Path(str(file.filename or "")).name or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in importer.SUFFIX_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{filename} is not a Bible file this service can read. "
                f"Supported extensions: {', '.join(sorted(importer.SUFFIX_KINDS))}."
            ),
        )
    destination = directory / f"{code.strip().lower() or 'upload'}{suffix}"
    written = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > importer.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"That file is larger than the {importer.MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                            "import limit. Nothing was installed."
                        ),
                    )
                handle.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503, detail=f"Could not write {destination} ({exc!s}). Is BIBLE_IMPORT_DIR writable?"
        ) from exc

    plan = _plan_from(
        ImportRequest(
            code=code,
            kind=kind or importer.detect_kind(destination),
            name=name,
            sha256=sha256,
            edition=edition,
            edition_name=edition_name,
            publisher=publisher,
            rights_holder=rights_holder,
            import_notes=import_notes,
        ),
        source_path=str(destination),
    )
    label = f"Uploaded {filename} to {destination} ({written} bytes)."
    return await run_in_threadpool(_admin_import_upload_run, plan, str(directory), label)
