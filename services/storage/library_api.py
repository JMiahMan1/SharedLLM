"""The family library as a librarian sees it: browse, tag, add, and audit.

Reads come from a fresh ``metadata.db`` through the Calibre provider. Writes go
through ``calibre_writer`` (version-checked, backed up). After a write the
search index is brought up to date for just that book: tags are patched into
its RAG rows at once, and a new book's author folder is re-indexed in the
background, so Jarvis and the Fasting card see it without a full crawl.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections import defaultdict
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from services.storage import book_cache, book_chapters, book_lookup
from services.storage.artifacts_api import require_internal_secret
from services.storage.calibre_writer import MAX_BOOK_BYTES, LibraryWriteError, LibraryWriter
from services.storage.models import IndexScanRequest, ProviderConfig
from services.storage.providers import build_provider
from services.storage.providers_impl.calibre import _TEXT_FORMATS, forget_catalog_checks

log = logging.getLogger(__name__)
router = APIRouter(prefix="/calibre", dependencies=[Depends(require_internal_secret)])

#: Formats whose text Jarvis reads are the shelf provider's own ``_TEXT_FORMATS``
#: (PDF among them): a book in one of them is searchable by its words, not its title only.


def _provider(library_path: str):
    if not library_path.strip():
        raise HTTPException(status_code=422, detail="No Calibre library path is configured.")
    return build_provider(ProviderConfig(kind="calibre", settings={"library_path": library_path}))


@contextlib.asynccontextmanager
async def _opened(library_path: str):
    """The library's provider, kept open between requests.

    ``build_provider`` keeps one provider per login and library, and its
    Nextcloud session with it: closing it after each request (as this once did,
    when every request built its own) made the next request open a new
    connection and have Nextcloud check the password again, seconds per call.
    ``build_provider`` closes a provider when it drops it.
    """
    yield _provider(library_path)


async def _catalog(library_path: str) -> dict[int, dict[str, Any]]:
    try:
        async with _opened(library_path) as provider:
            return await provider._load_catalog(refresh=True)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("[library] catalogue read failed: %s", exc)
        raise HTTPException(status_code=502, detail="The Calibre library could not be read.") from exc


def _files(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Each format's file at its full path in Nextcloud (what a fetch takes)."""
    out = []
    for entry in book.get("files") or []:
        full = str(entry.get("path") or "")
        out.append({"format": full.rsplit(".", 1)[-1].upper() if "." in full else "", "path": full, "size": entry.get("size")})
    return out


def _row(book: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": book["id"], "title": book["title"], "authors": book["authors"], "tags": book["tags"],
        "series": book["series"] or None, "series_index": book.get("series_index"),
        "formats": book["formats"], "readable": bool(set(book["formats"]) & _TEXT_FORMATS),
        "path": book["path"],
        "files": _files(book),
    }


# ── Reading ────────────────────────────────────────────────────────────────


class LibraryQuery(BaseModel):
    library_path: str
    q: str = Field(default="", max_length=200)
    tag: str = Field(default="", max_length=100)
    author: str = Field(default="", max_length=200)
    untagged: bool = False
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


@router.post("/books")
async def list_books(req: LibraryQuery):
    """Books matching every word of ``q`` (title, author, series, tags), a tag, or an author."""
    books = (await _catalog(req.library_path)).values()
    words = [w for w in req.q.lower().split() if w]
    tag = req.tag.strip().lower().removeprefix("jarvis:")
    author = req.author.strip().lower()
    out = []
    for book in books:
        tags = [t.lower() for t in book["tags"]]
        if tag and tag not in tags and f"jarvis:{tag}" not in tags:
            continue
        if author and not any(author in a.lower() for a in book["authors"]):
            continue
        if req.untagged and book["tags"]:
            continue
        if words:
            hay = " ".join([book["title"], *book["authors"], book["series"] or "", *book["tags"]]).lower()
            if not all(w in hay for w in words):
                continue
        out.append(book)
    out.sort(key=lambda b: (str(b.get("sort") or b["title"]).lower(), b["id"]))
    return {"total": len(out), "books": [_row(b) for b in out[req.offset:req.offset + req.limit]]}


