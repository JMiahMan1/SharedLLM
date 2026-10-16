"""Raven's streamed tokens are written in batches, not one entry per token."""
import json

from services.gateway import agent_loop


class FakeRedis:
    def __init__(self):
        self.history, self.published = [], []

    async def rpush(self, key, value):
        self.history.append(json.loads(value))

    async def ltrim(self, key, start, end):
        self.history = self.history[start:]

    async def expire(self, key, seconds):
        pass

    async def publish(self, channel, value):
        self.published.append(value)


async def test_tokens_are_batched_and_order_is_kept(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(agent_loop, "_stream_redis", fake)
    monkeypatch.setattr(agent_loop, "REASONING_FLUSH_SECONDS", 3600)
    monkeypatch.setattr(agent_loop, "REASONING_FLUSH_CHARS", 50)
    events = []

    async def capture(stream_event):
        for _ in range(30):
            await stream_event("reasoning", "tok ")
        await stream_event("system", "Tool ran")
        events.extend(fake.history)

    stream = agent_loop.MissionEventStream(7, [])
    await capture(stream.event)
    reasoning = [e for e in events if e["type"] == "reasoning"]
    assert 2 <= len(reasoning) <= 4  # 30 tokens, not 30 entries
    assert "".join(e["data"] for e in reasoning) == "tok " * 30
    assert events[-1]["type"] == "system"  # flushed before the next event


async def test_the_history_is_capped(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(agent_loop, "_stream_redis", fake)
    monkeypatch.setattr(agent_loop, "MISSION_HISTORY_MAX", 3)
    stream = agent_loop.MissionEventStream(7, [])
    for i in range(5):
        await stream.event("system", f"step {i}")
    assert [e["data"] for e in fake.history] == ["step 2", "step 3", "step 4"]


async def test_the_last_tokens_are_flushed(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(agent_loop, "_stream_redis", fake)
    stream = agent_loop.MissionEventStream(7, [])
    await stream.event("reasoning", "almost done")
    assert fake.history == []
    await stream.flush()
    assert fake.history[0]["data"] == "almost done"
