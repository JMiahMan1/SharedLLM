"""Guardian's calls to the GPU box name themselves, and background work asks for low priority.

Untagged, a phone's photo backlog reached alpaca-proxy as unknown-origin at normal priority
and swapped the household's chat model out from under a voice question.
"""
import pytest

from services.telemetry import llm


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_chat_content(session, url, payload, timeout_s, headers=None, what="the model"):
        calls.append(dict(headers or {}))
        return "ok"

    monkeypatch.setattr(llm, "chat_content", fake_chat_content)
    monkeypatch.setattr(llm.LOCAL_MODEL, "check", lambda: None)
    return calls


async def test_every_call_names_its_source(sent):
    await llm.local_chat_content(None, "http://gpu.invalid:11434/v1/chat/completions", {}, 5)
    assert sent == [{"X-Request-Source": "shared-llm/guardian"}]


async def test_background_work_asks_for_low_priority(sent):
    await llm.local_chat_content(None, "http://gpu.invalid:11434/v1/chat/completions", {}, 5,
                                 what="the local model", background=True)
    assert sent == [{"X-Request-Source": "shared-llm/guardian", "X-Priority": "low"}]
