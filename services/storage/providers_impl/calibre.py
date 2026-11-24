# services/storage/providers_impl/calibre.py
import asyncio
import logging
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from services.common.epub_text import EpubTextError, markup_text, read_chapters
from services.storage.models import ContentSection, StorageEntry
from services.storage.nextcloud_client import NextCloudClient
from services.storage.pdf_index import default_max_chars, pdf_index_sections
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
    "last_modified": str,
}
_FALLBACK_FORMAT_ORDER = ("EPUB", "AZW3", "MOBI", "FB2", "DOCX", "PDF", "TXT", "HTMLZ", "RTF")
# Formats this provider reads text from: the e-book formats through read_chapters,
# and PDF through pdf_index (its bookmarks, else its pages). A book in none of
# them stays in the index as metadata so it remains discoverable, but its text is
# never fabricated. EPUB still wins when a book has both, because the order above
# puts it first.
_TEXT_FORMATS = {"EPUB", "AZW3", "MOBI", "FB2", "PDF"}
_BOOK_ID_IN_PATH = re.compile(r"\((?P<book_id>\d+)\)\.txt$")

#: Bumped when what the catalogue card holds changes, so every book's card is
#: written again once (it is part of each entry's ``mtime`` stamp).
CATALOGUE_CARD_VERSION = "catalogue-1"
#: Characters of a book's description (Calibre's comments) kept on its card.
_DESCRIPTION_MAX_CHARS = 1500
#: Chapter titles listed on a card; a longer contents ends with "and N more".
_CONTENTS_MAX_TITLES = 60


class CalibreLibraryError(RuntimeError):
    """The Calibre library could not be read. Never a reason to index nothing."""


