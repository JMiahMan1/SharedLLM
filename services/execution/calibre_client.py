"""Read the family's Calibre library through the storage service.

Raven has to be able to answer "what books do I have?" and fetch one's text
without holding WebDAV credentials or a second opinion on the Calibre schema.
``services/storage`` owns both: its Calibre provider learned, by trial, that
``data.name`` carries no extension, that ``books.path`` is the locator (not
``dir``), and that every path segment must be percent-encoded individually or
Curly's apostrophe 404s. Duplicating any of that here would put a second copy
of the credentials and the format rules in the places to rotate.

So every call goes through storage's ``/providers`` routes with the internal
secret, and read-only is enforced two floors down -- the provider refuses
writes because Calibre itself owns ``metadata.db``. This client exposes no
write method at all; an irreversible operation must not be nameable by a
model in a codebase that has no approval gate.

The catalogue listing is cached briefly. ``list_entries`` re-downloads
``metadata.db`` on every call, and a search-then-fetch turn would otherwise
pull the 3 MB file twice; five minutes of shelf staleness is well inside how
often the library actually changes.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any

from services.execution import http_client

log = logging.getLogger("execution.calibre")

LIST_TIMEOUT_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 300.0
SETTING_TIMEOUT_SECONDS = 5.0

LIBRARY_SETTING = "calibre_library_path"
CACHE_TTL_SECONDS = 300.0

FETCH_MAX_BYTES = 4 * 1024 * 1024

_META_KEYS = ("title", "author", "authors", "tags", "series", "formats", "published")


class CalibreLibraryError(ValueError):
    """The caller asked for something the shelf cannot answer (refused)."""


class CalibreUnavailable(RuntimeError):
    """Storage, Identity or the shelf itself could not be reached (failed)."""


_cache: dict[str, Any] = {
    "library_path": None,
    "library_path_at": 0.0,
    "entries": None,
    "entries_at": 0.0,
}


def clear_cache() -> None:
    """Drop both caches so the next call re-reads Identity and the shelf."""
    _cache["library_path"] = None
    _cache["library_path_at"] = 0.0
    _cache["entries"] = None
    _cache["entries_at"] = 0.0


class CalibreClient:
    """Browse, search and read books; every method is a read."""

    def __init__(self, *, storage_url: str, internal_secret: str) -> None:
        self.storage_url = str(storage_url or "").strip().rstrip("/")
        self.internal_secret = str(internal_secret or "")

    def _storage_endpoint(self) -> str:
        if not self.storage_url:
            raise CalibreUnavailable(
                "The storage service URL is not set, so the Calibre library "
                "cannot be reached. Set storage_svc_url (or STORAGE_SVC_URL)."
            )
        if not self.internal_secret:
            raise CalibreUnavailable(
                "The internal secret is not set, so the storage service will "
                "refuse the request. Set INTERNAL_SECRET."
            )
        return self.storage_url

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Secret": self.internal_secret}

    async def _library_path(self) -> str:
        """The configured shelf root, read fresh from Identity at most a TTL apart.

        Blank is refused with the setting's name rather than guessed: a
        defaulted path would browse some other folder and report an empty or
        wrong shelf as if it were the library.
        """
        now = time.monotonic()
        cached = _cache["library_path"]
        if cached is not None and now - float(_cache["library_path_at"]) < CACHE_TTL_SECONDS:
            return str(cached)
        if not self.internal_secret:
            raise CalibreUnavailable(
                "The internal secret is not set, so Identity will refuse the "
                "request. Set INTERNAL_SECRET."
            )
        from services.config import IDENTITY_SVC_URL

        url = f"{IDENTITY_SVC_URL}/api/settings/{LIBRARY_SETTING}"
        resp = await http_client.request(
            "GET", url, headers=self._headers(), timeout=SETTING_TIMEOUT_SECONDS
        )
        status = int(resp.get("status_code") or 0)
        if status != 200:
            raise CalibreUnavailable(
                f"Identity answered {status} reading the {LIBRARY_SETTING} "
                "setting, so the library folder cannot be resolved."
            )
        try:
            body = json.loads(resp.get("text") or "")
        except ValueError as exc:
            raise CalibreUnavailable(
                f"Identity did not answer JSON for {LIBRARY_SETTING}: {exc}"
            ) from exc
        value = str((body or {}).get("value") or "").strip()
        if not value:
            raise CalibreLibraryError(
                f"Setting {LIBRARY_SETTING} is not configured, so there is no "
                "shelf to read. Point it at the Nextcloud folder holding the "
                "library, for example /Books/Text."
            )
        _cache["library_path"] = value
        _cache["library_path_at"] = now
        return value

    def _provider(self, library_path: str) -> dict[str, Any]:
        return {"kind": "calibre", "settings": {"library_path": library_path}}

    async def _entries(self) -> list[dict[str, Any]]:
        """Every book on the shelf as storage presents them, cached."""
        now = time.monotonic()
        cached = _cache["entries"]
        if cached is not None and now - float(_cache["entries_at"]) < CACHE_TTL_SECONDS:
            return list(cached)

        root = await self._library_path()
        url = f"{self._storage_endpoint()}/providers/list"
        resp = await http_client.request(
            "POST",
            url,
            json={"provider": self._provider(root), "path": "/", "recursive": True},
            headers=self._headers(),
            timeout=LIST_TIMEOUT_SECONDS,
        )
        body = _decode(resp, url)
        entries = body.get("entries")
        if not isinstance(entries, list):
            raise CalibreUnavailable(
                f"{url} did not return a list of entries, so the shelf cannot "
                "be read. It answered with "
                f"{sorted(body)[:6]}."
            )
        _cache["entries"] = list(entries)
        _cache["entries_at"] = now
        return list(entries)

    async def list_books(self, *, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
        """The first ``limit`` books and how many the shelf holds in total."""
        entries = await self._entries()
        return [_book_row(entry) for entry in entries[: max(int(limit), 0)]], len(entries)

    async def search(self, *, query: str, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
        """Books whose title, author or tags contain every word of ``query``.

        All-words-else-dropped would answer "wesley sermon" with every book
        mentioning either word; requiring every word keeps the list short
        enough for a model to read, and ranking puts a title substring first
        so the obvious match leads.
        """
        tokens = [t for t in re.findall(r"\w+", str(query or "").lower()) if t]
        if not tokens:
            raise CalibreLibraryError(
                "search needs at least one word to look for in titles, "
                "authors or tags."
            )
        matches: list[tuple[int, str, dict[str, Any]]] = []
        for entry in await self._entries():
            row = _book_row(entry)
            haystack = " ".join(
                str(row.get(key) or "") for key in ("title", "author", "authors", "tags")
            ).lower()
            if not all(token in haystack for token in tokens):
                continue
            title = str(row.get("title") or "").lower()
            rank = 0 if str(query).strip().lower() in title else 1
            matches.append((rank, title, row))
        matches.sort(key=lambda item: (item[0], item[1]))
        rows = [row for _rank, _title, row in matches]
        return rows[: max(int(limit), 0)], len(rows)

    async def get_book(self, *, book_id: Any = None, path: str | None = None) -> dict[str, Any]:
        """One book's shelf row, located by Calibre id or shelf path."""
        entries = await self._entries()
        if book_id is not None and str(book_id).strip():
            wanted = str(book_id).strip()
            for entry in entries:
                row = _book_row(entry)
                if str(row.get("book_id")) == wanted:
                    return row
            raise CalibreLibraryError(
                f"No book with id {wanted} on the shelf ({len(entries)} books "
                "are). Use list or search to find the id."
            )
        if path and str(path).strip():
            wanted_path = str(path).strip()
            for entry in entries:
                if str(entry.get("path") or "") == wanted_path:
                    return _book_row(entry)
            raise CalibreLibraryError(
                f"No book at {wanted_path!r} on the shelf ({len(entries)} "
                "books are). Use list or search to find the path."
            )
        raise CalibreLibraryError(
            "get_book and fetch_text need a book_id (preferred) or a shelf "
            "path; neither was supplied."
        )

    async def fetch_text(
        self, *, book_id: Any = None, path: str | None = None, max_chars: int = 20000
    ) -> tuple[str, dict[str, Any]]:
        """A book's extracted prose, truncated honestly at ``max_chars``.

        The bytes come back through ``/providers/fetch`` because the shelf's
        synthetic ``.txt`` path *is* the file and its bytes are its text; a
        PDF-only book has no text there, which is reported as the refusal it
        is rather than as an empty book.
        """
        row = await self.get_book(book_id=book_id, path=path)
        root = await self._library_path()
        url = f"{self._storage_endpoint()}/providers/fetch"
        resp = await http_client.request(
            "POST",
            url,
            json={
                "provider": self._provider(root),
                "path": row["path"],
                "max_bytes": FETCH_MAX_BYTES,
            },
            headers=self._headers(),
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        status = int(resp.get("status_code") or 0)
        if status in (413, 502):
            raise CalibreLibraryError(_fetch_refusal(status, resp, row))
        body = _decode(resp, url)
        encoded = body.get("content_b64")
        if not isinstance(encoded, str) or not encoded:
            raise CalibreUnavailable(
                f"{url} returned no content for {row['path']!r}, so nothing "
                "to read."
            )
        try:
            payload = base64.b64decode(encoded, validate=True)
            text = payload.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise CalibreUnavailable(
                f"{row['path']!r} came back as something that is not UTF-8 "
                f"text: {exc}"
            ) from exc
        total = len(text)
        bound = max(int(max_chars), 1)
        if total > bound:
            text = (
                text[:bound]
                + f"\n\n[Truncated: showing the first {bound:,} of "
                + f"{total:,} characters. For a passage beyond this point, "
                + "search the library (RAG collection calibre_files) instead.]"
            )
        detail = dict(row)
        detail["total_chars"] = total
        detail["truncated"] = total > bound
        return text, detail


def _fetch_refusal(status: int, resp: dict[str, Any], row: dict[str, Any]) -> str:
    """Turn storage's byte-fetch refusals into the book's actual problem."""
    if status == 502:
        formats = str(row.get("formats") or "no known format")
        return (
            f"{row.get('title') or row.get('path')} has no text to fetch: it "
            f"is stored only as {formats}, which has no extractor here. It "
            "still appears in list and search."
        )
    return (
        f"{row.get('title') or row.get('path')} is too large for one fetch "
        f"({FETCH_MAX_BYTES:,} byte ceiling). Search the library (RAG "
        "collection calibre_files) for a passage instead."
    )


def _decode(resp: dict[str, Any], url: str) -> dict[str, Any]:
    status = int(resp.get("status_code") or 0)
    text = resp.get("text") or ""
    if status >= 400:
        detail = text.strip()
        try:
            parsed = json.loads(text)
            detail = str(parsed.get("detail") or detail)
        except ValueError:
            pass
        raise CalibreUnavailable(
            f"{url} answered {status}" + (f": {detail[:400]}" if detail else ".")
        )
    try:
        body = json.loads(text)
    except ValueError as exc:
        raise CalibreUnavailable(f"{url} did not answer with JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise CalibreUnavailable(f"{url} answered with {type(body).__name__}, not an object.")
    return body


def _book_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one storage entry into the row a model can read."""
    meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return {
        "book_id": meta.get("calibre_id"),
        "title": meta.get("title") or entry.get("name"),
        "author": meta.get("author"),
        "authors": meta.get("authors"),
        "tags": meta.get("tags"),
        "series": meta.get("series"),
        "formats": meta.get("formats"),
        "published": meta.get("published"),
        "path": entry.get("path"),
        "indexed": bool(entry.get("indexed")),
    }
