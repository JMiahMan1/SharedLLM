import os
import sys

sys.path.insert(0, os.path.abspath("."))

import pytest

from services.geo import achievements


def _earned(name="Ten K Club", description="10k steps in a day", points=5):
    definition = achievements.Achievement(
        id="ten_k",
        name=name,
        description=description,
        points=points,
        rule_type="steps",
        rule_value=10_000,
    )
    return achievements.Earned(definition, "2026-09-27", points)


class _Resp:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def json(self):
        return {"status": "SUCCESS"}


class _Client:
    def __init__(self, recorder, status=200):
        self.recorder = recorder
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None, timeout=None):
        self.recorder.append({"url": url, "json": json})
        return _Resp(self.status)


def test_nothing_is_posted_without_a_chat_token(monkeypatch):
    monkeypatch.delenv("FAMILY_CHAT_TOKEN", raising=False)
    posted: list[dict] = []

    class _Session:
        def __init__(self, *a, **k):
            pass

        def post(self, *a, **k):
            posted.append({"url": a[0]})
            return _Resp()

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    __import__("asyncio").run(achievements.announce_awards([_earned()]))
    assert posted == []


def test_a_badge_posts_a_card_to_the_family_room(monkeypatch):
    monkeypatch.setenv("FAMILY_CHAT_TOKEN", "room-9")
    posted: list[dict] = []

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _Client(posted))
    __import__("asyncio").run(achievements.announce_awards([_earned()]))

    assert len(posted) == 1
    body = posted[0]["json"]
    assert body["action"] == "post_card"
    assert body["token"] == "room-9"
    assert body["card_kind"] == "activity"
    assert body["card_title"] == "Ten K Club"
    assert body["card_stars"] == 5


def test_a_talk_outage_never_raises(monkeypatch):
    monkeypatch.setenv("FAMILY_CHAT_TOKEN", "room-9")

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def post(self, *a, **k):
            raise RuntimeError("talk is down")

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", _Boom)
    # Must not raise: the badge is already banked.
    __import__("asyncio").run(achievements.announce_awards([_earned()]))
