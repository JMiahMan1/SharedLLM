"""Run, test and preview the code that lives in a workspace.

The code executes inside the workspace's own **sandbox container**
(``services/workspace_sandbox``): only that workspace's directory is mounted, it
runs as a non-root user on a private network with CPU, memory and PID limits, and
dangerous system commands are refused. This module only decides *what* to run --
from the detected project profile, never from free text typed by a model -- and
turns the sandbox's answer into something a person can read.

A preview is the same sandbox plus a published port: the dev server is started
detached inside the container, the port is exposed through the Workspace API, and
the URL is checked before it is returned. Stopping it kills the recorded PID and
closes the forward, so a preview never leaks a listener; starting a second
preview stops the first.

Every command goes through :func:`shell` (the execution service's
``/execute/workspace_shell``, the same entry the Terminal and Raven use) and every
answer is read by :func:`outcome`, so "did it work" is decided in one place.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import aiohttp

from services.gateway import workspace_api as ws
from services.gateway.config import EXECUTION_SVC
from services.gateway.studio import profiles

log = logging.getLogger("gateway.studio.runtime")

#: Where the preview server's log and PID live inside the workspace.
STUDIO_DIR = ".studio"
LOG_PATH = f"{STUDIO_DIR}/preview.log"
PID_PATH = f"{STUDIO_DIR}/preview.pid"

#: How much of a command's output is kept. A failing test run is long; the end is
#: what matters, and the whole thing is not useful in a chat bubble.
MAX_OUTPUT_CHARS = 6000

DEFAULT_TIMEOUT_S = 600


class StudioError(RuntimeError):
    """The studio could not do what was asked, with a reason a person can read."""


class ShellUnavailable(StudioError):
    """The execution service did not run the command at all (HTTP error, bad answer)."""


@dataclass
class RunResult:
    action: str
    stack: str
    command: str
    ok: bool
    returncode: int | None
    duration_s: float
    stdout: str = ""
    stderr: str = ""
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """One line for a chat turn or a toast."""
        state = "passed" if self.ok else "failed"
        code = "" if self.returncode is None else f" (exit {self.returncode})"
        return f"{self.action} {state}{code} in {self.duration_s:.1f}s"


def tail(text: str | None, limit: int = MAX_OUTPUT_CHARS) -> str:
    """The end of a long output, with a note saying how much was cut."""
    value = text or ""
    if len(value) <= limit:
        return value
    return f"[… {len(value) - limit} characters cut from the start …]\n{value[-limit:]}"


async def shell(workspace_id: str, command: str, *, user: str, timeout: int = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run one command in the workspace's sandbox and return the execution answer.

    An HTTP error from the execution service is a :class:`ShellUnavailable`, never
    a dict that reads like a failed command: "the command failed" and "nothing ran"
    send a person looking in different places.
    """
    status, body = await ws.request(
        "/execute/workspace_shell",
        {
            "workspace_id": workspace_id,
            "command": command,
            "timeout": timeout,
            "user_context": {"user": user},
        },
        float(timeout) + 60.0,
        base_url=EXECUTION_SVC,
    )
    if not 200 <= status < 300:
        detail = body.get("detail") or body.get("message") or body
        raise ShellUnavailable(f"the sandbox shell answered HTTP {status}: {str(detail)[:300]}")
    return body


def outcome(answer: dict[str, Any]) -> dict[str, Any]:
    """Read one shell answer: ok only for status SUCCESS *and* exit code 0.

    A SUCCESS without an exit code used to count as a pass; the execution service
    always reports one, so its absence means the answer is not what it claims.
    """
    detail = answer.get("detail") if isinstance(answer.get("detail"), dict) else {}
    returncode = detail.get("returncode")
    code = returncode if isinstance(returncode, int) else None
    stdout = str(detail.get("stdout") or "")
    stderr = str(detail.get("stderr") or "")
    return {
        "ok": str(answer.get("status") or "").upper() == "SUCCESS" and code == 0,
        "returncode": code,
        "stdout": stdout,
        "stderr": stderr,
        "output": "\n".join(part for part in (stdout, stderr) if part).strip(),
        "message": str(answer.get("message") or ""),
    }


async def profile_entries(workspace_id: str, *, user: str) -> list[dict[str, Any]]:
    """The file list a profile is detected from (one listing for every caller)."""
    return await ws.list_files(workspace_id, ".", user=user, recursive=True, max_depth=3, max_entries=600)


async def profile_for(workspace_id: str, *, user: str) -> profiles.Profile:
    """Detect the workspace's stack from its files."""
    return profiles.detect(await profile_entries(workspace_id, user=user))