class LibraryRef(BaseModel):
    library_path: str


@router.post("/tags")
async def list_tags(req: LibraryRef):
    """Every tag with how many books carry it, most used first."""
    counts: dict[str, int] = defaultdict(int)
    for book in (await _catalog(req.library_path)).values():
        for tag in book["tags"]:
            counts[tag] += 1
    tags = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return {"tags": [{"name": name, "books": n} for name, n in tags]}


class ChapterQuery(BaseModel):
    library_path: str
    chapter: str = Field(default="", max_length=200)
    part: int = Field(default=1, ge=1, le=1000)
    max_chars: int = Field(default=book_chapters.DEFAULT_PART_CHARS, ge=500, le=200000)


#: Formats whose chapters can be read, best first.
_CHAPTER_FORMATS = ("EPUB", "PDF", "TXT")


def _locate(book: dict[str, Any] | None, book_id: int) -> tuple[str, str]:
    """The format chosen for chapters and that file's path, or the refusal."""
    if book is None:
        raise HTTPException(status_code=404, detail=f"No book with id {book_id} in the library.")
    files = {f["format"]: f["path"] for f in _files(book)}
    fmt = next((f for f in _CHAPTER_FORMATS if f in files), None)
    if fmt is None:
        stored = ", ".join(book.get("formats") or []) or "no file"
        raise HTTPException(
            status_code=422,
            detail=f"{book['title']} is stored only as {stored}, which has no chapter reader here. "
            "Converting it to EPUB in Calibre makes its chapters readable.",
        )
    return fmt, files[fmt]


async def _file_signature(provider: Any, book: dict[str, Any], path: str) -> tuple[Any, ...]:
    """What says the book file is unchanged: its WebDAV ETag, size and mtime.

    When the server cannot be asked, the catalogue's own record of the file
    (Calibre rewrites metadata.db whenever it replaces a format) stands in.
    """
    stat = getattr(provider.client, "stat", None)
    found = None
    if callable(stat):
        with suppress(Exception):
            found = await stat(path)
    if isinstance(found, dict) and any(found.values()):
        return ("dav", found.get("etag"), found.get("size"), found.get("mtime"))
    size = next((f.get("size") for f in _files(book) if f["path"] == path), None)
    return ("catalogue", size, getattr(provider, "_catalog_digest", None))


async def _opened_book(library_path: str, book_id: int) -> tuple[dict[str, Any], str, book_chapters.OpenedBook]:
    """The book's row, its chapter format, and the book opened for reading, from the cache when unchanged.

    Logs one line per call with where the time went (catalogue, stat, download,
    open), which is how a slow chapter read is diagnosed.
    """
    timings: dict[str, float] = {}
    started = time.perf_counter()
    try:
        async with _opened(library_path) as provider:
            book = (await provider._load_catalog(refresh=True)).get(book_id)
            timings["catalogue"] = time.perf_counter() - started
            fmt, path = _locate(book, book_id)
            mark = time.perf_counter()
            signature = await _file_signature(provider, book, path)
            timings["stat"] = time.perf_counter() - mark
            key = (getattr(provider, "catalog_key", library_path), book_id, path)

            async def load() -> tuple[book_chapters.OpenedBook, int]:
                mark = time.perf_counter()
                data = await provider.client.get_file_bytes(path, timeout=provider.download_timeout)
                timings["download"] = time.perf_counter() - mark
                if not data:
                    raise HTTPException(status_code=502, detail=f"The {fmt} file of {book['title']} could not be downloaded.")
                timings["mb"] = len(data) / 1e6
                mark = time.perf_counter()
                try:
                    opened = await asyncio.to_thread(book_chapters.open_book, data, fmt)
                except book_chapters.ChapterError as exc:
                    raise HTTPException(status_code=422, detail=f"{book['title']}: {exc}") from exc
                timings["open"] = time.perf_counter() - mark
                return opened, opened.size_bytes()

            opened, cached = await book_cache.BOOKS.get_or_load(key, signature, load)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("[library] book %s file read failed: %s", book_id, exc)
        raise HTTPException(status_code=502, detail="The book file could not be read from the library.") from exc
    log.info(
        "[library] book %s (%s): %s; %s, total %.2f s",
        book_id, fmt, "cached" if cached else "downloaded and opened",
        ", ".join(f"{k} {v:.1f} MB" if k == "mb" else f"{k} {v:.2f} s" for k, v in timings.items()),
        time.perf_counter() - started,
    )
    return book, fmt, opened


