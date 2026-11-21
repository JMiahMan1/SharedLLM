import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
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

# Preemption state
_current_raven_mission: int | None = None
_preemption_lock = asyncio.Lock()


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

    async def enqueue_job(self, user_id: str, payload: dict[str, Any], priority: int = PRIORITY_NORMAL, tier: str = "tier2") -> str:
        """Adds a job to the priority queue."""
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

        # Get highest priority job (lowest score = highest priority)
        job_ids = await self._redis.zrange(self.QUEUE_KEY, 0, 0)  # type: ignore[misc]
        if not job_ids:
            return None

        job_id = job_ids[0]
        # Atomic move from queue to processing
        removed = await self._redis.zrem(self.QUEUE_KEY, job_id)  # type: ignore[misc]
        if not removed:
            return None

        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
        if not job_raw:
            return None

        job = json.loads(job_raw)
        job["status"] = JobStatus.PROCESSING
        job["started_at"] = time.time()
        job["attempts"] = int(job.get("attempts", 0)) + 1

        await self._redis.set(
            f"{self.JOB_PREFIX}{job_id}",
            json.dumps(job),
            ex=self.DEFAULT_TTL_SECONDS,
        )
        await self._redis.rpush(self.PROCESSING_KEY, job_id)  # type: ignore[misc]
        await self._publish_status(job_id, JobStatus.PROCESSING)
        await self.heartbeat_job(job_id)
        return job

    async def pop_job(self) -> dict[str, Any] | None:
        """Backward-compatible alias."""
        return await self.claim_job()

    async def heartbeat_job(self, job_id: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        await self._redis.set(f"{self.LEASE_PREFIX}{job_id}", str(time.time()), ex=self.LEASE_TTL_SECONDS)  # type: ignore[misc]

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
        log.info(f"Job {job_id} completed.")

    async def fail_job(self, job_id: str, error: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
        if not job_raw:
            return

        job = json.loads(job_raw)
        job["status"] = JobStatus.FAILED
        job["error"] = error
        job["completed_at"] = time.time()

        await self._redis.set(
            f"{self.JOB_PREFIX}{job_id}",
            json.dumps(job),
            ex=self.DEFAULT_TTL_SECONDS,
        )
        await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[misc]
        await self._publish_status(job_id, JobStatus.FAILED)
        await self._finalize_job(job_id)
        log.error(f"Job {job_id} failed: {error}")

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

    async def request_preemption(self, preempting_job_id: str, priority: int) -> bool:
        """
        Request preemption of current Raven mission.
        Returns True if preemption granted (mission paused).
        """
        async with _preemption_lock:
            global _current_raven_mission

            if _current_raven_mission is None:
                return False  # No Raven mission running

            if priority <= PRIORITY_NORMAL:
                return False  # Only high/critical priority can preempt

            # Signal the running mission to pause
            pause_key = f"raven:mission:pause:{_current_raven_mission}"
            try:
                r = redis.from_url(self.redis_url, decode_responses=True)
                await r.set(pause_key, "PREEMPTED", ex=3600)
                await r.publish(pause_key, "PREEMPTED")
                await r.close()
                log.warning(f"[Preemption] Mission {_current_raven_mission} preempted by job {preempting_job_id} (priority {PRIORITY_NAMES.get(priority, priority)})")
                return True
            except Exception as e:
                log.error(f"[Preemption] Failed to pause mission {_current_raven_mission}: {e}")
                return False

    async def register_raven_mission(
        self, mission_id: int, pause_event: asyncio.Event | None = None
    ):
        """Register the currently running Raven mission for preemption tracking.

        ``pause_event`` is accepted for callers that still pass one (preemption is
        signalled through Redis, not locally), but it is optional: the worker
        registers a mission with its id alone, and requiring an event there was a
        live TypeError that took the whole inference job down.
        """
        global _current_raven_mission
        async with _preemption_lock:
            _current_raven_mission = mission_id


    async def unregister_raven_mission(self, mission_id: int):
        """Unregister a completed/failed Raven mission."""
        global _current_raven_mission
        async with _preemption_lock:
            if _current_raven_mission == mission_id:
                _current_raven_mission = None

    async def get_current_raven_mission(self) -> int | None:
        """Get the currently running Raven mission ID."""
        global _current_raven_mission
        return _current_raven_mission


# Helper functions for priority inference
def infer_priority_from_payload(payload: dict[str, Any]) -> int:
    """
    Infer job priority from payload content.
    High priority: voice commands, home automation, quick queries
    Normal priority: standard chat, librarian queries
    Low priority: Raven missions (coding, building)
    """
    # Explicit priority in payload
    if "priority" in payload:
        return int(payload["priority"])

    # Raven missions are low priority (long-running)
    if payload.get("_mission_id"):
        return PRIORITY_LOW

    # Check for fast-path indicators
    query = str(payload.get("query", "")).lower()
    
    # Critical: SOS, emergency
    if any(kw in query for kw in ["sos", "emergency", "help me", "urgent"]):
        return PRIORITY_CRITICAL
    
    # High: voice commands, home automation, quick control
    high_priority_keywords = [
        "turn on", "turn off", "toggle", "set temperature", "lock", "unlock",
        "open garage", "close garage", "arm", "disarm", "pause", "resume",
        "play", "stop", "volume", "brightness", "thermostat", "lights",
        "what is the weather", "what time", "timer", "alarm"
    ]
    if any(kw in query for kw in high_priority_keywords):
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