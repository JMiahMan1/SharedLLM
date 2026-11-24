# services/execution/pdf_text.py
"""Text out of a PDF, page by page, within a character budget.

One reader for every tool that hands a PDF to the model (WebReadRequest,
StorageFileReadRequest, OcrRequest): each page is marked ``--- Page N ---`` and
the result says which pages it holds ("pages 1-6 of 23"), so the model knows
there is more and can ask again with ``start_page``. ``render_pdf_pages`` turns
pages into images for vision OCR, for a scan with no text layer.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

log = logging.getLogger("execution.pdf_text")

#: The character cap every reader applies to what it returns to the model.
DEFAULT_MAX_CHARS = 15000

SCANNED_NOTE = (
    "This PDF is a scanned image with no text layer, so there is no text to read. "
    "OcrRequest reads a PDF's pages as images (a workspace file or inline bytes)."
)


class PdfReadError(Exception):
    """A PDF that cannot be opened (encrypted, damaged); the message says which."""


@dataclass
class PdfText:
    title: str
    page_count: int
    first_page: int
    last_page: int
    text: str
    truncated: bool
    scanned: bool

    @property
    def pages_label(self) -> str:
        """"pages 1-6 of 23" (or "page 4 of 23" for a single page)."""
        if self.first_page == self.last_page:
            return f"page {self.first_page} of {self.page_count}"
        return f"pages {self.first_page}-{self.last_page} of {self.page_count}"

    @property
    def next_page(self) -> int | None:
        """The start_page that continues the read, or None when it reached the end."""
        return self.last_page + 1 if self.last_page < self.page_count else None

    def as_detail(self) -> dict:
        detail: dict = {
            "title": self.title,
            "content": self.text,
            "content_type": "application/pdf",
            "page_count": self.page_count,
            "first_page": self.first_page,
            "last_page": self.last_page,
            "pages": self.pages_label,
        }
        if self.next_page is not None:
            detail["next_start_page"] = self.next_page
            detail["note"] = (
                f"Only {self.pages_label} fit; call again with start_page={self.next_page} to read on."
            )
        if self.scanned:
            detail["note"] = SCANNED_NOTE
        return detail


def is_pdf(content_type: str | None, data: bytes | None) -> bool:
    """A PDF by its declared type or by its first bytes (servers often say octet-stream)."""
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype in ("application/pdf", "application/x-pdf"):
        return True
    return bool(data) and data[:1024].lstrip()[:5] == b"%PDF-"


def _metadata_title(reader) -> str:
    try:
        meta = reader.metadata
        title = (meta.title if meta is not None else None) or ""
    except Exception:
        return ""
    return str(title).strip()


def extract_pdf_text(
    data: bytes,
    *,
    start_page: int = 1,
    max_chars: int = DEFAULT_MAX_CHARS,
    fallback_title: str = "",
) -> PdfText:
    """Read pages from ``start_page`` on until ``max_chars`` is spent.

    Raises :class:`PdfReadError` for a PDF that cannot be opened, and
    ``ValueError`` for a ``start_page`` past the last page.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except Exception as exc:
                raise PdfReadError(f"The PDF is encrypted and could not be opened ({exc}).") from exc
            if not opened:
                raise PdfReadError("The PDF is encrypted with a password, so its text cannot be read.")
        page_count = len(reader.pages)
    except PdfReadError:
        raise
    except Exception as exc:
        raise PdfReadError(f"The file is not a readable PDF ({exc}).") from exc

    if page_count == 0:
        raise PdfReadError("The PDF has no pages.")
    start = max(1, int(start_page or 1))
    if start > page_count:
        raise ValueError(f"start_page {start} is past the end: the PDF has {page_count} pages.")

    parts: list[str] = []
    used = 0
    last = start
    truncated = False
    any_text = False
    for number in range(start, page_count + 1):
        try:
            page_text = (reader.pages[number - 1].extract_text() or "").strip()
        except Exception as exc:
            log.warning("pdf page %d could not be read: %s", number, exc)
            page_text = ""
        block = f"--- Page {number} ---\n{page_text}\n"
        if parts and used + len(block) > max_chars:
            truncated = True
            break
        if len(block) > max_chars:
            block = block[:max_chars] + "\n... (page truncated) ...\n"
            truncated = True
        parts.append(block)
        used += len(block)
        last = number
        any_text = any_text or bool(page_text)
        if truncated:
            break

    title = _metadata_title(reader) or fallback_title or "PDF document"
    return PdfText(
        title=title,
        page_count=page_count,
        first_page=start,
        last_page=last,
        text="\n".join(parts).strip(),
        truncated=truncated,
        scanned=not any_text,
    )


