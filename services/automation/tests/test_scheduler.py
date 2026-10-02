import json
from datetime import UTC, datetime, timedelta


import pytest

from services.automation import main as automation


class FakeRedis:
    """Minimal async Redis stand-in covering the methods the scheduler uses."""

    def __init__(self):
        self.strings: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expiry: dict[str, int] = {}
        self.deleted: list[str] = []
        self.scan_patterns: list[str] = []

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.strings[key] = value
        if ex is not None:
            self.expiry[key] = ex
        return True

    async def delete(self, key):
        self.deleted.append(key)
        self.strings.pop(key, None)
        return 1

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        if end == -1:
            self.lists[key] = items[start:]
        else:
            self.lists[key] = items[start:end + 1]

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start:end + 1]

    async def expire(self, key, seconds):
        self.expiry[key] = seconds
        return True

    async def scan_iter(self, match="*", count=100):
        self.scan_patterns.append(match)
        for key in list(self.strings):
            if key.endswith(":history"):
                continue
            if match == "*" or match == "timer:*" and key.startswith("timer:"):
                yield key

    async def aclose(self):
        return None


class FakeResponse:
    def __init__(self, status=200, text=""):
        self.status = status
        self._text = text

    async def text(self):
        return self._text


class FakeClient:
    def __init__(self, response=None, raises=None):
        self.response = response or FakeResponse()
        self.raises = raises
        self.calls: list[dict] = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.raises:
            raise self.raises
        return self.response

    async def close(self):
        return None


@pytest.fixture
def rc():
    return FakeRedis()


@pytest.fixture
def timer():
    now = datetime.now(UTC)
    return {
        "id": "timer-1",
        "user_id": "jeremiah",
        "title": "Monthly power report",
        "expires_at": (now - timedelta(minutes=1)).isoformat(),
        "active": True,
        "recurrence": None,
        "target_device": None,
    }


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(automation, "_get_client", lambda timeout=10.0: c)
    return c


def test_default_dispatch_targets_execution_trigger():
    t = {"id": "x", "user_id": "jeremiah"}
    method, url, payload = automation._resolve_dispatch(t)
    assert method == "POST"
    assert url == f"{automation.EXECUTION_SVC}/execute/trigger"
    assert payload == {"timer": t}


def test_explicit_target_resolves_allowlisted_service():
    t = {
        "id": "x",
        "user_id": "jeremiah",
        "target": {
            "service": "geo",
            "path": "/health-reports/run",
            "payload": {"period": "monthly"},
        },
    }
    method, url, payload = automation._resolve_dispatch(t)
    assert method == "POST"
    assert url == f"{automation.DISPATCH_TARGETS['geo']}/health-reports/run"
    assert payload["period"] == "monthly"
    # the timer is always forwarded so the target can attribute the run
    assert payload["timer"] == t
    assert payload["user_id"] == "jeremiah"


def test_target_rejects_unknown_service():
    t = {"target": {"service": "evil", "path": "/x"}}
    with pytest.raises(ValueError, match="not allowed"):
        automation._resolve_dispatch(t)


def test_target_rejects_absolute_url_path():
    t = {"target": {"service": "execution", "path": "http://attacker.example/x"}}
    with pytest.raises(ValueError, match="relative absolute path"):
        automation._resolve_dispatch(t)


def test_target_rejects_bad_method():
    t = {"target": {"service": "execution", "path": "/x", "method": "TRACE"}}
    with pytest.raises(ValueError, match="method not allowed"):
        automation._resolve_dispatch(t)


async def test_successful_one_shot_deletes_timer_and_records_history(rc, timer, client):
    key = "timer:jeremiah:timer-1"
    await rc.set(key, json.dumps(timer))

    await automation._fire_timer(rc, key, timer)

    assert key in rc.deleted
    assert client.calls[0]["url"].endswith("/execute/trigger")
    history = rc.lists[f"{key}:history"]
    assert json.loads(history[-1])["status"] == "success"


async def test_successful_recurring_timer_reschedules(rc, timer, client):
    timer["recurrence"] = "monthly"
    key = "timer:jeremiah:timer-1"
    await rc.set(key, json.dumps(timer))

    await automation._fire_timer(rc, key, timer)

    stored = json.loads(rc.strings[key])
    assert stored["expires_at"] > datetime.now(UTC).isoformat()
    assert stored["last_status"] == "success"
    assert stored["attempts"] == 0


