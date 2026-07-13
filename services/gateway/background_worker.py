# services/gateway/background_worker.py
"""
Jarvis Background Worker — The "heartbeat" and "brain" of the autonomous Raven agent.
1. Health Monitoring: Periodically scrapes logs and triggers self-repair.
2. Singleton Inference: Processes the FIFO job queue for Tier 2 (Librarian) and Tier 3 (Raven) tasks.
"""
import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import aiohttp

from services.gateway.agent_loop import should_persist_learning
from services.gateway.config import (
    EXECUTION_SVC,
    IDENTITY_SVC,
    INTERNAL_SECRET,
    RAG_SVC,
    RAVEN_CHECK_INTERVAL,
    RAVEN_ERROR_THRESHOLD,
    REDIS_URL,
    SYSTEM_IDENTITY,
)
from services.gateway.intent_engine import is_raven_intent
from services.shared.rag_client import push_mission


@asynccontextmanager
async def _shared_http_client():
    """Yield the gateway's shared, pooled HTTP client WITHOUT closing it.

    Reuses one connection pool across the worker's polling loops instead of
    opening a fresh aiohttp session per iteration. Caller must not close it.
    """
    from services.gateway.main import get_http_client

    yield get_http_client()
from services.gateway.messaging import TIER2_SEMAPHORE, TIER3_LOCK, InferenceJobQueue  # noqa: E402
from services.gateway.orchestrator import process_full_orchestration  # noqa: E402

log = logging.getLogger("gateway.background_worker")

CHECK_INTERVAL_SECONDS = RAVEN_CHECK_INTERVAL
ERROR_THRESHOLD = RAVEN_ERROR_THRESHOLD
GATEWAY_SVC = "http://localhost:11435"

