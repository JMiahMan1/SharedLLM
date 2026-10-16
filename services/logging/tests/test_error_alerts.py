"""Admins hear about error spikes in the central log, at most once per half
hour per error (services/logging/error_alerts.py)."""
import asyncio
import json

from services.logging import error_alerts

ACCESS = 'INFO:     172.26.0.9:4120 - "GET /api/notes/{n} HTTP/1.1" 500 Internal Server Error'


def _err(message, service="gateway", level="ERROR"):
    return {"service": service, "level": level, "message": message}


def test_readable_message_matches_the_dashboard():
    # Same cases as readableLogMessage in services/ui/src/pages/Dashboard.tsx.
    assert error_alerts.readable_message(ACCESS.format(n=7)) == "GET /api/notes/7: 500 Internal Server Error"
    assert error_alerts.readable_message('1.2.3.4:5 - "POST /x HTTP/1.0" 404') == "POST /x: 404"
    assert error_alerts.readable_message("Redis connection refused") == "Redis connection refused"
    assert error_alerts.readable_message("") == ""


def test_fingerprint_ignores_numbers_and_ids_but_not_the_service():
    a = error_alerts.fingerprint(_err(ACCESS.format(n=7)))
    b = error_alerts.fingerprint(_err(ACCESS.format(n=1234)))
    c = error_alerts.fingerprint(_err("mission 3f9a2b7c1d failed"))
    d = error_alerts.fingerprint(_err("mission 0011aabbcc failed"))
    assert a == b and c == d and a != c
    assert error_alerts.fingerprint(_err("boom", service="rag")) != error_alerts.fingerprint(_err("boom"))


def test_a_repeated_error_above_the_limit_is_reported_with_its_readable_message():
    entries = [_err(ACCESS.format(n=i)) for i in range(11)]
    notices = error_alerts.spike_notices(entries, total_threshold=20, repeat_threshold=10)
    assert len(notices) == 1
    _, notice = notices[0]
    assert notice["title"] == "Repeated error: gateway"
    assert notice["body"].startswith("GET /api/notes/0: 500 Internal Server Error (11 times")
    assert notice["actions"] == [{"label": "Open", "type": "view", "url": "/"}]


def test_exactly_at_the_limits_is_quiet():
    entries = [_err(f"error kind {chr(97 + i % 2)}") for i in range(20)]
    assert error_alerts.spike_notices(entries, total_threshold=20, repeat_threshold=10) == []


def test_many_different_errors_make_a_spike():
    entries = [_err(f"failure {chr(97 + i % 26)}{chr(97 + i // 26)}", service="rag") for i in range(21)]
    entries += [_err("just a warning", level="WARNING")] * 50
    notices = error_alerts.spike_notices(entries, total_threshold=20, repeat_threshold=10)
    assert [fp for fp, _ in notices] == [error_alerts.SPIKE_FINGERPRINT]
    assert notices[0][1]["body"].startswith("21 errors in the last 5 minutes")


class _Redis:
    def __init__(self, entries, now):
        self.entries = [(now - 10, json.dumps(e)) for e in entries]
        self.kv: dict[str, str] = {}

    async def zrangebyscore(self, key, lo, hi):
        assert key == "logs:entries"
        return [raw for ts, raw in self.entries if lo <= ts <= hi]

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def delete(self, key):
        self.kv.pop(key, None)


def _check(monkeypatch, r, now, accept=True):
    sent: list[dict] = []

    async def notify(notice):
        if accept:
            sent.append(notice)
        return accept

    monkeypatch.setattr(error_alerts, "notify_admins", notify)
    asyncio.run(error_alerts.check_once(r, 20, 10, now=now))
    return sent


def test_one_notice_per_fingerprint_per_cooldown(monkeypatch):
    now = 1_000_000.0
    r = _Redis([_err(ACCESS.format(n=i)) for i in range(12)], now)
    assert len(_check(monkeypatch, r, now)) == 1
    assert _check(monkeypatch, r, now + 60) == []
    # The cooldown is a Redis key that expires after 30 minutes.
    key = next(iter(r.kv))
    assert key.startswith("admin:alerts:errors:")
    r.kv.clear()
    assert len(_check(monkeypatch, r, now + 60)) == 1


def test_an_undelivered_notice_is_retried(monkeypatch):
    now = 1_000_000.0
    r = _Redis([_err(ACCESS.format(n=i)) for i in range(12)], now)
    assert _check(monkeypatch, r, now, accept=False) == []
    assert r.kv == {}
    assert len(_check(monkeypatch, r, now + 60)) == 1


def test_errors_older_than_five_minutes_do_not_count(monkeypatch):
    now = 1_000_000.0
    r = _Redis([_err(ACCESS.format(n=i)) for i in range(12)], now - 400)
    assert _check(monkeypatch, r, now) == []
