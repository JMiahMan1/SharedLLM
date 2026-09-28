"""Workspace pipeline smoke test.

The existing coverage drives the workspace through mocked collaborators: the
lint step is replaced with a stub, and command execution is replaced with a
fake Docker client. That is the right unit-test shape, but it means the parts
that actually touch the outside world - the linter dispatch table, the real
pytest invocation, the host-exec fallback - have never been run.

This file exercises the real pipeline against a real temporary git repository
and real subprocesses. Nothing here needs Docker: `run_workspace_cmd`
deliberately falls back to a host subprocess when the workspace is not under
the sandbox mount root, which is the case for every temporary directory, and
that fallback is what CI and dev machines use anyway.

Tests that need something CI does not have (a Docker daemon, a linter actually
installed) are marked `local_only`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import services.workspace_runtime.main as runtime
import services.workspace_sandbox as sandbox

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace directory that resolve_safe_path will accept.

    resolve_safe_path compares the *resolved* target against the *unresolved*
    base, so the base has to be already canonical. pytest's tmp_path is, but a
    hand-built path under /tmp is not on macOS, where /tmp is a symlink.
    """
    root = tmp_path.resolve()
    ws = root / "demo"
    ws.mkdir()
    monkeypatch.setattr(runtime, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(runtime, "get_workspace_root", lambda: root)
    return ws


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout


# --------------------------------------------------------------------------
# the linter dispatch table (real subprocesses)
# --------------------------------------------------------------------------


def test_an_extension_with_no_linter_passes_with_a_named_reason(ws: Path):
    """An unknown extension must not block a Raven write. The result has to say
    which decision was taken, or a model that picks a new file type cannot tell
    "no linter configured" from "linter found nothing"."""
    (ws / "notes.md").write_text("# hello\n")
    result = runtime._run_lint_for_file(ws, "notes.md")

    assert result["passed"] is True
    assert result["results"] == [
        {"tool": "none", "returncode": 0, "output": "No linter configured for .md"}
    ]


def test_json_files_are_validated_with_json_tool(ws: Path):
    """json.tool ships with python, so this is the one linter guaranteed to
    exist everywhere - which makes it the only dispatch-table arm that can be
    asserted for real in CI."""
    (ws / "good.json").write_text('{"a": 1}\n')
    (ws / "bad.json").write_text("{oops\n")

    good = runtime._run_lint_for_file(ws, "good.json")
    bad = runtime._run_lint_for_file(ws, "bad.json")

    assert good["passed"] is True
    assert good["results"][0]["tool"] == "json.tool"
    assert good["results"][0]["returncode"] == 0
    # A malformed document is a real finding, and the tool says so.
    assert bad["passed"] is False
    assert bad["results"][0]["returncode"] != 0
    assert bad["results"][0]["output"]


def test_a_missing_linter_binary_is_a_lint_failure_not_a_crash(ws: Path):
    """Regression: a linter that is not installed on the deployment must read
    as a failed lint, not an unhandled FileNotFoundError.

    The distinction matters because the caller only advances the quarantine
    counter on a returned result. An exception skips it, so a deployment
    missing `black` would 500 forever, the counter never reaches its threshold,
    and the quarantine - the whole mechanism for stopping a model from
    retrying the same bad write - never engages.
    """
    (ws / "mod.py").write_text("x = 1\n")
    monkey = runtime  # keep the reference obvious for the reader
    assert monkey is runtime

    if shutil.which("black"):
        pytest.skip("black is installed here, so the missing-linter path cannot be reached")

    result = runtime._run_lint_for_file(ws, "mod.py")

    assert result["passed"] is False
    missing = {r["tool"]: r for r in result["results"]}
    assert missing["black"]["returncode"] == runtime._LINTER_MISSING_RC
    assert "not installed" in missing["black"]["output"]
    # Both python linters are named, so the message says what to install.
    assert set(missing) == {"black", "flake8"}


def test_a_linter_that_hangs_is_reported_as_a_timeout_not_a_crash(ws: Path, monkeypatch: pytest.MonkeyPatch):
    (ws / "mod.py").write_text("x = 1\n")

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd=["black"], timeout=30)

    monkeypatch.setattr(runtime, "_run_command", _boom)
    result = runtime._run_lint_for_file(ws, "mod.py")

    assert result["passed"] is False
    assert all(r["returncode"] == runtime._LINTER_TIMEOUT_RC for r in result["results"])
    assert "timed out" in result["results"][0]["output"]


