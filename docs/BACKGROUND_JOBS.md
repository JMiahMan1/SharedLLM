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
| Inference queue | `RavenWorker._inference_loop` | polls every 1 s when idle | Claims jobs from Redis `raven:inference_queue` and runs each in its own task, capped at `MAX_INFLIGHT_INFERENCE_JOBS`; Tier 2 chat jobs share `TIER2_SEMAPHORE` (3 slots) and Raven missions stay serialized by `TIER3_LOCK`. Re-queues jobs whose 120 s lease expired (dead-letter after 3), and every 60 s fails missions left `queued`/`executing` with no backing job for 15 min. Queued chat jobs get the user's recent history attached, and completed turns are written back. |
| Talk monitor | `RavenWorker._talk_monitor_loop` | 10 s | Reads the system account's Talk rooms and enqueues an inference job for each new `@jarvis` message typed in another Talk app. Questions sent from the Family app are answered by execution's `send` and claimed in Redis (`jarvis:talk:answered:<token>:<id>`, 24 h); the monitor skips claimed ones, so nobody gets two answers. Cursor per room: `jarvis:talk:last_msg:<token>`. |
| Chat notify | `RavenWorker._chat_notify_loop` → `chat_notify.check_once` | 5 s (`POLL_SECONDS`) | For each family member with Nextcloud, reads their own Talk conversation list and posts a notice to telemetry for a new, unread message from someone else. A Jarvis answer (an `assistant` envelope, posted with the asker's login) is announced as "Jarvis" to everyone in the room, the asker included. A message counts as handled (`chat:notify:seen:<user>`) only once telemetry accepts it; the `once` key prevents duplicates on retry. |
| Cleanup / HA sync | `RavenWorker._cleanup_loop` | 300 s after each pass (`CLEANUP_INTERVAL_SECONDS`) | Per user with HA credentials: fetches entities via execution `/discovery/entities`, posts them to RAG `/rag/sync/ha` (which removes orphans and re-embeds only entities whose text changed), and refreshes the `ha:state:*` cache (60 s TTL). |
| Admin alerts | `RavenWorker._admin_alerts_loop` → `admin_alerts.check_once` | 60 s (`CHECK_SECONDS`), first pass 60 s after start | Service down or back up and desktop GPU services unreachable or back; see Admin alerts below. |
| Song notices | `RavenWorker._songs_loop` → `notices.check_songs_once` | 20 s (`SONG_POLL_SECONDS`) | Asks the audio server about each song in `jobs:songs:pending` (written by `POST /api/music/songs`, removed by cancel) and sends a `jobs` notice to whoever asked when it is done ("expires in 30 minutes"). Failed, cancelled, missing or 2 h old songs are dropped; a done song stays pending until telemetry accepts its notice (`once` per song). |

The gateway lifespan also warms RAG's lesson cache once at startup
(`_warm_rag_lesson_cache`).

## Other services

| Service | Job | Function | Interval | What it does |
|---|---|---|---|---|
| automation | Timer dispatch | `services/automation/main.py:scheduler_loop` | 1-60 s (sleeps until the next due timer, capped at 60 s) | Fires due `timer:*` keys at their target service with retries and run history; reschedules recurring timers. A malformed timer is logged and skipped. A fired timer (`/execute/trigger`) always puts a notice on the owner's phone (telemetry `once` key per firing) and also speaks on `target_device` when it names one. Status: `GET /api/automation/status`. |
| telemetry | Report scheduler | `services/telemetry/main.py:scheduler_loop` | 30 s | Queues `tel:job:*` reports whose `next_run_at` has passed. |
| telemetry | Report worker | `services/telemetry/main.py:worker_loop` | 20 s | Generates queued reports (deferred while Alpaca is busy, retried with backoff) and sends a `reports` notice through `deliver_notice`, the same path as `/api/telemetry/notify`. |
| execution | Energy telemetry | `services/execution/main.py:telemetry_ingestion_loop` | 60 s | Reads each power-tracked HA entity and ingests a usage snapshot. |
| execution | Temp media prune | `services/execution/main.py:temp_media_prune_loop` | 3600 s | Deletes stale generated video / TTS files. |
| execution | Watch glance | `services/execution/watch_glance.py:glance_loop` | 120 s | Pushes a "who is driving / out" line to paired ESPHome watches. |
| execution | Leave-by reminders | `services/execution/leave_by.py:leave_by_loop` | 300 s | Sends a notice when it is time to leave for a calendar event with a location (drive time from OSRM). Repeating Nextcloud events count once per occurrence. |
| execution | Calendar reminders | `services/execution/calendar_reminders.py:calendar_reminder_loop` | 120 s | For every family member, reads their own events (the leave-by reading and ownership rule) and sends a `calendar` notice `reminder_minutes` before each one (calendar settings, default 30, 0 = off), and one at 8:00 on each day of an all-day event. Times use the person's `timezone` calendar setting, else the household's. `once` per event start (per day for all-day). |
| execution | Home alerts | `services/execution/home_alerts.py:home_alert_loop` | 15 s | Reads all Home Assistant states with the system account, only when the `home_alert_entities` setting maps something (re-read every 60 s), and sends `home` notices for doorbell, motion while nobody is home, garage open too long, door unlocked at night, washer or dryer done and water leak. Appliance runs are tracked in memory, so a restart mid-cycle misses that one notice. See docs/INTEGRATIONS.md, Home alerts. |
| execution | Presence | `services/execution/presence.py:init_presence_tracker` | MQTT stream | ESPresense BLE presence into Redis; only when `MQTT_HOST`/`MQTT_PORT` are set. |
| geo | Idle trip sweep | `services/geo/main.py:_idle_trip_sweeper` | 60 s | Finishes drives idle for 15 min (`geo:active_trip:*`) so a phone that goes quiet does not lose the trip, while ordinary stops stay part of the same drive. |
| geo | Auto workout sweep | `services/geo/main.py:_auto_workout_sweeper` | 300 s | Scans the last 12 hours of breadcrumbs for walking, running and cycling. Saves pending `auto_detected` workouts that do not overlap a drive, manual workout or dismissed candidate. Running/paused workouts/trips pause the sweep. |
| geo | Daily fuel prices | `services/geo/main.py:_fuel_price_sweeper` | hourly check, once per day | Refreshes each vehicle's `cost_per_gallon` from AAA using its saved ZIP/city or the home zone. Diesel uses the diesel average, gasoline/hybrid regular, electric is skipped; a failed lookup leaves the old price. |
| geo | Notice sweep | `services/geo/main.py:_notice_sweeper` | 300 s (`NOTICE_SWEEP_S`) | Three jobs per pass: moves anyone who had geo's old presence switch off (`geo:presence_alerts`) to `family_presence=false` in identity, then drops the entry; sends one `family_safety` notice per long stop (`geo:stop:<user>`, `LONG_STOP_HOURS` 2 h somewhere that is not a zone, `LONG_STOP_ACTIVE_HOURS` 8-21 local); and on Sunday from `WEEKLY_SUMMARY_HOUR` (19) a `health` step summary per person, once per week (`geo:notice:week:*`). See GEO_SERVICE.md and HEALTH_STEPS.md. |
| bible | Reading reminders | `services/bible/reminders.py:reminder_loop` | 300 s | Sends the daily reading reminder after each reader's chosen time, once a day, if unread. |
| control_plane | Sandbox reaper | `services/control_plane/main.py:_reaper_loop` | `SANDBOX_REAP_INTERVAL` (3600 s) | Removes `wsbox-*` containers and networks with no registered workspace; skips the sweep if the workspace list cannot be read. |
| control_plane | OSRM map | `services/control_plane/main.py:_osrm_map_loop` | `OSRM_MAP_CHECK_INTERVAL` (3600 s) | Extends the road map to regions geo reports as needed and redeploys OSRM after a newer build. Needs a GitHub token. |
| control_plane | Disk alerts | `services/control_plane/main.py:_disk_alert_loop` → `disk_alerts.tick` | `DISK_ALERT_INTERVAL` (3600 s), first check at start | Disk nearly full notices to admins; see Admin alerts below. |
| control_plane | Router monitor | `services/control_plane/network_routes.py:loop` → `router_monitor.Monitor.tick` | `ROUTER_CHECK_INTERVAL` (60 s), first check at start | Internet and router reachability, Wi-Fi airtime, reconnects, DHCP and conntrack; alerts to admins, status and the router log for Admin > Network (docs/NETWORK.md). |
| dns | Mapping refresh | `services/dns/main.py:refresh_dns_mappings` | `DNS_REFRESH_INTERVAL` (30 s) | Reloads `dns_mappings` and failover settings from Identity. |
| dns | Docker watcher | `services/dns/main.py:DockerWatcher.start` | Docker event stream | Registers container names to IPs as containers start. |
| logging | Retention | `services/logging/main.py:retention_cleanup_task` | 3600 s | Drops `logs:entries` older than `LOG_RETENTION_DAYS` (30). Writes also trim to `LOG_MAX_ENTRIES` (10 000). |
| logging | Error alerts | `services/logging/main.py:error_alert_task` → `error_alerts.check_once` | 60 s | Error spike notices to admins; see Admin alerts below. |
| workspace_runtime | Nextcloud sync | `services/workspace_runtime/sync_ops.py:periodic_sync_loop` | `WORKSPACE_NEXTCLOUD_SYNC_INTERVAL_SECONDS` (300 s; 0 = off) | Two-way sync of every Nextcloud-mode workspace; also a debounced sync after each local write. |
| workspace_runtime | Toolchain report | `services/execution/toolchain.py:sync_toolchain_to_rag` | once at startup | Reports the sandbox shell's CLI binaries to RAG. |
| capabilities-sync | Capability index | `scripts/index_capabilities.py` | once per deploy (one-shot container) | Posts tool schemas to RAG `/rag/sync/capabilities`. |

Not periodic, by design: storage indexing (`POST /index/full`, started from the
UI or a tool), execution device discovery (on demand), RAG reindex, and the
gateway media event hubs (opened per subscriber, closed after 60 s idle).
Identity has no background jobs.

## Admin alerts

Notices to every admin's phone (category `admin`, user `@admins`; contract in
`docs/PUSH.md`). Each fires on a change of state only, and the later notice of
a pair carries the same tag, so it replaces the earlier one on the phone.
Thresholds are Identity settings (`docs/CONFIG.md`).

