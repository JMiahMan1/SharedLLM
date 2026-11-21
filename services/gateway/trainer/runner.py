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


class TrainerStorageError(RuntimeError):
    """The trainer's data directory cannot be written (not mounted, read-only)."""


def ensure_data_dir(directory: pathlib.Path | None = None) -> pathlib.Path:
    """Create and prove the trainer's data directory is writable, or raise.

    Checked *before* a run spends the model's time: a report that cannot be
    saved cannot be exported, listed or turned into a dataset later, so a run
    against a missing mount is refused up front instead of evaporating after.
    """
    target = pathlib.Path(directory) if directory else DATA_DIR
    probe = target / f".write-check-{uuid.uuid4().hex[:8]}"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise TrainerStorageError(
            f"The trainer cannot write to {target}: {exc}. Mount ./data/trainer into the "
            "gateway (docker-compose.yml) or set TRAINER_DATA_DIR to a writable directory."
        ) from exc
    return target

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


def _complete_with(model: str) -> CompleteFn:
    """Every case asks the same, already-resolved model."""

    def _call(case: Case) -> Awaitable[llm_mod.Completion]:
        return llm_mod.complete(
            case.prompt,
            system=case.system,
            model=model,
            max_tokens=case.max_tokens,
        )

    return _call  # type: ignore[return-value]


def _is_infrastructure(exc: BaseException) -> bool:
    """A failure of the host or the model service, not of the model's answer."""
    return isinstance(exc, llm_mod.LocalModelUnavailable)


def _new_report_id(now: float) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    return f"eval-{stamp}-{uuid.uuid4().hex[:4]}"


def _by_bucket(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in results:
        bucket = buckets.setdefault(
            row["bucket"],
            {"cases": 0, "passed": 0, "pass_rate": 0.0, "failed_ids": [], "error_ids": []},
        )
        if row.get("infrastructure_error"):
            # The model never answered: that says nothing about the curriculum.
            bucket["error_ids"].append(row["id"])
            continue
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
    """Run the cases in order and return a report (also written to disk).

    The model is resolved once, before the first case (``model`` when the caller
    named one, else the trainer's choice from the host's inventory), and every
    case asks that model. A model that cannot be resolved, or a data directory
    that cannot be written, raises before any case runs.
    """
    ensure_data_dir(_report_dir(report_dir))
    used_model = (model or "").strip()
    if complete_fn is None:
        from services.gateway.orchestrator import get_llm_settings

        used_model = await llm_mod.resolve_model(await get_llm_settings(), used_model or None)
        complete = _complete_with(used_model)
    else:
        complete = complete_fn
    started_wall = time.time()
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    consecutive_errors = 0
    last_infra: BaseException | None = None

    for index, case in enumerate(cases, start=1):
        if time.monotonic() - started > budget_s:
            stopped_reason = "budget"
            break

        answer = ""
        error: str | None = None
        infrastructure = False
        case_started = time.monotonic()
        try:
            produced = await complete(case)
            if isinstance(produced, llm_mod.Completion):
                answer = produced.text
                used_model = produced.model or used_model
            else:
                answer = str(produced or "")
            consecutive_errors = 0
        except Exception as exc:  # a broken case must not sink the run
            infrastructure = _is_infrastructure(exc)
            error = str(exc) if infrastructure else f"{type(exc).__name__}: {exc}"
            if infrastructure:
                last_infra = exc
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
                "infrastructure_error": infrastructure,
                "system": case.system or "",
                "prompt": case.prompt,
                "source": case.source,
            }
        )
        if progress:
            progress({"index": index, "total": len(cases), "case_id": case.id, "passed": passed})

        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            # "host_busy": the household had the GPU (a slot wait timed out).
            # "model_unavailable": the host or model failed. Neither is a lesson.
            stopped_reason = (
                "host_busy" if isinstance(last_infra, llm_mod.LocalModelBusy) else "model_unavailable"
            )
            log.warning("[trainer] stopping after %s model failures: %s", consecutive_errors, error)
            break

    finished_wall = time.time()
    passed_count = sum(1 for row in results if row["passed"])
    errored = sum(1 for row in results if row["infrastructure_error"])
    answered = len(results) - errored
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
            "infrastructure_errors": errored,
            "pass_rate": round(passed_count / answered, 3) if answered else 0.0,
            "answer_chars": sum(row["answer_chars"] for row in results),
        },
        "by_bucket": _by_bucket(results),
        "cases": results,
    }
    save_report(report, report_dir=report_dir)
    return report



def _report_dir(report_dir: pathlib.Path | None) -> pathlib.Path:
    return pathlib.Path(report_dir) if report_dir else DATA_DIR / "reports"


def save_report(report: dict[str, Any], *, report_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Write a report as ``<id>.json`` and return the path; raise if it cannot.

    A report that was not saved cannot be listed, exported or compared later, so
    a failed write is an error the caller sees (:class:`TrainerStorageError`),
    never a log line. ``run_curriculum`` checks the directory before it starts,
    so this only fails when the disk changed under a running pass.
    """
    directory = _report_dir(report_dir)
    path = directory / f"{report['id']}.json"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise TrainerStorageError(f"Could not save report {report.get('id')} to {path}: {exc}") from exc
    return path


def list_reports(*, report_dir: pathlib.Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Report summaries, newest first. An unreadable file is listed with its error."""
    directory = _report_dir(report_dir)
    if not directory.is_dir():
        return []
    summaries: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Listed, not hidden: a report that vanished from the list would look
            # like a run that never happened.
            log.warning("[trainer] unreadable report %s: %s", path, exc)
            summaries.append({"id": path.stem, "started_at": None, "error": f"unreadable: {exc}"})
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
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise TrainerStorageError(f"Report {report_id} exists but cannot be read: {exc}") from exc


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
        if case.get("infrastructure_error"):
            continue
        answer = str(case.get("answer") or "").strip()
        if not answer:
            continue
        # The exchange exactly as the model saw it: the case's own system prompt
        # when it had one, and no system message at all when it did not (an empty
        # one teaches the model to expect a blank instruction).
        system = str(case.get("system") or "").strip()
        messages: list[dict[str, str]] = [{"role": "system", "content": system}] if system else []
        messages += [
            {"role": "user", "content": case.get("prompt") or ""},
            {"role": "assistant", "content": answer},
        ]
        rows.append(
            {
                "messages": messages,
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
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise TrainerStorageError(f"Could not write the dataset to {target}: {exc}") from exc
    return {"path": str(target), "rows": len(rows), "accepted_only": accepted_only}
