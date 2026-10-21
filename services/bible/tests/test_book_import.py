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
        assert book.chapter_count >= 1
        chapters = book_import.chapters_for(session, book.id)
        assert any(chapter.label == "Day One" for chapter in chapters)
        assert any("Rest in the Lord" in chapter.text for chapter in chapters)
        entry = book_import.devotional_for_day(session, book.id, 1)
        assert entry is not None
        assert entry["work"] == f"book:{book.id}"
