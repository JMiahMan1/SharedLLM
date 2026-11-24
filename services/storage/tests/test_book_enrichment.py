"""A book added through Jarvis is filled in by itself, and books already on the shelf can be.

Live, 2026-11-24: a PDF added through the library page got no description, no ISBN
and no tags, and the person typed the title by hand. The add route trusted the
form: a field left empty stayed empty (a PDF's own details were never read there),
the catalogue lookup ran only in the page's "inspect" step, its first search hit was
taken unchecked (the file name "book" came back as "The Book Thief"), and the tags it
suggested were only exact matches. Now the add route fills every empty field itself
(file, then a verified catalogue answer), picks tags from the library's most used
ones, says when it could not identify the book, and never replaces what a person
typed. ``/calibre/books/enrich`` does the same for books already on the shelf.
"""
from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from services.common import book_metadata
from services.storage import book_lookup, book_sources, library_api
from services.storage import calibre_writer as cw
from services.storage.main import app
from services.storage.tests.test_calibre_writer import _Dav, _epub, _library_bytes, _open

client = TestClient(app)
H = {"X-Internal-Secret": "test-secret"}
LIB = "/Books/Text"


# --- is it the same book? -------------------------------------------------------------

def test_a_first_search_hit_is_not_taken_on_trust():
    assert not book_lookup.same_book("book", [], {"title": "The Book Thief", "authors": ["Markus Zusak"]})
    assert book_lookup.same_book("Harbor Lights", ["Ann Example"],
                                 {"title": "Harbor Lights: Revised & Updated", "authors": ["Ann Example"]})
    assert book_lookup.same_book("Harbor Lights Revised & Updated", ["Ann Example"],
                                 {"title": "Harbor Lights", "authors": ["A. Example"]})
    assert not book_lookup.same_book("Harbor Lights", ["Ann Example"],
                                     {"title": "Harbor Lights", "authors": ["Ben Other"]})


# --- tags -----------------------------------------------------------------------------

def test_tags_are_the_librarys_most_used_ones_never_its_catalogue_fragments():
    existing = ["Fiction", "Sea stories", "England -- Fiction", "1725-1798", "etc.", "Rare tag"]
    usage = {"Fiction": 59, "Sea stories": 12, "England -- Fiction": 48, "1725-1798": 31, "etc.": 25, "Rare tag": 1}
    found = {"title": "Harbor Lights", "comments": "",
             "subjects": ["Sea stories", "England -- Fiction", "1725-1798", "etc.", "Rare tag", "Brand new subject"]}
    names = [t["name"] for t in book_lookup.suggest_tags(found, existing, usage=usage)]
    assert names == ["Fiction", "Sea stories"], "most used first; no '--' headings, dates, fragments or one-offs"


# --- adding a book --------------------------------------------------------------------

#: Both catalogues on, Google Books with a made-up key (as identity resolves them).
BOTH_ON = book_metadata.resolve({"google_books_enabled": True}, None, key="made-up-books-key", key_source="own")


def _integrations(monkeypatch, resolved=BOTH_ON):
    """identity's /api/resolve, replaced: ``resolved`` is every person's book_metadata."""
    asked: list[dict] = []

    async def resolve(body):
        asked.append(body)
        return {"user": body.get("rag_user") or "household", "book_metadata": resolved}

    book_sources.forget()
    monkeypatch.setattr(book_sources, "identity_resolve", resolve)
    return asked


def _catalogues(monkeypatch, google=None, openlib=None):
    _integrations(monkeypatch)

    async def g(session, title, author, isbn, **kw):
        out = book_lookup._blank()
        out.update(google or {})
        return out

    async def o(session, title, author, isbn):
        out = book_lookup._blank()
        out.update(openlib or {})
        return out

    monkeypatch.setattr(book_lookup, "google_books", g)
    monkeypatch.setattr(book_lookup, "open_library", o)

    async def no_cover(url):
        return None

    monkeypatch.setattr(book_lookup, "fetch_cover", no_cover)


def _library(monkeypatch, dav):
    class Prov:
        client = dav

    async def catalog(path):
        conn = _open(dav.files[f"{LIB}/metadata.db"])
        books = {}
        for book_id, title in conn.execute("SELECT id, title FROM books"):
            books[book_id] = {"id": book_id, "title": title, "tags": cw.book_tags(conn, book_id)}
        return books

    async def index(*args):
        return None

    monkeypatch.setattr(library_api, "_provider", lambda path: Prov())
    monkeypatch.setattr(library_api, "_catalog", catalog)
    monkeypatch.setattr(library_api, "_index_folder", index)


