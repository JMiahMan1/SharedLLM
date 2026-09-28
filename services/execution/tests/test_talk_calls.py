import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath("."))


def _request(action="call_join", **kw):
    from services.execution.schemas import TalkRequest

    base = {"user_context": {"user": "mom"}, "action": action, "token": "room-1"}
    base.update(kw)
    return TalkRequest(**base)


@pytest.fixture()
def talk(monkeypatch):
    import services.execution.handlers.talk as talk_module

    monkeypatch.setattr(
        talk_module, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username="mom", password="x")
    )
    return talk_module


def test_join_posts_and_returns_the_signalling_details(talk, monkeypatch):
    import asyncio

    calls = []

    async def fake_request(provider, method, endpoint, **kwargs):
        calls.append((method, endpoint))
        return True, {
            "call": {
                "callId": "c-1",
                "callToken": "ct-1",
                "participantType": 3,
                "signaling": {"url": "wss://cloud.test/ocs/v2.php/apps/spreed/signaling/backend", "roomId": "r-1"},
            }
        }, ""

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)

    result = asyncio.run(talk.handle_talk(_request()))

    assert result.status == "SUCCESS"
    assert calls[0] == ("POST", "/ocs/v2.php/apps/spreed/api/v4/call/room-1")
    assert result.detail["call_id"] == "c-1"
    assert result.detail["call_token"] == "ct-1"
    assert result.detail["signaling"]["url"].startswith("wss://")
    assert result.detail["in_call"] is True


def test_leave_deletes_the_call(talk, monkeypatch):
    import asyncio

    calls = []

    async def fake_request(provider, method, endpoint, **kwargs):
        calls.append((method, endpoint))
        return True, {}, ""

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)

    result = asyncio.run(talk.handle_talk(_request("call_leave")))

    assert result.status == "SUCCESS"
    assert calls[0][0] == "DELETE"
    assert result.detail["in_call"] is False


def test_a_refused_join_is_reported(talk, monkeypatch):
    import asyncio

    async def fake_request(provider, method, endpoint, **kwargs):
        return False, None, "Call not found"

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)

    result = asyncio.run(talk.handle_talk(_request()))
    assert result.status == "FAILURE"
    assert "Call not found" in result.message


def test_join_needs_a_token(talk, monkeypatch):
    import asyncio

    async def fail(*a, **k):
        raise AssertionError("must not call Talk without a token")

    monkeypatch.setattr(talk, "_talk_request_with_retry", fail)

    result = asyncio.run(talk.handle_talk(_request(token=None)))
    assert result.status == "FAILURE"
    assert "token" in result.message.lower()
