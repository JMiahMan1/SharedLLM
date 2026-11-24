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
import contextlib
import json
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from services.execution import http_client

log = logging.getLogger("execution.calibre")

LIST_TIMEOUT_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 300.0
SETTING_TIMEOUT_SECONDS = 5.0

LIBRARY_SETTING = "calibre_library_path"
CACHE_TTL_SECONDS = 300.0

FETCH_MAX_BYTES = 4 * 1024 * 1024

#: A book matches a spoken title/author at or above this score (0..1).
MATCH_THRESHOLD = 0.7
#: Two books within this score of each other are an honest tie: ask, do not guess.
AMBIGUOUS_MARGIN = 0.05
#: With no book at the threshold, the closest is still read when it is clearly the one
#: meant (speech recognition heard "Wild and Park by John Elbridge"): it scores at least
#: this, beats the next book by ``CLOSEST_MARGIN``, and its author (similarity ratio at
#: least ``CLOSEST_AUTHOR_RATIO``) or its title (``CLOSEST_TITLE_RATIO``) matches what was
#: asked. The answer then says which book was read.
CLOSEST_MIN_SCORE = 0.5
CLOSEST_MARGIN = 0.15
CLOSEST_AUTHOR_RATIO = 0.8
CLOSEST_TITLE_RATIO = 0.7
#: Words that carry no identity in a title or a request.
_STOPWORDS = frozenset({"the", "a", "an", "at", "of", "by", "and", "in", "on", "to", "for", "book", "books"})

_META_KEYS = ("title", "author", "authors", "tags", "series", "formats", "published")


class CalibreLibraryError(ValueError):
    """The caller asked for something the shelf cannot answer (refused)."""


