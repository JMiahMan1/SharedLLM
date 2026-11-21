"""``/api/studio/diagnose`` and ``/verify``: turning "it is broken" into a cause.

The diagnostic is worth testing for one reason: its whole value is that it keeps
probing when something fails, attributes each failure to the right cause, and says
which part of the shell is at fault. A diagnostic that gives up on the first error
is just another error.
"""
from __future__ import annotations

import pytest

from services.gateway import workspace_api as ws
from services.gateway.studio import profiles, runtime

COMMANDS = {name: command for name, command, _why in runtime.DIAGNOSTIC_PROBES}


def _profile(stack: str = "python", steps: dict | None = None) -> profiles.Profile:
    return profiles.Profile(
        stack,
        stack.title(),
        "python",
        steps if steps is not None else {"test": profiles.Step("test", "python -m pytest -q")},
    )


@pytest.fixture
def answering_shell(monkeypatch):
    """A shell that answers per command, and records what was asked."""
    state: dict = {"answers": {}, "calls": []}

    async def fake_shell(workspace_id, command, *, user="default", timeout=30):
        state["calls"].append(command)
        answer = state["answers"].get(command)
        if isinstance(answer, Exception):
            raise answer
        return answer or {
            "status": "SUCCESS",
            "detail": {"returncode": 0, "stdout": "ok", "stderr": ""},
        }

    async def fake_list(workspace_id, path, **kwargs):
        # A Python project, so the detected profile has a real test step.
        return ["pyproject.toml"]

    monkeypatch.setattr(runtime, "shell", fake_shell)
    monkeypatch.setattr(runtime.ws, "list_files", fake_list)
    return state


@pytest.fixture
def workspace(monkeypatch):
    """A workspace that records writes so cleanup can be observed."""
    state: dict = {"files": {}, "deleted": []}

    async def fake_write(workspace_id, path, content, **kwargs):
        state["files"][path] = content
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runtime.ws, "write_file", fake_write)
    return state


def _healthy(state) -> None:
    """Every probe answers 0 with plausible output."""
    for _name, command, _why in runtime.DIAGNOSTIC_PROBES:
        state["answers"][command] = {
            "status": "SUCCESS",
            "detail": {"returncode": 0, "stdout": "ok", "stderr": ""},
        }


async def test_a_healthy_shell_reports_no_failures(answering_shell):
    _healthy(answering_shell)

    report = await runtime.diagnose("ws1")

    assert report["ok"] is True
    assert report["failed"] == []
    assert report["hint"] == "The sandbox shell is healthy."
    assert {probe["name"] for probe in report["probes"]} == set(COMMANDS)


async def test_a_failing_probe_does_not_stop_the_others(answering_shell):
    _healthy(answering_shell)
    answering_shell["answers"][COMMANDS["bash"]] = {
        "status": "ERROR",
        "message": "bash: not found",
        "detail": {"returncode": 127, "stdout": "", "stderr": "bash: not found"},
    }

    report = await runtime.diagnose("ws1")

    assert report["ok"] is False
    assert report["failed"] == ["bash"]
    assert "no working /bin/bash" in report["hint"]
    assert len(report["probes"]) == len(runtime.DIAGNOSTIC_PROBES)


async def test_an_unwritable_home_is_named_as_the_cause(answering_shell):
    _healthy(answering_shell)
    # The probe itself exits non-zero when HOME cannot be written, so the failure
    # is reported by the probe rather than inferred from the text.
    answering_shell["answers"][COMMANDS["home"]] = {
        "status": "ERROR",
        "detail": {"returncode": 1, "stdout": "/home/sharedllm (NOT writable)", "stderr": ""},
    }

    report = await runtime.diagnose("ws1")

    assert "home" in report["failed"]
    assert "HOME is /home/sharedllm" in report["hint"]
    assert "npm" in report["hint"]


