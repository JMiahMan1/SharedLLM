# services/storage/providers_impl/calibre.py
import logging
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from services.common.epub_text import EpubTextError, read_chapters
from services.storage.models import ContentSection, StorageEntry
from services.storage.nextcloud_client import NextCloudClient
from services.storage.providers import StorageProvider

log = logging.getLogger("storage.calibre")

# Calibre's metadata.db is a private, undocumented schema that has changed shape
# across releases. Rather than hard-coding a column list that breaks on the next
# Calibre upgrade, every query is built from the columns actually present.
_BOOK_COLUMNS = {
    "id": int,
    "title": str,
    "sort": str,
    "path": str,
    "author": str,
    "series": str,
    "series_index": float,
    "pubdate": str,
    "timestamp": str,
}
_FALLBACK_FORMAT_ORDER = ("EPUB", "AZW3", "MOBI", "FB2", "DOCX", "PDF", "TXT", "HTMLZ", "RTF")
# Formats read_chapters cannot open. Their books stay in the index as metadata
# so they remain discoverable, but their text is never fabricated.
_TEXT_FORMATS = {"EPUB", "AZW3", "MOBI", "FB2"}
_BOOK_ID_IN_PATH = re.compile(r"\((?P<book_id>\d+)\)\.txt$")


class CalibreLibraryError(RuntimeError):
    """The Calibre library could not be read. Never a reason to index nothing."""


class CalibreStorageProvider(StorageProvider):
    """Read a Calibre library over WebDAV and present each book as a document.

    A Calibre library is a folder holding ``metadata.db`` plus one directory per
    book, so it is a perfectly ordinary storage provider -- and because
    ``collection_name`` is derived from the provider kind, everything indexed
    through here lands in its own ``calibre_files`` collection with no changes
    to the RAG service.

    The library is exposed as one ``.txt`` entry per book at
    ``<library>/<author>/<title> (<id>).txt``. That path is a locator, not a real
    file: Calibre stores the book as ``<title> - <author>.epub`` inside
    ``<author>/<title> (<id>)/``. Synthesising a path buys three things -- the
    extension tells the indexer the entry is readable text, the author and title
    are already in the path so the index reads like a shelf, and the numeric id
    lets ``get_content`` find the book again on a later request without holding
    any state.
    """

    def __init__(self, settings: dict[str, Any], download_timeout: float = 120.0):
        self.client = NextCloudClient(settings["url"], settings["username"], settings["password"])
        library_path = str(settings["library_path"])
        self.library_path = "/" + library_path.strip("/")
        self.download_timeout = float(settings.get("download_timeout", download_timeout))
        self._catalog: dict[int, dict[str, Any]] = {}
        self._catalog_digest: str | None = None

    @property
    def metadata_db_path(self) -> str:
        return f"{self.library_path}/metadata.db"

    async def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        catalog = await self._load_catalog(refresh=True)
        root = "/" + str(path).strip("/")
        if root == "/":
            root = self.library_path

        entries: list[StorageEntry] = []
        for book in catalog.values():
            book_root = f"{self.library_path}/{book['path']}".rstrip("/")
            if root != self.library_path and not book_root.startswith(root + "/"):
                continue
            entries.append(
                StorageEntry(
                    path=_shelf_path(book_root, book),
                    name=f"{book['title']} ({book['id']}).txt",
                    is_dir=False,
                    content_type="text/plain; charset=utf-8",
                    metadata=book["metadata"],
                )
            )
        entries.sort(key=lambda e: e.path.lower())
        return entries

    async def get_sections(self, path: str) -> list[ContentSection] | None:
        book = await self._resolve_book(path)
        if book is None:
            return None
        sections = await self._read_sections(book)
        if sections is None:
            return None
        return [
            ContentSection(ordinal=chapter.ordinal, label=chapter.label, text=chapter.text)
            for chapter in sections
        ]

    async def get_content(self, path: str) -> str | None:
        book = await self._resolve_book(path)
        if book is None:
            return None
        sections = await self._read_sections(book)
        if not sections:
            return None
        return "\n\n".join(chapter.text for chapter in sections if chapter.text)

    async def write_content(
        self,
        path: str,
        content: str | bytes,
        create_parents: bool = True,
        verify: bool = True,
        is_binary: bool = False,
    ) -> dict[str, Any]:
        """Refuse writes. Mutating a library is Phase 2 and needs an owner.

        Returning a failure dict rather than raising keeps the caller contract,
        but the message is explicit: silently accepting a write into a library
        whose ``metadata.db`` is owned elsewhere would corrupt it.
        """
        return {
            "status": "FAILURE",
            "message": (
                "The Calibre provider is read-only. A library is written through "
                "Calibre itself (calibredb add / Calibre-Web), which owns "
                "metadata.db. Writing a file here would not register the book."
            ),
            "path": path,
        }

    async def close(self) -> None:
        await self.client.close()

    async def _resolve_book(self, path: str) -> dict[str, Any] | None:
        """Map a synthetic ``.txt`` path back to its catalogue row."""
        match = _BOOK_ID_IN_PATH.search(str(path))
        if match is None:
            log.warning("Calibre path carries no book id, so it is not a book locator: %s", path)
            return None
        catalog = await self._load_catalog()
        return catalog.get(int(match.group("book_id")))

    async def _read_sections(self, book: dict[str, Any]) -> list | None:
        if not book["text_format"]:
            log.warning(
                "Book %s (%s) is stored only as %s, which has no text extractor here. "
                "It is indexed as metadata only; add a converter to index its body.",
                book["title"],
                book["id"],
                ", ".join(book["formats"]) or "no format",
            )
            return None
        remote = book["text_path"]
        data = await self.client.get_file_bytes(remote, timeout=self.download_timeout)
        if not data:
            log.warning("Calibre could not fetch %s for book %s (%s)", remote, book["title"], book["id"])
            return None
        try:
            return read_chapters(data)
        except EpubTextError as exc:
            log.warning("Could not read %s for book %s (%s): %s", remote, book["title"], book["id"], exc)
            return None

    async def _load_catalog(self, refresh: bool = False) -> dict[int, dict[str, Any]]:
        """Fetch and parse ``metadata.db``, at most once per index run.

        The catalogue is only re-downloaded when ``refresh`` is set, which only
        ``list_entries`` does -- that is the call that must notice a newly added
        book. ``get_content`` reuses whatever is loaded, because an index run
        calls it once per book and re-fetching a multi-megabyte database for each
        of three thousand books would transfer gigabytes to prove nothing had
        changed.
        """
        if self._catalog and not refresh:
            return self._catalog
        data = await self.client.get_file_bytes(self.metadata_db_path, timeout=self.download_timeout)
        if not data:
            if self._catalog:
                return self._catalog
            raise CalibreLibraryError(
                f"Could not download the Calibre metadata database at {self.metadata_db_path}. "
                "Check the library_path setting and the Nextcloud credentials."
            )
        digest = _digest(data)
        if digest == self._catalog_digest and self._catalog:
            return self._catalog
        self._catalog = _parse_catalog(data, self.library_path)
        self._catalog_digest = digest
        log.info(
            "Calibre catalogue: %d books from %s", len(self._catalog), self.metadata_db_path
        )
        return self._catalog


