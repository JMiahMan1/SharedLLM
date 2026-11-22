# services/bible/book_routes.py
"""Admin and reader routes for imported PDF/EPUB books.

This module is separate from the translation importer because a devotional or
study book has a different contract: it is not a Bible, it must not be checked
against the 66-book canon, and it is read chapter by chapter rather than by
verse. Keeping the two paths apart is what lets a 365-day devotional import
without the Bible importer reporting 65 missing books.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from services.bible import book_import, library, study_narration, study_reformat
from services.bible.models import ImportedBook, ImportedBookAsset, StudySession, StudySessionTurn, utcnow


# The helpers below live in ``main`` because every existing route uses them.
# Importing ``main`` at module import time would close the circle (main imports
# this router), so they are resolved lazily on first call instead. By then main
# is fully initialized.
def _db():
    from services.bible.main import _db as helper
    return helper()


def _import_dir():
    from services.bible.main import _import_dir as helper
    return helper()


def _library_client(root: str):
    from services.bible.main import _library_client as helper
    return helper(root)


async def _live_settings():
    from services.bible.main import _live_settings as helper
    return await helper()


router = APIRouter()


class BookImportRequest(BaseModel):
    source: str = "nextcloud"  # nextcloud | calibre
    path: str
    title: str = ""
    author: str = ""
    kind: str = "study"


async def _books_root(source: str) -> str:
    import services.config as cfg

    live = await _live_settings()
    if source == "calibre":
        return str(live.get(library.LIBRARY_SETTING) or cfg.CALIBRE_LIBRARY_PATH or "").strip()
    return str(live.get("bible_books_path") or cfg.BIBLE_BOOKS_PATH or "/Books").strip()


def _report() -> dict:
    with Session(_db()) as session:
        books = [book_import.book_dict(book) for book in book_import.list_books(session)]
    return {"books": books, "count": len(books), "kinds": list(book_import.BOOK_KINDS)}


@router.get("/admin/books")
async def admin_books():
    """Imported books plus the two shelf roots the admin page can browse."""
    payload = _report()
    payload["sources"] = {
        "nextcloud": await _books_root("nextcloud"),
        "calibre": await _books_root("calibre"),
    }
    return payload


@router.get("/admin/books/browse")
async def admin_books_browse(
    source: str = Query(default="nextcloud"),
    path: str = Query(default=""),
):
    source = "calibre" if source == "calibre" else "nextcloud"
    root = await _books_root(source)
    if not root:
        setting = library.LIBRARY_SETTING if source == "calibre" else "bible_books_path"
        raise HTTPException(
            status_code=503,
            detail=f"The {setting} setting is not set, so there is no {source} shelf to browse.",
        )
    client = _library_client(root)
    try:
        listing = await client.browse(
            root=root,
            path=path,
            provider="nextcloud",
            recursive=source == "calibre",
        )
    except library.LibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except library.LibraryUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    data = listing.as_dict()
    for entry in data["entries"]:
        if entry["is_dir"]:
            continue
        suffix = Path(entry["name"]).suffix.lower()
        if suffix in book_import.SUPPORTED_SUFFIXES:
            entry["kind"] = suffix.lstrip(".")
            entry["installable"] = True
            entry["note"] = f"Ready to import as a {suffix.lstrip('.').upper()} book"
        else:
            entry["installable"] = False
            entry["note"] = "Not a book format this reader can import"
    if source == "calibre":
        data["entries"] = [
            entry for entry in data["entries"] if entry["is_dir"] or entry["installable"]
        ]
    data["count"] = len(data["entries"])
    data["installable"] = sum(1 for entry in data["entries"] if entry["installable"])
    return {"source": source, "root": root, "setting": "bible_books_path" if source == "nextcloud" else library.LIBRARY_SETTING, **data}


def _book_import_run(
    *,
    source: str,
    root: str,
    path: str,
    title: str,
    author: str,
    kind: str,
) -> dict:
    directory = _import_dir()
    destination = directory / library.candidate_names(path)
    client = _library_client(root)
    import asyncio

    asyncio.run(
        client.fetch(
            root=root,
            path=path,
            destination=destination,
            provider="nextcloud",
            allowed=book_import.SUPPORTED_SUFFIXES,
        )
    )
    with Session(_db()) as session:
        try:
            book = book_import.import_book(
                session,
                path=destination,
                title=title,
                author=author,
                kind=kind,
                source=source,
                source_path=path,
            )
        except book_import.BookImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": "imported", "book": book_import.book_dict(book)}


@router.post("/admin/books/import")
async def admin_book_import(payload: BookImportRequest):
    source = "calibre" if payload.source == "calibre" else "nextcloud"
    root = await _books_root(source)
    if not root:
        raise HTTPException(status_code=503, detail="The book shelf is not configured.")
    import asyncio

    return await asyncio.to_thread(
        _book_import_run,
        source=source,
        root=root,
        path=payload.path,
        title=payload.title,
        author=payload.author,
        kind=payload.kind,
    )


def _book_upload_run(destination: Path, title: str, author: str, kind: str, fallback_title: str = "") -> dict:
    with Session(_db()) as session:
        try:
            book = book_import.import_book(
                session,
                path=destination,
                title=title or fallback_title,
                author=author,
                kind=kind,
                source="upload",
                source_path=str(destination),
            )
        except book_import.BookImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": "imported", "book": book_import.book_dict(book)}


@router.post("/admin/books/upload")
async def admin_book_upload(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    author: str = Form(default=""),
    kind: str = Form(default="study"),
):
    directory = _import_dir()
    # Browsers and the Android picker may percent-encode the name (a space
    # arrives as "%20"), and the saved copy is named after the original file.
    # Decode before both saving and deriving the fallback title, otherwise the
    # book is listed as "book-1792617556-Walk%20With%20Jesus...".
    filename = Path(unquote(str(file.filename or ""))).name or "book"
    suffix = Path(filename).suffix.lower()
    if suffix not in book_import.SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{filename} is not a book this service can read. "
                "Supported: PDF, EPUB, MOBI, AZW3, DOCX, TXT."
            ),
        )
    destination = directory / f"book-{int(time.time())}-{filename}"
    fallback_title = Path(filename).stem.strip() or "Book"
    written = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > book_import.MAX_BOOK_BYTES:
                    raise HTTPException(status_code=413, detail="That book is larger than the 256 MB limit.")
                handle.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"Could not write {destination}: {exc}") from exc
    import asyncio

    result = await asyncio.to_thread(_book_upload_run, destination, title, author, kind, fallback_title)
    result["bytes"] = written
    return result


@router.delete("/admin/books/{book_id}")
async def admin_delete_book(book_id: int):
    with Session(_db()) as session:
        if not book_import.delete_book(session, book_id):
            raise HTTPException(status_code=404, detail="Book not found")
    return {"status": "deleted", "id": book_id}


@router.get("/imported-books")
def list_books(kind: str | None = Query(default=None)):
    with Session(_db()) as session:
        books = book_import.list_books(session, kind=kind)
        return {"books": [book_import.book_dict(book) for book in books], "count": len(books)}


@router.get("/imported-books/{book_id}")
def read_book(book_id: int):
    with Session(_db()) as session:
        book = book_import.get_book(session, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        chapters = book_import.chapters_for(session, book_id)
        aliases = study_reformat.aliases_for(session, book_id)
        return {
            "book": book_import.book_dict(book),
            "chapters": [book_import.chapter_dict(chapter) for chapter in chapters],
            # Places saved before a reformat (an old ordinal or section id) and
            # where those words are now, so a stored position still lands.
            "aliases": [
                {
                    "old_ordinal": alias.old_ordinal,
                    "old_section_id": alias.old_section_id,
                    "section_id": alias.section_id,
                    "ordinal": alias.ordinal,
                }
                for alias in aliases
            ],
        }


def _chapter_payload(book_id: int, chapter) -> dict:
    blocks = study_reformat.blocks_of(chapter)
    return {
        "book_id": book_id,
        "ordinal": chapter.ordinal,
        "section_id": chapter.section_id or f"ordinal-{chapter.ordinal}",
        "label": chapter.label,
        "text": chapter.text,
        "questions": book_import.chapter_dict(chapter)["questions"],
        "blocks": [block.as_dict() for block in blocks],
    }


@router.get("/imported-books/{book_id}/chapters/{ordinal}")
def read_book_chapter(book_id: int, ordinal: int):
    with Session(_db()) as session:
        chapters = book_import.chapters_for(session, book_id)
        for chapter in chapters:
            if chapter.ordinal == ordinal:
                return _chapter_payload(book_id, chapter)
    raise HTTPException(status_code=404, detail="Chapter not found")


@router.get("/imported-books/{book_id}/sections/{section_id}")
def read_book_section(book_id: int, section_id: str):
    """A section by its stable id, following a reformat's aliases if it moved."""
    with Session(_db()) as session:
        chapter = study_reformat.resolve(session, book_id, section=section_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="That section is not in this book.")
        return _chapter_payload(book_id, chapter)


