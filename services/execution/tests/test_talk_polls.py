import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FERNET_KEY", "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE=")

import pytest

from services.execution.handlers import talk as talk_handler


class FakeProvider:
    username = "u"
    password = "p"
    base_url = "https://cloud.test"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(talk_handler, "resolve_personal_data_provider", lambda _ctx: FakeProvider())


def request(action, **kwargs):
    from services.execution.schemas import TalkRequest

    return TalkRequest(user_context={"user": "jeremiah"}, action=action, **kwargs)


async def test_create_poll_posts_question_and_options(provider, monkeypatch):
    calls: list[dict] = []

    async def fake(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"method": method, "endpoint": endpoint, "data": data})
        return True, {"id": 7, "question": data["question"]}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake)

    result = await talk_handler.handle_talk(
        request("create_poll", token="room-alpha", question="Dinner?", options=["Tacos", "Pizza", "  "])
    )

    assert result.status == "SUCCESS"
    assert calls[0]["method"] == "POST"
    assert calls[0]["endpoint"].endswith("/poll/room-alpha")
    # blank options are dropped, question trimmed
    assert calls[0]["data"]["options"] == ["Tacos", "Pizza"]
    assert calls[0]["data"]["maxVotes"] == 1


async def test_create_poll_needs_two_real_options(provider, monkeypatch):
    called = False

    async def fake(*_args, **_kwargs):
        nonlocal called
        called = True
        return True, {}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake)

    result = await talk_handler.handle_talk(
        request("create_poll", token="room-alpha", question="Dinner?", options=["Only one"])
    )

    assert result.status == "FAILURE"
    assert not called, "must not hit Nextcloud with an invalid poll"


async def test_vote_posts_the_option(provider, monkeypatch):
    calls: list[dict] = []

    async def fake(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"endpoint": endpoint, "data": data})
        return True, {"id": 7}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake)

    result = await talk_handler.handle_talk(request("vote_poll", token="room-alpha", poll_id=7, option_id=2))

    assert result.status == "SUCCESS"
    assert calls[0]["endpoint"].endswith("/poll/room-alpha/7")
    assert calls[0]["data"] == {"optionId": 2}


async def test_vote_requires_all_ids(provider):
    result = await talk_handler.handle_talk(request("vote_poll", token="room-alpha"))
    assert result.status == "FAILURE"
