"""Self-repair must be safe before it is clever.

A model that overwrites a file with a truncated answer, or deletes the function
under test, has made the workspace worse. These tests pin the guards (fenced
code only, must compile, must keep every definition, must differ from the
failing file), the write-then-prove loop through the Workspace API, and the
promise that a failed repair restores the file it was given.
"""
from __future__ import annotations

import pytest

from services.gateway import workspace_api
from services.gateway.trainer import selffix

BROKEN = "def mean(values):\n    return sum(values) / len(values) + 1\n"
FIXED = "def mean(values):\n    return sum(values) / len(values)\n"


def _fenced(source: str) -> str:
    return f"Here is the fix:\n\n```python\n{source}```\n"


def _gate(returncode: int, stdout: str = "") -> dict:
    """What the runtime's /tests/pytest answers for one pytest exit code."""
    return {"status": "SUCCESS", "returncode": returncode, "passed": returncode == 0,
            "stdout": stdout, "stderr": ""}


@pytest.fixture
def workspace(monkeypatch):
    """A fake Workspace Runtime behind the real ``workspace_api`` functions.

    Only ``workspace_api.request`` (the single HTTP seam) is replaced, and it
    answers like the runtime does: ``/files/read`` cuts at ``max_bytes`` and says
    ``truncated``, ``/files/write`` enforces ``expected_sha256`` with a 409, and
    ``/tests/pytest`` answers each queued exit code (or a non-200 when the gate
    itself breaks). So the trainer's own use of the API is what is tested.
    """
    state = {
        "content": BROKEN,
        "writes": [],
        "gates": [],
        "read_error": None,
        "gate_status": 200,
    }

    async def fake_request(endpoint, payload, timeout=0.0, **kwargs):
        if endpoint == "/files/read":
            if state["read_error"]:
                return 404, {"detail": state["read_error"]}
            content = state["content"]
            limit = payload["max_bytes"]
            return 200, {"status": "SUCCESS", "content": content[:limit], "truncated": len(content) > limit}
        if endpoint == "/files/write":
            expected = payload.get("expected_sha256")
            if expected and expected != workspace_api.sha256(state["content"]):
                return 409, {"detail": "File contents changed"}
            state["writes"].append(
                {"path": payload["relative_path"], "content": payload["content"], "expected_sha256": expected}
            )
            state["content"] = payload["content"]
            return 200, {"status": "SUCCESS"}
        if endpoint == "/tests/pytest":
            if state["gate_status"] != 200:
                return state["gate_status"], {"detail": "pytest timed out"}
            return 200, (state["gates"].pop(0) if state["gates"] else _gate(0))
        raise AssertionError(f"unexpected workspace call {endpoint}")

    monkeypatch.setattr(workspace_api, "request", fake_request)
    return state


def test_the_answer_must_be_a_fenced_block():
    assert selffix.extract_code("no code here") is None
    assert selffix.extract_code("```python\ndef f():\n    return 1\n```") == "def f():\n    return 1\n"
    assert selffix.extract_code("```\nplain text\n```") == "plain text\n"


def test_a_candidate_that_does_not_compile_is_refused():
    reason = selffix.reject_reason("def broken(:\n    pass\n", BROKEN, "math_utils.py")

    assert reason is not None
    assert "does not compile" in reason


def test_a_candidate_that_drops_a_definition_is_refused():
    reason = selffix.reject_reason(
        "VERSION = 1\nOWNER = 'nobody'\nDESCRIPTION = 'no definitions at all here'\n",
        BROKEN,
        "math_utils.py",
    )

    assert reason is not None
    assert "removes definitions" in reason


def test_the_failing_file_and_a_short_answer_are_refused():
    assert "same as the failing file" in (selffix.reject_reason(BROKEN, BROKEN, "a.py") or "")
    assert "too short" in (selffix.reject_reason("x = 1\n", BROKEN, "a.py") or "")


def test_a_good_candidate_is_accepted():
    assert selffix.reject_reason(FIXED, BROKEN, "math_utils.py") is None


def test_the_prompt_asks_for_the_whole_file_and_keeps_the_error():
    prompt = selffix.build_repair_prompt("math_utils.py", BROKEN, "TypeError: bad", "1 failed")

    assert "ONLY the complete corrected file" in prompt
    assert "TypeError: bad" in prompt
    assert "1 failed" in prompt
    assert BROKEN in prompt