def _as_opened(fmt: str, data: bytes | book_chapters.OpenedBook) -> book_chapters.OpenedBook:
    return data if isinstance(data, book_chapters.OpenedBook) else book_chapters.open_book(data, fmt)


def chapter_payload(book: dict[str, Any], fmt: str, data: bytes | book_chapters.OpenedBook, *, chapter: str = "",
                    part: int = 1, max_chars: int = book_chapters.DEFAULT_PART_CHARS) -> dict[str, Any]:
    """The book's contents and, when ``chapter`` is named, that chapter's text (one part of it)."""
    try:
        opened = _as_opened(fmt, data)
    except book_chapters.ChapterError as exc:
        raise HTTPException(status_code=422, detail=f"{book['title']}: {exc}") from exc
    chapters = opened.chapters
    out: dict[str, Any] = {
        "book": _row(book), "format": fmt, "source": opened.source, "chapter": None,
    }
    if not str(chapter or "").strip():
        out["chapters"] = [c.row(i) for i, c in enumerate(chapters)]
        return out
    try:
        index = book_chapters.find_chapter(chapters, chapter)
    except book_chapters.ChapterError as exc:
        raise HTTPException(status_code=404, detail=f"{book['title']}: {exc}") from exc
    started = time.perf_counter()
    text = opened.chapter_text(index)
    extract_s = time.perf_counter() - started
    found = chapters[index]
    out["chapters"] = [c.row(i) for i, c in enumerate(chapters)]
    parts = book_chapters.split_parts(text, max_chars)
    if part > len(parts):
        raise HTTPException(status_code=422, detail=f"{book['title']}, {found.title}: there are only {len(parts)} parts.")
    out["chapter"] = {**found.row(index), "part": part, "parts": len(parts), "text": parts[part - 1]}
    log.info("[library] book %s chapter %r: extracted in %.2f s (%d chars, %d pages read so far)",
             book.get("id"), chapter, extract_s, len(text), opened.pages_extracted)
    return out


def _resize_cached(opened: book_chapters.OpenedBook) -> None:
    """A PDF chapter just read grew its cache entry; keep the byte budget honest."""
    if isinstance(opened, book_chapters.OpenedBook):
        book_cache.BOOKS.resize_value(opened, opened.size_bytes())


@router.post("/books/{book_id}/chapters")
async def book_chapter_list(book_id: int, req: ChapterQuery):
    """A book's chapters (from its contents, outline or headings), and one chapter's text if named."""
    book, fmt, opened = await _opened_book(req.library_path, book_id)
    out = await asyncio.to_thread(
        chapter_payload, book, fmt, opened, chapter=req.chapter, part=req.part, max_chars=req.max_chars,
    )
    _resize_cached(opened)
    return out


class BookTextQuery(BaseModel):
    library_path: str
    max_chars: int = Field(default=20000, ge=500, le=200000)


def text_payload(book: dict[str, Any], fmt: str, data: bytes | book_chapters.OpenedBook, *,
                 max_chars: int = 20000) -> dict[str, Any]:
    """A whole book's text, cut at ``max_chars``, read from its own file."""
    try:
        text = (data.whole_text() if isinstance(data, book_chapters.OpenedBook)
                else book_chapters.read_book_text(data, fmt))
    except book_chapters.ChapterError as exc:
        raise HTTPException(status_code=422, detail=f"{book['title']}: {exc}") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail=f"{book['title']}: its {fmt} file has no extractable text "
                            "(a scanned book needs OCR).")
    total = len(text)
    return {"book": _row(book), "format": fmt, "text": text[:max_chars], "total_chars": total,
            "truncated": total > max_chars}


