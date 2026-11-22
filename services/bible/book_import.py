"""Import ordinary PDF and EPUB books for devotionals and study.

The Bible importer proves that a file is a *complete Bible* and refuses
anything else. A daily devotional or a study book is not a Bible, so it needs
the opposite contract: keep every readable section, in order, and never make a
partial book look like a missing translation.

Every format (PDF, EPUB, MOBI/AZW3, DOCX, plain text) goes through the
normaliser in ``study_format``/``study_blocks``: running heads and page numbers
are removed, split words rejoined, headings, lists, quotes, footnotes, tables
and discussion questions recognised, and scripture references linked. Each
section keeps its plain ``text`` (what Ask, devotionals and Notes read) and its
structured ``blocks`` (what the reader draws and reads aloud).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sqlmodel import Session, select

from services.bible import study_format, study_reformat
from services.bible.models import ImportedBook, ImportedBookAlias, ImportedBookAsset, ImportedBookChapter, utcnow

SUPPORTED_SUFFIXES = study_format.SUPPORTED_SUFFIXES
BOOK_KINDS = ("devotional", "study")
MAX_BOOK_BYTES = 1024 * 1024 * 1024
MAX_CHAPTERS = 2000

_TITLE_AUTHOR = re.compile(r"^(?P<title>.+?)\s+-\s+(?P<author>.+)$")


class BookImportError(ValueError):
    """The book cannot be read or is not a supported format."""


def split_title_author(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    match = _TITLE_AUTHOR.match(stem)
    if match:
        return match.group("title").strip(), match.group("author").strip()
    return stem.strip(), ""


# EPUB imports keep the package's own part names ("..._epub_c5_r1"): they mean
# nothing to a reader, so the first short line of the text is a better label.
_INTERNAL_LABEL = re.compile(r"_epub_|(?:^|[ _])c\d+_r\d+(?:_|$)", re.IGNORECASE)
_LABEL_FALLBACK_CHARS = 90

#: "1. The God-Centered Spouse" and "42) One Day at a Time" both name their
#: devotion. Front matter (contents, introduction) has no such number, so it
#: never takes a day in the rotation.
_DEVOTION_NUMBER = re.compile(r"^\s*(\d{1,3})[.)]\s+")


def devotion_number(text: str) -> int | None:
    """The printed devotion number a section opens with, when it has one."""
    match = _DEVOTION_NUMBER.match(text or "")
    return int(match.group(1)) if match else None


def display_label(chapter: ImportedBookChapter) -> str:
    """A human label for a chapter, hiding EPUB-internal file names.

    A printed label like "Page 12" is kept as it is. Nothing is stored, so an
    existing import shows the friendlier label without being uploaded again.
    """
    label = (chapter.label or "").strip()
    if label and not _INTERNAL_LABEL.search(label):
        return label
    for line in (chapter.text or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if len(text) <= _LABEL_FALLBACK_CHARS:
            return text
        break
    return f"Reading {chapter.ordinal}"


def read_document(path: Path, *, title: str = ""):
    """The normalised book, or a :class:`BookImportError` that says why not."""
    try:
        return study_format.read(path, title=title)
    except study_format.FormatError as exc:
        raise BookImportError(str(exc)) from exc


def import_book(
    session: Session,
    *,
    path: Path,
    title: str = "",
    author: str = "",
    kind: str = "study",
    source: str = "upload",
    source_path: str = "",
) -> ImportedBook:
    if path.stat().st_size > MAX_BOOK_BYTES:
        raise BookImportError(
            f"{path.name} is larger than the {MAX_BOOK_BYTES // (1024 * 1024)} MB book limit."
        )
    resolved_kind = kind if kind in BOOK_KINDS else "study"
    fallback_title, fallback_author = split_title_author(path.name)
    supplied_title = bool(title.strip())
    title = (title or fallback_title).strip() or path.stem
    # "Title - Author.pdf" is a useful guess only when nobody supplied a
    # title. An upload that already has a (decoded) filename title must not
    # gain an author from an edition suffix such as "- 1st Edition".
    author = (author or ("" if supplied_title else fallback_author)).strip()
    document = read_document(path, title=title)
    if len(document.sections) > MAX_CHAPTERS:
        document.sections = document.sections[:MAX_CHAPTERS]

    existing = None
    if source_path:
        existing = session.exec(
            select(ImportedBook).where(ImportedBook.source_path == source_path)
        ).first()
    if existing is None:
        existing = session.exec(
            select(ImportedBook).where(
                ImportedBook.title == title,
                ImportedBook.author == author,
            )
        ).first()

    if existing is not None:
        book = existing
        previous = study_reformat.old_sections(session, book.id)
    else:
        book = ImportedBook(title=title, author=author, created_at=utcnow())
        session.add(book)
        previous = []
    book.title = title
    book.author = author
    book.kind = resolved_kind
    book.source = source or "upload"
    book.source_path = source_path
    book.stored_file = str(path)
    session.add(book)
    session.flush()
    assert book.id is not None
    study_reformat.store(session, book, document, previous=previous)
    session.commit()
    session.refresh(book)
    return book


def book_dict(book: ImportedBook) -> dict:
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "kind": book.kind,
        "source": book.source,
        "source_path": book.source_path,
        "chapter_count": book.chapter_count,
        "cadence": getattr(book, "cadence", "day") or "day",
        "created_at": book.created_at.isoformat() if book.created_at else None,
        "format_version": int(getattr(book, "format_version", 0) or 0),
        "reformattable": bool(getattr(book, "stored_file", "") or book.source_path),
    }


def chapter_dict(chapter: ImportedBookChapter) -> dict:
    return {
        "ordinal": chapter.ordinal,
        "section_id": chapter.section_id or f"ordinal-{chapter.ordinal}",
        "label": display_label(chapter),
        "number": devotion_number(chapter.text or ""),
        "characters": len(chapter.text or ""),
        "questions": json.loads(chapter.questions) if chapter.questions else [],
    }


def list_books(session: Session, kind: str | None = None) -> list[ImportedBook]:
    statement = select(ImportedBook)
    if kind:
        statement = statement.where(ImportedBook.kind == kind)
    return list(session.exec(statement.order_by(ImportedBook.title)).all())


def get_book(session: Session, book_id: int) -> ImportedBook | None:
    return session.get(ImportedBook, book_id)


def chapters_for(session: Session, book_id: int) -> list[ImportedBookChapter]:
    return list(
        session.exec(
            select(ImportedBookChapter)
            .where(ImportedBookChapter.book_id == book_id)
            .order_by(ImportedBookChapter.ordinal)
        ).all()
    )


def delete_book(session: Session, book_id: int) -> bool:
    book = session.get(ImportedBook, book_id)
    if book is None:
        return False
    for chapter in chapters_for(session, book_id):
        session.delete(chapter)
    for model in (ImportedBookAlias, ImportedBookAsset):
        for row in session.exec(select(model).where(model.book_id == book_id)).all():
            session.delete(row)
    session.delete(book)
    session.commit()
    return True


def devotional_entries(session: Session, book_id: int) -> list[ImportedBookChapter]:
    """The sections a devotional rotation may land on.

    A printed number ("1.", "42)") means a real devotion; front matter and
    back matter have none, so a numbered book never spends a day on its
    copyright page. Books without printed numbers keep every section.
    """
    chapters = chapters_for(session, book_id)
    numbered = [chapter for chapter in chapters if devotion_number(chapter.text or "")]
    return numbered or chapters


def set_cadence(session: Session, book_id: int, cadence: str) -> bool:
    """Store how often this book advances: every day or once a week."""
    if cadence not in ("day", "week"):
        return False
    book = session.get(ImportedBook, book_id)
    if book is None:
        return False
    book.cadence = cadence
    session.add(book)
    session.commit()
    return True


def devotional_for_day(
    session: Session, book_id: int, day_of_year: int
) -> dict | None:
    """A day's reading from an imported devotional book.

    The rotation starts at the book's first devotion, so day one of the year
    is devotion one. A daily book steps once a day; a weekly book steps once
    a week, which is what a year-long devotional expects. A 365-entry book
    therefore addresses the day directly, and a shorter book cycles rather
    than silently stopping.
    """
    book = session.get(ImportedBook, book_id)
    if book is None or book.kind != "devotional":
        return None
    entries = devotional_entries(session, book_id)
    if not entries:
        return None
    cadence = getattr(book, "cadence", "day") or "day"
    index = (day_of_year - 1) // 7 % len(entries) if cadence == "week" else (day_of_year - 1) % len(entries)
    chapter = entries[index]
    return {
        "source": "library",
        "work": f"book:{book.id}",
        "title": display_label(chapter),
        "reference": book.title,
        "text": chapter.text,
        "kind": "text",
        "url": "",
        "day_of_year": day_of_year,
    }
