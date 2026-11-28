"""Non-blocking Redis history and the revived long-term user-fact memory."""
from __future__ import annotations

import asyncio
import threading

import pytest

from services.gateway import history, orchestrator


class _FakePipe:
    def __init__(self, redis):
        self.redis = redis
        self.ops: list[tuple[str, str, object]] = []

    def rpush(self, key, msg):
        self.ops.append(("rpush", key, msg))

    def ltrim(self, key, start, end):
        self.ops.append(("ltrim", key, (start, end)))

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))

    def execute(self):
        self.redis.threads.append(threading.get_ident())
        for op, key, value in self.ops:
            if op == "rpush":
                self.redis.rows.setdefault(key, []).append(value)
            elif op == "ltrim":
                self.redis.rows[key] = self.redis.rows.get(key, [])[-10:]
        return []


class _FakeRedis:
    def __init__(self):
        self.rows: dict[str, list[str]] = {}
        self.threads: list[int] = []

    def lrange(self, key, start, end):
        self.threads.append(threading.get_ident())
        return list(self.rows.get(key, []))

    def pipeline(self):
        return _FakePipe(self)


async def test_history_reads_and_writes_run_off_the_event_loop(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(history, "get_redis", lambda: fake)
    monkeypatch.setattr(history, "_schedule_fact_extraction", lambda *_: None)
    main_thread = threading.get_ident()

    await history.update_history("alice", "user", "hello")
    await history.update_history("alice", "assistant", "hi")
    messages = await history.get_history("alice")

    assert [m["content"] for m in messages] == ["hello", "hi"]
    assert fake.threads
    assert all(thread_id != main_thread for thread_id in fake.threads)


async def test_room_history_reads_and_writes_run_off_the_event_loop(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(history, "get_redis", lambda: fake)
    main_thread = threading.get_ident()

    await history.update_room_history("room-1", "user", "hello")
    messages = await history.get_room_history("room-1")

    assert [m["content"] for m in messages] == ["hello"]
    assert all(thread_id != main_thread for thread_id in fake.threads)


async def test_only_user_turns_schedule_fact_extraction(monkeypatch):
    fake = _FakeRedis()
    scheduled: list[str] = []
    monkeypatch.setattr(history, "get_redis", lambda: fake)
    monkeypatch.setattr(history, "_schedule_fact_extraction", scheduled.append)

    await history.update_history("alice", "assistant", "hi")
    await history.update_history("alice", "user", "my dog is Rex")
    await history.update_room_history("room-1", "user", "hello")

    assert scheduled == ["alice"]


async def test_household_is_never_scheduled_for_fact_extraction(monkeypatch):
    created: list[str] = []

    async def fake_task(user_id):
        created.append(user_id)

    monkeypatch.setattr(history, "_fact_extraction_task", fake_task)
    monkeypatch.setattr(history, "FACT_EXTRACTION_INTERVAL_S", 0.0)
    history._last_fact_extraction.clear()

    history._schedule_fact_extraction("household")
    history._schedule_fact_extraction("default")
    history._schedule_fact_extraction("alice")
    await asyncio.sleep(0)

    assert created == ["alice"]


class _Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    async def json(self):
        return self._data


class _Client:
    def __init__(self, *, generate="NONE", existing=None):
        self.generate = generate
        self.existing = existing or []
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url, json=None, **kwargs):
        self.posts.append((url, json or {}))
        if url.endswith("/api/generate"):
            return _Resp({"response": self.generate})
        if url.endswith("/rag/search"):
            return _Resp({"results": self.existing})
        return _Resp({})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _settings(monkeypatch, *, model="librarian", ollama="http://ollama", rag="http://rag"):
    async def fake_settings():
        return {
            "ollama_librarian_model": model,
            "llm_local_url": ollama,
            "rag_svc_url": rag,
        }

    monkeypatch.setattr(orchestrator, "get_all_settings", fake_settings)


def _install_client(monkeypatch, client):
    import services.gateway.main as gateway_main

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: client)


_HISTORY = [
    {"role": "user", "content": "my dog is Rex"},
    {"role": "assistant", "content": "Nice to meet Rex."},
]


async def test_the_none_marker_is_an_exact_match(monkeypatch):
    _settings(monkeypatch)
    client = _Client(generate="NONE")
    _install_client(monkeypatch, client)

    await history.extract_and_store_user_facts("alice", _HISTORY)

    assert not any(url.endswith("/rag/ingest") for url, _ in client.posts)


async def test_a_fact_containing_none_is_not_dropped(monkeypatch):
    _settings(monkeypatch)
    client = _Client(generate="User has none of the paid tools installed.")
    _install_client(monkeypatch, client)

    await history.extract_and_store_user_facts("alice", _HISTORY)

    ingests = [body for url, body in client.posts if url.endswith("/rag/ingest")]
    assert len(ingests) == 1
    assert "none" in ingests[0]["content"]


async def test_existing_facts_are_not_ingested_twice(monkeypatch):
    _settings(monkeypatch)
    client = _Client(
        generate="User's dog is Rex",
        existing=[{"content": "User's dog is Rex"}],
    )
    _install_client(monkeypatch, client)

    await history.extract_and_store_user_facts("alice", _HISTORY)

    assert not any(url.endswith("/rag/ingest") for url, _ in client.posts)


async def test_explicit_facts_are_extracted_without_an_llm(monkeypatch):
    _settings(monkeypatch, model="", ollama="")
    client = _Client()
    _install_client(monkeypatch, client)

    await history.extract_and_store_user_facts("alice", _HISTORY)

    ingests = [body for url, body in client.posts if url.endswith("/rag/ingest")]
    assert [body["content"] for body in ingests] == ["User's dog is Rex"]
    assert ingests[0]["user_id"] == "alice"
    assert ingests[0]["collection_name"] == "user_facts"


@pytest.mark.parametrize("user", ["household", "default", "anonymous", ""])
async def test_household_never_receives_fact_extraction(monkeypatch, user):
    _settings(monkeypatch, model="")
    client = _Client()
    _install_client(monkeypatch, client)

    await history.extract_and_store_user_facts(user, _HISTORY)

    assert client.posts == []


async def test_recalled_facts_are_capped_for_the_prompt(monkeypatch):
    _settings(monkeypatch)
    long_fact = {"content": "x" * 1000}
    client = _Client(existing=[long_fact for _ in range(5)])
    _install_client(monkeypatch, client)

    memory = await history.get_long_term_memory("alice", "anything")

    assert memory
    assert len(memory) <= history.MAX_LONG_TERM_MEMORY_CHARS
