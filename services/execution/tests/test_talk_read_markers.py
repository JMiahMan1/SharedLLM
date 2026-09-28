import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


sys.path.insert(0, os.path.abspath("."))


def _request(**kw):
    from services.execution.schemas import TalkRequest

    base = {"user_context": {"user": "mom"}, "action": "mark_read"}
    base.update(kw)
    return TalkRequest(**base)


@pytest.mark.asyncio
async def test_mark_read_posts_the_read_marker(monkeypatch):
    import services.execution.handlers.talk as talk

    calls = []

    async def fake_request(provider, method, endpoint, **kwargs):
        calls.append((method, endpoint))
        return True, {}, ""

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)
    monkeypatch.setattr(talk, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username="mom", password="x"))

    result = await talk.handle_talk(_request(token="room-1"))

    assert result.status == "SUCCESS"
    assert result.detail["unread_messages"] == 0
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/chat/room-1/read")


@pytest.mark.asyncio
async def test_mark_read_requires_a_token(monkeypatch):
    import services.execution.handlers.talk as talk

    monkeypatch.setattr(talk, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username="mom", password="x"))

    result = await talk.handle_talk(_request())

    assert result.status == "FAILURE"
    assert "token" in result.message.lower()


@pytest.mark.asyncio
async def test_mark_read_reports_upstream_failure(monkeypatch):
    import services.execution.handlers.talk as talk

    async def fake_request(provider, method, endpoint, **kwargs):
        return False, None, "Talk said no"

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)
    monkeypatch.setattr(talk, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username="mom", password="x"))

    result = await talk.handle_talk(_request(token="room-1"))

    assert result.status == "FAILURE"
    assert "Talk said no" in result.message
