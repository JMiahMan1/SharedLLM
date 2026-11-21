"""The self-healing loop: Jarvis fixes its own errors, then proves it.

Given a workspace file and the error it is producing, this module asks the
local model for the whole corrected file, guards the answer (it must compile,
it must keep every definition, it must not be the file that is already
failing), writes it back through the **Workspace API** with a sha256 conflict
check, and runs the workspace's own pytest gate. A second attempt gets the real
test output as context.

Nothing is written that the tests have not accepted: if every attempt fails, the
original file is restored, so a failed repair leaves the workspace as it was.
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from services.gateway import workspace_api as ws

log = logging.getLogger("gateway.trainer.selffix")

#: Nothing bigger is a single-file repair, and reading more wastes the model's
#: small context window.
MAX_FILE_BYTES = 60_000

#: Enough of the test output for the model to see the failing assertion.
MAX_TEST_OUTPUT = 3_000

_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)```", re.DOTALL)


class RepairError(RuntimeError):
    """A workspace call could not be completed (kept as the repair-facing name)."""


@dataclass
class RepairResult:
    """What one repair attempt actually did."""

    status: str  # repaired | unrepaired | skipped
    workspace_id: str
    path: str
    attempts: int = 0
    tests_passed: bool | None = None
    model: str = ""
    initial_error: str = ""
    final_error: str = ""
    test_output: str = ""
    guards: list[str] = field(default_factory=list)
    committed: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_code(answer: str) -> str | None:
    """The candidate source from a model answer: the first fenced block, if any.

    A fenced block is required on purpose. A model that replies with prose plus a
    fragment is not trusted to overwrite a working file.
    """
    if not answer:
        return None
    match = _FENCE.search(answer)
    if not match:
        return None
    body = match.group(1)
    if not body.strip():
        return None
    return body.rstrip() + "\n"


def compiles(source: str) -> tuple[bool, str]:
    """Whether ``source`` is valid Python, with the compiler's own reason."""
    try:
        compile(source, "<repair-candidate>", "exec")
    except SyntaxError as exc:
        return False, f"{exc.msg} (line {exc.lineno})"
    return True, ""