class CalibreAmbiguous(CalibreLibraryError):
    """More than one book matches about equally; the caller must choose."""

    def __init__(self, message: str, candidates: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.candidates = candidates


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

    async def _library(self, route: str, body: dict[str, Any], timeout: float = LIST_TIMEOUT_SECONDS) -> dict[str, Any]:
        """One call to storage's librarian routes (``/calibre/...``)."""
        url = f"{self._storage_endpoint()}/calibre/{route}"
        resp = await http_client.request(
            "POST", url, json={"library_path": await self._library_path(), **body},
            headers=self._headers(), timeout=timeout,
        )
        return _decode(resp, url)

    async def tags(self) -> list[dict[str, Any]]:
        """Every tag on the shelf with how many books carry it."""
        return list((await self._library("tags", {})).get("tags") or [])

    async def books(self, *, tag: str = "", query: str = "", untagged: bool = False, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
        body = await self._library("books", {"tag": tag, "q": query, "untagged": untagged, "limit": limit})
        return list(body.get("books") or []), int(body.get("total") or 0)

    async def audit(self) -> dict[str, Any]:
        return await self._library("audit", {}, timeout=FETCH_TIMEOUT_SECONDS)

    async def set_tags(self, *, book_id: int, add: list[str], remove: list[str]) -> dict[str, Any]:
        """Add and remove tags on one book (storage writes metadata.db safely)."""
        out = await self._library(f"books/{int(book_id)}/tags", {"add": add, "remove": remove}, timeout=FETCH_TIMEOUT_SECONDS)
        clear_cache()
        return out

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

    async def resolve_book(
        self, *, book_id: Any = None, path: str | None = None, query: str | None = None
    ) -> dict[str, Any]:
        """One book by id, shelf path, or a spoken title and/or author.

        A query is matched loosely (see :func:`match_books`) so one call can go
        from "chapter 4 of Wild Heart by John Eldridge" to the book. A tie is
        refused with both candidates, and no match names the closest titles.
        """
        if (book_id is not None and str(book_id).strip()) or (path and str(path).strip()):
            return await self.get_book(book_id=book_id, path=path)
        if not str(query or "").strip():
            raise CalibreLibraryError(
                "Name the book: give book_id, or query with its title and/or author."
            )
        rows = [_book_row(entry) for entry in await self._entries()]
        return pick_book(rows, str(query))

    async def chapters(
        self,
        *,
        book_id: Any = None,
        path: str | None = None,
        query: str | None = None,
        chapter: str = "",
        part: int = 1,
        max_chars: int = 60000,
    ) -> dict[str, Any]:
        """The book's chapter list and, when ``chapter`` is named, that chapter (one part)."""
        started = time.perf_counter()
        row = await self.resolve_book(book_id=book_id, path=path, query=query)
        resolved_s = time.perf_counter() - started
        try:
            ident = int(str(row.get("book_id")))
        except ValueError as exc:
            raise CalibreLibraryError(f"{row.get('title')} has no Calibre id to read chapters by.") from exc
        url = f"{self._storage_endpoint()}/calibre/books/{ident}/chapters"
        resp = await http_client.request(
            "POST",
            url,
            json={
                "library_path": await self._library_path(),
                "chapter": str(chapter or ""),
                "part": max(int(part), 1),
                "max_chars": max(int(max_chars), 500),
            },
            headers=self._headers(),
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        log.info("[calibre] book %s chapter %r: resolved in %.2f s, storage answered %s in %.2f s",
                 ident, chapter, resolved_s, resp.get("status_code"), time.perf_counter() - started - resolved_s)
        status = int(resp.get("status_code") or 0)
        if status in (404, 422):
            detail = (resp.get("text") or "").strip()
            with contextlib.suppress(ValueError, AttributeError):
                detail = str(json.loads(detail).get("detail") or detail)
            raise CalibreLibraryError(detail or f"{row.get('title')}: the chapter could not be read.")
        body = _decode(resp, url)
        body["row"] = row
        return body

    async def fetch_text(
        self, *, book_id: Any = None, path: str | None = None, max_chars: int = 20000
    ) -> tuple[str, dict[str, Any]]:
        """A book's extracted prose, truncated honestly at ``max_chars``.

        The bytes come back through ``/providers/fetch`` because the shelf's
        synthetic ``.txt`` path *is* the file and its bytes are its text. A
        PDF-only book's shelf text is its PDF's pages. When the shelf has no text
        (a scan, a format storage cannot read) or more than one fetch carries,
        storage reads the book's own file (``/calibre/books/{id}/text``) and cuts
        it at ``max_chars``; only a book with no readable file at all is refused.
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
            text = await self._file_text(row, max_chars)
            if text is None:
                raise CalibreLibraryError(_fetch_refusal(status, resp, row))
            return text
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
        return _bounded_text(text, len(text), max_chars, row)

    async def _file_text(self, row: dict[str, Any], max_chars: int) -> tuple[str, dict[str, Any]] | None:
        """A book's text read by storage from its own file (EPUB, PDF or TXT), cut at
        ``max_chars``; ``None`` when storage has no reader for any of its files."""
        try:
            ident = int(str(row.get("book_id")))
        except ValueError:
            return None
        url = f"{self._storage_endpoint()}/calibre/books/{ident}/text"
        bound = max(int(max_chars), 500)
        resp = await http_client.request(
            "POST",
            url,
            json={"library_path": await self._library_path(), "max_chars": bound},
            headers=self._headers(),
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        status = int(resp.get("status_code") or 0)
        if status in (404, 422):
            detail = (resp.get("text") or "").strip()
            with contextlib.suppress(ValueError, AttributeError):
                detail = str(json.loads(detail).get("detail") or detail)
            if "no chapter reader" in detail or "no text reader" in detail.lower():
                return None
            raise CalibreLibraryError(detail or f"{row.get('title')}: its text could not be read.")
        body = _decode(resp, url)
        text = str(body.get("text") or "")
        total = int(body.get("total_chars") or len(text))
        return _bounded_text(text, total, bound, row)


def _bounded_text(text: str, total: int, max_chars: int, row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``text`` cut at ``max_chars`` with an honest note, and the row with its size."""
    bound = max(int(max_chars), 1)
    if total > bound:
        text = (
            text[:bound]
            + f"\n\n[Truncated: showing the first {bound:,} of "
            + f"{total:,} characters. For a passage beyond this point, read one "
            + "chapter (fetch_text with chapter) or search the library (RAG "
            + "collection calibre_files) instead.]"
        )
    detail = dict(row)
    detail["total_chars"] = total
    detail["truncated"] = total > bound
    return text, detail


def _norm_words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    folded = re.sub(r"[^a-z0-9\s]", " ", folded.replace("'", ""))
    return [w for w in folded.split() if w not in _STOPWORDS]


def _closeness(word: str, words: list[str]) -> float:
    """How well ``word`` matches the nearest of ``words``; misheard names still score."""
    best = 0.0
    for other in words:
        if word == other:
            return 1.0
        ratio = SequenceMatcher(None, word, other).ratio()
        best = max(best, ratio)
    return best if best >= 0.7 else 0.0


def book_score(row: dict[str, Any], query: str) -> float:
    """How well a shelf row answers a spoken title and/or author (0..1).

    Mostly: how many of the words said are in the title or author, each word
    allowed the small misspellings speech recognition makes (Eldridge for
    Eldredge). A little: how much of the title was said, so the exact title
    beats a longer one that merely contains it.
    """
    said = _norm_words(query)
    if not said:
        return 0.0
    title = _norm_words(str(row.get("title") or ""))
    authors = row.get("authors") if isinstance(row.get("authors"), list) else [row.get("authors") or ""]
    author = _norm_words(" ".join([str(row.get("author") or ""), *[str(a) for a in authors]]))
    words = title + author
    if not words:
        return 0.0
    coverage = sum(_closeness(w, words) for w in said) / len(said)
    title_said = sum(_closeness(w, said) for w in title) / len(title) if title else 0.0
    return round(0.85 * coverage + 0.15 * title_said, 4)


def _same_book(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _norm_words(str(a.get("title") or "")) == _norm_words(str(b.get("title") or "")) and _norm_words(
        str(a.get("author") or a.get("authors") or "")
    ) == _norm_words(str(b.get("author") or b.get("authors") or ""))


def match_books(rows: list[dict[str, Any]], query: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(matches, closest)``: the books tied for best at or above the threshold, else none,
    and the two closest titles either way. Copies of one book count as one match."""
    scored = sorted(
        ((book_score(row, query), str(row.get("title") or "").lower(), i, row) for i, row in enumerate(rows)),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    closest = [row for _s, _t, _i, row in scored[:2]]
    if not scored or scored[0][0] < MATCH_THRESHOLD:
        return [], closest
    top = scored[0][0]
    matches: list[dict[str, Any]] = []
    for score, _title, _i, row in scored:
        if score < top - AMBIGUOUS_MARGIN:
            break
        if not any(_same_book(row, kept) for kept in matches):
            matches.append(row)
    return matches, closest


def _phrase_ratio(asked: str, actual: str) -> float:
    a, b = " ".join(_norm_words(asked)), " ".join(_norm_words(actual))
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _row_authors(row: dict[str, Any]) -> list[str]:
    authors = row.get("authors") if isinstance(row.get("authors"), list) else [row.get("authors") or ""]
    return [str(a) for a in [row.get("author"), *authors] if str(a or "").strip()]


def clear_closest(rows: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """The closest book when it is clearly the one meant, else ``None`` (see ``CLOSEST_*``).

    Two books close to each other are never chosen between: that stays a question.
    """
    scored = sorted(((book_score(row, query), i, row) for i, row in enumerate(rows)), key=lambda t: (-t[0], t[1]))
    if not scored or scored[0][0] < CLOSEST_MIN_SCORE:
        return None
    top, _i, best = scored[0]
    second = next((score for score, _j, row in scored[1:] if not _same_book(row, best)), 0.0)
    if top - second < CLOSEST_MARGIN:
        return None
    asked_title, by, asked_author = str(query).rpartition(" by ")
    if not by:
        asked_title, asked_author = str(query), ""
    author_ratio = max((_phrase_ratio(asked_author, a) for a in _row_authors(best)), default=0.0) if asked_author else 0.0
    title_ratio = _phrase_ratio(asked_title, str(best.get("title") or ""))
    if author_ratio >= CLOSEST_AUTHOR_RATIO or title_ratio >= CLOSEST_TITLE_RATIO:
        log.info("[calibre] %r read as the closest book %s (score %.2f, next %.2f, author %.2f, title %.2f)",
                 query, _named(best), top, second, author_ratio, title_ratio)
        return best
    return None


def _named(row: dict[str, Any]) -> str:
    author = str(row.get("author") or row.get("authors") or "").strip() or "unknown author"
    return f"{row.get('title') or 'Untitled'} by {author} (id {row.get('book_id')})"


def pick_book(rows: list[dict[str, Any]], query: str) -> dict[str, Any]:
    """The one book ``query`` names, or a refusal that says what to ask."""
    if not rows:
        raise CalibreLibraryError("The library is empty, so there is no book to read.")
    matches, closest = match_books(rows, query)
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = "; ".join(_named(row) for row in matches[:5])
        raise CalibreAmbiguous(
            f"More than one book matches {query!r}: {names}. Ask which one, then call again "
            "with its book_id.",
            matches[:5],
        )
    nearest = clear_closest(rows, query)
    if nearest is not None:
        return {**nearest, "asked_for": str(query).strip()}
    near = "; ".join(_named(row) for row in closest)
    raise CalibreLibraryError(
        f"The library has no book matching {query!r}." + (f" The closest titles are: {near}." if near else "")
    )


def _fetch_refusal(status: int, resp: dict[str, Any], row: dict[str, Any]) -> str:
    """Turn storage's byte-fetch refusals into the book's actual problem."""
    if status == 502:
        formats = str(row.get("formats") or "no known format")
        return (
            f"{row.get('title') or row.get('path')} has no text to fetch: it "
            f"is stored only as {formats}, and no text could be read from it. It "
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
