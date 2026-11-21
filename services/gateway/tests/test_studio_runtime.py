"""Running code, previewing it, and stopping it must be honest about outcomes.

The sandbox does the work; this module decides what to run and reports what
happened. So the tests hold three things still:

* the command comes from the detected profile (never from a caller);
* a failed step reports the exit code and the tail of the output, not "done";
* a preview that starts but never answers says so, with the server's log, and is
  always stoppable -- including when the process already died.
"""
from __future__ import annotations

import pytest

from services.gateway import workspace_api as ws
from services.gateway.studio import profiles, runtime

NODE = profiles.detect(["package.json", "vite.config.ts"])


@pytest.fixture
def sandbox(monkeypatch):
    """A fake sandbox: records commands and replays scripted answers."""
    state = {
        "commands": [], "answers": [], "default": None, "ports": [], "unexposed": [],
        "stop_answer": {"status": "SUCCESS", "detail": {"stdout": "stopped\n", "returncode": 0}},
        "published": [{"host_port": 1234, "container_port": 0}],
    }

    async def fake_shell(workspace_id, command, *, user, timeout=runtime.DEFAULT_TIMEOUT_S):
        state["commands"].append(command)
        if command == runtime._STOP_COMMAND:
            return state["stop_answer"]
        if state["answers"]:
            return state["answers"].pop(0)
        return state["default"] or {"status": "SUCCESS", "detail": {"stdout": "", "returncode": 0}}

    async def fake_expose(workspace_id, port, *, user, host_port=None):
        state["ports"].append(port)
        return {"status": "active", "url": f"http://host:{9000 + port}", "host_port": 9000 + port}

    async def fake_unexpose(workspace_id, host_port, *, user):
        state["unexposed"].append(host_port)
        return True

    async def fake_list_ports(workspace_id):
        return list(state["published"])

    async def fake_read(workspace_id, path, user, *, max_bytes=ws.MAX_FILE_BYTES):
        if path.endswith("preview.log"):
            return "VITE ready on http://0.0.0.0:5173\n"
        raise ws.WorkspaceError("missing")

    monkeypatch.setattr(runtime, "shell", fake_shell)
    monkeypatch.setattr(runtime.ws, "expose_port", fake_expose)
    monkeypatch.setattr(runtime.ws, "unexpose_port", fake_unexpose)
    monkeypatch.setattr(runtime.ws, "list_ports", fake_list_ports)
    monkeypatch.setattr(runtime.ws, "read_file", fake_read)
    return state


async def test_the_test_step_runs_the_profiles_command_and_passes(sandbox):
    sandbox["answers"] = [{"status": "SUCCESS", "detail": {"stdout": "3 passed", "returncode": 0}}]

    result = await runtime.run_action("ws1", "test", profile=NODE, user="u1")

    assert sandbox["commands"] == ["npm test"]
    assert result.ok is True and result.returncode == 0
    assert "passed" in result.stdout
    assert result.summary().startswith("test passed")


async def test_a_failing_step_reports_the_exit_code_and_the_tail(sandbox):
    sandbox["answers"] = [
        {"status": "SUCCESS", "detail": {"stdout": "x" * 7000, "stderr": "1 failed", "returncode": 1}}
    ]

    result = await runtime.run_action("ws1", "build", profile=NODE, user="u1")

    assert result.ok is False
    assert result.returncode == 1
    assert result.stderr == "1 failed"
    assert "characters cut" in result.stdout, "a long log is trimmed with a note, not silently"
    assert "build failed (exit 1)" in result.summary()


async def test_an_action_the_stack_does_not_have_is_refused_with_the_list(sandbox):
    static = profiles.detect(["index.html"])

    with pytest.raises(runtime.StudioError) as excinfo:
        await runtime.run_action("ws1", "test", profile=static, user="u1")

    message = str(excinfo.value)
    assert "Static site" in message and "serve" in message
    assert sandbox["commands"] == []



async def test_a_preview_starts_detached_exposes_a_port_and_checks_it_answered(sandbox, monkeypatch):
    async def answered(url, timeout=3.0):
        return True

    monkeypatch.setattr(runtime, "_probe", answered)

    result = await runtime.preview("ws1", profile=NODE, ready_timeout=2, user="u1")

    stop, start = sandbox["commands"][:2]
    assert stop == runtime._STOP_COMMAND, "an earlier preview is stopped first"
    assert start.startswith("mkdir -p .studio")
    assert "{ nohup npm run dev -- --host 0.0.0.0 > .studio/preview.log 2>&1 < /dev/null & echo $! >" in start
    assert "preview.pid" in start
    assert sandbox["ports"] == [5173]
    assert result["ok"] is True and result["url"] == "http://host:14173"
    assert result["container_port"] == 5173


async def test_a_preview_that_never_answers_says_so_with_its_log(sandbox, monkeypatch):
    async def silent(url, timeout=3.0):
        return False

    monkeypatch.setattr(runtime, "_probe", silent)

    result = await runtime.preview("ws1", profile=NODE, ready_timeout=1, user="u1")

    assert result["ok"] is False
    assert "nothing answered" in result["message"]
    assert "VITE ready" in result["log"]


