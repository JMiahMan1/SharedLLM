"""Raven's own failed, killed and looping missions, turned into lessons and tests.

The teacher pipeline learns from what strong models got right; this learns from
what the local model got wrong. Every pass (the teacher pass in the background
worker calls :func:`run_pass`) reads finished missions from Identity, server
side, and for each new one that failed:

1. **classifies the cause first.** Infrastructure (the model was unavailable, a
   timeout, a 5xx, a restart, an orphaned or pre-empted job) is recorded and
   never becomes a lesson: it is a fact about the machine that afternoon, the
   same rule ``dream.INFRASTRUCTURE_KINDS`` applies. A model mistake (a loop,
   the iteration cap, a wrong tool, bad arguments, a path that does not exist,
   ignored errors, giving up early, claiming success without verifying) is
   learnt from.
2. **writes an "avoid" lesson** in ``system_learnings``, keyed by failure kind,
   tool and the error's shape, so the same mistake is one lesson whose
   confidence grows each time it recurs (capped), not a pile of copies.
3. **keeps a ``raven_failures`` evaluation case**: the mission's goal, checked
   so that the answer does not repeat the failing call and names a way to
   verify. The teacher pass re-runs the bucket on an idle card, so the trend
   shows when the local model stops failing it.
4. keeps the failed attempt, scrubbed, so the dataset export can pair it with
   a teacher trace that solved a similar task (contrastive rows).

Mission goals, results and logs can hold household data, so every text goes
through :mod:`teacher_scrub` (with the household's names from Identity) before
it is stored, written as a lesson or exported. Without the names the pass
refuses to run (fail closed).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from services.gateway.trainer import teacher
from services.gateway.trainer import teacher_scrub as scrub_mod
from services.gateway.trainer.curriculum import DEFAULT_SYSTEM, Case

log = logging.getLogger("gateway.trainer.raven_failures")

MAX_MISSIONS_PER_PASS = 200
MAX_LESSONS_PER_PASS = 10
MAX_CASES = 60
BASE_CONFIDENCE = 0.4
CONFIDENCE_STEP = 0.1
MAX_CONFIDENCE = 0.8
MAX_STEPS_KEPT = 30

#: Kinds that are about the machine, never the model.
INFRASTRUCTURE = "infrastructure"

_INFRA = re.compile(
    r"(?i)model (?:host |is )?(?:unavailable|busy|not loaded)|localmodelunavailable|no free slot|"
    r"timed? ?out|timeout|clienttimeout|\b50[0234]\b|bad gateway|service unavailable|connection (?:refused|reset)|"
    r"cannot connect|server disconnected|orphaned mission|no backing job|restart(?:ed|ing)?|redeploy|pre-?empt|"
    r"worker (?:shut|stopp)|out of memory|cuda|oom\b|queue_timeout"
)
_LOOP = re.compile(
    r"(?i)\blooping:|infinite loop|no-progress loop|same step failed repeatedly|same action repeated|"
    r"stuck in a loop|no progress"
)
_CAP = re.compile(
    r"(?i)max(?:imum)?(?: number of)? iterations?|iteration (?:cap|limit)|"
    r"ran out of iterations|out of iterations|iterations? (?:exhausted|reached)|"
    r"reached (?:the )?(?:max(?:imum)?|iteration|limit)"
)
#: A mission killed after this many iterations was going round in circles, not
#: unlucky: the loop guard would have stopped it at the cap anyway.
KILLED_LOOP_ITERATIONS = 10
#: Repeating the same failing call this often is a loop, even below the loop
#: detector's window: by the third identical failure the model has stopped
#: reading the error.
REPEATED_FAILURES = 3
_KILLED = re.compile(
    r"(?i)mission terminated|aborted by user|cancelled by user|killed by user|"
    r"user requested cancellation"
)
_ITERATION = re.compile(r"(?i)iteration\s+(\d+)\s*(?:/|\bof\b)\s*(\d+)")
_WRAP_KEYS = ("events", "output_log", "log", "audit_log", "items")
_WRONG_TOOL = re.compile(r"(?i)unknown action|unknown tool|not a (?:valid|known) tool|no such tool")
_BAD_ARGS = re.compile(
    r"(?i)validation error|field required|missing required|invalid payload|unexpected keyword|"
    r"keyerror|typeerror|is not of type|expected .* got"
)
_BAD_PATH = re.compile(
    r"(?i)no such file or directory|no such file|filenotfounderror|file not found|does not exist|"
    r"path not found|not a directory|can't open file|cannot open file|could not open file|"
    r"\[errno 2\]|errno 2|nonexistent (?:file|path)"
)
_UNVERIFIED = re.compile(r"(?i)unverified|without (?:running|verif)|verification gate|lint_errors|tests? failed")
_GAVE_UP = re.compile(r"(?i)\b(?:i (?:cannot|can't|am unable to)|unable to (?:complete|proceed)|giving up|gave up)\b")
_USER_ABORT = re.compile(r"(?i)aborted by user|cancelled by user")

_ADVICE = {
    "loop": "change approach after the second identical result: read the error, try another tool or other "
    "arguments, or stop and report what blocks you",
    "iteration_cap": "plan the few steps the goal needs, do them, verify once and finish; do not explore without a plan",
    "wrong_tool": "use only tools listed in the tool guide, by their exact names",
    "bad_arguments": "send exactly the payload fields the tool's description lists",
    "hallucinated_path": "list or search the workspace before reading or editing a path; never guess one",
    "ignored_errors": "read each tool error and fix its cause before the next step",
    "unverified_success": "run the lint or tests (or read the file back) before saying the work is done",
    "gave_up": "try a second approach before giving up, and say exactly what is missing",
}
LESSON_KINDS = frozenset(_ADVICE)


def _state_path() -> Any:
    return teacher.teacher_dir() / "raven_failures.json"


def read_state() -> dict[str, Any]:
    state = teacher._read_json(_state_path(), {})
    state.setdefault("seen", {})
    state.setdefault("lessons", {})
    state.setdefault("cases", [])
    return state


def write_state(state: dict[str, Any]) -> None:
    teacher._write_json(_state_path(), state)


# ---------------------------------------------------------------------------
# Reading a mission
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """A log field as text: JSON for structures, so error shapes stay stable."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _log_events(mission: dict[str, Any]) -> list[Any]:
    """The mission's audit log, tolerating wrappers and double-encoded JSON.

    Some writers store the log as a JSON string, some as a dict with an
    ``events`` list, and some encode the whole list twice. All of those are the
    same log; only the outer wrapping differs.
    """
    value = mission.get("output_log")
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return []
        elif isinstance(value, dict):
            nested = None
            for key in _WRAP_KEYS:
                candidate = value.get(key)
                if isinstance(candidate, list) or (isinstance(candidate, str) and candidate.strip()):
                    nested = candidate
                    break
            if nested is None:
                return [value] if ("type" in value or "raw_type" in value) else []
            value = nested
        elif isinstance(value, list):
            return value
        else:
            return []
    return value if isinstance(value, list) else []