def _add(fields=None, data=None, name="harbor.epub"):
    return client.post(
        "/calibre/books/add", headers=H,
        data={"library_path": LIB, **(fields or {})},
        files={"file": (name, data or _epub(title="Harbor Lights", author="Ann Example", subjects=("Sea stories",)),
                        "application/octet-stream")},
    )


def _book(dav, book_id):
    conn = _open(dav.files[f"{LIB}/metadata.db"])
    row = conn.execute("SELECT title, pubdate FROM books WHERE id = ?", [book_id]).fetchone()
    comments = conn.execute("SELECT text FROM comments WHERE book = ?", [book_id]).fetchone()
    isbn = conn.execute("SELECT val FROM identifiers WHERE book = ? AND type = 'isbn'", [book_id]).fetchone()
    return {"title": row[0], "pubdate": row[1], "comments": comments[0] if comments else "",
            "isbn": isbn[0] if isbn else "", "tags": cw.book_tags(conn, book_id)}


def test_an_added_book_is_filled_in_from_the_file_then_a_matching_catalogue(monkeypatch):
    dav = _Dav(_library_bytes([("Old Sea Tale", "Someone", ["Sea stories", "Fiction"]),
                               ("Another", "Someone", ["Fiction", "Sea stories"])]))
    _library(monkeypatch, dav)
    _catalogues(monkeypatch, google={"title": "Harbor Lights", "authors": ["Ann Example"], "comments": "A keeper's year.",
                                     "isbn": "9780000000002", "published": "2011-05-01",
                                     "subjects": ["Fiction / Sea stories"]})
    out = _add().json()
    assert out["identified"] is True
    assert out["title"] == "Harbor Lights" and out["authors"] == ["Ann Example"]
    assert set(out["filled"]) >= {"comments", "isbn", "published", "tags"}
    assert "Filled in" in out["message"]
    book = _book(dav, out["book_id"])
    assert book["comments"] == "A keeper's year." and book["isbn"] == "9780000000002"
    assert book["pubdate"].startswith("2011-05-01")
    assert book["tags"] == ["Fiction", "Sea stories"], "the library's own tags, not new ones"


def test_what_a_person_typed_is_never_replaced(monkeypatch):
    dav = _Dav(_library_bytes([("Old Sea Tale", "Someone", ["Fiction"]), ("B", "S", ["Fiction"])]))
    _library(monkeypatch, dav)
    _catalogues(monkeypatch, google={"title": "Harbor Lights", "authors": ["Ann Example"], "comments": "From Google.",
                                     "subjects": ["Fiction"]})
    out = _add({"title": "Harbor Lights", "authors": "Ann Example", "comments": "My own note.",
                "tags": "family-favourite"}).json()
    book = _book(dav, out["book_id"])
    assert book["comments"] == "My own note."
    assert book["tags"] == ["family-favourite"]
    assert "comments" not in out["filled"] and "tags" not in out["filled"]


def test_an_unidentified_book_is_added_and_the_reply_says_so(monkeypatch):
    dav = _Dav(_library_bytes([("Old", "Someone", [])]))
    _library(monkeypatch, dav)
    _catalogues(monkeypatch, google={"title": "The Book Thief", "authors": ["Markus Zusak"], "comments": "Wrong book."},
                openlib={"title": "The Book Thief", "authors": ["Markus Zusak"]})
    out = _add(data=b"%PDF-1.4 nothing", name="book.pdf").json()
    assert out["status"] == "SUCCESS" and out["identified"] is False
    assert "could not identify it" in out["message"] and "The Book Thief" in out["message"]
    assert _book(dav, out["book_id"])["comments"] == "", "a different book's description is not used"


def test_a_pdfs_own_details_are_read_when_it_is_added(monkeypatch):
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "Harbor Lights", "/Author": "Ann Example"})
    buf = io.BytesIO()
    writer.write(buf)
    dav = _Dav(_library_bytes([("Old", "Someone", [])]))
    _library(monkeypatch, dav)
    _catalogues(monkeypatch)
    out = _add(data=buf.getvalue(), name="scan-0001.pdf").json()
    assert out["title"] == "Harbor Lights" and out["authors"] == ["Ann Example"]
    assert out["identified"] is False


# --- books already on the shelf ----------------------------------------------------------

def test_filling_in_never_overwrites_a_field_that_has_a_value():
    conn = _open(_library_bytes([("Harbor Lights", "Ann Example", ["Fiction"])]))
    conn.execute("INSERT INTO comments (book, text) VALUES (1, 'Typed by hand.')")
    written = cw.fill_missing(conn, 1, {"title": "Other", "authors": ["Ben Other"], "tags": ["Sea stories"],
                                        "comments": "From a catalogue.", "isbn": "9780000000002",
                                        "published": "2011", "publisher": "Example House"})
    assert set(written) == {"isbn", "published", "publisher"}
    assert conn.execute("SELECT title FROM books WHERE id = 1").fetchone()[0] == "Harbor Lights"
    assert cw.book_tags(conn, 1) == ["Fiction"]
    assert conn.execute("SELECT text FROM comments WHERE book = 1").fetchone()[0] == "Typed by hand."


