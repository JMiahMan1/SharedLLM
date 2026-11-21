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
closes the forward, so a preview never leaks a listener.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import aiohttp

from services.gateway import workspace_api as ws
from services.gateway.config import EXECUTION_SVC, INTERNAL_SECRET
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


async def _execution_post(endpoint: str, payload: dict[str, Any], timeout: float = 700.0) -> dict[str, Any]:
    """POST to the execution service (the one seam tests replace)."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client, client.post(
        f"{EXECUTION_SVC}{endpoint}",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {"status": "ERROR", "message": (await resp.text())[:500]}
        return body if isinstance(body, dict) else {"status": "ERROR", "message": str(body)}


async def shell(workspace_id: str, command: str, *, user: str, timeout: int = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run one command in the workspace's sandbox."""
    return await _execution_post(
        "/execute/workspace_shell",
        {
            "workspace_id": workspace_id,
            "command": command,
            "timeout": timeout,
            "user_context": {"user": user},
        },
        timeout=float(timeout) + 60.0,
    )


async def profile_for(workspace_id: str, *, user: str = "default") -> profiles.Profile:
    """Detect the workspace's stack from its files."""
    entries = await ws.list_files(workspace_id, ".", user=user, recursive=True, max_depth=3, max_entries=600)
    return profiles.detect(entries)


