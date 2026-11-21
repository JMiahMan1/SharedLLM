"""Teacher traces in the trainer: store, worked-example lessons, eval cases, fine-tune rows.

Strong cloud models (the *teachers*) do agentic work on this repository in
opencode and Cline. ``scripts/collect_teacher_traces.py`` turns those sessions
into scrubbed traces on the machine that holds them (see
:mod:`teacher_traces` and :mod:`teacher_scrub`) and posts them here. This module
uses them three ways, all through the trainer that already exists:

* **Worked-example lessons.** A verified, successful trace becomes one compact
  ``system_learnings`` lesson ("for a task like X, the steps that worked were
  ...") which the orchestrator already retrieves into every mission, so Raven
  gets few-shot help by retrieval. Deduplicated, capped, tagged by tool and area,
  written at a moderate confidence that dreaming then weighs like any lesson.
* **Evaluation cases.** A trace with edited files becomes a ``teacher`` bucket
  :class:`~services.gateway.trainer.curriculum.Case`: the teacher's task as the
  prompt, and checks a script can trust (the files the teacher changed, the
  check it ran). The runner's reports then show the local model's progress on
  teacher tasks over time, and failures feed the backlog as before.
* **Fine-tuning rows.** Chat-format JSONL (OpenAI ``messages`` with
  ``tool_calls``, or ShareGPT/Hermes ``conversations``) with Raven's real system
  prompt and the teacher's tool calls mapped onto Raven's own tools, split into
  train and eval files. See ``docs/TEACHER_TRACES.md`` for training it.

The gateway never sees a raw transcript. It refuses a trace that was scrubbed by
another scrubber version, a dropped trace that still carries content, and any
trace in which :func:`teacher_scrub.leak_check` still finds a secret.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import time
from collections import Counter
from typing import Any

from services.gateway.trainer import runner
from services.gateway.trainer import teacher_scrub as scrub_mod
from services.gateway.trainer.curriculum import DEFAULT_SYSTEM, Case

log = logging.getLogger("gateway.trainer.teacher")

#: Accounts besides admins that may post traces (a dedicated agent account).
#: Read once at startup, like every other setting the gateway takes from env.
UPLOADERS = frozenset(
    name.strip() for name in os.getenv("TRAINER_TEACHER_UPLOADERS", "").split(",") if name.strip()
)

MAX_TRACES_PER_UPLOAD = 200
MAX_TRACE_BYTES = 400_000
MAX_LESSON_RULE_CHARS = 480
LESSON_CONFIDENCE = 0.6
MAX_TEACHER_CASES = 40

_TRACE_ID = re.compile(r"^tt-[0-9a-f]{12}$")
_OUTCOMES = {"success", "failure", "unverified"}
_STATUSES = {"clean", "masked", "dropped"}

#: A dropped trace may carry these keys and nothing else.
_STUB_KEYS = {
    "schema", "id", "source", "session_id", "task_index", "started_at", "model",
    "model_location", "outcome", "step_count", "scrub",
}


class TeacherError(ValueError):
    """A trace (or a request about traces) is malformed or unsafe."""


def teacher_dir() -> pathlib.Path:
    return runner.DATA_DIR / "teacher"


def _traces_dir() -> pathlib.Path:
    return teacher_dir() / "traces"


def _write_json(path: pathlib.Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise runner.TrainerStorageError(f"Could not write {path}: {exc}") from exc


def _read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise runner.TrainerStorageError(f"{path} exists but cannot be read: {exc}") from exc


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _content_of(trace: dict[str, Any]) -> dict[str, Any]:
    return {key: trace.get(key) for key in ("goal", "final_answer", "project", "steps", "files", "outcome_evidence")}


def check_trace(trace: Any) -> dict[str, Any]:
    """The trace as it will be stored, or :class:`TeacherError` saying why not."""
    if not isinstance(trace, dict):
        raise TeacherError("a trace must be a JSON object")
    trace_id = str(trace.get("id") or "")
    if not _TRACE_ID.match(trace_id):
        raise TeacherError(f"bad trace id {trace_id[:40]!r}")
    if trace.get("schema") != "teacher-trace/1":
        raise TeacherError("unknown trace schema; re-collect with the current scripts/collect_teacher_traces.py")
    scrub = trace.get("scrub")
    if not isinstance(scrub, dict):
        raise TeacherError("the trace was never scrubbed")
    if str(scrub.get("version")) != scrub_mod.SCRUB_VERSION:
        raise TeacherError(
            f"scrubbed by rules version {scrub.get('version')!r}; this gateway needs {scrub_mod.SCRUB_VERSION!r}"
        )
    status = scrub.get("status")
    if status not in _STATUSES:
        raise TeacherError(f"unknown scrub status {status!r}")
    if len(json.dumps(trace, ensure_ascii=False)) > MAX_TRACE_BYTES:
        raise TeacherError(f"the trace is larger than {MAX_TRACE_BYTES} bytes")
    if status == "dropped":
        extra = sorted(set(trace) - _STUB_KEYS)
        if extra:
            raise TeacherError(f"a dropped trace must not carry content (found: {', '.join(extra)})")
        return dict(trace)
    if not str(trace.get("goal") or "").strip():
        raise TeacherError("the trace has no goal")
    if not isinstance(trace.get("steps"), list):
        raise TeacherError("the trace has no step list")
    if trace.get("outcome") not in _OUTCOMES:
        raise TeacherError(f"unknown outcome {trace.get('outcome')!r}")
    leaks = scrub_mod.leak_check(_content_of(trace))
    if leaks:
        # Kinds only: the value itself is exactly what must not be echoed.
        raise TeacherError(f"the trace still contains secrets ({', '.join(leaks)}); it was not stored")
    return dict(trace)


def import_traces(traces: list[Any], *, uploader: str) -> dict[str, Any]:
    """Store every acceptable trace (upsert by id) and report the rest."""
    if not isinstance(traces, list):
        raise TeacherError("traces must be a list")
    if len(traces) > MAX_TRACES_PER_UPLOAD:
        raise TeacherError(f"at most {MAX_TRACES_PER_UPLOAD} traces per upload, not {len(traces)}")
    runner.ensure_data_dir(_traces_dir())
    accepted: list[str] = []
    updated: list[str] = []
    refused: list[dict[str, str]] = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for item in traces:
        try:
            trace = check_trace(item)
        except TeacherError as exc:
            refused.append({"id": str(item.get("id") if isinstance(item, dict) else "")[:40], "reason": str(exc)})
            continue
        path = _traces_dir() / f"{trace['id']}.json"
        existing = _read_json(path, None)
        trace["imported_at"] = (existing or {}).get("imported_at") or now
        trace["updated_at"] = now
        trace["uploaded_by"] = uploader
        _write_json(path, trace)
        (updated if existing else accepted).append(trace["id"])
    return {"accepted": accepted, "updated": updated, "refused": refused}


def load_trace(trace_id: str) -> dict[str, Any] | None:
    if not _TRACE_ID.match(trace_id or ""):
        return None
    return _read_json(_traces_dir() / f"{trace_id}.json", None)


def delete_trace(trace_id: str) -> bool:
    if not _TRACE_ID.match(trace_id or ""):
        return False
    path = _traces_dir() / f"{trace_id}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise runner.TrainerStorageError(f"Could not delete {path}: {exc}") from exc
    return True


def all_traces() -> list[dict[str, Any]]:
    """Every stored trace, newest first. An unreadable file is an error, not a gap."""
    directory = _traces_dir()
    if not directory.is_dir():
        return []
    traces = [_read_json(path, {}) for path in sorted(directory.glob("tt-*.json"))]
    traces.sort(key=lambda t: str(t.get("started_at") or ""), reverse=True)
    return traces


def _lessons_index() -> dict[str, Any]:
    return _read_json(teacher_dir() / "lessons.json", {"lessons": {}})


def trace_summary(trace: dict[str, Any], lessons: dict[str, Any] | None = None) -> dict[str, Any]:
    scrub = trace.get("scrub") or {}
    lesson = (lessons or {}).get(trace.get("id")) or {}
    case = case_for(trace) if scrub.get("status") != "dropped" else None
    return {
        "id": trace.get("id"),
        "source": trace.get("source"),
        "model": trace.get("model"),
        "model_location": trace.get("model_location"),
        "started_at": trace.get("started_at"),
        "imported_at": trace.get("imported_at"),
        "goal": str(trace.get("goal") or "")[:200],
        "outcome": trace.get("outcome"),
        "outcome_evidence": trace.get("outcome_evidence") or [],
        "step_count": trace.get("step_count", len(trace.get("steps") or [])),
        "tools": trace.get("tools") or [],
        "areas": trace.get("areas") or [],
        "scrub_status": scrub.get("status"),
        "scrub_masked": sum((scrub.get("masked") or {}).values()),
        "scrub_dropped_steps": scrub.get("dropped_steps") or 0,
        "drop_reason": scrub.get("drop_reason"),
        "lesson_id": lesson.get("lesson_id"),
        "case_id": case.id if case else None,
    }


def list_traces(*, limit: int = 100, outcome: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    lessons = _lessons_index()["lessons"]
    rows = []
    for trace in all_traces():
        if outcome and trace.get("outcome") != outcome:
            continue
        if status and (trace.get("scrub") or {}).get("status") != status:
            continue
        rows.append(trace_summary(trace, lessons))
        if len(rows) >= limit:
            break
    return rows


# ---------------------------------------------------------------------------
# Worked-example lessons
# ---------------------------------------------------------------------------


def _short(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _basename(path: str) -> str:
    return pathlib.PurePosixPath(path).name or path


def _step_phrase(step: dict[str, Any]) -> tuple[str, str]:
    """(verb, object) for one step, so consecutive steps can be merged."""
    kind = step.get("kind")
    args = step.get("args") or {}
    if kind == "read":
        return "read", _basename(str(args.get("file_path") or ""))
    if kind in ("edit", "write"):
        return "edited", _basename(str(args.get("file_path") or ""))
    if kind == "search":
        return "searched for", f"'{_short(str(args.get('query') or ''), 40)}'"
    if kind == "shell":
        command = _short(str(args.get("command") or ""), 70)
        from services.gateway.trainer.teacher_traces import is_test_command

        return ("checked with", f"`{command}`") if is_test_command(command) else ("ran", f"`{command}`")
    return "", ""


def lesson_rule(trace: dict[str, Any]) -> str:
    """"For a task like X (area): read a, b; edited c; checked with `pytest`." Capped."""
    parts: list[tuple[str, list[str]]] = []
    for step in trace.get("steps") or []:
        if step.get("status") == "error":
            continue
        verb, obj = _step_phrase(step)
        if not verb or not obj.strip("'` "):
            continue
        if parts and parts[-1][0] == verb:
            if obj not in parts[-1][1]:
                parts[-1][1].append(obj)
        else:
            parts.append((verb, [obj]))
    phrases = [f"{verb} {', '.join(objs[:4])}{' and more' if len(objs) > 4 else ''}" for verb, objs in parts]
    areas = ", ".join(trace.get("areas") or []) or "this repository"
    head = f'For a task like "{_short(str(trace.get("goal") or ""), 140)}" ({areas}), the steps that worked: '
    body = ""
    for phrase in phrases:
        candidate = f"{body}; {phrase}" if body else phrase
        if len(head) + len(candidate) + 1 > MAX_LESSON_RULE_CHARS:
            break
        body = candidate
    return _short(head + body + ".", MAX_LESSON_RULE_CHARS)


def lesson_id_for(trace: dict[str, Any]) -> str:
    key = _short(str(trace.get("goal") or "").lower(), 300) + "\x00" + ",".join(trace.get("areas") or [])
    return "lesson-teacher-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def lesson_worthy(trace: dict[str, Any]) -> str | None:
    """Why a trace cannot be a lesson, or ``None`` when it can."""
    scrub = trace.get("scrub") or {}
    if scrub.get("status") == "dropped":
        return "dropped by the scrubber"
    if trace.get("outcome") != "success":
        return f"outcome is {trace.get('outcome')}, not a verified success"
    useful = [s for s in trace.get("steps") or [] if s.get("kind") in ("read", "edit", "write", "search", "shell")]
    if len(useful) < 2:
        return "fewer than two useful steps"
    return None


def lesson_payload(trace: dict[str, Any], *, user_id: str = "default") -> dict[str, Any]:
    """A trace as a ``system_learnings`` document, the shape every lesson has."""
    lesson_id = lesson_id_for(trace)
    kinds = sorted({str(s.get("kind")) for s in trace.get("steps") or [] if s.get("kind")})
    tags = ["teacher", "worked_example", *kinds, *(trace.get("areas") or [])]
    rule = lesson_rule(trace)
    root_cause = f"Worked example from teacher trace {trace['id']} ({trace.get('model') or 'unknown model'})"
    summary = _short(
        f"{trace.get('goal') or ''} Outcome: {'; '.join(trace.get('outcome_evidence') or [])}", 400
    )
    fields = {
        "id": lesson_id,
        "topic": f"worked example: {', '.join(trace.get('areas') or []) or 'general'}",
        "rule": rule,
        "root_cause": root_cause,
        "outcome": "success",
        "confidence": LESSON_CONFIDENCE,
        "tags": tags,
        "summary": summary,
    }
    return {
        "user_id": user_id,
        "content": json.dumps(fields, ensure_ascii=False),
        "collection_name": "system_learnings",
        "metadata": {
            **fields,
            "type": "worked_example",
            "supersedes": [],
            "source_trace": trace["id"],
            "teacher_model": trace.get("model"),
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]{3,}", (text or "").lower())}


def _similar(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


async def write_lessons(*, user_id: str = "default", limit: int = 20, dry_run: bool = False) -> dict[str, Any]:
    """Turn verified traces into worked-example lessons in RAG (deduplicated)."""
    from services.gateway.trainer import dream as dream_mod

    index = _lessons_index()
    lessons: dict[str, Any] = index["lessons"]
    written_goals = [str(item.get("goal") or "") for item in lessons.values()]
    written: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    for trace in all_traces():
        if len(written) >= limit:
            break
        trace_id = str(trace.get("id"))
        if trace_id in lessons:
            continue
        reason = lesson_worthy(trace)
        if reason:
            skipped.append({"id": trace_id, "reason": reason})
            continue
        goal = str(trace.get("goal") or "")
        twin = next((g for g in written_goals if _similar(goal, g) >= 0.8), None)
        if twin is not None:
            skipped.append({"id": trace_id, "reason": "a lesson for the same task already exists"})
            continue
        payload = lesson_payload(trace, user_id=user_id)
        if not dry_run:
            try:
                await dream_mod.request("POST", "/rag/ingest", body=payload, timeout=30.0)
            except dream_mod.RagError as exc:
                failed.append({"id": trace_id, "error": str(exc)})
                continue
            lessons[trace_id] = {
                "lesson_id": payload["metadata"]["id"],
                "goal": _short(goal, 300),
                "written_at": payload["metadata"]["indexed_at"],
            }
        written_goals.append(goal)
        written.append({"id": trace_id, "lesson_id": payload["metadata"]["id"], "rule": payload["metadata"]["rule"]})
    if not dry_run and written:
        _write_json(teacher_dir() / "lessons.json", index)
    return {"written": written, "skipped": skipped, "failed": failed, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# Evaluation cases
# ---------------------------------------------------------------------------

_GENERIC_FILES = {"__init__.py", "index.ts", "index.tsx", "index.js", "README.md", "package.json"}
_RUNNERS = ("pytest", "vitest", "playwright", "tsc", "mypy", "ruff", "eslint", "jest", "cargo", "go test", "node --test")


def case_for(trace: dict[str, Any]) -> Case | None:
    """A checkable case from a trace, or ``None`` when nothing in it is checkable."""
    if (trace.get("scrub") or {}).get("status") == "dropped" or trace.get("outcome") != "success":
        return None
    files = [f for f in trace.get("files") or [] if _basename(f) not in _GENERIC_FILES and len(_basename(f)) >= 4]
    names: list[str] = []
    for path in files:
        name = _basename(path)
        if "[REDACTED" in name or "[PERSON]" in name or name in names:
            continue
        names.append(name)
        if len(names) == 2:
            break
    if not names:
        return None
    checks: dict[str, Any] = {"must_include": names, "max_chars": 1500}
    from services.gateway.trainer.teacher_traces import is_test_command

    tests = [
        str((s.get("args") or {}).get("command") or "")
        for s in trace.get("steps") or []
        if s.get("kind") == "shell" and is_test_command(str((s.get("args") or {}).get("command") or ""))
    ]
    if tests:
        runner_name = next((r for r in _RUNNERS if r in tests[-1]), None)
        if runner_name:
            checks["must_match"] = [r"(?i)\b" + re.escape(runner_name) + r"\b"]
    project = str(trace.get("project") or "this repository")
    prompt = (
        f"A teammate asked, in the {project} repository:\n\n"
        f"{_short(str(trace.get('goal') or ''), 700)}\n\n"
        "Plan the work. In at most 8 short lines, name the files you would read or change first, "
        "in order, and the exact command you would run to prove the change works."
    )
    return Case(
        id=f"teacher-{str(trace['id'])[3:]}",
        bucket="teacher",
        title=f"Teacher task: {_short(str(trace.get('goal') or ''), 70)}",
        prompt=prompt,
        checks=checks,
        system=DEFAULT_SYSTEM,
        source=f"teacher:{trace.get('model') or 'unknown'}",
        difficulty=3,
        max_tokens=360,
        tags=("teacher", *(trace.get("areas") or [])),
    )


def teacher_cases(limit: int = MAX_TEACHER_CASES) -> list[Case]:
    """The newest checkable teacher tasks, for the curriculum's ``teacher`` bucket."""
    cases: list[Case] = []
    for trace in all_traces():
        case = case_for(trace)
        if case is not None:
            cases.append(case)
            if len(cases) >= limit:
                break
    return cases


