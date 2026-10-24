"""A paused Raven mission wakes up even when no resume is ever published
(services/gateway/agent_loop.py:_await_mission_resume)."""
import asyncio

from services.gateway import agent_loop


class FakePubSub:
    def __init__(self, messages=()):
        self.messages = list(messages)

    async def subscribe(self, *keys):
        pass

    async def get_message(self, ignore_subscribe_messages=True, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def unsubscribe(self, *keys):
        pass

    async def close(self):
        pass


class FakeRedis:
    def __init__(self, pubsub):
        self._pubsub = pubsub

    def pubsub(self):
        return self._pubsub

    async def close(self):
        pass


def _setup(monkeypatch, flags, messages=()):
    """``flags``: key -> list of values returned on successive reads."""
    async def flag(key):
        values = flags.get(key.rsplit(":", 2)[-2], [False])
        return values.pop(0) if len(values) > 1 else values[0]

    monkeypatch.setattr(agent_loop, "_redis_flag_active", flag)
    monkeypatch.setattr(agent_loop.redis, "from_url", lambda *a, **k: FakeRedis(FakePubSub(messages)))
    monkeypatch.setattr(agent_loop, "PAUSE_POLL_SECONDS", 0.01)
    events = []

    async def stream_event(kind, text):
        events.append(text)

    return events, stream_event


async def test_a_pause_that_expires_with_nothing_published_resumes(monkeypatch):
    # Paused at the start and after subscribing; then the key is gone (expired).
    events, stream = _setup(monkeypatch, {"pause": [True, True, True, False]})
    killed = await asyncio.wait_for(agent_loop._await_mission_resume(7, stream), timeout=2)
    assert killed is False and events[-1] == "Mission resumed."


async def test_a_kill_set_while_paused_stops_the_mission(monkeypatch):
    events, stream = _setup(monkeypatch, {"pause": [True], "kill": [False, True]})
    killed = await asyncio.wait_for(agent_loop._await_mission_resume(7, stream), timeout=2)
    assert killed is True and events[-1] == "Mission terminated by user."


async def test_a_published_resume_still_works(monkeypatch):
    _events, stream = _setup(monkeypatch, {"pause": [True]}, messages=[{"type": "message", "channel": "x", "data": "RESUMED"}])
    assert await asyncio.wait_for(agent_loop._await_mission_resume(7, stream), timeout=2) is False
