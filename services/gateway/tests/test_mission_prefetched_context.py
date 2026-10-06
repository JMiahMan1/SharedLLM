"""Tests for retrieval handed to a Raven mission at dispatch time.

``AgentLoop`` has always accepted a ``rag_context`` and folded it into its system
prompt. The single-turn path populated it. The mission path never did, so a
mission briefed from a book began holding nothing at all -- which is the reason
Raven in a workspace could not see the library, and the reason the composer had
to be able to choose a mode to get retrieval back.

These tests pin the one place the omission lived, and pin that the fix is
additive: ``_fetch_rag_context`` also contributes live Home Assistant state and
workspace memory, and substituting the prefetched block for its output would
have silently thrown both away.
"""

from unittest.mock import AsyncMock, patch

import pytest

import services.gateway.agent_loop as agent_loop
import services.gateway.orchestrator as orchestrator

FETCHED = "FETCHED CONTEXT (books, home assistant, lessons)"
PREFETCHED = "PREFETCHED CONTEXT (passages the mission was launched from)"


def _job(**overrides):
    payload = {
        "query": "summarize the passages and then refactor the parser",
        "model": "some-model",
        "system": "SYSTEM",
        "creds": {"user": "testuser"},
        "_mission_id": 99,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def loop_capture(monkeypatch):
    """Capture the kwargs AgentLoop is called with, and neutralise the fetch."""
    captured: dict = {"loops": [], "singles": []}

    async def fake_agent_loop(*args, **kwargs):
        captured["loops"].append({"args": args, "kwargs": kwargs})
        return "RAVEN ANSWER"

    async def fake_single_turn(*args, **kwargs):
        captured["singles"].append({"args": args, "kwargs": kwargs})
        return "SINGLE ANSWER"

    monkeypatch.setattr(agent_loop, "AgentLoop", fake_agent_loop)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_single_turn)
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=FETCHED))
    return captured


def _rag_of(captured):
    return captured["loops"][0]["kwargs"]["rag_context"]


async def test_a_mission_without_prefetched_context_is_unchanged(loop_capture):
    """The pre-existing path must not move.

    Everything that dispatched a mission before this change relied on the
    orchestrator doing its own retrieval. If this test needed rewriting, the
    change had replaced retrieval instead of adding to it.
    """
    answer = await orchestrator.process_full_orchestration(_job())
    assert answer == "RAVEN ANSWER"
    assert _rag_of(loop_capture) == FETCHED


async def test_prefetched_context_reaches_raven(loop_capture):
    await orchestrator.process_full_orchestration(_job(rag_context=PREFETCHED))
    context = _rag_of(loop_capture)
    assert PREFETCHED in context


async def test_prefetched_context_does_not_displace_the_fetched_context(loop_capture):
    """Additive, not substitutive.

    The fetched half carries live Home Assistant state and workspace memory that
    the caller never saw. A mission that lost them because someone added a
    briefing block would be a quiet regression in behaviour that no test of the
    dispatch call would catch.
    """
    await orchestrator.process_full_orchestration(_job(rag_context=PREFETCHED))
    context = _rag_of(loop_capture)
    assert PREFETCHED in context
    assert FETCHED in context


async def test_prefetched_context_survives_an_empty_fetch(loop_capture, monkeypatch):
    """Retrieval at dispatch time can be all the context there is."""
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    await orchestrator.process_full_orchestration(_job(rag_context=PREFETCHED))
    assert _rag_of(loop_capture).strip() == PREFETCHED


async def test_blank_prefetched_context_is_treated_as_absent(loop_capture):
    """Whitespace is not context, and must not prepend blank lines to the prompt."""
    await orchestrator.process_full_orchestration(_job(rag_context="   \n  "))
    assert _rag_of(loop_capture) == FETCHED


async def test_prefetched_context_also_reaches_the_single_turn_path(loop_capture):
    """The composer can brief the Librarian the same way it briefs Raven."""
    await orchestrator.process_full_orchestration(
        _job(rag_context=PREFETCHED, _mission_id=None, query="what did he say?")
    )
    assert PREFETCHED in loop_capture["singles"][0]["args"][3]


async def test_a_mission_id_still_forces_raven_without_the_keyword(loop_capture):
    """The scar this whole area grew from must stay healed.

    A workspace-creating prompt that never says "raven" used to be routed to the
    single-turn path, which cannot create workspaces, and answered 404. The
    ``_mission_id`` check is the fix; nothing about adding context may undo it.
    """
    await orchestrator.process_full_orchestration(
        _job(query="create a workspace and deploy the service")
    )
    assert len(loop_capture["loops"]) == 1
    assert loop_capture["singles"] == []


async def test_prefetched_context_is_not_fetched_twice(loop_capture):
    """One retrieval per dispatch, not two.

    Prefetching is additive to the *context*, never a second call for the same
    answer: the caller already paid for it, and doing it again would double the
    latency of every dispatched mission for no benefit.
    """
    await orchestrator.process_full_orchestration(_job(rag_context=PREFETCHED))
    assert orchestrator._fetch_rag_context.await_count == 1