def score_history(limit: int = 30) -> list[dict[str, Any]]:
    """The local model's pass rate on teacher cases, run by run (oldest first)."""
    history: list[dict[str, Any]] = []
    for item in runner.list_reports(limit=limit):
        if item.get("error"):
            continue
        report = runner.load_report(str(item["id"]))
        if not report:
            continue
        bucket = (report.get("by_bucket") or {}).get("teacher")
        if not bucket or not bucket.get("cases"):
            continue
        history.append(
            {
                "report_id": report.get("id"),
                "started_at": report.get("started_at"),
                "model": report.get("model"),
                "cases": bucket["cases"],
                "passed": bucket["passed"],
                "pass_rate": bucket["pass_rate"],
            }
        )
    history.sort(key=lambda row: str(row.get("started_at") or ""))
    return history


# ---------------------------------------------------------------------------
# Fine-tuning rows
# ---------------------------------------------------------------------------

_GIT_ACTIONS = {"status", "diff", "add", "commit", "pull", "push", "log", "branch", "checkout"}


def raven_call(step: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The Raven tool call a teacher step corresponds to, or ``None`` (no equivalent)."""
    kind = step.get("kind")
    args = step.get("args") or {}
    if kind == "shell":
        command = str(args.get("command") or "").strip()
        if not command:
            return None
        match = re.match(r"^git\s+([a-z-]+)(.*)$", command, re.S)
        if match and match.group(1) in _GIT_ACTIONS and "&&" not in command and "|" not in command:
            payload: dict[str, Any] = {"action": match.group(1)}
            message = re.search(r"""-m\s+(["'])(.*?)\1""", match.group(2), re.S)
            if match.group(1) == "commit" and message:
                payload["commit_message"] = message.group(2)
            if match.group(1) in ("checkout", "branch"):
                target = match.group(2).split()
                if target and not target[-1].startswith("-"):
                    payload["branch"] = target[-1]
            return "GitOperationRequest", payload
        return "WorkspaceShellRequest", {"command": command}
    if kind == "read" and args.get("file_path"):
        payload = {"file_path": args["file_path"]}
        for key in ("offset_lines", "limit_lines"):
            if isinstance(args.get(key), int):
                payload[key] = args[key]
        return "WorkspaceFileReadRequest", payload
    if kind == "write" or (kind == "edit" and args.get("created")):
        if not args.get("file_path"):
            return None
        return "WorkspaceFileWriteRequest", {"file_path": args["file_path"], "content": args.get("content") or ""}
    if kind == "edit" and args.get("file_path"):
        return "WorkspaceFilePatchRequest", {
            "file_path": args["file_path"],
            "chunks": [{"old_text": args.get("old_text") or "", "new_text": args.get("new_text") or ""}],
        }
    if kind == "search" and args.get("query"):
        payload = {"query": args["query"]}
        for key in ("path", "include"):
            if args.get(key):
                payload[key] = args[key]
        return "WorkspaceSearchRequest", payload
    return None


def _tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    """OpenAI function definitions for the Raven tools a row uses."""
    from services.gateway import tool_registry

    rows = {row[0]: row for row in tool_registry._RAVEN_TOOL_TABLE}
    schemas = []
    for name in names:
        row = rows.get(name)
        if row is None:
            raise TeacherError(f"{name} is not in Raven's tool table; the mapping in teacher.raven_call is stale")
        fields = re.findall(r"[a-z_]+", str(row[6]).split(".", 1)[0].removeprefix("payload fields:"))
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(row[5]),
                    "parameters": {
                        "type": "object",
                        "properties": {field: {} for field in fields},
                    },
                },
            }
        )
    return schemas