def top_level_definitions(source: str) -> set[str]:
    """Names of the module's functions and classes, for the "did it keep them" guard."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def reject_reason(candidate: str, original: str, path: str) -> str | None:
    """Why this candidate must not be written, or ``None`` when it may be.

    These guards are the difference between self-repair and self-destruction:
    they stop the model from truncating a file, deleting the function under
    test, or writing the same broken file back.
    """
    if not candidate.strip():
        return "the answer had no code"
    if candidate.strip() == original.strip():
        return "the answer is the same as the failing file"
    if "```" in candidate:
        return "the answer still contains a code fence"
    if len(candidate.strip()) < 20:
        return "the answer is too short to be a file"

    if path.endswith(".py"):
        ok, reason = compiles(candidate)
        if not ok:
            return f"the answer does not compile: {reason}"
        lost = sorted(top_level_definitions(original) - top_level_definitions(candidate))
        if lost:
            return f"the answer removes definitions: {', '.join(lost)}"
    return None


def _sha256(content: str) -> str:
    return ws.sha256(content)


def _gate_output(result: dict[str, Any] | None) -> str:
    """The tail of the failing test run: what the next attempt needs to see."""
    if not result:
        return ""
    combined = "\n".join(part for part in (result.get("stdout"), result.get("stderr")) if part)
    return combined[-MAX_TEST_OUTPUT:]



def build_repair_prompt(path: str, source: str, error: str, test_output: str = "") -> str:
    """The repair instruction: whole file out, nothing else, keep the interface."""
    parts = [
        f"Fix the bug in `{path}`. Reply with ONLY the complete corrected file inside one "
        "```python block. No explanation, no diff, no other files.",
        "",
        "Rules: keep every function and class that exists now, keep the public names the same, "
        "change only what the error requires.",
        "",
        f"The error was:\n{error.strip() or '(no traceback given)'}",
    ]
    if test_output.strip():
        parts += ["", f"The test run said:\n{test_output.strip()[:MAX_TEST_OUTPUT]}"]
    parts += ["", f"Current contents of `{path}`:", "```python", source, "```"]
    return "\n".join(parts)


async def _ask(
    complete_fn: Any, prompt: str, result: RepairResult, llm_mod: Any
) -> str:
    """One model call, recording which model answered."""
    if complete_fn is not None:
        produced = await complete_fn(prompt)
        return str(produced or "")
    completion = await llm_mod.complete(
        prompt, max_tokens=1600, context_chars=12_000, timeout=300.0
    )
    result.model = completion.model
    return completion.text


async def repair_workspace(
    workspace_id: str,
    *,
    path: str,
    error_text: str | None = None,
    test_targets: list[str] | None = None,
    attempts: int = 2,
    user: str = "default",
    complete_fn: Any | None = None,
) -> RepairResult:
    """Read, repair, write back and prove with tests -- all through the Workspace API.

    ``complete_fn`` is the seam tests use: it takes a prompt and returns the
    model's answer. The default asks the local model through :mod:`trainer.llm`.
    """
    from services.gateway.trainer import llm as llm_mod

    result = RepairResult(status="unrepaired", workspace_id=workspace_id, path=path)
    targets = list(test_targets or [])
    try:
        original = await ws.read_file(workspace_id, path, user)
    except ws.WorkspaceError as exc:
        result.status, result.message = "skipped", str(exc)
        return result

    gate: dict[str, Any] | None = None
    if not error_text:
        gate = await ws.run_pytest(workspace_id, targets, user=user)
        if gate["passed"]:
            result.status = "skipped"
            result.tests_passed = True
            result.message = "the workspace's tests already pass"
            return result
        error_text = _gate_output(gate) or "the workspace's tests fail"

    result.initial_error = str(error_text)[:MAX_TEST_OUTPUT]
    current = original
    context = result.initial_error
    test_output = _gate_output(gate)

    for attempt in range(1, max(1, attempts) + 1):
        result.attempts = attempt
        # The first attempt sees the file as it is on disk; a later attempt sees
        # the candidate that just failed the tests, so it can build on it.
        prompt = build_repair_prompt(path, current, context, test_output)
        try:
            answer = await _ask(complete_fn, prompt, result, llm_mod)
        except llm_mod.LocalModelUnavailable as exc:
            result.final_error = str(exc)
            break

        candidate = extract_code(answer)
        reason = (
            "the answer had no fenced code block"
            if candidate is None
            else reject_reason(candidate, original, path)
        )
        if reason:
            result.guards.append(f"attempt {attempt}: {reason}")
            context = (
                f"The previous answer was rejected: {reason}. "
                "Return the whole file in one ```python block."
            )
            continue

        try:
            await ws.write_file(
                workspace_id, path, candidate, expected_sha256=_sha256(current), user=user
            )
        except ws.WorkspaceError as exc:
            result.final_error = str(exc)
            break

        gate = await ws.run_pytest(workspace_id, targets, user=user)
        test_output = _gate_output(gate)
        result.tests_passed = bool(gate["passed"])
        result.test_output = test_output
        if gate["passed"]:
            result.status = "repaired"
            result.final_error = ""
            return result

        result.final_error = test_output or "the tests still fail"
        current = candidate
        context = result.final_error

    # Every attempt failed: leave the workspace exactly as it was found.
    try:
        await ws.write_file(workspace_id, path, original, expected_sha256=_sha256(current), user=user)
    except ws.WorkspaceError as exc:  # pragma: no cover - only when something else edited it
        result.guards.append(f"could not restore the original file: {exc}")

    if not result.message:
        result.message = "no attempt produced a file the tests accept"
    return result