async def test_a_probe_that_raises_is_reported_not_propagated(answering_shell):
    _healthy(answering_shell)
    answering_shell["answers"][COMMANDS["git"]] = ws.WorkspaceError("sandbox gone")

    report = await runtime.diagnose("ws1")

    git_probe = next(p for p in report["probes"] if p["name"] == "git")
    assert git_probe["ok"] is False
    assert "sandbox gone" in git_probe["output"]
    assert report["ok"] is False


async def test_a_missing_profile_is_reported_without_losing_the_probes(answering_shell, monkeypatch):
    _healthy(answering_shell)

    async def no_files(workspace_id, path, **kwargs):
        raise ws.WorkspaceError("no such workspace")

    monkeypatch.setattr(runtime.ws, "list_files", no_files)

    report = await runtime.diagnose("ws1")

    assert report["profile"] is None
    assert "no such workspace" in report["profile_error"]
    assert len(report["probes"]) == len(runtime.DIAGNOSTIC_PROBES)


# --- verify: does code actually run here, and do the tests pass? --------------

async def test_verify_proves_a_program_ran_and_cleaned_up_after_itself(
    answering_shell, workspace
):
    _healthy(answering_shell)
    answering_shell["answers"]["python3 _studio_probe.py"] = {
        "status": "SUCCESS",
        "detail": {"returncode": 0, "stdout": "studio-ok from python", "stderr": ""},
    }

    verdict = await runtime.verify("ws1")

    assert verdict["ok"] is True
    assert verdict["program"]["ok"] is True
    assert "studio-ok from python" in verdict["program"]["output"]
    assert workspace["files"]["_studio_probe.py"].startswith("print(")
    # a probe is not a deliverable: it must not be left in the repository
    assert "rm -f _studio_probe.py" in answering_shell["calls"]


async def test_verify_names_a_program_that_cannot_run(answering_shell, workspace):
    _healthy(answering_shell)
    answering_shell["answers"]["python3 _studio_probe.py"] = {
        "status": "ERROR",
        "detail": {"returncode": 1, "stdout": "", "stderr": "PermissionError: /home/sharedllm"},
    }

    verdict = await runtime.verify("ws1")

    assert verdict["ok"] is False
    assert "`python3 _studio_probe.py` failed" in verdict["hint"]
    assert "PermissionError" in verdict["hint"]
    assert "rm -f _studio_probe.py" in answering_shell["calls"]


async def test_verify_reports_failing_tests_with_their_output(answering_shell, workspace):
    _healthy(answering_shell)
    answering_shell["answers"]["python -m pytest -q"] = {
        "status": "ERROR",
        "detail": {"returncode": 1, "stdout": "", "stderr": "1 failed, 4 passed"},
    }

    verdict = await runtime.verify("ws1")

    assert verdict["ok"] is False
    assert verdict["tests"]["ok"] is False
    assert "1 failed, 4 passed" in verdict["hint"]


async def test_verify_is_honest_when_a_stack_has_no_tests(answering_shell, monkeypatch, workspace):
    _healthy(answering_shell)

    async def static_profile(workspace_id, *, user="default"):
        return profiles.Profile("static", "Static site", "html", {})

    monkeypatch.setattr(runtime, "profile_for", static_profile)
    answering_shell["answers"]["python3 _studio_probe.py"] = {
        "status": "SUCCESS",
        "detail": {"returncode": 0, "stdout": "studio-ok from python", "stderr": ""},
    }

    verdict = await runtime.verify("ws1")

    assert verdict["tests"]["available"] is False
    assert verdict["ok"] is True
    assert "nothing to run" in verdict["tests"]["message"]


async def test_a_broken_shell_is_not_mistaken_for_failing_tests(answering_shell, workspace):
    for _name, command, _why in runtime.DIAGNOSTIC_PROBES:
        answering_shell["answers"][command] = {
            "status": "ERROR",
            "detail": {"returncode": 127, "stdout": "", "stderr": "no such command"},
        }

    verdict = await runtime.verify("ws1")

    assert verdict["shell"]["ok"] is False
    assert verdict["ok"] is False
    assert "identity" in verdict["shell"]["failed"]
    # the tests were never reached, so they are not blamed
    assert verdict["tests"] is not None and verdict["tests"]["ok"] is True

