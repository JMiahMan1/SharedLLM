"""Family sharing reads Identity's real payload, and activity cards respect it."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from services.bible import main as bible_main

#: Exactly what identity's GET /api/internal/activity-sharing returns.
IDENTITY_PAYLOAD = {
    "status": "SUCCESS",
    "users": [
        {"username": "mia", "enabled": True, "audience": "circle", "user_ids": [], "share": ["totals", "bible"]},
        {"username": "ben", "enabled": True, "audience": "users", "user_ids": ["sam"], "share": ["bible"]},
        {"username": "zoe", "enabled": True, "audience": "circle", "user_ids": [], "share": ["totals"]},
        {"username": "kim", "enabled": False, "audience": "circle", "user_ids": [], "share": ["bible"]},
    ],
}


class _Resp:
    status = 200

    async def json(self):
        return IDENTITY_PAYLOAD

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Client:
    def get(self, *a, **kw):
        return _Resp()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(bible_main, "IDENTITY_SVC_URL", "http://identity")
    monkeypatch.setattr(bible_main.aiohttp, "ClientSession", lambda *a, **kw: _Client())


@pytest.mark.parametrize(
    "owner,viewer,expected",
    [
        ("mia", "sam", True),       # circle + bible
        ("ben", "sam", True),       # named viewer
        ("ben", "tom", False),      # not named
        ("zoe", "sam", False),      # shares totals, not bible
        ("kim", "sam", False),      # sharing switched off
        ("nobody", "sam", False),   # never opted in
        ("mia", "__family__", True),
        ("ben", "__family__", False),
    ],
)
async def test_consent_reads_identitys_real_payload(identity, owner, viewer, expected):
    assert await bible_main._consent(owner, viewer, "bible") is expected


async def test_the_feed_settings_come_from_the_users_list(identity):
    assert [r["username"] for r in await bible_main._sharing_settings()] == ["mia", "ben", "zoe", "kim"]


async def test_an_unreachable_identity_is_not_consent(monkeypatch):
    monkeypatch.setattr(bible_main, "IDENTITY_SVC_URL", "")
    assert await bible_main._consent("mia", "sam", "bible") is None


def test_a_daily_quiz_card_posts_only_for_a_family_sharer(loaded_client: TestClient, monkeypatch, tmp_path):
    posted = []

    async def fake_post(cards):
        posted.extend(cards)
        return {"posted": len(cards), "status": "ok"}

    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    async def consent(owner, viewer, scope):
        return owner == "mia" and viewer == "__family__"

    monkeypatch.setattr(bible_main, "_post_cards", fake_post)
    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    monkeypatch.setattr(bible_main, "_consent", consent)
    monkeypatch.setattr(bible_main.games, "build", lambda *a, **kw: [bible_main.games.Question(0, "book_after", "p", ["A", "B", "C", "D"], "A")])
    seed = date.today().isoformat()
    for user in ("mia", "sam"):
        loaded_client.post("/games/daily/attempt", params={"username": user}, json={"seed": seed, "answers": ["A"]})
    quiz = [c for c in posted if c["card_kind"] == "game"]
    assert [c["message"] for c in quiz] == ["mia scored 1/1 on today's Bible quiz"]


def test_the_daily_card_carries_plans_and_due_memory(loaded_client: TestClient, monkeypatch):
    loaded_client.post("/plans/john-21/start", params={"username": "sam"})
    loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:2"})
    body = loaded_client.get("/daily", params={"username": "sam"}).json()
    assert body["memory_due"] == 1
    assert body["plans"][0]["name"] == "John in 21 Days" and body["plans"][0]["today"]["readings"][0]["ref"] == "John 1"


def test_every_card_kind_is_one_the_talk_handler_accepts():
    """A kind the Talk handler does not know is refused there, silently to the reader."""
    import inspect
    import re

    from services.execution.handlers import talk

    used = set(re.findall(r'"card_kind": "([a-z_]+)"', inspect.getsource(bible_main)))
    assert used and used <= set(talk.CARD_KINDS), used - set(talk.CARD_KINDS)
