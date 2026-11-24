"""PDF text for the search index: the one reader every storage provider uses.

A Calibre book stored only as PDF and a PDF in someone's Nextcloud are indexed
the same way: split at the PDF's bookmarks into chapters (each section carries
the chapter's title), or, with no bookmarks, one section per page ("Page 12").
The parsing is ``book_chapters.pdf_sections``; this adds what a crawl needs on
top of it: it runs off the event loop, it stops at a character budget so one
enormous PDF cannot hold the crawl's memory, and a scan with no text layer is
logged as exactly that and indexed by its metadata.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath

from services.storage import book_chapters
from services.storage.models import ContentSection

log = logging.getLogger("storage.pdf")


def is_pdf_path(path: str) -> bool:
    return PurePosixPath(str(path or "")).suffix.lower() == ".pdf"


def default_max_chars() -> int:
    from services.config import STORAGE_PDF_MAX_CHARS

    return int(STORAGE_PDF_MAX_CHARS)


async def pdf_index_sections(data: bytes, *, max_chars: int, what: str) -> list[ContentSection] | None:
    """The PDF's sections for the index, or ``None`` when it has no text to give.

    ``what`` names the document in the log ("Book Great Lent (12)", a path).
    """
    try:
        source, chapters = await asyncio.to_thread(book_chapters.pdf_sections, data, max_chars)
    except book_chapters.PdfNoTextLayer as exc:
        log.warning(
            "%s is a PDF with no text layer (%s); it is indexed as metadata only. "
            "OCR the PDF (ocrmypdf) to index its text.",
            what, exc,
        )
        return None
    except book_chapters.ChapterError as exc:
        log.warning("Could not read the PDF %s: %s", what, exc)
        return None
    kept = [chapter for chapter in chapters if chapter.text.strip()]
    log.info("%s: %d PDF sections from %s", what, len(kept), source)
    return [ContentSection(ordinal=i, label=chapter.label, text=chapter.text) for i, chapter in enumerate(kept, start=1)]