@router.post("/books/{book_id}/text")
async def book_text(book_id: int, req: BookTextQuery):
    """A book's whole text from its EPUB, PDF or TXT file."""
    book, fmt, opened = await _opened_book(req.library_path, book_id)
    out = await asyncio.to_thread(text_payload, book, fmt, opened, max_chars=req.max_chars)
    _resize_cached(opened)
    return out


_NOISE = re.compile(
    r"\((?:[^)]*)\)|\[(?:[^\]]*)\]|\b(?:annotated|illustrated|unabridged|abridged|revised|updated|complete|"
    r"edition|ed\.|ebook|e-book|kindle|epub|pdf|\d+(?:st|nd|rd|th))\b",
    re.IGNORECASE,
)


_VOLUME = re.compile(r"\b(?:vol(?:ume)?|part|book|no|number)\.?\s*([ivxlc]+|\d+)\b", re.IGNORECASE)


def _key(text: str) -> str:
    text = re.sub(r"^(a|an|the)\s+", "", text.strip().lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def audit(books: list[dict[str, Any]], limit: int = 40) -> dict[str, Any]:
    """What a librarian would raise: copies, editions, books Jarvis cannot read,
    books with no file, and books with no tags. Suggestions only; nothing is removed."""
    exact: dict[tuple[str, str], list[dict]] = defaultdict(list)
    near: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for book in books:
        author = _key(book["authors"][0]) if book["authors"] else ""
        exact[(_key(book["title"]), author)].append(book)
        # Volume, part and book numbers survive the noise stripping, so the
        # volumes of one work are never taken for editions of it.
        volumes = " ".join(_VOLUME.findall(book["title"])).lower()
        near[(_key(_NOISE.sub(" ", book["title"])) + (f" vol {volumes}" if volumes else ""), author)].append(book)
    copies = [group for group in exact.values() if len(group) > 1]
    seen = {b["id"] for group in copies for b in group}
    editions = [group for group in near.values() if len(group) > 1 and not {b["id"] for b in group} <= seen]

    def group_out(group: list[dict], why: str) -> dict:
        best = max(group, key=lambda b: (bool(set(b["formats"]) & _TEXT_FORMATS), len(b["tags"]), len(b["formats"]), -b["id"]))
        return {
            "why": why,
            "keep": _row(best),
            "others": [_row(b) for b in group if b["id"] != best["id"]],
        }

    no_file = [b for b in books if not b["formats"]]
    unreadable = [b for b in books if b["formats"] and not set(b["formats"]) & _TEXT_FORMATS]
    untagged = [b for b in books if not b["tags"]]
    return {
        "books": len(books),
        "copies": [group_out(g, "The same title by the same author is in the library more than once.") for g in copies][:limit],
        "editions": [group_out(g, "Looks like another edition or format of the same book.") for g in editions][:limit],
        "no_file": {"count": len(no_file), "books": [_row(b) for b in no_file[:limit]],
                    "why": "A record with no book file: nothing to read."},
        "unreadable": {"count": len(unreadable), "books": [_row(b) for b in unreadable[:limit]],
                       "why": "Only in a format Jarvis cannot read (such as DJVU or CBZ): only the title is searchable. Converting to EPUB or PDF in Calibre fixes it."},
        "untagged": {"count": len(untagged), "books": [_row(b) for b in untagged[:limit]],
                     "why": "No tags, so no topic (fasting, prayer, Bible) can find it."},
        "counts": {"copies": len(copies), "editions": len(editions)},
    }


@router.post("/audit")
async def audit_library(req: LibraryRef):
    return audit(list((await _catalog(req.library_path)).values()))


# ── Writing ────────────────────────────────────────────────────────────────


async def _rag_tags(book_id: int, tags: list[str]) -> None:
    """Patch a book's tags into its search rows, so topics find it at once."""
    from services.common.http import get_client
    from services.config import INTERNAL_SECRET, RAG_SVC_URL

    try:
        async with get_client() as client, client.post(
            f"{RAG_SVC_URL}/rag/calibre/tags", json={"calibre_id": book_id, "tags": "; ".join(tags)},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        ) as resp:
            if resp.status >= 400:
                log.warning("[library] RAG tag patch for %s returned %s", book_id, resp.status)
    except Exception as exc:
        log.warning("[library] RAG tag patch for %s failed: %s", book_id, exc)


async def _index_folder(library_path: str, folder: str) -> None:
    """Index the author folder a new book went into (a scoped crawl)."""
    from services.storage import main as storage_main

    library = "/" + library_path.strip("/")
    req = IndexScanRequest(
        provider=ProviderConfig(kind="calibre", settings={"library_path": library_path}),
        path=f"{library}/{folder.split('/', 1)[0]}", recursive=True,
    )
    await storage_main._run_full_index_task(req)


class TagChange(BaseModel):
    library_path: str
    add: list[str] = Field(default_factory=list, max_length=20)
    remove: list[str] = Field(default_factory=list, max_length=20)


@router.post("/books/{book_id}/tags")
async def change_tags(book_id: int, req: TagChange, background: BackgroundTasks):
    if not req.add and not req.remove:
        raise HTTPException(status_code=422, detail="Name a tag to add or remove.")
    async with _opened(req.library_path) as provider:
        writer = LibraryWriter(provider.client, req.library_path)
        try:
            result = await writer.set_tags(book_id, req.add, req.remove)
        except LibraryWriteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            forget_catalog_checks()
    background.add_task(_rag_tags, book_id, result["tags"])
    return {"status": "SUCCESS", **result}


def _list(value: str) -> list[str]:
    """A form field that may be a JSON list or "a; b" / "a, b"."""
    value = (value or "").strip()
    if value.startswith("["):
        try:
            return [str(v) for v in json.loads(value) if str(v).strip()]
        except ValueError:
            pass
    return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]


