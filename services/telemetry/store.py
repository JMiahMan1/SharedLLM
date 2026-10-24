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

# What each person does inside Jarvis (music played, games finished, pages
# opened, verses read). Capped so a busy month cannot grow without bound.
ACTIVITY_PREFIX = "tel:activity:"
ACTIVITY_MAX = 500

# Screen time per day and when the day started and stopped (the phone's
# UsageStatsManager reports it; the profile and the parents' views read it).
USAGE_PREFIX = "tel:usage:"
USAGE_MAX_DAYS = 90

# Content-monitoring hits per child (newest last), and the weekly recaps the
# parents receive, kept by week start.
CONCERNS_PREFIX = "tel:concerns:"
CONCERNS_MAX = 300
RECAP_PREFIX = "tel:recap:"
RECAP_MAX_WEEKS = 12

# Screen-on stretches overnight per device (18:00 to noon), for the sleep
# estimate. Keyed by device so a tablet counts for whoever it is assigned to.
NIGHT_PREFIX = "tel:night:"
NIGHT_MAX = 14
NIGHT_MAX_INTERVALS = 500


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


async def append_activity(rc, user: str, event: dict[str, Any]) -> None:
    """Append one activity event, keeping only the newest ACTIVITY_MAX."""
    key = f"{ACTIVITY_PREFIX}{user}"
    await rc.rpush(key, json.dumps(event))
    await rc.ltrim(key, -ACTIVITY_MAX, -1)


async def list_activity(rc, user: str, limit: int = 50) -> list[dict[str, Any]]:
    """The user's recent events, newest first."""
    raw = await rc.lrange(f"{ACTIVITY_PREFIX}{user}", -limit, -1)
    events: list[dict[str, Any]] = []
    for item in reversed(raw):
        try:
            event = json.loads(item)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


