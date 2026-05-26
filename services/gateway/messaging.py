import asyncio
import json
import uuid
import logging
import time
from typing import Any, Dict, Optional, List
import redis.asyncio as redis  # noqa: E402

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
        self._redis: Optional[redis.Redis] = None
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

    async def enqueue_job(self, user_id: str, payload: Dict[str, Any]) -> str:
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
        
        # Push to FIFO queue (Right push)
        await self._redis.rpush(self.QUEUE_KEY, job_id)  # type: ignore[reportGeneralTypeIssues]
        
        log.info(f"Job {job_id} enqueued for user {user_id}")
        return job_id

    async def claim_job(self) -> Optional[Dict[str, Any]]:
        """
        Atomically claims the next queued job and places it into the processing list.
        The lease must be renewed while work is in progress.
        """
        if not self._redis:
            await self.connect()

        assert self._redis is not None

        job_id = await self._redis.lmove(self.QUEUE_KEY, self.PROCESSING_KEY, "LEFT", "RIGHT")  # type: ignore[reportGeneralTypeIssues]
        if not job_id:
            return None

        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[reportGeneralTypeIssues]
        if not job_raw:
            await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[reportGeneralTypeIssues]
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
        await self.heartbeat_job(job_id)
        return job

    async def pop_job(self) -> Optional[Dict[str, Any]]:
        """Backward-compatible alias for older callers."""
        return await self.claim_job()

    async def heartbeat_job(self, job_id: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        await self._redis.set(f"{self.LEASE_PREFIX}{job_id}", str(time.time()), ex=self.LEASE_TTL_SECONDS)  # type: ignore[reportGeneralTypeIssues]

    async def complete_job(self, job_id: str, result: Any):
        """Marks a job as completed and stores the result."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[reportGeneralTypeIssues]
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
        await self._finalize_job(job_id)
        log.info(f"Job {job_id} completed.")

    async def fail_job(self, job_id: str, error: str):
        """Marks a job as failed."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[reportGeneralTypeIssues]
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
        await self._finalize_job(job_id)
        log.error(f"Job {job_id} failed: {error}")

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the current status and result of a job."""
        if not self._redis:
            await self.connect()

        assert self._redis is not None

        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[reportGeneralTypeIssues]
        return json.loads(job_raw) if job_raw else None

    async def get_queue_position(self, job_id: str) -> int:
        """Returns the 0-indexed position of a job in the queue."""
        if not self._redis:
            await self.connect()

        assert self._redis is not None

        queue = await self._redis.lrange(self.QUEUE_KEY, 0, -1)  # type: ignore[reportGeneralTypeIssues]
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
        await self._redis.rpush(key, chunk)  # type: ignore[reportGeneralTypeIssues]
        await self._redis.expire(key, 600) # 10 minute TTL for chunks  # type: ignore[reportGeneralTypeIssues]

    async def get_chunks(self, job_id: str) -> List[str]:
        """Pops all available chunks for a job."""
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        key = f"raven:job:chunks:{job_id}"
        # Atomic pop all
        chunks = await self._redis.lrange(key, 0, -1)  # type: ignore[reportGeneralTypeIssues]
        await self._redis.ltrim(key, len(chunks), -1)  # type: ignore[reportGeneralTypeIssues]
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
        processing_jobs = await self._redis.lrange(self.PROCESSING_KEY, 0, -1)  # type: ignore[reportGeneralTypeIssues]
        for job_id in processing_jobs:
            lease_exists = await self._redis.exists(f"{self.LEASE_PREFIX}{job_id}")  # type: ignore[reportGeneralTypeIssues]
            if lease_exists:
                continue

            job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")  # type: ignore[reportGeneralTypeIssues]
            if not job_raw:
                await self._finalize_job(job_id)
                continue

            job = json.loads(job_raw)
            if job.get("status") != JobStatus.PROCESSING:
                await self._finalize_job(job_id)
                continue

            attempts = int(job.get("attempts", 0))
            await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[reportGeneralTypeIssues]

            if attempts >= self.MAX_ATTEMPTS:
                job["status"] = JobStatus.FAILED
                job["error"] = "Job lease expired too many times."
                job["completed_at"] = time.time()
                await self._redis.set(
                    f"{self.JOB_PREFIX}{job_id}",
                    json.dumps(job),
                    ex=self.DEFAULT_TTL_SECONDS,
                )
                await self._redis.rpush(self.DEAD_LETTER_KEY, job_id)  # type: ignore[reportGeneralTypeIssues]
                log.error("Job %s moved to dead-letter queue after %s expired attempts", job_id, attempts)
                continue

            job["status"] = JobStatus.QUEUED
            await self._redis.set(
                f"{self.JOB_PREFIX}{job_id}",
                json.dumps(job),
                ex=self.DEFAULT_TTL_SECONDS,
            )
            await self._redis.rpush(self.QUEUE_KEY, job_id)  # type: ignore[reportGeneralTypeIssues]
            reclaimed += 1
            log.warning("Re-queued expired job %s after lease loss (attempt %s)", job_id, attempts)

        return reclaimed

    async def _finalize_job(self, job_id: str):
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        await self._redis.lrem(self.PROCESSING_KEY, 1, job_id)  # type: ignore[reportGeneralTypeIssues]
        await self._redis.delete(f"{self.LEASE_PREFIX}{job_id}")  # type: ignore[reportGeneralTypeIssues]
