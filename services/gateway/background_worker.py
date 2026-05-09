# services/gateway/background_worker.py
"""
Raven Background Worker — The "heartbeat" and "brain" of the autonomous system.
1. Health Monitoring: Periodically scrapes logs and triggers self-repair.
2. Singleton Inference: Processes the FIFO job queue for LLM tasks.
"""
import asyncio
import logging
import httpx
import os
import json
import time
from typing import Any, Dict, Optional
from datetime import datetime
try:
    from .orchestrator import process_full_orchestration
except (ImportError, ValueError):
    from orchestrator import process_full_orchestration

try:
    from .messaging import InferenceJobQueue, JobStatus, INFERENCE_LOCK
except (ImportError, ValueError):
    from messaging import InferenceJobQueue, JobStatus, INFERENCE_LOCK

log = logging.getLogger("gateway.background_worker")

# Configuration
CHECK_INTERVAL_SECONDS = int(os.getenv("RAVEN_CHECK_INTERVAL", "300"))
ERROR_THRESHOLD = int(os.getenv("RAVEN_ERROR_THRESHOLD", "5"))
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
GATEWAY_SVC = "http://localhost:11435" # Gateway port
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

class RavenWorker:
    def __init__(self):
        self.is_running = False
        self._health_task = None
        self._inference_task = None
        self.job_queue = InferenceJobQueue(os.getenv("REDIS_URL", "redis://redis:6379/0"))

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        await self.job_queue.connect()
        self._health_task = asyncio.create_task(self._health_loop())
        self._inference_task = asyncio.create_task(self._inference_loop())
        log.info("Raven Background Worker (Health + Inference) started.")

    async def stop(self):
        self.is_running = False
        if self._health_task: self._health_task.cancel()
        if self._inference_task: self._inference_task.cancel()
        await self.job_queue.close()
        log.info("Raven Background Worker stopped.")

    async def _health_loop(self):
        while self.is_running:
            try:
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                await self.perform_health_check()
            except Exception as e:
                log.error(f"Error in Health loop: {e}")

    async def _inference_loop(self):
        """The Singleton Inference Worker — Processes jobs one by one."""
        log.info("Inference worker listening for jobs...")
        while self.is_running:
            try:
                job = await self.job_queue.pop_job()
                if job:
                    log.info(f"Processing job {job['job_id']} for {job['user_id']}")
                    await self._process_inference_job(job)
                else:
                    await asyncio.sleep(1.0) # Wait for jobs
            except Exception as e:
                log.error(f"Error in Inference loop: {e}")
                await asyncio.sleep(5.0)

    async def _process_inference_job(self, job: Dict[str, Any]):
        job_id = job["job_id"]
        payload = job["payload"]
        
        try:
            # 1. Inference Orchestration

            # 2. Singleton Inference with Full Orchestration and Streaming support
            async with INFERENCE_LOCK:
                log.info(f"Inference Lock ACQUIRED for job {job_id}")
                
                async def chunk_callback(chunk: str):
                    await self.job_queue.push_chunk(job_id, chunk)
                
                ans = await process_full_orchestration(job, chunk_callback=chunk_callback)
                log.info(f"Inference Lock RELEASED for job {job_id}")

            # 3. Tool Extraction & Execution (Future Phases will expand this)
            # 4. Proactive TTS Callback for Voice Clients
            if payload.get("client") == "voice" or payload.get("source") == "home_assistant":
                await self._trigger_tts_callback(payload, ans)

            await self.job_queue.complete_job(job_id, ans)
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.error(f"Failed to process job {job_id}: {e}\n{tb}")
            await self.job_queue.fail_job(job_id, str(e))

    async def _trigger_tts_callback(self, payload: Dict[str, Any], message: str):
        """Proactively broadcast result via TTS."""
        try:
            creds = payload.get("creds", {})
            user_id = payload.get("user_id")
            
            # Use execution service's announce endpoint
            announce_payload = {
                "user_context": creds,
                "message": message,
                "entity_id": payload.get("device_id") or "media_player.office_tv",
                "volume": 0.6
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    f"{EXECUTION_SVC}/execute/announce",
                    json=announce_payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
            log.info(f"Proactive TTS callback triggered for {user_id}")
        except Exception as e:
            log.error(f"TTS Callback failed: {e}")

    # --- Legacy Health Check Methods ---
    async def perform_health_check(self):
        log.info("Performing Raven health check...")
        containers = await self._get_containers()
        if not containers: return
        problematic = []
        for c in containers:
            errs = await self._get_errors(c["name"])
            if len(errs) >= ERROR_THRESHOLD:
                problematic.append({"name": c["name"], "count": len(errs), "sample": errs[:3]})
        if problematic:
            await self.trigger_self_repair(problematic)

    async def _get_containers(self):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{EXECUTION_SVC}/execute/docker_containers", headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=5.0)
                if resp.status_code == 200: return resp.json().get("detail", {}).get("containers", [])
        except: return []

    async def _get_errors(self, name):
        try:
            payload = {"user_context": {"user": "raven", "is_admin": True}, "container_name": name, "tail": 100, "filter_level": "ERROR"}
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{EXECUTION_SVC}/execute/docker_logs", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=5.0)
                if resp.status_code == 200: return resp.json().get("detail", {}).get("lines", [])
        except: return []

    async def trigger_self_repair(self, problematic):
        summary = "\n".join([f"- {c['name']}: {c['count']} errors" for c in problematic])
        query = f"SYSTEM ALERT: Health check detected errors.\n\nServices:\n{summary}\n\nFix them."
        await self.job_queue.enqueue_job("raven_admin", {
            "query": query, "model": "qwen2.5-coder:7b", "system": "You are a repair agent.", "stream": False
        })

# Global instance
worker = RavenWorker()
