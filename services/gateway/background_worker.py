# services/gateway/background_worker.py
"""
Raven Background Worker — The "heartbeat" of the autonomous system.

This module implements a background loop that periodically scrapes Docker logs
for errors and, if detected, submits a self-repair request to the Gateway's 
own chat endpoint using the Autonomous Developer prompt.
"""
import asyncio
import logging
import httpx
import os
import json
from datetime import datetime

log = logging.getLogger("gateway.background_worker")

# Configuration
CHECK_INTERVAL_SECONDS = int(os.getenv("RAVEN_CHECK_INTERVAL", "300"))  # Default 5 minutes
ERROR_THRESHOLD = int(os.getenv("RAVEN_ERROR_THRESHOLD", "5"))          # Min errors to trigger repair
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
GATEWAY_SVC = "http://localhost:8000" # Internal to container

class RavenWorker:
    def __init__(self):
        self.is_running = False
        self._task = None

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        log.info("Raven Background Worker started.")

    async def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Raven Background Worker stopped.")

    async def _loop(self):
        while self.is_running:
            try:
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
                await self.perform_health_check()
            except Exception as e:
                log.error(f"Error in Raven loop: {e}")

    async def perform_health_check(self):
        """Scrapes logs and triggers repair if errors are found."""
        log.info("Performing Raven health check...")
        
        # 1. Discover containers
        containers = await self._get_containers()
        if not containers:
            return

        total_errors = 0
        problematic_containers = []

        # 2. Check each container for recent errors
        for container in containers:
            error_lines = await self._get_errors(container["name"])
            if len(error_lines) >= ERROR_THRESHOLD:
                total_errors += len(error_lines)
                problematic_containers.append({
                    "name": container["name"],
                    "count": len(error_lines),
                    "sample": error_lines[:3]
                })

        # 3. If errors exceed threshold, trigger self-repair
        if problematic_containers:
            log.warning(f"Raven detected {total_errors} errors across {len(problematic_containers)} services.")
            await self.trigger_self_repair(problematic_containers)
        else:
            log.info("System healthy. No critical error patterns detected.")

    async def _get_containers(self):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{EXECUTION_SVC}/execute/docker_containers",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=5.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("detail", {}).get("containers", [])
        except Exception as e:
            log.error(f"Failed to fetch container list: {e}")
        return []

    async def _get_errors(self, container_name):
        try:
            payload = {
                "user_context": {"user": "raven", "is_admin": True},
                "container_name": container_name,
                "tail": 100,
                "filter_level": "ERROR"
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{EXECUTION_SVC}/execute/docker_logs",
                    json=payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=5.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("detail", {}).get("lines", [])
        except Exception as e:
            log.error(f"Failed to fetch logs for {container_name}: {e}")
        return []

    async def trigger_self_repair(self, problematic_containers):
        """Sends a chat request to the Gateway to trigger the Autonomous Developer loop."""
        summary = "\n".join([f"- {c['name']}: {c['count']} errors (Sample: {c['sample'][0] if c['sample'] else 'N/A'})" for c in problematic_containers])
        
        query = (
            f"SYSTEM ALERT: Raven health check detected multiple errors.\n\n"
            f"Problematic Services:\n{summary}\n\n"
            f"Analyze the logs for these services, identify the root cause, and implement a fix. "
            f"If it's a transient Docker issue, restart the container. If it's a code bug, fix the file, commit, and redeploy."
        )

        log.info("Triggering autonomous self-repair request...")
        
        try:
            # We use /api/chat with the internal secret to bypass normal auth
            # The Gateway will see 'Raven' signals and use the Autonomous prompt.
            payload = {
                "query": query,
                "user": "raven_admin",
                "is_admin": True,
                "stream": False
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{GATEWAY_SVC}/api/chat",
                    json=payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=120.0 # Long timeout for complex repair tasks
                )
                if resp.status_code == 200:
                    log.info("Self-repair loop completed successfully.")
                    result = resp.json()
                    log.info(f"Repair Result Summary: {result.get('message', '')[:200]}...")
                else:
                    log.error(f"Self-repair request failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            log.error(f"Failed to trigger self-repair: {e}")

# Global instance
worker = RavenWorker()

