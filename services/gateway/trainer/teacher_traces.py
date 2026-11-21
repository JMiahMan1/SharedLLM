"""Teacher transcripts as normalised traces. Standard library only.

A *teacher* is a strong cloud model doing agentic work on this repository in
opencode or Cline. Each user turn in one of those sessions is a *task*: the
goal the person typed, the tool calls the teacher made with what each returned,
and the answer it closed with. This module turns both formats into one shape::

    {
      "schema": "teacher-trace/1",
      "id": "tt-<12 hex>",              # stable per (source, session, task)
      "source": "opencode" | "cline",
      "session_id", "task_index", "started_at", "model", "model_location",
      "project": "<directory name>",
      "goal": "<what the person asked>",
      "steps": [{"tool", "kind", "args", "output", "status", "exit", "thought"}],
      "final_answer": "...",
      "outcome": "success" | "failure" | "unverified",
      "outcome_evidence": ["..."],
      "verification": {"test_runs", "last_test_passed", "corrected"},
      "files": [...], "tools": [...], "areas": [...],
    }

Nothing here scrubs: :mod:`teacher_scrub` does, and the collector never writes
or sends a trace that has not been through it. Inputs and outputs are truncated
here so a trace stays small (``MAX_*``), with a visible marker.

**Outcome** is judged from evidence, never from the teacher's own claim:

* a later user message that corrects the task ("that didn't work", "still
  failing", "revert that") makes it a ``failure``;
* otherwise the *last* test-like command decides: passing is ``success``,
  failing is ``failure``;
* no test run at all is ``unverified`` (kept for evaluation, never a lesson).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time
from collections.abc import Iterable, Iterator
from typing import Any

SCHEMA = "teacher-trace/1"

MAX_GOAL_CHARS = 2000
MAX_ARG_CHARS = 1500
MAX_OUTPUT_CHARS = 1500
MAX_THOUGHT_CHARS = 600
MAX_ANSWER_CHARS = 3000
MAX_STEPS = 80

#: Providers that run on the household's own hardware. Everything else is cloud.
DEFAULT_LOCAL_PROVIDERS = ("ollama", "ollama-moe", "alpaca", "llamacpp", "llama.cpp", "lmstudio", "local")

_TEST_COMMAND = re.compile(
    r"(?i)\b(?:pytest|py\.test|vitest|jest|tsc\b|mypy|ruff\s+check|eslint|go\s+test|cargo\s+(?:test|check|clippy)"
    r"|npm\s+(?:run\s+)?(?:test|typecheck|lint|build)|npx\s+(?:vitest|playwright|tsc|eslint|jest)"
    r"|node\s+--test|playwright\s+test|python3?\s+-m\s+(?:pytest|unittest|py_compile|compileall)|make\s+test)\b"
)
_TEST_FAILED = re.compile(
    r"(?m)\b[1-9]\d*\s+(?:failed|errors?)\b|^FAILED\b|^ERROR\b|\berror TS\d+|Traceback \(most recent call last\)"
    r"|\bFound [1-9]\d* errors?\b|\b[1-9]\d* problems?\b|npm ERR!|\bexit code [1-9]"
)
_TEST_PASSED = re.compile(
    r"(?im)\b\d+\s+passed\b|\bAll checks passed\b|\bTests?\s+\d+\s+passed|\bSuccess: no issues found"
    r"|\bok\s*\(\d+|\b0 errors?\b|^\s*✓|\bpass\s+\d+\b"
)
_CORRECTION = re.compile(
    r"(?ix)^\s*(?:no\b|nope\b|wrong\b|revert\b|undo\b|stop\b)"
    r"|\b(?:did\s*n[o']?t|does\s*n[o']?t|is\s*n[o']?t|still\s+(?:not|broken|failing|fails|wrong|doesn|isn|the\s+same))\s*(?:work|fix|help|right)?"
    r"|\b(?:that(?:'s|\s+is)\s+(?:wrong|not\s+(?:right|it|what))|you\s+broke|it\s+broke|broke\s+it|not\s+fixed|same\s+error)\b"
)


def trace_id(source: str, session_id: str, task_index: int) -> str:
    digest = hashlib.sha1(f"{source}\x00{session_id}\x00{task_index}".encode()).hexdigest()[:12]
    return f"tt-{digest}"


def _iso(ms: int | float | None) -> str | None:
    if not ms:
        return None
    seconds = float(ms) / 1000.0 if float(ms) > 1e11 else float(ms)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def truncate(text: Any, limit: int) -> str:
    """Head and tail of an over-long text, joined by a visible marker."""
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    head = int(limit * 0.7)
    tail = max(0, limit - head - 40)
    return f"{value[:head]}\n...[truncated {len(value) - head - tail} chars]...\n{value[-tail:] if tail else ''}"


def _truncate_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        if value is None or value == "":
            continue
        if isinstance(value, str):
            out[key] = truncate(value, MAX_ARG_CHARS)
        elif isinstance(value, list):
            out[key] = [truncate(v, MAX_ARG_CHARS) if isinstance(v, str) else v for v in value[:20]]
        else:
            out[key] = value
    return out


def model_location(provider: str, local_providers: Iterable[str] = DEFAULT_LOCAL_PROVIDERS) -> str:
    provider = (provider or "").lower()
    local = {p.lower() for p in local_providers}
    if provider in local or provider.startswith("ollama"):
        return "local"
    return "cloud"


def _relative(path: str, root: str) -> str:
    if not path:
        return path
    if root and path.startswith(root.rstrip("/") + "/"):
        return path[len(root.rstrip("/")) + 1 :]
    return path


# ---------------------------------------------------------------------------
# One tool call, normalised
# ---------------------------------------------------------------------------

#: Tool name (any source) -> kind. Kinds are what the gateway maps to Raven tools.
_KINDS = {
    "shell": "shell", "bash": "shell", "run_commands": "shell", "execute_command": "shell",
    "read": "read", "read_files": "read", "read_file": "read",
    "write": "write", "write_to_file": "write",
    "edit": "edit", "editor": "edit", "replace_in_file": "edit", "patch": "edit",
    "grep": "search", "search_codebase": "search", "search_files": "search",
    "glob": "list", "list": "list", "list_files": "list",
    "webfetch": "web", "websearch": "web", "fetch_web_content": "web",
    "question": "ask", "ask_question": "ask", "ask_followup_question": "ask",
    "todowrite": "plan", "todoread": "plan", "compress": "context", "skill": "context",
    "subagent": "delegate", "task": "delegate", "explore": "delegate",
}


def _normalise_args(kind: str, raw: dict[str, Any], root: str) -> dict[str, Any]:
    """The arguments that matter for a kind, under one set of names."""
    raw = raw if isinstance(raw, dict) else {}
    if kind == "shell":
        commands = raw.get("commands")
        if isinstance(commands, list) and commands:
            return {"command": " && ".join(str(c) for c in commands)}
        return {"command": str(raw.get("command") or raw.get("code") or "")}
    if kind == "read":
        files = raw.get("files")
        if isinstance(files, list) and files:
            paths = [_relative(str(f.get("path") if isinstance(f, dict) else f), root) for f in files]
            return {"file_path": paths[0], "more_paths": paths[1:]} if len(paths) > 1 else {"file_path": paths[0]}
        return {
            "file_path": _relative(str(raw.get("path") or raw.get("filePath") or raw.get("file_path") or ""), root),
            "offset_lines": raw.get("offset"),
            "limit_lines": raw.get("limit"),
        }
    if kind == "write":
        return {
            "file_path": _relative(str(raw.get("path") or raw.get("filePath") or raw.get("file_path") or ""), root),
            "content": raw.get("content"),
        }
    if kind == "edit":
        old = raw.get("oldString", raw.get("old_text", raw.get("old_str")))
        new = raw.get("newString", raw.get("new_text", raw.get("new_str")))
        args = {
            "file_path": _relative(str(raw.get("path") or raw.get("filePath") or raw.get("file_path") or ""), root),
            "old_text": old,
            "new_text": new,
        }
        if not old and new is not None:
            return {"file_path": args["file_path"], "content": new, "created": True}
        return args
    if kind == "search":
        queries = raw.get("queries")
        query = " | ".join(str(q) for q in queries) if isinstance(queries, list) else raw.get("pattern") or raw.get("query") or raw.get("regex")
        return {"query": query, "path": _relative(str(raw.get("path") or ""), root), "include": raw.get("include") or raw.get("file_pattern")}
    if kind == "list":
        return {"pattern": raw.get("pattern"), "path": _relative(str(raw.get("path") or ""), root)}
    if kind == "web":
        requests = raw.get("requests")
        if isinstance(requests, list) and requests and isinstance(requests[0], dict):
            return {"url": requests[0].get("url")}
        return {"url": raw.get("url"), "query": raw.get("query")}
    return {key: value for key, value in raw.items() if isinstance(value, str | int | float | bool)}


def make_step(
    tool: str,
    raw_args: dict[str, Any],
    output: Any,
    *,
    status: str = "completed",
    exit_code: int | None = None,
    thought: str = "",
    root: str = "",
) -> dict[str, Any]:
    kind = _KINDS.get((tool or "").lower(), "other")
    args = _normalise_args(kind, raw_args, root)
    return {
        "tool": tool,
        "kind": kind,
        "args": _truncate_args(args),
        "output": truncate(output, MAX_OUTPUT_CHARS),
        "status": status,
        "exit": exit_code,
        "thought": truncate(thought, MAX_THOUGHT_CHARS) if thought else "",
    }


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


def is_test_command(command: str) -> bool:
    return bool(_TEST_COMMAND.search(command or ""))


def test_passed(step: dict[str, Any]) -> bool:
    """Did a test-like step pass? The exit code wins; the output decides otherwise."""
    if step.get("status") == "error":
        return False
    exit_code = step.get("exit")
    if isinstance(exit_code, int):
        return exit_code == 0
    output = str(step.get("output") or "")
    if _TEST_FAILED.search(output):
        return False
    return bool(_TEST_PASSED.search(output))


def is_correction(text: str) -> bool:
    return bool(_CORRECTION.search((text or "")[:400]))


def judge_outcome(steps: list[dict[str, Any]], next_user_text: str | None) -> tuple[str, list[str], dict[str, Any]]:
    tests = [s for s in steps if s.get("kind") == "shell" and is_test_command(str((s.get("args") or {}).get("command") or ""))]
    corrected = bool(next_user_text) and is_correction(next_user_text or "")
    last_passed = test_passed(tests[-1]) if tests else None
    evidence: list[str] = []
    if corrected:
        evidence.append("the next message corrected this task")
        outcome = "failure"
    elif tests:
        command = str((tests[-1].get("args") or {}).get("command") or "")[:160]
        evidence.append(f"last check {'passed' if last_passed else 'failed'}: {command}")
        outcome = "success" if last_passed else "failure"
    else:
        evidence.append("no test or check was run")
        outcome = "unverified"
    return outcome, evidence, {"test_runs": len(tests), "last_test_passed": last_passed, "corrected": corrected}


# ---------------------------------------------------------------------------
# A task -> a trace
# ---------------------------------------------------------------------------

_AREA_ROOTS = ("services", "tools", "scripts", "docs", "prompts", "tests", "web", "arcade", "agent")


def areas_of(files: Iterable[str]) -> list[str]:
    """``services/gateway/x.py`` -> ``services/gateway``; ``scripts/a.py`` -> ``scripts``."""
    found: list[str] = []
    for path in files:
        parts = [p for p in str(path).split("/") if p and p != "."]
        if not parts:
            continue
        if parts[0] == "services" and len(parts) > 2:
            area = "/".join(parts[:2])
        elif parts[0] in _AREA_ROOTS or len(parts) > 1:
            area = parts[0]
        else:
            area = "root"
        if area not in found:
            found.append(area)
    return found[:6]


def build_trace(
    *,
    source: str,
    session_id: str,
    task_index: int,
    started_at: str | None,
    model: str,
    location: str,
    project: str,
    goal: str,
    steps: list[dict[str, Any]],
    final_answer: str,
    next_user_text: str | None,
) -> dict[str, Any]:
    outcome, evidence, verification = judge_outcome(steps, next_user_text)
    files: list[str] = []
    for step in steps:
        if step["kind"] in ("write", "edit"):
            path = str(step["args"].get("file_path") or "")
            if path and path not in files:
                files.append(path)
    tools: list[str] = []
    for step in steps:
        if step["tool"] not in tools:
            tools.append(step["tool"])
    truncated = len(steps) > MAX_STEPS
    if truncated:
        steps = steps[: MAX_STEPS - 20] + steps[-20:]
    return {
        "schema": SCHEMA,
        "id": trace_id(source, session_id, task_index),
        "source": source,
        "session_id": session_id,
        "task_index": task_index,
        "started_at": started_at,
        "model": model,
        "model_location": location,
        "project": project,
        "goal": truncate(goal.strip(), MAX_GOAL_CHARS),
        "steps": steps,
        "steps_truncated": truncated,
        "final_answer": truncate(final_answer.strip(), MAX_ANSWER_CHARS),
        "outcome": outcome,
        "outcome_evidence": evidence,
        "verification": verification,
        "files": files[:40],
        "tools": tools,
        "areas": areas_of(files),
    }


# ---------------------------------------------------------------------------
# opencode (SQLite: session_v2 + session_message)
# ---------------------------------------------------------------------------


def _opencode_output(state: dict[str, Any]) -> str:
    if state.get("status") == "error":
        error = state.get("error") or {}
        return str(error.get("message") if isinstance(error, dict) else error)
    parts = state.get("content") or []
    if isinstance(parts, list):
        return "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
    return str(state.get("output") or "")


def opencode_tasks(
    session: dict[str, Any],
    messages: Iterable[tuple[str, int, int, str]],
    *,
    local_providers: Iterable[str] = DEFAULT_LOCAL_PROVIDERS,
) -> list[dict[str, Any]]:
    """The tasks of one opencode session.

    ``session``: a ``session_v2`` row as a dict (``id``, ``directory``,
    ``model``). ``messages``: ``(type, seq, time_created, data)`` rows of
    ``session_message`` for that session, in ``seq`` order.
    """
    root = str(session.get("directory") or "")
    project = pathlib.PurePath(root).name if root else ""
    default_model = _opencode_model(session.get("model"))
    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def close(next_text: str | None) -> None:
        if current is None:
            return
        tasks.append(
            build_trace(
                source="opencode",
                session_id=str(session["id"]),
                task_index=current["index"],
                started_at=current["started_at"],
                model=current["model"] or default_model[0],
                location=model_location(current["provider"] or default_model[1], local_providers),
                project=project,
                goal=current["goal"],
                steps=current["steps"],
                final_answer=current["answer"],
                next_user_text=next_text,
            )
        )

    index = 0
    for kind, _seq, created, raw in messages:
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except ValueError:
            continue
        if kind == "user":
            text = str(data.get("text") or "")
            close(text)
            current = {
                "index": index, "goal": text, "steps": [], "answer": "", "thought": "",
                "started_at": _iso(created), "model": "", "provider": "",
            }
            index += 1
            continue
        if kind != "assistant" or current is None:
            continue
        model, provider = _opencode_model(data.get("model"))
        if model:
            current["model"], current["provider"] = model, provider
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "reasoning":
                current["thought"] = str(block.get("text") or "")
            elif btype == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    current["answer"] = text
            elif btype == "tool":
                state = block.get("state") or {}
                meta = state.get("metadata") or {}
                current["steps"].append(
                    make_step(
                        str(block.get("name") or block.get("tool") or ""),
                        state.get("input") or {},
                        _opencode_output(state),
                        status=str(state.get("status") or "completed"),
                        exit_code=meta.get("exit") if isinstance(meta.get("exit"), int) else None,
                        thought=current["thought"],
                        root=root,
                    )
                )
                current["thought"] = ""
                current["answer"] = ""
    close(None)
    return tasks


def _opencode_model(value: Any) -> tuple[str, str]:
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return value, value.split("/", 1)[0]
    if isinstance(data, dict):
        provider = str(data.get("providerID") or "")
        model_id = str(data.get("id") or data.get("modelID") or "")
        return (f"{provider}/{model_id}" if provider else model_id), provider
    return "", ""


# ---------------------------------------------------------------------------
# Cline (~/.cline/data/sessions/<id>/<id>.messages.json + <id>.json)
# ---------------------------------------------------------------------------


def _cline_result_text(content: Any) -> tuple[str, bool]:
    if isinstance(content, str):
        return content, True
    if isinstance(content, list):
        texts: list[str] = []
        ok = True
        for item in content:
            if isinstance(item, dict):
                texts.append(str(item.get("result") or item.get("text") or ""))
                if item.get("success") is False:
                    ok = False
            else:
                texts.append(str(item))
        return "\n".join(texts), ok
    return "" if content is None else str(content), True


def cline_tasks(
    meta: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    local_providers: Iterable[str] = DEFAULT_LOCAL_PROVIDERS,
) -> list[dict[str, Any]]:
    """The tasks of one Cline session (``meta`` is ``<id>.json``)."""
    session_id = str(meta.get("session_id") or meta.get("sessionId") or "")
    root = str(meta.get("workspace_root") or meta.get("cwd") or "")
    project = pathlib.PurePath(root).name if root else ""
    provider = str(meta.get("provider") or "")
    model = str(meta.get("model") or "")
    full_model = f"{provider}/{model}" if provider and model else model or provider
    location = model_location(provider, local_providers)

    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending: dict[str, dict[str, Any]] = {}
    index = 0

    def close(next_text: str | None) -> None:
        if current is None:
            return
        tasks.append(
            build_trace(
                source="cline", session_id=session_id, task_index=current["index"],
                started_at=current["started_at"], model=full_model, location=location, project=project,
                goal=current["goal"], steps=current["steps"], final_answer=current["answer"],
                next_user_text=next_text,
            )
        )

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
        if role == "user":
            texts = [str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            text = "\n".join(t for t in texts if t.strip())
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result" or current is None:
                    continue
                call = pending.pop(str(block.get("tool_use_id") or ""), None)
                if call is None:
                    continue
                output, ok = _cline_result_text(block.get("content"))
                current["steps"].append(
                    make_step(call["name"], call["input"], output, status="completed" if ok else "error",
                              thought=call["thought"], root=root)
                )
            if text.strip():
                close(text)
                current = {"index": index, "goal": text, "steps": [], "answer": "", "thought": "",
                           "started_at": _iso(message.get("ts"))}
                index += 1
                pending.clear()
            continue
        if role != "assistant" or current is None:
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                current["thought"] = str(block.get("thinking") or "")
            elif btype == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    current["answer"] = text
            elif btype == "tool_use":
                pending[str(block.get("id") or "")] = {
                    "name": str(block.get("name") or ""),
                    "input": block.get("input") or {},
                    "thought": current["thought"],
                }
                current["thought"] = ""
                current["answer"] = ""
    close(None)
    return tasks


def iter_steps(trace: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from trace.get("steps") or []
