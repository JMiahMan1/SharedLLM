"""SIM wiring in the three harnesses: read, prompt-cache safety, write-back."""
from __future__ import annotations

from services.gateway import orchestrator
from services.gateway.schemas import ResolvedCredentials
from services.gateway.trainer import selffix
from services.gateway.trainer import sim as sim_mod


def _creds(user: str = "alice", admin: bool = False) -> ResolvedCredentials:
    return ResolvedCredentials(user=user, is_admin=admin)


async def _no_injected_outcomes(*args, **kwargs):
    return None


async def test_satellite_sim_hint_goes_in_the_question_and_never_the_static_prefix(monkeypatch):
    captured: dict = {}
    queried: dict = {}

    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "TOOL GUIDE")
    monkeypatch.setattr(orchestrator, "hub_tool_guide", lambda text: text)
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")

    async def fake_context_for_turn(**kwargs):
        queried.update(kwargs)
        return "SIM-HINT-MARKER", [{"problem_id": "sim-1", "edit_or_rule": "rule"}]

    async def fake_call_ollama(payload, use_chat=True):
        captured["messages"] = payload["messages"]
        return {"message": {"content": "A plain answer."}}

    monkeypatch.setattr(sim_mod, "context_for_turn", fake_context_for_turn)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", _no_injected_outcomes)
    monkeypatch.setattr(orchestrator, "call_ollama", fake_call_ollama)

    answer = await orchestrator._single_turn_inference(
        "what is the weather",
        "qwen3-6-35b",
        "STATIC PERSONA AND TOOL GUIDE",
        "",
        [],
        _creds(),
        harness="satellite",
    )

    assert answer == "A plain answer."
    assert queried["harness"] == "satellite"
    assert queried["user_id"] == "alice"
    system = captured["messages"][0]["content"]
    question = captured["messages"][-1]["content"]
    # The cached prefix must be byte-for-byte unchanged: no dynamic SIM in it.
    assert "SIM-HINT-MARKER" not in system
    assert system.startswith("STATIC PERSONA AND TOOL GUIDE")
    assert "SIM-HINT-MARKER" in question
    # The hint travels in the per-request context section, with the question.
    assert question.index("SIM-HINT-MARKER") > question.index("[Context for this question]")


async def test_workspace_turn_queries_the_workspace_harness(monkeypatch):
    seen: list[str] = []

    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "TOOL GUIDE")
    monkeypatch.setattr(orchestrator, "hub_tool_guide", lambda text: text)
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")

    async def fake_context_for_turn(**kwargs):
        seen.append(kwargs["harness"])
        return "", []

    async def fake_call_ollama(payload, use_chat=True):
        return {"message": {"content": "done"}}

    monkeypatch.setattr(sim_mod, "context_for_turn", fake_context_for_turn)
    monkeypatch.setattr(orchestrator, "call_ollama", fake_call_ollama)

    await orchestrator._single_turn_inference(
        "fix the tests", "qwen3-6-35b", "SYSTEM", "", [], _creds(), harness="workspace"
    )
    assert seen == ["workspace"]


async def test_a_tool_error_followed_by_success_records_a_system_recovery(monkeypatch):
    answers = iter(
        [
            '{"tool": "WebSearchRequest", "query": "x"}',
            '{"tool": "WebSearchRequest", "query": "x fixed"}',
            "Here is what I found.",
        ]
    )
    recovered: list[dict] = []

    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "TOOL GUIDE")
    monkeypatch.setattr(orchestrator, "hub_tool_guide", lambda text: text)
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")

    async def fake_context_for_turn(**kwargs):
        return "", []

    async def fake_call_ollama(payload, use_chat=True):
        return {"message": {"content": next(answers)}}

    async def fake_execute(action, tool_data, query, creds, workspace_id=None):
        if "fixed" in str(tool_data):
            return "Found one result."
        return "HTTP 422 validation error: unexpected field 'q'"

    async def fake_recovery(**kwargs):
        recovered.append(kwargs)

    monkeypatch.setattr(sim_mod, "context_for_turn", fake_context_for_turn)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", _no_injected_outcomes)
    monkeypatch.setattr(sim_mod, "record_tool_recovery", fake_recovery)
    monkeypatch.setattr(orchestrator, "call_ollama", fake_call_ollama)
    monkeypatch.setattr(orchestrator, "_execute_single_tool", fake_execute)

    await orchestrator._single_turn_inference(
        "search for x", "qwen3-6-35b", "SYSTEM", "", [], _creds(), harness="satellite"
    )

    assert len(recovered) == 1
    assert recovered[0]["harness"] == "satellite"
    assert "422" in recovered[0]["error"]


async def test_selffix_prompt_carries_sim_and_success_records_the_exact_model(monkeypatch):
    recorded: list[dict] = []

    async def fake_context_for_turn(**kwargs):
        return "REPAIR-SIM-HINT", []

    async def fake_success(**kwargs):
        recorded.append(kwargs)

    async def fake_outcomes(*args, **kwargs):
        return None

    monkeypatch.setattr(sim_mod, "context_for_turn", fake_context_for_turn)
    monkeypatch.setattr(sim_mod, "record_success_lesson", fake_success)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", fake_outcomes)

    prompt = selffix.build_repair_prompt(
        "math_utils.py", "def f():\n    return 1\n", "1 failed", sim_hints="REPAIR-SIM-HINT"
    )
    assert "REPAIR-SIM-HINT" in prompt

    from services.gateway.trainer.selffix import RepairResult

    result = RepairResult(
        status="repaired",
        workspace_id="sermons",
        path="math_utils.py",
        model="qwen3-6-35b",
        initial_error="1 failed",
    )
    await selffix._record_repair_learning(result, [], passed=True)

    assert recorded[0]["model"] == "qwen3-6-35b"
    assert recorded[0]["harness"] == "workspace"
    assert "math_utils.py" in recorded[0]["signature"]


async def test_selffix_failure_records_a_negative_lesson(monkeypatch):
    failures: list[dict] = []

    async def fake_failure(**kwargs):
        failures.append(kwargs)

    async def fake_outcomes(*args, **kwargs):
        return None

    monkeypatch.setattr(sim_mod, "record_failure_lesson", fake_failure)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", fake_outcomes)

    from services.gateway.trainer.selffix import RepairResult

    result = RepairResult(
        status="unrepaired",
        workspace_id="sermons",
        path="math_utils.py",
        model="inkling-2-mini",
        initial_error="1 failed",
        final_error="still failing",
    )
    await selffix._record_repair_learning(result, [], passed=False)
    assert failures[0]["model"] == "inkling-2-mini"
    assert failures[0]["harness"] == "workspace"
