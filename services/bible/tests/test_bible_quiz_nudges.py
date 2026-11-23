"""Family quiz nudges: one gentle prompt after a counted daily quiz."""
from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from services.bible import corpus, games, quiz_nudges
from services.bible import main as bible_main
from services.bible.models import GameScore, UserBibleState
from services.bible.tests.conftest import _mini_source

TODAY = date.today().isoformat()
NOW = datetime(2026, 2, 10, 12, 0)
NOW_TODAY = "2026-02-10"


class _HouseholdResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload


class _HouseholdSession:
    def __init__(self, payload=None, status=200, fail=False):
        self._payload = payload
        self.status = status
        self.fail = fail
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("identity down")
        return _HouseholdResponse(self.status, self._payload)


def _seed(engine, *, scores=None, states=None):
    with Session(engine) as session:
        for row in scores or []:
            session.add(row)
        for username, fields in (states or {}).items():
            session.add(UserBibleState(username=username, **fields))
        session.commit()


def _words():
    return [
        "faith",
        "grace",
        "mercy",
        "light",
        "shepherd",
        "kingdom",
        "river",
        "mountain",
        "garden",
        "morning",
        "strength",
        "wisdom",
    ]


def _games_source() -> list[dict]:
    refs = json.loads(games._VERSES_PATH.read_text())["verses"]
    source = _mini_source()
    by_osis = {b["abbrev"]: b for b in source}
    words = _words()
    for n, (osis, chapter, verse) in enumerate(refs):
        rows = by_osis[osis]["chapters"][chapter - 1]
        while len(rows) < verse:
            rows.append("Placeholder.")
        w = [words[(n + k) % len(words)] for k in range(4)]
        rows[verse - 1] = f"The {w[0]} of the Lord brings {w[1]} and {w[2]} to every {w[3]} number{n}."
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
    with Session(bible_main.app.state.engine) as session:
        return [q.answer for q in games.build(session, mode, seed, "kjv")]


def test_quiz_nudge_note():
    named = quiz_nudges.quiz_nudge_note("player-a", 7, 10)
    assert named["title"] == "Bible quiz"
    assert named["body"] == "player-a just played today's Bible quiz (7/10). Your turn?"
    assert named["data"]["link"]["href"] == "/bible?tab=play" and "link" not in named

    anon = quiz_nudges.quiz_nudge_note(None, 7, 10)
    assert anon["body"] == "Someone in the family just played today's Bible quiz. Your turn?"
    assert anon["data"]["link"]["href"] == "/bible?tab=play"


def test_nudge_family_recipients_and_consent(engine, monkeypatch):
    _seed(
        engine,
        scores=[
            GameScore(
                username="player-c",
                mode="daily",
                day=NOW_TODAY,
                seed=NOW_TODAY,
                version_code="kjv",
                correct=5,
                total=10,
                counted=True,
            )
        ],
        states={"player-d": {"quiz_nudges": False}},
    )
    sent = []

    async def fake_send(body, telemetry=None, secret=None):
        sent.append(body)
        return True

    async def fake_consent(owner, viewer, scope):
        assert owner == "player-a"
        assert scope == "bible"
        return {"player-b": True, "player-e": False, "player-f": None}.get(viewer)

    async def fake_display_name(username):
        assert username == "player-a"
        return "Player A"

    monkeypatch.setattr(
        quiz_nudges.aiohttp,
        "ClientSession",
        lambda *a, **k: _HouseholdSession({"usernames": ["player-a", "player-b", "player-c", "player-d", "player-e", "player-f"]}),
    )
    monkeypatch.setattr(quiz_nudges.notify, "send", fake_send)

    n = asyncio.run(
        quiz_nudges.nudge_family(
            engine,
            "player-a",
            8,
            10,
            identity_url="http://identity:11439",
            telemetry_url="http://telemetry:11438",
            secret="s3",
            consent=fake_consent,
            display_name=fake_display_name,
            now=NOW,
        )
    )

    assert n == 3
    assert [b["user"] for b in sent] == ["player-b", "player-e", "player-f"]
    by_user = {b["user"]: b for b in sent}
    assert by_user["player-b"]["body"] == "Player A just played today's Bible quiz (8/10). Your turn?"
    assert by_user["player-e"]["body"] == "Someone in the family just played today's Bible quiz. Your turn?"
    assert by_user["player-f"]["body"] == "Someone in the family just played today's Bible quiz. Your turn?"
    for body in sent:
        assert body["category"] == "bible"
        assert body["kind"] == "bible_quiz_nudge"
        assert body["tag"] == "bible:quiz"
        assert body["once"] == f"bible-quiz:{NOW_TODAY}"
        assert body["title"] == "Bible quiz"
        assert body["data"]["url"] == "/bible?tab=play"


