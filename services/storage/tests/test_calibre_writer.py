"""Writing to a Calibre library: real schema, Calibre's triggers, compare-and-swap."""
from __future__ import annotations

import asyncio
import io
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from services.storage import calibre_writer as cw

SCHEMA = (Path(__file__).parent / "fixtures" / "calibre_metadata_schema.sql").read_text()


def _library_bytes(books: list[tuple[str, str, list[str]]] = ()) -> bytes:
    """A metadata.db with the real schema and a few books (title, author, tags)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metadata.db"
        conn = cw.open_library_copy(path)
        conn.executescript(SCHEMA)
        for title, author, tags in books:
            cw.insert_book(conn, cw.NewBook(title=title, authors=[author], fmt="epub", size=10, tags=tags))
        conn.commit()
        conn.close()
        return path.read_bytes()


def _open(data: bytes) -> sqlite3.Connection:
    path = Path(tempfile.mkdtemp()) / "metadata.db"
    path.write_bytes(data)
    return cw.open_library_copy(path)


def test_names_follow_calibre():
    assert cw.title_sort("The Lenten Spring") == "Lenten Spring, The"
    assert cw.title_sort("Great Lent") == "Great Lent"
    assert cw.author_sort("Alexander Schmemann") == "Schmemann, Alexander"
    assert cw.author_sort("Martin Luther King Jr.") == "King, Martin Luther Jr."
    assert cw.ascii_name('Fasting: "A Hunger" for God?') == "Fasting_ _A Hunger_ for God_"
    assert cw.clean_tags(["Fasting", "fasting", " Lent, Pascha ", ""]) == ["Fasting", "Lent Pascha"]


def test_a_book_is_registered_the_way_calibre_registers_one():
    conn = _open(_library_bytes())
    book_id, folder, name = cw.insert_book(conn, cw.NewBook(
        title="The Lenten Spring", authors=["Thomas Hopko"], fmt="epub", size=1234,
        tags=["fasting", "Lent"], series="Lenten", comments="Forty meditations.", has_cover=True,
    ))
    conn.commit()
    row = conn.execute("SELECT title, sort, uuid, path, author_sort, has_cover FROM books WHERE id=?", [book_id]).fetchone()
    # Calibre's triggers ran: sort from title_sort(), a uuid from uuid4().
    assert row[0] == "The Lenten Spring" and row[1] == "Lenten Spring, The" and len(row[2]) == 36
    assert row[3] == folder == f"Thomas Hopko/The Lenten Spring ({book_id})" and row[4] == "Hopko, Thomas" and row[5] == 1
    assert name == "The Lenten Spring - Thomas Hopko"
    assert conn.execute("SELECT format, name FROM data WHERE book=?", [book_id]).fetchone() == ("EPUB", name)
    assert cw.book_tags(conn, book_id) == ["fasting", "Lent"]
    assert conn.execute("SELECT book FROM metadata_dirtied").fetchall() == [(book_id,)]
    cw.check(conn)


def test_tags_are_added_and_removed_without_duplicates():
    conn = _open(_library_bytes([("Great Lent", "Alexander Schmemann", ["Orthodox"])]))
    assert cw.set_book_tags(conn, 1, ["Fasting", "orthodox"], []) == ["Fasting", "Orthodox"]
    assert cw.set_book_tags(conn, 1, [], ["ORTHODOX"]) == ["Fasting"]
    # An existing tag is reused whatever its case.
    assert conn.execute("SELECT COUNT(*) FROM tags WHERE name = 'fasting' COLLATE NOCASE").fetchone()[0] == 1
    with pytest.raises(cw.LibraryWriteError):
        cw.set_book_tags(conn, 99, ["x"], [])


def _epub(title="Great Lent", author="Alexander Schmemann", subjects=("Lent",), cover=b"\xff\xd8JPEG") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        subjects_xml = "".join(f"<dc:subject>{s}</dc:subject>" for s in subjects)
        zf.writestr("OEBPS/content.opf", f'<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    f'<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>{subjects_xml}<meta name="cover" content="c"/></metadata>'
                    '<manifest><item id="c" href="images/cover.jpg" media-type="image/jpeg"/></manifest></package>')
        zf.writestr("OEBPS/images/cover.jpg", cover)
    return buf.getvalue()


def test_an_epub_names_itself_and_brings_its_cover():
    found = cw.epub_details(_epub())
    assert found["title"] == "Great Lent" and found["authors"] == ["Alexander Schmemann"]
    assert found["tags"] == ["Lent"] and found["cover"] == b"\xff\xd8JPEG"
    assert cw.epub_details(b"not a zip")["title"] == ""


class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status, self._body, self.headers = status, body, headers or {}

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Dav:
    """Nextcloud in memory: ETags, If-Match, and a rival writer on demand."""

    def __init__(self, db: bytes):
        self.files = {"/Books/Text/metadata.db": db}
        self.version = 1
        self.rival_writes = 0
        self.deleted: list[str] = []
        self.client = self

    async def _full_url(self, path):
        return path

    def get(self, url, **kw):
        return _Resp(200, self.files[url], {"ETag": f'"v{self.version}"'})

    def put(self, url, data=None, headers=None, **kw):
        if url.endswith("metadata.db") and headers and "If-Match" in headers:
            if self.rival_writes:
                self.rival_writes -= 1
                self.version += 1  # someone else saved first
            if headers["If-Match"] != f'"v{self.version}"':
                return _Resp(412)
            self.version += 1
        self.files[url] = data
        return _Resp(201)

    def request(self, method, url, **kw):
        self.deleted.append(url)
        self.files = {k: v for k, v in self.files.items() if not k.startswith(url)}
        return _Resp(204)

    async def ensure_directory(self, path):
        return None

    async def list_files(self, path):
        return [{"href": f"/remote.php/dav/files/u{k}"} for k in self.files if k.startswith(path)]


def test_adding_a_book_uploads_it_then_swaps_the_database():
    dav = _Dav(_library_bytes([("Great Lent", "Alexander Schmemann", [])]))
    out = asyncio.run(cw.LibraryWriter(dav, "/Books/Text").add_book(_epub(title="A Hunger for God", author="John Piper"), "piper.epub", tags=["fasting"]))
    assert out["title"] == "A Hunger for God" and out["tags"] == ["fasting"] and out["attempts"] == 1
    folder = f"/Books/Text/{out['path']}"
    assert f"{folder}/{out['file']}" in dav.files and f"{folder}/cover.jpg" in dav.files
    assert out["backup"].startswith("/Books/.jarvis-calibre-backups/metadata-")
    conn = _open(dav.files["/Books/Text/metadata.db"])
    assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 2


def test_a_rival_write_is_never_overwritten():
    dav = _Dav(_library_bytes([("Great Lent", "Alexander Schmemann", [])]))
    dav.rival_writes = 1
    out = asyncio.run(cw.LibraryWriter(dav, "/Books/Text").set_tags(1, ["fasting"], []))
    assert out["attempts"] == 2 and out["tags"] == ["fasting"]

    # A rival that never stops: the book's folder is cleaned up and it gives up.
    dav.rival_writes = 99
    with pytest.raises(cw.LibraryWriteError):
        asyncio.run(cw.LibraryWriter(dav, "/Books/Text").add_book(_epub(), "x.epub"))
    assert dav.deleted and not any(k.endswith(".epub") for k in dav.files)


def test_a_file_calibre_does_not_keep_is_refused():
    dav = _Dav(_library_bytes())
    with pytest.raises(cw.LibraryWriteError):
        asyncio.run(cw.LibraryWriter(dav, "/Books/Text").add_book(b"x", "notes.exe"))
