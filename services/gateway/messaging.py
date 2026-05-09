import asyncio
import json
import uuid
import logging
import time
from typing import Any, Dict, Optional, List
import redis.asyncio as redis

log = logging.getLogger("gateway.messaging")

INFERENCE_LOCK = asyncio.Lock()

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
        self.JOB_PREFIX = "raven:job:"

    async def connect(self):
        if not self._redis:
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
            log.info(f"Connected to Redis at {self.redis_url} for job queuing.")

    async def enqueue_job(self, user_id: str, payload: Dict[str, Any]) -> str:
        """Adds a job to the FIFO queue."""
        if not self._redis:
            await self.connect()

        job_id = str(uuid.uuid4())
        job_data = {
            "job_id": job_id,
            "user_id": user_id,
            "status": JobStatus.QUEUED,
            "payload": payload,
            "created_at": time.time(),
            "result": None,
            "error": None
        }

        # Store job metadata
        await self._redis.set(f"{self.JOB_PREFIX}{job_id}", json.dumps(job_data), ex=3600) # 1 hour expiry
        
        # Push to FIFO queue (Right push)
        await self._redis.rpush(self.QUEUE_KEY, job_id)
        
        log.info(f"Job {job_id} enqueued for user {user_id}")
        return job_id

    async def pop_job(self) -> Optional[Dict[str, Any]]:
        """Pops the next job from the queue (Left pop)."""
        if not self._redis:
            await self.connect()

        job_id = await self._redis.lpop(self.QUEUE_KEY)
        if not job_id:
            return None

        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")
        if not job_raw:
            return None

        job = json.loads(job_raw)
        job["status"] = JobStatus.PROCESSING
        job["started_at"] = time.time()
        
        await self._redis.set(f"{self.JOB_PREFIX}{job_id}", json.dumps(job), ex=3600)
        return job

    async def complete_job(self, job_id: str, result: Any):
        """Marks a job as completed and stores the result."""
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")
        if not job_raw:
            return

        job = json.loads(job_raw)
        job["status"] = JobStatus.COMPLETED
        job["result"] = result
        job["completed_at"] = time.time()
        
        await self._redis.set(f"{self.JOB_PREFIX}{job_id}", json.dumps(job), ex=3600)
        log.info(f"Job {job_id} completed.")

    async def fail_job(self, job_id: str, error: str):
        """Marks a job as failed."""
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")
        if not job_raw:
            return

        job = json.loads(job_raw)
        job["status"] = JobStatus.FAILED
        job["error"] = error
        job["completed_at"] = time.time()
        
        await self._redis.set(f"{self.JOB_PREFIX}{job_id}", json.dumps(job), ex=3600)
        log.error(f"Job {job_id} failed: {error}")

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the current status and result of a job."""
        if not self._redis:
            await self.connect()
        
        job_raw = await self._redis.get(f"{self.JOB_PREFIX}{job_id}")
        return json.loads(job_raw) if job_raw else None

    async def get_queue_position(self, job_id: str) -> int:
        """Returns the 0-indexed position of a job in the queue."""
        if not self._redis:
            await self.connect()
        
        queue = await self._redis.lrange(self.QUEUE_KEY, 0, -1)
        try:
            return queue.index(job_id)
        except ValueError:
            return -1

    async def push_chunk(self, job_id: str, chunk: str):
        """Pushes a message chunk to a job-specific Redis list."""
        if not self._redis:
            await self.connect()
        key = f"raven:job:chunks:{job_id}"
        await self._redis.rpush(key, chunk)
        await self._redis.expire(key, 600) # 10 minute TTL for chunks

    async def get_chunks(self, job_id: str) -> List[str]:
        """Pops all available chunks for a job."""
        if not self._redis:
            await self.connect()
        key = f"raven:job:chunks:{job_id}"
        # Atomic pop all
        chunks = await self._redis.lrange(key, 0, -1)
        await self._redis.ltrim(key, len(chunks), -1)
        return chunks

    async def close(self):
        if self._redis:
            await self._redis.close()
