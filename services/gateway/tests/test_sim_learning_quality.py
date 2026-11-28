"""SIM lessons must record real failures, concrete arg fixes and user corrections."""
from __future__ import annotations

import pytest

from services.gateway import orchestrator
from services.gateway.schemas import ResolvedCredentials
from services.gateway.trainer import sim as sim_mod


def _creds(user: str = "alice") -> ResolvedCredentials:
    return ResolvedCredentials(user=user, is_admin=False)


@pytest.mark.parametrize(
    "result",
    [
        "Fixed the failing test; the error was a missing import.",
        "I found an article about error handling in Python.",
        "The word cannot appears in the middle of prose.",
        '["one", "error in the logs"]',
        '{"results": [{"content": "an error in the logs"}]}',
        "",
    ],
)
def test_prose_and_normal_results_are_not_tool_errors(result):
    assert sim_mod.looks_like_tool_error(result) is False


@pytest.mark.parametrize(
    "result",
    [
        "Error: unknown field 'q'",
        "HTTP 422 validation error: unexpected field 'q'",
        "Sorry, I couldn't reach the device.",
        "Failed to parse the response.",
        "ValidationError: field required",
        "Traceback (most recent call last):\n  ...",
        '{"status": "ERROR", "message": "boom"}',
        '{"error": "bad request"}',
        '{"success": false}',
        '{"status_code": 500, "detail": "down"}',
    ],
)
def test_structured_and_leading_failures_are_tool_errors(result):
    assert sim_mod.looks_like_tool_error(result) is True


def test_arg_difference_names_keys_and_shapes_without_values():
    diff = sim_mod.describe_arg_difference(
        {"q": "secret query", "limit": 5, "keep": True},
        {"query": "secret query", "limit": "5", "drop": 1},
    )
    assert "removed keep, q" in diff
    assert "added drop, query" in diff
    assert "limit (number -> string" in diff
    assert "secret" not in diff


async def test_record_tool_recovery_uses_the_concrete_argument_difference(monkeypatch):
    captured: dict = {}

    async def fake_record(**kwargs):
        captured.update(kwargs)
        return {"problem_id": "sim-1"}

    monkeypatch.setattr(sim_mod, "record", fake_record)

    await sim_mod.record_tool_recovery(
        user_id="alice",
        harness="satellite",
        action="WebSearchRequest",
        error="HTTP 422 validation error: unexpected field 'q'",
        model="qwen3-6-35b",
        query="search for x",
        failed_args={"q": "x"},
        working_args={"query": "x"},
    )

    assert captured["scope"] == "system"
    assert "removed q" in captured["edit_or_rule"]
    assert "added query" in captured["edit_or_rule"]
    assert "removed q" in captured["rationale"]
    assert captured["category"] == "tool_recovery"


def test_detect_user_correction_only_matches_explicit_short_corrections():
    history = [{"role": "user", "content": "search the web for the budget"}]
    assert sim_mod.detect_user_correction("what is the budget", history) is None
    assert sim_mod.detect_user_correction(
        "no, I meant use the file search", history
    )["correction"].startswith("no, I meant")
    assert sim_mod.detect_user_correction(
        "don't call the web tool, call the file tool", history
    )
    assert sim_mod.detect_user_correction("actually use the calendar tool", history)
    # Too long to be a quick correction.
    assert sim_mod.detect_user_correction("no, I meant " + "x" * 400, history) is None


async def test_user_correction_is_written_to_that_users_sim_only(monkeypatch):
    captured: dict = {}

    async def fake_record(**kwargs):
        captured.update(kwargs)
        return {"problem_id": "sim-user"}

    monkeypatch.setattr(sim_mod, "record", fake_record)

    result = await sim_mod.record_user_correction(
        user_id="Alice",
        harness="satellite",
        query="no, I meant use the file search",
        history=[{"role": "user", "content": "search the web"}],
        model="qwen3-6-35b",
    )

    assert result == {"problem_id": "sim-user"}
    assert captured["scope"] == "user"
    assert captured["user_id"] == "alice"
    assert captured["category"] == "user_correction"
    assert captured["outcome_status"] == "pending"
    assert "no, I meant use the file search" in captured["edit_or_rule"]


@pytest.mark.parametrize("user", ["household", "default", "anonymous", ""])
async def test_household_corrections_are_never_written(monkeypatch, user):
    called = False

    async def fake_record(**kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(sim_mod, "record", fake_record)
    result = await sim_mod.record_user_correction(
        user_id=user,
        harness="satellite",
        query="no, I meant use the file search",
        history=[{"role": "user", "content": "search the web"}],
    )
    assert result is None
    assert called is False


async def test_a_reply_that_mentions_failure_does_not_regress_sim(monkeypatch):
    recorded: list[dict] = []

    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "TOOL GUIDE")
    monkeypatch.setattr(orchestrator, "hub_tool_guide", lambda text: text)
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")
    monkeypatch.setattr(orchestrator, "get_long_term_memory", _empty_memory)

    async def fake_context_for_turn(**kwargs):
        return "SIM-HINT", [{"problem_id": "sim-1", "edit_or_rule": "rule"}]

    async def fake_outcomes(items, *, success, **kwargs):
        recorded.append({"items": items, "success": success})

    async def fake_correction(**kwargs):
        return None

    async def fake_call_ollama(payload, use_chat=True):
        return {"message": {"content": "Fixed the failing test. That error was a missing import."}}

    monkeypatch.setattr(sim_mod, "context_for_turn", fake_context_for_turn)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", fake_outcomes)
    monkeypatch.setattr(sim_mod, "record_user_correction", fake_correction)
    monkeypatch.setattr(orchestrator, "call_ollama", fake_call_ollama)

    answer = await orchestrator._single_turn_inference(
        "fix the tests", "qwen3-6-35b", "SYSTEM", "", [], _creds(), harness="satellite",
    )

    assert "Fixed the failing test" in answer
    assert recorded and recorded[-1]["success"] is True


async def test_an_error_shaped_reply_still_regresses_sim(monkeypatch):
    recorded: list[dict] = []

    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "TOOL GUIDE")
    monkeypatch.setattr(orchestrator, "hub_tool_guide", lambda text: text)
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")
    monkeypatch.setattr(orchestrator, "get_long_term_memory", _empty_memory)

    async def fake_context_for_turn(**kwargs):
        return "SIM-HINT", [{"problem_id": "sim-1", "edit_or_rule": "rule"}]

    async def fake_outcomes(items, *, success, **kwargs):
        recorded.append({"items": items, "success": success})

    async def fake_correction(**kwargs):
        return None

    async def fake_call_ollama(payload, use_chat=True):
        return {"message": {"content": "Error: I could not reach the tool."}}

    monkeypatch.setattr(sim_mod, "context_for_turn", fake_context_for_turn)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", fake_outcomes)
    monkeypatch.setattr(sim_mod, "record_user_correction", fake_correction)
    monkeypatch.setattr(orchestrator, "call_ollama", fake_call_ollama)

    await orchestrator._single_turn_inference(
        "do the thing", "qwen3-6-35b", "SYSTEM", "", [], _creds(), harness="satellite",
    )

    assert recorded and recorded[-1]["success"] is False


async def _empty_memory(user_id, query):
    return ""