async def test_a_repair_that_passes_the_tests_is_written_and_reported(workspace):
    workspace["gates"] = [_gate(0, "1 passed")]
    answers = iter([_fenced(FIXED)])

    async def fake_complete(prompt):
        return next(answers)

    result = await selffix.repair_workspace(
        "sermons", path="math_utils.py", error_text="1 failed", complete_fn=fake_complete
    )

    assert result.status == "repaired"
    assert result.tests_passed is True
    assert workspace["writes"][0]["content"] == FIXED
    # The write is conflict-checked against what was read.
    assert workspace["writes"][0]["expected_sha256"] == workspace_api.sha256(BROKEN)


async def test_a_rejected_answer_is_retried_with_the_reason(workspace):
    workspace["gates"] = [_gate(0, "1 passed")]
    prompts: list[str] = []
    answers = iter(["no code at all", _fenced(FIXED)])

    async def fake_complete(prompt):
        prompts.append(prompt)
        return next(answers)

    result = await selffix.repair_workspace(
        "sermons", path="math_utils.py", error_text="1 failed", attempts=2, complete_fn=fake_complete
    )

    assert result.status == "repaired"
    assert result.attempts == 2
    assert any("rejected: the answer had no fenced code block" in p for p in prompts)
    assert result.guards and result.guards[0].startswith("attempt 1:")


async def test_a_repair_that_keeps_failing_restores_the_original(workspace):
    workspace["gates"] = [_gate(1, "still failing")]

    async def fake_complete(prompt):
        return _fenced(FIXED)

    result = await selffix.repair_workspace(
        "sermons", path="math_utils.py", error_text="1 failed", attempts=1, complete_fn=fake_complete
    )

    assert result.status == "unrepaired"
    assert result.tests_passed is False
    assert workspace["writes"][-1]["content"] == BROKEN, "the failing file was not restored"
    assert workspace["content"] == BROKEN


async def test_a_workspace_whose_tests_pass_is_left_alone(workspace):
    workspace["gates"] = [_gate(0, "2 passed")]

    result = await selffix.repair_workspace("sermons", path="math_utils.py", complete_fn=None)

    assert result.status == "skipped"
    assert result.message == "the workspace's tests already pass"
    assert workspace["writes"] == []


async def test_an_unreadable_file_is_skipped_not_guessed(workspace):
    workspace["read_error"] = "Could not read nope.py: 404"

    result = await selffix.repair_workspace("sermons", path="nope.py")

    assert result.status == "skipped"
    assert "Could not read" in result.message


async def test_a_file_too_big_to_show_whole_is_refused_not_trimmed(workspace):
    """The model is asked for the complete file, so it must see the complete file."""
    workspace["content"] = BROKEN + "\n".join(f"VALUE_{i} = {i}" for i in range(3000)) + "\n"
    asked: list[str] = []

    async def fake_complete(prompt):
        asked.append(prompt)
        return _fenced(FIXED)

    result = await selffix.repair_workspace(
        "sermons", path="math_utils.py", error_text="1 failed", complete_fn=fake_complete
    )

    assert result.status == "skipped"
    assert "larger than" in result.message
    assert asked == [] and workspace["writes"] == []


def test_every_largest_allowed_file_fits_the_prompt_budget():
    source = "x" * selffix.MAX_FILE_BYTES
    prompt = selffix.build_repair_prompt(
        "a.py", source, "e" * selffix.MAX_TEST_OUTPUT, "t" * selffix.MAX_TEST_OUTPUT
    )

    assert len(prompt) <= selffix.REPAIR_CONTEXT_CHARS


def test_a_candidate_that_drops_a_method_is_refused():
    original = (
        "class Parser:\n"
        "    def feed(self, text):\n        return text.split()\n\n"
        "    def close(self):\n        return None\n"
    )
    candidate = "class Parser:\n    def feed(self, text):\n        return text.split(' ')\n"

    reason = selffix.reject_reason(candidate, original, "parser.py")

    assert reason is not None and "Parser.close" in reason


def test_nested_and_conditional_definitions_are_counted():
    source = (
        "try:\n    import fast\nexcept ImportError:\n    def fallback():\n        return 1\n"
        "def outer():\n    def inner():\n        return 2\n    return inner\n"
    )

    assert selffix.all_definitions(source) == {"fallback", "outer", "outer.inner"}


async def test_a_broken_test_setup_is_an_error_not_a_bug_to_fix(workspace):
    """pytest exit 4 (usage) or 5 (no tests): nothing for a repair to prove."""
    workspace["gates"] = [_gate(5, "no tests ran")]
    asked: list[str] = []

    async def fake_complete(prompt):
        asked.append(prompt)
        return _fenced(FIXED)

    result = await selffix.repair_workspace("sermons", path="math_utils.py", complete_fn=fake_complete)

    assert result.status == "error"
    assert "exit code 5" in result.message
    assert asked == [] and workspace["writes"] == []


