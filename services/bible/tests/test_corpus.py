"""Corpus import and reads.

Import is the one path that writes Scripture, so the tests here are mostly about
what it refuses: a wrong checksum, a book with the wrong number of chapters, a
source missing a book. A silently short Bible is the worst failure this service
can have, because every reader after it looks like the text is simply like that.
"""
import json

import pytest

from services.bible import books as book_table
from services.bible import corpus
from services.bible.refs import ReferenceError, parse_one


def codes(session) -> list[str]:
    return [v["code"] for v in corpus.list_versions(session)]


def test_import_then_read_a_passage(loaded):
    assert codes(loaded) == ["kjv"]
    verses = corpus.fetch_passage(loaded, "kjv", [parse_one("Gen 1:2")])
    assert [(v["osis"], v["chapter"], v["verse"], v["text"]) for v in verses] == [
        ("Gen", 1, 2, "Verse 1:2 in Gen.")
    ]
    assert verses[0]["reference"] == "Genesis 1:2"


def test_import_records_a_verse_count(loaded):
    summary = corpus.corpus_summary(loaded)
    assert summary["verse_rows"] > 0
    assert summary["versions"][0]["verse_count"] == summary["verse_rows"]


def test_import_is_idempotent_per_version(loaded, corpus_file):
    first = corpus.corpus_summary(loaded)["verse_rows"]
    corpus.import_corpus(loaded, code="kjv", name="King James Version", source_path=corpus_file)
    assert corpus.corpus_summary(loaded)["verse_rows"] == first


def test_two_versions_coexist(loaded, corpus_file):
    corpus.import_corpus(loaded, code="asv", name="American Standard Version", source_path=corpus_file)
    assert codes(loaded) == ["asv", "kjv"]


def test_wrong_checksum_refuses_to_import(session, corpus_file):
    with pytest.raises(corpus.CorpusError) as exc:
        corpus.import_corpus(
            session, code="kjv", name="KJV", source_path=corpus_file, expected_sha256="0" * 64
        )
    assert "checksum" in str(exc.value).lower()
    assert codes(session) == []


def test_right_checksum_imports(session, corpus_file):
    digest = corpus.file_sha256(corpus_file)
    result = corpus.import_corpus(
        session, code="kjv", name="KJV", source_path=corpus_file, expected_sha256=digest
    )
    assert result["sha256"] == digest


def test_missing_book_is_rejected(session, tmp_path):
    payload = [{"abbrev": b["osis"], "name": b["name"], "chapters": [["x"]] * b["chapters"]} for b in book_table.BOOKS[:-1]]
    path = tmp_path / "short.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(corpus.CorpusError) as exc:
        corpus.import_corpus(session, code="kjv", name="KJV", source_path=path)
    assert "66" in str(exc.value) or "missing" in str(exc.value).lower()


def test_wrong_chapter_count_is_rejected(session, tmp_path):
    payload = []
    for entry in book_table.BOOKS:
        rows = [["x"]] * (entry["chapters"] - 1 if entry["osis"].lower() == "gen" else entry["chapters"])
        payload.append({"abbrev": entry["osis"], "name": entry["name"], "chapters": rows})
    path = tmp_path / "bad_chapters.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(corpus.CorpusError) as exc:
        corpus.import_corpus(session, code="kjv", name="KJV", source_path=path)
    assert "chapters" in str(exc.value).lower()


def test_blank_verse_is_rejected(session, tmp_path):
    payload = []
    for entry in book_table.BOOKS:
        rows = [["x"]] * entry["chapters"]
        if entry["osis"].lower() == "gen":
            rows[0][0] = "   "
        payload.append({"abbrev": entry["osis"], "name": entry["name"], "chapters": rows})
    path = tmp_path / "blank.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(corpus.CorpusError):
        corpus.import_corpus(session, code="kjv", name="KJV", source_path=path)


def test_require_version_names_what_is_available(session):
    with pytest.raises(ReferenceError) as exc:
        corpus.require_version(session, "nope")
    message = str(exc.value)
    assert "nope" in message
    assert "corpus_manifest.json" in message


def test_require_version_of_a_catalogued_translation_explains_how_to_install_it(loaded):
    with pytest.raises(ReferenceError) as exc:
        corpus.require_version(loaded, "esv")
    message = str(exc.value)
    assert "esv" in message
    assert "copyrighted" in message
    assert "api.bible" in message
    assert "Admin > Bible" in message


def test_a_translation_with_no_provider_still_gets_the_command(loaded, tmp_path):
    """A catalogued but unbacked translation must not point at a service."""
    manifest = tmp_path / "corpus_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "jarvis.bible.corpus",
                "versions": [
                    {
                        "code": "xyz",
                        "name": "A Translation Nobody Hosts",
                        "language": "en",
                        "license_class": "licensed",
                        "rights_holder": "Nobody",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReferenceError) as exc:
        corpus.require_version(loaded, "xyz", manifest_path=manifest)
    message = str(exc.value)
    assert "--only xyz" in message
    assert "api.bible" not in message


def test_catalogue_lists_installed_and_missing_translations(loaded):
    entries = {entry["code"]: entry for entry in corpus.catalogue(loaded)}
    assert entries["kjv"]["installed"] is True
    assert entries["kjv"]["verse_count"] > 0
    assert entries["kjv"]["note"] == ""
    assert entries["esv"]["installed"] is False
    assert entries["esv"]["license_class"] == "licensed"
    assert entries["esv"]["rights_holder"] == "Crossway"
    assert "not bundled" in entries["esv"]["note"]


def test_search_finds_text(loaded):
    hits = corpus.search_verses(loaded, "kjv", "in Gen")
    assert hits and hits[0]["reference"].startswith("Genesis 1:1")


def test_search_respects_the_book_filter(loaded):
    hits = corpus.search_verses(loaded, "kjv", "in Gen", osis="exod")
    assert hits == []


def test_search_escapes_wildcards(loaded):
    assert corpus.search_verses(loaded, "kjv", "%") == []


def test_fetch_chapter_returns_every_verse(loaded):
    chapter = corpus.fetch_chapter(loaded, "kjv", "gen", 1)
    assert [v["verse"] for v in chapter] == [1, 2, 3]


def test_whole_book_reference_returns_every_chapter(loaded):
    verses = corpus.fetch_passage(loaded, "kjv", [parse_one("gen")])
    assert len(verses) == 50 * 3