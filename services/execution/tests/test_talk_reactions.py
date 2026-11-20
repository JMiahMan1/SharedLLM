import pytest

from services.execution.handlers import talk as talk_handler


class FakeProvider:
    username = "alex"
    password = "p"
    base_url = "https://cloud.test"

    def __init__(self):
        self.answers: list = []
        self.calls: list[dict] = []

    async def request(self, method, endpoint, *, params=None, data=None, timeout=30):
        self.calls.append({"method": method, "endpoint": endpoint, "params": params})
        return self.answers.pop(0) if self.answers else (True, [], "")


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(talk_handler, "resolve_personal_data_provider", lambda _ctx: fake)
    return fake


def request(action, **kwargs):
    from services.execution.schemas import TalkRequest

    return TalkRequest(user_context={"user": "alex"}, action=action, **kwargs)


def actor(actor_id, name, ts=1700000000):
    return {"actorType": "users", "actorId": actor_id, "actorDisplayName": name, "timestamp": ts}


# What Talk answers to POST/DELETE/GET /reactions: every emoji with the people who used it.
TALK_REACTIONS = {
    "🎉": [actor("alex", "Alex"), actor("sam", "Sam")],
    "❤️": [actor("sam", "Sam")],
}


async def test_react_posts_the_emoji_and_answers_counts_mine_and_who(provider, monkeypatch):
    calls: list[dict] = []

    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"method": method, "endpoint": endpoint, "data": data})
        return True, TALK_REACTIONS, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)

    result = await talk_handler.handle_talk(request("react", token="room-alpha", message_id=101, reaction="🎉"))

    assert result.status == "SUCCESS"
    assert calls[0]["method"] == "POST"
    assert calls[0]["endpoint"].endswith("/reactions/room-alpha/101")
    assert calls[0]["data"] == {"reaction": "🎉"}
    # Two people with the same emoji is a count of two, not one overwriting the other.
    assert result.detail["reactions"] == {"🎉": 2, "❤️": 1}
    assert result.detail["reactions_self"] == ["🎉"]
    assert [p["display_name"] for p in result.detail["reactors"]["🎉"]] == ["Alex", "Sam"]


async def test_unreact_deletes_only_my_reaction(provider, monkeypatch):
    calls: list[dict] = []

    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"method": method, "endpoint": endpoint, "params": params})
        return True, {"🎉": [actor("sam", "Sam")]}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)

    result = await talk_handler.handle_talk(request("unreact", token="room-alpha", message_id=101, reaction="🎉"))

    assert result.status == "SUCCESS"
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["endpoint"].endswith("/reactions/room-alpha/101")
    assert calls[0]["params"] == {"reaction": "🎉"}
    # Sam's stays.
    assert result.detail["reactions"] == {"🎉": 1}
    assert result.detail["reactions_self"] == []


async def test_the_last_reaction_removed_is_an_empty_list_from_talk(provider, monkeypatch):
    async def fake_talk_request(*_args, **_kwargs):
        return True, [], ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)

    result = await talk_handler.handle_talk(request("unreact", token="room-alpha", message_id=101, reaction="🎉"))

    assert result.detail == {"reactions": {}, "reactions_self": [], "reactors": {}}


async def test_reactions_lists_who_reacted(provider, monkeypatch):
    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        assert method == "GET"
        return True, TALK_REACTIONS, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)

    result = await talk_handler.handle_talk(request("reactions", token="room-alpha", message_id=101))

    assert result.status == "SUCCESS"
    assert result.detail["reactors"]["❤️"][0]["display_name"] == "Sam"


@pytest.mark.parametrize("action", ["react", "unreact"])
async def test_react_and_unreact_require_message_and_emoji(provider, action):
    result = await talk_handler.handle_talk(request(action, token="room-alpha"))
    assert result.status == "FAILURE"
    assert "required" in result.message


def talk_message(mid, text="hi", **extra):
    return {"id": mid, "token": "room-alpha", "actorType": "users", "actorId": "sam", "actorDisplayName": "Sam",
            "timestamp": 1700000000 + mid, "messageType": "comment", "message": text, **extra}


def reaction_update(mid, parent_id, system="reaction"):
    # How Talk posts a reaction: a system message carrying the emoji, whose parent is the message reacted to.
    return talk_message(mid, "🎉", messageType="system", systemMessage=system,
                        parent=talk_message(parent_id, reactions={"🎉": 1}))


async def test_a_reaction_wakes_the_long_poll_without_becoming_a_feed_line(provider):
    provider.answers = [(True, [reaction_update(12, 10), reaction_update(13, 9, "reaction_revoked")], "")]

    result = await talk_handler.handle_talk(request("wait", token="room-alpha", last_known_id=11, wait_s=5))

    assert result.status == "SUCCESS"
    # Nothing to draw, but the poll moves past both and names the messages that changed,
    # so every other member's app refetches and shows the new reaction at once.
    assert result.detail["messages"] == []
    assert result.detail["last_id"] == 13
    assert result.detail["updated"] == [9, 10]


async def test_history_leaves_reaction_lines_out_and_keeps_the_counts_on_the_message(provider):
    provider.answers = [(True, [reaction_update(12, 10), talk_message(10, reactions={"🎉": 2}, reactionsSelf=["🎉"])], "")]

    result = await talk_handler.handle_talk(request("messages", token="room-alpha"))

    assert [m["id"] for m in result.detail["messages"]] == [10]
    assert result.detail["messages"][0]["reactions"] == {"🎉": 2}
    assert result.detail["messages"][0]["reactions_self"] == ["🎉"]
    assert result.detail["last_id"] == 12


async def test_ordinary_system_lines_still_show(provider):
    provider.answers = [(True, [talk_message(14, "Sam joined the call", messageType="system", systemMessage="call_joined")], "")]

    result = await talk_handler.handle_talk(request("wait", token="room-alpha", last_known_id=13, wait_s=5))

    assert [m["id"] for m in result.detail["messages"]] == [14]