def finetune_row(trace: dict[str, Any], system_prompt: str, *, fmt: str = "openai") -> dict[str, Any] | None:
    """One trace as one training conversation, or ``None`` when it cannot be one."""
    if not str(trace.get("final_answer") or "").strip():
        return None
    calls: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    unmapped: Counter[str] = Counter()
    for step in trace.get("steps") or []:
        mapped = raven_call(step)
        if mapped is None:
            unmapped[str(step.get("tool"))] += 1
            continue
        calls.append((step, mapped[0], mapped[1]))
    if not calls:
        return None
    meta = {
        "trace_id": trace["id"],
        "teacher_model": trace.get("model"),
        "source": trace.get("source"),
        "outcome": trace.get("outcome"),
        "areas": trace.get("areas") or [],
        "unmapped_tools": dict(unmapped),
        "steps_truncated": bool(trace.get("steps_truncated")),
    }
    names = sorted({name for _, name, _ in calls})
    tools = _tool_schemas(names)
    goal = str(trace["goal"])
    answer = str(trace["final_answer"])
    if fmt == "openai":
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, {"role": "user", "content": goal}]
        for number, (step, name, payload) in enumerate(calls, start=1):
            call_id = f"call_{number}"
            messages.append(
                {
                    "role": "assistant",
                    "content": str(step.get("thought") or ""),
                    "tool_calls": [
                        {"id": call_id, "type": "function",
                         "function": {"name": name, "arguments": json.dumps(payload, ensure_ascii=False)}}
                    ],
                }
            )
            messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": str(step.get("output") or "")})
        messages.append({"role": "assistant", "content": answer})
        return {"messages": messages, "tools": tools, "meta": meta}
    if fmt == "sharegpt":
        system = system_prompt + "\n\n<tools>\n" + json.dumps(tools, ensure_ascii=False) + "\n</tools>"
        conversations = [{"from": "system", "value": system}, {"from": "human", "value": goal}]
        for step, name, payload in calls:
            thought = str(step.get("thought") or "").strip()
            call = json.dumps({"name": name, "arguments": payload}, ensure_ascii=False)
            conversations.append({"from": "gpt", "value": (thought + "\n" if thought else "") + f"<tool_call>\n{call}\n</tool_call>"})
            conversations.append({"from": "tool", "value": f"<tool_response>\n{step.get('output') or ''}\n</tool_response>"})
        conversations.append({"from": "gpt", "value": answer})
        return {"conversations": conversations, "meta": meta}
    raise TeacherError(f"unknown format {fmt!r}; use openai or sharegpt")