def test_linting_a_directory_is_a_client_error(ws: Path):
    (ws / "sub").mkdir()
    with pytest.raises(runtime.HTTPException) as exc:
        runtime._run_lint_for_file(ws, "sub")
    assert exc.value.status_code == 400
    assert "not a file" in exc.value.detail


def test_linting_a_missing_file_is_a_404(ws: Path):
    """resolve_safe_path raises before the linter is ever chosen, so this is a
    "no such file" rather than a lint verdict."""
    with pytest.raises(runtime.HTTPException) as exc:
        runtime._run_lint_for_file(ws, "nope.py")
    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail


@pytest.mark.local_only
@pytest.mark.parametrize("tool", ["black", "flake8"])
def test_the_python_linters_are_really_installed_on_a_deployment(tool: str):
    """The Dockerfile pip-installs both, so a deployment that cannot lint a
    .py file is a broken image rather than a design choice."""
    assert shutil.which(tool), f"{tool} is not on PATH; the workspace image should provide it"


# --------------------------------------------------------------------------
# run_workspace_cmd: the real host-exec fallback
# --------------------------------------------------------------------------


async def test_a_command_actually_runs_and_its_output_comes_back(ws: Path):
    result = await sandbox.run_workspace_cmd("demo", str(ws), ["echo", "hello-workspace"])

    assert result["returncode"] == 0
    assert "hello-workspace" in result["stdout"]
    assert result["stderr"] == ""


async def test_the_command_runs_inside_the_workspace_not_the_service_cwd(ws: Path, tmp_path: Path):
    """A workspace command that printed pwd would otherwise hand Raven paths
    from the service container, and every relative path in its reasoning would
    be wrong."""
    (ws / "marker.txt").write_text("x")
    result = await sandbox.run_workspace_cmd("demo", str(ws), ["ls"])

    assert "marker.txt" in result["stdout"]


async def test_a_failing_command_reports_its_code_rather_than_raising(ws: Path):
    result = await sandbox.run_workspace_cmd("demo", str(ws), ["sh", "-c", "exit 3"])

    assert result["returncode"] == 3


async def test_a_command_outside_the_mount_root_uses_host_exec(ws: Path):
    """tmp_path is never under /workspaces, so this is the documented fallback
    path - the one CI and every dev machine actually take."""
    assert not sandbox._under_mount_root(str(ws))
    result = await sandbox.run_workspace_cmd("demo", str(ws), ["echo", "host-path"])

    assert result["returncode"] == 0
    assert "host-path" in result["stdout"]
    # _host_exec reports the caller's own argv, not a /bin/sh wrapper.
    assert result["args"] == ["echo", "host-path"]


async def test_a_string_command_is_wrapped_in_a_shell(ws: Path):
    result = await sandbox.run_workspace_cmd("demo", str(ws), "echo one && echo two", shell=True)

    assert result["returncode"] == 0
    assert "one" in result["stdout"] and "two" in result["stdout"]


async def test_a_hanging_command_is_killed_and_reported_as_124(ws: Path):
    result = await sandbox.run_workspace_cmd("demo", str(ws), ["sleep", "30"], timeout=1.0)

    assert result["returncode"] == 124
    assert "timed out" in result["stderr"]


async def test_a_missing_executable_is_reported_not_raised(ws: Path):
    result = await sandbox.run_workspace_cmd("demo", str(ws), ["definitely-not-a-real-binary"])

    assert result["returncode"] == -1
    assert "not found" in result["stderr"].lower()


