# services/execution/document_text.py
"""Document text extraction for the workspace/audiobook pipeline.

Raven workspaces frequently hold source material as PDFs, EPUBs, DOCX, RTF,
HTML, or plain text (e.g. ``Week 2 - Scripture.pdf``). Both the workspace
file-read tool and the audiobook regeneration flow need to turn those into
plain text before TTS. We dispatch to the right converter:

* **PDF**     -> ``pdftotext -layout`` (poppler-utils)
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
    paragraph) on its own line — the TTS structure-pause pass relies on that
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
    """Extract text from a PDF preserving layout (headings/list columns)."""
    if not shutil.which(PDFTOTEXT_BIN):
        raise RuntimeError(
            "pdftotext is not installed in this container; cannot extract PDF text. "
            "Install poppler-utils or convert the PDF to text another way."
        )
    return await _run([PDFTOTEXT_BIN, "-layout", path, "-"])


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
    converter is unavailable or yields no text (e.g. a scanned image-only PDF).
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
        raise RuntimeError(f"No text extracted from {path} (scanned/image-only document?)")
    return text
