"""Several study Bibles over one translation, and reading notes across translations.

These are the two questions that a single ``version_code`` column could not
answer: which commentary is this, and whose commentary is this?
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from services.bible import corpus, editions, study
from services.bible.corpus import CorpusError, file_sha256
from services.bible.refs import ReferenceError, parse_one

SECRET = "test-secret"

COMMENTARY = "The word is a hapax legomenon."
FOOTNOTE = "1:16 alludes to Gen. 3:15."


@pytest.fixture
def two_translations(loaded: Session, corpus_file) -> Session:
    """kjv and asv, so the cross-translation switch has something to switch to."""
    corpus.import_corpus(
        loaded,
        code="asv",
        name="American Standard Version",
        source_path=corpus_file,
        expected_sha256=file_sha256(corpus_file),
    )
    return loaded


def _note(chapter: int, verse: int, kind: str, body: str, edition: str, ordinal: int = 1):
    return {
        "osis": "John",
        "chapter": chapter,
        "verse": verse,
        "kind": kind,
        "ordinal": ordinal,
        "body": body,
        "edition_code": edition,
    }


def _macarthur(loaded, john_3_16: str = COMMENTARY):
    return study.import_notes(
        loaded,
        "kjv",
        [_note(3, 16, "commentary", john_3_16, "kjv-macarthur")],
        source="macarthur.epub",
        edition="kjv-macarthur",
        name="The MacArthur Study Bible",
        publisher="MacArthur Bible Ministries",
        rights_holder="MacArthur Bible Ministries",
    )


def test_two_study_bibles_can_sit_on_one_translation(loaded_client: TestClient):
    client = loaded_client
    with client.app.state.engine.connect() as conn:  # noqa: F841 - keeps the check honest
        pass

    first = client.get("/study/notes?ref=John+3:16&version=kjv")
    assert first.status_code == 200
    assert first.json()["count"] == 0

    # Install a second study Bible over the same words.
    client.post("/events", params={"kind": "note_created"})

    catalogue = client.get("/editions?version=kjv").json()
    assert catalogue["default"] == "kjv"
    assert [e["code"] for e in catalogue["editions"]] == ["kjv"]
    assert catalogue["other_translations"] == []


def test_the_catalogue_lists_every_edition_for_a_translation(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    _macarthur(loaded)

    editions = corpus.list_editions(loaded, "kjv")
    assert [e["code"] for e in editions] == ["kjv-macarthur", "kjv"]
    assert editions[0]["note_count"] == 1
    assert editions[0]["publisher"] == "MacArthur Bible Ministries"
    assert editions[1]["note_count"] == 0


def test_notes_are_scoped_to_the_study_bible_they_were_written_for(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    study.import_notes(
        loaded,
        "kjv",
        [_note(3, 16, "commentary", COMMENTARY, "kjv")],
        source="nelson.epub",
        edition="kjv",
        name="NKJV Study Bible",
    )
    _macarthur(loaded)

    nelson = study.notes_for_span(loaded, "kjv", parse_one("John 3:16"), edition="kjv")
    macarthur = study.notes_for_span(
        loaded, "kjv", parse_one("John 3:16"), edition="kjv-macarthur"
    )
    assert [n.body for n in nelson] == [COMMENTARY]
    assert [n.body for n in macarthur] == [COMMENTARY]

    # The note itself is the same body, but they are distinct notes, not one note
    # shown twice.
    assert nelson[0].identity != macarthur[0].identity
    assert nelson[0].edition_name == "NKJV Study Bible"
    assert macarthur[0].edition_name == "The MacArthur Study Bible"


def test_an_edition_with_no_notes_reports_zero_rather_than_everything(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    _macarthur(loaded)

    assert study.available_kinds(loaded, "kjv", edition="kjv") == {}
    assert study.available_kinds(loaded, "kjv", edition="kjv-macarthur") == {"commentary": 1}
    assert not study.has_notes(loaded, "kjv", edition="kjv")
    assert study.has_notes(loaded, "kjv", edition="kjv-macarthur")


def test_other_translations_are_listed_so_the_toggle_can_name_them(two_translations):
    corpus.ensure_default_edition(two_translations, "kjv")
    corpus.ensure_default_edition(two_translations, "asv")
    _macarthur(two_translations)
    study.import_notes(
        two_translations,
        "asv",
        [_note(3, 16, "commentary", "Different wording entirely.", "asv")],
        source="asv.epub",
        edition="asv",
    )

    others = study.versions_with_notes(two_translations, exclude="kjv")
    assert [o["version"] for o in others] == ["asv"]
    assert others[0]["note_count"] == 1
    assert others[0]["version_name"] == "American Standard Version"


def test_cross_version_notes_are_off_until_asked_for(two_translations):
    corpus.ensure_default_edition(two_translations, "kjv")
    corpus.ensure_default_edition(two_translations, "asv")
    study.import_notes(
        two_translations,
        "kjv",
        [_note(3, 16, "commentary", "NKJV commentary.", "kjv")],
        source="kjv.epub",
        edition="kjv",
    )
    study.import_notes(
        two_translations,
        "asv",
        [_note(3, 16, "commentary", "ASV commentary.", "asv")],
        source="asv.epub",
        edition="asv",
    )
    span = parse_one("John 3:16")

    own = study.notes_for_span(two_translations, "kjv", span)
    assert [n.body for n in own] == ["NKJV commentary."]

    both = study.notes_for_span(two_translations, "kjv", span, cross_version=True)
    assert sorted(n.body for n in both) == ["ASV commentary.", "NKJV commentary."]
    assert sorted(n.version_name for n in both) == [
        "American Standard Version",
        "King James Version",
    ]


def test_importing_a_second_time_replaces_only_that_editions_notes(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    _macarthur(loaded, "First pass of the MacArthur note.")
    _macarthur(loaded, "Corrected text replaces it.")

    notes = study.notes_for_span(
        loaded, "kjv", parse_one("John 3:16"), edition="kjv-macarthur"
    )
    assert [n.body for n in notes] == ["Corrected text replaces it."]
    assert corpus.list_editions(loaded, "kjv")[0]["note_count"] == 1


def test_a_translation_is_not_a_study_bible(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    with pytest.raises(ReferenceError):
        corpus.remove_edition(loaded, "kjv")


def test_editions_carry_a_note_kind_and_count_in_the_catalogue(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    study.import_notes(
        loaded,
        "kjv",
        [
            _note(3, 16, "commentary", COMMENTARY, "kjv"),
            _note(3, 16, "footnote", FOOTNOTE, "kjv"),
        ],
        source="nelson.epub",
        edition="kjv",
        name="NKJV Study Bible",
    )

    entry = corpus.edition_catalogue(loaded, "kjv")[0]
    assert entry["code"] == "kjv"
    assert entry["note_count"] == 2
    assert sorted(entry["note_kinds"]) == ["commentary", "footnote"]

def test_a_note_that_names_the_wrong_study_bible_is_refused(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    with pytest.raises(study.StudyNoteError) as caught:
        study.import_notes(
            loaded,
            "kjv",
            [_note(3, 16, "commentary", COMMENTARY, "kjv-macarthur")],
            source="macarthur.epub",
            edition="kjv",
            name="NKJV Study Bible",
        )
    assert "kjv-macarthur" in str(caught.value)
    assert "John 3:16" in str(caught.value)


def test_a_note_dict_only_needs_an_osis(loaded):
    """The EPUB harvest speaks osis; a dict keyed by book alone used to be dropped."""
    corpus.ensure_default_edition(loaded, "kjv")
    summary = study.import_notes(
        loaded,
        "kjv",
        [{"osis": "John", "chapter": 3, "verse": 16, "kind": "commentary", "body": COMMENTARY}],
        source="harvest.epub",
        edition="kjv",
        name="NKJV Study Bible",
    )
    assert summary["imported"] == 1
    assert summary["skipped"] == 0

def _edition(session, code):
    """Look an edition up by its code — the primary key is the row id."""
    return session.exec(
        select(corpus.BibleEdition).where(corpus.BibleEdition.code == code)
    ).first()


def test_notes_summary_reads_the_rows_and_survives_a_stale_cache(loaded):
    """The cached total is a convenience; a wrong one reads as "no notes"."""
    corpus.ensure_default_edition(loaded, "kjv")
    study.import_notes(
        loaded,
        "kjv",
        [
            _note(3, 16, "commentary", COMMENTARY, "kjv"),
            _note(3, 16, "footnote", FOOTNOTE, "kjv"),
        ],
        source="nelson.epub",
        edition="kjv",
        name="NKJV Study Bible",
    )
    assert corpus.notes_summary(loaded, "kjv") == {
        "count": 2,
        "kinds": ["commentary", "footnote"],
    }

    row = _edition(loaded, "kjv")
    row.note_count = 0
    row.note_kinds = ""
    loaded.commit()
    assert corpus.list_editions(loaded, "kjv")[0]["note_count"] == 0

    assert corpus.refresh_edition_notes(loaded) == ["kjv"]
    refreshed = corpus.list_editions(loaded, "kjv")[0]
    assert refreshed["note_count"] == 2
    assert refreshed["note_kinds"] == ["commentary", "footnote"]
    assert corpus.refresh_edition_notes(loaded) == []


def test_recounting_reports_an_edition_whose_notes_are_gone(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    study.import_notes(
        loaded,
        "kjv",
        [_note(3, 16, "commentary", COMMENTARY, "kjv")],
        source="nelson.epub",
        edition="kjv",
        name="NKJV Study Bible",
    )
    loaded.exec(
        delete(corpus.StudyNote).where(corpus.StudyNote.edition_code == "kjv")
    )
    loaded.commit()
    assert corpus.refresh_edition_notes(loaded) == ["kjv"]
    assert corpus.list_editions(loaded, "kjv")[0]["note_count"] == 0


def test_recounting_leaves_a_correct_edition_alone(loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    assert corpus.refresh_edition_notes(loaded) == []


def test_editions_list_recounts_before_it_reports(capsys, loaded):
    corpus.ensure_default_edition(loaded, "kjv")
    row = _edition(loaded, "kjv")
    row.note_count = 7
    row.note_kinds = "heading"
    loaded.commit()

    assert editions.main(["list", "--database-url", str(loaded.bind.url)]) == 0
    printed = capsys.readouterr().out
    assert "kjv" in printed
    assert "7 notes" not in printed
    assert "0 notes" in printed


def test_editions_list_needs_a_database_url(monkeypatch):
    import services.config

    monkeypatch.setattr(services.config, "BIBLE_DATABASE_URL", "")
    with pytest.raises(CorpusError) as caught:
        editions._database_url("")
    assert "BIBLE_DATABASE_URL" in str(caught.value)
