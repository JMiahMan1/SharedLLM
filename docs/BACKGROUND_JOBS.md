# Background jobs

Every periodic or queue-driven job in the system, the service that owns it,
how often it runs and what it does. Each loop catches its own errors and keeps
going; a failed pass is logged at WARNING or ERROR with the exception type.

Verify a job live (read-only) with `docker logs --since 6h sharedllm_<service>`.
Several services log at WARNING only, so a healthy loop can be silent.

## Gateway (`services/gateway/background_worker.py`, own event loop in thread `raven-worker`)

| Job | Function | Interval | What it does |
|---|---|---|---|
| Health scan | `RavenWorker._health_loop` | `raven_scan_interval` setting, else `RAVEN_CHECK_INTERVAL` (300 s) | Reads the last 100 ERROR lines of each container via execution `/execute/docker_logs`; at `raven_error_threshold` (5) errors it files an `admin_fix` mission (30 min cooldown per container, skipped while one is pending). Paused by `raven_suspended`. |
| Inference queue | `RavenWorker._inference_loop` | polls every 1 s when idle | Claims Raven/Talk jobs from Redis `raven:inference_queue` one at a time, re-queues jobs whose 120 s lease expired (dead-letter after 3), and every 60 s fails missions left `queued`/`executing` with no backing job for 15 min. |
| Talk monitor | `RavenWorker._talk_monitor_loop` | 10 s | Reads the system account's Talk rooms and enqueues an inference job for each new `@jarvis` message typed in another Talk app. Questions sent from the Family app are answered by execution's `send` and claimed in Redis (`jarvis:talk:answered:<token>:<id>`, 24 h); the monitor skips claimed ones, so nobody gets two answers. Cursor per room: `jarvis:talk:last_msg:<token>`. |
| Chat notify | `RavenWorker._chat_notify_loop` → `chat_notify.check_once` | 5 s (`POLL_SECONDS`) | For each family member with Nextcloud, reads their own Talk conversation list and posts a notice to telemetry for a new, unread message from someone else. A Jarvis answer (an `assistant` envelope, posted with the asker's login) is announced as "Jarvis" to everyone in the room, the asker included. A message counts as handled (`chat:notify:seen:<user>`) only once telemetry accepts it; the `once` key prevents duplicates on retry. |
| Cleanup / HA sync | `RavenWorker._cleanup_loop` | 300 s after each pass (`CLEANUP_INTERVAL_SECONDS`) | Per user with HA credentials: fetches entities via execution `/discovery/entities`, posts them to RAG `/rag/sync/ha` (which removes orphans and re-embeds only entities whose text changed), and refreshes the `ha:state:*` cache (60 s TTL). |

The gateway lifespan also warms RAG's lesson cache once at startup
(`_warm_rag_lesson_cache`).

## Other services

| Service | Job | Function | Interval | What it does |
|---|---|---|---|---|
| automation | Timer dispatch | `services/automation/main.py:scheduler_loop` | 1-60 s (sleeps until the next due timer, capped at 60 s) | Fires due `timer:*` keys at their target service with retries and run history; reschedules recurring timers. A malformed timer is logged and skipped. A fired timer (`/execute/trigger`) always puts a notice on the owner's phone (telemetry `once` key per firing) and also speaks on `target_device` when it names one. Status: `GET /api/automation/status`. |
| telemetry | Report scheduler | `services/telemetry/main.py:scheduler_loop` | 30 s | Queues `tel:job:*` reports whose `next_run_at` has passed. |
| telemetry | Report worker | `services/telemetry/main.py:worker_loop` | 20 s | Generates queued reports (deferred while Alpaca is busy, retried with backoff) and pushes a notification. |
| execution | Energy telemetry | `services/execution/main.py:telemetry_ingestion_loop` | 60 s | Reads each power-tracked HA entity and ingests a usage snapshot. |
| execution | Temp media prune | `services/execution/main.py:temp_media_prune_loop` | 3600 s | Deletes stale generated video / TTS files. |
| execution | Watch glance | `services/execution/watch_glance.py:glance_loop` | 120 s | Pushes a "who is driving / out" line to paired ESPHome watches. |
| execution | Leave-by reminders | `services/execution/leave_by.py:leave_by_loop` | 300 s | Sends a notice when it is time to leave for a calendar event with a location (drive time from OSRM). Repeating Nextcloud events count once per occurrence. |
| execution | Presence | `services/execution/presence.py:init_presence_tracker` | MQTT stream | ESPresense BLE presence into Redis; only when `MQTT_HOST`/`MQTT_PORT` are set. |
| geo | Idle trip sweep | `services/geo/main.py:_idle_trip_sweeper` | 60 s | Finishes drives idle for 5 min (`geo:active_trip:*`) so a phone that goes quiet does not lose the trip. |
| bible | Reading reminders | `services/bible/reminders.py:reminder_loop` | 300 s | Sends the daily reading reminder after each reader's chosen time, once a day, if unread. |
| control_plane | Sandbox reaper | `services/control_plane/main.py:_reaper_loop` | `SANDBOX_REAP_INTERVAL` (3600 s) | Removes `wsbox-*` containers and networks with no registered workspace; skips the sweep if the workspace list cannot be read. |
| control_plane | OSRM map | `services/control_plane/main.py:_osrm_map_loop` | `OSRM_MAP_CHECK_INTERVAL` (3600 s) | Extends the road map to regions geo reports as needed and redeploys OSRM after a newer build. Needs a GitHub token. |
| dns | Mapping refresh | `services/dns/main.py:refresh_dns_mappings` | `DNS_REFRESH_INTERVAL` (30 s) | Reloads `dns_mappings` and failover settings from Identity. |
| dns | Docker watcher | `services/dns/main.py:DockerWatcher.start` | Docker event stream | Registers container names to IPs as containers start. |
| logging | Retention | `services/logging/main.py:retention_cleanup_task` | 3600 s | Drops `logs:entries` older than `LOG_RETENTION_DAYS` (30). Writes also trim to `LOG_MAX_ENTRIES` (10 000). |
| workspace_runtime | Nextcloud sync | `services/workspace_runtime/sync_ops.py:periodic_sync_loop` | `WORKSPACE_NEXTCLOUD_SYNC_INTERVAL_SECONDS` (300 s; 0 = off) | Two-way sync of every Nextcloud-mode workspace; also a debounced sync after each local write. |
| workspace_runtime | Toolchain report | `services/execution/toolchain.py:sync_toolchain_to_rag` | once at startup | Reports the sandbox shell's CLI binaries to RAG. |
| capabilities-sync | Capability index | `scripts/index_capabilities.py` | once per deploy (one-shot container) | Posts tool schemas to RAG `/rag/sync/capabilities`. |

Not periodic, by design: storage indexing (`POST /index/full`, started from the
UI or a tool), execution device discovery (on demand), RAG reindex, and the
gateway media event hubs (opened per subscriber, closed after 60 s idle).
Identity has no background jobs.

## Alpaca desktop (`audio_server.py` in the alpaca repo, container `audio-server`)

| Job | Function | Interval | What it does |
|---|---|---|---|
| Music job queue | `_music_worker` | event-driven | Runs queued music/song jobs one at a time; finished jobs are kept for `MUSIC_JOB_KEEP_S` (env `AUDIO_MUSIC_JOB_KEEP_S`, 1800 s). |
| Idle unloader | `_idle_unloader` → `_idle_unload_once` | 15 s | Unloads TTS or music models idle longer than `AUDIO_IDLE_UNLOAD_S` (180 s) to give VRAM back. |
