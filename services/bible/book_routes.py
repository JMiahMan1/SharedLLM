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

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from services.bible import book_import, library
from services.bible.models import StudySession, StudySessionTurn, utcnow

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
        if entry["kind"] not in {"pdf", "epub"}:
            entry["installable"] = False
            entry["note"] = "Not a PDF or EPUB book"
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
        client.fetch(root=root, path=path, destination=destination, provider="nextcloud")
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
            detail=f"{filename} is not a book this service can read. Supported: PDF, EPUB.",
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


@router.get("/books")
def list_books(kind: str | None = Query(default=None)):
    with Session(_db()) as session:
        books = book_import.list_books(session, kind=kind)
        return {"books": [book_import.book_dict(book) for book in books], "count": len(books)}


@router.get("/books/{book_id}")
def read_book(book_id: int):
    with Session(_db()) as session:
        book = book_import.get_book(session, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        chapters = book_import.chapters_for(session, book_id)
        return {
            "book": book_import.book_dict(book),
            "chapters": [book_import.chapter_dict(chapter) for chapter in chapters],
        }


@router.get("/books/{book_id}/chapters/{ordinal}")
def read_book_chapter(book_id: int, ordinal: int):
    with Session(_db()) as session:
        chapters = book_import.chapters_for(session, book_id)
        for chapter in chapters:
            if chapter.ordinal == ordinal:
                return {
                    "book_id": book_id,
                    "ordinal": chapter.ordinal,
                    "label": chapter.label,
                    "text": chapter.text,
                    "questions": book_import.chapter_dict(chapter)["questions"],
                }
    raise HTTPException(status_code=404, detail="Chapter not found")


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


@router.post("/books/{book_id}/chapters/{ordinal}/ask")
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
