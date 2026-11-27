import asyncio
import json
import logging
import re
import threading
import time
import uuid
from typing import Any

import redis.asyncio as redis

log = logging.getLogger("gateway.messaging")

# Priority levels (higher = more urgent)
PRIORITY_LOW = 0      # Background Raven missions (coding, building)
PRIORITY_NORMAL = 50  # Standard chat, librarian queries
PRIORITY_HIGH = 100   # Voice commands, home automation, quick queries
PRIORITY_CRITICAL = 200  # Emergency (SOS, security alerts)

# Priority names for logging
PRIORITY_NAMES = {
    PRIORITY_LOW: "LOW",
    PRIORITY_NORMAL: "NORMAL",
    PRIORITY_HIGH: "HIGH",
    PRIORITY_CRITICAL: "CRITICAL",
}

# Tiered Inference Concurrency Control
# - TIER2_SEMAPHORE: Allows up to 3 concurrent non-autonomous (Librarian) jobs
# - TIER3_LOCK: Exclusive lock for autonomous (Raven) jobs (only one at a time)
# The worker's dispatcher claims jobs sequentially and runs each claimed job in
# its own task, so these primitives actually gate concurrent inference.
TIER2_SEMAPHORE = asyncio.Semaphore(3)
TIER3_LOCK = asyncio.Lock()

# Preemption state (see InferenceJobQueue.register_raven_mission). Keyed by the
# mission id as a string; the value is the mission's pause event.
_raven_missions: dict[str, asyncio.Event] = {}
#: Job ids currently holding each mission paused.
_preemptors: dict[str, set[str]] = {}
_registry_lock = threading.Lock()

#: What preemption writes to ``raven:mission:pause:<id>`` (a user pause writes PAUSED).
PREEMPTED = "PREEMPTED"
#: Backstop for a gateway that dies mid-preemption: the flag then lapses.
PREEMPTION_FLAG_TTL_SECONDS = 3600
#: Delete KEYS[1] only while it still holds ARGV[1].
_DELETE_IF_EQUALS = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
)


#: claim_job's script results.
CLAIM_OK = 1
CLAIM_RACED = 0
CLAIM_ORPHAN = -1
#: Move a queued job to processing in one step.
#: KEYS: queue zset, processing list, job record, lease.
#: ARGV: job id, record as read ("" if missing), new record, record ttl,
#:       lease value, lease ttl.
_CLAIM_JOB = """
if not redis.call('zscore', KEYS[1], ARGV[1]) then return 0 end
local current = redis.call('get', KEYS[3])
if not current then
  if ARGV[2] == '' then
    redis.call('zrem', KEYS[1], ARGV[1])
    return -1
  end
  return 0
end
if current ~= ARGV[2] then return 0 end
redis.call('zrem', KEYS[1], ARGV[1])
redis.call('set', KEYS[3], ARGV[3], 'EX', tonumber(ARGV[4]))
redis.call('set', KEYS[4], ARGV[5], 'EX', tonumber(ARGV[6]))
redis.call('rpush', KEYS[2], ARGV[1])
return 1
"""


def _count_outcome(job: dict[str, Any], *, failed: bool, error: str = "", detail: dict[str, Any] | None = None) -> None:
    """Count how a request to Jarvis went (voice_outcomes, in memory only). A job
    that was never a person's request (a mission) carries no record and is skipped."""
    try:
        from services.gateway import voice_outcomes

        voice_outcomes.finish_job(job, failed=failed, error=error, detail=detail)
    except Exception as exc:  # counting must never fail a job
        log.warning(f"[voice-outcomes] could not count job {job.get('job_id')}: {exc}")


class JobStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class InferenceJobQueue:
    """
    Priority-aware Job Queue for inference work.
    Supports preemption: high-priority jobs can pause lower-priority Raven missions.
    """
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis: redis.Redis | None = None
        self.QUEUE_KEY = "raven:inference_queue"
        self.PROCESSING_KEY = "raven:inference_processing"
        self.DEAD_LETTER_KEY = "raven:inference_dead_letter"
        self.JOB_PREFIX = "raven:job:"
        self.LEASE_PREFIX = "raven:lease:"
        self.DEFAULT_TTL_SECONDS = 3600
        self.LEASE_TTL_SECONDS = 120
        self.MAX_ATTEMPTS = 3
        self.CLAIM_RETRIES = 5
        self._queue_type_checked = False

    async def connect(self):
        if not self._redis:
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
            log.info(f"Connected to Redis at {self.redis_url} for job queuing.")

    async def claim_loop(self, name: str, token: str, ttl_seconds: int) -> bool:
        """Whether this worker may run the ``name`` loop right now."""
        if not self._redis:
            try:
                await self.connect()
            except Exception as e:
                log.warning(f"[RavenWorker] No Redis for the {name} lease: {e}")
                return True

        assert self._redis is not None

        key = f"raven:loop:{name}"
        try:
            if await self._redis.set(key, token, nx=True, ex=ttl_seconds):  # type: ignore[misc]
                return True
            holder = await self._redis.get(key)  # type: ignore[misc]
            if holder == token:
                await self._redis.expire(key, ttl_seconds)  # type: ignore[misc]
                return True
            return False
        except Exception as e:
            log.warning(f"[RavenWorker] Could not check the {name} lease: {e}")
            return True

    async def enqueue_job(
        self,
        user_id: str,
        payload: dict[str, Any],
        priority: int | None = None,
        tier: str | None = None,
    ) -> str:
        """Adds a job to the priority queue.

        ``priority`` and ``tier`` default to what the payload implies
        (``infer_priority_from_payload``): no caller passes them, and a fixed
        NORMAL default made every job equal, so nothing could ever preempt.
        """
        if priority is None:
            priority = infer_priority_from_payload(payload)
        if tier is None:
            tier = infer_tier_from_payload(payload)
        if not self._redis:
            await self.connect()

        assert self._redis is not None

        job_id = str(uuid.uuid4())
        job_data = {
            "job_id": job_id,
            "user_id": user_id,
            "status": JobStatus.QUEUED,
            "payload": payload,
            "priority": priority,
            "tier": tier,
            "created_at": time.time(),
            "attempts": 0,
            "result": None,
            "error": None
        }

        # Store job metadata
        await self._redis.set(
            f"{self.JOB_PREFIX}{job_id}",
            json.dumps(job_data),
            ex=self.DEFAULT_TTL_SECONDS,
        )

        # Push to priority queue (sorted set by -priority, then created_at)
        score = -priority * 1_000_000_000 + time.time()
        await self._redis.zadd(self.QUEUE_KEY, {job_id: score})  # type: ignore[misc]

        log.info(f"Job {job_id} enqueued for user {user_id} with priority {PRIORITY_NAMES.get(priority, priority)} (tier: {tier})")
        return job_id

    async def claim_job(self) -> dict[str, Any] | None:
        """
        Atomically claims the highest-priority queued job.
        """
        if not self._redis:
            await self.connect()

        assert self._redis is not None
        if not self._queue_type_checked:
            await self._migrate_fifo_queue()

        # One claim is one script: leave the queue, mark the record, take the
        # lease and join the processing list together. Done as separate calls,
        # a gateway that died between ZREM and RPUSH lost the job outright.
        # The record is rewritten here in Python (Redis's cjson turns an empty
        # list in a payload into {}), so the script only commits it when the
        # record is still the one we read; a racing claimer makes it retry.
        for _ in range(self.CLAIM_RETRIES):
            job_ids = await self._redis.zrange(self.QUEUE_KEY, 0, 0)  # type: ignore[misc]
            if not job_ids:
                return None
            job_id = job_ids[0]
            job_key = f"{self.JOB_PREFIX}{job_id}"
            job_raw = await self._redis.get(job_key)  # type: ignore[misc]
            if job_raw:
                job = json.loads(job_raw)
                job["status"] = JobStatus.PROCESSING
                job["started_at"] = time.time()
                job["attempts"] = int(job.get("attempts", 0)) + 1
                new_raw = json.dumps(job)
            else:
                job, new_raw = None, ""
            outcome = await self._redis.eval(  # type: ignore[misc]
                _CLAIM_JOB,
                4,
                self.QUEUE_KEY,
                self.PROCESSING_KEY,
                job_key,
                f"{self.LEASE_PREFIX}{job_id}",
                job_id,
                job_raw or "",
                new_raw,
                self.DEFAULT_TTL_SECONDS,
                str(time.time()),
                self.LEASE_TTL_SECONDS,
            )
            outcome = int(outcome)
            if outcome == CLAIM_ORPHAN:
                log.warning("Dropped queued job %s: its record had expired.", job_id)
                continue
            if outcome == CLAIM_RACED:
                continue  # another worker claimed or changed it first
            if outcome != CLAIM_OK or job is None:
                raise RuntimeError(f"claim script returned {outcome} for job {job_id}")
            await self._publish_status(job_id, JobStatus.PROCESSING)
            return job
        log.warning("Could not claim a job after %s attempts (contention).", self.CLAIM_RETRIES)
        return None

    async def _migrate_fifo_queue(self) -> None:
        """Move jobs left in the old FIFO list into the priority set.

        The queue used to be a Redis list under the same key. A gateway
        upgraded while jobs were queued would otherwise hit WRONGTYPE on every
        claim and never run anything again.
        """
        assert self._redis is not None
        try:
            if await self._redis.type(self.QUEUE_KEY) != "list":  # type: ignore[misc]
                self._queue_type_checked = True
                return
            job_ids = await self._redis.lrange(self.QUEUE_KEY, 0, -1)  # type: ignore[misc]
            await self._redis.delete(self.QUEUE_KEY)  # type: ignore[misc]
            for job_id in job_ids:
                raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
                job = json.loads(raw) if raw else {}
                priority = job.get("priority", infer_priority_from_payload(job.get("payload") or {}))
                score = -priority * 1_000_000_000 + job.get("created_at", time.time())
                await self._redis.zadd(self.QUEUE_KEY, {job_id: score})  # type: ignore[misc]
            log.warning(f"Moved {len(job_ids)} job(s) from the old FIFO queue into the priority queue.")
            self._queue_type_checked = True
        except Exception as e:
            log.warning(f"Could not check the job queue's type: {e}")
            self._queue_type_checked = True

    async def pop_job(self) -> dict[str, Any] | None:
        """Backward-compatible alias."""
        return await self.claim_job()

    async def heartbeat_job(self, job_id: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        await self._redis.set(f"{self.LEASE_PREFIX}{job_id}", str(time.time()), ex=self.LEASE_TTL_SECONDS)  # type: ignore[misc]
        # Keep the job record alive as long as the job is: a mission may run
        # two hours, and a record that expired after one made the orphan
        # reaper fail the mission while it was still running.
        await self._redis.expire(f"{self.JOB_PREFIX}{job_id}", self.DEFAULT_TTL_SECONDS)  # type: ignore[misc]

    async def complete_job(self, job_id: str, result: Any):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
        if not job_raw:
            return

        job = json.loads(job_raw)
        job["status"] = JobStatus.COMPLETED
        job["result"] = result
        job["completed_at"] = time.time()

        await self._redis.set(
            f"{self.JOB_PREFIX}{job_id}",
            json.dumps(job),
            ex=self.DEFAULT_TTL_SECONDS,
        )
        await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[misc]
        await self._publish_status(job_id, JobStatus.COMPLETED)
        await self._finalize_job(job_id)
        _count_outcome(job, failed=False)
        log.info(f"Job {job_id} completed.")

    async def fail_job(self, job_id: str, error: str, detail: dict[str, Any] | None = None):
        """Mark a job failed. ``error`` is what the person is shown; ``detail``
        (``code``, ``error_id``, ``http_status``, ``offline``) tells the chat
        handler which status and fields to answer with."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
        if not job_raw:
            return

        job = json.loads(job_raw)
        job["status"] = JobStatus.FAILED
        job["error"] = error
        if detail:
            job["error_detail"] = detail
        job["completed_at"] = time.time()

        await self._redis.set(
            f"{self.JOB_PREFIX}{job_id}",
            json.dumps(job),
            ex=self.DEFAULT_TTL_SECONDS,
        )
        await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[misc]
        await self._publish_status(job_id, JobStatus.FAILED)
        await self._finalize_job(job_id)
        _count_outcome(job, failed=True, error=error, detail=detail)
        log.error(f"Job {job_id} failed: {error}")

    async def peek_job(self) -> dict[str, Any] | None:
        """The job ``claim_job`` would take next, without taking it (None when empty)."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        if not self._queue_type_checked:
            await self._migrate_fifo_queue()
        for job_id in await self._redis.zrange(self.QUEUE_KEY, 0, 4):  # type: ignore[misc]
            raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
            if raw:
                return json.loads(raw)
        return None

    async def requeue_job(self, job_id: str, note: str = "") -> bool:
        """Put a claimed job back in the queue as it was, without counting the attempt.

        For work that could not start for a reason that is not its own (the local
        AI is offline): it keeps its priority and place, and its lease and record
        are refreshed so it does not expire while it waits.
        """
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        key = f"{self.JOB_PREFIX}{job_id}"
        raw = await self._redis.get(key)  # type: ignore[misc]
        if not raw:
            return False
        job = json.loads(raw)
        job["status"] = JobStatus.QUEUED
        job["attempts"] = max(0, int(job.get("attempts", 0)) - 1)
        job.pop("started_at", None)
        if note:
            job["waiting"] = note
        await self._redis.set(key, json.dumps(job), ex=self.DEFAULT_TTL_SECONDS)  # type: ignore[misc]
        await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[misc]
        await self._redis.delete(f"{self.LEASE_PREFIX}{job_id}")  # type: ignore[misc]
        score = -int(job.get("priority", PRIORITY_NORMAL)) * 1_000_000_000 + float(job.get("created_at", time.time()))
        await self._redis.zadd(self.QUEUE_KEY, {job_id: score})  # type: ignore[misc]
        await self._publish_status(job_id, JobStatus.QUEUED)
        log.warning("Job %s put back in the queue: %s", job_id, note or "it could not start")
        return True

    async def touch_queued_jobs(self) -> int:
        """Keep every queued job's record alive (an outage can outlast its TTL).

        A record that expired while it waited was dropped at claim time as an
        orphan, and the mission reaper then failed its mission: work queued
        before a long outage was lost. Returns how many records were refreshed.
        """
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        touched = 0
        for job_id in await self._redis.zrange(self.QUEUE_KEY, 0, -1):  # type: ignore[misc]
            if await self._redis.expire(f"{self.JOB_PREFIX}{job_id}", self.DEFAULT_TTL_SECONDS):  # type: ignore[misc]
                touched += 1
        return touched

    async def queued_count(self) -> int:
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        return int(await self._redis.zcard(self.QUEUE_KEY))  # type: ignore[misc]

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
        return json.loads(job_raw) if job_raw else None

    async def get_queue_position(self, job_id: str) -> int:
        """Returns the 0-indexed position of a job in the queue."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        queue = await self._redis.zrange(self.QUEUE_KEY, 0, -1)  # type: ignore[misc]
        try:
            return queue.index(job_id)
        except ValueError:
            return -1

    async def push_chunk(self, job_id: str, chunk: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        key = f"raven:job:chunks:{job_id}"
        await self._redis.rpush(key, chunk)  # type: ignore[misc]
        await self._redis.expire(key, 600)  # type: ignore[misc]

    async def get_chunks(self, job_id: str) -> list[str]:
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        key = f"raven:job:chunks:{job_id}"
        chunks = await self._redis.lrange(key, 0, -1)  # type: ignore[misc]
        await self._redis.ltrim(key, len(chunks), -1)  # type: ignore[misc]
        return chunks

    async def close(self):
        if self._redis:
            await self._redis.close()

    async def reclaim_expired_jobs(self) -> int:
        if not self._redis:
            await self.connect()
        assert self._redis is not None

        reclaimed = 0
        processing_jobs = await self._redis.lrange(self.PROCESSING_KEY, 0, -1)  # type: ignore[misc]
        for job_id in processing_jobs:
            lease_exists = await self._redis.exists(f"{self.LEASE_PREFIX}{job_id}")  # type: ignore[misc]
            if lease_exists:
                continue

            job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
            if not job_raw:
                await self._finalize_job(job_id)
                continue

            job = json.loads(job_raw)
            if job.get("status") != JobStatus.PROCESSING:
                await self._finalize_job(job_id)
                continue

            attempts = int(job.get("attempts", 0))
            await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[misc]

            if attempts >= self.MAX_ATTEMPTS:
                job["status"] = JobStatus.FAILED
                job["error"] = "Job lease expired too many times."
                job["completed_at"] = time.time()
                await self._redis.set(
                    f"{self.JOB_PREFIX}{job_id}",
                    json.dumps(job),
                    ex=self.DEFAULT_TTL_SECONDS,
                )
                await self._publish_status(job_id, JobStatus.FAILED)
                await self._redis.rpush(self.DEAD_LETTER_KEY, job_id)  # type: ignore[misc]
                log.error("Job %s moved to dead-letter queue after %s expired attempts", job_id, attempts)
                continue

            job["status"] = JobStatus.QUEUED
            await self._redis.set(
                f"{self.JOB_PREFIX}{job_id}",
                json.dumps(job),
                ex=self.DEFAULT_TTL_SECONDS,
            )
            await self._publish_status(job_id, JobStatus.QUEUED)
            # Re-add to priority queue
            score = -job.get("priority", PRIORITY_NORMAL) * 1_000_000_000 + job.get("created_at", time.time())
            await self._redis.zadd(self.QUEUE_KEY, {job_id: score})  # type: ignore[misc]
            reclaimed += 1
            log.warning("Re-queued expired job %s after lease loss (attempt %s)", job_id, attempts)

        return reclaimed

    async def _finalize_job(self, job_id: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[misc]
        await self._redis.delete(f"{self.LEASE_PREFIX}{job_id}")  # type: ignore[misc]

    async def find_jobs_for_mission(self, mission_id: str | int) -> list[str]:
        if not self._redis:
            await self.connect()
        assert self._redis is not None

        target = str(mission_id)
        job_ids: list[str] = []
        for key in (self.QUEUE_KEY, self.PROCESSING_KEY):
            if key == self.QUEUE_KEY:
                items = await self._redis.zrange(key, 0, -1)  # type: ignore[misc]
            else:
                items = await self._redis.lrange(key, 0, -1)  # type: ignore[misc]
            for jid in items:
                raw = await self._redis.get(f"{self.JOB_PREFIX}{jid}")  # type: ignore[misc]
                if not raw:
                    continue
                try:
                    job = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                payload = job.get("payload") or {}
                if str(payload.get("_mission_id")) == target:
                    job_ids.append(jid)
        return job_ids

    async def drop_jobs_for_mission(self, mission_id: str | int) -> int:
        if not self._redis:
            await self.connect()
        assert self._redis is not None

        dropped = 0
        for jid in await self.find_jobs_for_mission(mission_id):
            await self._redis.zrem(self.QUEUE_KEY, jid)  # type: ignore[misc]
            await self._redis.lrem(self.PROCESSING_KEY, 1, jid)  # type: ignore[misc]
            await self._redis.delete(f"{self.JOB_PREFIX}{jid}")  # type: ignore[misc]
            await self._redis.delete(f"{self.LEASE_PREFIX}{jid}")  # type: ignore[misc]
            dropped += 1
        return dropped

    async def _publish_status(self, job_id: str, status: str):
        if not self._redis:
            return
        try:
            await self._redis.publish(f"{self.JOB_PREFIX}status:{job_id}", status)
        except Exception as e:
            log.warning(f"Failed to publish status for job {job_id}: {e}")

    # --- Preemption Support ---
    #
    # One Raven mission runs at a time (TIER3_LOCK) and can hold the model for
    # hours. A HIGH or CRITICAL Tier 2 job (a voice command, a light switch)
    # pauses it at its next checkpoint and lets it go on once the job is done:
    #
    # 1. The worker creates an ``asyncio.Event`` per mission and registers it
    #    here once it holds TIER3_LOCK (``register_raven_mission``).
    # 2. ``request_preemption`` sets that event (the in-process signal the agent
    #    loop reads at every checkpoint, see ``mission_pause_event``) and writes
    #    ``PREEMPTED`` to ``raven:mission:pause:<id>`` for any other reader.
    # 3. ``release_preemption`` runs when the preempting job ends; once no
    #    preempting job is left it clears the event and removes the Redis key,
    #    but only if it still says ``PREEMPTED``: a user's own pause (``PAUSED``)
    #    is theirs to lift.
    #
    # The registry is per process and every call comes from the worker's event
    # loop; the threading lock only guards the dicts, never an await.

    async def register_raven_mission(self, mission_id: int | str, pause_event: asyncio.Event | None = None) -> None:
        """Track the running Raven mission so a high-priority job can pause it.

        ``pause_event`` is owned by the mission: set means "pause at the next
        checkpoint". Call this only while holding TIER3_LOCK, so the mission
        registered is the one actually running and not one waiting its turn.
        """
        if pause_event is None:
            # A caller with no event of its own still gets one, so preemption can pause it.
            pause_event = asyncio.Event()
        key = str(mission_id)
        with _registry_lock:
            _raven_missions[key] = pause_event
            preemptors = _preemptors.setdefault(key, set())
            # A preemption that arrived for an earlier run of a reused id ended
            # with that run; never start a fresh mission paused.
            preemptors.clear()
        pause_event.clear()

    async def unregister_raven_mission(self, mission_id: int | str) -> None:
        """Forget a finished mission and lift any preemption still pending on it."""
        key = str(mission_id)
        with _registry_lock:
            event = _raven_missions.pop(key, None)
            had_preemptors = bool(_preemptors.pop(key, None))
        if event is not None:
            event.clear()
        if had_preemptors:
            await self._clear_preempted_flag(key)

    async def get_current_raven_mission(self) -> str | None:
        """The id of the Raven mission running in this process, if any."""
        with _registry_lock:
            return next(iter(_raven_missions), None)

    async def request_preemption(self, preempting_job_id: str, priority: int) -> str | None:
        """Pause the running Raven mission for a HIGH/CRITICAL job.

        Returns the paused mission's id, or None when there was nothing to
        pause or the job is not urgent enough. Every successful call must be
        matched by ``release_preemption(preempting_job_id)``.
        """
        if priority <= PRIORITY_NORMAL:
            return None
        with _registry_lock:
            if not _raven_missions:
                return None
            mission_id, event = next(iter(_raven_missions.items()))
            _preemptors.setdefault(mission_id, set()).add(preempting_job_id)
        event.set()

        pause_key = f"raven:mission:pause:{mission_id}"
        try:
            if not self._redis:
                await self.connect()
            assert self._redis is not None
            # NX: never overwrite a user's own PAUSED with PREEMPTED, or the
            # release would resume a mission the user meant to keep paused.
            await self._redis.set(pause_key, PREEMPTED, ex=PREEMPTION_FLAG_TTL_SECONDS, nx=True)  # type: ignore[misc]
            await self._redis.publish(pause_key, PREEMPTED)
        except Exception as e:
            # The in-process event already pauses the mission; the Redis flag
            # only informs other readers.
            log.warning(f"[Preemption] Could not write the pause flag of mission {mission_id}: {e}")
        log.warning(
            f"[Preemption] Mission {mission_id} paused for job {preempting_job_id} "
            f"(priority {PRIORITY_NAMES.get(priority, priority)})"
        )
        return mission_id

    async def release_preemption(self, preempting_job_id: str) -> str | None:
        """Undo ``request_preemption`` for one job; resume the mission when no
        preempting job is left. Returns the resumed mission's id, if any."""
        resumed: str | None = None
        with _registry_lock:
            for mission_id, jobs in _preemptors.items():
                if preempting_job_id in jobs:
                    jobs.discard(preempting_job_id)
                    if not jobs:
                        resumed = mission_id
                    else:
                        return None
                    break
            event = _raven_missions.get(resumed) if resumed else None
        if resumed is None:
            return None
        if event is not None:
            event.clear()
        await self._clear_preempted_flag(resumed)
        log.info(f"[Preemption] Mission {resumed} resumed after job {preempting_job_id}")
        return resumed

    async def _clear_preempted_flag(self, mission_id: str) -> None:
        """Remove the pause flag only if preemption wrote it, then say RESUMED."""
        pause_key = f"raven:mission:pause:{mission_id}"
        try:
            if not self._redis:
                await self.connect()
            assert self._redis is not None
            removed = await self._redis.eval(_DELETE_IF_EQUALS, 1, pause_key, PREEMPTED)  # type: ignore[misc]
            if removed:
                await self._redis.publish(pause_key, "RESUMED")
        except Exception as e:
            log.warning(f"[Preemption] Could not clear the pause flag of mission {mission_id}: {e}")


def mission_pause_event(mission_id: int | str | None) -> asyncio.Event | None:
    """The pause event of a mission running in this process (None otherwise).

    The agent loop reads it at every checkpoint alongside the Redis pause flag.
    """
    if mission_id is None:
        return None
    with _registry_lock:
        return _raven_missions.get(str(mission_id))


# Helper functions for priority inference

#: Words that make a request CRITICAL. Whole words only: a substring test made
#: "help me write an essay" an emergency and "SOS" match inside other words.
_CRITICAL_RE = re.compile(r"\b(?:sos|emergency|urgent)\b")
#: Household control and quick questions; whole words for the same reason
#: ("lock" in "block", "arm" in "warm", "play" in "display").
_HIGH_RE = re.compile(
    r"\b(?:turn on|turn off|toggle|set (?:the )?temperature|lock|unlock|open (?:the )?garage|"
    r"close (?:the )?garage|arm|disarm|pause|resume|play|stop|volume|brightness|thermostat|"
    r"lights?|what is the weather|what time|timer|alarm)\b"
)


def infer_priority_from_payload(payload: dict[str, Any]) -> int:
    """
    Infer job priority from payload content.
    High priority: voice commands, home automation, quick queries
    Normal priority: standard chat, librarian queries
    Low priority: Raven missions (coding, building)
    """
    # Explicit priority in payload
    if "priority" in payload:
        try:
            return int(payload["priority"])
        except (TypeError, ValueError):
            log.warning(f"Ignoring a job priority that is not a number: {payload['priority']!r}")

    # Raven missions and background work are low priority so a user's question
    # always sorts ahead of deferred work (including while the local AI is offline).
    if payload.get("_mission_id") or payload.get("background"):
        return PRIORITY_LOW

    query = str(payload.get("query", "")).lower()

    if _CRITICAL_RE.search(query):
        return PRIORITY_CRITICAL

    if _HIGH_RE.search(query):
        return PRIORITY_HIGH

    # High: voice context (short queries from voice satellites)
    if payload.get("voice_context"):
        return PRIORITY_HIGH

    # Normal: standard chat, librarian, questions
    return PRIORITY_NORMAL


def infer_tier_from_payload(payload: dict[str, Any]) -> str:
    """Infer which tier (tier2 or tier3) based on payload."""
    # Raven missions go to tier3
    if payload.get("_mission_id"):
        return "tier3"

    # Autonomous/Raven intent goes to tier3
    query = str(payload.get("query", "")).lower()
    coding_keywords = [
        "raven", "build a", "create a", "fix the", "write code", "implement",
        "write a python", "write a script", "write a function", "create a script",
        "build a", "make a", "develop a", "code a", "program a",
        "scrape", "crawler", "spider", "api wrapper", "backend", "frontend"
    ]
    if any(kw in query for kw in coding_keywords):
        return "tier3"

    # Everything else goes to tier2 (Librarian)
    return "tier2"
