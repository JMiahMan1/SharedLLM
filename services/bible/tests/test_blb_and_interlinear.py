"""Blue Letter Bible links in the scheme blb.org serves, and imported interlinears.

The interlinear fixtures in ``tests/data`` are verbatim excerpts of STEPBible's
TAGNT and TAHOT (CC BY 4.0), so the parser is tested on the real format.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import services.config as cfg
from services.bible import blb_links, interlinear
from services.bible import main as bible_main
from services.bible.books import BOOKS, resolve_book

DATA = Path(__file__).parent / "data"
GREEK = DATA / "tagnt_john_3_16-17.txt"
HEBREW = DATA / "tahot_gen_1_1-2.txt"

#: Every code below was fetched from blueletterbible.org as /kjv/{code}/1/1/
#: and answered with that book's first chapter (2026-10).
BLB_CODES = (
    "gen exo lev num deu jos jdg rth 1sa 2sa 1ki 2ki 1ch 2ch ezr neh est job psa pro "
    "ecc sng isa jer lam eze dan hos joe amo oba jon mic nah hab zep hag zec mal "
    "mat mar luk jhn act rom 1co 2co gal eph phl col 1th 2th 1ti 2ti tit phm heb jas "
    "1pe 2pe 1jo 2jo 3jo jde rev"
).split()


def test_every_book_has_its_blb_code_in_canonical_order():
    assert [blb_links.BLB_BOOK[b["osis"]] for b in BOOKS] == BLB_CODES


@pytest.mark.parametrize("osis,code", [(b["osis"], c) for b, c in zip(BOOKS, BLB_CODES)])
def test_each_book_links_to_its_blb_page(osis, code):
    assert blb_links.verse_path(osis, 1, 1) == f"/kjv/{code}/1/1/"


@pytest.mark.parametrize(
    "osis,chapter,verse,expected",
    # Read off blueletterbible.org's own verse ids.
    [("Gen", 1, 1, 1001), ("Gen", 2, 3, 2003), ("Ps", 119, 176, 597176), ("John", 3, 16, 1000016),
     ("John", 4, 1, 1001001), ("Rev", 22, 21, 1189021)],
)
def test_the_concordance_id_matches_blb(osis, chapter, verse, expected):
    assert blb_links.concordance_id(osis, chapter, verse) == expected


def test_an_unknown_translation_opens_in_the_kjv():
    assert blb_links.verse_path("John", 3, 16, "bsb") == "/kjv/jhn/3/16/"
    assert blb_links.verse_path("John", 3, 16, "ESV") == "/esv/jhn/3/16/"


def test_a_whole_book_links_to_its_first_verse(monkeypatch):
    monkeypatch.setattr(bible_main, "BLB_BASE_URL", "https://bible.test")
    assert bible_main.blb_link("Jude", "", "")["url"] == "https://bible.test/kjv/jde/1/1/"


def test_an_unknown_tool_is_refused(monkeypatch):
    monkeypatch.setattr(bible_main, "BLB_BASE_URL", "https://bible.test")
    with pytest.raises(Exception):
        bible_main.blb_link("John 3:16", "lexicon", "")


@pytest.mark.parametrize("token,osis", [("Is", "Isa"), ("Ezr", "Ezra"), ("Ga", "Gal"), ("He", "Heb"), ("Ob", "Obad")])
def test_one_letter_pair_abbreviations_resolve(token, osis):
    """Single aliases were written ("Is") -- a string, not a tuple -- so the
    abbreviation itself never resolved and its letters did."""
    assert resolve_book(token) == osis


def test_single_letters_are_not_books():
    assert resolve_book("e") is None and resolve_book("z") is None


# ── interlinear ─────────────────────────────────────────────────────────────


def test_the_greek_file_parses_word_by_word():
    verses = [v for v in interlinear.parse(GREEK.open(encoding="utf-8")) if not isinstance(v, str)]
    assert [(v.book, v.chapter, v.verse) for v in verses] == [("John", 3, 16), ("John", 3, 17)]
    first = verses[0].words[0]
    assert first == {"w": "οὕτως", "t": "houtōs", "e": "Thus", "s": "G3779", "g": "ADV", "l": "οὕτω, οὕτως", "d": "thus(-ly)"}
    assert verses[0].language == "greek"
    assert any(w.get("k") for w in verses[0].words), "variant words keep their edition code"


def test_the_hebrew_file_parses_and_uses_english_verse_numbers():
    verses = [v for v in interlinear.parse(HEBREW.open(encoding="utf-8")) if not isinstance(v, str)]
    assert [(v.book, v.chapter, v.verse) for v in verses] == [("Gen", 1, 1), ("Gen", 1, 2), ("Gen", 31, 55)]
    word = verses[0].words[1]
    assert word["w"] and word["t"] == "ba.Ra'" and word["s"] == "H1254A" and word["d"] == "to create"
    assert verses[0].language == "hebrew" and "/" not in verses[0].words[0]["w"]


def test_import_replaces_a_book_rather_than_duplicating_it(session: Session):
    for _ in range(2):
        result = interlinear.import_lines(session, GREEK.open(encoding="utf-8"), "tagnt.txt")
    assert result.verses == 2 and result.books == ["John"]
    rows = interlinear.verses_for(session, "John", 3, 1, 99)
    assert [r.verse for r in rows] == [16, 17]


@pytest.mark.parametrize(
    "strongs,path",
    [("G0025", "/lexicon/g25/kjv/tr/0-1/"), ("H7225G", "/lexicon/h7225/kjv/wlc/0-1/"), ("H9003", None), ("", None)],
)
def test_strongs_numbers_link_to_the_blb_lexicon(strongs, path):
    assert blb_links.lexicon_path(strongs) == path


def test_the_api_serves_an_imported_verse_and_says_when_there_is_none(client: TestClient, session: Session, monkeypatch):
    monkeypatch.setattr(bible_main, "BLB_BASE_URL", "https://bible.test")
    interlinear.import_lines(session, GREEK.open(encoding="utf-8"), "tagnt.txt")
    body = client.get("/interlinear", params={"ref": "John 3:16"}).json()
    assert body["available"] is True and [v["reference"] for v in body["verses"]] == ["John 3:16"]
    loved = body["verses"][0]["words"][2]
    assert loved["e"] == "loved" and loved["x"] == "https://bible.test/lexicon/g25/kjv/tr/0-1/"
    assert "STEPBible" in body["attribution"]
    chapter = client.get("/interlinear", params={"ref": "John 3"}).json()
    assert [v["reference"] for v in chapter["verses"]] == ["John 3:16", "John 3:17"]
    none = client.get("/interlinear", params={"ref": "Romans 8:28"}).json()
    assert none == {"ref": "Romans 8:28", "available": False, "attribution": None, "verses": []}


def test_an_uploaded_file_is_imported_and_reported(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "BIBLE_IMPORT_DIR", str(tmp_path))
    resp = client.post(
        "/admin/interlinear/upload",
        files={"file": ("TAHOT Gen-Deu.txt", HEBREW.read_bytes(), "text/plain")},
    )
    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "succeeded"
    assert body["verses"] == 3 and body["imported_books"] == ["Gen"]
    assert body["coverage"]["verse_count"] == 3
    assert (tmp_path / "interlinear" / "TAHOT Gen-Deu.txt").exists()
    assert client.get("/admin/interlinear").json()["verse_count"] == 3


def test_a_file_that_is_not_an_interlinear_is_refused(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "BIBLE_IMPORT_DIR", str(tmp_path))
    resp = client.post("/admin/interlinear/upload", files={"file": ("notes.txt", b"just some notes\n", "text/plain")})
    assert resp.status_code == 422 and resp.json()["status"] == "failed"
    wrong = client.post("/admin/interlinear/upload", files={"file": ("bible.pdf", b"%PDF", "application/pdf")})
    assert wrong.status_code == 400
