"""Filling in a book before it is added: the file, the catalogues, and tags."""
import asyncio
import io

import pytest

from services.storage import book_lookup
from services.storage.tests.test_calibre_writer import _epub


def test_tags_come_from_matching_library_tags_and_the_apps_topics():
    found = {"title": "Great Lent", "subjects": ["Lent", "Orthodox Eastern Church"],
             "comments": "A journey of fasting and prayer to Pascha."}
    tags = book_lookup.suggest_tags(found, ["Lent", "Orthodox", "Fasting"])
    names = [t["name"] for t in tags]
    assert names[0] == "Lent" and tags[0]["existing"]
    # The library's own spelling of a topic wins over a new tag.
    assert "Fasting" in names and "fasting" not in names
    assert "prayer" in names and not next(t for t in tags if t["name"] == "prayer")["existing"]
    assert "health" not in names


def test_breakfast_is_not_a_fast():
    assert book_lookup.suggest_tags({"title": "Breakfast Recipes", "subjects": [], "comments": ""}, []) == []


def test_the_file_comes_first_then_the_catalogues(monkeypatch):
    async def google(session, title, author, isbn):
        out = book_lookup._blank()
        out.update(title="Great Lent : Journey to Pascha", comments="From Google.", publisher="SVS Press",
                   isbn="9780913836040", cover_url="https://books.google.com/x.jpg")
        return out

    async def openlib(session, title, author, isbn):
        out = book_lookup._blank()
        out.update(published="1969", subjects=["Lent"])
        return out

    monkeypatch.setattr(book_lookup, "google_books", google)
    monkeypatch.setattr(book_lookup, "open_library", openlib)
    out = asyncio.run(book_lookup.inspect(_epub(cover=b"\xff\xd8" + b"x" * 2000), "great-lent.epub", ["Lent"]))
    assert out["title"] == "Great Lent" and out["sources"]["title"] == "the file"
    assert out["comments"] == "From Google." and out["sources"]["comments"] == "Google Books"
    assert out["published"] == "1969" and out["sources"]["published"] == "Open Library"
    # The file's own cover is used; no catalogue cover is fetched.
    assert out["cover_url"] == "" and out["cover_preview"].startswith("data:image/jpeg;base64,")
    assert out["tags"][0]["name"] == "Lent"


def test_offline_it_still_reads_the_file_and_its_name():
    out = asyncio.run(book_lookup.inspect(b"%PDF-1.4 nothing", "The Lenten Spring - Thomas Hopko.pdf", [], online=False))
    assert out["title"] == "The Lenten Spring" and out["authors"] == ["Thomas Hopko"]
    assert out["sources"]["title"] == "the file name"


def test_a_pdfs_document_info_is_read():
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": "A Hunger for God", "/Author": "John Piper", "/Keywords": "fasting; prayer"})
    buf = io.BytesIO()
    writer.write(buf)
    out = book_lookup.pdf_details(buf.getvalue())
    assert out["title"] == "A Hunger for God" and out["authors"] == ["John Piper"] and out["subjects"] == ["fasting", "prayer"]


def test_covers_come_only_from_the_catalogues():
    assert asyncio.run(book_lookup.fetch_cover("http://covers.openlibrary.org/b/id/1-L.jpg")) is None
    assert asyncio.run(book_lookup.fetch_cover("https://169.254.169.254/latest")) is None
    assert asyncio.run(book_lookup.fetch_cover("https://example.com/cover.jpg")) is None


def test_open_library_language_is_english_when_listed_and_never_a_guess():
    class Session:
        def __init__(self, languages):
            self.languages = languages

        def get(self, url, timeout=None):
            body = {"docs": [{"title": "Great Lent", "language": self.languages}]} if "search.json" in url else {}

            class Resp:
                status = 200

                async def json(self, content_type=None):
                    return body

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

            return Resp()

    assert asyncio.run(book_lookup.open_library(Session(["dut", "eng", "rus"]), "Great Lent", "", ""))["language"] == "eng"
    assert asyncio.run(book_lookup.open_library(Session(["dut", "rus"]), "Great Lent", "", ""))["language"] == ""
    assert asyncio.run(book_lookup.open_library(Session(["fre"]), "Great Lent", "", ""))["language"] == "fre"
