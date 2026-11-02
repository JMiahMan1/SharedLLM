"""Writing to the family's Calibre library: add a book, change its tags.

The library lives in Nextcloud (``<library>/metadata.db`` plus one folder per
book), and Calibre or Calibre-Web may be using it too. So every write is a
compare-and-swap on ``metadata.db``, the way a careful second writer has to be:

1. Download ``metadata.db`` and note its ETag.
2. Make the change in a local copy, with Calibre's own SQL functions
   (``title_sort``, ``uuid4``) registered so its triggers run as they would in
   Calibre, mark the book "dirtied" so Calibre rewrites its OPF backup, and run
   ``PRAGMA integrity_check``.
3. Upload the book's files (a new folder, so nothing existing is touched).
4. Keep the old ``metadata.db`` in ``<library parent>/.jarvis-calibre-backups``.
5. Upload the new ``metadata.db`` with ``If-Match: <ETag>``. If anything else
   wrote it meanwhile, Nextcloud answers 412: the new folder is removed and the
   whole change is made again on the fresh copy (up to three times).

What this cannot protect against is a desktop Calibre that has the library
open: it keeps its own copy in memory and will not see these changes until it
is reopened. Calibre-Web reads the file afresh and sees them at once.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import sqlite3
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

log = logging.getLogger(__name__)

#: Calibre's date for "not known".
UNDEFINED_DATE = "0101-01-01 00:00:00+00:00"
#: Folder and file names stay short enough for Calibre on Windows too.
PATH_LIMIT = 60
FORMATS = {"epub", "pdf", "mobi", "azw3", "fb2", "txt", "docx", "rtf", "cbz", "djvu"}
MAX_BOOK_BYTES = 1024 * 1024 * 1024  # a scanned reference work (a concordance) runs to hundreds of MB
ATTEMPTS = 3
BACKUPS_KEPT = 20

_ARTICLES = re.compile(r"^(a|an|the)\s+(.*)$", re.IGNORECASE)
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "ph.d."}
_UNSAFE = re.compile(r'[\\/<>:"|?*\x00-\x1f]')

#: One write at a time from this service; other writers are handled by If-Match.
_lock = asyncio.Lock()


class LibraryWriteError(Exception):
    """A write that was refused or could not be completed; the message says why."""


class LibraryChanged(Exception):
    """metadata.db changed under us (HTTP 412); the write is made again."""


# ── Calibre's naming rules ──────────────────────────────────────────────────


def title_sort(title: str | None) -> str:
    """Calibre's default: a leading article moves to the end ("Lenten Spring, The")."""
    text = (title or "").strip()
    m = _ARTICLES.match(text)
    return f"{m.group(2)}, {m.group(1)}" if m else text


def author_sort(name: str) -> str:
    """"Alexander Schmemann" sorts as "Schmemann, Alexander"."""
    parts = name.strip().split()
    if len(parts) < 2 or "," in name:
        return name.strip()
    suffix = []
    while len(parts) > 2 and parts[-1].lower() in _SUFFIXES:
        suffix.insert(0, parts.pop())
    last = parts.pop()
    return " ".join([f"{last},", *parts, *suffix])


def ascii_name(text: str, limit: int = PATH_LIMIT) -> str:
    """A folder or file name Calibre would accept on any system."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    plain = _UNSAFE.sub("_", plain).strip().strip(".")
    plain = re.sub(r"\s+", " ", plain)[:limit].rstrip(" .")
    return plain or "Unknown"


def book_folder(book_id: int, title: str, author: str) -> str:
    return f"{ascii_name(author)}/{ascii_name(title)} ({book_id})"


def book_file_name(title: str, author: str) -> str:
    return f"{ascii_name(title)} - {ascii_name(author)}"


def clean_tags(tags: list[str] | None) -> list[str]:
    """Trimmed, de-duplicated (case-insensitively, as Calibre does), commas removed."""
    out: dict[str, str] = {}
    for tag in tags or []:
        name = re.sub(r"\s+", " ", str(tag).replace(",", " ")).strip()[:100]
        if name:
            out.setdefault(name.lower(), name)
    return list(out.values())


# ── The database change itself (pure: a connection in, ids out) ────────────


def open_library_copy(path: Path) -> sqlite3.Connection:
    """A writable connection with the SQL functions Calibre's triggers call."""
    conn = sqlite3.connect(path)
    conn.create_function("title_sort", 1, title_sort)
    conn.create_function("uuid4", 0, lambda: str(uuid.uuid4()))
    conn.execute("PRAGMA foreign_keys = OFF")  # Calibre enforces links with its own triggers
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(sep=" ")


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [name]).fetchone() is not None


