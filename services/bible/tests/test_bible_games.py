"""Bible games: built from the installed text, identical per seed, scored by the server."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from services.bible import corpus, games
from services.bible import main as bible_main
from services.bible.tests.conftest import _mini_source

_WORDS = "faith grace mercy light shepherd kingdom river mountain garden morning strength wisdom".split()


def _games_source() -> list[dict]:
    """The mini corpus, with real-looking sentences at every curated verse."""
    refs = json.loads(games._VERSES_PATH.read_text())["verses"]
    source = _mini_source()
    by_osis = {b["abbrev"]: b for b in source}
    for n, (osis, chapter, verse) in enumerate(refs):
        rows = by_osis[osis]["chapters"][chapter - 1]
        while len(rows) < verse:
            rows.append("Placeholder.")
        w = [_WORDS[(n + k) % len(_WORDS)] for k in range(4)]
        rows[verse - 1] = f"The {w[0]} of the Lord brings {w[1]} and {w[2]} to every {w[3]} number{n}."
        # The verse after it, for the next-verse game (unless it is curated too).
        if len(rows) <= verse:
            rows.append(f"And then the {w[3]} answered with {w[0]} again, verse after {n}.")
        elif rows[verse] == "Placeholder.":
            rows[verse] = f"And then the {w[3]} answered with {w[0]} again, verse after {n}."
    return source


@pytest.fixture
def game_client(client: TestClient, reader_db: Session, tmp_path: Path) -> TestClient:
    path = tmp_path / "games.json"
    path.write_text(json.dumps(_games_source()), encoding="utf-8")
    corpus.import_corpus(reader_db, code="kjv", name="King James Version", source_path=path)
    return client


def _key(client: TestClient, mode: str, seed: str) -> list[str]:
    """The answer key, rebuilt the way the server does it."""
    with Session(bible_main.app.state.engine) as session:
        return [q.answer for q in games.build(session, mode, seed, "kjv")]


def test_the_catalogue_lists_the_games(game_client: TestClient):
    modes = {m["mode"] for m in game_client.get("/games").json()["modes"]}
    assert {"daily", "verse_race", "which_book", "fill_blank", "books", "next_verse"} <= modes


def test_a_game_never_sends_its_answers(game_client: TestClient):
    body = game_client.get("/games/daily", params={"username": "sam"}).json()
    assert len(body["questions"]) == 10
    for q in body["questions"]:
        assert "answer" not in q and len(q["options"]) == 4 and len(set(q["options"])) == 4
        if q["kind"] == "which_book":
            assert "reference" not in q, "the reference would give the book away"


def test_the_daily_quiz_is_the_same_for_the_whole_family(game_client: TestClient):
    a = game_client.get("/games/daily", params={"username": "sam"}).json()
    b = game_client.get("/games/daily", params={"username": "mia"}).json()
    assert a["seed"] == date.today().isoformat()
    assert a["questions"] == b["questions"]


def test_every_answer_is_in_the_text_or_the_book_list(game_client: TestClient):
    with Session(bible_main.app.state.engine) as session:
        for mode in games.MODES:
            for q in games.build(session, mode, "check-1", "kjv"):
                assert q.answer in q.options or q.answer.lower() in {o.lower() for o in q.options}
                if q.kind == "fill_blank":
                    assert games.BLANK in q.text
                    assert q.text.replace(games.BLANK, q.answer, 1) in {v["text"] for v in games._known_verses(session, "kjv")}


def test_practice_games_differ_by_seed(game_client: TestClient):
    one = game_client.get("/games/which_book", params={"username": "sam", "seed": "aaa"}).json()["questions"]
    two = game_client.get("/games/which_book", params={"username": "sam", "seed": "bbb"}).json()["questions"]
    assert one != two


def test_an_attempt_is_scored_by_the_server(game_client: TestClient, monkeypatch):
    granted = []

    async def fake_award(username, grants):
        granted.extend(grants)
        return {"granted": sum(g["stars"] for g in grants), "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    seed = date.today().isoformat()
    key = _key(game_client, "daily", seed)
    answers = key[:7] + ["nope"] * 3
    body = game_client.post("/games/daily/attempt", params={"username": "sam"},
                            json={"seed": seed, "answers": answers, "duration_ms": 42000}).json()
    assert body["correct"] == 7 and body["total"] == 10 and body["counted"] is True
    assert [r["correct"] for r in body["results"]] == [True] * 7 + [False] * 3
    assert granted[0]["stars"] == 7 and granted[0]["reason"] == "game"
    assert any(b["id"] == "quiz_first" for b in body["newly_earned"])


def test_only_the_first_daily_try_counts(game_client: TestClient, monkeypatch):
    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    seed = date.today().isoformat()
    key = _key(game_client, "daily", seed)
    first = game_client.post("/games/daily/attempt", params={"username": "sam"}, json={"seed": seed, "answers": [None] * 10})
    second = game_client.post("/games/daily/attempt", params={"username": "sam"}, json={"seed": seed, "answers": key})
    assert first.json()["counted"] is True
    assert second.json()["counted"] is False and second.json()["correct"] == 10
    played = game_client.get("/games/daily", params={"username": "sam"}).json()["played_today"]
    assert played["correct"] == 0


def test_a_wrong_answer_count_is_refused(game_client: TestClient):
    resp = game_client.post("/games/books/attempt", params={"username": "sam"}, json={"seed": "x1", "answers": ["a"]})
    assert resp.status_code == 400 and "Expected 8 answers" in resp.json()["detail"]


def test_an_unknown_game_is_a_400(game_client: TestClient):
    assert game_client.get("/games/chess", params={"username": "sam"}).status_code == 400


def test_the_leaderboard_hides_members_who_do_not_share(game_client: TestClient, monkeypatch):
    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    async def consent(owner, viewer, scope):
        return owner == "mia"

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    monkeypatch.setattr(bible_main, "_consent", consent)
    seed = date.today().isoformat()
    key = _key(game_client, "daily", seed)
    for user, right in (("sam", 5), ("mia", 9), ("ben", 10)):
        game_client.post("/games/daily/attempt", params={"username": user},
                         json={"seed": seed, "answers": key[:right] + [None] * (10 - right)})
    board = game_client.get("/games/leaderboard", params={"viewer": "sam"}).json()["entries"]
    assert [(e["username"], e["correct"]) for e in board] == [("mia", 9), ("sam", 5)]
    assert board[1]["you"] is True


def test_quizzes_feed_the_quiz_badges(game_client: TestClient, monkeypatch):
    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    key = _key(game_client, "books", "perfect")
    body = game_client.post("/games/books/attempt", params={"username": "sam"}, json={"seed": "perfect", "answers": key}).json()
    earned = {b["id"] for b in body["newly_earned"]}
    assert {"quiz_first", "quiz_perfect_1"} <= earned


def test_too_little_text_is_named_not_faked(loaded_client: TestClient):
    resp = loaded_client.get("/games/which_book", params={"username": "sam"})
    assert resp.status_code == 503 and "well-known verses" in resp.json()["detail"]


def test_what_comes_next_offers_real_following_verses(game_client: TestClient):
    with Session(bible_main.app.state.engine) as session:
        questions = games.build(session, "next_verse", "nv-1", "kjv")
        for q in questions:
            assert q.kind == "next_verse" and q.answer in q.options and len(set(q.options)) == 4
            book, cv = q.reference.rsplit(" ", 1)
            chapter, verse = (int(x) for x in cv.split(":"))
            osis = next(b["osis"] for b in games.corpus.list_books(session, "kjv") if b["name"] == book)
            row = session.get(games.BibleVerse, {"version_code": "kjv", "osis": osis, "chapter": chapter, "verse": verse + 1})
            assert row.text.strip() == q.answer


def test_an_attempt_can_be_reviewed_by_its_player(game_client: TestClient, monkeypatch):
    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    seed = date.today().isoformat()
    key = _key(game_client, "daily", seed)
    game_client.post("/games/daily/attempt", params={"username": "sam"},
                     json={"seed": seed, "answers": key[:9] + ["wrong"]})
    board = game_client.get("/games/leaderboard", params={"viewer": "sam"}).json()["entries"]
    review = game_client.get(f"/games/attempts/{board[0]['attempt_id']}", params={"viewer": "sam"}).json()
    assert review["correct"] == 9 and len(review["results"]) == 10
    missed = [r for r in review["results"] if not r["correct"]]
    assert len(missed) == 1 and missed[0]["chosen"] == "wrong" and missed[0]["prompt"]


def test_someone_elses_daily_review_waits_until_you_have_played(game_client: TestClient, monkeypatch):
    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    async def consent(owner, viewer, scope):
        return True

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    monkeypatch.setattr(bible_main, "_consent", consent)
    seed = date.today().isoformat()
    key = _key(game_client, "daily", seed)
    game_client.post("/games/daily/attempt", params={"username": "mia"}, json={"seed": seed, "answers": key})
    mia = game_client.get("/games/leaderboard", params={"viewer": "sam"}).json()["entries"][0]["attempt_id"]
    early = game_client.get(f"/games/attempts/{mia}", params={"viewer": "sam"})
    assert early.status_code == 409 and "Play today's quiz first" in early.json()["detail"]
    game_client.post("/games/daily/attempt", params={"username": "sam"}, json={"seed": seed, "answers": [None] * 10})
    assert game_client.get(f"/games/attempts/{mia}", params={"viewer": "sam"}).json()["correct"] == 10


def test_a_non_sharers_attempt_is_not_found(game_client: TestClient, monkeypatch):
    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    async def consent(owner, viewer, scope):
        return False

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    monkeypatch.setattr(bible_main, "_consent", consent)
    resp = game_client.post("/games/books/attempt", params={"username": "mia"},
                            json={"seed": "x1", "answers": [None] * 8})
    assert resp.status_code == 200
    from sqlmodel import select
    with Session(bible_main.app.state.engine) as s:
        attempt = s.exec(select(bible_main.GameScore)).first().id
    assert game_client.get(f"/games/attempts/{attempt}", params={"viewer": "sam"}).status_code == 404
