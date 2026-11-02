"""The family library as a librarian sees it: browse, tag, add, and audit.

Reads come from a fresh ``metadata.db`` through the Calibre provider. Writes go
through ``calibre_writer`` (version-checked, backed up). After a write the
search index is brought up to date for just that book: tags are patched into
its RAG rows at once, and a new book's author folder is re-indexed in the
background, so Jarvis and the Fasting card see it without a full crawl.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from services.storage.artifacts_api import require_internal_secret
from services.storage.calibre_writer import MAX_BOOK_BYTES, LibraryWriteError, LibraryWriter
from services.storage.models import IndexScanRequest, ProviderConfig
from services.storage.providers import build_provider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/calibre", dependencies=[Depends(require_internal_secret)])

_TEXT_FORMATS = {"EPUB", "AZW3", "MOBI", "FB2"}


def _provider(library_path: str):
    if not library_path.strip():
        raise HTTPException(status_code=422, detail="No Calibre library path is configured.")
    return build_provider(ProviderConfig(kind="calibre", settings={"library_path": library_path}))


async def _catalog(library_path: str) -> dict[int, dict[str, Any]]:
    try:
        return await _provider(library_path)._load_catalog(refresh=True)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("[library] catalogue read failed: %s", exc)
        raise HTTPException(status_code=502, detail="The Calibre library could not be read.") from exc


def _row(book: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": book["id"], "title": book["title"], "authors": book["authors"], "tags": book["tags"],
        "series": book["series"] or None, "series_index": book.get("series_index"),
        "formats": book["formats"], "readable": bool(set(book["formats"]) & _TEXT_FORMATS),
        "path": book["path"],
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


_NOISE = re.compile(
    r"\((?:[^)]*)\)|\[(?:[^\]]*)\]|\b(?:annotated|illustrated|unabridged|abridged|revised|updated|complete|"
    r"edition|ed\.|ebook|e-book|kindle|epub|pdf|\d+(?:st|nd|rd|th))\b",
    re.IGNORECASE,
)


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
        near[(_key(_NOISE.sub(" ", book["title"])), author)].append(book)
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
                       "why": "Only in a format Jarvis cannot read (usually PDF): only the title is searchable. Converting to EPUB in Calibre fixes it."},
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
    writer = LibraryWriter(_provider(req.library_path).client, req.library_path)
    try:
        result = await writer.set_tags(book_id, req.add, req.remove)
    except LibraryWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
):
    data = await file.read(MAX_BOOK_BYTES + 1)
    writer = LibraryWriter(_provider(library_path).client, library_path)
    try:
        result = await writer.add_book(
            data, file.filename or "book", title=title, authors=[a for a in re.split(r"\s*(?:&|;)\s*", authors) if a.strip()],
            tags=_list(tags), series=series, comments=comments,
        )
    except LibraryWriteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background.add_task(_index_folder, library_path, result["path"])
    return {"status": "SUCCESS", **result}