# --- counting tests ----------------------------------------------------------

#: (stack prefix, pattern for tests that ran) -- read from the runner's own summary.
_PASSED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("python", re.compile(r"\b(\d+) (?:passed|failed|errors?)\b")),
    ("rust", re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed")),
    ("go", re.compile(r"^(?:ok|FAIL)\s+\S+\s+(?:[\d.]+s|\(cached\))", re.MULTILINE)),
    ("node", re.compile(r"Tests?:?\s+(?:\d+ failed, )?(\d+) passed|# pass (\d+)|(\d+) passing")),
)

#: What each runner prints when it found nothing to run.
_NOTHING_RAN = re.compile(
    r"no tests ran|collected 0 items|running 0 tests|\[no test files\]|no test files found|"
    r"no tests found|Error: no test specified|0 passing",
    re.IGNORECASE,
)


def count_tests(stack: str, output: str) -> int | None:
    """How many tests the runner reports having run; ``None`` when it does not say.

    Exit code 0 with zero tests is not a passing suite: ``go test`` and
    ``cargo test`` exit 0 on a package with no tests, and a verdict built on the
    exit code alone called that "the tests pass".
    """
    if _NOTHING_RAN.search(output or "") and not any(
        pattern.search(output or "") for prefix, pattern in _PASSED_PATTERNS if stack.startswith(prefix)
    ):
        return 0
    for prefix, pattern in _PASSED_PATTERNS:
        if not stack.startswith(prefix):
            continue
        matches = list(pattern.finditer(output or ""))
        if not matches:
            return None
        if prefix == "go":
            return len(matches)
        total = 0
        for match in matches:
            total += sum(int(group) for group in match.groups() if group)
        return total
    return None


async def run_action(
    workspace_id: str,
    action: str,
    *,
    user: str,
    timeout: int = DEFAULT_TIMEOUT_S,
    profile: profiles.Profile | None = None,
) -> RunResult:
    """Run the profile's ``action`` (install, test, build, lint) and report it."""
    detected = profile or await profile_for(workspace_id, user=user)
    step = detected.command_for(action)
    if step is None:
        raise StudioError(
            f"{detected.label} has no '{action}' step. Available: "
            + (", ".join(sorted(detected.steps)) or "none")
        )

    started = time.monotonic()
    result = outcome(await shell(workspace_id, step.command, user=user, timeout=timeout))
    elapsed = round(time.monotonic() - started, 2)
    ok = result["ok"]
    message = result["message"]
    extra: dict[str, Any] = {}
    if action == "test":
        ran = count_tests(detected.stack, result["output"])
        extra["tests_ran"] = ran
        if ok and ran == 0:
            ok = False
            message = "no tests ran: the test step exited 0 but found nothing to run"
    return RunResult(
        action=action,
        stack=detected.stack,
        command=step.command,
        ok=ok,
        returncode=result["returncode"],
        duration_s=elapsed,
        stdout=tail(result["stdout"]),
        stderr=tail(result["stderr"]),
        message=message,
        extra=extra,
    )


#: Safe probes for the shell diagnostic: each proves one thing a workspace needs,
#: and each is harmless to run in any project.
DIAGNOSTIC_PROBES: tuple[tuple[str, str, str], ...] = (
    ("identity", "id -un; id -u", "who the sandbox runs as"),
    (
        "home",
        # Exits non-zero when HOME is not writable: an unwritable HOME *is* the
        # failure, and a probe that reports "healthy" here is worse than none.
        'printf "%s" "$HOME"; if [ -w "$HOME" ]; then echo " (writable)"; '
        'else echo " (NOT writable)"; false; fi',
        "the HOME tools write their caches to",
    ),
    ("directory", "pwd && ls -a | head -5", "where the shell starts and what it sees"),
    ("bash", "/bin/bash -lc 'echo bash-ok'", "the interactive shell the terminal opens"),
    ("python", "python3 --version", "the Python a workspace project needs"),
    ("git", "git --version", "git, for every workspace with a remote"),
)


#: A one-line program per language the sandbox can run directly. Writing and
#: executing this is the difference between "the environment looks right" and
#: "code runs here".
PROBE_SCRIPTS: dict[str, tuple[str, str, str]] = {
    "python": ("_studio_probe.py", 'print("studio-ok from python")\n', "python3 _studio_probe.py"),
    "javascript": (
        "_studio_probe.mjs",
        'console.log("studio-ok from node");\n',
        "node _studio_probe.mjs",
    ),
}