class CalibreStorageProvider(StorageProvider):
    """Read a Calibre library over WebDAV and present each book as a document.

    The search index holds each book's catalogue card only (``catalogue_card``:
    title, author, series, tags, description and chapter titles), never its
    text: embedding whole books on the household server's CPU took about 1.6 s
    per passage, which for a library of three thousand books is weeks of work.
    A book's text is read on demand (``library_api`` chapter and text routes).

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

    #: The indexer stores this provider's catalogue cards, not the books' text.
    index_full_text = False

    def __init__(self, settings: dict[str, Any], download_timeout: float = 120.0):
        self.client = NextCloudClient(settings["url"], settings["username"], settings["password"])
        library_path = str(settings["library_path"])
        self.library_path = "/" + library_path.strip("/")
        self.download_timeout = float(settings.get("download_timeout", download_timeout))
        self.pdf_max_chars = int(settings.get("pdf_max_chars") or default_max_chars())
        self._catalog: dict[int, dict[str, Any]] = {}
        self._catalog_digest: str | None = None
        # PDF books already found to have no text (a scan) in this catalogue, so
        # the indexer's get_content fallback does not download and parse them twice.
        self._no_text: set[int] = set()

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
                    # Calibre's last_modified changes with any edit to the book or its
                    # metadata: with the card version it is what the index checkpoint
                    # compares, so an unchanged book is not read again.
                    mtime=f"{book.get('last_modified') or book['id']}|{CATALOGUE_CARD_VERSION}",
                    metadata=book["metadata"],
                )
            )
        entries.sort(key=lambda e: e.path.lower())
        return entries

    async def list_book_files(self) -> list[StorageEntry]:
        """Every book file in the library, at its real path, from ``metadata.db``.

        One download of the catalogue (skipped when its checksum is unchanged)
        instead of a WebDAV walk of every author and book folder, which on a
        library of thousands took minutes.
        """
        catalog = await self._load_catalog(refresh=True)
        entries = [
            StorageEntry(path=f["path"], name=f["path"].rsplit("/", 1)[-1], is_dir=False, size=f.get("size"))
            for book in catalog.values()
            for f in book.get("files", [])
        ]
        entries.sort(key=lambda e: e.path.lower())
        return entries

    async def catalogue_card(self, path: str) -> str | None:
        """The book's catalogue card: what the search index holds for it.

        Title, author, series, tags, publisher, date, description and the titles
        of its chapters, so a book is found by its name, its author or its
        subject. The chapter titles come from the file's own contents (an EPUB's
        nav or NCX, a PDF's bookmarks), never from its text, so reading them
        costs one download and no page extraction; a book whose contents cannot
        be read still gets a card without them.
        """
        book = await self._resolve_book(path)
        if book is None:
            return None
        lines = [f"Book: {book['title']}"]
        if book["authors"]:
            lines.append(f"Author: {'; '.join(book['authors'])}")
        if book["series"]:
            index = book.get("series_index")
            number = f" (book {index:g})" if isinstance(index, (int, float)) and index else ""
            lines.append(f"Series: {book['series']}{number}")
        if book["tags"]:
            lines.append(f"Tags: {'; '.join(book['tags'])}")
        if book.get("publisher"):
            lines.append(f"Publisher: {book['publisher']}")
        if book["pubdate"] and not str(book["pubdate"]).startswith("0101"):
            lines.append(f"Published: {str(book['pubdate'])[:10]}")
        if book["formats"]:
            lines.append(f"Formats: {', '.join(book['formats'])}")
        if book.get("description"):
            lines.append(f"Description: {book['description']}")
        titles = await self._contents_titles(book)
        if titles:
            shown = titles[:_CONTENTS_MAX_TITLES]
            more = len(titles) - len(shown)
            lines.append("Contents: " + "; ".join(shown) + (f"; and {more} more" if more > 0 else ""))
        return "\n".join(lines)

    async def _contents_titles(self, book: dict[str, Any]) -> list[str]:
        if not book["text_format"] or not book["text_path"]:
            return []
        try:
            data = await self.client.get_file_bytes(book["text_path"], timeout=self.download_timeout)
        except Exception as exc:
            log.warning("Calibre could not fetch %s for its contents: %s", book["text_path"], exc)
            return []
        if not data:
            return []
        from services.storage.book_chapters import contents_titles

        return await asyncio.to_thread(contents_titles, data, book["text_format"])

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

    async def get_bytes(self, path: str) -> bytes | None:
        """The synthetic ``.txt`` file's bytes, which are its UTF-8 text.

        The shelf entry *is* a ``.txt`` document, so its raw bytes are the
        book's extracted prose -- that is what makes ``/providers/fetch`` a
        plain byte fetch for callers like Raven's ``fetch_text`` without a
        second content route. A PDF-only book gives its pages' text. A book
        with no text to give (a scan with no text layer, a format with no
        reader) returns ``None``, which keeps storage's honest "cannot read as
        bytes" refusal instead of an empty file.
        """
        text = await self.get_content(path)
        if text is None:
            return None
        return text.encode("utf-8")

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
        if int(book["id"]) in self._no_text:
            return None
        remote = book["text_path"]
        data = await self.client.get_file_bytes(remote, timeout=self.download_timeout)
        if not data:
            log.warning("Calibre could not fetch %s for book %s (%s)", remote, book["title"], book["id"])
            return None
        if book["text_format"] == "PDF":
            sections = await self._read_pdf_sections(book, remote, data)
            if sections is None:
                self._no_text.add(int(book["id"]))
            return sections
        try:
            return read_chapters(data)
        except EpubTextError as exc:
            log.warning("Could not read %s for book %s (%s): %s", remote, book["title"], book["id"], exc)
            return None

    async def _read_pdf_sections(self, book: dict[str, Any], remote: str, data: bytes) -> list | None:
        """A PDF book's sections: its bookmarked chapters, else its pages (``pdf_index``).

        A scan with no text layer is logged as one, by name, and keeps its
        metadata row; reading stops at ``pdf_max_chars`` of text.
        """
        return await pdf_index_sections(
            data, max_chars=self.pdf_max_chars, what=f"Book {book['title']} ({book['id']}) at {remote}"
        )

    @property
    def catalog_key(self) -> tuple[str, str, str]:
        """Which library's catalogue this is: one per Nextcloud login and metadata.db."""
        return (
            str(getattr(self.client, "base_url", "") or ""),
            str(getattr(self.client, "username", "") or ""),
            self.metadata_db_path,
        )

    async def _stat_metadata(self) -> tuple[Any, ...] | None:
        """metadata.db's ``(etag, size, mtime)``, or ``None`` when it cannot be asked."""
        stat = getattr(self.client, "stat", None)
        if not callable(stat):
            return None
        try:
            found = await stat(self.metadata_db_path)
        except Exception as exc:
            log.info("Calibre catalogue: stat of %s failed: %s", self.metadata_db_path, exc)
            return None
        if not isinstance(found, dict):
            return None
        return (found.get("etag"), found.get("size"), found.get("mtime"))

    def _adopt(self, kept: dict[str, Any]) -> dict[int, dict[str, Any]]:
        if kept["digest"] != self._catalog_digest:
            self._no_text.clear()  # a new catalogue: books found textless may have changed
        self._catalog = kept["catalog"]
        self._catalog_digest = kept["digest"]
        return self._catalog

    async def _load_catalog(self, refresh: bool = False) -> dict[int, dict[str, Any]]:
        """Fetch and parse ``metadata.db``, only when it changed.

        Without ``refresh`` whatever is loaded is reused: an index run calls
        ``get_content`` once per book, and re-fetching a multi-megabyte database
        for each of three thousand books would transfer gigabytes to prove nothing
        had changed. With ``refresh`` (``list_entries``, the librarian routes)
        metadata.db is checked, not downloaded: a PROPFIND for its ETag, size and
        modification time, at most every ``CALIBRE_CATALOG_CHECK_S`` seconds, and
        the download and parse happen only when one of them changed. The parsed
        catalogue is kept per library (``_CATALOGS``), so a provider built after
        another expired does not download it again either. A client that cannot
        stat (or a stat that fails) downloads every time, as before, and the
        digest still spares the parse.
        """
        if self._catalog and not refresh:
            return self._catalog
        key = self.catalog_key
        kept = _CATALOGS.get(key)
        if kept is not None and kept["signature"] is not None and \
                time.monotonic() - kept["checked_at"] < _catalog_check_s():
            return self._adopt(kept)
        started = time.perf_counter()
        signature = await self._stat_metadata()
        stat_s = time.perf_counter() - started
        if kept is not None and signature is not None and signature == kept["signature"]:
            kept["checked_at"] = time.monotonic()
            log.debug("Calibre catalogue: %s unchanged (stat %.2f s)", self.metadata_db_path, stat_s)
            return self._adopt(kept)
        data = await self.client.get_file_bytes(self.metadata_db_path, timeout=self.download_timeout)
        download_s = time.perf_counter() - started - stat_s
        if not data:
            if self._catalog:
                return self._catalog
            if kept is not None:
                return self._adopt(kept)
            raise CalibreLibraryError(
                f"Could not download the Calibre metadata database at {self.metadata_db_path}. "
                "Check the library_path setting and the Nextcloud credentials."
            )
        digest = _digest(data)
        if kept is not None and digest == kept["digest"]:
            kept.update(signature=signature, checked_at=time.monotonic())
            log.info("Calibre catalogue: %s downloaded again but unchanged (stat %.2f s, download %.2f s, %.1f MB)",
                     self.metadata_db_path, stat_s, download_s, len(data) / 1e6)
            return self._adopt(kept)
        if digest == self._catalog_digest and self._catalog:
            catalog = self._catalog
        else:
            parse_started = time.perf_counter()
            catalog = await asyncio.to_thread(_parse_catalog, data, self.library_path)
            log.info(
                "Calibre catalogue: %d books from %s (stat %.2f s, download %.2f s, %.1f MB, parse %.2f s)",
                len(catalog), self.metadata_db_path, stat_s, download_s, len(data) / 1e6,
                time.perf_counter() - parse_started,
            )
        kept = {"signature": signature, "digest": digest, "catalog": catalog, "checked_at": time.monotonic()}
        _CATALOGS[key] = kept
        return self._adopt(kept)


