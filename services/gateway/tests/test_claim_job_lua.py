"""claim_job against the real Lua script (fakeredis runs it in a Lua VM).

The other queue tests use hand-written fakes with a Python twin of the
script; these tests make sure the script itself does what the twin does.
"""

import json

import fakeredis
import pytest

from services.gateway.messaging import InferenceJobQueue, JobStatus


@pytest.fixture
def queue():
    q = InferenceJobQueue("redis://fake")
    q._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return q


@pytest.mark.asyncio
async def test_claim_moves_the_job_marks_it_and_takes_the_lease_in_one_step(queue):
    job_id = await queue.enqueue_job("alice", {"query": "hello", "messages": []})
    job = await queue.claim_job()

    assert job is not None and job["job_id"] == job_id
    r = queue._redis
    assert await r.zrange(queue.QUEUE_KEY, 0, -1) == []
    assert await r.lrange(queue.PROCESSING_KEY, 0, -1) == [job_id]
    stored = json.loads(await r.get(f"{queue.JOB_PREFIX}{job_id}"))
    assert stored["status"] == JobStatus.PROCESSING
    assert stored["attempts"] == 1
    # An empty list in the payload stays a list (Redis's cjson would make it {}).
    assert stored["payload"]["messages"] == []
    assert await r.exists(f"{queue.LEASE_PREFIX}{job_id}")
    assert 0 < await r.ttl(f"{queue.LEASE_PREFIX}{job_id}") <= queue.LEASE_TTL_SECONDS


@pytest.mark.asyncio
async def test_a_record_changed_after_it_was_read_is_not_claimed(queue):
    job_id = await queue.enqueue_job("alice", {"query": "hello"})
    r = queue._redis
    original_get = r.get
    calls = {"n": 0}

    async def racing_get(key):
        raw = await original_get(key)
        if key == f"{queue.JOB_PREFIX}{job_id}" and calls["n"] == 0:
            calls["n"] += 1
            # Someone else rewrites the record between our read and the script.
            changed = json.loads(raw)
            changed["status"] = "cancelled-elsewhere"
            await r.set(key, json.dumps(changed))
        return raw

    r.get = racing_get
    job = await queue.claim_job()
    # The first attempt is refused, the retry reads the new record and claims it.
    assert job is not None
    assert job["status"] == JobStatus.PROCESSING
    assert await r.lrange(queue.PROCESSING_KEY, 0, -1) == [job_id]


@pytest.mark.asyncio
async def test_a_queued_id_without_a_record_is_dropped_not_claimed(queue):
    r = queue._redis
    await r.zadd(queue.QUEUE_KEY, {"ghost": 1.0})
    assert await queue.claim_job() is None
    assert await r.zrange(queue.QUEUE_KEY, 0, -1) == []
    assert await r.lrange(queue.PROCESSING_KEY, 0, -1) == []


@pytest.mark.asyncio
async def test_two_claimers_never_get_the_same_job(queue):
    import asyncio

    ids = [await queue.enqueue_job("alice", {"query": f"q{i}"}) for i in range(4)]
    other = InferenceJobQueue("redis://fake")
    other._redis = queue._redis
    other._queue_type_checked = True
    claimed = await asyncio.gather(*(q.claim_job() for q in (queue, other, queue, other)))
    got = [j["job_id"] for j in claimed if j]
    assert sorted(got) == sorted(ids)
    assert sorted(await queue._redis.lrange(queue.PROCESSING_KEY, 0, -1)) == sorted(ids)
