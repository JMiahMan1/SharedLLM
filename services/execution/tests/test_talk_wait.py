"""Talk's long poll: Nextcloud holds the request until something new lands."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath("."))


def _request(**kw):
    from services.execution.schemas import TalkRequest

    base = {"user_context": {"user": "dad"}, "action": "wait", "token": "room-1"}
    base.update(kw)
    return TalkRequest(**base)


@pytest.fixture()
def talk(monkeypatch):
    import services.execution.handlers.talk as talk_module

    calls: list[dict] = []
    answers: list[tuple] = []

    async def fake_request(method, endpoint, **kwargs):
        calls.append({"method": method, "endpoint": endpoint, **kwargs})
        return answers.pop(0)

    provider = SimpleNamespace(username="dad", password="x", request=fake_request)
    monkeypatch.setattr(talk_module, "resolve_personal_data_provider", lambda ctx: provider)
    talk_module.calls, talk_module.answers = calls, answers
    return talk_module


async def test_it_asks_nextcloud_to_hold_for_messages_after_the_newest(talk):
    talk.answers.append((True, [{"id": 42, "message": "❓", "actorId": "jarvis"}], ""))

    result = await talk.handle_talk(_request(last_known_id=41, wait_s=25))

    assert result.status == "SUCCESS"
    assert [m["id"] for m in result.detail["messages"]] == [42]
    params = talk.calls[0]["params"]
    assert params["lookIntoFuture"] == "1" and params["lastKnownMessageId"] == "41" and params["timeout"] == "25"
    assert talk.calls[0]["timeout"] > 25


async def test_nothing_new_is_an_empty_success(talk):
    talk.answers.append((True, [], ""))
    result = await talk.handle_talk(_request(last_known_id=41))
    assert result.status == "SUCCESS" and result.detail["messages"] == []


async def test_a_failure_is_reported_not_taken_for_quiet(talk):
    talk.answers.append((False, None, "Unauthorized"))
    result = await talk.handle_talk(_request(last_known_id=41))
    assert result.status == "FAILURE"


async def test_it_needs_a_message_to_wait_after(talk):
    result = await talk.handle_talk(_request())
    assert result.status == "FAILURE" and not talk.calls


async def test_a_304_from_nextcloud_reads_as_nothing_new(monkeypatch):
    from services.execution import nextcloud_client

    async def fake_request(method, url, **kw):
        return {"ok": False, "text": "", "status_code": 304}

    monkeypatch.setattr(nextcloud_client, "request", fake_request)
    assert await nextcloud_client.ocs_request("GET", "https://cloud.test", "u", "p", "/chat/x") == (True, [], "")