def test_an_unknown_author_and_missing_tags_are_filled():
    conn = _open(_library_bytes([("Harbor Lights", "Unknown", [])]))
    written = cw.fill_missing(conn, 1, {"authors": ["Ann Example"], "tags": ["Fiction"]})
    assert written == {"authors": ["Ann Example"], "tags": ["Fiction"]}
    assert cw.book_tags(conn, 1) == ["Fiction"]


def _shelf(monkeypatch, dav, books):
    class Prov:
        client = dav
        download_timeout = 5

    async def catalog(path):
        return books

    monkeypatch.setattr(library_api, "_provider", lambda path: Prov())
    monkeypatch.setattr(library_api, "_catalog", catalog)


SHELF = {
    1: {"id": 1, "title": "Harbor Lights", "authors": ["Ann Example"], "tags": [], "series": "", "pubdate": "0101-01-01",
        "text_path": None},
    2: {"id": 2, "title": "Quiet Fields", "authors": ["Ben Sample"], "tags": ["Fiction"], "series": "",
        "pubdate": "2001-01-01", "text_path": None},
    3: {"id": 3, "title": "River Notes", "authors": ["Unknown"], "tags": ["Fiction"], "series": "",
        "pubdate": "0101-01-01", "text_path": None},
    4: {"id": 4, "title": "Fiction Sampler", "authors": ["Cy Placeholder"], "tags": ["Fiction"], "series": "",
        "pubdate": "0101-01-01", "text_path": None},
}


def test_a_dry_run_counts_and_proposes_but_writes_nothing(monkeypatch):
    dav = _Dav(_library_bytes([("Harbor Lights", "Ann Example", []), ("Quiet Fields", "Ben Sample", ["Fiction"]),
                               ("River Notes", "Unknown", ["Fiction"])]))
    before = dict(dav.files)
    _shelf(monkeypatch, dav, SHELF)
    _catalogues(monkeypatch, google={"title": "Harbor Lights", "authors": ["Ann Example"], "subjects": ["Fiction"]})
    out = client.post("/calibre/books/enrich", headers=H, json={"library_path": LIB}).json()
    assert out["dry_run"] is True and out["books_missing"] == 2
    assert out["missing"] == {"title": 0, "authors": 1, "tags": 1}
    proposal = next(p for p in out["proposals"] if p["book_id"] == 1)
    assert proposal["identified"] and proposal["fill"]["tags"] == ["Fiction"]
    assert dav.files == before, "a dry run writes nothing"


def test_a_real_run_fills_the_proposed_fields_in_one_write(monkeypatch):
    dav = _Dav(_library_bytes([("Harbor Lights", "Ann Example", []), ("Quiet Fields", "Ben Sample", ["Fiction"])]))
    _shelf(monkeypatch, dav, {1: SHELF[1], 2: SHELF[2], 4: SHELF[4]})
    _catalogues(monkeypatch, google={"title": "Harbor Lights", "authors": ["Ann Example"], "subjects": ["Fiction"],
                                     "comments": "A keeper's year."})
    out = client.post("/calibre/books/enrich", headers=H,
                      json={"library_path": LIB, "dry_run": False, "limit": 5}).json()
    assert out["written"]["1"]["tags"] == ["Fiction"]
    book = _book(dav, 1)
    assert book["tags"] == ["Fiction"] and book["comments"] == "A keeper's year."


def test_report_wording():
    assert "could not identify" in book_lookup.report("X", False, {}, [])
    assert inspect.iscoroutinefunction(book_lookup.lookup)


def test_open_library_is_asked_for_subjects_publishers_and_isbns(monkeypatch):
    import asyncio

    asked: list[str] = []

    async def fake_json(session, url, source=""):
        asked.append(url)
        return {"docs": [{"title": "Dawn", "author_name": ["H. Rider Haggard"], "subject": ["Fiction, general"]}]}

    monkeypatch.setattr(book_lookup, "_json", fake_json)
    out = asyncio.run(book_lookup.open_library(None, "Dawn", "Haggard", ""))
    assert "fields=" in asked[0] and "subject" in asked[0] and "isbn" in asked[0]
    assert out["subjects"] == ["Fiction, general"]
    names = [t["name"] for t in book_lookup.suggest_tags(out, ["Fiction"], usage={"Fiction": 59})]
    assert names == ["Fiction"], "'Fiction, general' lands on the library's Fiction shelf"
