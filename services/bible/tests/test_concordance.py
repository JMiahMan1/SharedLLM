"""A Strong's concordance read out of its OCR text, installed and looked up."""
from __future__ import annotations

from services.bible import concordance

HEBREW = (
    "1379 Hebrew-Aramaic Dictionary-Index to the Old Testament\n"
    "1 lis 'ab, n.m. GK: 3 [-> 2]. father, ancestor:- father [593], fathers |475], chief (2)\n"
    "2 2N 'ab (Aram.), n.m. GK: 10003 |-> 1]. father:- fathers [6], father [3]\n"
    # OCR read 3 as "8": the gap between 2 and 4 says what it has to be.
    "8 ain 'eb, n.[m.]. GK: 4. green shoot:- greenness [1], fruits [1]\n"
    "4 nnx 'abad, v. GK: 6 ]-> 5]. [Q] to perish:- perish [71],\n"
    "destroy [41], lost [9]\n"
)
GREEK = (
    "1499 Creek Dictionary-Index to the New Testament\n"
    "1 A, A, n. GK: 1. Alpha:- Alpha [4]\n"
    "2 Aapciv, Aaron. n.pr. GK: 2. Aaron:- Aaron [5]\n"
    "3 ABaSScov, Abaddon, n.pr. GK: 3 [-> 3]. Abaddon:- Abaddon [1]\n"
)
CONCORDANCE = (
    "9 Exhaustive Concordance FATHER-FATHERS\n"
    "FATHER (1) [FATHERS]\n"
    "Ge 2:24 shall a man leave his f and his mother 1\n"
    "4:20 he was the f of such as dwell in tents 1\n"
    # A misread number: H2 is never rendered "father's" -- the dictionary says so.
    "FATHER'S (1)\n"
    "Ge 9:22 saw the nakedness of his f 2\n"
    "PERISH (2)\n"
    "Est 4:16 and if I p, I p. 4\n"
    "Rev 9:11 whose name in the Hebrew tongue is A 3\n"
)
PAGES = [CONCORDANCE, HEBREW, GREEK]


def test_entries_are_read_in_number_order_through_ocr_noise():
    entries = concordance.parse(PAGES)
    assert sorted(entries) == ["G1", "G2", "G3", "H1", "H2", "H3", "H4"]
    father = entries["H1"]
    assert (father.transliteration, father.part_of_speech, father.gk) == ("'ab", "n.m", "3")
    assert father.definition == "father, ancestor"
    assert father.usage == [("father", 593), ("fathers", 475), ("chief", 2)]
    assert entries["H2"].aramaic
    assert entries["H3"].definition == "green shoot"
    assert entries["H4"].definition == "[Q] to perish"
    assert entries["H4"].usage[-1] == ("lost", 9)
    assert entries["G2"].transliteration == "Aaron"


def test_occurrences_are_kept_only_where_the_dictionary_agrees():
    entries = concordance.parse(PAGES)
    found = {(o.strongs, o.osis, o.chapter, o.verse) for o in concordance.occurrences(PAGES, entries)}
    assert ("H1", "Gen", 2, 24) in found and ("H1", "Gen", 4, 20) in found
    assert ("H4", "Esth", 4, 16) in found
    assert ("G3", "Rev", 9, 11) not in found  # "PERISH" is not how the KJV renders Abaddon
    assert not any(key[0] == "H2" for key in found)


def test_install_and_look_up(session):
    result = concordance.install(session, PAGES, source="nextcloud:/Books/Strong.pdf")
    assert result["hebrew"] == 4 and result["greek"] == 3 and result["occurrences"] == 3
    status = concordance.status(session)
    assert status["installed"] and status["source"] == "nextcloud:/Books/Strong.pdf"

    entry = concordance.entry(session, "h0001")
    assert entry["strongs"] == "H1" and entry["usage"][0] == {"word": "father", "count": 593}
    assert entry["usage_total"] == 1070 and entry["language"] == "hebrew"
    places = concordance.verses(session, "H1")
    assert places["source"] == "concordance" and not places["complete"]
    assert [v["reference"] for v in places["verses"]] == ["Genesis 2:24", "Genesis 4:20"]

    words = concordance.verse_words(session, "Gen", 2, 24)
    assert words["words"][0]["strongs"] == "H1" and words["words"][0]["definition"] == "father, ancestor"
    assert [r["strongs"] for r in concordance.search(session, "father")][:1] == ["H1"]
    assert [r["strongs"] for r in concordance.search(session, "G3")] == ["G3"]

    # Installing again replaces, never doubles.
    concordance.install(session, PAGES, source="nextcloud:/Books/Strong.pdf")
    assert concordance.status(session)["occurrences"] == 3


def test_the_interlinear_gives_the_original_word_and_every_verse(session):
    import json

    from services.bible.models import InterlinearVerse

    concordance.install(session, PAGES, source="s")
    session.add(InterlinearVerse(book="Gen", chapter=2, verse=24, language="hebrew", words=json.dumps(
        [{"w": "אָבִיו", "t": "'a-viv", "e": "his father", "s": "H0001", "l": "אָב", "d": "father"}],
        ensure_ascii=False)))
    session.commit()
    entry = concordance.entry(session, "H1")
    assert entry["lemma"] == "אָב" and entry["gloss"] == "father"
    places = concordance.verses(session, "H1")
    assert places["source"] == "interlinear" and places["complete"]
    assert places["verses"][0]["word"] == "his father"


def test_only_a_concordance_tag_counts():
    assert concordance.is_concordance_tag("Concordance") and concordance.is_concordance_tag("Strong\u2019s")
    assert not concordance.is_concordance_tag("Bible")
    assert concordance.normalise("G0025") == "G25" and concordance.normalise("H7225G") == "H7225"
    assert concordance.normalise("love") is None


def test_the_lexicon_routes(client, session):
    concordance.install(session, PAGES, source="s")
    assert client.get("/lexicon/status").json()["entries"] == 7
    entry = client.get("/lexicon/H1").json()
    assert entry["definition"] == "father, ancestor" and entry["occurrences"]["total"] == 2
    assert client.get("/lexicon/H9999").status_code == 404
    verse = client.get("/lexicon/verse", params={"ref": "Genesis 2:24"}).json()
    assert verse["reference"] == "Genesis 2:24" and verse["words"][0]["strongs"] == "H1"
    assert client.get("/lexicon/search", params={"q": "perish"}).json()["results"][0]["strongs"] == "H4"


def test_the_admin_list_says_which_tagged_concordance_is_installed(client, session, monkeypatch):
    from services.bible import main as bible_main

    books = [
        {"id": 7, "title": "The Strongest Strong's", "files": [{"format": "PDF", "path": "/Books/Text/Z/S (7)/S.pdf"}]},
        {"id": 8, "title": "Strong's (EPUB)", "files": [{"format": "EPUB", "path": "/Books/Text/Z/E (8)/E.epub"}]},
    ]

    async def fake_books():
        return books

    monkeypatch.setattr(bible_main, "_tagged_concordances", fake_books)
    concordance.install(session, PAGES, source="nextcloud:/Books/Text/Z/S (7)/S.pdf")
    listing = client.get("/admin/library/concordances").json()
    assert listing["installed"]["entries"] == 7
    assert [b["state"] for b in listing["books"]] == ["installed", "needs attention"]
    assert client.post("/admin/library/concordances/8/install").status_code == 422
    assert client.post("/admin/library/concordances/99/install").status_code == 404
