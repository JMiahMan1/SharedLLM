# services/gateway/background_worker.py
"""
Jarvis Background Worker — The "heartbeat" and "brain" of the autonomous Raven agent.
1. Health Monitoring: Periodically scrapes logs and triggers self-repair.
2. Singleton Inference: Processes the FIFO job queue for Tier 2 (Librarian) and Tier 3 (Raven) tasks.
"""
import asyncio
import logging
import httpx
import os
from typing import Any, Dict, Optional
try:
    from .orchestrator import process_full_orchestration
    from .config import SYSTEM_IDENTITY
except (ImportError, ValueError):
    from orchestrator import process_full_orchestration
    from config import SYSTEM_IDENTITY


try:
    from .messaging import InferenceJobQueue, JobStatus, TIER2_SEMAPHORE, TIER3_LOCK
except (ImportError, ValueError):
    from messaging import InferenceJobQueue, TIER2_SEMAPHORE, TIER3_LOCK

log = logging.getLogger("gateway.background_worker")

# Configuration
CHECK_INTERVAL_SECONDS = int(os.getenv("RAVEN_CHECK_INTERVAL", "300"))
ERROR_THRESHOLD = int(os.getenv("RAVEN_ERROR_THRESHOLD", "5"))
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
GATEWAY_SVC = "http://localhost:11435" # Gateway port
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

