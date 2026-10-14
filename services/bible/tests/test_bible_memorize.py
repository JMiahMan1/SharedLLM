"""Scripture memory: the recall ladder, spaced review, and server-side grading."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from services.bible import memorize


def test_grading_forgives_case_and_punctuation_but_not_missing_words():
    verse = "Jesus wept."
    assert memorize.grade(verse, "jesus WEPT")["passed"]
    long = "In the beginning God created the heaven and the earth."
    assert memorize.grade(long, "In the beginning, God created the heaven and the earth")["passed"]
    missed = memorize.grade(long, "In the beginning God created the earth")
    assert not missed["passed"]
    assert [w["word"] for w in missed["words"] if not w["ok"]] == ["heaven", "and", "the"]


def test_extra_words_count_against_a_recall():
    assert not memorize.grade("Jesus wept.", "And then Jesus wept very loudly")["passed"]


def test_the_ladder_climbs_on_a_pass_and_drops_on_a_miss():
    today = date(2026, 10, 13)
    assert memorize.advance(0, True, today) == (1, (today + timedelta(days=1)).isoformat())
    assert memorize.advance(3, True, today) == (4, (today + timedelta(days=14)).isoformat())
    assert memorize.advance(4, True, today) == (5, (today + timedelta(days=30)).isoformat())
    assert memorize.advance(5, True, today) == (5, (today + timedelta(days=90)).isoformat())
    assert memorize.advance(3, False, today) == (2, (today + timedelta(days=1)).isoformat())
    assert memorize.advance(0, False, today) == (0, (today + timedelta(days=1)).isoformat())


def test_first_letters_and_cloze_keep_every_word():
    text = "In the beginning God created"
    assert memorize.first_letters(text) == "I_ t__ b___ G__ c___"
    assert [w["word"] for w in memorize.cloze(text, 0)] == text.split()
    assert any(w["hidden"] for w in memorize.cloze(text, 0))


def test_a_verse_is_added_once_and_is_due_today(loaded_client: TestClient):
    body = loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Gen 1:2"}).json()
    assert body["added"] == ["Genesis 1:2"]
    again = loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:2"}).json()
    assert again["added"] == [] and len(again["verses"]) == 1
    assert again["due_count"] == 1 and again["verses"][0]["stage"] == "read"


def test_a_long_or_vague_reference_is_refused(loaded_client: TestClient):
    whole = loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1"})
    assert whole.status_code == 400
    spread = loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:1-2:3"})
    assert spread.status_code == 400


def test_due_verses_carry_their_text_and_help(loaded_client: TestClient):
    loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:2"})
    due = loaded_client.get("/memorize/due", params={"username": "sam"}).json()["due"]
    assert due[0]["text"] == "Verse 1:2 in Gen."
    assert due[0]["first_letters"] and due[0]["cloze"]


def test_a_recall_is_graded_on_the_server_and_moves_the_verse(loaded_client: TestClient):
    vid = loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:2"}).json()["verses"][0]["id"]
    good = loaded_client.post(f"/memorize/{vid}/recall", params={"username": "sam"}, json={"typed": "verse 1 2 in gen"}).json()
    assert good["passed"] and good["verse"]["level"] == 1 and good["verse"]["due"] is False
    bad = loaded_client.post(f"/memorize/{vid}/recall", params={"username": "sam"}, json={"typed": "nope"}).json()
    assert not bad["passed"] and bad["verse"]["level"] == 0 and bad["verse"]["lapses"] == 1


def test_another_readers_verse_is_not_found(loaded_client: TestClient):
    vid = loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:2"}).json()["verses"][0]["id"]
    assert loaded_client.post(f"/memorize/{vid}/recall", params={"username": "mia"}, json={"typed": "x"}).status_code == 404
    assert loaded_client.delete(f"/memorize/{vid}", params={"username": "mia"}).status_code == 404


def test_a_set_adds_what_the_text_holds_and_names_what_it_skipped(loaded_client: TestClient):
    body = loaded_client.post("/memorize", params={"username": "sam"}, json={"set_id": "kids"}).json()
    assert "Genesis 1:1" in body["added"]
    kids = next(s for s in body["sets"] if s["id"] == "kids")
    assert kids["added"] == len(body["added"])


def test_mastery_earns_the_memory_badge(loaded_client: TestClient, monkeypatch):
    from services.bible import main as bible_main

    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    vid = loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:3"}).json()["verses"][0]["id"]
    earned = set()
    for _ in range(5):
        body = loaded_client.post(f"/memorize/{vid}/recall", params={"username": "sam"}, json={"typed": "Verse 1:3 in Gen."}).json()
        earned |= {b["id"] for b in body["newly_earned"]}
    assert body["verse"]["mastered"] is True
    assert "memory_mastered_1" in earned
