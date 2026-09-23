# services/telemetry/store.py
"""Redis storage for report jobs, reports, and the notification outbox."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

from services.telemetry.config import (
    GEO_SVC,
    IDENTITY_SVC,
    INTERNAL_SECRET,
    NOTIFICATION_HISTORY,
    REDIS_URL,
)
from services.telemetry.schedule import next_run_at

JOB_PREFIX = "tel:job:"
JOBS_BY_USER = "tel:jobs:user:"
REPORTS_BY_USER = "tel:reports:user:"
REPORT_PREFIX = "tel:report:"
LATEST_PREFIX = "tel:latest:"
NOTIFY_PREFIX = "tel:notify:"
QUEUE = "tel:queue"
LOCK_PREFIX = "tel:lock:"


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def make_job(
    user: str,
    report_type: str,
    period: str,
    run_at: str,
    tz: str,
    enabled: bool = True,
    job_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": job_id or new_id(),
        "user": user,
        "type": report_type,
        "period": period,
        "run_at": run_at,
        "timezone": tz,
        "enabled": enabled,
        "next_run_at": next_run_at(period, run_at, tz).isoformat(),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "attempts": 0,
        "created_at": utcnow_iso(),
    }


async def save_job(rc, job: dict[str, Any]) -> None:
    await rc.set(f"{JOB_PREFIX}{job['id']}", json.dumps(job))
    await rc.sadd(f"{JOBS_BY_USER}{job['user']}", job["id"])


async def get_job(rc, job_id: str) -> dict[str, Any] | None:
    raw = await rc.get(f"{JOB_PREFIX}{job_id}")
    return json.loads(raw) if raw else None


async def list_jobs(rc, user: str) -> list[dict[str, Any]]:
    ids = await rc.smembers(f"{JOBS_BY_USER}{user}")
    jobs: list[dict[str, Any]] = []
    for job_id in ids or []:
        job = await get_job(rc, job_id)
        if job:
            jobs.append(job)
    return sorted(jobs, key=lambda j: (j.get("type", ""), j.get("period", "")))


async def delete_job(rc, job: dict[str, Any]) -> None:
    await rc.delete(f"{JOB_PREFIX}{job['id']}")
    await rc.srem(f"{JOBS_BY_USER}{job['user']}", job["id"])


async def enqueue(rc, job_id: str, available_at: float | None = None) -> None:
    """Queue a run.

    Always a sorted set scored by the time the run may start, so an immediate
    run and a deferred (busy-machine) run share one queue the worker can claim
    from in due order.
    """
    import time as _time

    await rc.zadd(QUEUE, {job_id: available_at if available_at is not None else _time.time()})


async def dequeue_due(rc, limit: int = 5, now: float | None = None) -> list[str]:
    """Claim up to ``limit`` queued runs whose time has come.

    Uses a sorted set so a deferred (busy) run can wait for a specific time
    instead of being retried on every tick.
    """
    import time as _time

    now_ts = now if now is not None else _time.time()
    claimed: list[str] = []
    for job_id in await rc.zrangebyscore(QUEUE, "-inf", now_ts, start=0, num=limit):
        removed = await rc.zrem(QUEUE, job_id)
        if removed:
            claimed.append(job_id)
    return claimed


async def acquire_lock(rc, job_id: str, ttl: int) -> bool:
    return bool(await rc.set(f"{LOCK_PREFIX}{job_id}", "1", nx=True, ex=ttl))


async def release_lock(rc, job_id: str) -> None:
    await rc.delete(f"{LOCK_PREFIX}{job_id}")


async def save_report(rc, report: dict[str, Any]) -> None:
    await rc.set(f"{REPORT_PREFIX}{report['id']}", json.dumps(report))
    await rc.zadd(f"{REPORTS_BY_USER}{report['user']}", {report["id"]: report.get("generated_ts", 0)})
    await rc.set(
        f"{LATEST_PREFIX}{report['user']}:{report['type']}:{report['period']}",
        report["id"],
    )
    await rc.set(f"{LATEST_PREFIX}{report['user']}:{report['type']}:any", report["id"])


async def get_report(rc, report_id: str) -> dict[str, Any] | None:
    raw = await rc.get(f"{REPORT_PREFIX}{report_id}")
    return json.loads(raw) if raw else None


async def list_reports(
    rc, user: str, report_type: str | None = None, period: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    ids = await rc.zrevrange(f"{REPORTS_BY_USER}{user}", 0, max(0, limit * 3 - 1))
    out: list[dict[str, Any]] = []
    for report_id in ids or []:
        report = await get_report(rc, report_id)
        if not report:
            continue
        if report_type and report.get("type") != report_type:
            continue
        if period and report.get("period") != period:
            continue
        out.append(report)
        if len(out) >= limit:
            break
    return out


async def latest_report(rc, user: str, report_type: str, period: str = "any") -> dict[str, Any] | None:
    report_id = await rc.get(f"{LATEST_PREFIX}{user}:{report_type}:{period}")
    return await get_report(rc, report_id) if report_id else None


async def push_notification(rc, user: str, notification: dict[str, Any]) -> None:
    key = f"{NOTIFY_PREFIX}{user}"
    await rc.rpush(key, json.dumps(notification))
    await rc.ltrim(key, -NOTIFICATION_HISTORY, -1)


async def list_notifications(rc, user: str, limit: int = 20) -> list[dict[str, Any]]:
    raw = await rc.lrange(f"{NOTIFY_PREFIX}{user}", -limit, -1)
    return [json.loads(r) for r in raw if r]


def internal_headers() -> dict[str, str]:
    return {"X-Internal-Secret": INTERNAL_SECRET}


__all__ = [
    "GEO_SVC",
    "IDENTITY_SVC",
    "INTERNAL_SECRET",
    "acquire_lock",
    "dequeue_due",
    "delete_job",
    "enqueue",
    "get_job",
    "get_report",
    "internal_headers",
    "latest_report",
    "list_jobs",
    "list_notifications",
    "list_reports",
    "make_job",
    "push_notification",
    "release_lock",
    "save_job",
    "save_report",
    "utcnow_iso",
    "_client",
]
