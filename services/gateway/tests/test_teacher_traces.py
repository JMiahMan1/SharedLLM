"""opencode and Cline sessions become one trace shape, with outcomes judged from evidence."""
from __future__ import annotations

import json

import teacher_samples as samples

from services.gateway.trainer import teacher_traces as traces


def _opencode() -> list[dict]:
    session = {"id": "ses_synthetic01", "directory": samples.ROOT_DIR, "model": json.dumps(samples.MODEL)}
    return traces.opencode_tasks(session, samples.opencode_messages())


def test_each_user_turn_is_a_task():
    tasks = _opencode()

    assert [t["task_index"] for t in tasks] == [0, 1, 2]
    assert tasks[0]["goal"].startswith("Fix the off-by-one")
    assert tasks[0]["model"] == "openrouter/nvidia/teacher-ultra"
    assert tasks[0]["model_location"] == "cloud"
    assert tasks[0]["project"] == "SharedLLM"
    assert tasks[0]["id"] == traces.trace_id("opencode", "ses_synthetic01", 0)


def test_steps_are_normalised_and_paths_made_relative():
    first = _opencode()[0]

    kinds = [s["kind"] for s in first["steps"]]
    assert kinds == ["read", "search", "edit", "shell", "shell"]
    assert first["steps"][0]["args"]["file_path"] == "services/gateway/pager.py"
    assert first["steps"][0]["thought"] == "Look at the pager first."
    assert first["steps"][2]["args"] == {
        "file_path": "services/gateway/pager.py", "old_text": "range(n - 1)", "new_text": "range(n)"}
    assert first["files"] == ["services/gateway/pager.py"]
    assert first["areas"] == ["services/gateway"]
    assert first["final_answer"].startswith("Fixed")


def test_outcomes_come_from_checks_and_corrections_not_claims():
    first, second, third = _opencode()

    assert first["outcome"] == "success"
    assert first["verification"] == {"test_runs": 1, "last_test_passed": True, "corrected": False}
    assert second["outcome"] == "failure"
    assert second["verification"]["corrected"] is True
    assert third["outcome"] == "unverified"


def test_a_test_run_without_an_exit_code_is_judged_by_its_output():
    passed = traces.make_step("shell", {"command": "pytest -q"}, "10 passed in 1s")
    failed = traces.make_step("shell", {"command": "pytest -q"}, "1 failed, 9 passed")
    errored = traces.make_step("shell", {"command": "npx tsc --noEmit"}, "src/a.ts(1,1): error TS2322: nope")

    assert traces.test_passed(passed) is True
    assert traces.test_passed(failed) is False
    assert traces.test_passed(errored) is False


def test_cline_sessions_use_the_same_shape():
    import pathlib
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = samples.cline_session(pathlib.Path(tmp))
        meta = json.loads((folder / f"{folder.name}.json").read_text())
        messages = json.loads((folder / f"{folder.name}.messages.json").read_text())["messages"]

    (task,) = traces.cline_tasks(meta, messages)

    assert task["source"] == "cline"
    assert task["model"] == "cline/deepseek/teacher-flash"
    assert [s["kind"] for s in task["steps"]] == ["search", "edit", "shell"]
    assert task["steps"][1]["args"]["created"] is True
    assert task["files"] == ["services/gateway/tests/test_pager_health.py"]
    assert task["outcome"] == "success"


def test_local_providers_are_local():
    assert traces.model_location("ollama-moe") == "local"
    assert traces.model_location("alpaca") == "local"
    assert traces.model_location("openrouter") == "cloud"


def test_long_outputs_are_truncated_with_a_marker():
    step = traces.make_step("shell", {"command": "cat big.log"}, "x" * 10_000)

    assert len(step["output"]) <= traces.MAX_OUTPUT_CHARS + 60
    assert "[truncated" in step["output"]
