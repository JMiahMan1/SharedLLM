import io
import zipfile

from sqlmodel import Session, SQLModel, create_engine

from services.bible import book_import
from services.bible.models import ImportedBook


def _epub() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test</dc:title></metadata>
  <manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            "<html><body><h1>Day One</h1><p>Rest in the Lord.</p>"
            "<h2>Questions</h2><p>What does rest mean here?</p></body></html>",
        )
    return buffer.getvalue()


def _pdf(text: str) -> bytes:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def test_a_pdf_book_imports_one_page_per_section(tmp_path):
    source = tmp_path / "My Study Book - A Writer.pdf"
    source.write_bytes(_pdf("Rest in the Lord on page one."))
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        book = book_import.import_book(
            session,
            path=source,
            kind="study",
            source="upload",
            source_path=str(source),
        )
        assert book.title == "My Study Book"
        assert book.author == "A Writer"
        assert book.chapter_count == 1
        chapters = book_import.chapters_for(session, book.id)
        assert chapters[0].label == "Page 1"
        assert "Rest in the Lord" in chapters[0].text


def test_an_epub_book_imports_as_chapters(tmp_path):
    source = tmp_path / "My Devotional - A Writer.epub"
    source.write_bytes(_epub())
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        book = book_import.import_book(
            session,
            path=source,
            kind="devotional",
            source="upload",
            source_path=str(source),
        )
        assert isinstance(book, ImportedBook)
        assert book.title == "My Devotional"
        assert book.author == "A Writer"
        assert book.chapter_count == 1
        chapters = book_import.chapters_for(session, book.id)
        assert chapters[0].label == "Day One"
        assert "Rest in the Lord" in chapters[0].text
        assert "What does rest mean here?" in chapters[0].questions
        entry = book_import.devotional_for_day(session, book.id, 1)
        assert entry is not None
        assert entry["work"] == f"book:{book.id}"


def test_an_uploaded_book_decodes_its_filename_and_keeps_the_whole_title(tmp_path, client, monkeypatch):
    """A percent-encoded picker name must not become the book's title.

    The Android picker and some browsers send ``Walk%20With%20Jesus.pdf``. That
    name used to be accepted verbatim, saved under a generated
    ``book-<timestamp>-`` prefix, and the prefix plus ``%20`` ended up in the
    title the reader saw.
    """
    from services.bible import main as bible_main

    monkeypatch.setattr(bible_main, "_import_dir", lambda: tmp_path)
    response = client.post(
        "/admin/books/upload",
        files={
            "file": (
                "Walk%20With%20Jesus%20PDF%20-%201st%20Edition.pdf",
                _pdf("Week 1: Introduction to Walk With Jesus."),
                "application/pdf",
            )
        },
        data={"kind": "study"},
    )
    assert response.status_code == 200, response.text
    book = response.json()["book"]
    assert book["title"] == "Walk With Jesus PDF - 1st Edition"
    assert book["author"] == ""
    assert book["chapter_count"] == 1
    saved = [path for path in tmp_path.iterdir() if path.suffix == ".pdf"]
    assert len(saved) == 1
    assert saved[0].name.endswith("-Walk With Jesus PDF - 1st Edition.pdf")
    assert "%20" not in saved[0].name


def test_imported_book_routes_do_not_collide_with_the_scripture_catalogue(
    tmp_path, client, monkeypatch
):
    """The reader's imported shelf is not the list of Bible books.

    ``GET /books`` in the bible service is the Scripture catalogue. The imported
    library used to register the same path, so the later route was shadowed and
    the shelf received 66 Bible books with no ``chapter_count`` and rendered an
    empty list.
    """
    from services.bible import main as bible_main

    monkeypatch.setattr(bible_main, "_import_dir", lambda: tmp_path)
    uploaded = client.post(
        "/admin/books/upload",
        files={"file": ("Reader Study.pdf", _pdf("A chapter from the reader."), "application/pdf")},
        data={"kind": "study"},
    )
    assert uploaded.status_code == 200, uploaded.text
    book_id = uploaded.json()["book"]["id"]

    listed = client.get("/imported-books", params={"kind": "study"})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1
    assert body["books"][0]["title"] == "Reader Study"
    assert body["books"][0]["chapter_count"] == 1

    detail = client.get(f"/imported-books/{book_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["chapters"][0]["ordinal"] == 1

    chapter = client.get(f"/imported-books/{book_id}/chapters/1")
    assert chapter.status_code == 200, chapter.text
    assert "reader" in chapter.json()["text"].lower()

    scripture = client.get("/books")
    # The fixture has no translation loaded, so /books may be 503. What matters
    # is that it is the Scripture catalogue, never the imported shelf.
    assert scripture.status_code in (200, 503), scripture.text
    if scripture.status_code == 200:
        assert scripture.json()["books"][0]["name"] == "Genesis"
        assert "chapter_count" not in scripture.json()["books"][0]