async def test_failure_retries_with_exponential_backoff(rc, timer, monkeypatch):
    calls: list[dict] = []

    class FailingClient(FakeClient):
        async def request(self, method, url, **kwargs):
            calls.append({"method": method, "url": url})
            return FakeResponse(status=503, text="unavailable")

    monkeypatch.setattr(automation, "_get_client", lambda timeout=10.0: FailingClient())
    key = "timer:jeremiah:timer-1"
    await rc.set(key, json.dumps(timer))

    await automation._fire_timer(rc, key, timer)

    stored = json.loads(rc.strings[key])
    assert stored["attempts"] == 1
    assert stored["active"] is True
    assert not stored.get("failed")
    # backed off ~30s, not left due
    retry_at = datetime.fromisoformat(stored["expires_at"])
    assert retry_at > datetime.now(UTC)
    assert retry_at <= datetime.now(UTC) + timedelta(seconds=automation.RETRY_BASE_SECONDS + 5)
    assert json.loads(rc.lists[f"{key}:history"][-1])["status"] == "retry"
    assert key not in rc.deleted


async def test_failure_exhaustion_parks_timer(rc, timer, monkeypatch):
    class FailingClient(FakeClient):
        async def request(self, method, url, **kwargs):
            return FakeResponse(status=500, text="boom")

    monkeypatch.setattr(automation, "_get_client", lambda timeout=10.0: FailingClient())
    key = "timer:jeremiah:timer-1"
    timer["max_attempts"] = 2
    await rc.set(key, json.dumps(timer))

    await automation._fire_timer(rc, key, timer)
    assert json.loads(rc.strings[key])["attempts"] == 1

    await automation._fire_timer(rc, key, json.loads(rc.strings[key]))

    stored = json.loads(rc.strings[key])
    assert stored["attempts"] == 2
    assert stored["failed"] is True
    assert stored["active"] is False
    assert "boom" in stored["last_error"]
    assert rc.expiry.get(key) == automation.FAILED_TIMER_TTL_SECONDS
    assert json.loads(rc.lists[f"{key}:history"][-1])["status"] == "failed"


async def test_network_error_is_retried_not_lost(rc, timer, monkeypatch):
    class BoomClient(FakeClient):
        async def request(self, method, url, **kwargs):
            raise ConnectionResetError("alpaca went away")

    monkeypatch.setattr(automation, "_get_client", lambda timeout=10.0: BoomClient())
    key = "timer:jeremiah:timer-1"
    await rc.set(key, json.dumps(timer))

    await automation._fire_timer(rc, key, timer)

    stored = json.loads(rc.strings[key])
    assert stored["attempts"] == 1
    assert "ConnectionResetError" in stored["last_error"]
    assert stored["active"] is True


async def test_invalid_target_parks_immediately_without_retry(rc, timer):
    timer["target"] = {"service": "not-allowed", "path": "/x"}
    key = "timer:jeremiah:timer-1"
    await rc.set(key, json.dumps(timer))

    await automation._fire_timer(rc, key, timer)

    stored = json.loads(rc.strings[key])
    assert stored["active"] is False
    assert stored["last_status"] == "invalid_target"
    assert json.loads(rc.lists[f"{key}:history"][-1])["status"] == "invalid_target"