async def run_action(
    workspace_id: str,
    action: str,
    *,
    user: str = "default",
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
    answer = await shell(workspace_id, step.command, user=user, timeout=timeout)
    elapsed = round(time.monotonic() - started, 2)
    detail = answer.get("detail") if isinstance(answer.get("detail"), dict) else {}
    returncode = detail.get("returncode")
    ok = str(answer.get("status") or "").upper() == "SUCCESS" and returncode in (0, None)
    return RunResult(
        action=action,
        stack=detected.stack,
        command=step.command,
        ok=ok,
        returncode=returncode if isinstance(returncode, int) else None,
        duration_s=elapsed,
        stdout=tail(detail.get("stdout")),
        stderr=tail(detail.get("stderr")),
        message=str(answer.get("message") or ""),
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
    workspace_id: str, *, user: str = "default", profile: profiles.Profile | None = None
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
            answer = await shell(workspace_id, command, user=user, timeout=30)
        except Exception as exc:  # a probe must never hide the others
            probes.append({
                "name": name, "command": command, "why": why, "ok": False,
                "returncode": None, "output": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.monotonic() - started, 2),
            })
            continue
        detail = answer.get("detail") if isinstance(answer.get("detail"), dict) else {}
        output = "\n".join(
            part for part in (detail.get("stdout"), detail.get("stderr")) if part
        ).strip()
        returncode = detail.get("returncode")
        probes.append({
            "name": name,
            "command": command,
            "why": why,
            "ok": str(answer.get("status") or "").upper() == "SUCCESS"
            and returncode in (0, None),
            "returncode": returncode if isinstance(returncode, int) else None,
            "output": tail(output, 600),
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
    user: str = "default",
    timeout: int = DEFAULT_TIMEOUT_S,
    run_tests: bool = True,
    profile: profiles.Profile | None = None,
) -> dict[str, Any]:
    """Prove this workspace works: the shell, a real program, and the tests.

    Three questions a person actually asks -- "can I run anything here?", "does
    my code run?", "do the tests pass?" -- answered in one call, in that order,
    stopping at the first thing that is wrong so the answer names one cause
    instead of a pile of symptoms.
    """
    detected = profile or await profile_for(workspace_id, user=user)
    diagnosis = await diagnose(workspace_id, user=user, profile=detected)

    probe = await _run_probe(workspace_id, detected, user=user, timeout=min(timeout, 120))
    tests: dict[str, Any] | None = None
    if run_tests and detected.command_for("test") is not None:
        try:
            result = await run_action(
                workspace_id, "test", user=user, timeout=timeout, profile=detected
            )
            tests = result.to_dict()
        except (StudioError, ws.WorkspaceError) as exc:
            tests = {"action": "test", "ok": False, "message": str(exc), "available": True}
    elif run_tests:
        tests = {
            "action": "test",
            "ok": True,
            "available": False,
            "message": f"{detected.label} declares no test step, so there is nothing to run",
        }

    probe_ok = bool(probe.get("ok"))
    tests_ok = tests is None or bool(tests.get("ok"))
    verdict = {
        "workspace_id": workspace_id,
        "stack": detected.stack,
        "label": detected.label,
        "shell": {"ok": bool(diagnosis.get("ok")), "failed": diagnosis.get("failed", [])},
        "program": probe,
        "tests": tests,
        "ok": bool(diagnosis.get("ok")) and probe_ok and tests_ok,
    }
    verdict["hint"] = _verify_hint(verdict, diagnosis)
    return verdict


async def _run_probe(
    workspace_id: str, detected: profiles.Profile, *, user: str, timeout: int
) -> dict[str, Any]:
    """Write a one-line program, run it, and remove it again.

    The file is deleted even when the run fails: a probe is not a deliverable, and
    a leftover ``_studio_probe.py`` in someone's repository is litter.
    """
    language = detected.language or "python"
    script = PROBE_SCRIPTS.get(language)
    if script is None:
        return {
            "ok": True,
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
        return {"ok": False, "language": language, "command": command, "message": str(exc)}
    try:
        answer = await shell(workspace_id, command, user=user, timeout=timeout)
        detail = answer.get("detail") if isinstance(answer.get("detail"), dict) else {}
        output = "\n".join(
            part for part in (detail.get("stdout"), detail.get("stderr")) if part
        ).strip()
        returncode = detail.get("returncode")
        return {
            "ok": str(answer.get("status") or "").upper() == "SUCCESS"
            and returncode in (0, None),
            "language": language,
            "file": filename,
            "command": command,
            "returncode": returncode if isinstance(returncode, int) else None,
            "output": tail(output, 600),
        }
    except Exception as exc:  # a probe failure must not hide the diagnosis
        return {"ok": False, "language": language, "command": command, "message": str(exc)}
    finally:
        try:
            await shell(workspace_id, f"rm -f {filename}", user=user, timeout=30)
        except Exception:
            log.debug("could not remove the studio probe %s", filename, exc_info=True)


def _verify_hint(verdict: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    """One sentence naming the first thing that is actually broken."""
    if verdict["ok"]:
        ran = bool(verdict["tests"]) and verdict["tests"].get("available") is not False
        return (
            f"{verdict['label']}: the shell works, a program runs, and "
            + ("the tests pass." if ran else "there are no tests to run.")
        )
    if not verdict["shell"]["ok"]:
        return str(diagnosis.get("hint") or "the sandbox shell is not usable")
    program = verdict["program"]
    if not program.get("ok"):
        return (
            f"the shell works but `{program.get('command')}` failed: "
            f"{(program.get('output') or program.get('message') or '')[:300]}"
        )
    tests = verdict.get("tests") or {}
    if tests and not tests.get("ok"):
        failing = (tests.get("stderr") or tests.get("stdout") or tests.get("message") or "")[-300:]
        return f"the tests fail (`{tests.get('command')}`): {failing}"
    return "something in the workspace check failed; the detail above names it."


async def _probe(url: str, timeout: float = 3.0) -> bool:
    """Whether something answers at ``url`` (any HTTP answer counts)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client, client.get(url) as resp:
            return resp.status < 500
    except Exception:
        return False


async def preview(
    workspace_id: str,
    *,
    user: str = "default",
    port: int | None = None,
    ready_timeout: float = 30.0,
    profile: profiles.Profile | None = None,
) -> dict[str, Any]:
    """Start the workspace's dev server, publish its port, and check it answers.

    The server is started detached (``nohup ... &``) so the sandbox exec returns
    at once; its PID is recorded so :func:`stop` can end exactly that process.
    """
    detected = profile or await profile_for(workspace_id, user=user)
    step = detected.command_for("serve")
    if step is None:
        raise StudioError(f"{detected.label} has nothing to serve (no dev server or start script).")
    container_port = int(port or detected.preview_port or 8080)

    start = (
        f"mkdir -p {STUDIO_DIR} && rm -f {PID_PATH} && "
        f"nohup {step.command} > {LOG_PATH} 2>&1 & echo $! > {PID_PATH}"
    )
    answer = await shell(workspace_id, start, user=user, timeout=30)
    if str(answer.get("status") or "").upper() != "SUCCESS":
        detail = answer.get("detail") if isinstance(answer.get("detail"), dict) else {}
        raise StudioError(
            f"the dev server would not start: {answer.get('message') or detail.get('stderr') or 'unknown error'}"
        )

    exposed = await ws.expose_port(workspace_id, container_port, user=user)
    url = str(exposed.get("url") or "")
    host_port = exposed.get("host_port")

    deadline = time.monotonic() + max(1.0, ready_timeout)
    answered = False
    while time.monotonic() < deadline:
        if url and await _probe(url):
            answered = True
            break
        await asyncio.sleep(1.0)

    log_tail = ""
    try:
        log_tail = tail(await ws.read_file(workspace_id, LOG_PATH, user, max_bytes=8000), 2000)
    except ws.WorkspaceError:
        log_tail = ""

    return {
        "ok": answered,
        "stack": detected.stack,
        "command": step.command,
        "container_port": container_port,
        "host_port": host_port,
        "url": url,
        "answered": answered,
        "log": log_tail,
        "message": "" if answered else (
            f"the server started but nothing answered at {url or 'the published port'} "
            f"within {ready_timeout:.0f}s; see the log"
        ),
    }


async def stop(workspace_id: str, *, user: str = "default") -> dict[str, Any]:
    """Stop the preview server and close every port this workspace published."""
    stopped_process = False
    try:
        answer = await shell(
            workspace_id,
            f"kill -TERM $(cat {PID_PATH}) 2>/dev/null && echo stopped || true",
            user=user,
            timeout=30,
        )
        detail = answer.get("detail") if isinstance(answer.get("detail"), dict) else {}
        stopped_process = "stopped" in str(detail.get("stdout") or "")
    except Exception as exc:  # a sandbox that is already gone is not an error
        log.debug("[studio] could not kill the preview process: %s", exc)

    closed: list[int] = []
    for port in await ws.list_ports(workspace_id):
        host_port = port.get("host_port")
        if not isinstance(host_port, int):
            continue
        try:
            if await ws.unexpose_port(workspace_id, host_port, user=user):
                closed.append(host_port)
        except ws.WorkspaceError as exc:
            log.warning("[studio] could not close port %s: %s", host_port, exc)

    return {"stopped_process": stopped_process, "closed_ports": closed}
