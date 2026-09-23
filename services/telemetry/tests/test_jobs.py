import json
import os
import time
from datetime import UTC, datetime, timedelta

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("FERNET_KEY", "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE=")

import pytest

from services.telemetry import store
from services.telemetry.alpaca import evaluate_slots
from services.telemetry.config import BUSY_DEFER_SECONDS, MAX_ATTEMPTS
from services.telemetry.main import process_job
from services.telemetry.schedule import next_run_at, window_for

from fakes import FakeRedis


@pytest.fixture
def rc(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    return fake


def make_job(user="jeremiah", report_type="health", period="daily", enabled=True):
    return store.make_job(user, report_type, period, "21:00", "UTC", enabled=enabled)


# ── schedule math ───────────────────────────────────────────────────────────

def test_daily_job_fires_next_occurrence_at_local_time():
    after = datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
    nxt = next_run_at("daily", "21:00", "UTC", after)
    assert nxt == datetime(2026, 5, 10, 21, 0, tzinfo=UTC)


def test_daily_job_rolls_to_tomorrow_when_time_passed():
    after = datetime(2026, 5, 10, 22, 0, tzinfo=UTC)
    nxt = next_run_at("daily", "21:00", "UTC", after)
    assert nxt == datetime(2026, 5, 11, 21, 0, tzinfo=UTC)


def test_weekly_and_monthly_and_yearly_anchor_correctly():
    after = datetime(2026, 5, 10, 22, 0, tzinfo=UTC)
    assert next_run_at("weekly", "21:00", "UTC", after) == datetime(2026, 5, 17, 21, 0, tzinfo=UTC)
    assert next_run_at("monthly", "21:00", "UTC", after) == datetime(2026, 6, 10, 21, 0, tzinfo=UTC)
    assert next_run_at("yearly", "21:00", "UTC", after) == datetime(2027, 5, 10, 21, 0, tzinfo=UTC)


def test_monthly_clamps_to_last_valid_day():
    after = datetime(2026, 1, 31, 22, 0, tzinfo=UTC)
    nxt = next_run_at("monthly", "21:00", "UTC", after)
    assert (nxt.month, nxt.day) == (2, 28)


def test_timezone_is_respected():
    # 21:00 in a UTC-7 zone is 04:00 UTC the next day
    after = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    nxt = next_run_at("daily", "21:00", "America/Phoenix", after)
    assert nxt == datetime(2026, 5, 11, 4, 0, tzinfo=UTC)


def test_window_sizes_match_period():
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    for period, days in (("daily", 1), ("weekly", 7), ("monthly", 30), ("yearly", 365)):
        start, end = window_for(period, now=now, tz="UTC")
        assert end == now
        assert (end - start).days == days


# ── alpaca admission ─────────────────────────────────────────────────────────

def test_slots_busy_detection():
    assert evaluate_slots({"slots": [{"is_processing": True}]})[0] is True
    assert evaluate_slots({"slots": [{"is_processing": False}]})[0] is False
    assert evaluate_slots({"slots": [{"state": 1}]})[0] is True
    assert evaluate_slots({"slots": [{"state": 0}]})[0] is False
    assert evaluate_slots({"slots": []})[0] is True
    assert evaluate_slots({})[0] is True


async def test_job_defers_without_consuming_attempt_when_alpaca_busy(rc, monkeypatch):
    job = make_job()
    await store.save_job(rc, job)
    await store.enqueue(rc, job["id"])

    async def busy(_model=None):
        return True, "2/2 slots busy"

    monkeypatch.setattr("services.telemetry.main.is_busy", busy)

    outcome = await process_job(job["id"])
    assert outcome == "deferred"

    stored = await store.get_job(rc, job["id"])
    assert stored["attempts"] == 0  # a busy machine is not a failure
    assert stored["last_status"] == "deferred_busy"
    # requeued with a defer delay rather than dropped
    assert job["id"] in rc.zsets[store.QUEUE]


async def test_job_runs_when_alpaca_idle(rc, monkeypatch):
    job = make_job()
    await store.save_job(rc, job)
    await store.enqueue(rc, job["id"])

    async def idle(_model=None):
        return False, "slots idle"

    async def fake_generate(_client, user, report_type, period, tz):
        return {
            "id": "r1",
            "user": user,
            "type": report_type,
            "period": period,
            "status": "ready",
            "analysis": "Solid week.",
            "stats": {"days_with_data": 7},
            "generated_ts": 1,
        }

    monkeypatch.setattr("services.telemetry.main.is_busy", idle)
    monkeypatch.setattr("services.telemetry.main.generate_report", fake_generate)

    outcome = await process_job(job["id"])
    assert outcome == "ready"

    report = await store.latest_report(rc, "jeremiah", "health")
    assert report["analysis"] == "Solid week."

    notifications = await store.list_notifications(rc, "jeremiah")
    assert notifications[-1]["kind"] == "report_ready"

    stored = await store.get_job(rc, job["id"])
    assert stored["attempts"] == 0
    assert stored["last_status"] == "ready"
    assert stored["next_run_at"] > store.utcnow_iso()


async def test_failure_retries_then_disables_and_notifies(rc, monkeypatch):
    job = make_job()
    job["max_attempts"] = 2
    await store.save_job(rc, job)
    await store.enqueue(rc, job["id"])

    async def idle(_model=None):
        return False, "idle"

    async def boom(*_args, **_kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr("services.telemetry.main.is_busy", idle)
    monkeypatch.setattr("services.telemetry.main.generate_report", boom)

    assert await process_job(job["id"]) == "error"
    first = await store.get_job(rc, job["id"])
    assert first["attempts"] == 1
    assert first["enabled"] is True  # will retry

    await store.enqueue(rc, job["id"])
    assert await process_job(job["id"]) == "error"
    second = await store.get_job(rc, job["id"])
    assert second["attempts"] >= 2
    assert second["enabled"] is False
    assert second["last_status"] == "failed"

    notifications = await store.list_notifications(rc, "jeremiah")
    assert notifications[-1]["kind"] == "report_failed"


async def test_no_data_window_reports_no_data_without_analysis(rc, monkeypatch):
    job = make_job()
    await store.save_job(rc, job)
    await store.enqueue(rc, job["id"])

    async def idle(_model=None):
        return False, "idle"

    async def no_data(_client, user, report_type, period, tz):
        return {
            "id": "r-empty",
            "user": user,
            "type": report_type,
            "period": period,
            "status": "no_data",
            "analysis": None,
            "stats": {"days_with_data": 0},
            "generated_ts": 2,
        }

    monkeypatch.setattr("services.telemetry.main.is_busy", idle)
    monkeypatch.setattr("services.telemetry.main.generate_report", no_data)

    outcome = await process_job(job["id"])
    assert outcome == "no_data"
    notifications = await store.list_notifications(rc, "jeremiah")
    assert all(n["kind"] != "report_ready" for n in notifications)


# ── store ───────────────────────────────────────────────────────────────────

async def test_jobs_are_scoped_per_user(rc):
    await store.save_job(rc, make_job(user="jeremiah"))
    await store.save_job(rc, make_job(user="work"))
    mine = await store.list_jobs(rc, "jeremiah")
    assert len(mine) == 1 and mine[0]["user"] == "jeremiah"


async def test_reports_are_filtered_by_type_and_period(rc):
    await store.save_report(rc, {"id": "a", "user": "u", "type": "health", "period": "daily", "generated_ts": 1})
    await store.save_report(rc, {"id": "b", "user": "u", "type": "power", "period": "monthly", "generated_ts": 2})
    assert len(await store.list_reports(rc, "u", "health")) == 1
    assert len(await store.list_reports(rc, "u", period="monthly")) == 1
    assert len(await store.list_reports(rc, "u")) == 2


async def test_dequeue_respects_defer_time(rc):
    await store.enqueue(rc, "now-job")
    await store.enqueue(rc, "later-job", available_at=time.time() + 600)
    claimed = await store.dequeue_due(rc)
    assert claimed == ["now-job"]
