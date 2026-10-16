"""``?level=warning,error`` returns only problems, from the whole buffer.

The log is almost entirely gateway request lines at INFO, so the dashboard's
"Recent problems" panel needs the service to filter rather than page through
the newest few hundred lines.
"""
import asyncio
import json

from services.logging import main as logging_main


class _Redis:
    def __init__(self, entries):
        self.entries = entries
        self.num = None

    async def zrevrangebyscore(self, key, hi, lo, start=0, num=None):
        self.num = num
        return [json.dumps(e) for e in self.entries][:num]


def _run(monkeypatch, entries, **kw):
    fake = _Redis(entries)

    async def _get():
        return fake

    monkeypatch.setattr(logging_main, "get_redis", _get)
    return asyncio.run(logging_main._fetch_logs(**kw)), fake


def test_level_filter_keeps_only_matching_levels(monkeypatch):
    entries = [{"level": "INFO", "message": f"GET /x {i}"} for i in range(600)]
    entries += [{"level": "WARNING", "message": "RESPONSE GET /a -> 401"}, {"level": "ERROR", "message": "RESPONSE GET /b -> 502"}]
    out, fake = _run(monkeypatch, entries, user_id="admin", limit=10, levels="warning,error")
    assert [e["level"] for e in out] == ["WARNING", "ERROR"]
    assert fake.num == logging_main.LOG_MAX_ENTRIES


def test_warn_alias_and_no_filter(monkeypatch):
    entries = [{"level": "WARN", "message": "a"}, {"level": "INFO", "message": "b"}]
    out, _ = _run(monkeypatch, entries, user_id="admin", limit=10, levels="warning")
    assert [e["message"] for e in out] == ["a"]
    out, fake = _run(monkeypatch, entries, user_id="admin", limit=10)
    assert len(out) == 2 and fake.num == 100