async def test_a_stack_with_nothing_to_serve_is_refused(sandbox):
    python = profiles.detect(["pyproject.toml"])

    with pytest.raises(runtime.StudioError) as excinfo:
        await runtime.preview("ws1", profile=python, user="u1")

    assert "nothing to serve" in str(excinfo.value)
    assert sandbox["commands"] == []


async def test_stopping_kills_the_recorded_pid_and_closes_the_ports(sandbox):
    result = await runtime.stop("ws1", user="u1")

    assert sandbox["commands"] == [runtime._STOP_COMMAND]
    assert result["stopped_process"] is True and result["process"] == "stopped"
    assert result["closed_ports"] == [1234]
    assert sandbox["unexposed"] == [1234]


async def test_stopping_a_preview_that_already_died_is_not_an_error(sandbox):
    sandbox["stop_answer"] = {"status": "SUCCESS", "detail": {"stdout": "gone\n", "returncode": 0}}

    result = await runtime.stop("ws1", user="u1")

    assert result["stopped_process"] is False and result["process"] == "gone"
    assert result["closed_ports"] == [1234], "the port is still closed even if the process was gone"


async def test_a_kill_that_fails_is_an_error_not_a_quiet_no(sandbox):
    sandbox["stop_answer"] = {
        "status": "FAILURE",
        "message": "Command failed with exit code 1",
        "detail": {"stdout": "", "stderr": "kill: (42) - Operation not permitted", "returncode": 1},
    }

    with pytest.raises(runtime.StudioError, match="Operation not permitted"):
        await runtime.stop("ws1", user="u1")


async def test_a_port_that_will_not_close_is_named_after_the_others_are_closed(sandbox, monkeypatch):
    sandbox["published"] = [{"host_port": 1111}, {"host_port": 2222}]

    async def stuck(workspace_id, host_port, *, user):
        if host_port == 1111:
            raise ws.WorkspaceError("Could not unexpose port 1111: busy", 500)
        sandbox["unexposed"].append(host_port)
        return True

    monkeypatch.setattr(runtime.ws, "unexpose_port", stuck)

    with pytest.raises(runtime.StudioError, match="1111") as excinfo:
        await runtime.stop("ws1", user="u1")
    assert sandbox["unexposed"] == [2222], "one stuck forward must not keep the others open"
    assert "closed [2222]" in str(excinfo.value)


async def test_a_port_list_that_cannot_be_read_is_an_error(sandbox, monkeypatch):
    async def unreachable(workspace_id):
        raise ws.WorkspaceError("Could not list the published ports of ws1: 503", 503)

    monkeypatch.setattr(runtime.ws, "list_ports", unreachable)

    with pytest.raises(ws.WorkspaceError):
        await runtime.stop("ws1", user="u1")


async def test_a_second_preview_stops_the_first_and_its_forward(sandbox, monkeypatch):
    async def answered(url, timeout=3.0):
        return True

    monkeypatch.setattr(runtime, "_probe", answered)
    sandbox["published"] = [{"host_port": 14173}]

    result = await runtime.preview("ws1", profile=NODE, ready_timeout=2, user="u1")

    assert result["replaced"] == {"stopped_process": True, "process": "stopped", "closed_ports": [14173]}
    assert sandbox["unexposed"] == [14173]
    assert sandbox["commands"].index(runtime._STOP_COMMAND) < next(
        i for i, c in enumerate(sandbox["commands"]) if "nohup" in c
    )


def test_the_test_count_is_read_from_each_runner():
    assert runtime.count_tests("python", "3 passed, 1 failed in 0.2s") == 4
    assert runtime.count_tests("python", "no tests ran in 0.01s") == 0
    assert runtime.count_tests("go", "ok  \texample.com/a\t0.01s\n?   \texample.com/b\t[no test files]") == 1
    assert runtime.count_tests("go", "?   \texample.com/b\t[no test files]") == 0
    assert runtime.count_tests("rust", "test result: ok. 0 passed; 0 failed; 0 ignored") == 0
    assert runtime.count_tests("rust", "test result: ok. 5 passed; 0 failed; 0 ignored") == 5
    assert runtime.count_tests("node-vite", "Tests  7 passed (7)") == 7
    assert runtime.count_tests("java", "BUILD SUCCESS") is None, "an unknown runner is unknown, not zero"


async def test_a_test_step_that_ran_nothing_is_not_a_pass(sandbox):
    sandbox["answers"] = [{"status": "SUCCESS", "detail": {"stdout": "no tests ran in 0.01s", "returncode": 0}}]

    result = await runtime.run_action("ws1", "test", profile=profiles.detect(["pyproject.toml"]), user="u1")

    assert result.ok is False
    assert result.extra["tests_ran"] == 0
    assert "no tests ran" in result.message


async def test_a_success_without_an_exit_code_is_not_a_pass(sandbox):
    sandbox["answers"] = [{"status": "SUCCESS", "detail": {"stdout": "built"}}]

    result = await runtime.run_action("ws1", "build", profile=NODE, user="u1")

    assert result.ok is False


def test_output_is_trimmed_from_the_start():
    trimmed = runtime.tail("a" * 100 + "THE END", 20)

    assert trimmed.endswith("THE END")
    assert trimmed.startswith("[…")