async def test_run_history_is_capped(rc, timer, client):
    key = "timer:jeremiah:timer-1"
    timer["recurrence"] = "daily"
    for _ in range(automation.HISTORY_MAX_ENTRIES + 5):
        await automation._fire_timer(rc, key, timer)
        timer = json.loads(rc.strings[key])
        timer["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await rc.set(key, json.dumps(timer))
        timer = json.loads(rc.strings[key])

    history = rc.lists[f"{key}:history"]
    assert len(history) == automation.HISTORY_MAX_ENTRIES


def test_retry_backoff_is_exponential_and_capped():
    assert automation._retry_delay_seconds(1) == automation.RETRY_BASE_SECONDS
    assert automation._retry_delay_seconds(2) == automation.RETRY_BASE_SECONDS * 2
    assert automation._retry_delay_seconds(3) == automation.RETRY_BASE_SECONDS * 4
    assert automation._retry_delay_seconds(50) == automation.RETRY_MAX_SECONDS


def test_monthly_and_yearly_recurrence_supported():
    base = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
    monthly = automation._next_recurrence(base, "monthly")
    assert (monthly.year, monthly.month, monthly.day) == (2026, 2, 28)

    yearly = automation._next_recurrence(datetime(2026, 3, 1, tzinfo=UTC), "yearly")
    assert yearly.year == 2027


async def test_collect_status_counts_due_and_failed(rc, timer):
    now = datetime.now(UTC)
    due = dict(timer)
    due["id"] = "due"
    future = dict(timer)
    future["id"] = "future"
    future["expires_at"] = (now + timedelta(hours=2)).isoformat()
    failed = dict(timer)
    failed["id"] = "failed"
    failed["active"] = False
    failed["failed"] = True
    for t in (due, future, failed):
        await rc.set(f"timer:jeremiah:{t['id']}", json.dumps(t))

    status = await automation._collect_status(rc)
    assert status["active_timers"] == 2
    assert status["due_now"] == 1
    assert status["failed_timers"] == 1
    assert status["next_due_at"] is not None


# ── The scheduler loop itself ────────────────────────────────────────────────
#
# Every other test in this file calls _fire_timer directly, so none of them
# execute the loop that calls it. That is how `for key in _iter_timer_keys(...)`
# survived: _iter_timer_keys is an async generator, a plain `for` over it raises
# 'async_generator' object is not iterable on the first pass, and the loop body
# was skipped entirely -- the scheduler never fired a single timer, it just
# logged once a second and retried. Testing the units is not testing the loop.
#
# These drive the real loop body by cancelling it after one pass.


class _StopAfterOnePass(Exception):
    """Raised from the patched sleep to break out of `while True`."""


async def _run_one_scheduler_pass(rc, monkeypatch):
    """Run scheduler_loop until its first sleep, and return what it did."""
    monkeypatch.setattr(automation.redis, "from_url", lambda *a, **k: rc)

    async def _resolve():
        return {}

    monkeypatch.setattr(
        "services.config.resolve_runtime_config", _resolve, raising=False
    )

    sleeps: list[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        raise _StopAfterOnePass

    monkeypatch.setattr(automation.asyncio, "sleep", _fake_sleep)

    fired: list[str] = []

    async def _record_fire(client, key, t, duration_ms=None):
        fired.append(key)
        await rc.delete(key)

    monkeypatch.setattr(automation, "_fire_timer", _record_fire)

    with pytest.raises(_StopAfterOnePass):
        await automation.scheduler_loop()
    return fired, sleeps


async def test_scheduler_loop_actually_fires_a_due_timer(rc, timer, monkeypatch):
    for t in (timer,):
        await rc.set(f"timer:jeremiah:{t['id']}", json.dumps(t))

    fired, _sleeps = await _run_one_scheduler_pass(rc, monkeypatch)

    assert fired == [f"timer:jeremiah:{timer['id']}"]


async def test_scheduler_loop_leaves_a_future_timer_alone(rc, timer, monkeypatch):
    future = dict(timer)
    future["expires_at"] = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    await rc.set(f"timer:jeremiah:{future['id']}", json.dumps(future))

    fired, _sleeps = await _run_one_scheduler_pass(rc, monkeypatch)

    assert fired == []


async def test_scheduler_loop_sleeps_until_the_next_due_timer(rc, timer, monkeypatch):
    """A future timer must shorten the sleep, or the loop polls at the cap."""
    soon = dict(timer)
    soon["expires_at"] = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    await rc.set(f"timer:jeremiah:{soon['id']}", json.dumps(soon))

    _fired, sleeps = await _run_one_scheduler_pass(rc, monkeypatch)

    assert len(sleeps) == 1
    assert sleeps[0] < automation.SCHEDULER_INTERVAL_MAX


async def test_scheduler_loop_iterates_timer_keys_asynchronously(rc, timer, monkeypatch):
    """Pins the bug directly: _iter_timer_keys is an async generator, so the
    loop must use `async for`. A plain `for` raised on the first pass and the
    loop silently never ran."""
    import inspect
    import re

    assert inspect.isasyncgenfunction(automation._iter_timer_keys)
    src = inspect.getsource(automation.scheduler_loop)
    # "async for key in ..." contains "for key in ..." as a substring, so a
    # plain `not in` check is wrong in the other direction -- it can never
    # distinguish the two forms. Look for a `for` with no `async` before it.
    assert not re.search(r"(?<!async )\bfor key in _iter_timer_keys\(", src), (
        "plain `for` over an async generator -- the scheduler body would be skipped"
    )
    assert "async for key in _iter_timer_keys(" in src