async def diagnose(
    workspace_id: str, *, user: str, profile: profiles.Profile | None = None
) -> dict[str, Any]:
    """Run a handful of safe probes and report exactly what works in the sandbox.

    "The shell is broken" is not a diagnosis. This turns it into one: which user,
    whether HOME is writable, where the shell starts, whether bash itself runs, and
    whether python and git are present -- each with its own output.
    """
    detected = None
    profile_error = ""
    try:
        resolved = profile or await profile_for(workspace_id, user=user)
        detected = resolved.to_dict()
    except ws.WorkspaceError as exc:
        profile_error = str(exc)

    probes: list[dict[str, Any]] = []
    for name, command, why in DIAGNOSTIC_PROBES:
        started = time.monotonic()
        try:
            result = outcome(await shell(workspace_id, command, user=user, timeout=30))
        except (StudioError, ws.WorkspaceError, aiohttp.ClientError, TimeoutError) as exc:
            # A probe that could not run is reported as failed, with why; it
            # must not hide the probes after it.
            probes.append({
                "name": name, "command": command, "why": why, "ok": False,
                "returncode": None, "output": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.monotonic() - started, 2),
            })
            continue
        probes.append({
            "name": name,
            "command": command,
            "why": why,
            "ok": result["ok"],
            "returncode": result["returncode"],
            "output": tail(result["output"], 600),
            "duration_s": round(time.monotonic() - started, 2),
        })

    failed = [probe["name"] for probe in probes if not probe["ok"]]
    return {
        "workspace_id": workspace_id,
        "profile": detected,
        "profile_error": profile_error,
        "ok": not failed,
        "failed": failed,
        "probes": probes,
        "hint": _diagnosis_hint(failed, probes),
    }


def _diagnosis_hint(failed: list[str], probes: list[dict[str, Any]]) -> str:
    """One sentence naming the most likely cause, from what actually failed."""
    if not failed:
        return "The sandbox shell is healthy."
    by_name = {probe["name"]: probe for probe in probes}
    home = str(by_name.get("home", {}).get("output") or "")
    if "home" in failed or "NOT writable" in home:
        return (
            f"HOME is {home or 'unreadable'}: tools that cache to $HOME (npm, pip --user, git) "
            "will fail with permission errors. The sandbox should set HOME from its own passwd "
            "entry; if this persists, the sandbox image's user is missing."
        )
    if "bash" in failed:
        return (
            "The sandbox has no working /bin/bash. Commands run through /bin/sh still work, so "
            "the interactive Terminal is the broken part -- check the sandbox image."
        )
    if "identity" in failed:
        return (
            "The shell could not even report its user: the sandbox container is not running or "
            "was created with a user that does not exist in the image."
        )
    if "directory" in failed:
        return (
            "The shell cannot enter the workspace: its directory is missing or not readable by "
            "the sandbox user (a host-path/permission problem, not a command problem)."
        )
    missing = [name for name in failed if name in {"python", "git"}]
    if missing:
        return f"{' and '.join(missing)} is not installed in the sandbox image."
    return "Some probes failed; the output above names the failing command."


async def verify(
    workspace_id: str,
    *,
    user: str,
    timeout: int = DEFAULT_TIMEOUT_S,
    run_tests: bool = True,
    profile: profiles.Profile | None = None,
) -> dict[str, Any]:
    """Prove this workspace works: the shell, a real program, and the tests.

    Three questions a person actually asks -- "can I run anything here?", "does
    my code run?", "do the tests pass?" -- answered in one call, in that order,
    stopping at the first thing that is wrong so the answer names one cause
    instead of a pile of symptoms. A broken shell stops here: the program and
    the tests are not run (and not blamed).

    ``ok`` needs proof: a program that ran, or tests that ran and passed. A
    skipped probe with no tests is "nothing proved this works", not a pass.
    """
    detected = profile or await profile_for(workspace_id, user=user)
    diagnosis = await diagnose(workspace_id, user=user, profile=detected)
    verdict: dict[str, Any] = {
        "workspace_id": workspace_id,
        "stack": detected.stack,
        "label": detected.label,
        "shell": {"ok": bool(diagnosis.get("ok")), "failed": diagnosis.get("failed", [])},
        "program": None,
        "tests": None,
        "ok": False,
    }
    if not diagnosis.get("ok"):
        verdict["hint"] = str(diagnosis.get("hint") or "the sandbox shell is not usable")
        return verdict

    probe = await _run_probe(workspace_id, detected, user=user, timeout=min(timeout, 120))
    verdict["program"] = probe
    if not probe.get("skipped") and not probe.get("ok"):
        verdict["hint"] = _verify_hint(verdict)
        return verdict

    tests: dict[str, Any] | None = None
    if run_tests and detected.command_for("test") is not None:
        try:
            result = await run_action(
                workspace_id, "test", user=user, timeout=timeout, profile=detected
            )
            tests = {**result.to_dict(), "available": True, "skipped": False}
        except (StudioError, ws.WorkspaceError) as exc:
            tests = {"action": "test", "ok": False, "message": str(exc), "available": True, "skipped": False}
    elif run_tests:
        tests = {
            "action": "test",
            "ok": False,
            "available": False,
            "skipped": True,
            "message": f"{detected.label} declares no test step, so there is nothing to run",
        }
    else:
        tests = {"action": "test", "ok": False, "available": True, "skipped": True,
                 "message": "the test step was not requested"}
    verdict["tests"] = tests

    program_proved = bool(probe.get("ok")) and not probe.get("skipped")
    tests_failed = not tests.get("skipped") and not tests.get("ok")
    tests_proved = not tests.get("skipped") and bool(tests.get("ok"))
    verdict["ok"] = not tests_failed and (program_proved or tests_proved)
    verdict["hint"] = _verify_hint(verdict)
    return verdict