def test_nudge_family_quiet_hours_sends_nothing(engine, monkeypatch):
    sent = []
    household = _HouseholdSession({"usernames": ["player-a", "player-b"]})

    async def fake_send(body, telemetry=None, secret=None):
        sent.append(body)
        return True

    monkeypatch.setattr(quiz_nudges.aiohttp, "ClientSession", lambda *a, **k: household)
    monkeypatch.setattr(quiz_nudges.notify, "send", fake_send)

    n = asyncio.run(
        quiz_nudges.nudge_family(
            engine,
            "player-a",
            8,
            10,
            identity_url="http://identity:11439",
            telemetry_url="http://telemetry:11438",
            secret="s3",
            consent=lambda *a: True,
            display_name=lambda *_: "Player A",
            now=datetime(2026, 2, 10, 22, 30),
        )
    )
    assert n == 0
    assert sent == []
    assert household.calls == 0


def test_nudge_family_identity_failure_sends_nothing(engine, monkeypatch):
    sent = []

    async def fake_send(body, telemetry=None, secret=None):
        sent.append(body)
        return True

    monkeypatch.setattr(quiz_nudges.aiohttp, "ClientSession", lambda *a, **k: _HouseholdSession(fail=True))
    monkeypatch.setattr(quiz_nudges.notify, "send", fake_send)

    n = asyncio.run(
        quiz_nudges.nudge_family(
            engine,
            "player-a",
            8,
            10,
            identity_url="http://identity:11439",
            telemetry_url="http://telemetry:11438",
            secret="s3",
            consent=lambda *a: True,
            display_name=lambda *_: "Player A",
            now=NOW,
        )
    )
    assert n == 0
    assert sent == []

    monkeypatch.setattr(quiz_nudges.aiohttp, "ClientSession", lambda *a, **k: _HouseholdSession({"bad": []}, status=200))
    n = asyncio.run(
        quiz_nudges.nudge_family(
            engine,
            "player-a",
            8,
            10,
            identity_url="http://identity:11439",
            telemetry_url="http://telemetry:11438",
            secret="s3",
            consent=lambda *a: True,
            display_name=lambda *_: "Player A",
            now=NOW,
        )
    )
    assert n == 0


def test_attempt_game_schedules_nudge_only_for_counted_daily(game_client: TestClient, monkeypatch):
    scheduled = []

    async def fake_award(username, grants):
        return {"granted": sum(g["stars"] for g in grants), "status": "ok"}

    async def fake_share(username, card):
        return None

    async def fake_nudge(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    created = []

    def capture_task(coro):
        created.append(coro)
        with contextlib.suppress(Exception):
            coro.close()

        class _Done:
            def add_done_callback(self, cb):
                return None

        return _Done()

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    monkeypatch.setattr(bible_main, "_share_card", fake_share)
    monkeypatch.setattr(bible_main.quiz_nudges, "nudge_family", fake_nudge)
    monkeypatch.setattr(bible_main.asyncio, "create_task", capture_task)

    seed = date.today().isoformat()
    key = _key(game_client, "daily", seed)

    first = game_client.post(
        "/games/daily/attempt",
        params={"username": "player-a"},
        json={"seed": seed, "answers": key[:7] + [None] * 3, "duration_ms": 1000},
    )
    assert first.status_code == 200
    assert first.json()["counted"] is True
    assert len(created) == 1

    second = game_client.post(
        "/games/daily/attempt",
        params={"username": "player-a"},
        json={"seed": seed, "answers": key, "duration_ms": 1000},
    )
    assert second.status_code == 200
    assert second.json()["counted"] is False
    assert len(created) == 1

    practice = game_client.post(
        "/games/books/attempt",
        params={"username": "player-a"},
        json={"seed": "practice-seed", "answers": [None] * 8, "duration_ms": 1000},
    )
    assert practice.status_code == 200
    assert practice.json()["counted"] is False
    assert len(created) == 1
