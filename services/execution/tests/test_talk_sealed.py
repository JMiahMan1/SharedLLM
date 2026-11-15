"""A private chat's encrypted message passes through execution untouched.

It is never judged as a game answer, never a stop word, never a question for
Jarvis, and never part of the room context the assistant reads.
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath("."))

SEALED = '\U0001f512 Encrypted message\n\n```jarvis-envelope\n{"kind":"sealed","title":"Encrypted message","v":1,"iv":"AAAA","ct":"QUJD"}\n```'


@pytest.fixture()
def talk(monkeypatch):
    import services.execution.handlers.talk as talk_module

    monkeypatch.setattr(talk_module, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username="alex"))
    calls: list[tuple] = []

    async def fake_request(provider, method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        if method == "GET":
            return True, [
                {"id": 1, "actorDisplayName": "Sam", "message": SEALED},
                {"id": 2, "actorDisplayName": "Sam", "message": "hello"},
            ], ""
        return True, {"id": 9}, ""

    monkeypatch.setattr(talk_module, "_talk_request_with_retry", fake_request)
    talk_module.calls = calls
    return talk_module


def test_sealed_messages_are_posted_and_nothing_else_happens(talk, monkeypatch):
    from services.execution.schemas import TalkRequest

    judge = MagicMock(return_value=None)
    monkeypatch.setattr(talk, "_judge_game_answer", judge)
    orchestrate = AsyncMock()
    monkeypatch.setattr(talk, "run_jarvis_orchestration", orchestrate)
    # Even ciphertext that happens to start like a question for Jarvis.
    sealed_question = "@Jarvis " + SEALED
    for message in (SEALED, sealed_question):
        result = asyncio.run(talk.handle_talk(TalkRequest(
            user_context={"user": "alex"}, action="send", token="room1", message=message)))
        assert result.status == "SUCCESS"
    judge.assert_not_called()
    orchestrate.assert_not_called()
    assert [c[2]["data"]["message"] for c in talk.calls] == [SEALED, sealed_question]


def test_room_context_leaves_sealed_messages_out(talk):
    turns = asyncio.run(talk._room_context(SimpleNamespace(), "room1"))
    assert turns == [{"role": "user", "content": "Sam: hello"}]