def _item_id(conn: sqlite3.Connection, table: str, name: str, with_sort: bool) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ? COLLATE NOCASE", [name]).fetchone()
    if row:
        return int(row[0])
    if with_sort:
        cur = conn.execute(f"INSERT INTO {table} (name, sort) VALUES (?, ?)", [name, author_sort(name) if table == "authors" else None])
    else:
        cur = conn.execute(f"INSERT INTO {table} (name) VALUES (?)", [name])
    return int(cur.lastrowid)


def _dirty(conn: sqlite3.Connection, book_id: int) -> None:
    """Ask Calibre to rewrite the book's OPF backup from the database."""
    if _has_table(conn, "metadata_dirtied"):
        conn.execute("INSERT OR IGNORE INTO metadata_dirtied (book) VALUES (?)", [book_id])


def set_book_tags(conn: sqlite3.Connection, book_id: int, add: list[str], remove: list[str]) -> list[str]:
    """Add and remove tags on one book; returns its tags after."""
    if not conn.execute("SELECT 1 FROM books WHERE id = ?", [book_id]).fetchone():
        raise LibraryWriteError(f"There is no book {book_id} in the library.")
    for name in clean_tags(remove):
        row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", [name]).fetchone()
        if row:
            conn.execute("DELETE FROM books_tags_link WHERE book = ? AND tag = ?", [book_id, row[0]])
    for name in clean_tags(add):
        tag_id = _item_id(conn, "tags", name, with_sort=False)
        conn.execute("INSERT OR IGNORE INTO books_tags_link (book, tag) VALUES (?, ?)", [book_id, tag_id])
    conn.execute("UPDATE books SET last_modified = ? WHERE id = ?", [_now(), book_id])
    _dirty(conn, book_id)
    return book_tags(conn, book_id)


def book_tags(conn: sqlite3.Connection, book_id: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT t.name FROM tags t JOIN books_tags_link l ON l.tag = t.id WHERE l.book = ? ORDER BY t.name COLLATE NOCASE",
        [book_id],
    )]


@dataclass
class NewBook:
    title: str
    authors: list[str]
    fmt: str
    size: int
    tags: list[str] = field(default_factory=list)
    series: str = ""
    series_index: float = 1.0
    comments: str = ""
    has_cover: bool = False
    publisher: str = ""
    #: "2007", "2007-03" or "2007-03-15"; Calibre stores a full timestamp.
    published: str = ""
    isbn: str = ""
    #: ISO 639 code, two letters ("en") or three ("eng").
    language: str = ""


#: The two-letter codes a book is most likely to carry, as Calibre's three-letter ones.
_LANGUAGES = {"en": "eng", "es": "spa", "fr": "fra", "de": "deu", "it": "ita", "pt": "por", "el": "ell",
              "la": "lat", "ru": "rus", "nl": "nld", "he": "heb", "ar": "ara", "zh": "zho", "ko": "kor", "ja": "jpn"}


def calibre_language(code: str) -> str:
    code = (code or "").strip().lower().split("-")[0]
    if len(code) == 3:
        return code
    return _LANGUAGES.get(code, "")


def calibre_date(value: str) -> str:
    """A published date as Calibre stores it, or its "unknown" date."""
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", (value or "").strip())
    if not m:
        return UNDEFINED_DATE
    year, month, day = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
    if not (100 <= year <= 2999 and 1 <= month <= 12 and 1 <= day <= 31):
        return UNDEFINED_DATE
    return f"{year:04d}-{month:02d}-{day:02d} 12:00:00+00:00"


