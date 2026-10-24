# services/telemetry/main.py
"""Telemetry scheduling service.

Owns scheduled + on-demand report jobs (health/fitness and power), decides when
they run, and makes sure a run never interferes with work Alpaca is already
doing:

* If Alpaca is busy the run is deferred (no attempt consumed) and retried later.
* When it does run, the request is handed to Alpaca's own slot queue.
* Failures are retried with exponential backoff, then the job is marked failed.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import redis.asyncio as redis
from fastapi import HTTPException

from services.common.secrets import secret_matches
from services.telemetry import notices, push, store
from services.telemetry.alpaca import is_busy
from services.telemetry.config import (
    BUSY_DEFER_SECONDS,
    INTERNAL_SECRET,
    JOB_LOCK_TTL_SECONDS,
    MAX_ATTEMPTS,
    REDIS_URL,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    SCHEDULER_TICK_SECONDS,
    WORKER_TICK_SECONDS,
)
from services.telemetry.reports import generate_report
from services.telemetry.schedule import PERIODS, REPORT_TYPES, next_run_at
from services.telemetry.store import utcnow_iso

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
log = logging.getLogger("telemetry")

_http: aiohttp.ClientSession | None = None


def _client() -> aiohttp.ClientSession:
    global _http
    if _http is None or _http.closed:
        _http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0))
    return _http


def _redis() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def _retry_delay(attempts: int) -> int:
    return int(min(RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)), RETRY_MAX_SECONDS))


# Scheduler

async def scheduler_loop() -> None:
    """Queue scheduled jobs whose next run time has arrived."""
    rc = _redis()
    while True:
        try:
            now = datetime.now(UTC)
            for job in await _all_jobs(rc):
                if not job.get("enabled"):
                    continue
                nxt = job.get("next_run_at")
                if not nxt:
                    continue
                try:
                    due_at = datetime.fromisoformat(nxt)
                except ValueError:
                    continue
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=UTC)
                if now >= due_at:
                    await store.enqueue(rc, job["id"])
                    # Move the anchor forward so the job is not re-queued every
                    # tick while its run is still pending.
                    job["next_run_at"] = next_run_at(
                        job["period"], job["run_at"], job.get("timezone")
                    ).isoformat()
                    await store.save_job(rc, job)
                    log.info(
                        "Queued scheduled %s/%s report for %s",
                        job.get("type"), job.get("period"), job.get("user"),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Scheduler tick failed: %s", e)
        await asyncio.sleep(SCHEDULER_TICK_SECONDS)


async def _all_jobs(rc) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    async for key in rc.scan_iter(match=f"{store.JOB_PREFIX}*", count=200):
        raw = await rc.get(key)
        if raw:
            with contextlib.suppress(json.JSONDecodeError):
                jobs.append(json.loads(raw))
    return jobs


# Worker

async def deliver_notice(body: dict[str, Any]) -> dict[str, Any]:
    """One notice to one user, or to every admin with ``"user": "@admins"``
    (docs/PUSH.md, services/telemetry/notices.py): preference check, ``once``
    dedupe, the in-app outbox, then web push and ntfy, best effort. Report
    notices use it too, so every notice takes the same path."""
    try:
        fields = notices.parse(body)
    except notices.NoticeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    rc = _redis()
    # System notices (outages, the network, errors, deploys, disks) reach admins
    # only, whoever a sender addressed them to.
    system = fields["category"] == notices.ADMIN_CATEGORY
    if fields["user"] != notices.ADMINS and not system:
        return {"status": "SUCCESS", **await notices.deliver(rc, _client(), fields["user"], fields)}
    try:
        names = await notices.admins(_client())
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not resolve the admins: {e}") from None
    if fields["user"] != notices.ADMINS:
        if fields["user"].lower() not in names:
            log.warning("Dropped a system notice (%s) addressed to a non-admin", fields.get("tag"))
            return {"status": "SUCCESS", "dropped": "not_admin"}
        names = [fields["user"].lower()]
    recipients = {name: await notices.deliver(rc, _client(), name, fields) for name in names}
    return {"status": "SUCCESS", "recipients": recipients}


def _report_notice(job: dict[str, Any], kind: str, title: str, body: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": job["user"],
        "category": "reports",
        "kind": kind,
        "title": title,
        "body": body,
        "tag": f"report:{job.get('type')}:{job.get('period')}",
        "data": {"url": "/settings", "report_type": job.get("type"), "period": job.get("period"), **data},
    }


async def _notify(rc, job: dict[str, Any], report: dict[str, Any]) -> None:
    title = f"Your {report.get('period')} {report.get('type')} report is ready"
    await deliver_notice(_report_notice(
        job, "report_ready", title, (report.get("analysis") or "")[:180],
        {"report_id": report.get("id"), "status": report.get("status")},
    ))


async def _handle_failure(rc, job: dict[str, Any], error: str) -> None:
    attempts = int(job.get("attempts") or 0) + 1
    job["attempts"] = attempts
    job["last_error"] = error[:500]
    job["last_status"] = "error"
    delay = _retry_delay(attempts)
    if attempts >= min(int(job.get("max_attempts") or MAX_ATTEMPTS), MAX_ATTEMPTS):
        job["last_status"] = "failed"
        job["enabled"] = False
        job["failed_at"] = utcnow_iso()
        await store.save_job(rc, job)
        failure_title = f"Your {job.get('period')} {job.get('type')} report could not be generated"
        await deliver_notice(_report_notice(job, "report_failed", failure_title, error[:180], {"error": error[:200]}))
        log.error("Job %s failed permanently after %s attempts: %s", job["id"], attempts, error)
        return
    await store.save_job(rc, job)
    await store.enqueue(rc, job["id"], available_at=time.time() + delay)
    log.warning(
        "Job %s failed (%s); retry %s in %ss", job["id"], error, attempts, delay
    )


async def process_job(job_id: str) -> str:
    """Run one queued job. Returns the outcome for tests/observability."""
    rc = _redis()
    job = await store.get_job(rc, job_id)
    if not job:
        return "missing"

    if not await store.acquire_lock(rc, job_id, JOB_LOCK_TTL_SECONDS):
        return "locked"

    try:
        # 1. Never disturb work Alpaca is already doing: defer instead of
        #    competing, and do not spend an attempt on a busy machine.
        busy, reason = await is_busy()
        if busy:
            await store.enqueue(rc, job_id, available_at=time.time() + BUSY_DEFER_SECONDS)
            job["last_status"] = "deferred_busy"
            job["last_error"] = f"alpaca busy: {reason}"
            await store.save_job(rc, job)
            log.info("Job %s deferred, alpaca busy (%s)", job_id, reason)
            return "deferred"

        # 2. Generate. A failure here is retried with backoff.
        try:
            report = await generate_report(
                _client(), job["user"], job["type"], job["period"], job.get("timezone")
            )
        except Exception as e:
            await _handle_failure(rc, job, f"{type(e).__name__}: {e}")
            return "error"

        await store.save_report(rc, report)
        job["attempts"] = 0
        job["last_error"] = None
        job["last_status"] = report.get("status")
        job["last_run_at"] = utcnow_iso()
        if job.get("enabled") and job.get("period"):
            job["next_run_at"] = next_run_at(
                job["period"], job["run_at"], job.get("timezone")
            ).isoformat()
        await store.save_job(rc, job)
        if report.get("status") == "ready":
            await _notify(rc, job, report)
        return report.get("status", "ready")
    finally:
        await store.release_lock(rc, job_id)


async def worker_loop() -> None:
    rc = _redis()
    while True:
        try:
            for job_id in await store.dequeue_due(rc):
                try:
                    await process_job(job_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("Worker failed on job %s: %s", job_id, e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Worker tick failed: %s", e)
        await asyncio.sleep(WORKER_TICK_SECONDS)


# HTTP API

def _internal_ok(value: str | None) -> bool:
    expected = INTERNAL_SECRET or ""
    return bool(expected) and secret_matches(value or "", expected)


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(scheduler_loop()), asyncio.create_task(worker_loop())]
    log.info("Telemetry scheduler + worker started")
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if _http and not _http.closed:
            await _http.close()


def create_app():
    from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query

    async def require_internal(x_internal_secret: str | None = Header(None)):
        if not _internal_ok(x_internal_secret):
            raise HTTPException(status_code=401, detail="Invalid internal secret")
        return True

    application = FastAPI(title="Telemetry Scheduler", lifespan=lifespan)

    @application.get("/health")
    async def health():
        return {"status": "ok", "service": "telemetry"}

    @application.get("/api/telemetry/schedules/{user}")
    async def get_schedules(user: str, _: bool = Depends(require_internal)):
        rc = _redis()
        return {"status": "SUCCESS", "jobs": await store.list_jobs(rc, user)}

    @application.put("/api/telemetry/schedules/{user}")
    async def put_schedule(
        user: str,
        body: dict = Body(...),
        _: bool = Depends(require_internal),
    ):
        report_type = str(body.get("type") or "health")
        period = str(body.get("period") or "daily")
        if report_type not in REPORT_TYPES:
            raise HTTPException(status_code=422, detail=f"type must be one of {list(REPORT_TYPES)}")
        if period not in PERIODS:
            raise HTTPException(status_code=422, detail=f"period must be one of {list(PERIODS)}")
        run_at = str(body.get("run_at") or "21:00")
        tz = str(body.get("timezone") or "UTC")
        enabled = bool(body.get("enabled", True))
        rc = _redis()
        existing = await store.list_jobs(rc, user)
        match = next(
            (j for j in existing if j.get("type") == report_type and j.get("period") == period),
            None,
        )
        job = store.make_job(user, report_type, period, run_at, tz, enabled, job_id=match and match["id"])
        await store.save_job(rc, job)
        return {"status": "SUCCESS", "job": job}

    @application.delete("/api/telemetry/schedules/{user}/{job_id}")
    async def delete_schedule(user: str, job_id: str, _: bool = Depends(require_internal)):
        rc = _redis()
        job = await store.get_job(rc, job_id)
        if not job or job.get("user") != user:
            raise HTTPException(status_code=404, detail="Job not found")
        await store.delete_job(rc, job)
        return {"status": "SUCCESS", "deleted": job_id}

    @application.post("/api/telemetry/reports/request")
    async def request_report(
        body: dict = Body(...),
        _: bool = Depends(require_internal),
    ):
        """Queue an on-demand report. Returns immediately; the queue decides when."""
        user = str(body.get("user") or "")
        if not user:
            raise HTTPException(status_code=422, detail="user is required")
        report_type = str(body.get("type") or "health")
        period = str(body.get("period") or "daily")
        if report_type not in REPORT_TYPES or period not in PERIODS:
            raise HTTPException(status_code=422, detail="invalid type or period")
        rc = _redis()
        job = store.make_job(
            user,
            report_type,
            period,
            str(body.get("run_at") or "21:00"),
            str(body.get("timezone") or "UTC"),
            enabled=False,
        )
        await store.save_job(rc, job)
        await store.enqueue(rc, job["id"])
        return {"status": "QUEUED", "job_id": job["id"]}

    @application.get("/api/telemetry/reports/{user}")
    async def get_reports(
        user: str,
        type: str | None = Query(default=None),
        period: str | None = Query(default=None),
        limit: int = Query(default=20, le=100),
        _: bool = Depends(require_internal),
    ):
        rc = _redis()
        return {
            "status": "SUCCESS",
            "reports": await store.list_reports(rc, user, type, period, limit),
        }

    @application.get("/api/telemetry/reports/{user}/latest")
    async def get_latest(
        user: str,
        type: str = Query(default="health"),
        period: str = Query(default="any"),
        _: bool = Depends(require_internal),
    ):
        rc = _redis()
        report = await store.latest_report(rc, user, type, period)
        return {"status": "SUCCESS", "report": report}

    @application.get("/api/telemetry/notifications/{user}")
    async def get_notifications(
        user: str, limit: int = Query(default=20, le=100), _: bool = Depends(require_internal)
    ):
        rc = _redis()
        return {"status": "SUCCESS", "notifications": await store.list_notifications(rc, user, limit)}

    @application.post("/api/telemetry/activity")
    async def record_activity(body: dict = Body(...), _: bool = Depends(require_internal)):
        """One thing a person did inside Jarvis (music, a game, a page, a verse).

        Callers send what happened; the feed keeps the last few hundred events
        per user and a profile reads them back newest-first. ``at`` defaults to
        now (ISO-8601, UTC)."""
        user = str(body.get("user") or "").strip()
        feature = str(body.get("feature") or "").strip()
        if not user or not feature:
            raise HTTPException(status_code=422, detail="user and feature required")
        event = {
            "user": user,
            "feature": feature,
            "item": str(body.get("item") or "").strip(),
            "action": str(body.get("action") or "view").strip(),
            "duration_s": body.get("duration_s"),
            "at": str(body.get("at") or "").strip() or utcnow_iso(),
            "detail": body.get("detail") if isinstance(body.get("detail"), dict) else {},
        }
        rc = _redis()
        await store.append_activity(rc, user, event)
        return {"status": "SUCCESS", "event": event}

    @application.get("/api/telemetry/activity/{user}")
    async def get_activity(
        user: str, limit: int = Query(default=50, le=200), _: bool = Depends(require_internal)
    ):
        rc = _redis()
        return {"status": "SUCCESS", "events": await store.list_activity(rc, user, limit)}

    @application.post("/api/telemetry/usage")
    async def record_usage(body: dict = Body(...), _: bool = Depends(require_internal)):
        """One phone's day of screen time and when the day started and stopped."""
        user = str(body.get("user") or "").strip()
        day = str(body.get("day") or "").strip()
        if not user or not day:
            raise HTTPException(status_code=422, detail="user and day required")
        try:
            millis = int(body.get("millis") or 0)
            sessions = int(body.get("sessions") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="millis and sessions must be numbers")
        if millis < 0 or sessions < 0:
            raise HTTPException(status_code=422, detail="millis and sessions must not be negative")
        entry = {
            "day": day,
            "millis": millis,
            "first_use": body.get("first_use"),
            "last_use": body.get("last_use"),
            "sessions": sessions,
            "apps": body.get("apps") if isinstance(body.get("apps"), list) else [],
            "source": (str(body.get("source") or "phone").strip() or "phone"),
            "at": utcnow_iso(),
        }
        rc = _redis()
        merged = await store.merge_usage(rc, user, entry)
        return {"status": "SUCCESS", "usage": merged}

    @application.get("/api/telemetry/usage/{user}")
    async def get_usage_days(
        user: str, days: int = Query(default=7, le=60), _: bool = Depends(require_internal)
    ):
        rc = _redis()
        return {"status": "SUCCESS", "days": await store.get_usage(rc, user, days)}

    @application.post("/api/telemetry/night")
    async def record_night(body: dict = Body(...), _: bool = Depends(require_internal)):
        """One device's screen-on stretches overnight (the sleep estimate reads them)."""
        device = str(body.get("device") or "").strip()
        night = str(body.get("night") or "").strip()
        user = str(body.get("user") or "").strip()
        if not device or not night or not user or not isinstance(body.get("intervals"), list):
            raise HTTPException(status_code=422, detail="device, night, user and intervals required")
        rc = _redis()
        return {"status": "SUCCESS", "night": await store.merge_night(rc, device, night, body["intervals"], user)}

    @application.get("/api/telemetry/night")
    async def get_night(
        night: str, devices: str = Query(default=""), _: bool = Depends(require_internal)
    ):
        keys = [d.strip() for d in devices.split(",") if d.strip()][:20]
        rc = _redis()
        return {"status": "SUCCESS", "nights": await store.get_nights(rc, keys, night)}

    @application.post("/api/telemetry/notify")
    async def notify(body: dict = Body(...), _: bool = Depends(require_internal)):
        """Send one notification to a user from another service (docs/PUSH.md)."""
        return await deliver_notice(body)

    @application.get("/api/telemetry/push/key")
    async def get_push_key(_: bool = Depends(require_internal)):
        """VAPID public key the browser needs to subscribe."""
        return {"status": "SUCCESS", "public_key": await push.vapid_public_key()}

    @application.post("/api/telemetry/push/subscribe")
    async def subscribe_push(body: dict = Body(...), _: bool = Depends(require_internal)):
        user = str(body.get("user") or "")
        subscription = body.get("subscription")
        if not user or not isinstance(subscription, dict) or not subscription.get("endpoint"):
            raise HTTPException(status_code=422, detail="user and subscription.endpoint required")
        await push.save_subscription(user, subscription)
        return {"status": "SUCCESS"}

    @application.post("/api/telemetry/push/unsubscribe")
    async def unsubscribe_push(body: dict = Body(...), _: bool = Depends(require_internal)):
        user = str(body.get("user") or "")
        endpoint = str(body.get("endpoint") or "")
        if not user or not endpoint:
            raise HTTPException(status_code=422, detail="user and endpoint required")
        removed = await push.remove_subscription(user, endpoint)
        return {"status": "SUCCESS", "removed": removed}

    return application


app = create_app()


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("TELEMETRY_HOST", "0.0.0.0"),
        port=int(os.getenv("TELEMETRY_PORT", "11438")),
    )
