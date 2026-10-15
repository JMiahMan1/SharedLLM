

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
    # Talk takes a list, even for a single-choice poll.
    assert calls[0]["data"] == {"optionIds": [2]}


async def test_vote_requires_all_ids(provider):
    result = await talk_handler.handle_talk(request("vote_poll", token="room-alpha"))
    assert result.status == "FAILURE"


async def test_open_polls_are_found_in_the_messages_and_shaped_for_the_chat(provider, monkeypatch):
    calls: list[str] = []
    chat = [
        {"id": 50, "messageParameters": {"object": {"type": "talk-poll", "id": "9", "name": "Dinner?"}}},
        {"id": 49, "messageParameters": {"object": {"type": "talk-poll", "id": "8", "name": "Old poll"}}},
        {"id": 48, "messageParameters": []},
    ]
    polls = {
        9: {"id": 9, "question": "Dinner?", "status": 0, "options": ["Tacos", "Pizza"], "votes": {"option-0": 2}, "numVoters": 2, "votedSelf": [0]},
        8: {"id": 8, "question": "Old poll", "status": 1, "options": ["A", "B"], "votes": {}},
    }

    async def fake(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append(endpoint)
        if "/chat/" in endpoint:
            return True, chat, ""
        return True, polls[int(endpoint.rsplit("/", 1)[1])], ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake)
    result = await talk_handler.handle_talk(request("polls", token="room-alpha"))

    assert result.status == "SUCCESS"
    assert calls[0].endswith("/chat/room-alpha")
    assert result.detail["polls"] == [
        {
            "id": 9,
            "question": "Dinner?",
            "status": 0,
            "options": [{"id": 0, "label": "Tacos", "numVotes": 2}, {"id": 1, "label": "Pizza", "numVotes": 0}],
            "num_voters": 2,
            "voted_self": [0],
        }
    ], "closed polls are not pinned"


async def test_structured_bodies_go_as_json(monkeypatch):
    from services.execution import nextcloud_client

    seen = {}

    async def fake_request(method, url, **kw):
        seen.update(kw)
        return {"ok": True, "text": '{"ocs": {"meta": {"status": "ok"}, "data": {}}}', "status_code": 200}

    monkeypatch.setattr(nextcloud_client, "request", fake_request)
    await nextcloud_client.ocs_request("POST", "https://cloud.test", "u", "p", "/poll/x", data={"question": "Q", "options": ["A", "B"]})
    assert seen["json"] == {"question": "Q", "options": ["A", "B"]} and seen["data"] is None
    await nextcloud_client.ocs_request("POST", "https://cloud.test", "u", "p", "/chat/x", data={"message": "hi"})
    assert seen["data"] == {"message": "hi"} and seen["json"] is None