class RavenWorker:
    def __init__(self):
        self.is_running = False
        self._health_task = None
        self._inference_task = None
        self.job_queue = InferenceJobQueue(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        # Autonomous detection signals — must match orchestrator list
        self._autonomy_signals = [
            "raven", "use raven", "audit", "repair", "self repair", "self-heal",
            "self fix", "deploy", "bootstrap", "develop", "fix the app",
            "fix the service", "fix the codebase", "agentic", "autonomous"
        ]

    def _is_autonomous_job(self, payload: Dict[str, Any], user_id: str) -> bool:
        """Determine if a job requires Tier-3 (Raven) exclusive lock."""
        query = str(payload.get("query", "")).lower()
        if any(signal in query for signal in self._autonomy_signals):
            return True
        if user_id.lower() in ("raven_admin", SYSTEM_IDENTITY):
            return True
        return False

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        await self.job_queue.connect()
        await self._recover_orphaned_missions()
        self._health_task = asyncio.create_task(self._health_loop())
        self._inference_task = asyncio.create_task(self._inference_loop())
        self._talk_task = asyncio.create_task(self._talk_monitor_loop())
        log.info("Raven Background Worker (Health + Inference + Talk Monitor) started.")

    async def _recover_orphaned_missions(self):
        """
        On startup, any mission still in 'executing' status is an orphan —
        the gateway was killed or restarted mid-run. Mark them failed so the
        UI unblocks and TIER3_LOCK is not permanently held.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "http://identity:8001/api/raven/missions",
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status_code != 200:
                    return
                missions = resp.json()
                orphans = [m for m in missions if m.get("status") == "executing"]
                for mission in orphans:
                    mid = mission["id"]
                    await client.patch(
                        f"http://identity:8001/api/raven/missions/{mid}",
                        json={"status": "failed", "result": "Mission interrupted: gateway restarted during execution."},
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    log.warning(f"[RavenWorker] Recovered orphaned mission #{mid} → marked failed")
        except Exception as e:
            log.warning(f"[RavenWorker] Orphan recovery failed (non-critical): {e}")

    async def stop(self):
        self.is_running = False
        if self._health_task:
            self._health_task.cancel()
        if self._inference_task:
            self._inference_task.cancel()
        if self._talk_task:
            self._talk_task.cancel()
        await self.job_queue.close()
        log.info("Raven Background Worker stopped.")

    async def _health_loop(self):
        while self.is_running:
            try:
                from agent_loop import get_dynamic_llm_settings
                settings = await get_dynamic_llm_settings()
                
                is_suspended = settings.get("raven_suspended", "false").lower() == "true"
                scan_interval = int(settings.get("raven_scan_interval", "300"))
                error_threshold = int(settings.get("raven_error_threshold", "5"))
                
                if is_suspended:
                    log.info("Raven health checks are suspended. Standing by...")
                    await asyncio.sleep(scan_interval)
                    continue

                await self.perform_health_check(error_threshold, settings)
                await asyncio.sleep(scan_interval)
            except Exception as e:
                log.error(f"Error in Health loop: {e}")
                await asyncio.sleep(300)

    async def _inference_loop(self):
        """The Singleton Inference Worker — Processes jobs one by one."""
        log.info("Inference worker listening for jobs...")
        while self.is_running:
            try:
                reclaimed = await self.job_queue.reclaim_expired_jobs()
                if reclaimed:
                    log.warning("Recovered %s expired Raven job(s) back into the queue.", reclaimed)

                job = await self.job_queue.claim_job()
                if job:
                    log.info(f"Processing job {job['job_id']} for {job['user_id']}")
                    await self._process_inference_job(job)
                else:
                    await asyncio.sleep(1.0) # Wait for jobs
            except Exception as e:
                log.error(f"Error in Inference loop: {e}")
                await asyncio.sleep(5.0)

    async def _talk_monitor_loop(self):
        """Polls Nextcloud Talk for @jarvis mentions."""
        log.info("Talk Monitor worker started.")
        import redis.asyncio as redis
        r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
        
        while self.is_running:
            try:
                await self._check_talk_once(r)
                await asyncio.sleep(10) # Poll every 10 seconds
            except Exception as e:
                log.error(f"Error in Talk Monitor loop: {e}")
                await asyncio.sleep(30)

    async def _check_talk_once(self, r):
        """Perform a single poll of Nextcloud Talk."""
        # 1. Resolve system credentials
        creds = await self._get_system_creds()
        if not creds or not creds.get("nextcloud_user"):
            return

        # 2. List rooms
        async with httpx.AsyncClient(timeout=30.0) as client:
            list_resp = await client.post(
                f"{EXECUTION_SVC}/execute/talk",
                json={"user_context": creds, "action": "list"},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if list_resp.status_code != 200:
                return
            
            rooms = list_resp.json().get("detail", {}).get("conversations", [])
            for room in rooms:
                token = room["token"]
                
                msg_resp = await client.post(
                    f"{EXECUTION_SVC}/execute/talk",
                    json={"user_context": creds, "action": "messages", "token": token, "limit": 5},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if msg_resp.status_code != 200:
                    continue
                    
                messages = msg_resp.json().get("detail", {}).get("messages", [])
                if not messages:
                    continue
                    
                messages.sort(key=lambda m: m.get("id") or 0)
                
                last_processed = await r.get(f"jarvis:talk:last_msg:{token}")
                new_last = last_processed
                
                for msg in messages:
                    msg_id = str(msg.get("id"))
                    if last_processed and int(msg_id) <= int(last_processed):
                        continue
                    
                    new_last = msg_id
                    content = msg.get("message", "")
                    actor_id = msg.get("actor_id", "")
                    
                    if actor_id == creds.get("nextcloud_user"):
                        continue

                    if "@jarvis" in content.lower():
                        query = content.replace("@jarvis", "").strip()
                        log.info(f"[TalkMonitor] Detected @jarvis mention in room {token}: {query}")
                        
                        await self.job_queue.enqueue_job(
                            user_id=creds["user"],
                            payload={
                                "query": query,
                                "model": "auto",
                                "creds": creds,
                                "_talk_token": token,
                                "_talk_source": "nextcloud"
                            }
                        )
                
                if new_last != last_processed:
                    await r.set(f"jarvis:talk:last_msg:{token}", new_last)

    async def _get_system_creds(self) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    "http://identity:8001/api/resolve",
                    json={"rag_user": "default"},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            log.error(f"Failed to resolve system credentials: {e}")
        return None

    async def _process_inference_job(self, job: Dict[str, Any]):
        job_id = job["job_id"]
        payload = job["payload"]
        user_id = job.get("user_id", "")
        heartbeat_task = None
        
        # Determine job tier (2 = Librarian, 3 = Raven) for concurrency control
        is_autonomous = self._is_autonomous_job(payload, user_id)
        
        try:
            heartbeat_task = asyncio.create_task(self._job_heartbeat(job_id))
            
            # Acquire appropriate lock based on tier, then run orchestration
            if is_autonomous:
                log.info(f"[Worker] Acquiring TIER3 (Raven) lock for job {job_id}")
                async with TIER3_LOCK:
                    mission_id = payload.get("_mission_id")
                    if mission_id:
                        try:
                            async with httpx.AsyncClient(timeout=10.0) as client:
                                await client.patch(
                                    f"{os.getenv('IDENTITY_SVC_URL', 'http://identity:8001')}/api/raven/missions/{mission_id}",
                                    json={"status": "executing"},
                                    headers={"X-Internal-Secret": os.getenv("INTERNAL_SECRET")}
                                )
                        except Exception as patch_e:
                            log.error(f"Failed to update mission {mission_id} to executing: {patch_e}")
                            
                    async def chunk_callback(chunk: str):
                        await self.job_queue.push_chunk(job_id, chunk)
                    payload["_job_id"] = job_id  # traceability
                    
                    # --- CANCELLABLE ORCHESTRATION ---
                    orchestration_task = asyncio.create_task(process_full_orchestration(payload, chunk_callback=chunk_callback))
                    
                    # Monitor for kill signal
                    kill_monitor = None
                    if mission_id:
                        async def _monitor_kill(mid, task_to_cancel):
                            import redis.asyncio as redis
                            r_kill = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
                            pubsub = r_kill.pubsub()
                            await pubsub.subscribe(f"raven:mission:kill:{mid}")
                            try:
                                async for message in pubsub.listen():
                                    if message["type"] == "message" and message["data"] == "KILL":
                                        log.warning(f"[Worker] KILL SIGNAL RECEIVED for mission {mid}. Cancelling task.")
                                        task_to_cancel.cancel()
                                        break
                            finally:
                                await pubsub.unsubscribe()
                                await r_kill.close()
                        kill_monitor = asyncio.create_task(_monitor_kill(mission_id, orchestration_task))

                    try:
                        ans = await orchestration_task
                    except asyncio.CancelledError:
                        log.warning(f"[Worker] Orchestration for mission {mission_id} was CANCELLED.")
                        ans = "Mission aborted by user."
                    finally:
                        if kill_monitor:
                            kill_monitor.cancel()
            else:
                log.info(f"[Worker] Acquiring TIER2 (Librarian) semaphore for job {job_id}")
                async with TIER2_SEMAPHORE:
                    mission_id = payload.get("_mission_id")
                    if mission_id:
                        try:
                            async with httpx.AsyncClient(timeout=10.0) as client:
                                await client.patch(
                                    f"{os.getenv('IDENTITY_SVC_URL', 'http://identity:8001')}/api/raven/missions/{mission_id}",
                                    json={"status": "executing"},
                                    headers={"X-Internal-Secret": os.getenv("INTERNAL_SECRET")}
                                )
                        except Exception as patch_e:
                            log.error(f"Failed to update mission {mission_id} to executing: {patch_e}")
                            
                    async def chunk_callback(chunk: str):
                        await self.job_queue.push_chunk(job_id, chunk)
                    payload["_job_id"] = job_id
                    ans = await process_full_orchestration(payload, chunk_callback=chunk_callback)
            
            await self.job_queue.complete_job(job_id, ans)
            
            # --- TALK CALLBACK ---
            talk_token = payload.get("_talk_token")
            if talk_token:
                await self._trigger_talk_callback(payload, str(ans))

            mission_id = payload.get("_mission_id")
            if mission_id:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.patch(
                            f"{os.getenv('IDENTITY_SVC_URL', 'http://identity:8001')}/api/raven/missions/{mission_id}",
                            json={"status": "completed", "result": str(ans)},
                            headers={"X-Internal-Secret": os.getenv("INTERNAL_SECRET")}
                        )
                except Exception as patch_e:
                    log.error(f"Failed to update mission {mission_id} to completed: {patch_e}")
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.error(f"Failed to process job {job_id}: {e}\n{tb}")
            await self.job_queue.fail_job(job_id, str(e))
            
            mission_id = payload.get("_mission_id")
            if mission_id:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.patch(
                            f"{os.getenv('IDENTITY_SVC_URL', 'http://identity:8001')}/api/raven/missions/{mission_id}",
                            json={"status": "failed", "result": str(e)},
                            headers={"X-Internal-Secret": os.getenv("INTERNAL_SECRET")}
                        )
                except Exception as patch_e:
                    log.error(f"Failed to update mission {mission_id} to failed: {patch_e}")
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

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

    async def _trigger_talk_callback(self, payload: Dict[str, Any], message: str):
        """Send response back to Nextcloud Talk."""
        try:
            creds = payload.get("creds", {})
            token = payload.get("_talk_token")
            if not token:
                return
            
            talk_payload = {
                "user_context": creds,
                "action": "send",
                "token": token,
                "message": message
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    f"{EXECUTION_SVC}/execute/talk",
                    json=talk_payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
            log.info(f"Talk callback sent to room {token}")
        except Exception as e:
            log.error(f"Talk Callback failed: {e}")

    async def perform_health_check(self, error_threshold: int, settings: Dict[str, Any]):
        log.info("Performing Raven health check...")
        containers = await self._get_containers()
        if not containers:
            return
        problematic = []
        for c in containers:
            errs = await self._get_errors(c["name"])
            if len(errs) >= error_threshold:
                problematic.append({"name": c["name"], "count": len(errs), "sample": errs[:3]})
        if problematic:
            await self.trigger_self_repair(problematic, settings)

    async def _get_containers(self):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{EXECUTION_SVC}/execute/docker_containers", headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=5.0)
                if resp.status_code == 200:
                    return resp.json().get("detail", {}).get("containers", [])
        except Exception:
            return []

    async def _get_errors(self, name):
        try:
            payload = {"user_context": {"user": SYSTEM_IDENTITY, "is_admin": True}, "container_name": name, "tail": 100, "filter_level": "ERROR"}
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{EXECUTION_SVC}/execute/docker_logs", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=5.0)
                if resp.status_code == 200:
                    return resp.json().get("detail", {}).get("lines", [])
        except Exception:
            return []

    async def trigger_self_repair(self, problematic, settings):
        coding_model = settings.get("coding_model") or settings.get("ollama_coding_model")
        if not coding_model or coding_model == "auto":
            log.error("No valid coding model configured in Identity. Triage queue may fail to execute.")
            # We still push the anomaly, but the UI must be used to assign a model.

        for c in problematic:
            summary = f"Detected {c['count']} errors."
            query = f"SYSTEM ALERT: Health check detected errors.\n\nServices:\n- {c['name']}: {c['count']} errors\n\nFix them."
            
            mission_payload = {
                "mission_type": "admin_fix",
                "target_container": c["name"],
                "error_summary": summary,
                "proposed_mission": query,
                "coding_model": coding_model
            }
            
            try:
                # Push to Identity Triage Queue instead of executing immediately
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{os.getenv('IDENTITY_SVC_URL', 'http://identity:8001')}/api/raven/missions",
                        json=mission_payload,
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    if resp.status_code == 200:
                        log.info(f"Mission for {c['name']} successfully pushed to Triage Queue.")
                    else:
                        log.error(f"Failed to push mission to Triage Queue: {resp.text}")
            except Exception as e:
                log.error(f"Error pushing to Triage Queue: {e}")

    async def _job_heartbeat(self, job_id: str):
        while self.is_running:
            await asyncio.sleep(30)
            await self.job_queue.heartbeat_job(job_id)

# Global instance
worker = RavenWorker()
