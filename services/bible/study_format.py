"""Read any supported study book file into one clean :class:`Document`.

The format readers find structure; :func:`study_blocks.finish` cleans and
labels it. This module only dispatches on the file type and checks the result
is worth keeping.
"""
from __future__ import annotations

from pathlib import Path

from services.bible import study_blocks, study_docx, study_html, study_pdf
from services.bible.study_blocks import Document

SUPPORTED_SUFFIXES = (".pdf", ".epub", ".mobi", ".azw", ".azw3", ".docx", ".txt", ".md")


class FormatError(ValueError):
    """The file cannot be read, with a sentence that says why."""


def read(path: Path, *, title: str = "") -> Document:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            extracted = study_pdf.extract(path)
        elif suffix == ".epub":
            extracted = study_html.extract_epub_bytes(path.read_bytes())
        elif suffix in {".mobi", ".azw", ".azw3"}:
            extracted = study_html.extract_kindle(path)
        elif suffix == ".docx":
            extracted = study_docx.extract(path)
        elif suffix in {".txt", ".md"}:
            extracted = study_blocks.blocks_from_text(path.read_text("utf-8", errors="replace"))
        else:
            raise FormatError(
                f"{path.name} is not a book format this service can read. "
                f"Supported: {', '.join(SUPPORTED_SUFFIXES)}."
            )
    except (study_pdf.PdfReadError, study_html.HtmlReadError, study_docx.DocxReadError) as exc:
        raise FormatError(str(exc)) from exc
    document = study_blocks.finish(extracted, title=title)
    if not document.sections:
        raise FormatError(f"{path.name} yielded no readable text.")
    return document