def clean_isbn(value: str) -> str:
    digits = re.sub(r"[^0-9Xx]", "", value or "").upper()
    return digits if len(digits) in (10, 13) else ""


def insert_book(conn: sqlite3.Connection, book: NewBook) -> tuple[int, str, str]:
    """Register a book; returns (id, its folder, its file name without extension)."""
    title = book.title.strip() or "Unknown"
    authors = [a.strip() for a in book.authors if a.strip()] or ["Unknown"]
    now = _now()
    cur = conn.execute(
        "INSERT INTO books (title, timestamp, pubdate, series_index, author_sort, path, has_cover, last_modified) "
        "VALUES (?, ?, ?, ?, ?, '', ?, ?)",
        [title, now, calibre_date(book.published), book.series_index, " & ".join(author_sort(a) for a in authors), int(book.has_cover), now],
    )
    book_id = int(cur.lastrowid)
    folder = book_folder(book_id, title, authors[0])
    conn.execute("UPDATE books SET path = ? WHERE id = ?", [folder, book_id])
    for name in authors:
        conn.execute("INSERT OR IGNORE INTO books_authors_link (book, author) VALUES (?, ?)",
                     [book_id, _item_id(conn, "authors", name, with_sort=True)])
    for name in clean_tags(book.tags):
        conn.execute("INSERT OR IGNORE INTO books_tags_link (book, tag) VALUES (?, ?)",
                     [book_id, _item_id(conn, "tags", name, with_sort=False)])
    if book.series.strip():
        conn.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?)",
                     [book_id, _item_id(conn, "series", book.series.strip(), with_sort=True)])
    if book.comments.strip():
        conn.execute("INSERT INTO comments (book, text) VALUES (?, ?)", [book_id, book.comments.strip()])
    if book.publisher.strip() and _has_table(conn, "books_publishers_link"):
        conn.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?)",
                     [book_id, _item_id(conn, "publishers", book.publisher.strip()[:200], with_sort=True)])
    if (isbn := clean_isbn(book.isbn)) and _has_table(conn, "identifiers"):
        conn.execute("INSERT OR REPLACE INTO identifiers (book, type, val) VALUES (?, 'isbn', ?)", [book_id, isbn])
    if (lang := calibre_language(book.language)) and _has_table(conn, "books_languages_link"):
        row = conn.execute("SELECT id FROM languages WHERE lang_code = ?", [lang]).fetchone()
        lang_id = int(row[0]) if row else int(conn.execute("INSERT INTO languages (lang_code) VALUES (?)", [lang]).lastrowid)
        conn.execute("INSERT OR IGNORE INTO books_languages_link (book, lang_code, item_order) VALUES (?, ?, 0)", [book_id, lang_id])
    name = book_file_name(title, authors[0])
    conn.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?, ?, ?, ?)",
                 [book_id, book.fmt.upper(), book.size, name])
    _dirty(conn, book_id)
    return book_id, folder, name


def check(conn: sqlite3.Connection) -> None:
    result = conn.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise LibraryWriteError(f"The changed library failed its integrity check ({result}); nothing was saved.")


# ── What an EPUB says about itself ──────────────────────────────────────────

_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "opf": "http://www.idpf.org/2007/opf",
       "dc": "http://purl.org/dc/elements/1.1/"}


