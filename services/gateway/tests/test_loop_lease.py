"""One gateway runs the loops that post things; a lease decides which one.

Claiming inference jobs is safe from any number of workers. Everything else in
the worker posts something - bot replies, chat notifications, admin alerts,
song notices, cleanup - so a second replica must stand down.
"""
import pytest

from services.gateway.messaging import InferenceJobQueue


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int | None] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.expiries[key] = ex
        return True

    async def get(self, key):
        return self.store.get(key)

    async def expire(self, key, ttl):
        self.expiries[key] = ttl
        return True


class _BrokenRedis:
    async def set(self, *args, **kwargs):
        raise ConnectionError("no redis")

    async def get(self, *args, **kwargs):
        raise ConnectionError("no redis")


def _queue(redis):
    queue = InferenceJobQueue("redis://unused")
    queue._redis = redis
    return queue


@pytest.mark.asyncio
async def test_the_first_worker_takes_a_lease_and_the_second_stands_down():
    redis = _FakeRedis()
    first, second = _queue(redis), _queue(redis)

    assert await first.claim_loop("chat_notify", "one", 60) is True
    assert await second.claim_loop("chat_notify", "two", 60) is False
    # The holder refreshing keeps it, and each loop has its own lease.
    assert await first.claim_loop("chat_notify", "one", 60) is True
    assert redis.expiries["raven:loop:chat_notify"] == 60
    assert await second.claim_loop("talk_monitor", "two", 60) is True


@pytest.mark.asyncio
async def test_a_redis_failure_fails_open():
    # Losing every notification would be worse than a possible duplicate.
    assert await _queue(_BrokenRedis()).claim_loop("songs", "one", 60) is True


@pytest.mark.asyncio
async def test_the_health_loop_stands_down_when_somebody_else_holds_the_lease(monkeypatch):
    from services.gateway import background_worker as bw

    worker = bw.RavenWorker()
    worker.is_running = True
    seen: dict[str, object] = {}

    async def fake_settings():
        return {"raven_scan_interval": "42"}

    async def fake_check(threshold, _settings):
        seen["scanned"] = True

    async def fake_sleep(seconds):
        seen["sleep"] = seconds
        worker.is_running = False

    async def no_lease(name, token, ttl):
        return False

    import services.gateway.agent_loop as agent_loop

    monkeypatch.setattr(agent_loop, "get_dynamic_llm_settings", fake_settings)
    monkeypatch.setattr(worker, "perform_health_check", fake_check)
    monkeypatch.setattr(worker.job_queue, "claim_loop", no_lease)
    monkeypatch.setattr(bw.asyncio, "sleep", fake_sleep)

    await worker._health_loop()

    assert "scanned" not in seen
    assert seen == {"sleep": 42}
