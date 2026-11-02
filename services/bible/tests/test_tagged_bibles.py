"""Bibles tagged "Bible" in the family library: found, named, installed once."""
from __future__ import annotations

from services.bible import importer, tagged
from services.bible import main as bible_main
from services.bible.models import ImportRun


def _book(book_id, title, tags=("Bible",), formats=("EPUB",)):
    folder = f"/Books/Text/Author/{title} ({book_id})"
    return {
        "id": book_id, "title": title, "authors": ["Publisher"], "tags": list(tags), "formats": list(formats),
        "files": [{"format": f, "path": f"{folder}/{title}.{f.lower()}"} for f in formats],
    }


def test_the_translation_and_edition_come_from_the_title():
    assert tagged.detect_code("NKJV, Apply the Word Study Bible, eBook", []) == "nkjv"
    assert tagged.detect_code("The Holy Bible: King James Version", []) == "kjv"
    assert tagged.detect_code("ESV Study Bible", []) == "esv"
    assert tagged.detect_code("NLT Courage For Life Study Bible for Men", []) == "nlt"
    assert tagged.detect_code("My Grandmother's Bible", ["Bible", "nlt"]) == "nlt"
    assert tagged.detect_code("A Bible Atlas", ["Bible"]) is None
    code, name = tagged.edition_for("nkjv", "NKJV, Apply the Word Study Bible, eBook")
    assert code == "nkjv-apply-the-word-study" and name == "NKJV, Apply the Word Study Bible"
    assert tagged.edition_for("esv", "The Holy Bible, English Standard Version") == ("esv", "")
    niv = "NIV, the NIV Zondervan Study Bible, eBook: Built on the Truth of Scripture"
    assert tagged.edition_for("niv", niv) == ("niv-zondervan-study", "NIV, the NIV Zondervan Study Bible")
    # Notes already installed under a name in the title are reused, not doubled.
    existing = [("nlt-courage", "Courage For Life", "nlt"), ("nlt", "New Living Translation", "nlt")]
    assert tagged.edition_for("nlt", "NLT Courage For Life Study Bible for Men", existing) == ("nlt-courage", "Courage For Life")


def test_only_the_exact_tag_counts():
    assert tagged.is_bible_tag("Bible") and tagged.is_bible_tag("jarvis:bible")
    assert not tagged.is_bible_tag("Bible. O.T. -- Legends") and not tagged.is_bible_tag("Bible--Hermeneutics")


def test_an_epub_is_preferred_and_brings_its_notes():
    plan, reason = tagged.plan_for(_book(7, "ESV Study Bible", formats=("PDF", "EPUB")))
    assert reason == "" and plan.code == "esv" and plan.kind == "epub" and plan.import_notes
    assert plan.library_path.endswith("ESV Study Bible.epub") and plan.edition == "esv-study"
    pdf, _ = tagged.plan_for(_book(8, "King James Bible", formats=("PDF",)))
    assert pdf.kind == "pdf" and not pdf.import_notes


def test_a_book_that_cannot_be_installed_says_why():
    assert "translation" in tagged.plan_for(_book(9, "A Bible Atlas"))[1]
    assert "catalogue" in tagged.plan_for(_book(10, "CSB Study Bible"), code="csb")[1]
    assert "EPUB or PDF" in tagged.plan_for(_book(11, "NIV Bible", formats=("MOBI",)))[1]


def test_each_file_is_tried_once(session):
    books = [_book(1, "ESV Study Bible"), _book(2, "NIV Bible")]
    assert [p.code for p in tagged.due(session, books)] == ["esv", "niv"]
    session.add(ImportRun(source=tagged.source_of(books[0]["files"][0]["path"]), kind="epub", code="esv", status="failed",
                          message="Extracted 2 of 66 books"))
    session.commit()
    assert [p.code for p in tagged.due(session, books)] == ["niv"]
    row = tagged.describe(session, books[0])
    assert row["state"] == "failed" and "2 of 66" in row["reason"]


def test_the_admin_route_lists_and_installs_through_the_importer(client, monkeypatch):
    books = [_book(5, "NKJV, Apply the Word Study Bible, eBook")]
    ran: list[importer.ImportPlan] = []

    async def fake_books():
        return books

    def fake_run(plan, registry):
        ran.append(plan)
        return {"status": "succeeded"}

    monkeypatch.setattr(bible_main, "_tagged_books", fake_books)
    monkeypatch.setattr(bible_main, "_admin_import_run", fake_run)
    listing = client.get("/admin/library/tagged").json()
    assert listing["books"][0]["code"] == "nkjv" and listing["books"][0]["state"] == "ready"
    assert client.post("/admin/library/tagged/5/install", json={}).status_code == 200
    assert ran[0].library_path.endswith(".epub") and ran[0].edition.startswith("nkjv-apply")
    assert client.post("/admin/library/tagged/99/install", json={}).status_code == 404


def test_a_study_bible_already_installed_from_an_upload_is_not_installed_twice(session):
    from services.bible.models import BibleEdition

    session.add(BibleEdition(code="nlt-courage", version_code="nlt", name="Courage For Life", publisher="Tyndale",
                             license_class="licensed", rights_holder="Tyndale"))
    session.commit()
    book = _book(3, "NLT Courage For Life Study Bible for Men")
    assert tagged.due(session, [book]) == []
    row = tagged.describe(session, book)
    assert row["state"] == "installed" and row["edition"] == "nlt-courage" and "another copy" in row["reason"]


def test_reinstalling_a_copy_whose_notes_were_not_found_keeps_the_old_notes(client, session, monkeypatch):
    from fastapi.responses import JSONResponse

    from services.bible.models import BibleEdition, StudyNote

    session.add(BibleEdition(code="nlt-courage", version_code="nlt", name="Courage For Life", publisher="Tyndale",
                             license_class="licensed", rights_holder="Tyndale"))
    session.add(StudyNote(version_code="nlt", edition_code="nlt-courage", osis="John", chapter=1, verse=1,
                          body="In the beginning", source="/data/imports/nlt.epub"))
    session.commit()
    book = _book(3, "NLT Courage For Life Study Bible for Men")

    async def fake_books():
        return [book]

    monkeypatch.setattr(bible_main, "_tagged_books", fake_books)
    monkeypatch.setattr(bible_main, "_admin_import_run", lambda plan, registry: JSONResponse({"status": "succeeded"}))
    assert client.post("/admin/library/tagged/3/install", json={}).status_code == 200
    session.expire_all()
    assert [n.source for n in session.exec(bible_main.select(StudyNote)).all()] == ["/data/imports/nlt.epub"]