async def raven_system_prompt() -> str:
    """Raven's live system prompt (Identity's ``raven_autonomous_protocol``).

    The rows must be trained on the prompt the model actually runs under, so a
    missing or unreadable prompt stops the export instead of falling back to a
    copy that may have drifted.
    """
    from services.gateway.http_client import get_http_client
    from services.gateway.prompts import PROMPT_RAVEN_AUTONOMOUS_PROTOCOL, load_prompt

    try:
        prompt = await load_prompt(get_http_client(), PROMPT_RAVEN_AUTONOMOUS_PROTOCOL)
    except Exception as exc:
        raise TeacherError(f"Raven's system prompt could not be read from Identity: {exc}") from exc
    if not str(prompt or "").strip():
        raise TeacherError("raven_autonomous_protocol is empty in Identity; seed the prompts first")
    return str(prompt)


def _split(trace_id: str, eval_share: float) -> str:
    bucket = int(hashlib.sha1(trace_id.encode()).hexdigest()[:8], 16) % 1000
    return "eval" if bucket < int(eval_share * 1000) else "train"


def export_finetune(
    system_prompt: str,
    *,
    fmt: str = "openai",
    eval_share: float = 0.1,
    include_unverified: bool = False,
    directory: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Write train/eval JSONL files for a LoRA trainer; returns paths and counts."""
    if not system_prompt.strip():
        raise TeacherError("the system prompt is empty; the rows would not match how Raven is prompted")
    if fmt not in ("openai", "sharegpt"):
        raise TeacherError(f"unknown format {fmt!r}; use openai or sharegpt")
    if not 0.0 <= eval_share < 1.0:
        raise TeacherError("eval_share must be at least 0 and below 1")
    wanted = {"success", "unverified"} if include_unverified else {"success"}
    rows: dict[str, list[dict[str, Any]]] = {"train": [], "eval": []}
    skipped: Counter[str] = Counter()
    for trace in all_traces():
        if (trace.get("scrub") or {}).get("status") == "dropped":
            skipped["dropped"] += 1
            continue
        if trace.get("outcome") not in wanted:
            skipped[f"outcome_{trace.get('outcome')}"] += 1
            continue
        row = finetune_row(trace, system_prompt, fmt=fmt)
        if row is None:
            skipped["no_mapped_steps_or_answer"] += 1
            continue
        rows[_split(str(trace["id"]), eval_share)].append(row)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = pathlib.Path(directory) if directory else runner.DATA_DIR / "datasets"
    paths: dict[str, str] = {}
    try:
        target.mkdir(parents=True, exist_ok=True)
        for split, items in rows.items():
            path = target / f"teacher-{fmt}-{stamp}-{split}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for row in items:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            paths[split] = str(path)
    except OSError as exc:
        raise runner.TrainerStorageError(f"Could not write the teacher dataset to {target}: {exc}") from exc
    result = {
        "format": fmt,
        "paths": paths,
        "train_rows": len(rows["train"]),
        "eval_rows": len(rows["eval"]),
        "skipped": dict(skipped),
        "created_at": stamp,
    }
    exports = _read_json(teacher_dir() / "exports.json", {"exports": []})
    exports["exports"] = [result, *exports["exports"]][:20]
    _write_json(teacher_dir() / "exports.json", exports)
    return result


# ---------------------------------------------------------------------------
# The Lab's view
# ---------------------------------------------------------------------------


def summary(report_limit: int = 30) -> dict[str, Any]:
    traces = all_traces()
    lessons = _lessons_index()["lessons"]
    count = Counter
    by_status: Counter[str] = count(str((t.get("scrub") or {}).get("status")) for t in traces)
    return {
        "traces": {
            "total": len(traces),
            "by_status": dict(by_status),
            "by_outcome": dict(count(str(t.get("outcome")) for t in traces)),
            "by_source": dict(count(str(t.get("source")) for t in traces)),
            "by_location": dict(count(str(t.get("model_location")) for t in traces)),
            "by_model": dict(count(str(t.get("model")) for t in traces).most_common(8)),
            "masked_values": sum(sum(((t.get("scrub") or {}).get("masked") or {}).values()) for t in traces),
            "dropped_steps": sum(int((t.get("scrub") or {}).get("dropped_steps") or 0) for t in traces),
        },
        "lessons": {
            "written": len(lessons),
            "last_written_at": max((str(v.get("written_at") or "") for v in lessons.values()), default=None),
            "eligible": sum(1 for t in traces if lesson_worthy(t) is None and t.get("id") not in lessons),
        },
        "eval_cases": len(teacher_cases()),
        "scores": score_history(report_limit),
        "exports": _read_json(teacher_dir() / "exports.json", {"exports": []})["exports"][:5],
        "scrub_version": scrub_mod.SCRUB_VERSION,
    }