async def _run_probe(
    workspace_id: str, detected: profiles.Profile, *, user: str, timeout: int
) -> dict[str, Any]:
    """Write a one-line program, run it, and remove it again.

    The file is deleted even when the run fails: a probe is not a deliverable, and
    a leftover ``_studio_probe.py`` in someone's repository is litter. A language
    with no probe is reported as ``skipped`` (with ``ok: False``): nothing ran, so
    nothing was proved.
    """
    language = detected.language or "python"
    script = PROBE_SCRIPTS.get(language)
    if script is None:
        return {
            "ok": False,
            "skipped": True,
            "language": language,
            "message": (
                f"no one-line probe is defined for {detected.label}; "
                f"the test step is the proof for this stack"
            ),
        }
    filename, source, command = script
    try:
        await ws.write_file(workspace_id, filename, source, user=user)
    except ws.WorkspaceError as exc:
        return {"ok": False, "skipped": False, "language": language, "command": command, "message": str(exc)}
    cleanup_error = ""
    try:
        result = outcome(await shell(workspace_id, command, user=user, timeout=timeout))
        report = {
            "ok": result["ok"],
            "skipped": False,
            "language": language,
            "file": filename,
            "command": command,
            "returncode": result["returncode"],
            "output": tail(result["output"], 600),
        }
    except (StudioError, ws.WorkspaceError, aiohttp.ClientError, TimeoutError) as exc:
        report = {"ok": False, "skipped": False, "language": language, "command": command, "message": str(exc)}
    finally:
        try:
            removed = outcome(await shell(workspace_id, f"rm -f {filename}", user=user, timeout=30))
            if not removed["ok"]:
                cleanup_error = removed["output"] or removed["message"]
        except (StudioError, ws.WorkspaceError, aiohttp.ClientError, TimeoutError) as exc:
            cleanup_error = str(exc)
    if cleanup_error:
        report["cleanup_error"] = f"{filename} could not be removed: {cleanup_error}"
    return report


def _verify_hint(verdict: dict[str, Any]) -> str:
    """One sentence naming the first thing that is actually broken, or what proved it."""
    program = verdict.get("program") or {}
    tests = verdict.get("tests") or {}
    if verdict["ok"]:
        ran = not tests.get("skipped")
        proof = "the tests pass." if ran else "there are no tests to run."
        if program.get("skipped"):
            return f"{verdict['label']}: the shell works and the tests pass."
        return f"{verdict['label']}: the shell works, a program runs, and {proof}"
    if program and not program.get("skipped") and not program.get("ok"):
        return (
            f"the shell works but `{program.get('command')}` failed: "
            f"{(program.get('output') or program.get('message') or '')[:300]}"
        )
    if tests and not tests.get("skipped") and not tests.get("ok"):
        failing = (tests.get("message") or tests.get("stderr") or tests.get("stdout") or "")[-300:]
        return f"the tests fail (`{tests.get('command')}`): {failing}"
    return (
        f"nothing proved code runs here: {verdict['label']} has no one-line probe and "
        f"{tests.get('message') or 'no tests ran'}."
    )


async def _probe(url: str, timeout: float = 3.0) -> bool:
    """Whether something answers at ``url`` (any HTTP answer below 500 counts).

    A connection refused is the normal answer while a dev server boots, so it is
    "not yet", not an error: the caller polls until its deadline.
    """
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client, client.get(url) as resp:
            return resp.status < 500
    except (aiohttp.ClientError, TimeoutError):
        return False


