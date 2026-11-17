"""Reading a whole Talk room for an export (the private chat's move off Talk), and deleting without leaving."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath("."))


def _request(**kw):
    from services.execution.schemas import TalkRequest

    base = {"user_context": {"user": "dad"}, "action": "messages", "token": "room-1"}
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


async def test_an_export_page_reads_back_without_marking_anything_read(talk):
    raw = [{"id": 41, "message": "x", "messageType": "comment", "actorId": "dad"}]
    talk.answers.append((True, raw, ""))
    result = await talk.handle_talk(_request(limit=200, export=True, last_known_id=42))
    assert result.status == "SUCCESS"
    assert result.detail["export"] is True and result.detail["raw"] == raw
    params = talk.calls[0]["params"]
    assert params == {"lookIntoFuture": "0", "limit": "200", "setReadMarker": "0", "markNotificationsAsRead": "0",
                      "includeLastKnown": "0", "lastKnownMessageId": "42"}


async def test_an_ordinary_read_is_unchanged(talk):
    talk.answers.append((True, [], ""))
    result = await talk.handle_talk(_request(limit=50, last_known_id=42))
    assert talk.calls[0]["params"] == {"lookIntoFuture": "0", "limit": "50"}
    assert "raw" not in result.detail


async def test_delete_only_never_leaves_the_room_instead(talk):
    talk.answers.append((False, None, "forbidden"))
    result = await talk.handle_talk(_request(action="delete_conversation", delete_only=True))
    assert result.status == "FAILURE"
    assert len(talk.calls) == 1 and talk.calls[0]["method"] == "DELETE"
    assert not talk.calls[0]["endpoint"].endswith("/participants/self")