| Alert | Where | Fires when | Tag |
|---|---|---|---|
| Service down / back up | gateway `admin_alerts.service_transitions` | A service in the gateway's `/health/ready` list (the dashboard's) fails 2 checks in a row; back up on the next good check | `admin:service:<name>` |
| Desktop GPU services | gateway `admin_alerts.desktop_transitions` | `llm_local_url`, `vision_ocr_proxy_url`, `ALPACA_AUDIO_URL`, `ALPACA_WEB_URL`, `ALPACA_ARCADE_URL` (whichever are set) give no HTTP answer for `alert_desktop_down_minutes` (10). One notice names all of them and is updated as the set changes; "back" when it empties | `admin:desktop` |
| Error spike | logging `error_alerts.spike_notices` | ERROR entries in the last 5 min exceed `alert_error_spike_threshold` (20), or one error (service plus readable message, numbers and ids ignored) exceeds `alert_error_repeat_threshold` (10). At most once per 30 min per error (Redis `admin:alerts:errors:<fingerprint>`). The body uses the dashboard's readable message; Open goes to the dashboard | `admin:errors:<fingerprint>` |
| Disk nearly full / recovered | control_plane `disk_alerts.disk_transitions` | Free space on `./data`, the workspaces or `/var/lib/docker` (mounted read-only under `/hostfs`; `DISK_ALERT_PATHS`; one check per filesystem) drops below `alert_disk_free_percent` (10), and again when it is back above | `admin:disk:<disk>` |
| Home network | control_plane `router_monitor` | Internet down or back, router unreachable or restarted, a device crowding a Wi-Fi channel, a congested channel, a device reconnecting over and over, DHCP pool, conntrack or router memory nearly full; thresholds in docs/NETWORK.md. State is kept in Redis, so a restart does not repeat alerts | `admin:network:<check>` |
| Deploy finished / failed | `scripts/deploy_remote.sh` (EXIT trap) | Every run that got past choosing services: the commit, branch and services, or the step that failed and the server's `[FAIL]` line | `admin:deploy` |

The gateway keeps its alert state in Redis (`admin:alerts:gateway`), so a
restart neither repeats a notice nor loses the "back up" that answers one; a
notice telemetry did not accept is resent on the next pass. The control plane
keeps the disk state in memory: after a restart a disk that is still low is
reported once more.

There is no backup job in the stack (no cron, timer or service runs one; the
volume inventory in execution only prints example commands). A backup job
added later reports its failure with `scripts/notify_admins.sh`.

## Alpaca desktop (`audio_server.py` in the alpaca repo, container `audio-server`)

| Job | Function | Interval | What it does |
|---|---|---|---|
| Music job queue | `_music_worker` | event-driven | Runs queued music/song jobs one at a time; finished jobs are kept for `MUSIC_JOB_KEEP_S` (env `AUDIO_MUSIC_JOB_KEEP_S`, 1800 s). |
| Idle unloader | `_idle_unloader` → `_idle_unload_once` | 15 s | Unloads TTS or music models idle longer than `AUDIO_IDLE_UNLOAD_S` (180 s) to give VRAM back. |
