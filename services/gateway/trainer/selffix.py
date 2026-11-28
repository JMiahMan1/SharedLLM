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

#: The largest file a repair will touch. The model must read the *whole* file
#: and write the *whole* file back, so it has to fit the prompt budget below and
#: the answer's token budget. A bigger file is refused, never trimmed: a trimmed
#: prompt asking for "the complete file" gets back a file missing its middle.
MAX_FILE_BYTES = 12_000

#: Enough of the test output for the model to see the failing assertion.
MAX_TEST_OUTPUT = 3_000

#: The prompt budget for one repair call. The prompt is checked against it
#: before the call and is never trimmed by :func:`llm.complete`.
REPAIR_CONTEXT_CHARS = 24_000

#: A repair is at most this many model attempts (the route and the cycle both
#: enforce it; each attempt is a full-file generation).
MAX_ATTEMPTS = 3

_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)```", re.DOTALL)


class RepairError(RuntimeError):
    """A workspace call could not be completed (kept as the repair-facing name)."""


@dataclass
class RepairResult:
    """What one repair attempt actually did."""

    status: str  # repaired | unrepaired | skipped | error (the gate or the restore broke)
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


_DEFINITION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def top_level_definitions(source: str) -> set[str]:
    """Names of the module's top-level functions and classes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {node.name for node in tree.body if isinstance(node, _DEFINITION)}


def all_definitions(source: str) -> set[str]:
    """Every function and class at any depth, as a dotted path.

    ``Parser.feed`` and ``build.inner`` count, not just top-level names: a
    candidate that keeps ``class Parser`` but drops its ``feed`` method has
    deleted the code under test just as surely as one that drops the class.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, _DEFINITION):
                name = f"{prefix}{node.name}"
                names.add(name)
                visit(node.body, f"{name}.")
            else:
                # Definitions inside if/try/with/for blocks keep the enclosing path.
                for field_name in ("body", "orelse", "finalbody", "handlers"):
                    inner = getattr(node, field_name, None)
                    if isinstance(inner, list):
                        visit([item for item in inner if isinstance(item, ast.stmt)], prefix)
                        for handler in inner:
                            if isinstance(handler, ast.ExceptHandler):
                                visit(handler.body, prefix)

    visit(tree.body, "")
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
        lost = sorted(all_definitions(original) - all_definitions(candidate))
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



def build_repair_prompt(path: str, source: str, error: str, test_output: str = "", sim_hints: str = "") -> str:
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
    if sim_hints.strip():
        parts += [
            "",
            "Lessons from earlier repairs (shared System and this user's own; apply only when relevant):",
            sim_hints.strip(),
        ]
    if test_output.strip():
        parts += ["", f"The test run said:\n{test_output.strip()[:MAX_TEST_OUTPUT]}"]
    parts += ["", f"Current contents of `{path}`:", "```python", source, "```"]
    return "\n".join(parts)


def answer_tokens(source: str) -> int:
    """Enough tokens for the whole file back (about 3 characters a token), plus room."""
    return max(1600, len(source) // 3 + 512)


async def _ask(
    complete_fn: Any, prompt: str, result: RepairResult, llm_mod: Any, *, source: str
) -> str:
    """One model call, recording which model answered."""
    if complete_fn is not None:
        produced = await complete_fn(prompt)
        return str(produced or "")
    completion = await llm_mod.complete(
        prompt,
        model=result.model or None,
        max_tokens=answer_tokens(source),
        context_chars=REPAIR_CONTEXT_CHARS,
        timeout=300.0,
    )
    result.model = completion.model
    return completion.text


async def _record_repair_learning(
    result: RepairResult, sim_items: list[dict[str, Any]], *, passed: bool
) -> None:
    """Attribute a repair to SIM and keep the accepted/failed fix for next time."""
    try:
        from services.gateway.trainer import sim as sim_mod

        observed = (
            f"pytest passed after {result.attempts} attempt(s) with {result.model or 'the local model'}"
            if passed
            else (result.final_error or "the pytest gate still failed")[:300]
        )
        await sim_mod.record_injected_outcomes(
            sim_items,
            success=passed,
            model=result.model,
            harness="workspace",
            observed_outcome=observed,
        )
        signature = f"{result.path}::{result.initial_error.splitlines()[0][:160]}"
        if passed:
            await sim_mod.record_success_lesson(
                user_id="default",
                harness="workspace",
                category="selffix_repair",
                signature=signature,
                rationale="The pytest gate rejected the original file; this repair passed it.",
                rule=(
                    f"When repairing `{result.path}` after `{result.initial_error.splitlines()[0][:120]}`, "
                    "return the whole file and keep every existing definition; that is what the gate accepted."
                ),
                observed_outcome=observed,
                model=result.model,
                domain=str(result.initial_error or "")[:200],
            )
        else:
            await sim_mod.record_failure_lesson(
                user_id="default",
                harness="workspace",
                category="selffix_failure",
                signature=signature,
                rationale=(
                    "A whole-file repair was attempted and the pytest gate still failed, so the "
                    "same rewrite approach must not be proposed again unchanged."
                ),
                rule=(
                    f"For `{result.path}` failing with `{result.initial_error.splitlines()[0][:120]}`, "
                    "a whole-file rewrite did not pass the gate; inspect the assertion or change approach."
                ),
                observed_outcome=observed,
                model=result.model,
                domain=str(result.initial_error or "")[:200],
            )
    except Exception as exc:  # SIM is never allowed to fail a repair
        log.debug("[selffix] SIM outcome skipped: %s", exc)


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

    if not 1 <= int(attempts) <= MAX_ATTEMPTS:
        raise ValueError(f"attempts must be between 1 and {MAX_ATTEMPTS}, not {attempts}")
    result = RepairResult(status="unrepaired", workspace_id=workspace_id, path=path)
    targets = list(test_targets or [])
    try:
        # The whole file or nothing: the model is asked for the complete file, so
        # it must be shown the complete file.
        original = await ws.read_text_exact(workspace_id, path, user=user, max_bytes=MAX_FILE_BYTES)
    except ws.WorkspaceError as exc:
        result.status, result.message = "skipped", str(exc)
        return result

    gate: dict[str, Any] | None = None
    if not error_text:
        try:
            gate = await ws.run_pytest(workspace_id, targets, user=user)
        except ws.WorkspaceError as exc:
            result.status, result.message = "error", str(exc)
            return result
        if gate["outcome"] == "passed":
            result.status = "skipped"
            result.tests_passed = True
            result.message = "the workspace's tests already pass"
            return result
        if gate["outcome"] == "error":
            # Pytest could not judge the code (collection error, no tests, bad
            # targets): there is no failing test for a repair to fix or to prove.
            result.status = "error"
            result.test_output = _gate_output(gate)
            result.message = (
                f"the test gate did not run the tests (pytest exit code {gate['returncode']}); "
                "fix the test setup before asking for a repair"
            )
            return result
        error_text = _gate_output(gate) or "the workspace's tests fail"

    if complete_fn is None:
        from services.gateway.orchestrator import get_llm_settings

        result.model = await llm_mod.resolve_model(await get_llm_settings())

    sim_items: list[dict[str, Any]] = []
    sim_block = ""
    try:
        from services.gateway.trainer import sim as sim_mod

        sim_block, sim_items = await sim_mod.context_for_turn(
            user_id=str(user or "default"),
            harness="workspace",
            query=f"{path}: {str(error_text or '')[:500]}",
            model=result.model,
        )
    except Exception as exc:
        log.debug("[selffix] SIM query skipped: %s", exc)

    result.initial_error = str(error_text)[:MAX_TEST_OUTPUT]
    current = original
    context = result.initial_error
    test_output = _gate_output(gate)

    for attempt in range(1, int(attempts) + 1):
        result.attempts = attempt
        # The first attempt sees the file as it is on disk; a later attempt sees
        # the candidate that just failed the tests, so it can build on it.
        prompt = build_repair_prompt(path, current, context, test_output, sim_hints=sim_block)
        if len(prompt) > REPAIR_CONTEXT_CHARS:
            result.final_error = (
                f"the repair prompt is {len(prompt):,} characters, over the "
                f"{REPAIR_CONTEXT_CHARS:,} budget; refusing to trim the file"
            )
            break
        try:
            answer = await _ask(complete_fn, prompt, result, llm_mod, source=current)
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
        assert candidate is not None

        try:
            await ws.write_file(
                workspace_id, path, candidate, expected_sha256=_sha256(current), user=user
            )
        except ws.WorkspaceError as exc:
            result.final_error = str(exc)
            break
        current = candidate

        try:
            gate = await ws.run_pytest(workspace_id, targets, user=user)
        except ws.WorkspaceError as exc:
            # The gate broke, not the code: no verdict, so the candidate is not kept.
            result.final_error = str(exc)
            result.guards.append(f"attempt {attempt}: the test gate could not run")
            break
        test_output = _gate_output(gate)
        result.test_output = test_output
        if gate["outcome"] == "passed":
            result.tests_passed = True
            result.status = "repaired"
            result.final_error = ""
            await _record_repair_learning(result, sim_items, passed=True)
            return result
        if gate["outcome"] == "error":
            result.final_error = (
                f"the test gate did not run the tests (pytest exit code {gate['returncode']})"
            )
            result.guards.append(f"attempt {attempt}: {result.final_error}")
            break

        result.tests_passed = False
        result.final_error = test_output or "the tests still fail"
        context = result.final_error

    # Every attempt failed: leave the workspace exactly as it was found.
    if current != original:
        try:
            await ws.write_file(
                workspace_id, path, original, expected_sha256=_sha256(current), user=user
            )
        except ws.WorkspaceError as exc:
            result.status = "error"
            result.guards.append(f"could not restore the original file: {exc}")
            result.message = f"the original {path} could not be restored: {exc}"

    if not result.message:
        result.message = "no attempt produced a file the tests accept"
    await _record_repair_learning(result, sim_items, passed=False)
    return result
