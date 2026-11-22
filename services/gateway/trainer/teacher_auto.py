"""The hands-off half of the teacher pipeline: what the gateway does on its own.

The Mac collects and posts traces (launchd every 30 minutes, plus the opencode
plugin per session) and reports a heartbeat after each run. Every
``PASS_SECONDS`` the gateway's background worker calls :func:`run_pass`, which:

1. writes worked-example lessons for newly imported verified traces (RAG only,
   never the model);
2. runs the ``teacher`` evaluation bucket when it is due
   (``trainer_teacher_eval_interval_hours``) and only when Alpaca's readiness
   gate says the card is idle, so the household never waits on it; the report
   is what the score trend is drawn from;
3. exports a fine-tuning snapshot once ``trainer_teacher_snapshot_every`` new
   verified traces have arrived since the last one, and tells the admins (counts
   only, never content);
4. checks the Mac's heartbeat: an error it reported, or silence for longer than
   ``trainer_teacher_sync_stale_minutes``, is a failure.

Every outcome lands in ``data/trainer/teacher/status.json``, which the status
route and the Lab card read. A failing part notifies the admins once, when it
starts failing (not on every retry), and again only after it has recovered and
failed anew. ``trainer_teacher_auto`` turns the whole pass off.
"""
from __future__ import annotations

import calendar
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from services.common import notice_links
from services.gateway.trainer import alpaca, raven_failures, runner, teacher
from services.gateway.trainer import llm as llm_mod
from services.gateway.trainer import teacher_scrub as scrub_mod

log = logging.getLogger("gateway.trainer.teacher_auto")

#: How often the background worker runs a pass.
PASS_SECONDS = 1800

#: The first pass waits this long after the worker starts.
START_DELAY_S = 60

#: Settings the pass reads (Identity GlobalSettings, seeded with defaults).
SETTING_KEYS = (
    "trainer_teacher_auto",
    "trainer_teacher_eval_interval_hours",
    "trainer_teacher_snapshot_every",
    "trainer_teacher_sync_stale_minutes",
)

EVAL_CASE_LIMIT = 20
EVAL_BUDGET_S = 600.0
LESSONS_PER_PASS = 50

Notify = Callable[[dict[str, Any]], Awaitable[bool]]


class TeacherAutoConfigError(ValueError):
    """A setting the pass needs is missing or malformed."""