#: Parsed catalogues by ``catalog_key``: ``{signature, digest, catalog, checked_at}``.
_CATALOGS: dict[tuple[str, str, str], dict[str, Any]] = {}


def _catalog_check_s() -> float:
    from services.config import CALIBRE_CATALOG_CHECK_S

    return float(CALIBRE_CATALOG_CHECK_S)


def forget_catalog_checks() -> None:
    """Make the next catalogue read check metadata.db now (after a write to it)."""
    for kept in _CATALOGS.values():
        kept["checked_at"] = 0.0


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
        book["files"] = [
            {"path": f"{library_path}/{book['path']}/{info['path']}".replace("//", "/"),
             "size": info.get("uncompressed_size")}
            for _fmt, info in sorted(book_formats.items())
        ]
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
    comments = {int(row[0]): row[1] for row in _safe_rows(conn, "SELECT book, text FROM comments") if row[1]}
    publishers_by_book = _read_link(conn, "books_publishers_link", "book", "publisher")
    # Calibre keeps a book's series in a link table (books has no series column).
    series_by_book = _read_link(conn, "books_series_link", "book", "series")
    series_names = {int(row[0]): row[1] for row in _safe_rows(conn, "SELECT id, name FROM series")}
    publisher_names = {int(row[0]): row[1] for row in _safe_rows(conn, "SELECT id, name FROM publishers")}

    books: dict[int, dict[str, Any]] = {}
    for row in rows:
        record = dict(zip(wanted, row, strict=False))
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
            "series": record.get("series") or next(
                (series_names[x] for x in series_by_book.get(book_id, []) if series_names.get(x)), ""),
            "series_index": series_index,
            "pubdate": record.get("pubdate") or "",
            "tags": [tag_names.get(t, "") for t in tags_by_book.get(book_id, []) if tag_names.get(t)],
            "last_modified": str(record.get("last_modified") or ""),
            "publisher": next((publisher_names[p] for p in publishers_by_book.get(book_id, [])
                               if publisher_names.get(p)), ""),
            "description": _description(comments.get(book_id)),
        }
    return books


def _description(html: str | None) -> str:
    """Calibre's comments (HTML) as one paragraph of plain text, kept short."""
    if not html:
        return ""
    text = " ".join(markup_text(str(html)).split())
    if len(text) > _DESCRIPTION_MAX_CHARS:
        text = text[:_DESCRIPTION_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return text


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

    Returning a format we have no reader for -- a DJVU, say -- would be a lie
    with a confusing consequence: the caller would hand its bytes to a reader
    that reports "not a zip archive" for every one of those books, instead of
    the honest "indexed as metadata, add a converter". ``None`` routes those
    books to the metadata-only path, where they are still discoverable. A PDF
    is read (``_read_pdf_sections``), but only when no e-book format exists.
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
    # The stored file a hit points at (the format text is read from on request).
    if book.get("text_path"):
        meta["file"] = str(book["text_path"])
        meta["file_format"] = str(book["text_format"])
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