async def test_a_gate_that_cannot_run_is_not_read_as_failing_tests(workspace):
    workspace["gate_status"] = 500

    result = await selffix.repair_workspace("sermons", path="math_utils.py", complete_fn=None)

    assert result.status == "error"
    assert "could not run" in result.message
    assert workspace["writes"] == []


async def test_a_gate_breaking_after_a_write_restores_the_original(workspace):
    workspace["gates"] = [_gate(2, "interrupted")]

    async def fake_complete(prompt):
        return _fenced(FIXED)

    result = await selffix.repair_workspace(
        "sermons", path="math_utils.py", error_text="1 failed", attempts=2, complete_fn=fake_complete
    )

    assert result.status == "unrepaired"
    assert result.attempts == 1, "a broken gate is not retried as if the code were wrong"
    assert workspace["content"] == BROKEN


@pytest.mark.parametrize("attempts", [0, selffix.MAX_ATTEMPTS + 1])
async def test_attempts_are_bounded(workspace, attempts):
    with pytest.raises(ValueError, match="attempts"):
        await selffix.repair_workspace(
            "sermons", path="math_utils.py", error_text="x", attempts=attempts, complete_fn=None
        )


async def test_the_real_model_call_is_never_trimmed_and_asks_for_enough_tokens(workspace, monkeypatch):
    from services.gateway import orchestrator
    from services.gateway.trainer import llm

    seen: dict = {}

    async def fake_settings():
        return {"llm_local_url": "http://model-host:11434"}

    async def fake_resolve(settings, requested=None):
        return "coder:7b"

    async def fake_complete(prompt, **kwargs):
        seen.update(kwargs, prompt=prompt)
        return llm.Completion(text=_fenced(FIXED), model="coder:7b", elapsed_s=0.1, prompt_chars=len(prompt))

    monkeypatch.setattr(orchestrator, "get_llm_settings", fake_settings)
    monkeypatch.setattr(llm, "resolve_model", fake_resolve)
    monkeypatch.setattr(llm, "complete", fake_complete)

    result = await selffix.repair_workspace("sermons", path="math_utils.py", error_text="1 failed")

    assert result.status == "repaired"
    assert seen["model"] == "coder:7b"
    assert seen["context_chars"] == selffix.REPAIR_CONTEXT_CHARS
    assert len(seen["prompt"]) <= seen["context_chars"], "the prompt would have been trimmed"
    assert seen["max_tokens"] >= len(BROKEN) // 3


async def test_learning_is_recorded_even_with_an_empty_initial_error(monkeypatch):
    from services.gateway.trainer import sim as sim_mod
    from services.gateway.trainer.selffix import RepairResult

    captured: list[dict] = []

    async def fake_success(**kwargs):
        captured.append(kwargs)

    async def no_outcomes(*args, **kwargs):
        return None

    monkeypatch.setattr(sim_mod, "record_success_lesson", fake_success)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", no_outcomes)

    result = RepairResult(
        status="repaired",
        workspace_id="sermons",
        path="math_utils.py",
        model="qwen3-6-35b",
        initial_error="",
    )
    await selffix._record_repair_learning(result, [], passed=True)

    assert captured and "unknown error" in captured[0]["signature"]


async def test_sim_hints_are_dropped_before_a_repair_is_refused_for_the_budget(workspace, monkeypatch):
    from services.gateway.trainer import sim as sim_mod

    workspace["gates"] = [_gate(0, "1 passed")]
    prompts: list[str] = []

    async def fake_context_for_turn(**kwargs):
        return "SIM-HINT-" + "h" * 600, []

    async def fake_complete(prompt):
        prompts.append(prompt)
        return _fenced(FIXED)

    async def no_learning(*args, **kwargs):
        return None

    monkeypatch.setattr(sim_mod, "context_for_turn", fake_context_for_turn)
    monkeypatch.setattr(sim_mod, "record_success_lesson", no_learning)
    monkeypatch.setattr(sim_mod, "record_injected_outcomes", no_learning)
    monkeypatch.setattr(selffix, "REPAIR_CONTEXT_CHARS", 800)

    result = await selffix.repair_workspace(
        "sermons", path="math_utils.py", error_text="1 failed", complete_fn=fake_complete
    )

    assert result.status == "repaired", result.message
    assert prompts and "SIM-HINT-" not in prompts[0]
    assert prompts[0].startswith("Fix the bug in `math_utils.py`")