def epub_details(data: bytes) -> dict[str, Any]:
    """Title, authors, subjects, description and cover image of an EPUB (any may be missing)."""
    out: dict[str, Any] = {"title": "", "authors": [], "tags": [], "comments": "", "cover": None,
                           "publisher": "", "published": "", "isbn": "", "language": ""}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            container = ElementTree.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find(".//c:rootfile", _NS)
            opf_path = rootfile.get("full-path") if rootfile is not None else None
            if not opf_path:
                return out
            opf = ElementTree.fromstring(zf.read(opf_path))
            meta = opf.find("opf:metadata", _NS)
            if meta is not None:
                out["title"] = (meta.findtext("dc:title", default="", namespaces=_NS) or "").strip()
                out["authors"] = [e.text.strip() for e in meta.findall("dc:creator", _NS) if e.text and e.text.strip()]
                out["tags"] = [e.text.strip() for e in meta.findall("dc:subject", _NS) if e.text and e.text.strip()]
                desc = meta.findtext("dc:description", default="", namespaces=_NS) or ""
                out["comments"] = desc.strip()
                out["publisher"] = (meta.findtext("dc:publisher", default="", namespaces=_NS) or "").strip()
                out["published"] = (meta.findtext("dc:date", default="", namespaces=_NS) or "").strip()[:10]
                out["language"] = (meta.findtext("dc:language", default="", namespaces=_NS) or "").strip()
                for ident in meta.findall("dc:identifier", _NS):
                    if isbn := clean_isbn((ident.text or "").replace("urn:isbn:", "")):
                        out["isbn"] = isbn
                        break
            manifest = opf.find("opf:manifest", _NS)
            href = None
            if manifest is not None:
                items = manifest.findall("opf:item", _NS)
                cover_id = None
                if meta is not None:
                    for m in meta.findall("opf:meta", _NS):
                        if m.get("name") == "cover":
                            cover_id = m.get("content")
                for item in items:
                    named = "cover-image" in (item.get("properties") or "") or (cover_id and item.get("id") == cover_id)
                    if named and str(item.get("media-type") or "").startswith("image/"):
                        href = item.get("href")
                        break
            if href:
                base = PurePosixPath(opf_path).parent
                target = str(base / href) if str(base) != "." else href
                out["cover"] = zf.read(target)
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, OSError) as exc:
        log.info("[calibre] could not read EPUB details: %s", exc)
    return out


# ── The compare-and-swap against Nextcloud ──────────────────────────────────


