"""Bot posts go out under Jarvis's own Nextcloud account when there is one."""
from types import SimpleNamespace

import pytest

import services.execution.handlers.talk as talk


class _Account:
    def __init__(self, username, answers):
        self.username = username
        self.calls: list[tuple[str, str]] = []
        self._answers = answers

    async def request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint))
        return self._answers(endpoint)


@pytest.fixture
def accounts(monkeypatch):
    talk._BOT_UNJOINABLE.clear()
    member_posts: list[dict] = []

    async def member_post(provider, method, endpoint, **kwargs):
        member_posts.append(kwargs.get("data") or {})
        return True, {"id": 1}, ""

    monkeypatch.setattr(talk, "_talk_request_with_retry", member_post)

    def install(bot_answers, member_answers=lambda e: (True, {}, "")):
        bot = _Account("jarvis", bot_answers)
        member = _Account("michele", member_answers)
        monkeypatch.setattr(talk, "_jarvis_bot", lambda: bot)
        return bot, member, member_posts

    yield install
    talk._BOT_UNJOINABLE.clear()


async def test_a_bot_post_is_authored_by_jarvis(accounts):
    bot, member, member_posts = accounts(lambda e: (True, {"id": 5}, ""))

    ok, _, _ = await talk._post_as_jarvis(member, "room-1", {"message": "✅ Michele got it!"})

    assert ok and bot.calls == [("POST", "/ocs/v2.php/apps/spreed/api/v1/chat/room-1")]
    assert member_posts == []


async def test_the_member_adds_the_bot_once_when_it_is_not_in_the_room(accounts):
    joined = SimpleNamespace(done=False)

    def bot_answers(endpoint):
        return (True, {}, "") if joined.done else (False, None, "Not found")

    def member_answers(endpoint):
        joined.done = True
        return True, {}, ""

    bot, member, member_posts = accounts(bot_answers, member_answers)

    ok, _, _ = await talk._post_as_jarvis(member, "room-1", {"message": "hi"})

    assert ok and member.calls == [("POST", "/ocs/v2.php/apps/spreed/api/v4/room/room-1/participants")]
    assert len(bot.calls) == 2 and member_posts == []


async def test_a_room_the_bot_cannot_join_falls_back_and_is_not_retried_every_time(accounts):
    bot, member, member_posts = accounts(lambda e: (False, None, "Not found"), lambda e: (False, None, "one-to-one"))

    await talk._post_as_jarvis(member, "dm-1", {"message": "first"})
    await talk._post_as_jarvis(member, "dm-1", {"message": "second"})

    assert [p["message"] for p in member_posts] == ["first", "second"]
    assert len(bot.calls) == 1  # the second post skipped the bot


async def test_without_a_bot_account_posts_go_out_as_before(monkeypatch):
    monkeypatch.setattr(talk, "_jarvis_bot", lambda: None)
    sent: list[dict] = []

    async def member_post(provider, method, endpoint, **kwargs):
        sent.append(kwargs.get("data") or {})
        return True, {}, ""

    monkeypatch.setattr(talk, "_talk_request_with_retry", member_post)
    await talk._post_as_jarvis(object(), "room-1", {"message": "hi"})
    assert sent == [{"message": "hi"}]
