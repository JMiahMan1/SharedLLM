"""Run the curriculum, score it, and keep the evidence.

A run is sequential on purpose: the household has one modest model, and firing
five prompts at it makes every one of them slower and less predictable. Each
answer is scored by :func:`score_answer` (strings and regexes only), the whole
run is bounded by a wall-clock budget, and the result is a report on disk plus a
JSONL dataset that can be used to fine-tune the model later.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from services.gateway.trainer import llm as llm_mod
from services.gateway.trainer.curriculum import Case

log = logging.getLogger("gateway.trainer.runner")

#: Reports, datasets and the improvement backlog. ``/data/*`` is gitignored, so
#: nothing the trainer writes can accidentally be committed.
DATA_DIR = pathlib.Path(
    os.getenv("TRAINER_DATA_DIR")
    or pathlib.Path(__file__).resolve().parents[3] / "data" / "trainer"
)

#: One answer is stored up to this many characters; checks never need more.
MAX_ANSWER_CHARS = 4000

#: How many model failures in a row before a run gives up (model not loaded).
MAX_CONSECUTIVE_ERRORS = 3

#: The seam tests replace so no model is ever called.
CompleteFn = Callable[[Case], Awaitable[str]]

_REPORT_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def score_answer(case: Case, answer: str) -> tuple[bool, float, list[str]]:
    """Judge one answer against its case's checks.

    Returns ``(passed, score, failed_checks)`` where ``score`` is the fraction
    of individual checks that held -- partial progress is worth recording even
    though only a clean sheet counts as a pass.
    """
    checks = case.checks or {}
    text = answer or ""
    lowered = text.lower()
    failed: list[str] = []
    total = 0

    for needle in checks.get("must_include", []) or []:
        total += 1
        if str(needle).lower() not in lowered:
            failed.append(f"missing: {needle}")
    for needle in checks.get("must_not_include", []) or []:
        total += 1
        if str(needle).lower() in lowered:
            failed.append(f"unexpected: {needle}")
    for pattern in checks.get("must_match", []) or []:
        total += 1
        if not re.search(str(pattern), text):
            failed.append(f"no match: {pattern}")
    for pattern in checks.get("must_not_match", []) or []:
        total += 1
        if re.search(str(pattern), text):
            failed.append(f"matched: {pattern}")

    limit = checks.get("max_chars")
    if limit:
        total += 1
        if len(text) > int(limit):
            failed.append(f"too long: {len(text)} > {limit}")

    if total == 0:
        return True, 1.0, []
    score = round((total - len(failed)) / total, 3)
    return not failed, score, failed


def _default_complete(case: Case) -> Awaitable[llm_mod.Completion]:
    return llm_mod.complete(
        case.prompt,
        system=case.system,
        max_tokens=case.max_tokens,
    )


def _new_report_id(now: float) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    return f"eval-{stamp}-{uuid.uuid4().hex[:4]}"


def _by_bucket(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in results:
        bucket = buckets.setdefault(
            row["bucket"], {"cases": 0, "passed": 0, "pass_rate": 0.0, "failed_ids": []}
        )
        bucket["cases"] += 1
        if row["passed"]:
            bucket["passed"] += 1
        else:
            bucket["failed_ids"].append(row["id"])
    for bucket in buckets.values():
        bucket["pass_rate"] = round(bucket["passed"] / bucket["cases"], 3) if bucket["cases"] else 0.0
    return dict(sorted(buckets.items()))


async def run_curriculum(
    cases: list[Case],
    *,
    model: str | None = None,
    complete_fn: CompleteFn | None = None,
    budget_s: float = 900.0,
    report_dir: pathlib.Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the cases in order and return a report (also written to disk)."""
    complete = complete_fn or _default_complete
    started_wall = time.time()
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    consecutive_errors = 0
    used_model = model or ""

    for index, case in enumerate(cases, start=1):
        if time.monotonic() - started > budget_s:
            stopped_reason = "budget"
            break

        answer = ""
        error: str | None = None
        case_started = time.monotonic()
        try:
            produced = await complete(case)
            if isinstance(produced, llm_mod.Completion):
                answer = produced.text
                used_model = produced.model or used_model
            else:
                answer = str(produced or "")
            consecutive_errors = 0
        except llm_mod.LocalModelUnavailable as exc:
            error = str(exc)
            consecutive_errors += 1
        except Exception as exc:  # a broken case must not sink the run
            error = f"{type(exc).__name__}: {exc}"
            consecutive_errors += 1
        elapsed = round(time.monotonic() - case_started, 2)

        if error:
            passed, score, failed = False, 0.0, [f"error: {error}"]
        else:
            passed, score, failed = score_answer(case, answer)

        results.append(
            {
                "id": case.id,
                "bucket": case.bucket,
                "title": case.title,
                "difficulty": case.difficulty,
                "passed": passed,
                "score": score,
                "elapsed_s": elapsed,
                "failed_checks": failed,
                "answer": answer[:MAX_ANSWER_CHARS],
                "answer_chars": len(answer),
                "error": error,
                "prompt": case.prompt,
                "source": case.source,
            }
        )
        if progress:
            progress({"index": index, "total": len(cases), "case_id": case.id, "passed": passed})

        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            stopped_reason = "model_unavailable"
            log.warning("[trainer] stopping after %s model failures: %s", consecutive_errors, error)
            break

    finished_wall = time.time()
    passed_count = sum(1 for row in results if row["passed"])
    report: dict[str, Any] = {
        "id": _new_report_id(started_wall),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_wall)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_wall)),
        "elapsed_s": round(time.monotonic() - started, 2),
        "model": used_model,
        "budget_s": budget_s,
        "stopped_reason": stopped_reason,
        "totals": {
            "cases": len(results),
            "requested": len(cases),
            "passed": passed_count,
            "pass_rate": round(passed_count / len(results), 3) if results else 0.0,
            "answer_chars": sum(row["answer_chars"] for row in results),
        },
        "by_bucket": _by_bucket(results),
        "cases": results,
    }
    save_report(report, report_dir=report_dir)
    return report