def _digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _shelf_path(book_root: str, book: dict[str, Any]) -> str:
    """Place a book on the shelf beside its folder, not inside it.

    Calibre's ``books.path`` already ends in the book's own directory, so
    appending ``<title> (<id>).txt`` to it would produce a doubled name like
    ``The Faithful Promiser (617)/The Faithful Promiser (617).txt``. The book's
    directory is dropped instead, which leaves the author shelf readable and
    keeps the numeric id at the end of the path where ``_BOOK_ID_IN_PATH`` can
    find it again.
    """
    parent = book_root.rsplit("/", 1)[0] if "/" in book_root else book_root
    return f"{parent}/{book['title']} ({book['id']}).txt"


def _parse_catalog(data: bytes, library_path: str) -> dict[int, dict[str, Any]]:
    """Read Calibre's metadata.db into ``{book_id: row}``.

    The database is opened read-only against a temporary copy. Calibre's own
    database may carry a ``-wal`` sidecar that a copy does not, which is exactly
    why the copy is the right thing to parse: a fresh download is a consistent
    snapshot as far as WebDAV can offer one, and parsing it never takes a lock
    on a file two other hosts are writing.
    """
    with tempfile.TemporaryDirectory(prefix="calibre-") as tmp:
        db_path = Path(tmp) / "metadata.db"
        db_path.write_bytes(data)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return _query_catalog(conn, library_path)
        finally:
            conn.close()


def _query_catalog(conn: sqlite3.Connection, library_path: str) -> dict[int, dict[str, Any]]:
    books = _read_books(conn)
    formats = _read_formats(conn)
    for book_id, book in books.items():
        book_formats = formats.get(book_id, {})
        book["formats"] = sorted(book_formats)
        book["text_format"] = _pick_text_format(book_formats)
        book["text_path"] = _format_remote_path(library_path, book, book_formats)
        book["metadata"] = _book_metadata(book)
    return books