@router.get("/imported-books/{book_id}/resolve")
def resolve_book_place(
    book_id: int,
    section: str = Query(default=""),
    ordinal: int | None = Query(default=None, ge=1),
    legacy: bool = Query(default=False, description="The ordinal was saved before a reformat"),
):
    """Where a saved place (section id or ordinal) is in the book as it is now."""
    with Session(_db()) as session:
        chapter = study_reformat.resolve(session, book_id, ordinal=ordinal, section=section, legacy=legacy)
        if chapter is None:
            raise HTTPException(status_code=404, detail="That place is not in this book.")
        return {
            "book_id": book_id,
            "ordinal": chapter.ordinal,
            "section_id": chapter.section_id,
            "label": chapter.label,
        }


@router.get("/imported-books/{book_id}/assets/{key}")
def read_book_asset(book_id: int, key: str):
    with Session(_db()) as session:
        row = session.exec(
            select(ImportedBookAsset).where(ImportedBookAsset.book_id == book_id, ImportedBookAsset.key == key)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Image not found")
        return Response(
            content=row.data,
            media_type=row.mime,
            headers={"Cache-Control": "private, max-age=86400"},
        )


# --- Reformat ---------------------------------------------------------------


class ReformatPreviewRequest(BaseModel):
    #: Ask the household's local model to label lines the rules are unsure of.
    assist: bool = False


class ReformatApplyRequest(BaseModel):
    token: str


PREVIEW_TTL_S = 24 * 3600


def _reformat_dir() -> Path:
    directory = _import_dir() / "reformat"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def _source_file(book: ImportedBook) -> Path:
    """The file this book was read from: kept locally, or fetched again from its shelf."""
    for candidate in (getattr(book, "stored_file", "") or "", book.source_path or ""):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    if book.source in {"nextcloud", "calibre"} and book.source_path:
        destination = _import_dir() / library.candidate_names(book.source_path)
        if destination.is_file():
            return destination
        root = await _books_root(book.source)
        if not root:
            raise HTTPException(
                status_code=503,
                detail="The book shelf is not configured, so the original file cannot be fetched.",
            )
        try:
            await _library_client(root).fetch(
                root=root,
                path=book.source_path,
                destination=destination,
                provider="nextcloud",
                allowed=book_import.SUPPORTED_SUFFIXES,
            )
        except library.LibraryError as exc:
            raise HTTPException(status_code=409, detail=f"The original file could not be fetched: {exc}") from exc
        except library.LibraryUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return destination
    raise HTTPException(
        status_code=409,
        detail="The original file of this book is no longer kept, so it cannot be reformatted. Import it again.",
    )


async def _assist_settings() -> tuple[str, str]:
    """The local model only: household books never go to a cloud model."""
    import services.config as cfg

    live = await _live_settings()
    url = str(live.get("llm_local_url") or cfg.LLM_URL or "").strip()
    model = str(live.get("bible_import_model") or live.get("default_model") or cfg.DEFAULT_MODEL or "").strip()
    return url, model


async def _normalised(book: ImportedBook, assist: bool):
    import asyncio

    path = await _source_file(book)
    try:
        document = await asyncio.to_thread(book_import.read_document, path, title=book.title)
    except book_import.BookImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assist_report = None
    if assist:
        from services.bible import study_assist

        url, model = await _assist_settings()
        assist_report = await study_assist.assist(document, llm_url=url, model=model)
        document.report["assist"] = assist_report
    return path, document, assist_report


@router.post("/admin/books/{book_id}/reformat/preview")
async def admin_book_reformat_preview(book_id: int, payload: ReformatPreviewRequest | None = None):
    """What reformatting this book would change. Nothing is written to the book."""
    import secrets

    payload = payload or ReformatPreviewRequest()
    with Session(_db()) as session:
        book = book_import.get_book(session, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        previous = study_reformat.old_sections(session, book_id)
        session.expunge(book)
    _path, document, assist_report = await _normalised(book, payload.assist)
    report = study_reformat.preview(book, previous, document)
    token = secrets.token_urlsafe(12)
    state = {
        "book_id": book_id,
        "fingerprint": report["fingerprint"],
        # The model's labels are not reproducible, so they travel with the token.
        "labels": {
            block.id: [block.type, block.level]
            for section in document.sections
            for block in section.blocks
        }
        if payload.assist
        else {},
        "created": time.time(),
    }
    (_reformat_dir() / f"{book_id}-{token}.json").write_text(json.dumps(state))
    report["token"] = token
    report["assist"] = assist_report
    return report


@router.post("/admin/books/{book_id}/reformat/apply")
async def admin_book_reformat_apply(book_id: int, payload: ReformatApplyRequest):
    """Replace the book's sections with the previewed reformat, keeping readers' places."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", payload.token or ""):
        raise HTTPException(status_code=400, detail="That preview token is not valid.")
    token_file = _reformat_dir() / f"{book_id}-{payload.token}.json"
    if not token_file.is_file():
        raise HTTPException(status_code=409, detail="That preview has expired. Preview the reformat again.")
    state = json.loads(token_file.read_text())
    if time.time() - float(state.get("created", 0)) > PREVIEW_TTL_S:
        token_file.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="That preview has expired. Preview the reformat again.")
    with Session(_db()) as session:
        book = book_import.get_book(session, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        session.expunge(book)
    path, document, _ = await _normalised(book, False)
    labels = state.get("labels") or {}
    if labels:
        for section in document.sections:
            for block in section.blocks:
                if block.id in labels:
                    block.type, block.level = labels[block.id][0], int(labels[block.id][1])
        document.report["assist"] = {"status": "applied from the preview"}
    if study_reformat.fingerprint(document) != state.get("fingerprint"):
        raise HTTPException(
            status_code=409,
            detail="The book reads differently now than in the preview. Preview the reformat again.",
        )
    with Session(_db()) as session:
        book = book_import.get_book(session, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        book.stored_file = str(path)
        mapping = study_reformat.store(session, book, document)
        session.commit()
        session.refresh(book)
        result = {"status": "reformatted", "book": book_import.book_dict(book), "mapping": mapping}
    token_file.unlink(missing_ok=True)
    return result


# --- Read aloud ---------------------------------------------------------------


def _narration_section(session: Session, book_id: int, ordinal: int):
    book = book_import.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    chapter = next((row for row in book_import.chapters_for(session, book_id) if row.ordinal == ordinal), None)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return book, chapter


@router.get("/imported-books/{book_id}/chapters/{ordinal}/narration/plan")
def book_narration_plan(
    book_id: int,
    ordinal: int,
    voice: str | None = Query(default=None, max_length=120),
):
    """How a section is read aloud: its pieces, and which blocks each one speaks."""
    with Session(_db()) as session:
        book, chapter = _narration_section(session, book_id, ordinal)
        try:
            return study_narration.plan(session, book=book, chapter=chapter, voice=voice)
        except study_narration.NarrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/imported-books/{book_id}/chapters/{ordinal}/narration/chunk")
async def book_narration_chunk(
    book_id: int,
    ordinal: int,
    index: int = Query(..., ge=0),
    voice: str | None = Query(default=None, max_length=120),
):
    """One spoken piece of a section, synthesised once and then cached."""
    # The same engine address and secret the Scripture narration uses.
    from services.bible import main as bible_main

    with Session(_db()) as session:
        book, chapter = _narration_section(session, book_id, ordinal)
        try:
            return await study_narration.narrate_chunk(
                session,
                book=book,
                chapter=chapter,
                voice=voice,
                index=index,
                execution_url=bible_main.EXECUTION_SVC_URL,
                internal_secret=bible_main.INTERNAL_SECRET,
            )
        except study_narration.NarrationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except study_narration.NarrationUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/devotional/works")
def devotional_works():
    with Session(_db()) as session:
        books = book_import.list_books(session, kind="devotional")
        return {"works": [book_import.book_dict(book) for book in books]}


@router.get("/devotional/book/{book_id}")
def devotional_book(book_id: int, day: str | None = Query(default=None)):
    from datetime import date

    from services.bible.devotionals.base import day_of_year

    target = date.fromisoformat(day) if day else date.today()
    with Session(_db()) as session:
        entry = book_import.devotional_for_day(session, book_id, day_of_year(target))
    if entry is None:
        raise HTTPException(status_code=404, detail="This book has no reading for that day.")
    return {"day": target.isoformat(), "entry": entry}


class DevotionalCadenceUpdate(BaseModel):
    cadence: str


@router.post("/devotional/book/{book_id}/cadence")
def devotional_cadence(book_id: int, payload: DevotionalCadenceUpdate):
    """Choose how a devotional advances: daily or weekly."""
    with Session(_db()) as session:
        if not book_import.set_cadence(session, book_id, payload.cadence):
            raise HTTPException(status_code=400, detail="Cadence must be 'day' or 'week'.")
        book = book_import.get_book(session, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="That book is not in the library.")
    return {"book": book_import.book_dict(book)}


class StudySessionCreate(BaseModel):
    username: str
    reference: str
    version: str = ""
    edition: str = ""
    title: str = ""


class StudyTurnCreate(BaseModel):
    question: str
    answer: str
    references: list[str] = []


class BookQuestionRequest(BaseModel):
    username: str
    question: str


@router.post("/imported-books/{book_id}/chapters/{ordinal}/ask")
async def ask_book_question(book_id: int, ordinal: int, payload: BookQuestionRequest):
    """Answer a chapter's end questions from that chapter, never from elsewhere."""
    from services.bible import study_ask
    from services.config import GATEWAY_INTERNAL_URL, INTERNAL_SECRET

    with Session(_db()) as session:
        book = book_import.get_book(session, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        chapter = next(
            (row for row in book_import.chapters_for(session, book_id) if row.ordinal == ordinal),
            None,
        )
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="A question is required")
    prompt = (
        f"Book: {book.title}\nAuthor: {book.author or 'unknown'}\n"
        f"Chapter: {chapter.label}\n\n{chapter.text}\n\n"
        f"Question: {question}\n\n"
        "Answer only from this chapter. If the chapter does not answer it, say so plainly."
    )
    try:
        answer = await study_ask.ask(
            prompt=prompt,
            user=payload.username,
            gateway_url=GATEWAY_INTERNAL_URL,
            internal_secret=INTERNAL_SECRET,
        )
    except study_ask.StudyAskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except study_ask.StudyAskUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"answer": answer, "book_id": book_id, "ordinal": ordinal, "question": question}


def _session_dict(session: StudySession, turns: list[StudySessionTurn] | None = None) -> dict:
    payload = {
        "id": session.id,
        "username": session.username,
        "title": session.title,
        "reference": session.reference,
        "version": session.version,
        "edition": session.edition,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }
    if turns is not None:
        payload["turns"] = [
            {
                "id": turn.id,
                "question": turn.question,
                "answer": turn.answer,
                "references": turn.references,
                "created_at": turn.created_at.isoformat() if turn.created_at else None,
            }
            for turn in turns
        ]
    return payload


@router.get("/study/sessions")
def list_study_sessions(username: str = Query(...)):
    with Session(_db()) as session:
        rows = list(
            session.exec(
                select(StudySession)
                .where(StudySession.username == username)
                .order_by(StudySession.updated_at.desc())
            ).all()
        )
        return {"sessions": [_session_dict(row) for row in rows], "count": len(rows)}


@router.post("/study/sessions")
def create_study_session(payload: StudySessionCreate):
    with Session(_db()) as session:
        row = StudySession(
            username=payload.username,
            title=(payload.title or payload.reference).strip(),
            reference=payload.reference.strip(),
            version=payload.version.strip(),
            edition=payload.edition.strip(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"session": _session_dict(row)}


@router.get("/study/sessions/search")
def search_study_memory(
    username: str = Query(...),
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(20, ge=1, le=50),
):
    """Search the reader's saved study turns and session titles.

    This is the retrieval side of study memory: the answer is built from the
    reader's own notes and prior questions, not from the open web. Results keep
    their session id so the panel can reopen the conversation they came from.
    """
    from sqlalchemy import or_

    needle = q.strip()
    with Session(_db()) as session:
        rows = session.exec(
            select(StudySessionTurn, StudySession)
            .where(StudySessionTurn.session_id == StudySession.id)
            .where(StudySession.username == username)
            .where(
                or_(
                    StudySessionTurn.question.contains(needle),
                    StudySessionTurn.answer.contains(needle),
                    StudySession.title.contains(needle),
                    StudySession.reference.contains(needle),
                )
            )
            .order_by(StudySessionTurn.created_at.desc())
            .limit(limit)
        ).all()
        results = []
        for turn, study in rows:
            try:
                references = json.loads(turn.references or "[]")
            except (TypeError, ValueError):
                references = []
            results.append(
                {
                    "session_id": turn.session_id,
                    "title": study.title or study.reference,
                    "reference": study.reference,
                    "version": study.version,
                    "question": turn.question,
                    "answer": turn.answer,
                    "references": references if isinstance(references, list) else [],
                    "created_at": turn.created_at.isoformat() if turn.created_at else None,
                }
            )
        return {"results": results, "count": len(results)}


@router.get("/study/sessions/{session_id}")
def get_study_session(session_id: int, username: str = Query(...)):
    with Session(_db()) as session:
        row = session.get(StudySession, session_id)
        if row is None or row.username != username:
            raise HTTPException(status_code=404, detail="Study session not found")
        turns = list(
            session.exec(
                select(StudySessionTurn)
                .where(StudySessionTurn.session_id == session_id)
                .order_by(StudySessionTurn.created_at)
            ).all()
        )
        return {"session": _session_dict(row, turns)}


@router.post("/study/sessions/{session_id}/turn")
def add_study_turn(session_id: int, payload: StudyTurnCreate, username: str = Query(...)):
    with Session(_db()) as session:
        row = session.get(StudySession, session_id)
        if row is None or row.username != username:
            raise HTTPException(status_code=404, detail="Study session not found")
        turn = StudySessionTurn(
            session_id=session_id,
            question=payload.question.strip(),
            answer=payload.answer.strip(),
            references=json.dumps(payload.references),
        )
        session.add(turn)
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
        session.refresh(turn)
        return {"turn": _session_dict(row, [turn])["turns"][0]}


@router.delete("/study/sessions/{session_id}")
def delete_study_session(session_id: int, username: str = Query(...)):
    with Session(_db()) as session:
        row = session.get(StudySession, session_id)
        if row is None or row.username != username:
            raise HTTPException(status_code=404, detail="Study session not found")
        for turn in session.exec(
            select(StudySessionTurn).where(StudySessionTurn.session_id == session_id)
        ).all():
            session.delete(turn)
        session.delete(row)
        session.commit()
    return {"status": "deleted", "id": session_id}