def _report_dir(report_dir: pathlib.Path | None) -> pathlib.Path:
    return pathlib.Path(report_dir) if report_dir else DATA_DIR / "reports"


def save_report(report: dict[str, Any], *, report_dir: pathlib.Path | None = None) -> pathlib.Path | None:
    """Write a report as ``<id>.json``; returns the path (or ``None`` if unwritable).

    A deploy can mount the gateway's data directory read-only. Keeping the run's
    result in memory is what matters; failing to persist it must not turn a
    completed evaluation into an error.
    """
    directory = _report_dir(report_dir)
    path = directory / f"{report['id']}.json"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("[trainer] could not save report %s: %s", report.get("id"), exc)
        return None
    return path


def list_reports(*, report_dir: pathlib.Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Report summaries, newest first. Never raises: a bad file is skipped."""
    directory = _report_dir(report_dir)
    if not directory.is_dir():
        return []
    summaries: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.debug("[trainer] unreadable report %s", path)
            continue
        summaries.append(
            {
                "id": data.get("id") or path.stem,
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "model": data.get("model"),
                "totals": data.get("totals") or {},
                "stopped_reason": data.get("stopped_reason"),
            }
        )
    summaries.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return summaries[: max(1, limit)]


def load_report(report_id: str, *, report_dir: pathlib.Path | None = None) -> dict[str, Any] | None:
    """One report by id. Ids are validated, so a caller cannot walk the tree."""
    if not _REPORT_ID.match(report_id or ""):
        return None
    path = _report_dir(report_dir) / f"{report_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def training_rows(report: dict[str, Any], *, accepted_only: bool = True) -> list[dict[str, Any]]:
    """The report as fine-tuning rows: one chat exchange per case.

    A passing case is a positive example. A failing case is kept unless
    ``accepted_only`` is set: it is still useful as a negative example (what the
    model did say, alongside the checks it missed), which is what makes a later
    preference-tuning pass possible without re-running the model.
    """
    rows: list[dict[str, Any]] = []
    for case in report.get("cases", []):
        if accepted_only and not case.get("passed"):
            continue
        answer = str(case.get("answer") or "").strip()
        if not answer:
            continue
        rows.append(
            {
                "messages": [
                    {"role": "system", "content": (report.get("system") or "")},
                    {"role": "user", "content": case.get("prompt") or ""},
                    {"role": "assistant", "content": answer},
                ],
                "meta": {
                    "case_id": case.get("id"),
                    "bucket": case.get("bucket"),
                    "score": case.get("score"),
                    "accepted": bool(case.get("passed")),
                    "failed_checks": case.get("failed_checks") or [],
                    "model": report.get("model"),
                    "report_id": report.get("id"),
                },
            }
        )
    return rows


def export_dataset(
    report: dict[str, Any],
    *,
    path: pathlib.Path | None = None,
    accepted_only: bool = True,
) -> dict[str, Any]:
    """Write the rows as JSONL (the shape fine-tuning tools read)."""
    rows = training_rows(report, accepted_only=accepted_only)
    target = pathlib.Path(path) if path else DATA_DIR / "datasets" / f"{report.get('id')}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"path": str(target), "rows": len(rows), "accepted_only": accepted_only}
