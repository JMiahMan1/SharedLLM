"""A grounded turn must answer from its own retrieval instead of going to look it up.

Retrieval has already been paid for when the Librarian is handed a context block,
so advertising a search tool on that turn is an invitation to discard it.
Prompting alone did not hold: on 2026-10-06 a grounded turn was given 15,913
characters of its own book passages together with an explicit instruction not to
re-search them, called WebSearchRequest anyway, and summarised Shakespeare's
Macbeth for a question about John R. Macduff. These tests pin the two levers that
did hold -- the tools are absent from the guide, and a call is refused at
dispatch -- because either one alone leaves the door open.
"""
from __future__ import annotations

import pathlib

from services.gateway import orchestrator

GUIDE_PATH = pathlib.Path(__file__).resolve().parents[3] / "prompts" / "single_turn_tool_guide.md"
GUIDE = GUIDE_PATH.read_text(encoding="utf-8")


def test_grounding_removes_the_retrieval_tools_from_the_guide():
    grounded = orchestrator._grounded_tool_guide(GUIDE)

    assert "### ContextSearchRequest" not in grounded
    assert "### WebSearchRequest" not in grounded
    assert "### CalibreRequest" not in grounded


def test_grounding_removes_exactly_the_three_search_tools():
    grounded = orchestrator._grounded_tool_guide(GUIDE)

    assert GUIDE.count("### ") - grounded.count("### ") == 3


def test_grounding_leaves_every_other_tool_advertised():
    grounded = orchestrator._grounded_tool_guide(GUIDE)

    for tool in ("### NoteRequest", "### MediaPlayRequest", "### LightControlRequest", "### EntitySearchRequest"):
        assert tool in GUIDE, f"{tool} missing from the real guide, so this test proves nothing"
        assert tool in grounded


def test_grounding_says_why_the_tools_are_gone():
    grounded = orchestrator._grounded_tool_guide(GUIDE)

    assert orchestrator._GROUNDING_NOTICE.strip() in grounded
    assert grounded.index("## Available Tools") < grounded.index(orchestrator._GROUNDING_NOTICE.strip())
    assert "cite it" in grounded


def test_the_guide_without_context_is_left_alone():
    """Nothing to answer from means the tools are still the right way to find out."""
    assert orchestrator._GROUNDED_OMITTED_ACTIONS == (
        "contextsearchrequest",
        "websearchrequest",
        "calibrerequest",
    )


async def test_a_grounded_turn_refuses_a_retrieval_tool_call(monkeypatch):
    calls: list[str] = []
    seen_messages: list[list[dict]] = []

    async def _execute(action, tool_data, query, creds, workspace_id=None):
        calls.append(action)
        return "executed"

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "WebSearchRequest", "query": "Macduff faith poverty"}',
        "A cited answer from the retrieved passages.",
    ])

    async def _call_ollama(payload, **kwargs):
        seen_messages.append(payload["messages"])
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: GUIDE)

    answer = await orchestrator._single_turn_inference(
        query="What did Macduff say about poverty?",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="[LIBRARY] a passage about trusting God in trial",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="tester"),
        grounded=True,
    )

    assert calls == [], "the retrieval tool ran even though context was already loaded"
    assert len(seen_messages) >= 2, "the model was never told its tool call had been refused"
    handbacks = [
        m["content"]
        for turn in seen_messages
        for m in turn
        if "Tool result:" in m.get("content", "")
    ]
    assert handbacks, "no tool result was ever handed back to the model"
    assert all("not available for this turn" in h for h in handbacks)
    assert answer == "A cited answer from the retrieved passages."


async def test_an_ungrounded_turn_still_uses_the_search_tool(monkeypatch):
    calls: list[str] = []
    seen_messages: list[list[dict]] = []

    async def _execute(action, tool_data, query, creds, workspace_id=None):
        calls.append(action)
        return "Search results for 'anything': some results"

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "WebSearchRequest", "query": "anything"}',
        "A final answer.",
    ])

    async def _call_ollama(payload, **kwargs):
        seen_messages.append(payload["messages"])
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: GUIDE)

    answer = await orchestrator._single_turn_inference(
        query="What is happening in the news?",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="tester"),
    )

    assert calls == ["websearchrequest"]
    assert answer == "A final answer."
    handbacks = [
        m["content"]
        for turn in seen_messages
        for m in turn
        if "Tool result:" in m.get("content", "")
    ]
    assert handbacks, "the tool result never reached the model"
    assert "Search results for 'anything'" in handbacks[0]