def _read_books(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(books)")}
    if not columns:
        raise CalibreLibraryError("metadata.db has no 'books' table, so this is not a Calibre library.")
    wanted = [name for name in _BOOK_COLUMNS if name in columns]
    sql = f"SELECT {', '.join(wanted)} FROM books"
    rows = conn.execute(sql).fetchall()

    authors_by_book = _read_link(conn, "books_authors_link", "book", "author")
    names_by_id = {
        int(row[0]): row[1]
        for row in _safe_rows(conn, "SELECT id, name FROM authors")
    }
    tags_by_book = _read_link(conn, "books_tags_link", "book", "tag")
    tag_names = {int(row[0]): row[1] for row in _safe_rows(conn, "SELECT id, name FROM tags")}

    books: dict[int, dict[str, Any]] = {}
    for row in rows:
        record = dict(zip(wanted, row))
        book_id = int(record["id"])
        path = str(record.get("path") or f"Book {book_id}")
        titles = [names_by_id.get(a) for a in authors_by_book.get(book_id, [])]
        titles = [t for t in titles if t]
        if not titles and record.get("author"):
            # Calibre keeps a display-name fallback in books.author. Losing the
            # author because a link table is empty would misattribute the book,
            # and an unlinked author is exactly what a partially rebuilt library
            # looks like.
            titles = [str(record["author"])]
        series_index = record.get("series_index")
        books[book_id] = {
            "id": book_id,
            "title": str(record.get("title") or f"Book {book_id}"),
            "sort": record.get("sort"),
            "path": path,
            "author": titles[0] if titles else "",
            "authors": titles,
            "series": record.get("series") or "",
            "series_index": series_index,
            "pubdate": record.get("pubdate") or "",
            "tags": [tag_names.get(t, "") for t in tags_by_book.get(book_id, []) if tag_names.get(t)],
        }
    return books


def _read_formats(conn: sqlite3.Connection) -> dict[int, dict[str, dict[str, Any]]]:
    """Map book id to ``{format: {"path": ..., "uncompressed_size": ...}}``.

    ``data.name`` does not include the file extension -- the file on disk is
    ``f"{name}.{format.lower()}"``. Reading ``name`` as a whole filename is the
    single easiest way to 404 on every book in the library.
    """
    formats: dict[int, dict[str, dict[str, Any]]] = {}
    for book_id, fmt, name, size in conn.execute(
        "SELECT book, format, name, uncompressed_size FROM data"
    ):
        formats.setdefault(int(book_id), {})[str(fmt).upper()] = {
            "path": f"{name}.{str(fmt).lower()}",
            "uncompressed_size": size,
        }
    return formats


def _pick_text_format(formats: dict[str, dict[str, Any]]) -> str | None:
    """Choose the format this provider can actually read, or ``None``.

    Returning a format we have no reader for -- a PDF, say -- would be a lie
    with a confusing consequence: the caller would hand PDF bytes to the EPUB
    reader and report "not a zip archive" for every one of those books, instead
    of the honest "indexed as metadata, add a converter". ``None`` routes those
    books to the metadata-only path, where they are still discoverable.
    """
    for candidate in _FALLBACK_FORMAT_ORDER:
        if candidate in formats and candidate in _TEXT_FORMATS:
            return candidate
    return None


def _format_remote_path(
    library_path: str, book: dict[str, Any], formats: dict[str, dict[str, Any]]
) -> str | None:
    fmt = book["text_format"]
    if not fmt or fmt not in formats:
        return None
    return f"{library_path}/{book['path']}/{formats[fmt]['path']}".replace("//", "/")


def _book_metadata(book: dict[str, Any]) -> dict[str, Any]:
    """Flatten a book to scalar metadata for the index and for RAG rows.

    Every value is a scalar or a list of them. ``/rag/sync/files`` coerces
    anything else to ``str()`` on the way in, so a nested dict would reach the
    index as a Python repr, and a retrieval answer citing that would be citing
    noise.
    """
    meta: dict[str, Any] = {
        "calibre_id": int(book["id"]),
        "title": str(book["title"]),
        "author": str(book["author"]),
    }
    if len(book["authors"]) > 1:
        meta["authors"] = "; ".join(book["authors"])
    if book["series"]:
        meta["series"] = str(book["series"])
        if book["series_index"] not in (None, 0, 0.0):
            meta["series_index"] = float(book["series_index"])
    if book["pubdate"]:
        meta["published"] = str(book["pubdate"])
    if book["tags"]:
        meta["tags"] = "; ".join(book["tags"])
    if book["formats"]:
        meta["formats"] = "; ".join(book["formats"])
    return meta


def _read_link(conn: sqlite3.Connection, table: str, book_col: str, other_col: str) -> dict[int, list[int]]:
    """Read a ``books_<x>_link`` table, tolerating its absence.

    Older libraries and trimmed backups do not have every link table, and a
    missing one means "this dimension is empty", not "this is not a library".
    """
    links: dict[int, list[int]] = {}
    for book_id, other in _safe_rows(conn, f"SELECT {book_col}, {other_col} FROM {table} ORDER BY {other_col}"):
        links.setdefault(int(book_id), []).append(int(other))
    return links


def _safe_rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    """Run a query that may not exist in this schema, returning nothing on failure."""
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        log.debug("Calibre schema lacks this query (%s): %s", sql, exc)
        return []