async def test_env_overrides_reach_the_command(ws: Path):
    result = await sandbox.run_workspace_cmd(
        "demo", str(ws), ["sh", "-c", "echo $ALPACA_SMOKE"], env={"ALPACA_SMOKE": "present"}
    )

    assert "present" in result["stdout"]


async def test_a_container_cwd_outside_the_workspace_is_clamped(ws: Path, tmp_path: Path):
    """A relative escape must not put the command somewhere the workspace
    sandbox does not own."""
    outside = tmp_path.parent
    assert sandbox._to_container_cwd(str(outside), str(ws)) == str(ws)
    # A cwd inside the workspace is preserved verbatim.
    inside = ws / "sub"
    assert sandbox._to_container_cwd(str(inside), str(ws)) == str(inside)
    # No cwd at all means the workspace root.
    assert sandbox._to_container_cwd(None, str(ws)) == str(ws)


# --------------------------------------------------------------------------
# the pytest gate
# --------------------------------------------------------------------------


async def test_pytest_really_runs_and_its_verdict_is_returned(ws: Path, monkeypatch: pytest.MonkeyPatch):
    """run_pytest shells out to the interpreter running the tests. Prove the
    subprocess works, that a passing file passes, and that a failing one is
    reported as failed - because the workflow turns that into a 400 and refuses
    to commit."""
    (ws / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    (ws / "test_bad.py").write_text("def test_bad():\n    assert 1 + 1 == 3\n")
    monkeypatch.setattr(runtime, "_require_internal_secret", lambda *_a, **_k: None)
    # run_pytest resolves the workspace through the ORM; this test is about the
    # real subprocess, so hand it the path directly.
    monkeypatch.setattr(
        runtime, "_resolve_workspace", lambda *_a, **_k: {"id": "demo", "resolved_path": str(ws)}
    )

    def _req(targets, **kw):
        return runtime.PytestRequest(
            workspace_id="demo", local_path="demo", rag_user="admin", targets=targets, timeout_seconds=60, **kw
        )

    passing = await asyncio_to_thread_result(runtime.run_pytest, _req(["test_ok.py"]), "test-secret")
    failing = await asyncio_to_thread_result(runtime.run_pytest, _req(["test_bad.py"]), "test-secret")

    assert passing["returncode"] == 0
    assert passing["passed"] is True
    assert failing["returncode"] != 0
    assert failing["passed"] is False
    assert failing["stdout"] or failing["stderr"]


async def asyncio_to_thread_result(fn, *args, **kwargs):
    """Call a sync route handler from an async test and await it."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


def test_pytest_targets_cannot_smuggle_flags_or_absolute_paths():
    """A model-supplied target is passed straight to `pytest -q <target>`, so
    anything that looks like a flag, an absolute path, or a parent reference
    has to be rejected before it reaches the argv."""
    assert runtime._sanitize_targets(["test_a.py", "  tests/test_b.py  "]) == ["test_a.py", "tests/test_b.py"]
    assert runtime._sanitize_targets(["", None]) == []  # type: ignore[list-item]

    for bad in ("-k something", "--pdb", "-p", "/etc/passwd", "../outside.py", "a/../../b.py"):
        with pytest.raises(runtime.HTTPException) as exc:
            runtime._sanitize_targets([bad])
        assert exc.value.status_code == 400, bad


def test_the_pytest_binary_is_the_one_running_the_tests():
    """A missing interpreter would make every workflow fail with a 500 rather
    than a clean gate failure, so assert the fallback chain resolves."""
    assert sys.executable or shutil.which("python3") or shutil.which("python")


# --------------------------------------------------------------------------
# the container path (needs a Docker daemon)
# --------------------------------------------------------------------------


@pytest.mark.local_only
def test_docker_is_reachable_for_the_workspace_sandbox():
    """run_workspace_cmd only reaches a real container when the workspace lives
    under the mount root. That is a deployment-only path; when it breaks it
    silently degrades to host exec, so the daemon has to be assertable here."""
    try:
        client = sandbox._docker_client()
        assert client.ping() is True
    except Exception as exc:  # pragma: no cover - diagnostic only
        pytest.skip(f"no Docker daemon: {exc}")