def _now_iso(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def _parse_iso(value: Any) -> float | None:
    if not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def parse_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """The pass's settings, or :class:`TeacherAutoConfigError` naming what is wrong."""
    missing = [key for key in SETTING_KEYS if settings.get(key) in (None, "")]
    if missing:
        raise TeacherAutoConfigError(
            f"missing setting(s) {', '.join(missing)}; Identity seeds them on start (restart identity)"
        )
    try:
        config = {
            "auto": str(settings["trainer_teacher_auto"]).strip().lower() == "true",
            "eval_hours": float(settings["trainer_teacher_eval_interval_hours"]),
            "snapshot_every": int(settings["trainer_teacher_snapshot_every"]),
            "stale_minutes": float(settings["trainer_teacher_sync_stale_minutes"]),
        }
    except (TypeError, ValueError) as exc:
        raise TeacherAutoConfigError(f"a trainer_teacher_* setting is not a number: {exc}") from exc
    if config["snapshot_every"] < 1 or config["eval_hours"] <= 0 or config["stale_minutes"] <= 0:
        raise TeacherAutoConfigError("trainer_teacher_* intervals and counts must be positive")
    return config


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status_path() -> Any:
    return teacher.teacher_dir() / "status.json"


def read_status() -> dict[str, Any]:
    data = teacher._read_json(status_path(), {})
    data.setdefault("failures", {})
    return data


def write_status(status: dict[str, Any]) -> None:
    teacher._write_json(status_path(), status)


def _fail(status: dict[str, Any], part: str, reason: str, now: float) -> None:
    current = status["failures"].get(part)
    if current is None:
        status["failures"][part] = {"reason": reason[:300], "since": _now_iso(now), "notified": False}
    else:
        current["reason"] = reason[:300]
        current["last_seen"] = _now_iso(now)


def _ok(status: dict[str, Any], part: str) -> None:
    status["failures"].pop(part, None)


_LABELS = {
    "settings": "Teacher pipeline settings",
    "lessons": "Teacher lessons",
    "eval": "Teacher evaluation",
    "snapshot": "Teacher dataset snapshot",
    "missions": "Learning from failed missions",
    "sync": "Teacher sync from the Mac",
}


async def _send_failure_notices(status: dict[str, Any], notify: Notify | None) -> None:
    """One notice per failure, the first time it is seen (retried until delivered)."""
    if notify is None:
        return
    for part, failure in sorted(status["failures"].items()):
        if failure.get("notified"):
            continue
        delivered = await notify(
            {
                "title": f"{_LABELS.get(part, part)} is failing",
                "body": failure["reason"],
                "priority": 3,
                "tag": f"admin:trainer:teacher:{part}",
                "data": {"link": notice_links.link("lab", tab="training")},
            }
        )
        failure["notified"] = bool(delivered)


def record_heartbeat(payload: dict[str, Any], *, uploader: str, now: float | None = None) -> dict[str, Any]:
    """The Mac's report after a sync or flush: counts and an error line, never content."""
    now = time.time() if now is None else now
    config = scrub_mod.ScrubConfig()
    error = payload.get("error")
    sync = {
        "at": _now_iso(now),
        "by": uploader,
        "command": str(payload.get("command") or "")[:20],
        "ok": bool(payload.get("ok")),
        "queue_length": int(payload.get("queue_length") or 0),
        "pending_sessions": int(payload.get("pending_sessions") or 0),
        "tasks": int(payload.get("tasks") or 0),
        "sent": int(payload.get("sent") or 0),
        # An error line is scrubbed again: it may quote a URL or a path.
        "error": scrub_mod.scrub_text(str(error), config)[:300] if error else None,
    }
    status = read_status()
    status["sync"] = sync
    if sync["ok"]:
        status["sync"]["last_ok_at"] = sync["at"]
    else:
        status["sync"]["last_ok_at"] = (status.get("sync") or {}).get("last_ok_at")
    write_status(status)
    return sync


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def verified_count() -> int:
    return sum(
        1
        for trace in teacher.all_traces()
        if trace.get("outcome") == "success" and (trace.get("scrub") or {}).get("status") != "dropped"
    )


async def _default_notify(notice: dict[str, Any]) -> bool:
    from services.gateway.admin_alerts import notify_admins
    from services.gateway.http_client import shared_http_client

    async with shared_http_client() as client:
        return await notify_admins(client, notice)


async def run_pass(
    settings: dict[str, Any],
    *,
    now: float | None = None,
    hardware: dict[str, Any] | None = None,
    notify: Notify | None = _default_notify,
    complete_fn: Any | None = None,
    system_prompt: Callable[[], Awaitable[str]] | None = None,
    failures_fetch: Any = None,
) -> dict[str, Any]:
    """One hands-off pass. Returns what it did; the status file holds the rest."""
    now = time.time() if now is None else now
    runner.ensure_data_dir(teacher.teacher_dir())
    status = read_status()
    done: dict[str, Any] = {"at": _now_iso(now)}
    try:
        config = parse_settings(settings)
    except TeacherAutoConfigError as exc:
        _fail(status, "settings", str(exc), now)
        await _send_failure_notices(status, notify)
        write_status(status)
        return {**done, "error": str(exc)}
    _ok(status, "settings")
    if not config["auto"]:
        write_status(status)
        return {**done, "skipped": "trainer_teacher_auto is off"}

    # 1. Lessons from new verified traces.
    try:
        lessons = await teacher.write_lessons(limit=LESSONS_PER_PASS)
    except runner.TrainerStorageError as exc:
        _fail(status, "lessons", str(exc), now)
    else:
        status["lessons"] = {
            "at": _now_iso(now),
            "written": len(lessons["written"]),
            "failed": len(lessons["failed"]),
            "total": status.get("lessons", {}).get("total", 0) + len(lessons["written"]),
        }
        if lessons["failed"]:
            _fail(status, "lessons", f"memory refused {len(lessons['failed'])} lesson(s): {lessons['failed'][0]['error']}", now)
        else:
            _ok(status, "lessons")
        done["lessons_written"] = len(lessons["written"])

    # 1b. Raven's own failed missions: avoid lessons and raven_failures cases.
    if failures_fetch is not False:
        try:
            learned = await raven_failures.run_pass(**({"fetch": failures_fetch} if failures_fetch else {}))
        except Exception as exc:  # Identity down, an empty roster, storage
            _fail(status, "missions", f"failed missions could not be read: {exc}", now)
        else:
            status["missions"] = {
                "at": _now_iso(now),
                "examined": learned["examined"],
                "infrastructure": learned["infrastructure"],
                "by_kind": learned["by_kind"],
                "lessons": len(learned["lessons"]),
            }
            if learned.get("errors"):
                _fail(status, "missions", f"memory refused a failure lesson: {learned['errors'][0]}", now)
            else:
                _ok(status, "missions")
            done["missions"] = {"examined": learned["examined"], "lessons": len(learned["lessons"])}

    # 2. The teacher and raven_failures buckets, when due and only on an idle card.
    last_eval = _parse_iso((status.get("eval") or {}).get("at"))
    due = last_eval is None or now - last_eval >= config["eval_hours"] * 3600
    cases = (
        [*teacher.teacher_cases(limit=EVAL_CASE_LIMIT), *raven_failures.failure_cases(limit=EVAL_CASE_LIMIT)]
        if due
        else []
    )
    if due and cases:
        done["eval"] = await _evaluate(status, settings, cases, now, hardware, complete_fn)
    elif due:
        done["eval"] = "no teacher or failure cases yet"

    # 3. A dataset snapshot every N new verified traces.
    verified = verified_count()
    since = int((status.get("snapshot") or {}).get("verified_total") or 0)
    if verified - since >= config["snapshot_every"]:
        done["snapshot"] = await _snapshot(status, verified, now, notify, system_prompt)

    # 4. Is the Mac still sending?
    sync = status.get("sync") or {}
    last_heard = _parse_iso(sync.get("at"))
    if sync and not sync.get("ok"):
        _fail(status, "sync", f"the last {sync.get('command') or 'sync'} on the Mac failed: {sync.get('error') or 'no reason given'}", now)
    elif last_heard is not None and now - last_heard > config["stale_minutes"] * 60:
        minutes = int((now - last_heard) // 60)
        _fail(status, "sync", f"no sync from the Mac for {minutes} minutes (is the launchd agent installed and the Mac awake?)", now)
    elif sync:
        _ok(status, "sync")

    await _send_failure_notices(status, notify)
    status["last_pass_at"] = _now_iso(now)
    write_status(status)
    return done


async def _evaluate(
    status: dict[str, Any],
    settings: dict[str, Any],
    cases: list[Any],
    now: float,
    hardware: dict[str, Any] | None,
    complete_fn: Any | None,
) -> str:
    from services.gateway.trainer import facilitator

    if facilitator.cycle_running():
        return "a trainer cycle is running; next pass"
    if hardware is None:
        hardware = await alpaca.probe(settings)
    if hardware.get("reachable") is not True:
        reasons = "; ".join(str(r) for r in hardware.get("reasons") or ["no answer"])
        _fail(status, "eval", f"the model host could not be probed: {reasons}", now)
        return "host unreachable"
    if hardware.get("ready") is not True:
        # The household is using the card: not a failure, just not now.
        status.setdefault("eval", {})["waiting"] = {
            "since": _now_iso(now), "reasons": [str(r) for r in hardware.get("reasons") or []][:3]
        }
        return "host busy; waiting for an idle card"
    try:
        report = await runner.run_curriculum(
            cases,
            model=((hardware.get("models") or {}).get("chosen") or None),
            complete_fn=complete_fn,
            budget_s=EVAL_BUDGET_S,
        )
    except (llm_mod.LocalModelUnavailable, runner.TrainerStorageError) as exc:
        _fail(status, "eval", str(exc), now)
        return "failed"
    by_bucket = report.get("by_bucket") or {}
    bucket = by_bucket.get("teacher") or {}
    failures = by_bucket.get("raven_failures") or {}
    status["eval"] = {
        "at": _now_iso(now),
        "report_id": report.get("id"),
        "model": report.get("model"),
        "cases": bucket.get("cases", 0),
        "passed": bucket.get("passed", 0),
        "pass_rate": bucket.get("pass_rate", 0.0),
        "failure_cases": failures.get("cases", 0),
        "failure_passed": failures.get("passed", 0),
        "stopped_reason": report.get("stopped_reason"),
    }
    if report.get("stopped_reason") == "model_unavailable":
        _fail(status, "eval", "the local model stopped answering during the teacher evaluation", now)
    else:
        _ok(status, "eval")
    text = f"{bucket.get('passed', 0)}/{bucket.get('cases', 0)} passed"
    if failures.get("cases"):
        text += f"; failed missions {failures.get('passed', 0)}/{failures.get('cases', 0)}"
    return text


async def _snapshot(
    status: dict[str, Any],
    verified: int,
    now: float,
    notify: Notify | None,
    system_prompt: Callable[[], Awaitable[str]] | None,
) -> str:
    try:
        prompt = await (system_prompt or teacher.raven_system_prompt)()
        result = teacher.export_finetune(prompt)
    except (teacher.TeacherError, runner.TrainerStorageError) as exc:
        _fail(status, "snapshot", str(exc), now)
        return "failed"
    status["snapshot"] = {
        "at": _now_iso(now),
        "verified_total": verified,
        "train_rows": result["train_rows"],
        "eval_rows": result["eval_rows"],
        "format": result["format"],
        "file": str(result["paths"].get("train", "")).rsplit("/", 1)[-1],
    }
    _ok(status, "snapshot")
    if notify is not None:
        await notify(
            {
                "title": "Teacher dataset snapshot ready",
                "body": f"{result['train_rows']} training and {result['eval_rows']} evaluation rows from "
                f"{verified} verified teacher tasks. See Lab, Training, Teacher traces.",
                "priority": 2,
                "tag": "admin:trainer:teacher:snapshot-ready",
                "data": {"link": notice_links.link("lab", tab="training")},
            }
        )
    return f"{result['train_rows']} train / {result['eval_rows']} eval rows"


def health(now: float | None = None) -> dict[str, Any]:
    """What the status route and the Lab card show."""
    status = read_status()
    return {
        "last_pass_at": status.get("last_pass_at"),
        "sync": status.get("sync"),
        "lessons": status.get("lessons"),
        "eval": status.get("eval"),
        "snapshot": status.get("snapshot"),
        "missions": status.get("missions"),
        "failures": [
            {"part": part, "label": _LABELS.get(part, part), **{k: v for k, v in failure.items() if k != "notified"}}
            for part, failure in sorted(status["failures"].items())
        ],
    }