#: Dots per inch a page is rendered at for vision OCR: enough for body text,
#: small enough that a page image stays a few hundred KB.
RENDER_DPI = 150


def _pdftoppm_pages(data: bytes, out_dir: str, first: int, last: int, dpi: int) -> list[tuple[int, str]]:
    import glob
    import os
    import re
    import shutil
    import subprocess

    binary = shutil.which("pdftoppm")
    if not binary:
        return []
    source = os.path.join(out_dir, "source.pdf")
    with open(source, "wb") as handle:
        handle.write(data)
    prefix = os.path.join(out_dir, "page")
    try:
        subprocess.run(
            [binary, "-r", str(dpi), "-f", str(first), "-l", str(last), "-png", source, prefix],
            check=True, capture_output=True, timeout=180,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("pdftoppm could not render pages %d-%d: %s", first, last, exc)
        return []
    pages = []
    for path in glob.glob(prefix + "-*.png"):
        match = re.search(r"-(\d+)\.png$", path)
        if match:
            pages.append((int(match.group(1)), path))
    return sorted(pages)


def _embedded_page_images(reader, out_dir: str, first: int, last: int) -> list[tuple[int, str]]:
    """Each page's largest embedded image: what a scanned page is, when pdftoppm is missing."""
    import os

    pages = []
    for number in range(first, last + 1):
        try:
            images = list(reader.pages[number - 1].images)
        except Exception as exc:
            log.warning("pdf page %d images could not be read: %s", number, exc)
            continue
        if not images:
            continue
        image = max(images, key=lambda img: len(img.data or b""))
        suffix = os.path.splitext(str(image.name or ""))[1] or ".png"
        path = os.path.join(out_dir, f"page-{number}{suffix}")
        with open(path, "wb") as handle:
            handle.write(image.data)
        pages.append((number, path))
    return pages


def render_pdf_pages(
    data: bytes, out_dir: str, *, first_page: int = 1, max_pages: int = 5, dpi: int = RENDER_DPI
) -> tuple[list[tuple[int, str]], int]:
    """Page images of a PDF for vision OCR: ``([(page number, image path)], page_count)``.

    pdftoppm (poppler, in the base image) renders each page; without it, each
    page's own embedded image is used, which is what a scanned page is. Raises
    :class:`PdfReadError` for a PDF that cannot be opened and ``ValueError`` for
    a ``first_page`` past the end.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfReadError("The PDF is encrypted with a password, so its pages cannot be read.")
        page_count = len(reader.pages)
    except PdfReadError:
        raise
    except Exception as exc:
        raise PdfReadError(f"The file is not a readable PDF ({exc}).") from exc
    if page_count == 0:
        raise PdfReadError("The PDF has no pages.")
    first = max(1, int(first_page or 1))
    if first > page_count:
        raise ValueError(f"start_page {first} is past the end: the PDF has {page_count} pages.")
    last = min(page_count, first + max(1, int(max_pages)) - 1)
    pages = _pdftoppm_pages(data, out_dir, first, last, dpi) or _embedded_page_images(reader, out_dir, first, last)
    return pages, page_count