class RavenWorker:
    def __init__(self):
        self.is_running = False
        self._health_task = None
        self._inference_task = None
        self._talk_task = None
        self._cleanup_task = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.job_queue = InferenceJobQueue(REDIS_URL)
    def _is_autonomous_job(self, payload: dict[str, Any], user_id: str) -> bool:
        """Determine if a job requires Tier-3 (Raven) exclusive lock."""
        query = str(payload.get("query", "")).lower()
        if is_raven_intent(query):
            return True
        return user_id.lower() in ("raven_admin", SYSTEM_IDENTITY)

    async def _get_coding_model_from_settings(self) -> str:
        """Resolve coding model from Identity settings. Never hardcode."""
        try:
            from services.gateway.orchestrator import get_all_settings
            settings = await get_all_settings()
            model = settings.get("ollama_coding_model") or settings.get("coding_model") or settings.get("assistant_model")
            if model:
                return model
        except Exception:
            pass
        raise RuntimeError("No coding model configured in Identity settings")

    async def _get_model_from_settings(self) -> str:
        """Resolve assistant/librarian model from Identity settings. Never hardcode."""
        try:
            from services.gateway.orchestrator import get_all_settings
            settings = await get_all_settings()
            model = settings.get("ollama_librarian_model") or settings.get("librarian_model") or settings.get("assistant_model")
            if model:
                return model
        except Exception:
            pass
        raise RuntimeError("No assistant/librarian model configured in Identity settings")

    def start(self):
        """Start the worker on its OWN event loop in a dedicated daemon thread.

        Running the worker loop separately from the FastAPI API event loop is
        what prevents a long-running Raven mission from saturating the API:
        mission orchestration (LLM calls, shell, file I/O) executes on the
        worker loop, so the API loop stays free to serve /api/raven/missions
        and the rest of the gateway while a mission is in flight.
        """
        if self.is_running:
            return
        self.is_running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="raven-worker", daemon=True)
        self._thread.start()
        # Bootstrap (connect redis, recover orphans, spawn loops) on the worker loop.
        fut = asyncio.run_coroutine_threadsafe(self._bootstrap(), self._loop)
        try:
            fut.result(timeout=60)
        except Exception as e:  # pragma: no cover - defensive
            log.error(f"[RavenWorker] Bootstrap failed (non-critical): {e}")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _bootstrap(self):
        await self.job_queue.connect()
        await self._recover_orphaned_missions()
        self._health_task = self._loop.create_task(self._health_loop())
        self._inference_task = self._loop.create_task(self._inference_loop())
        self._talk_task = self._loop.create_task(self._talk_monitor_loop())
        self._cleanup_task = self._loop.create_task(self._cleanup_loop())
        log.info("Raven Background Worker (Health + Inference + Talk Monitor + Cleanup) started.")

    def stop(self):
        """Signal the worker loops to stop and shut the worker loop down."""
        self.is_running = False
        loop = self._loop
        if loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=30)
            except Exception as e:  # pragma: no cover - defensive
                log.warning(f"[RavenWorker] Shutdown error (non-critical): {e}")
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    async def _shutdown(self):
        for t in (self._health_task, self._inference_task, self._talk_task, self._cleanup_task):
            if t is not None:
                t.cancel()
        await self.job_queue.close()
        # Stop the worker's own event loop so its thread can exit.
        self._loop.stop()

    async def _recover_orphaned_missions(self):
        from services.gateway.main import _build_raven_system_prompt

        """
        On startup, any mission still in 'executing' or 'paused' status is an orphan —
        the gateway was killed or restarted mid-run. Re-enqueue them so they resume
        automatically from their last checkpoint.
        """
        try:
            async with _shared_http_client() as client:
                resp = await client.get(
                    "http://identity:8001/api/raven/missions",
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status != 200:
                    return
                missions = await resp.json()
                orphans = [m for m in missions if m.get("status") in ("executing", "paused")]
                for mission in orphans:
                    mid = mission["id"]
                    # Resolve model from Identity settings; never hardcode
                    coding_model = mission.get("coding_model") or await self._get_coding_model_from_settings()
                    payload = {
                        "query": mission["proposed_mission"],
                        "model": coding_model,
                        "system": await _build_raven_system_prompt(mission["proposed_mission"]),
                        "stream": False,
                        "creds": {"user": "default", "is_admin": True},
                        "_mission_id": mid,
                    }
                    await self.job_queue.enqueue_job("raven_resume", payload)
                    await client.patch(
                        f"http://identity:8001/api/raven/missions/{mid}",
                        json={"status": "queued"},
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    log.warning(f"[RavenWorker] Recovered orphaned mission #{mid} → re-enqueued (will resume from checkpoint)")
        except Exception as e:
            log.warning(f"[RavenWorker] Orphan recovery failed (non-critical): {e}")

    async def _health_loop(self):
        while self.is_running:
            try:
                from services.gateway.agent_loop import get_dynamic_llm_settings
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

                # Periodically reap orphaned queued/executing missions (no backing
                # Redis job) so they cannot linger forever after a worker crash.
                now = time.time()
                if now - getattr(self, "_last_reap", 0) > 60:
                    self._last_reap = now
                    try:
                        await self._reap_orphaned_missions()
                    except Exception as e:
                        log.warning(f"[RavenWorker] Orphan reaper failed (non-critical): {e}")

                job = await self.job_queue.claim_job()
                if job:
                    log.info(f"Processing job {job['job_id']} for {job['user_id']}")
                    await self._process_inference_job(job)
                else:
                    await asyncio.sleep(1.0) # Wait for jobs
            except Exception as e:
                log.error(f"Error in Inference loop: {e}")
                await asyncio.sleep(5.0)

    async def _reap_orphaned_missions(self):
        """Cancel queued/executing Raven missions that have no backing Redis job.

        A mission can be left in ``queued`` (or ``executing``) if the worker died
        before its job was enqueued, or the Redis job was lost on a crash. These
        would otherwise sit forever with no worker to pick them up. After a grace
        period with no backing job, we cancel them so the queue stays clean.
        """
        grace_seconds = 900  # 15 minutes
        try:
            async with _shared_http_client() as client:
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/raven/missions",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                )
                if resp.status != 200:
                    return
                missions = await resp.json()
        except Exception as e:
            log.warning(f"[RavenWorker] Orphan reaper list failed (non-critical): {e}")
            return

        for m in missions:
            status = m.get("status")
            if status not in ("queued", "executing"):
                continue
            mid = m.get("id")

            # A live mission has a job in the queue/processing list.
            jobs = await self.job_queue.find_jobs_for_mission(mid)
            if jobs:
                continue

            # No job and still queued/executing — only reap if old enough.
            ts_raw = m.get("queued_at") or m.get("created_at") or m.get("updated_at")
            if ts_raw:
                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    age = (datetime.now(UTC) - ts).total_seconds()
                except Exception:
                    age = grace_seconds + 1
            else:
                age = grace_seconds + 1

            if age < grace_seconds:
                continue

            log.warning(
                f"[RavenWorker] Reaping orphaned {status} mission #{mid} "
                f"(no backing job, age {age:.0f}s)"
            )
            try:
                async with _shared_http_client() as client:
                    await client.patch(
                        f"{IDENTITY_SVC}/api/raven/missions/{mid}",
                        json={
                            "status": "failed",
                            "result": "Cancelled: orphaned mission with no backing job",
                        },
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                    )
            except Exception as e:
                log.warning(f"[RavenWorker] Failed to reap mission #{mid}: {e}")

    async def _talk_monitor_loop(self):
        """Polls Nextcloud Talk for @jarvis mentions."""
        log.info("Talk Monitor worker started.")

        while self.is_running:
            # Create a fresh Redis connection each iteration so stale connections
            # from early startup or Redis restarts are always replaced.
            import redis.asyncio as redis
            r = redis.from_url(REDIS_URL, decode_responses=True)

            # Retry with exponential backoff on startup / Redis restart
            connected = False
            for attempt in range(30):
                try:
                    await r.ping()
                    connected = True
                    break
                except Exception as ping_e:
                    delay = min(2 ** attempt, 30)
                    log.warning(f"Talk Monitor: Redis connection attempt {attempt+1}/30 failed ({REDIS_URL}): {ping_e}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    with suppress(Exception):
                        await r.aclose()
                    import redis.asyncio as redis
                    r = redis.from_url(REDIS_URL, decode_responses=True)

            if not connected:
                log.error(f"Talk Monitor: Redis connection failed after 30 attempts ({REDIS_URL}). Will retry next cycle.")
                await asyncio.sleep(60)
                continue

            try:
                await self._check_talk_once(r)
                await asyncio.sleep(10) # Poll every 10 seconds
            except Exception as e:
                log.error(f"Error in Talk Monitor loop: {e}")
                await asyncio.sleep(30)
            finally:
                await r.aclose()

    async def _check_talk_once(self, r):
        """Perform a single poll of Nextcloud Talk."""
        # 1. Resolve system credentials
        creds = await self._get_system_creds()
        if not creds or not creds.get("nextcloud_user"):
            return

        # 2. List rooms
        url = f"{EXECUTION_SVC}/execute/talk"
        async with _shared_http_client() as client:
            list_resp = await client.post(
                url,
                json={"user_context": creds, "action": "list"},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if list_resp.status != 200:
                return

            list_text = await list_resp.text()
            try:
                list_json = json.loads(list_text) if list_text else {}
            except Exception:
                list_json = {}
            if not isinstance(list_json, dict):
                list_json = {}
            rooms = (list_json.get("detail") or {}).get("conversations", [])
            for room in rooms:
                token = room["token"]

                msg_resp = await client.post(
                    url,
                    json={"user_context": creds, "action": "messages", "token": token, "limit": 5},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if msg_resp.status != 200:
                    continue

                msg_text = await msg_resp.text()
                try:
                    msg_json = json.loads(msg_text) if msg_text else {}
                except Exception:
                    msg_json = {}
                if not isinstance(msg_json, dict):
                    msg_json = {}
                messages = (msg_json.get("detail") or {}).get("messages", [])
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

                        model = await self._get_model_from_settings()
                        await self.job_queue.enqueue_job(
                            user_id=creds["user"],
                            payload={
                                "query": query,
                                "model": model,
                                "creds": creds,
                                "_talk_token": token,
                                "_talk_source": "nextcloud"
                            }
                        )

                if new_last != last_processed:
                    await r.set(f"jarvis:talk:last_msg:{token}", new_last)

    async def _get_system_creds(self) -> dict[str, Any] | None:
        try:
            async with _shared_http_client() as client:
                resp = await client.post(
                    "http://identity:8001/api/resolve",
                    json={"rag_user": "default"},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status == 200:
                    resp_text = await resp.text()
                    return json.loads(resp_text) if resp_text else None
        except Exception as e:
            log.error(f"Failed to resolve system credentials: {e}")
        return None

    async def _process_inference_job(self, job: dict[str, Any]):
        job_id = job["job_id"]
        payload = job["payload"]
        user_id = job.get("user_id", "")
        heartbeat_task = None

        # Determine job tier (2 = Librarian, 3 = Raven) for concurrency control
        is_autonomous = self._is_autonomous_job(payload, user_id)

        # Retry configuration for infrastructure failures (alpaca restarts, connection drops)
        INFRA_RETRIES = 3
        INFRA_RETRY_DELAY = 10  # seconds

        try:
            heartbeat_task = asyncio.create_task(self._job_heartbeat(job_id))
            started_ts: float | None = None
            started_iso: str | None = None

            # Acquire appropriate lock based on tier, then run orchestration
            if is_autonomous:
                log.info(f"[Worker] Acquiring TIER3 (Raven) lock for job {job_id}")
                async with TIER3_LOCK:
                    started_ts = time.time()
                    started_iso = datetime.now(UTC).isoformat()
                    mission_id = payload.get("_mission_id")
                    if mission_id:
                        try:
                            async with _shared_http_client() as client:
                                await client.patch(
                                    f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                                    json={"status": "executing", "started_at": started_iso},
                                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                                )
                        except Exception as patch_e:
                            log.error(f"Failed to update mission {mission_id} to executing: {patch_e}")

                        # Clear any stale kill flag for this mission id. Mission ids can be
                        # REUSED by SQLite (INTEGER PRIMARY KEY has no AUTOINCREMENT),
                        # so a kill flag left over from a *previous* run that shared
                        # this id must not abort the fresh one. The agent-loop kill
                        # watcher polls this key, so a lingering "KILL" would otherwise
                        # terminate a brand-new mission the instant it starts.
                        try:
                            import redis.asyncio as redis

                            r_clear = redis.from_url(REDIS_URL, decode_responses=True)
                            await r_clear.delete(f"raven:mission:kill:{mission_id}")
                            await r_clear.close()
                        except Exception:
                            pass

                    async def chunk_callback(chunk: str):
                        await self.job_queue.push_chunk(job_id, chunk)
                    payload["_job_id"] = job_id  # traceability

                    # Retry orchestration on infrastructure failures
                    ans = None
                    for attempt in range(INFRA_RETRIES):
                        try:
                            # --- CANCELLABLE ORCHESTRATION ---
                            orchestration_task = asyncio.create_task(process_full_orchestration(payload, chunk_callback=chunk_callback))

                            # Monitor for kill signal
                            kill_monitor = None
                            if mission_id:
                                async def _monitor_kill(mid, task_to_cancel):
                                    import redis.asyncio as redis
                                    r_kill = redis.from_url(REDIS_URL, decode_responses=True)
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
                            break  # Success, exit retry loop
                        except (aiohttp.ClientConnectorError, aiohttp.ClientConnectionError, ConnectionResetError, BrokenPipeError) as e:
                            log.warning(f"[Worker] Infrastructure error on attempt {attempt + 1}/{INFRA_RETRIES}: {e}")
                            if attempt < INFRA_RETRIES - 1:
                                log.info(f"[Worker] Retrying orchestration in {INFRA_RETRY_DELAY}s...")
                                await asyncio.sleep(INFRA_RETRY_DELAY)
                            else:
                                log.error(f"[Worker] All {INFRA_RETRIES} infrastructure retries failed: {e}")
                                raise
            else:
                log.info(f"[Worker] Acquiring TIER2 (Librarian) semaphore for job {job_id}")
                async with TIER2_SEMAPHORE:
                    started_ts = time.time()
                    started_iso = datetime.now(UTC).isoformat()
                    mission_id = payload.get("_mission_id")
                    if mission_id:
                        try:
                            async with _shared_http_client() as client:
                                await client.patch(
                                    f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                                    json={"status": "executing", "started_at": started_iso},
                                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                                )
                        except Exception as patch_e:
                            log.error(f"Failed to update mission {mission_id} to executing: {patch_e}")

                        # Clear any stale kill flag for this mission id (see TIER3
                        # path): SQLite reuses ids, so a leftover "KILL" from a
                        # prior run must not abort this fresh one.
                        try:
                            import redis.asyncio as redis

                            r_clear = redis.from_url(REDIS_URL, decode_responses=True)
                            await r_clear.delete(f"raven:mission:kill:{mission_id}")
                            await r_clear.close()
                        except Exception:
                            pass

                    async def chunk_callback(chunk: str):
                        await self.job_queue.push_chunk(job_id, chunk)
                    payload["_job_id"] = job_id

                    # Retry orchestration on infrastructure failures
                    ans = None
                    for attempt in range(INFRA_RETRIES):
                        try:
                            ans = await process_full_orchestration(payload, chunk_callback=chunk_callback)
                            break  # Success, exit retry loop
                        except (aiohttp.ClientConnectorError, aiohttp.ClientConnectionError, ConnectionResetError, BrokenPipeError) as e:
                            log.warning(f"[Worker] Infrastructure error on attempt {attempt + 1}/{INFRA_RETRIES}: {e}")
                            if attempt < INFRA_RETRIES - 1:
                                log.info(f"[Worker] Retrying orchestration in {INFRA_RETRY_DELAY}s...")
                                await asyncio.sleep(INFRA_RETRY_DELAY)
                            else:
                                log.error(f"[Worker] All {INFRA_RETRIES} infrastructure retries failed: {e}")
                                raise

            await self.job_queue.complete_job(job_id, ans)

            # --- TALK CALLBACK ---
            talk_token = payload.get("_talk_token")
            if talk_token:
                from services.gateway.orchestrator import strip_json_from_response
                await self._trigger_talk_callback(payload, strip_json_from_response(str(ans)))

            # --- TTS CALLBACK (for voice clients with device_id) ---
            device_id = payload.get("device_id")
            if device_id:
                from services.gateway.orchestrator import strip_json_from_response
                await self._trigger_tts_callback(payload, strip_json_from_response(str(ans)))

            mission_id = payload.get("_mission_id")
            if mission_id:
                try:
                    result_str = str(ans)
                    is_meaningful = should_persist_learning(result_str)
                    if is_meaningful:
                        status = "completed"
                        log.info(f"[Worker] Mission {mission_id} completed with meaningful result")
                    else:
                        # Check if this looks like a schema/tool format error — retry with bigger model
                        needs_upgrade = self._should_upgrade_model(result_str, payload)
                        if needs_upgrade:
                            await self._retry_with_bigger_model(mission_id, payload, result_str)
                            return  # Don't update status yet — retry will handle it
                        status = "failed"
                        # Preserve the actual result so the user can see what went wrong
                        if result_str and result_str.strip() not in ("None", "", "null"):
                            result_str = f"The mission did not produce a meaningful result. Output: {result_str[:1000]}"
                        else:
                            result_str = "The mission did not produce a meaningful result. The LLM returned an empty or invalid response."
                        log.warning(f"[Worker] Mission {mission_id} marked failed — no meaningful work accomplished")
                    async with _shared_http_client() as client:
                        completed_iso = datetime.now(UTC).isoformat()
                        duration = int(time.time() - started_ts) if started_ts else None
                        await client.patch(
                            f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                            json={"status": status, "result": result_str, "completed_at": completed_iso, "duration": duration},
                            headers={"X-Internal-Secret": INTERNAL_SECRET}
                        )
                        # Best-effort: persist a mission post-mortem into RAG for
                        # self-repair recall (Section 6: mission_history).
                        task_desc = str(payload.get("query") or payload.get("prompt") or "")
                        asyncio.ensure_future(
                            push_mission(
                                mission_id,
                                task_desc,
                                status,
                                error_summary=result_str if status == "failed" else "",
                                user_id=str(payload.get("user_id") or "default"),
                            )
                        )
                except Exception as patch_e:
                    log.error(f"Failed to update mission {mission_id} status: {patch_e}")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.error(f"Failed to process job {job_id}: {e}\n{tb}")
            await self.job_queue.fail_job(job_id, str(e))

            mission_id = payload.get("_mission_id")
            if mission_id:
                try:
                    async with _shared_http_client() as client:
                        completed_iso = datetime.now(UTC).isoformat()
                        duration = int(time.time() - started_ts) if started_ts else None
                        await client.patch(
                            f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                            json={"status": "failed", "result": str(e), "completed_at": completed_iso, "duration": duration},
                            headers={"X-Internal-Secret": INTERNAL_SECRET}
                        )
                except Exception as patch_e:
                    log.error(f"Failed to update mission {mission_id} to failed: {patch_e}")
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task

    def _should_upgrade_model(self, result: str, payload: dict[str, Any]) -> bool:
        """
        Detect if a mission failed due to schema/tool format errors that suggest
        the model was too small to understand the tool calling format.
        """
        retry_count = payload.get("_retry_count", 0)
        if retry_count >= 1:
            return False  # Only one retry allowed

        result_lower = result.lower()
        schema_error_indicators = [
            "422",
            "schema error",
            "field required",
            "missing",
            "loc",
            "validation error",
            "no valid tool call",
            "failed to produce valid tool",
            "agent failed to produce",
        ]
        if any(indicator in result_lower for indicator in schema_error_indicators):
            log.warning("[Worker] Schema error detected — candidate for model upgrade")
            return True

        # Also detect when model just writes garbage instead of using tools
        if "successfully wrote" in result_lower and len(result) < 200:
            log.warning("[Worker] Suspicious short 'success' — candidate for model upgrade")
            return True

        return False

    async def _get_upgrade_model(self, current_model: str) -> str:
        """
        Dynamically find the largest available model that isn't the current one.
        Queries Ollama's /api/tags and picks the model with the largest size.
        """
        from services.gateway.orchestrator import _get, get_all_settings
        try:
            settings = await get_all_settings()
            ollama_url = _get(settings, "llm_local_url")
            if not ollama_url:
                raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
            async with _shared_http_client() as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                if resp.status != 200:
                    log.warning(f"[Worker] Failed to fetch Ollama models: {resp.status}")
                    return current_model
                models = (await resp.json()).get("models", [])
                if not models:
                    log.warning("[Worker] No models available from Ollama")
                    return current_model
                # Filter out the current model and pick the largest by size
                candidates = [m for m in models if m["name"] != current_model]
                if not candidates:
                    log.warning("[Worker] No alternative models available for upgrade")
                    return current_model
                best = max(candidates, key=lambda m: m.get("size", 0))
                log.info(f"[Worker] Selected upgrade model: {best['name']} ({best.get('size', 0) / 1e9:.1f}GB)")
                return best["name"]
        except Exception as e:
            log.warning(f"[Worker] Failed to discover upgrade model: {e}")
            return current_model

    async def _retry_with_bigger_model(self, mission_id: int, payload: dict[str, Any], result_str: str):
        """Re-enqueue mission with a larger model after schema failure."""
        original_model = payload.get("model", "unknown")
        upgrade_model = await self._get_upgrade_model(original_model)

        payload["_retry_count"] = payload.get("_retry_count", 0) + 1
        payload["model"] = upgrade_model

        retry_count = payload["_retry_count"]
        log.warning(f"[Worker] Upgrading mission {mission_id} from {original_model} → {upgrade_model} (retry {retry_count})")

        async with _shared_http_client() as client:
            await client.patch(
                f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                json={"status": "executing", "result": f"Retrying with larger model ({upgrade_model}). Previous attempt: {result_str[:200]}"},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )

        await self.job_queue.enqueue_job("raven_resume", payload)
        log.info(f"[Worker] Mission {mission_id} re-enqueued with {upgrade_model} as retry {retry_count}")

    async def _trigger_tts_callback(self, payload: dict[str, Any], message: str):
        """Proactively broadcast result via TTS."""
        try:
            creds = payload.get("creds", {})
            user_id = payload.get("user_id")

            device_id = payload.get("device_id")
            if not device_id:
                log.warning("Announcement requested without device_id — skipping")
                return

            # Use execution service's announce endpoint
            announce_payload = {
                "user_context": creds,
                "message": message,
                "entity_id": device_id,
                "volume": 0.6
            }

            async with _shared_http_client() as client:
                await client.post(
                    f"{EXECUTION_SVC}/execute/announce",
                    json=announce_payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
            log.info(f"Proactive TTS callback triggered for {user_id}")
        except Exception as e:
            log.error(f"TTS Callback failed: {e}")

    async def _trigger_talk_callback(self, payload: dict[str, Any], message: str):
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

            async with _shared_http_client() as client:
                await client.post(
                    f"{EXECUTION_SVC}/execute/talk",
                    json=talk_payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
            log.info(f"Talk callback sent to room {token}")
        except Exception as e:
            log.error(f"Talk Callback failed: {e}")

    async def perform_health_check(self, error_threshold: int, settings: dict[str, Any]):
        log.info("Performing Raven health check...")
        containers = await self._get_containers()
        if not containers:
            return
        problematic = []
        for c in containers:
            if not isinstance(c, dict) or "name" not in c:
                continue
            errs = await self._get_errors(c["name"])
            if errs and len(errs) >= error_threshold:
                problematic.append({"name": c["name"], "count": len(errs), "sample": errs[:3]})
        if problematic:
            await self.trigger_self_repair(problematic, settings)

    async def _get_containers(self):
        try:
            async with _shared_http_client() as client:
                resp = await client.get(f"{EXECUTION_SVC}/execute/docker_containers", headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=5.0))
                if resp.status == 200:
                    resp_text = await resp.text()
                    resp_json = json.loads(resp_text) if resp_text else {}
                    return resp_json.get("detail", {}).get("containers", [])
        except Exception:
            return []

    async def _get_errors(self, name):
        try:
            payload = {"user_context": {"user": SYSTEM_IDENTITY, "is_admin": True}, "container_name": name, "tail": 100, "filter_level": "ERROR"}
            async with _shared_http_client() as client:
                resp = await client.post(f"{EXECUTION_SVC}/execute/docker_logs", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=5.0))
                if resp.status == 200:
                    resp_text = await resp.text()
                    resp_json = json.loads(resp_text) if resp_text else {}
                    return resp_json.get("detail", {}).get("lines", [])
        except Exception:
            return []

    async def trigger_self_repair(self, problematic, settings):
        coding_model = settings.get("coding_model") or settings.get("ollama_coding_model")
        if not coding_model:
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
                async with _shared_http_client() as client:
                    resp = await client.post(
                        f"{IDENTITY_SVC}/api/raven/missions",
                        json=mission_payload,
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    if resp.status == 200:
                        log.info(f"Mission for {c['name']} successfully pushed to Triage Queue.")
                    else:
                        log.error(f"Failed to push mission to Triage Queue: {await resp.text()}")
            except Exception as e:
                log.error(f"Error pushing to Triage Queue: {e}")

    async def _job_heartbeat(self, job_id: str):
        while self.is_running:
            await asyncio.sleep(30)
            await self.job_queue.heartbeat_job(job_id)

    async def _cleanup_loop(self):
        """Periodic cleanup: HA entity sync, orphaned RAG entries, stale Redis cache keys."""
        CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "300"))  # Default 5 min
        log.info(f"Cleanup loop started (interval={CLEANUP_INTERVAL}s).")

        while self.is_running:
            try:
                await self._run_cleanup()
            except Exception as e:
                log.error(f"Cleanup loop error: {e}")

            # Sleep in small increments to allow signal handling
            for _ in range(CLEANUP_INTERVAL):
                if not self.is_running:
                    break
                await asyncio.sleep(1)

        log.info("Cleanup loop stopped.")

    async def _run_cleanup(self):
        """Single cleanup pass: sync HA entities, prune orphans, clean Redis cache."""
        from services.gateway.ha_state_cache import get_redis

        # 1. Fetch all users from Identity to sync their HA entities
        try:
            async with _shared_http_client() as client:
                resp = await client.get(
                    f"{IDENTITY_SVC}/api/users",
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status != 200:
                    return
                users = await resp.json()
        except Exception as e:
            log.error(f"Cleanup: failed to fetch users: {e}")
            return

        r = get_redis()
        total_orphaned = 0

        for user in users:
            username = user.get("username", "")
            if not username:
                continue

            # Resolve decrypted HA credentials for this user. UserRead (returned
            # by /api/users) intentionally omits encrypted tokens, so we use
            # /api/resolve — the system's source of truth for credentials,
            # including any Config DB overrides. This is how the default system
            # user (ID 1) picks up the HA account configured in the UI.
            try:
                async with _shared_http_client() as client:
                    cred_resp = await client.post(
                        f"{IDENTITY_SVC}/api/resolve",
                        json={"rag_user": username},
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                    )
                    if cred_resp.status != 200:
                        continue
                    creds = await cred_resp.json()
            except Exception as e:
                log.warning(f"Cleanup: failed to resolve creds for {username}: {e}")
                continue

            ha_url = creds.get("ha_url")
            ha_token = creds.get("ha_token")
            if not ha_url or not ha_token:
                log.debug(f"Cleanup: no HA credentials for {username}, skipping sync.")
                continue

            try:
                # Fetch live entities from HA
                async with _shared_http_client() as client:
                    resp = await client.get(
                        f"{EXECUTION_SVC}/discovery/entities",
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    if resp.status != 200:
                        continue
                    resp_text = await resp.text()
                    data = json.loads(resp_text) if resp_text else {}
                    entities = data.get("entities", []) if isinstance(data, dict) else []

                if not entities:
                    continue

                # Sync to RAG (triggers orphan cleanup in RAG collection)
                async with _shared_http_client() as client:
                    sync_resp = await client.post(
                        f"{RAG_SVC}/rag/sync/ha",
                        json={"entities": entities, "user_id": username},
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    if sync_resp.status == 200:
                        sync_text = await sync_resp.text()
                        result = json.loads(sync_text) if sync_text else {}
                        orphaned = result.get("orphaned_entity_ids", [])
                        total_orphaned += len(orphaned)

                        # Clean up orphaned Redis cache keys
                        for eid in orphaned:
                            with suppress(Exception):
                                r.delete(f"ha:state:{eid}")

                # Update Redis cache with fresh states
                from services.gateway.ha_state_cache import cache_all_states
                cache_all_states(entities)

            except Exception as e:
                log.error(f"Cleanup: failed for user {username}: {e}")

        if total_orphaned > 0:
            log.info(f"Cleanup pass complete: removed {total_orphaned} orphaned entity entries across all users")

# Global instance
worker = RavenWorker()
