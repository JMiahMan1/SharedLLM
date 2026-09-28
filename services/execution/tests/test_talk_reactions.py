

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


async def test_react_posts_the_emoji_to_the_message(provider, monkeypatch):
    calls: list[dict] = []

    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"method": method, "endpoint": endpoint, "data": data})
        return True, [{"reaction": "🎉", "actor_display_name": "jeremiah"}], ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)

    result = await talk_handler.handle_talk(request("react", token="room-alpha", message_id=101, reaction="🎉"))

    assert result.status == "SUCCESS"
    assert calls[0]["method"] == "POST"
    assert calls[0]["endpoint"].endswith("/reactions/room-alpha/101")
    assert calls[0]["data"] == {"reaction": "🎉"}
    assert result.detail["reactions"][0]["reaction"] == "🎉"


async def test_reactions_reads_the_message_reactions(provider, monkeypatch):
    async def fake_talk_request(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        assert method == "GET"
        return True, [{"reaction": "❤️", "actor_display_name": "Michele"}], ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake_talk_request)

    result = await talk_handler.handle_talk(request("reactions", token="room-alpha", message_id=101))

    assert result.status == "SUCCESS"
    assert result.detail["reactions"][0]["actor_display_name"] == "Michele"


async def test_react_requires_message_and_emoji(provider):
    result = await talk_handler.handle_talk(request("react", token="room-alpha"))
    assert result.status == "FAILURE"
    assert "required" in result.message
