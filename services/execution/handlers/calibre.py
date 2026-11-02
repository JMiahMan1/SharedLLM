"""The family's Calibre library, for Jarvis as its librarian.

Reads -- ``list``, ``search``, ``get_book``, ``fetch_text``, ``tags``,
``by_tag``, ``audit`` -- and one reversible write, ``tag``, for admins. This
codebase has no approval gate anywhere in the tool path (the seeded protocol
even instructs the agent never to seek one), and a Calibre deletion cannot be
undone, so no delete is offered at all: not offering the verb is the only
control that holds, and the Pydantic ``Literal`` refuses it at the schema
edge. The audit only *suggests* what the family may not need.

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
        if req.action == "tags":
            return await _tags(client)
        if req.action == "by_tag":
            return await _by_tag(client, req)
        if req.action == "audit":
            return await _audit(client)
        if req.action == "tag":
            return await _tag(client, req)
        return ExecutionResult.fail(
            f"Action {req.action!r} is not supported: list, search, get_book, "
            "fetch_text, tags, by_tag, audit, tag.",
            service=SERVICE,
        )
    except (CalibreLibraryError, CalibreUnavailable) as exc:
        return ExecutionResult.fail(str(exc), service=SERVICE)
    except Exception as exc:  # a tool must answer, not 500
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
        f"{_line(book)}, {formats}"
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


def _row_line(book: dict) -> str:
    authors = " & ".join(book.get("authors") or []) or "unknown author"
    tags = ", ".join(book.get("tags") or [])
    return f"{book.get('title') or 'Untitled'} by {authors} (id {book.get('id')}){f' [{tags}]' if tags else ''}"


async def _tags(client: CalibreClient) -> ExecutionResult:
    tags = await client.tags()
    if not tags:
        return ExecutionResult.ok("No book on the shelf has a tag yet.", service=SERVICE, detail={"tags": []})
    shown = ", ".join(f"{t['name']} ({t['books']})" for t in tags[:60])
    more = f" and {len(tags) - 60} more" if len(tags) > 60 else ""
    return ExecutionResult.ok(f"{len(tags):,} tags, most used first: {shown}{more}", service=SERVICE, detail={"tags": tags})


async def _by_tag(client: CalibreClient, req: CalibreRequest) -> ExecutionResult:
    tag = str(req.tag or "").strip()
    if not tag and not req.untagged:
        return ExecutionResult.fail("action=by_tag needs a tag (e.g. fasting) or untagged=true.", service=SERVICE)
    books, total = await client.books(tag=tag, untagged=req.untagged and not tag, query=str(req.query or ""), limit=req.limit)
    what = f"tagged {tag!r}" if tag else "with no tags"
    if not books:
        return ExecutionResult.ok(f"No books {what}.", service=SERVICE, detail={"total": 0, "books": []})
    lines = "; ".join(_row_line(b) for b in books[:_MESSAGE_ROWS * 2])
    return ExecutionResult.ok(f"{total:,} books {what}: {lines}", service=SERVICE, detail={"total": total, "books": books})


async def _audit(client: CalibreClient) -> ExecutionResult:
    report = await client.audit()
    parts = [f"{report.get('books', 0):,} books."]
    for key, label in (("copies", "duplicate copies"), ("editions", "other editions of the same book")):
        groups = report.get(key) or []
        if groups:
            sample = "; ".join(
                f"keep {_row_line(g['keep'])}, could remove {', '.join(_row_line(o) for o in g['others'])}" for g in groups[:5]
            )
            parts.append(f"{report.get('counts', {}).get(key, len(groups))} {label}: {sample}.")
    for key, label in (("no_file", "records with no book file"), ("unreadable", "books only in a format Jarvis cannot read (convert to EPUB)"),
                       ("untagged", "books with no tags")):
        section = report.get(key) or {}
        if section.get("count"):
            sample = "; ".join(_row_line(b) for b in (section.get("books") or [])[:5])
            parts.append(f"{section['count']:,} {label}, e.g. {sample}.")
    parts.append("These are suggestions; nothing has been removed. Deleting a book is done in Calibre.")
    return ExecutionResult.ok(" ".join(parts), service=SERVICE, detail=report)


async def _tag(client: CalibreClient, req: CalibreRequest) -> ExecutionResult:
    if not req.user_context.is_admin:
        return ExecutionResult.fail("Only an admin can change the library's tags.", service=SERVICE)
    if req.book_id is None:
        return ExecutionResult.fail("action=tag needs book_id (find it with search or by_tag).", service=SERVICE)
    if not req.add_tags and not req.remove_tags:
        return ExecutionResult.fail("action=tag needs add_tags or remove_tags.", service=SERVICE)
    out = await client.set_tags(book_id=req.book_id, add=req.add_tags, remove=req.remove_tags)
    tags = ", ".join(out.get("tags") or []) or "no tags"
    return ExecutionResult.ok(
        f"Book {req.book_id} now has: {tags}. (The previous library database was backed up.)",
        service=SERVICE, detail=out,
    )