async def preview(
    workspace_id: str,
    *,
    user: str,
    port: int | None = None,
    ready_timeout: float = 30.0,
    profile: profiles.Profile | None = None,
) -> dict[str, Any]:
    """Start the workspace's dev server, publish its port, and check it answers.

    Any preview already running for this workspace is stopped first (its process
    and every published port), so a second start never leaves the first server
    and its forward behind. The server is started detached (``nohup ... &``) so
    the sandbox exec returns at once; its PID is recorded so :func:`stop` can end
    exactly that process.
    """
    detected = profile or await profile_for(workspace_id, user=user)
    step = detected.command_for("serve")
    if step is None:
        raise StudioError(f"{detected.label} has nothing to serve (no dev server or start script).")
    container_port = int(port or detected.preview_port or 8080)
    command = step.for_port(container_port)

    previous = await stop(workspace_id, user=user)

    # The braces matter: ``a && b && nohup c > log &`` backgrounds the whole
    # and-list as a subshell, which keeps the exec's output pipe open (the exec
    # never returns) and makes ``$!`` the subshell, so stop() killed the wrong
    # process and the server lived on. stdin is closed for the same reason.
    start = (
        f"mkdir -p {STUDIO_DIR} && rm -f {PID_PATH} && "
        f"{{ nohup {command} > {LOG_PATH} 2>&1 < /dev/null & echo $! > {PID_PATH}; }}"
    )
    started = outcome(await shell(workspace_id, start, user=user, timeout=30))
    if not started["ok"]:
        raise StudioError(
            f"the dev server would not start: {started['message'] or started['stderr'] or 'unknown error'}"
        )

    exposed = await ws.expose_port(workspace_id, container_port, user=user)
    url = str(exposed.get("url") or "")
    host_port = exposed.get("host_port")
    if not url:
        raise StudioError(f"port {container_port} was published but the runtime returned no URL: {exposed}")

    deadline = time.monotonic() + max(1.0, ready_timeout)
    answered = False
    while time.monotonic() < deadline:
        if await _probe(url):
            answered = True
            break
        await asyncio.sleep(1.0)

    try:
        log_tail = tail(await ws.read_file(workspace_id, LOG_PATH, user, max_bytes=8000), 2000)
    except ws.WorkspaceError as exc:
        log_tail = f"(the server log {LOG_PATH} could not be read: {exc})"

    return {
        "ok": answered,
        "stack": detected.stack,
        "command": command,
        "container_port": container_port,
        "host_port": host_port,
        "url": url,
        "answered": answered,
        "log": log_tail,
        "replaced": previous,
        "message": "" if answered else (
            f"the server started but nothing answered at {url} "
            f"within {ready_timeout:.0f}s; see the log"
        ),
    }


#: Ends the recorded preview process and says which case it was. Only a kill
#: that fails exits non-zero; "no preview" and "already gone" are answers.
_STOP_COMMAND = (
    f"if [ ! -f {PID_PATH} ]; then echo none; "
    f"elif kill -0 \"$(cat {PID_PATH})\" 2>/dev/null; then "
    f"kill -TERM \"$(cat {PID_PATH})\" && rm -f {PID_PATH} && echo stopped; "
    f"else rm -f {PID_PATH} && echo gone; fi"
)


async def stop(workspace_id: str, *, user: str) -> dict[str, Any]:
    """Stop the preview server and close every port this workspace published.

    Nothing is swallowed: a kill that fails, a port list that cannot be read or a
    forward that will not close is a :class:`StudioError` naming it (after every
    port has been tried, so one stuck forward does not keep the others open).
    """
    result = outcome(await shell(workspace_id, _STOP_COMMAND, user=user, timeout=30))
    state = result["stdout"].strip().splitlines()[-1:] or [""]
    process = state[0]
    if not result["ok"] or process not in {"none", "stopped", "gone"}:
        raise StudioError(
            f"the preview process could not be stopped: {result['output'] or result['message'] or 'no answer'}"
        )

    closed: list[int] = []
    failures: list[str] = []
    for port in await ws.list_ports(workspace_id):
        host_port = port.get("host_port")
        if not isinstance(host_port, int):
            failures.append(f"a published port without a host port: {port}")
            continue
        try:
            await ws.unexpose_port(workspace_id, host_port, user=user)
        except ws.WorkspaceError as exc:
            failures.append(f"port {host_port}: {exc}")
            continue
        closed.append(host_port)
    if failures:
        raise StudioError(
            f"stopped the preview ({process}) but could not close every port "
            f"(closed {closed or 'none'}): " + "; ".join(failures)
        )
    return {"stopped_process": process == "stopped", "process": process, "closed_ports": closed}
