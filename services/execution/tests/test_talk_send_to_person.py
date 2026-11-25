""""Send <person> a message": the one-to-one chat with them, found by the name that was said.

Every name here is made up.
"""
import asyncio
from types import SimpleNamespace

import pytest

PEOPLE = [{"id": "sam", "label": "Sam Example", "source": "users"},
          {"id": "sasha", "label": "Sasha Example", "source": "users"}]


@pytest.fixture()
def talk(monkeypatch):
    import services.execution.handlers.talk as talk_module

    monkeypatch.setattr(talk_module, "resolve_personal_data_provider",
                        lambda ctx: SimpleNamespace(username="parent", password="x", base_url="https://cloud.test"))
    calls = []

    async def fake_request(provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append((method, endpoint, params, data))
        if endpoint.endswith("/autocomplete/get"):
            return True, PEOPLE, ""
        if endpoint.endswith("/api/v4/room"):
            return True, {"token": f"dm-{data['invite']}", "displayName": data["invite"]}, ""
        return True, {"id": 7, "message": data.get("message") if data else ""}, ""

    monkeypatch.setattr(talk_module, "_talk_request_with_retry", fake_request)
    return talk_module, calls


def _send(talk_module, target, message="Call me when you can"):
    from services.execution.schemas import TalkRequest

    req = TalkRequest(user_context={"user": "parent"}, action="send", target_user=target, message=message)
    return asyncio.run(talk_module.handle_talk(req))


def test_a_first_name_opens_their_direct_chat_and_sends_from_the_asker(talk):
    talk_module, calls = talk
    result = _send(talk_module, "Sam")
    assert result.status == "SUCCESS"
    assert "Sam Example" in result.message
    assert ("POST", "/ocs/v2.php/apps/spreed/api/v4/room", None, {"roomType": "1", "invite": "sam"}) in calls
    assert calls[-1][1] == "/ocs/v2.php/apps/spreed/api/v1/chat/dm-sam"


def test_an_unknown_name_asks_instead_of_guessing(talk):
    talk_module, calls = talk
    result = _send(talk_module, "Nobody")
    assert result.status == "FAILURE"
    assert "Which one" in result.message
    assert not any(c[1].endswith("/chat/dm-sam") for c in calls)