def mission_events(mission: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool calls of a mission's normalised audit log, as ``{tool, payload, ok, result}``."""
    calls: list[dict[str, Any]] = []
    for event in _log_events(mission):
        if not isinstance(event, dict):
            continue
        kind = event.get("raw_type") or event.get("type")
        if kind not in ("result_success", "result_error"):
            continue
        payload = event.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {"raw": payload}
        calls.append(
            {
                "tool": str(event.get("tool") or ""),
                "payload": payload if isinstance(payload, dict) else {},
                "ok": kind == "result_success",
                "result": _text(event.get("raw_data") if event.get("raw_data") not in (None, "") else event.get("data")),
            }
        )
    return calls


def iteration_span(mission: dict[str, Any]) -> tuple[int | None, int | None]:
    """``(last_iteration, max_iterations)`` from the log's system events or the mission row."""
    last: int | None = None
    maximum: int | None = None
    for event in _log_events(mission):
        if not isinstance(event, dict):
            continue
        if (event.get("raw_type") or event.get("type")) != "system":
            continue
        match = _ITERATION.search(_text(event.get("data") or event.get("message") or ""))
        if match:
            last, maximum = int(match.group(1)), int(match.group(2))
    for key in ("iteration", "iterations", "iteration_count", "current_iteration"):
        try:
            value = int(str(mission.get(key)).strip())
        except (TypeError, ValueError):
            continue
        if last is None or value > last:
            last = value
    for key in ("max_iterations", "iteration_limit"):
        try:
            value = int(str(mission.get(key)).strip())
        except (TypeError, ValueError):
            continue
        if maximum is None or value > maximum:
            maximum = value
    return last, maximum


def _call_signature(call: dict[str, Any]) -> str:
    from services.gateway.agent_loop import action_signature

    return action_signature(call["tool"], call["payload"])


def _most_common(values: list[str]) -> str | None:
    return max(values, key=values.count) if values else None


def _repeated_failure(calls: list[dict[str, Any]], threshold: int = REPEATED_FAILURES) -> str | None:
    """``"looping: <call> failed N times"`` when the same call keeps failing."""
    counts: dict[str, int] = {}
    for call in calls:
        if call["ok"]:
            continue
        signature = _call_signature(call)
        counts[signature] = counts.get(signature, 0) + 1
    if not counts:
        return None
    signature, count = max(counts.items(), key=lambda item: item[1])
    if count >= threshold:
        return f"looping: {signature[:120]} failed {count} times"
    return None


def _error_shape(text: str) -> str:
    """The error without its particulars: numbers, quoted values and paths folded."""
    text = re.sub(r"[\w.-]*/[\w./-]*|\b[\w-]+\.[A-Za-z0-9]{1,5}\b", "P", text or "")
    text = re.sub(r"\d+", "N", text)
    text = re.sub(r"['\"`][^'\"`]{1,80}['\"`]", "'X'", text)
    return re.sub(r"\s+", " ", text).strip()[:100]


def is_failed(mission: dict[str, Any]) -> bool:
    status = str(mission.get("status") or "").lower()
    return status in ("failed", "killed", "cancelled", "stalled", "error")


def classify(mission: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """``{kind, infrastructure, tool, evidence}`` for one failed mission."""
    from services.gateway.agent_loop import detect_mission_loop

    result = " ".join(str(mission.get(k) or "") for k in ("result", "error_summary"))
    errors = [c for c in calls if not c["ok"]]
    last_error = errors[-1] if errors else None
    last_iteration, max_iteration = iteration_span(mission)
    killed = bool(_KILLED.search(result))

    def finding(kind: str, tool: str | None, evidence: str) -> dict[str, Any]:
        return {"kind": kind, "infrastructure": kind == INFRASTRUCTURE, "tool": tool, "evidence": evidence[:300]}

    # Infrastructure first, on the mission's own verdict and its last error: a
    # timeout or an unavailable model is never the model's mistake, even when
    # it repeated until the mission gave up.
    infra_verdict = bool(_INFRA.search(result)) and not _LOOP.search(result)
    infra_last = bool(last_error and _INFRA.search(last_error["result"])) and not _USER_ABORT.search(result)
    if infra_verdict or infra_last:
        return finding(INFRASTRUCTURE, last_error["tool"] if last_error else None, result or (last_error or {}).get("result", ""))
    history = [(_call_signature(c), c["ok"], _error_shape(c["result"]) if not c["ok"] else "") for c in calls]
    loop = detect_mission_loop(history, window=max(16, len(history)))
    repeated = _repeated_failure(calls)
    if _LOOP.search(result) or loop:
        return finding("loop", _most_common([c["tool"] for c in calls]), loop or result)
    # Killed while still making iterations: a kill is not a verdict on the work,
    # but 38 of 60 iterations on a trivial task is a loop that never got a name.
    if killed and (repeated or (last_iteration is not None and last_iteration >= KILLED_LOOP_ITERATIONS)):
        tool = _most_common([c["tool"] for c in calls if not c["ok"]]) or _most_common([c["tool"] for c in calls])
        if repeated:
            return finding("loop", tool, repeated)
        where = f" at iteration {last_iteration}/{max_iteration}" if max_iteration else f" at iteration {last_iteration}"
        return finding("loop", tool, f"killed while looping{where}: {result}".strip())
    # The third identical failure is the model no longer reading the error.
    if repeated:
        return finding("loop", _most_common([c["tool"] for c in calls if not c["ok"]]), repeated)
    for kind, pattern in (("wrong_tool", _WRONG_TOOL), ("bad_arguments", _BAD_ARGS), ("hallucinated_path", _BAD_PATH)):
        hit = next((c for c in reversed(errors) if pattern.search(c["result"])), None)
        if hit or pattern.search(result):
            return finding(kind, hit["tool"] if hit else None, hit["result"] if hit else result)
    capped = _CAP.search(result) or (last_iteration is not None and max_iteration is not None and last_iteration >= max_iteration)
    if capped:
        where = f"iteration {last_iteration}/{max_iteration}" if max_iteration else result
        return finding("iteration_cap", None, where)
    if _UNVERIFIED.search(result):
        return finding("unverified_success", None, result)
    if len({_error_shape(c["result"]) for c in errors}) >= 3:
        return finding("ignored_errors", last_error["tool"] if last_error else None, last_error["result"] if last_error else result)
    if _GAVE_UP.search(result + " " + str(mission.get("last_llm_reply") or "")) and len(calls) < 5:
        return finding("gave_up", None, result)
    if killed:
        return finding("aborted_unexplained", None, result)
    return finding("unexplained", last_error["tool"] if last_error else None, result)


# ---------------------------------------------------------------------------
# Lessons and cases
# ---------------------------------------------------------------------------


def lesson_key(finding: dict[str, Any]) -> str:
    shape = _error_shape(finding.get("evidence") or "") if finding["kind"] not in ("loop", "iteration_cap") else ""
    raw = f"{finding['kind']}\x00{(finding.get('tool') or '').lower()}\x00{shape}"
    return "lesson-avoid-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def avoid_lesson(finding: dict[str, Any], *, count: int, goal: str, user_id: str = "default") -> dict[str, Any]:
    """A ``system_learnings`` document: what went wrong, and what to do instead."""
    tool = finding.get("tool") or "any tool"
    rule = (
        f"Avoid {finding['kind'].replace('_', ' ')} with {tool} "
        f"(seen {count} time{'s' if count != 1 else ''}): {_ADVICE[finding['kind']]}."
    )
    confidence = round(min(MAX_CONFIDENCE, BASE_CONFIDENCE + CONFIDENCE_STEP * (count - 1)), 2)
    lesson_id = lesson_key(finding)
    fields = {
        "id": lesson_id,
        "topic": f"raven failure: {finding['kind']}",
        "rule": rule[:480],
        "root_cause": str(finding.get("evidence") or "")[:300],
        "outcome": "failure",
        "confidence": confidence,
        "tags": ["raven_failure", finding["kind"], *([str(finding["tool"]).lower()] if finding.get("tool") else [])],
        "summary": f"Failed mission like: {goal[:300]}",
    }
    return {
        "user_id": user_id,
        "content": json.dumps(fields, ensure_ascii=False),
        "collection_name": "system_learnings",
        "metadata": {
            **fields,
            "type": "avoid",
            "supersedes": [],
            "occurrences": count,
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


_VERIFY = r"(?i)\b(?:test|tests|pytest|lint|ruff|verify|check|read (?:it )?back|ls|cat|confirm)\b"


def case_for(record: dict[str, Any]) -> Case:
    """A ``raven_failures`` case: plan the mission without the failing call, with a check."""
    checks: dict[str, Any] = {"must_match": [_VERIFY], "max_chars": 1500}
    bad = str(record.get("bad_call") or "").strip()
    if bad:
        checks["must_not_include"] = [bad]
    prompt = (
        "Raven was given this mission and failed it before:\n\n"
        f"{record['goal'][:700]}\n\n"
        "Plan it again. In at most 8 short lines, list the tool calls you would make, in order, "
        "with their key arguments, and how you would verify the result before finishing."
    )
    return Case(
        id=f"raven-failure-{record['mission_id']}",
        bucket="raven_failures",
        title=f"Failed mission ({record['kind'].replace('_', ' ')}): {record['goal'][:60]}",
        prompt=prompt,
        checks=checks,
        system=DEFAULT_SYSTEM,
        source=f"raven_failure:{record['kind']}",
        difficulty=3,
        max_tokens=360,
        tags=("raven_failures", record["kind"]),
    )


def failure_cases(limit: int = MAX_CASES) -> list[Case]:
    records = read_state()["cases"]
    return [case_for(record) for record in records[-limit:]]


def _bad_call(finding: dict[str, Any], calls: list[dict[str, Any]]) -> str:
    """The argument that failed, when it is specific enough to check for (a path, a tool)."""
    if finding["kind"] == "wrong_tool" and finding.get("tool"):
        return str(finding["tool"])
    if finding["kind"] == "hallucinated_path":
        for call in reversed(calls):
            if not call["ok"]:
                path = call["payload"].get("file_path") or call["payload"].get("path")
                if path and len(str(path)) >= 4:
                    return str(path)
    return ""


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------

Fetch = Callable[[str], Awaitable[Any]]


async def _identity_get(path: str) -> Any:
    import aiohttp

    from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET
    from services.gateway.http_client import shared_http_client

    async with shared_http_client() as client, client.get(
        f"{IDENTITY_SVC}{path}",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=aiohttp.ClientTimeout(total=20.0),
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Identity answered {resp.status} for {path.split('?')[0]}")
        return await resp.json()


async def run_pass(*, fetch: Fetch = _identity_get, user_id: str = "default", write_lessons: bool = True) -> dict[str, Any]:
    """Learn from missions that failed since the last pass. Raises on a missing roster."""
    from services.gateway.trainer import dream as dream_mod

    roster = await fetch("/api/internal/name-roster")
    names = teacher.household_name_words(roster)
    if not names:
        raise RuntimeError("Identity's name roster is empty; mission logs are not scrubbed without it")
    config = scrub_mod.ScrubConfig(names=tuple(names), names_loaded=True)

    state = read_state()
    done: dict[str, Any] = {"examined": 0, "lessons": [], "infrastructure": 0, "by_kind": {}}
    # Proposer context: read the full SIM ledger (including retired/regressed
    # fixes) before writing another avoid-lesson, so a failed approach is not
    # proposed again under a new id.
    try:
        sim_history = await dream_mod.sim_history(user_id)
        done["sim_problems"] = sim_history.get("count", 0)
        done["sim_retired"] = [
            p.get("problem_id") for p in sim_history.get("problems") or [] if p.get("status") == "retired"
        ]
    except Exception as exc:  # SIM is optional context: a ledger failure never stops the pass
        done.setdefault("errors", []).append(f"sim history: {exc}")
    listing = await fetch(f"/api/raven/missions?limit={MAX_MISSIONS_PER_PASS}")
    new = [m for m in listing if is_failed(m) and str(m.get("id")) not in state["seen"]]
    done["examined"] = len(new)
    written = 0
    for item in new:
        mission = await fetch(f"/api/raven/missions/{item['id']}")
        calls = mission_events(mission)
        finding = classify(mission, calls)
        kind = finding["kind"]
        done["by_kind"][kind] = done["by_kind"].get(kind, 0) + 1
        goal = scrub_mod.scrub_text(str(mission.get("proposed_mission") or "")[:2000], config)
        finding["evidence"] = scrub_mod.scrub_text(str(finding.get("evidence") or ""), config)
        record = {
            "mission_id": str(mission.get("id")),
            "kind": kind,
            "tool": finding.get("tool"),
            "evidence": finding["evidence"],
            "goal": goal,
            "at": mission.get("completed_at") or mission.get("created_at"),
        }
        state["seen"][record["mission_id"]] = {"kind": kind, "at": record["at"]}
        if finding["infrastructure"]:
            done["infrastructure"] += 1
            continue
        if kind not in LESSON_KINDS:
            continue
        record["bad_call"] = scrub_mod.scrub_text(_bad_call(finding, calls), config)
        record["steps"] = [
            scrub_mod.scrub_value(
                {"tool": c["tool"], "payload": c["payload"], "ok": c["ok"], "result": c["result"][:400]}, config, scrub_mod.ScrubStats()
            )
            for c in calls[-MAX_STEPS_KEPT:]
        ]
        if scrub_mod.leak_check({"goal": goal, "steps": record["steps"], "evidence": record["evidence"]}, config):
            done["by_kind"]["dropped_by_scrubber"] = done["by_kind"].get("dropped_by_scrubber", 0) + 1
            continue
        state["cases"] = [*state["cases"], record][-MAX_CASES:]
        key = lesson_key(finding)
        entry = state["lessons"].setdefault(key, {"count": 0, "kind": kind, "tool": finding.get("tool")})
        entry["count"] += 1
        entry["last_mission"] = record["mission_id"]
        if write_lessons and written < MAX_LESSONS_PER_PASS:
            payload = avoid_lesson(finding, count=entry["count"], goal=goal, user_id=user_id)
            try:
                await dream_mod.request("POST", "/rag/ingest", body=payload, timeout=30.0)
            except dream_mod.RagError as exc:
                done.setdefault("errors", []).append(str(exc))
                entry["pending"] = True
                continue
            entry.pop("pending", None)
            entry["confidence"] = payload["metadata"]["confidence"]
            entry["written_at"] = payload["metadata"]["indexed_at"]
            # Selector: this exact failure was observed on this mission's model.
            # It is a negative lesson, so the next proposer sees that the fix
            # implied by the avoid-lesson already failed once.
            try:
                await dream_mod.sim_record(
                    scope="system",
                    user_id="default",
                    harness="raven",
                    category=f"raven_failure_{kind}",
                    signature=f"{finding.get('tool') or 'mission'}::{kind}::{finding['evidence'][:120]}",
                    rationale=f"A Raven mission failed with `{kind}`: {finding['evidence'][:240]}",
                    edit_or_rule=f"When a mission is about to {kind.replace('_', ' ')}, {_ADVICE[kind]}.",
                    observed_outcome=str(mission.get("error_summary") or finding["evidence"])[:300],
                    outcome_status="regressed",
                    model=str(mission.get("model") or ""),
                    domain=goal[:200],
                )
            except Exception as exc:  # SIM is optional: a ledger failure never stops the pass
                done.setdefault("errors", []).append(f"sim: {exc}")
            written += 1
            done["lessons"].append(key)
    write_state(state)
    return done


def summary() -> dict[str, Any]:
    state = read_state()
    by_kind: dict[str, int] = {}
    for item in state["seen"].values():
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    history: list[dict[str, Any]] = []
    from services.gateway.trainer import runner

    for item in runner.list_reports(limit=30):
        if item.get("error"):
            continue
        report = runner.load_report(str(item["id"])) or {}
        bucket = (report.get("by_bucket") or {}).get("raven_failures")
        if bucket and bucket.get("cases"):
            history.append({"report_id": report.get("id"), "started_at": report.get("started_at"),
                            "cases": bucket["cases"], "passed": bucket["passed"], "pass_rate": bucket["pass_rate"]})
    history.sort(key=lambda row: str(row.get("started_at") or ""))
    return {
        "missions_examined": len(state["seen"]),
        "by_kind": by_kind,
        "learned_from": sum(n for kind, n in by_kind.items() if kind in LESSON_KINDS),
        "infrastructure": by_kind.get(INFRASTRUCTURE, 0),
        "lessons": len(state["lessons"]),
        "cases": len(state["cases"]),
        "scores": history,
    }


def failed_attempts() -> list[dict[str, Any]]:
    """Scrubbed failed attempts, for contrastive pairing in the dataset export."""
    return [record for record in read_state()["cases"] if record.get("steps")]
