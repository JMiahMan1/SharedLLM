"""An answer that says it did something, when no tool was called, is sent back to the model once.

Seen by voice on 2026-11-23: "Playing worship music in the office." and "I searched the web..."
with no tool call in the turn. Nothing had played and nothing had been searched.
"""
from __future__ import annotations

import pytest

from services.gateway import orchestrator
from services.gateway.device_claims import NO_TOOL_NUDGE, claims_action
from services.gateway.speech_stream import SpeechGate


@pytest.mark.parametrize("answer", [
    "Playing worship music in the office.",
    "I searched the web and found three results.",
    "Okay, playing some hymns now.",
    "I've turned off the kitchen lights.",
    "I set a timer for five minutes.",
    "Worship music is now playing in the office.",
    "Done. I added milk to the shopping list.",
    # Said with no tool call while the TV sat at 31% (2026-11-24).
    "Volume is at 50% on Office TV Cast.",
    "Yes, it's confirmed at 50% on Office TV Cast.",
    "It's now at 30 percent.",
    "I've checked, and the volume is set to 40%.",
    "I turned it down a little.",
])
def test_a_claimed_action(answer):
    assert claims_action(answer)


@pytest.mark.parametrize("answer", [
    "Would you like me to play some worship music?",
    "I can search the web for that.",
    "I'll play that for you if you like.",
    "I couldn't find that song.",
    "Sorry, I can't play that.",
    "The capital of France is Paris.",
    "Do you want me to turn on the lights?",
    "Would you like me to set the volume to 30%?",
    "I can't confirm the volume on that TV.",
    "",
])
def test_not_a_claim(answer):
    assert not claims_action(answer)


def _run(monkeypatch, replies, query="Please play some worship music"):
    prompts: list[list[dict]] = []
    executed: list[str] = []
    it = iter(replies)

    async def _call_ollama(payload, **kwargs):
        prompts.append([dict(m) for m in payload["messages"]])
        return {"message": {"content": next(it)}}

    async def _execute(action, tool_data, q, creds, workspace_id=None):
        executed.append(action)
        return "Search results: one made-up page."

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: "## Available Tools\n")
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")

    import asyncio

    answer = asyncio.run(orchestrator._single_turn_inference(
        query=query, model="test-model", system_prompt="You answer carefully.", rag_context="",
        history=[], creds=orchestrator.ResolvedCredentials(user="tester")))
    return answer, prompts, executed


def _nudges(prompts):
    return sum(1 for m in prompts[-1] if m.get("role") == "user" and m.get("content") == NO_TOOL_NUDGE)


def test_a_claim_with_no_tool_is_asked_again_and_the_new_answer_is_used(monkeypatch):
    answer, prompts, _ = _run(monkeypatch, [
        "Playing worship music in the office.",
        "I could not start any music just now.",
    ])
    assert len(prompts) == 2 and _nudges(prompts) == 1
    assert answer == "I could not start any music just now."


def test_the_nudge_happens_at_most_once(monkeypatch):
    answer, prompts, _ = _run(monkeypatch, [
        "Playing worship music in the office.",
        "Playing worship music now.",
    ])
    assert len(prompts) == 2 and _nudges(prompts) == 1
    assert answer == "Playing worship music now."


def test_no_nudge_when_a_tool_was_called(monkeypatch):
    answer, prompts, executed = _run(monkeypatch, [
        '{"tool": "WebSearchRequest", "query": "made-up topic"}',
        "I searched the web and found one page.",
    ], query="Search the web for a made-up topic")
    assert executed and len(prompts) == 2 and _nudges(prompts) == 0
    assert answer == "I searched the web and found one page."


@pytest.mark.parametrize("reply", ["Would you like me to play some worship music?", "I can search the web for that."])
def test_no_nudge_for_an_offer_or_a_question(monkeypatch, reply):
    answer, prompts, _ = _run(monkeypatch, [reply])
    assert len(prompts) == 1 and answer == reply


def test_the_voice_stream_does_not_say_a_claim_no_tool_backs():
    said: list[dict] = []

    async def emit(chunk):
        said.append(chunk)

    import asyncio

    async def go():
        gate = SpeechGate(emit, "Please play some worship music")
        await gate.begin_turn()
        await gate.feed("Playing worship music in the office.")
        await gate.end_call()
        return gate

    asyncio.run(go())
    assert not [c for c in said if c.get("type") == "speech"]

    said.clear()

    async def after_tool():
        gate = SpeechGate(emit, "Please play some worship music")
        gate.tool_called = True
        await gate.begin_turn()
        await gate.feed("Playing worship music in the office.")
        await gate.end_call()

    asyncio.run(after_tool())
    assert [c["text"] for c in said if c.get("type") == "speech"] == ["Playing worship music in the office."]