class LibraryWriter:
    """One library in Nextcloud, written the safe way (see the module doc)."""

    def __init__(self, client, library_path: str):
        self.client = client
        self.library = "/" + library_path.strip("/")

    @property
    def db_path(self) -> str:
        return f"{self.library}/metadata.db"

    async def _download_db(self) -> tuple[bytes, str]:
        url = await self.client._full_url(self.db_path)
        async with self.client.client.get(url, timeout=_timeout(120)) as resp:
            if resp.status != 200:
                raise LibraryWriteError(f"Could not read {self.db_path} (HTTP {resp.status}).")
            etag = resp.headers.get("ETag") or resp.headers.get("OC-ETag")
            if not etag:
                raise LibraryWriteError("Nextcloud did not give metadata.db a version (ETag), so it cannot be changed safely.")
            return await resp.read(), etag

    async def _put(self, path: str, data: bytes, *, if_match: str | None = None) -> None:
        url = await self.client._full_url(path)
        headers = {"Content-Type": "application/octet-stream"}
        if if_match:
            headers["If-Match"] = if_match
        async with self.client.client.put(url, data=data, headers=headers, timeout=_timeout(300)) as resp:
            if resp.status == 412:
                raise LibraryChanged(path)
            if resp.status >= 400:
                raise LibraryWriteError(f"Could not write {path} (HTTP {resp.status}).")

    async def _delete(self, path: str) -> None:
        url = await self.client._full_url(path)
        try:
            async with self.client.client.request("DELETE", url, timeout=_timeout(60)) as resp:
                if resp.status >= 400 and resp.status != 404:
                    log.warning("[calibre] could not remove %s (HTTP %s)", path, resp.status)
        except Exception as exc:  # cleanup is best effort
            log.warning("[calibre] could not remove %s: %s", path, exc)

    async def _backup(self, data: bytes) -> str:
        parent = str(PurePosixPath(self.library).parent).rstrip("/")
        folder = f"{parent}/.jarvis-calibre-backups"
        await self.client.ensure_directory(folder)
        name = f"{folder}/metadata-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.db"
        await self._put(name, data)
        await self._prune_backups(folder)
        return name

    async def _prune_backups(self, folder: str) -> None:
        """Keep the newest ``BACKUPS_KEPT`` copies (their names sort by time)."""
        try:
            entries = await self.client.list_files(folder)
        except Exception:
            return
        names = sorted(
            name for e in entries
            if (name := unquote(str(e.get("href") or "")).rstrip("/").rsplit("/", 1)[-1]).startswith("metadata-")
        )
        for old in names[:-BACKUPS_KEPT]:
            await self._delete(f"{folder}/{old}")

    async def _change(self, edit, upload=None) -> dict:
        """Run ``edit(conn) -> result`` on a fresh copy and swap it in; retry on a race."""
        async with _lock:
            for attempt in range(1, ATTEMPTS + 1):
                original, etag = await self._download_db()
                with tempfile.TemporaryDirectory(prefix="calibre-write-") as tmp:
                    path = Path(tmp) / "metadata.db"
                    path.write_bytes(original)
                    conn = open_library_copy(path)
                    try:
                        result = edit(conn)
                        conn.commit()
                        check(conn)
                    except sqlite3.Error as exc:
                        raise LibraryWriteError(f"The library refused the change: {exc}") from exc
                    finally:
                        conn.close()
                    changed = path.read_bytes()
                uploaded: list[str] = []
                try:
                    if upload:
                        uploaded = await upload(result)
                    backup = await self._backup(original)
                    await self._put(self.db_path, changed, if_match=etag)
                except LibraryChanged:
                    log.info("[calibre] metadata.db changed during write (attempt %d); trying again", attempt)
                    for folder in uploaded:
                        await self._delete(folder)
                    continue
                except Exception:
                    for folder in uploaded:
                        await self._delete(folder)
                    raise
                return {**result, "backup": backup, "attempts": attempt}
        raise LibraryWriteError("The library kept changing while Jarvis tried to write it. Try again in a minute.")

    async def set_tags(self, book_id: int, add: list[str], remove: list[str]) -> dict:
        def edit(conn):
            return {"book_id": book_id, "tags": set_book_tags(conn, book_id, add, remove)}
        return await self._change(edit)

    async def add_book(self, data: bytes, filename: str, *, title: str = "", authors: list[str] | None = None,
                       tags: list[str] | None = None, series: str = "", comments: str = "", publisher: str = "",
                       published: str = "", isbn: str = "", language: str = "", cover: bytes | None = None) -> dict:
        fmt = PurePosixPath(filename).suffix.lower().lstrip(".")
        if fmt not in FORMATS:
            raise LibraryWriteError(f"{filename} is not a book format Calibre keeps ({', '.join(sorted(FORMATS))}).")
        if not data:
            raise LibraryWriteError("The book file is empty.")
        if len(data) > MAX_BOOK_BYTES:
            raise LibraryWriteError(f"The book is larger than {MAX_BOOK_BYTES // (1024 * 1024)} MB.")
        found = epub_details(data) if fmt == "epub" else {"title": "", "authors": [], "tags": [], "comments": "", "cover": None,
                                                           "publisher": "", "published": "", "isbn": "", "language": ""}
        if cover:
            found["cover"] = cover
        book = NewBook(
            title=(title or found["title"] or PurePosixPath(filename).stem).strip(),
            authors=[a for a in (authors or found["authors"]) if a.strip()] or ["Unknown"],
            fmt=fmt,
            size=len(data),
            tags=clean_tags([*(tags or []), *([] if tags else found["tags"])]),
            series=series,
            comments=comments or found["comments"],
            has_cover=bool(found["cover"]),
            publisher=publisher or found["publisher"],
            published=published or found["published"],
            isbn=isbn or found["isbn"],
            language=language or found["language"],
        )

        def edit(conn):
            book_id, folder, name = insert_book(conn, book)
            return {"book_id": book_id, "path": folder, "file": f"{name}.{fmt}", "title": book.title,
                    "authors": book.authors, "tags": book_tags(conn, book_id)}

        async def upload(result):
            # A new folder of its own: only it is removed if the swap fails,
            # never the author's folder, which may hold other books.
            folder = f"{self.library}/{result['path']}"
            await self.client.ensure_directory(folder)
            await self._put(f"{folder}/{result['file']}", data)
            if found["cover"]:
                await self._put(f"{folder}/cover.jpg", found["cover"])
            return [folder]

        return await self._change(edit, upload)


def _timeout(seconds: float):
    import aiohttp

    return aiohttp.ClientTimeout(total=seconds, sock_read=seconds)