async def merge_usage(rc, user: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Merge one day of device usage into the person's history.

    The phone re-reports as the day goes on, so the stored day is the union of
    what has been seen: the longest screen time, the earliest first use, the
    latest last use, the most sessions.
    """
    key = f"{USAGE_PREFIX}{user}"
    day = str(entry.get("day") or "").strip()
    raw = await rc.hget(key, day)
    merged: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            merged = parsed

    def _num(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    millis = _num(entry.get("millis")) or 0.0
    first = _num(entry.get("first_use"))
    last = _num(entry.get("last_use"))
    sessions = _num(entry.get("sessions")) or 0.0

    merged["day"] = day
    merged["millis"] = int(max(_num(merged.get("millis")) or 0.0, millis))
    prev_first = _num(merged.get("first_use"))
    if first is not None and (prev_first is None or first < prev_first):
        merged["first_use"] = int(first)
    elif "first_use" not in merged:
        merged["first_use"] = None
    prev_last = _num(merged.get("last_use"))
    if last is not None and (prev_last is None or last > prev_last):
        merged["last_use"] = int(last)
    elif "last_use" not in merged:
        merged["last_use"] = None
    merged["sessions"] = int(max(_num(merged.get("sessions")) or 0.0, sessions))
    merged["source"] = str(entry.get("source") or merged.get("source") or "phone")
    merged["updated_at"] = str(entry.get("at") or utcnow_iso())

    # Per-app foreground time for the day, merged by package: keep the longest
    # reading seen for each app so a re-report never shrinks a row.
    incoming_apps = entry.get("apps")
    if isinstance(incoming_apps, list):
        by_pkg: dict[str, dict[str, Any]] = {}
        for existing in (merged.get("apps") or []):
            if isinstance(existing, dict) and existing.get("package"):
                by_pkg[str(existing["package"])] = dict(existing)
        for raw in incoming_apps:
            if not isinstance(raw, dict):
                continue
            pkg = str(raw.get("package") or "").strip()
            if not pkg:
                continue
            try:
                amillis = int(raw.get("millis") or 0)
            except (TypeError, ValueError):
                amillis = 0
            alast = raw.get("last_use")
            try:
                alast_i = int(alast) if alast is not None else None
            except (TypeError, ValueError):
                alast_i = None
            prev = by_pkg.get(pkg)
            if prev is None:
                by_pkg[pkg] = {"package": pkg, "millis": max(0, amillis), "last_use": alast_i}
            else:
                prev["millis"] = max(int(prev.get("millis") or 0), max(0, amillis))
                prev_last_app = prev.get("last_use")
                if alast_i is not None and (prev_last_app is None or alast_i > prev_last_app):
                    prev["last_use"] = alast_i
        merged["apps"] = sorted(by_pkg.values(), key=lambda a: int(a.get("millis") or 0), reverse=True)

    await rc.hset(key, day, json.dumps(merged))

    if await rc.hlen(key) > USAGE_MAX_DAYS:
        stale = sorted((await rc.hgetall(key)).keys())
        for old_day in stale[: len(stale) - USAGE_MAX_DAYS]:
            await rc.hdel(key, old_day)
    return merged


def _intervals(raw: Any) -> list[list[int]]:
    out: list[list[int]] = []
    for pair in raw if isinstance(raw, list) else []:
        try:
            start, end = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end >= start >= 0:
            out.append([start, end])
    return out


async def merge_night(rc, device: str, night: str, intervals: list[Any], user: str) -> dict[str, Any]:
    """Merge one device's overnight screen-on stretches into that night.

    Re-reports through the morning only add stretches, never drop one, so a
    later short reading cannot shrink what was seen.
    """
    key = f"{NIGHT_PREFIX}{device}"
    raw = await rc.hget(key, night)
    try:
        existing = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        existing = {}
    seen = {tuple(pair) for pair in _intervals(existing.get("intervals"))}
    seen.update(tuple(pair) for pair in _intervals(intervals))
    merged = {
        "night": night,
        "device": device,
        "user": user,
        "intervals": [list(pair) for pair in sorted(seen)][-NIGHT_MAX_INTERVALS:],
        "updated_at": utcnow_iso(),
    }
    await rc.hset(key, night, json.dumps(merged))
    if await rc.hlen(key) > NIGHT_MAX:
        stale = sorted((await rc.hgetall(key)).keys())
        for old in stale[: len(stale) - NIGHT_MAX]:
            await rc.hdel(key, old)
    return merged


async def get_nights(rc, devices: list[str], night: str) -> list[dict[str, Any]]:
    """The stored night for each device that reported one."""
    out: list[dict[str, Any]] = []
    for device in devices:
        raw = await rc.hget(f"{NIGHT_PREFIX}{device}", night)
        try:
            parsed = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


async def add_concerns(rc, user: str, concerns: list[dict[str, Any]]) -> None:
    key = f"{CONCERNS_PREFIX}{user}"
    for concern in concerns:
        await rc.rpush(key, json.dumps(concern))
    await rc.ltrim(key, -CONCERNS_MAX, -1)


async def list_concerns(rc, user: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in await rc.lrange(f"{CONCERNS_PREFIX}{user}", 0, -1):
        try:
            item = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


async def save_recap(rc, user: str, week: str, recap: dict[str, Any]) -> None:
    key = f"{RECAP_PREFIX}{user}"
    await rc.hset(key, week, json.dumps(recap))
    if await rc.hlen(key) > RECAP_MAX_WEEKS:
        stale = sorted((await rc.hgetall(key)).keys())
        for old in stale[: len(stale) - RECAP_MAX_WEEKS]:
            await rc.hdel(key, old)


async def get_usage(rc, user: str, days: int = 7) -> list[dict[str, Any]]:
    """The person's recent days of usage, newest day first."""
    raw = await rc.hgetall(f"{USAGE_PREFIX}{user}")
    out: list[dict[str, Any]] = []
    for value in raw.values():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("day"):
            out.append(parsed)
    out.sort(key=lambda item: str(item.get("day")), reverse=True)
    return out[:days]


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
