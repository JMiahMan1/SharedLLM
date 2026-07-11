import asyncio
import json
import logging
import time
import uuid
from typing import Any

import redis.asyncio as redis

log = logging.getLogger("gateway.messaging")

# Tiered Inference Concurrency Control
# - TIER2_SEMAPHORE: Allows up to 3 concurrent non-autonomous (Librarian) jobs
# - TIER3_LOCK: Exclusive lock for autonomous (Raven) jobs (only one at a time)
TIER2_SEMAPHORE = asyncio.Semaphore(3)
TIER3_LOCK = asyncio.Lock()

class JobStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class InferenceJobQueue:
    """
    Strict FIFO Job Queue for Singleton Inference.
    Ensures only one LLM task is processed at a time.
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

    async def enqueue_job(self, user_id: str, payload: dict[str, Any]) -> str:
        """Adds a job to the FIFO queue."""
        if not self._redis:
            await self.connect()

        assert self._redis is not None

        job_id = str(uuid.uuid4())
        job_data = {
            "job_id": job_id,
            "user_id": user_id,
            "status": JobStatus.QUEUED,
            "payload": payload,
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

        # Push to FIFO queue (Right push) - rpush returns int but we await for consistency
        await self._redis.rpush(self.QUEUE_KEY, job_id)  # type: ignore[misc]

        log.info(f"Job {job_id} enqueued for user {user_id}")
        return job_id

    async def claim_job(self) -> dict[str, Any] | None:
        """
        Atomically claims the next queued job and places it into the processing list.
        The lease must be renewed while work is in progress.
        """
        if not self._redis:
            await self.connect()

        assert self._redis is not None

        job_id = await self._redis.lmove(self.QUEUE_KEY, self.PROCESSING_KEY, "LEFT", "RIGHT")  # type: ignore[misc]
        if not job_id:
            return None

        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[misc]
        if not job_raw:
            await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[misc]
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
        await self._publish_status(job_id, JobStatus.PROCESSING)
        await self.heartbeat_job(job_id)
        return job

    async def pop_job(self) -> dict[str, Any] | None:
        """Backward-compatible alias for older callers."""
        return await self.claim_job()

    async def heartbeat_job(self, job_id: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        await self._redis.set(f"{self.LEASE_PREFIX}{job_id}", str(time.time()), ex=self.LEASE_TTL_SECONDS)  # type: ignore[misc]

    async def complete_job(self, job_id: str, result: Any):
        """Marks a job as completed and stores the result."""
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
        await self._publish_status(job_id, JobStatus.COMPLETED)
        await self._finalize_job(job_id)
        log.info(f"Job {job_id} completed.")

    async def fail_job(self, job_id: str, error: str):
        """Marks a job as failed."""
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
        await self._publish_status(job_id, JobStatus.FAILED)
        await self._finalize_job(job_id)
        log.error(f"Job {job_id} failed: {error}")

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Retrieves the current status and result of a job."""
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

        queue = await self._redis.lrange(self.QUEUE_KEY, 0, -1)  # type: ignore[misc]
        try:
            return queue.index(job_id)
        except ValueError:
            return -1

    async def push_chunk(self, job_id: str, chunk: str):
        """Pushes a message chunk to a job-specific Redis list."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        key = f"raven:job:chunks:{job_id}"
        await self._redis.rpush(key, chunk)  # type: ignore[misc]
        await self._redis.expire(key, 600) # 10 minute TTL for chunks  # type: ignore[misc]

    async def get_chunks(self, job_id: str) -> list[str]:
        """Pops all available chunks for a job."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        key = f"raven:job:chunks:{job_id}"
        # Atomic pop all
        chunks = await self._redis.lrange(key, 0, -1)  # type: ignore[misc]
        await self._redis.ltrim(key, len(chunks), -1)  # type: ignore[misc]
        return chunks

    async def close(self):
        if self._redis:
            await self._redis.close()

    async def reclaim_expired_jobs(self) -> int:
        """
        Requeue jobs whose worker lease expired.
        After MAX_ATTEMPTS, move them to the dead-letter list and mark failed.
        """
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
            await self._redis.rpush(self.QUEUE_KEY, job_id)  # type: ignore[misc]
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
        """Return job_ids whose payload carries ``_mission_id == mission_id``.

        Scans both the pending queue and the in-progress list so a mission can be
        located no matter which stage its job is in.
        """
        if not self._redis:
            await self.connect()
        assert self._redis is not None

        target = str(mission_id)
        job_ids: list[str] = []
        for key in (self.QUEUE_KEY, self.PROCESSING_KEY):
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
        """Remove every queued/in-progress job for a mission and its metadata/lease.

        Returns the number of jobs dropped. Used when cancelling a mission so the
        singleton worker can never claim a job whose mission no longer exists.
        """
        if not self._redis:
            await self.connect()
        assert self._redis is not None

        dropped = 0
        for jid in await self.find_jobs_for_mission(mission_id):
            await self._redis.lrem(self.QUEUE_KEY, 1, jid)  # type: ignore[misc]
            await self._redis.lrem(self.PROCESSING_KEY, 1, jid)  # type: ignore[misc]
            await self._redis.delete(f"{self.JOB_PREFIX}{jid}")  # type: ignore[misc]
            await self._redis.delete(f"{self.LEASE_PREFIX}{jid}")  # type: ignore[misc]
            dropped += 1
        return dropped

    async def _publish_status(self, job_id: str, status: str):
        """Notify SSE subscribers that a job's status changed.

        Best-effort: failures here must never break job processing. The SSE
        status stream also falls back to a periodic GET, so a missed publish is
        self-healing.
        """
        if not self._redis:
            return
        try:
            await self._redis.publish(f"{self.JOB_PREFIX}status:{job_id}", status)
        except Exception as e:
            log.warning(f"Failed to publish status for job {job_id}: {e}")
