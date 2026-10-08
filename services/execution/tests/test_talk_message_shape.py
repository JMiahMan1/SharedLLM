"""The fields the chat panel draws from: reactions, replies and who spoke last."""

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


def test_a_message_carries_its_reactions_and_the_message_it_replies_to():
    summary = talk_handler._message_summary({
        "id": 5,
        "actorId": "michele",
        "actorDisplayName": "Michele",
        "message": "Yes!",
        "reactions": {"👍": 2},
        "reactionsSelf": ["👍"],
        "parent": {"id": 4, "actorDisplayName": "Jeremiah", "message": "Tacos tonight?"},
    })
    assert summary["reactions"] == {"👍": 2}
    assert summary["reactions_self"] == ["👍"]
    assert summary["parent"] == {"id": 4, "actor_display_name": "Jeremiah", "message": "Tacos tonight?"}


def test_a_message_without_extras_has_empty_ones():
    summary = talk_handler._message_summary({"id": 1, "message": "hi"})
    assert summary["reactions"] == {}
    assert summary["parent"] is None


def test_a_conversation_says_who_spoke_last_and_when():
    summary = talk_handler._conversation_summary({
        "token": "t", "displayName": "Family", "type": 2, "unreadMention": True,
        "lastMessage": {"message": "Dinner at 6", "actorDisplayName": "Michele", "actorId": "michele", "timestamp": 1715000000},
    })
    assert summary["last_message_actor"] == "Michele"
    assert summary["last_message_actor_id"] == "michele"
    assert summary["last_message_timestamp"] == 1715000000
    assert summary["type"] == 2
    assert summary["unread_mention"] is True


async def test_a_reply_is_sent_with_reply_to(provider, monkeypatch):
    calls: list[dict] = []

    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"data": data})
        return True, {"id": 9, "message": "Yes"}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)
    await talk_handler.handle_talk(request("send", token="room", message="Yes", reply_to=4))
    await talk_handler.handle_talk(request("send", token="room", message="Plain"))

    assert calls[0]["data"] == {"message": "Yes", "replyTo": "4"}
    assert calls[1]["data"] == {"message": "Plain"}
