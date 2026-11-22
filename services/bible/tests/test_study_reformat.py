"""Reformatting an existing study book, and reading a section aloud.

A book imported before the normaliser is one page per section with every
running head in the text. Reformatting it must be previewed, must not lose a
reader's place (an old page number lands on the lesson that holds those
words), and must refuse to apply something other than what was previewed.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import Session, select

from services.bible import main as bible_main
from services.bible import narration, study_assist, study_blocks, study_format
from services.bible.models import ImportedBook, ImportedBookChapter, NarrationAudio
from services.bible.tests import study_fixtures as fx


def _legacy_book(engine, pdf_path) -> int:
    """A book as the old importer stored it: raw page text, one page per section."""
    import pymupdf

    document = pymupdf.open(pdf_path)
    pages = [page.get_text("text").strip() for page in document]
    document.close()
    with Session(engine) as session:
        book = ImportedBook(
            title="A Made-up Study Guide",
            kind="study",
            source="upload",
            source_path=str(pdf_path),
            chapter_count=len(pages),
        )
        session.add(book)
        session.flush()
        for ordinal, text in enumerate(pages, start=1):
            session.add(ImportedBookChapter(book_id=book.id, ordinal=ordinal, label=f"Page {ordinal}", text=text))
        session.commit()
        return int(book.id)


@pytest.fixture
def legacy(engine, client, tmp_path, monkeypatch):
    monkeypatch.setattr(bible_main, "_import_dir", lambda: tmp_path)
    pdf = fx.pdf_book(tmp_path / "guide.pdf")
    return _legacy_book(engine, pdf), pdf


def test_a_preview_reports_the_change_and_writes_nothing(client, engine, legacy):
    book_id, _pdf = legacy
    response = client.post(f"/admin/books/{book_id}/reformat/preview", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["before"]["sections"] == 6 and body["before"]["format_version"] == 0
    assert body["after"]["sections"] == 2 and body["after"]["format_version"] == study_blocks.FORMAT_VERSION
    assert body["after"]["report"]["running_lines_removed"] >= 12
    # Removing running heads costs a little of the old wording; losing pages would cost a lot.
    assert body["retained"] > 0.8
    assert body["token"]
    with Session(engine) as session:
        rows = session.exec(select(ImportedBookChapter).where(ImportedBookChapter.book_id == book_id)).all()
        assert len(rows) == 6 and all(not row.blocks for row in rows)


def test_applying_a_preview_keeps_every_old_page_reachable(client, engine, legacy):
    book_id, _pdf = legacy
    preview = client.post(f"/admin/books/{book_id}/reformat/preview", json={}).json()
    mapping = {entry["old_ordinal"]: entry["ordinal"] for entry in preview["mapping"]}
    assert mapping == {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2}

    applied = client.post(f"/admin/books/{book_id}/reformat/apply", json={"token": preview["token"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["book"]["chapter_count"] == 2
    assert applied.json()["book"]["format_version"] == study_blocks.FORMAT_VERSION

    detail = client.get(f"/imported-books/{book_id}").json()
    assert [c["section_id"] for c in detail["chapters"]] == [
        "s-lesson-1-walking-in-the-light",
        "s-lesson-2-walking-in-the-light",
    ]
    assert {a["old_ordinal"]: a["ordinal"] for a in detail["aliases"]} == mapping

    # A reader's stored "page 5" from before the reformat lands in lesson 2.
    resolved = client.get(f"/imported-books/{book_id}/resolve", params={"ordinal": 5, "legacy": True})
    assert resolved.json()["section_id"] == "s-lesson-2-walking-in-the-light"
    section = client.get(f"/imported-books/{book_id}/sections/s-lesson-2-walking-in-the-light").json()
    assert section["ordinal"] == 2
    assert any(block["type"] == "question" for block in section["blocks"])

    # The preview token is spent.
    again = client.post(f"/admin/books/{book_id}/reformat/apply", json={"token": preview["token"]})
    assert again.status_code == 409


def test_a_second_reformat_keeps_old_section_ids_working(client, engine, legacy):
    book_id, _pdf = legacy
    first = client.post(f"/admin/books/{book_id}/reformat/preview", json={}).json()
    client.post(f"/admin/books/{book_id}/reformat/apply", json={"token": first["token"]})
    second = client.post(f"/admin/books/{book_id}/reformat/preview", json={}).json()
    assert second["retained"] == 1.0
    assert client.post(f"/admin/books/{book_id}/reformat/apply", json={"token": second["token"]}).status_code == 200
    resolved = client.get(f"/imported-books/{book_id}/resolve", params={"section": "s-lesson-1-walking-in-the-light"})
    assert resolved.json()["ordinal"] == 1


def test_apply_refuses_what_was_not_previewed(client, engine, legacy, tmp_path):
    book_id, pdf = legacy
    preview = client.post(f"/admin/books/{book_id}/reformat/preview", json={}).json()
    fx.pdf_book(pdf, pages=4)  # the file changed under the preview
    response = client.post(f"/admin/books/{book_id}/reformat/apply", json={"token": preview["token"]})
    assert response.status_code == 409
    assert "Preview" in response.json()["detail"]
    assert client.post(f"/admin/books/{book_id}/reformat/apply", json={"token": "../../etc"}).status_code == 400


def test_a_book_whose_file_is_gone_says_so(client, engine, legacy):
    book_id, pdf = legacy
    pdf.unlink()
    response = client.post(f"/admin/books/{book_id}/reformat/preview", json={})
    assert response.status_code == 409
    assert "Import it again" in response.json()["detail"]


def test_new_formats_upload(client, tmp_path, monkeypatch):
    monkeypatch.setattr(bible_main, "_import_dir", lambda: tmp_path)
    docx = fx.docx_book(tmp_path / "source.docx").read_bytes()
    response = client.post(
        "/admin/books/upload",
        files={"file": ("Grace Study.docx", docx, "application/octet-stream")},
        data={"kind": "study"},
    )
    assert response.status_code == 200, response.text
    book = response.json()["book"]
    assert book["chapter_count"] == 2 and book["reformattable"]
    chapter = client.get(f"/imported-books/{book['id']}/chapters/1").json()
    assert chapter["section_id"] == "s-lesson-1-grace"
    linked = [ref["ref"] for block in chapter["blocks"] for ref in block.get("refs", [])]
    assert "Ephesians 2:8-9" in linked
    refused = client.post(
        "/admin/books/upload",
        files={"file": ("Slides.pptx", b"x", "application/octet-stream")},
        data={"kind": "study"},
    )
    assert refused.status_code == 400


def test_book_images_are_served(client, tmp_path, monkeypatch):
    monkeypatch.setattr(bible_main, "_import_dir", lambda: tmp_path)
    epub = fx.epub_book(tmp_path / "source.epub").read_bytes()
    book = client.post(
        "/admin/books/upload", files={"file": ("Vine.epub", epub, "application/epub+zip")}, data={"kind": "study"}
    ).json()["book"]
    chapter = client.get(f"/imported-books/{book['id']}/chapters/1").json()
    image = next(block for block in chapter["blocks"] if block["type"] == "image")
    served = client.get(f"/imported-books/{book['id']}/assets/{image['src']}")
    assert served.status_code == 200
    assert served.content == fx.PNG and served.headers["content-type"] == "image/png"
    assert client.get(f"/imported-books/{book['id']}/assets/missing.png").status_code == 404


# --- Read aloud ----------------------------------------------------------------


@pytest.fixture
def spoken_book(client, tmp_path, monkeypatch):
    monkeypatch.setattr(bible_main, "_import_dir", lambda: tmp_path)
    epub = fx.epub_book(tmp_path / "source.epub").read_bytes()
    return client.post(
        "/admin/books/upload", files={"file": ("Vine.epub", epub, "application/epub+zip")}, data={"kind": "study"}
    ).json()["book"]


def test_a_section_reads_aloud_without_its_footnotes(client, spoken_book, monkeypatch):
    calls: list[str] = []

    async def fake_speak(text, **kwargs):
        calls.append(text)
        return b"RIFF-fake", "audio/wav"

    monkeypatch.setattr(narration, "_speak", fake_speak)
    monkeypatch.setattr(bible_main, "EXECUTION_SVC_URL", "http://execution.invalid")
    plan = client.get(f"/imported-books/{spoken_book['id']}/chapters/1/narration/plan").json()
    assert plan["count"] >= 2 and plan["section_id"] == "s-lesson-1"
    assert plan["chunks"][0]["characters"] <= narration.FIRST_CHUNK_CHARACTERS + 200
    spoken_ids = [mark["block_id"] for chunk in plan["chunks"] for mark in chunk["blocks"]]
    assert spoken_ids and all(not block_id.startswith(("n-", "t-", "i-")) for block_id in spoken_ids)

    texts = []
    for index in range(plan["count"]):
        response = client.get(f"/imported-books/{spoken_book['id']}/chapters/1/narration/chunk", params={"index": index})
        assert response.status_code == 200, response.text
        texts.append(" ".join(calls[-1:]))
    script = " ".join(calls)
    assert script.startswith("Lesson 1. The Vine and the Branches.")
    assert "Question 1. What does it mean to abide?" in script
    assert "A footnote about the vine" not in script
    assert "Day | Reading" not in script

    # The second play is served from the cache, not the engine.
    before = len(calls)
    again = client.get(f"/imported-books/{spoken_book['id']}/chapters/1/narration/chunk", params={"index": 0}).json()
    assert again["cached"] is True and len(calls) == before
    assert again["blocks"][0]["block_id"].startswith("h-")


def test_a_missing_engine_is_a_503_that_names_the_setting(client, spoken_book, monkeypatch):
    monkeypatch.setattr(bible_main, "EXECUTION_SVC_URL", "")
    response = client.get(f"/imported-books/{spoken_book['id']}/chapters/1/narration/chunk", params={"index": 0})
    assert response.status_code == 503
    assert "EXECUTION_SVC_URL" in response.json()["detail"]


def test_a_part_past_the_end_is_a_400(client, spoken_book):
    response = client.get(f"/imported-books/{spoken_book['id']}/chapters/1/narration/chunk", params={"index": 99})
    assert response.status_code == 400


def test_a_legacy_section_reads_aloud_from_its_text(engine, tmp_path):
    from services.bible import study_narration

    with Session(engine) as session:
        book = ImportedBook(title="Old", kind="study")
        session.add(book)
        session.flush()
        chapter = ImportedBookChapter(book_id=book.id, ordinal=1, label="Page 1", text="12\n\nFirst para-\ngraph.\n\nSecond.")
        session.add(chapter)
        session.commit()
        spoken = study_narration.spoken_blocks(chapter)
        assert [s.text for s in spoken] == ["First paragraph.", "Second."]
        assert session.exec(select(NarrationAudio)).all() == []


# --- The local model pass ----------------------------------------------------------


def _ambiguous_document():
    extracted = study_blocks.Extracted(
        blocks=[
            study_blocks.Block(type="heading", text="Lesson 1", level=1),
            study_blocks.Block(type="paragraph", text="Abiding in Christ"),
            study_blocks.Block(type="paragraph", text="Placeholder prose that follows the short line above."),
        ]
    )
    return study_blocks.finish(extracted)


def test_the_model_pass_is_skipped_without_a_local_model():
    document = _ambiguous_document()
    report = asyncio.run(study_assist.assist(document, llm_url="", model="m"))
    assert report["status"] == "skipped"


def test_the_model_only_labels_lines_and_cannot_change_words(monkeypatch):
    document = _ambiguous_document()
    before = study_blocks.text_fingerprint("".join(study_blocks.section_text(s) for s in document.sections))
    seen: dict = {}

    class FakeResponse:
        status = 200

        async def json(self, content_type=None):
            # A model that tries to rewrite text gets nowhere: only ids are read.
            return {"message": {"content": json.dumps({"headings": [1], "text": "Something invented"})}}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None, timeout=None):
            seen["url"] = url
            seen["body"] = json
            return FakeResponse()

    monkeypatch.setattr(study_assist.aiohttp, "ClientSession", FakeSession)
    report = asyncio.run(study_assist.assist(document, llm_url="http://alpaca.local:11434", model="local-model"))
    assert report["status"] == "applied" and report["changed"] == 1
    assert seen["url"] == "http://alpaca.local:11434/api/chat"
    assert "Abiding in Christ" in seen["body"]["messages"][1]["content"]
    blocks = document.sections[0].blocks
    assert blocks[1].type == "heading"
    after = study_blocks.text_fingerprint("".join(study_blocks.section_text(s) for s in document.sections))
    assert after == before


def test_a_label_pass_that_changed_the_words_is_discarded(monkeypatch):
    document = _ambiguous_document()
    calls = {"n": 0}
    real = study_assist.section_text

    def drifting(section):
        calls["n"] += 1
        return real(section) + ("x" if calls["n"] > 1 else "")

    monkeypatch.setattr(study_assist, "section_text", drifting)
    block_id = document.sections[0].blocks[1].id
    with pytest.raises(ValueError):
        study_assist.apply_labels(document, {block_id}, {block_id})
    assert document.sections[0].blocks[1].type == "paragraph"


def test_reading_a_book_is_deterministic(tmp_path):
    a = study_format.read(fx.pdf_book(tmp_path / "a.pdf"))
    b = study_format.read(fx.pdf_book(tmp_path / "b.pdf"))
    from services.bible import study_reformat

    assert study_reformat.fingerprint(a) == study_reformat.fingerprint(b)
