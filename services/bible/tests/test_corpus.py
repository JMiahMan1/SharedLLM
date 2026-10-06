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
from services.bible.corpus import CorpusError
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
    assert "not a translation this server knows about" in message
    assert "corpus_manifest.json" not in message


def test_require_version_of_a_catalogued_translation_explains_how_to_install_it(loaded):
    with pytest.raises(ReferenceError) as exc:
        corpus.require_version(loaded, "esv")
    message = str(exc.value)
    assert "esv" in message
    assert "Copyrighted by Crossway" in message
    assert "api.bible" in message
    assert "Admin > Bible" in message
    assert "import_corpus" not in message


def _unbacked_manifest(tmp_path):
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
    return manifest


def test_a_translation_with_no_provider_is_not_pointed_at_a_service(loaded, tmp_path):
    """A catalogued but unbacked translation must not name an online provider."""
    manifest = _unbacked_manifest(tmp_path)
    with pytest.raises(ReferenceError) as exc:
        corpus.require_version(loaded, "xyz", manifest_path=manifest)
    message = str(exc.value)
    assert "Nobody" in message
    assert "Admin > Bible" in message
    assert "api.bible" not in message
    assert "python" not in message


def test_the_shell_route_is_kept_for_the_command_line_only(tmp_path):
    """The CLI still needs to know how to install it; the API must not say so."""
    version = corpus.load_manifest(_unbacked_manifest(tmp_path))[0]
    hint = corpus._acquisition_hint(version)
    assert "--only xyz" in hint
    assert "import_corpus" in hint
    assert "import_corpus" not in corpus.reader_note(version)


def test_the_manifest_marks_one_primary_and_one_public_domain_fallback():
    assert corpus.primary_code() == "nkjv"
    assert corpus.fallback_code() == "kjv"
    marked = [v["code"] for v in corpus.load_manifest() if v.get("fallback")]
    assert marked == ["kjv"]


def test_a_fallback_is_only_used_when_it_is_actually_installed(loaded):
    """Being listed in the manifest is not the same as being readable."""
    assert corpus.default_version_code(loaded) == "kjv"
    assert corpus.fallback_code() not in {"", None}


def test_the_reader_default_falls_back_when_the_primary_is_absent(loaded, tmp_path):
    """A server with only public-domain text still opens a readable Bible."""
    manifest = tmp_path / "corpus_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "jarvis.bible.corpus",
                "versions": [
                    {"code": "kjv", "name": "KJV", "language": "en",
                     "license_class": "public_domain", "fallback": True,
                     "source_url": "https://example.invalid/kjv.json"},
                    {"code": "nkjv", "name": "NKJV", "language": "en",
                     "license_class": "licensed", "primary": True,
                     "rights_holder": "Thomas Nelson"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReferenceError):
        corpus.require_version(loaded, "nkjv", manifest_path=manifest)
    assert corpus.default_version_code(loaded, manifest_path=manifest) == "kjv"


def test_a_translation_cannot_be_both_primary_and_fallback(tmp_path):
    manifest = tmp_path / "corpus_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "jarvis.bible.corpus",
                "versions": [
                    {"code": "kjv", "name": "KJV", "language": "en",
                     "license_class": "public_domain", "source_url": "x",
                     "primary": True, "fallback": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError) as exc:
        corpus.load_manifest(manifest)
    assert "primary" in str(exc.value) and "fallback" in str(exc.value)


def test_the_install_route_is_something_a_ui_can_act_on():
    entries = {v["code"]: v for v in corpus.load_manifest()}
    assert corpus.install_route(entries["kjv"]) == {"kind": "bundled"}
    assert corpus.install_route(entries["esv"]) == {
        "kind": "provider", "provider": "api.bible"
    }
    assert corpus.install_route(entries["nkjv"]) == {"kind": "file"}


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