@router.post("/books/inspect")
async def inspect_book(
    library_path: str = Form(...),
    file: UploadFile = File(...),
    online: bool = Form(default=True),
):
    """Everything Jarvis can find about a book before it is added (nothing is saved)."""
    data = await file.read(MAX_BOOK_BYTES + 1)
    if not data or len(data) > MAX_BOOK_BYTES:
        raise HTTPException(status_code=422, detail=f"Choose a book file under {MAX_BOOK_BYTES // (1024 * 1024)} MB.")
    # Suggestions without the library's own tags are still suggestions.
    existing: list[str] = []
    with suppress(HTTPException):
        existing = sorted({t for book in (await _catalog(library_path)).values() for t in book["tags"]})
    return await book_lookup.inspect(data, file.filename or "book", existing, online=online)


@router.post("/books/add")
async def add_book(
    background: BackgroundTasks,
    library_path: str = Form(...),
    file: UploadFile = File(...),
    title: str = Form(default=""),
    authors: str = Form(default=""),
    tags: str = Form(default=""),
    series: str = Form(default=""),
    comments: str = Form(default=""),
    publisher: str = Form(default=""),
    published: str = Form(default=""),
    isbn: str = Form(default=""),
    language: str = Form(default=""),
    cover_url: str = Form(default=""),
):
    data = await file.read(MAX_BOOK_BYTES + 1)
    cover = await book_lookup.fetch_cover(cover_url) if cover_url else None
    async with _opened(library_path) as provider:
        writer = LibraryWriter(provider.client, library_path)
        try:
            result = await writer.add_book(
                data, file.filename or "book", title=title, authors=[a for a in re.split(r"\s*(?:&|;)\s*", authors) if a.strip()],
                tags=_list(tags), series=series, comments=comments, publisher=publisher, published=published,
                isbn=isbn, language=language, cover=cover,
            )
        except LibraryWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            forget_catalog_checks()
    background.add_task(_index_folder, library_path, result["path"])
    return {"status": "SUCCESS", **result}
