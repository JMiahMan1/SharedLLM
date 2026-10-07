"""Four read-only actions over the family's Calibre library.

The action set is deliberately closed to reads -- ``list``, ``search``,
``get_book``, ``fetch_text`` -- because this codebase has no approval gate
anywhere in the tool path (the seeded protocol even instructs the agent never
to seek one), and a Calibre deletion or overwrite in ``metadata.db`` cannot
be undone. Not offering the verb at all is the only control that holds; the
Pydantic ``Literal`` refuses the rest at the schema edge.

Everything interesting is delegated: the storage service owns WebDAV and the
Calibre schema rules, and the client owns caching and honest truncation. This
layer exists to turn their refusals into ``ExecutionResult`` failures the
model can read, never to swallow them.
"""

from __future__ import annotations

import logging

from services.execution.calibre_client import (
    CalibreClient,
    CalibreLibraryError,
    CalibreUnavailable,
)
from services.execution.schemas import CalibreRequest, ExecutionResult

log = logging.getLogger("execution.calibre")

SERVICE = "calibre"

_MESSAGE_ROWS = 10


def _client() -> CalibreClient:
    from services.config import INTERNAL_SECRET, STORAGE_SVC_URL

    return CalibreClient(storage_url=STORAGE_SVC_URL, internal_secret=INTERNAL_SECRET)


async def handle_calibre(req: CalibreRequest) -> ExecutionResult:
    """Dispatch one read action, converting every refusal into a visible failure."""
    client = _client()
    try:
        if req.action == "list":
            return await _list(client, req)
        if req.action == "search":
            return await _search(client, req)
        if req.action == "get_book":
            return await _get_book(client, req)
        if req.action == "fetch_text":
            return await _fetch_text(client, req)
        return ExecutionResult.fail(
            f"Action {req.action!r} is not supported. The Calibre surface is "
            "read-only: list, search, get_book, fetch_text.",
            service=SERVICE,
        )
    except (CalibreLibraryError, CalibreUnavailable) as exc:
        return ExecutionResult.fail(str(exc), service=SERVICE)
    except Exception as exc:  # noqa: BLE001 - a tool must answer, not 500
        log.exception("Calibre %s failed for user %s", req.action, req.user_context.user)
        return ExecutionResult.fail(
            f"Calibre {req.action} failed unexpectedly: {exc}", service=SERVICE
        )


def _line(book: dict) -> str:
    author = str(book.get("author") or book.get("authors") or "").strip() or "unknown author"
    book_id = book.get("book_id")
    return f"{book.get('title') or 'Untitled'} by {author} (id {book_id})"


async def _list(client: CalibreClient, req: CalibreRequest) -> ExecutionResult:
    books, total = await client.list_books(limit=req.limit)
    if total == 0:
        return ExecutionResult.ok(
            "The shelf is empty: the configured library has no books.",
            service=SERVICE,
            detail={"total": 0, "books": []},
        )
    shown = books[:_MESSAGE_ROWS]
    lines = "; ".join(_line(book) for book in shown)
    hidden = total - len(shown)
    more = ""
    if hidden > 0:
        hint = "; raise limit" if total > len(books) else ""
        more = f" ({hidden:,} more not shown{hint})"
    message = f"{total:,} books on the shelf. First {len(shown)}: {lines}{more}"
    return ExecutionResult.ok(
        message, service=SERVICE, detail={"total": total, "books": books}
    )


async def _search(client: CalibreClient, req: CalibreRequest) -> ExecutionResult:
    query = str(req.query or "").strip()
    if not query:
        return ExecutionResult.fail(
            "action=search needs a query naming words from a title, author "
            "or tag (for example: wesley sermons).",
            service=SERVICE,
        )
    matches, total = await client.search(query=query, limit=req.limit)
    if not matches:
        return ExecutionResult.ok(
            f"No books on the shelf match {query!r}.",
            service=SERVICE,
            detail={"query": query, "total": 0, "books": []},
        )
    shown = "; ".join(_line(book) for book in matches[:_MESSAGE_ROWS])
    message = f"{total:,} books match {query!r}: {shown}"
    return ExecutionResult.ok(
        message, service=SERVICE, detail={"query": query, "total": total, "books": matches}
    )


async def _get_book(client: CalibreClient, req: CalibreRequest) -> ExecutionResult:
    book = await client.get_book(book_id=req.book_id, path=req.path)
    formats = str(book.get("formats") or "unknown format")
    tags = str(book.get("tags") or "").strip()
    message = (
        f"{_line(book)} — {formats}"
        + (f"; tags: {tags}" if tags else "")
        + f"; shelf path {book.get('path')}"
        + ("; indexed for RAG search" if book.get("indexed") else "; not yet in the RAG index")
    )
    return ExecutionResult.ok(message, service=SERVICE, detail={"book": book})


async def _fetch_text(client: CalibreClient, req: CalibreRequest) -> ExecutionResult:
    text, book = await client.fetch_text(
        book_id=req.book_id, path=req.path, max_chars=req.max_chars
    )
    total = int(book.get("total_chars") or 0)
    truncation = ", truncated" if book.get("truncated") else ""
    header = (
        f"{book.get('title') or 'Book'} by "
        f"{book.get('author') or book.get('authors') or 'unknown author'} "
        f"({total:,} characters{truncation}):\n\n"
    )
    return ExecutionResult.ok(
        header + text,
        service=SERVICE,
        detail={
            "book_id": book.get("book_id"),
            "title": book.get("title"),
            "total_chars": total,
            "truncated": bool(book.get("truncated")),
        },
    )
