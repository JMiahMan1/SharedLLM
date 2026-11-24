# services/execution/document_text.py
"""Document text extraction for the workspace/audiobook pipeline.

Raven workspaces frequently hold source material as PDFs, EPUBs, DOCX, RTF,
HTML, or plain text (e.g. ``Week 2 - Scripture.pdf``). Both the workspace
file-read tool and the audiobook regeneration flow need to turn those into
plain text before TTS. We dispatch to the right converter:

* **PDF**     -> ``pdftotext -layout`` (poppler-utils), else pypdf when the
  binary is missing or fails on the file (both are in the execution image)
* **EPUB**    -> ``pandoc -t plain``
* **DOCX**    -> ``pandoc -t plain`` (also docx->markdown etc.)
* **RTF/ODT** -> ``pandoc -t plain``
* **HTML**    -> ``html2text``
* **plain**   -> read directly as UTF-8

pandoc is the Swiss-army converter (epub/docx/rtf/odt/latex/ipynb/...); the
execution image ships it along with pdftotext and html2text. Fallbacks raise a
clear RuntimeError so the agent knows which converter is missing instead of
silently returning empty text.
"""
import asyncio
import logging
import os
import shutil

log = logging.getLogger("execution.document_text")

PDFTOTEXT_BIN = os.getenv("PDFTOTEXT_BIN") or "pdftotext"
PANDOC_BIN = os.getenv("PANDOC_BIN") or "pandoc"
HTML2TEXT_BIN = os.getenv("HTML2TEXT_BIN") or "html2text"
_DOC_MAX_BYTES = 128 * 1024 * 1024

# Plain-text formats we can hand to TTS with no converter.
_TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log", ".json", ".xml", ".yml", ".yaml"}


def is_document(path: str) -> bool:
    """Return True when the file needs conversion (not raw UTF-8 text)."""
    return not is_text_file(path)


def is_text_file(path: str) -> bool:
    """Return True when the file is read directly as UTF-8 text."""
    return os.path.splitext(path)[1].lower() in _TEXT_EXTS


async def _pandoc(path: str) -> str:
    """Extract text via pandoc as plain text.

    The plain writer already emits every block element (heading, list item,
    paragraph) on its own line; the TTS structure-pause pass relies on that
    line-delimited structure. Note: ``--split-level`` is an EPUB/HTML chunking
    option taking a heading NUMBER, not a style name, so it has no place here
    (passing e.g. ``paragraph`` makes pandoc reject the whole conversion).
    """
    if not shutil.which(PANDOC_BIN):
        raise RuntimeError(
            "pandoc is not installed in this container; cannot extract embedded text. "
            "Install pandoc or convert the file to text another way."
        )
    return await _run([PANDOC_BIN, path, "-t", "plain", "-s"])


async def _pdftotext(path: str) -> str:
    """Extract text from a PDF preserving layout (headings/list columns).

    pdftotext keeps the layout best; when it is missing or cannot read the file,
    pypdf reads the text layer instead, so a PDF is never refused for want of
    one converter.
    """
    if shutil.which(PDFTOTEXT_BIN):
        try:
            return await _run([PDFTOTEXT_BIN, "-layout", path, "-"])
        except RuntimeError as exc:
            log.warning("pdftotext could not read %s (%s); reading it with pypdf", path, exc)
    else:
        log.info("pdftotext is not installed; reading %s with pypdf", path)
    return await asyncio.to_thread(_pypdf_text, path)


def _pypdf_text(path: str) -> str:
    """Every page's text in order, pages separated by a blank line (no page markers:
    this text may be read aloud)."""
    import io

    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "Neither pdftotext nor pypdf is installed in this container; cannot extract PDF text."
        ) from exc
    with open(path, "rb") as f:
        data = f.read()
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise RuntimeError(f"{path} is encrypted with a password, so its text cannot be read.")
        pages = []
        for page in reader.pages:
            try:
                pages.append((page.extract_text() or "").strip())
            except Exception as exc:
                log.warning("pypdf could not read a page of %s: %s", path, exc)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{path} is not a readable PDF ({exc}).") from exc
    return "\n\n".join(p for p in pages if p)


async def _html2text(path: str) -> str:
    """Extract text from HTML via html2text (markdown-flavored plain text)."""
    if not shutil.which(HTML2TEXT_BIN):
        raise RuntimeError(
            "html2text is not installed in this container; cannot extract HTML text. "
            "Install html2text or convert the file to text another way."
        )
    return await _run([HTML2TEXT_BIN, path])


async def _run(cmd: list[str]) -> str:
    """Run a converter subprocess and return its decoded stdout."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise RuntimeError(f"Document converter timed out: {' '.join(cmd)}") from None
    if proc.returncode != 0:
        raise RuntimeError(
            f"'{cmd[0]}' failed ({proc.returncode}) for {cmd[-1]}: {stderr.decode(errors='replace')[-500:]}"
        )
    return stdout.decode("utf-8", errors="replace").strip()


def _read_plain(path: str) -> str:
    """Read a raw UTF-8 text file directly."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip()


async def extract_document_text(path: str) -> str:
    """Return the plain text of an EPUB/DOCX/PDF/RTF/ODT/HTML/plain file.

    Raises FileNotFoundError when the file is missing, RuntimeError when the
    converter is unavailable or yields no text (e.g. a scanned image-only PDF,
    which OcrRequest can read page by page).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Document not found: {path}")
    if os.path.getsize(path) > _DOC_MAX_BYTES:
        raise RuntimeError(f"Document too large to extract (>{_DOC_MAX_BYTES // (1024 * 1024)} MB): {path}")

    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        text = await _pdftotext(path)
    elif ext in (".html", ".htm"):
        text = await _html2text(path)
    elif ext in _TEXT_EXTS:
        text = _read_plain(path)
    else:
        # EPUB, DOCX, RTF, ODT, LaTeX, ipynb, and everything else pandoc reads.
        text = await _pandoc(path)

    text = text.strip()
    if not text:
        hint = " Read it with OcrRequest instead (it reads a PDF's pages as images)." if ext == ".pdf" else ""
        raise RuntimeError(f"No text extracted from {path} (scanned/image-only document?).{hint}")
    return text
