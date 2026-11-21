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


@pytest.fixture
def workspace(monkeypatch):
    """A fake Workspace API: one file, a queue of gate results, every write recorded."""
    state = {
        "content": BROKEN,
        "writes": [],
        "gates": [],
        "read_error": None,
    }

    async def fake_read(workspace_id, path, user="default", **kwargs):
        if state["read_error"]:
            raise workspace_api.WorkspaceError(state["read_error"])
        return state["content"]

    async def fake_write(workspace_id, path, content, *, expected_sha256=None, user="default", **kwargs):
        state["writes"].append({"path": path, "content": content, "expected_sha256": expected_sha256})
        state["content"] = content
        return {"status": "SUCCESS"}

    async def fake_gate(workspace_id, targets=None, *, user="default", timeout_seconds=300):
        return state["gates"].pop(0) if state["gates"] else {"passed": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(selffix.ws, "read_file", fake_read)
    monkeypatch.setattr(selffix.ws, "write_file", fake_write)
    monkeypatch.setattr(selffix.ws, "run_pytest", fake_gate)
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
    workspace["gates"] = [{"passed": True, "stdout": "1 passed", "stderr": ""}]
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
    workspace["gates"] = [{"passed": True, "stdout": "1 passed", "stderr": ""}]
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
    workspace["gates"] = [{"passed": False, "stdout": "still failing", "stderr": ""}]

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
    workspace["gates"] = [{"passed": True, "stdout": "2 passed", "stderr": ""}]

    result = await selffix.repair_workspace("sermons", path="math_utils.py", complete_fn=None)

    assert result.status == "skipped"
    assert result.message == "the workspace's tests already pass"
    assert workspace["writes"] == []


async def test_an_unreadable_file_is_skipped_not_guessed(workspace):
    workspace["read_error"] = "Could not read nope.py: 404"

    result = await selffix.repair_workspace("sermons", path="nope.py")

    assert result.status == "skipped"
    assert "Could not read" in result.message
