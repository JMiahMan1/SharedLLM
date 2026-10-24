"""The Raven health loop reads its interval and threshold from the Raven Ops settings."""

import pytest

from services.gateway import background_worker as bw


async def _one_pass(monkeypatch, settings):
    worker = bw.RavenWorker()
    worker.is_running = True
    seen = {}

    async def fake_settings():
        return settings

    async def fake_check(threshold, _settings):
        seen["threshold"] = threshold

    async def fake_sleep(seconds):
        seen["sleep"] = seconds
        worker.is_running = False

    import services.gateway.agent_loop as agent_loop
    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", fake_settings)
    monkeypatch.setattr(worker, "perform_health_check", fake_check)
    monkeypatch.setattr(bw.asyncio, "sleep", fake_sleep)
    await worker._health_loop()
    return seen


@pytest.mark.asyncio
async def test_settings_drive_interval_and_threshold(monkeypatch):
    seen = await _one_pass(monkeypatch, {"raven_scan_interval": "42", "raven_error_threshold": "7"})
    assert seen == {"threshold": 7, "sleep": 42}


@pytest.mark.asyncio
async def test_config_defaults_when_settings_are_unset(monkeypatch):
    # Identity unreachable -> {}; unset values arrive as None.
    seen = await _one_pass(monkeypatch, {"raven_scan_interval": None})
    assert seen == {"threshold": bw.RAVEN_ERROR_THRESHOLD, "sleep": bw.RAVEN_CHECK_INTERVAL}


@pytest.mark.asyncio
async def test_suspended_skips_the_scan(monkeypatch):
    seen = await _one_pass(monkeypatch, {"raven_suspended": "true", "raven_scan_interval": "9"})
    assert seen == {"sleep": 9}


class _Resp:
    status = 200

    def __init__(self, body):
        self._body = body

    async def text(self):
        import json
        return json.dumps(self._body)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("pending", True), ("queued", True), ("completed", False)])
async def test_a_fix_awaiting_approval_is_not_filed_again(monkeypatch, status, expected):
    from contextlib import asynccontextmanager

    class _Client:
        async def get(self, *a, **k):
            return _Resp([{"id": 1, "mission_type": "admin_fix", "target_container": "sharedllm_geo", "status": status}])

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(bw, "_shared_http_client", fake_client)
    assert await bw.RavenWorker()._has_pending_self_repair("sharedllm_geo") is expected
