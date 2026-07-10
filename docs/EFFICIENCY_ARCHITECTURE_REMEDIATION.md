# Efficiency & Architecture Remediation Plan

> **Source review:** Full codebase audit (architecture, hardcoded values, Python best-practices, frontend).
> **Scope (agreed):** Production `services/**` + `docker-compose.yml` / `Caddyfile` / `.github/workflows`. Scripts & tests excluded.
> **Priority:** Efficiency / Architecture. Hardcoded-IP/secret cleanup and frontend polish are **deferred** (later opt-in phases).
> **Branch discipline:** All work lands on `microservices`. Verify green via `soa_tests.yml` + `e2e-tests.yml` before merge.

---

## Findings summary (by phase)

### Phase 1 — HTTP session / connection reuse
- Gateway bypasses its own pooled client with throwaway `aiohttp.ClientSession()` per call:
  - `emit_log` — `services/gateway/main.py:808`
  - `get_api_logs` — `services/gateway/main.py:820`
  - `fetch_global_setting` — `services/gateway/main.py:223`
  - other ad-hoc sessions — `main.py:1761, 3666`, `background_worker.py`
  - Fix: route through `get_http_client()` / `borrow_http_client()` (`main.py:461,513`).
- Execution shared client bugs (`services/execution/http_client.py`):
  - **Inverted TLS `verify`** at `:104-106` (`verify=False` verifies; `verify=True` does not) — security + correctness.
  - **Double body read** at `:78-79` (`resp.text()` + `resp.read()`).
  - **Deprecated `get_event_loop()`** at `:91` → `asyncio.get_running_loop()`.
- Execution sub-clients open a fresh session per call: `ha_client.py`, `abs_client.py`, `mass_client.py`, `mass_ha_client.py`.

### Phase 2 — Replace polling loops with event/backoff
- Redis kill/pause poll every 5s — `services/gateway/agent_loop.py:1141` → Redis pub/sub or blocking pop.
- LLM slot wait poll every 1s — `services/gateway/llm_providers.py:78` → `asyncio.Event` / capped backoff; interval env-configurable.
- Job-status poll every 1s — `services/gateway/main.py:3085` → webhook/callback or Redis list-block.
- `background_worker.py` monitor loops (10/30/300s) — reuse shared client; use `asyncio` tasks.
- DNS refresh every 30s — `services/dns/main.py:716`, `dns-proxy` — keep, make interval an env var.

### Phase 3 — Cache repeated identity/settings fetches
- `resolve_identity` (`main.py:1683`) and `get_all_settings` run every chat; only `prompts.py:54` has a 30s TTL.
- Add shared in-memory TTL cache for identity / global settings / prompts; reuse in `ha_state_cache.py` + `media_device_cache.py`; invalidate on write; TTLs env-configurable.

### Phase 4 — Network isolation & config hygiene (higher risk, optional)
- Execution `network_mode: host` (`docker-compose.yml:113`) → move to bridge, reach via `execution:8003` (as `docker-compose.test.yml` / `test.env` already do). Validate `.local` DNS sidecar (`services/execution/dns_resolver.py`) first as a spike.
- De-duplicate `*_SVC_URL` env repeated in ~8 compose blocks → shared `env_file` / compose `x-` extension.
- Soften `recreate_http_client` (`main.py:478`) — refresh only affected host, not all keepalive connections.

### Phase 5 — Residual findings (post-remediation rescan)
> Added during a full-codebase rescan after Phases 1–4 were marked complete. These are
> genuine misses: the same bug *classes* fixed in earlier phases were not propagated to
> every service. Marked `[ ] pending` — not yet remediated.
- **`asyncio.get_event_loop()` not fully replaced (same class as Phase 1 row 54).** Phase 1 only fixed `execution/http_client.py`, but production code still calls the deprecated/invalid `asyncio.get_event_loop()`:
  - `services/dns-proxy/main.py:74, 296, 650` (`loop = asyncio.get_event_loop()`)
  - `services/dns/main.py:744`
  - `services/gateway/ma_ws_client.py:242`
  - `services/gateway/resolver.py:20` (`asyncio.get_event_loop().run_in_executor` → should be `get_running_loop()`)
  - (Gateway `main.py` / `agent_loop.py` use `asyncio.get_event_loop().time()` only for elapsed-timing — works but should use `time.monotonic()`.)
  - Fix: `asyncio.get_running_loop()` (or `time.monotonic()` for timing).
- **Per-call `aiohttp.ClientSession()` in non-gateway/execution services (same class as Phase 1 rows 55/59).** The pooled-client pattern (`get_http_client()` / `shared_http_client()` / execution sub-client `_*_session()`) was only propagated to gateway + execution `http_client`/sub-clients. Other services still open a fresh session per request:
  - `services/storage/main.py:52,127,195`, `services/storage/nextcloud_client.py:37` (module-level session — OK, but no pooling/TTL)
  - `services/workspace_runtime/main.py:57,510,1182,1368,2828`
  - `services/identity/main.py:867,1117,1138,1774,1795`
  - `services/automation/main.py:52` (per-timer-trigger session)
  - `services/dns/main.py:305,675`, `services/shim_users.py:28`, `services/execution/websearch.py:16,41`, `services/execution/presence.py:238,300`
  - `services/execution/main.py:129,183,219,604,957,983,1318,1867,2206,2237,2528,2548` + `services/execution/handlers/*` (workspace, learning, webos, video, browser, talk, ha_config, audiobookshelf, git, roku, network_scan, intercom, groups, telemetry, ha_client)
  - These are lower-frequency than gateway hot paths, but still per-request churn and inconsistent with the remediation standard. Introduce/propagate a module-level pooled client (or share `get_http_client()`) and route through it.
- **`redis.from_url()` churn contradicts Phase 2 row 60's "removes per-iteration churn" claim.** Row 60 only migrated the kill/pause pub/sub path to `_get_redis_cmd()` / `get_redis()`. Remaining per-iteration / per-request `redis.from_url()`:
  - `services/gateway/background_worker.py:207,225` — `_talk_monitor_loop` recreates Redis every 10s iteration (intentional for Redis-restart resilience; keep but note exception)
  - `services/gateway/background_worker.py:382` — per-mission `_monitor_kill` (short-lived, acceptable)
  - `services/gateway/agent_loop.py:773,798,1076,1238,1331,1352` — per-iteration/per-stream Redis
  - `services/gateway/main.py:3093,4713,4736,4759,4857,4915` — per-request Redis (a shared `get_redis()` helper exists at `cache.py:111` / `history.py:16` but these sites don't use it)
  - `services/automation/main.py:28` — module-level (fine, but no shared pool)
  - Fix: route through the existing `get_redis()` helper / `_get_redis_cmd()` everywhere; document the `_talk_monitor_loop` exception.
- **Automation 5s timer-poller is a busy Redis key-scan loop (same class as Phase 2 row 62).** `services/automation/main.py:30` `while True` + `await asyncio.sleep(SCHEDULER_INTERVAL)` (5s) scans `timer:*` keys every iteration and fires due timers (also opening a fresh `aiohttp.ClientSession` per trigger). Not covered by Phase 2. Fix: event-driven via Redis keyspace notifications, or a sorted-set of `{fire_ts: timer_id}` + `BZPOPMIN`-style blocking wait / capped backoff (mirrors the job-status webhook work in row 62).

### Deferred (opt-in later)
- **Hardcoded IPs/secrets/config:** `192.168.2.205`, `change-me-in-production` defaults, centralize `UI_URL`/`GATEWAY_URL`/`MA_URL`.
- **Frontend:** storage-key constants, interceptor `console.log`, `exhaustive-deps` disables, progress-bar dedupe.
- **Correctness bugs off the efficiency path:** sync-Redis-in-async (`history.py`), `await resp.json().get()` coroutine bugs, blocking `subprocess.run` in async execution endpoints.

---

## Progress

| Phase | Task | Status | PR / Commit |
|-------|------|--------|-------------|
| 1 | Fix inverted TLS `verify` in `execution/http_client.py` | [x] done | `services/execution/http_client.py` |
| 1 | Remove double body read in `execution/http_client.py` | [x] done | `services/execution/http_client.py` |
| 1 | Replace `get_event_loop()` with `get_running_loop()` | [x] done | `services/execution/http_client.py` |
| 1 | Route flagged gateway hot paths through `get_http_client()` | [x] done | `emit_log`, `get_api_logs`, `fetch_global_setting`, `_proxy_execution_with_identity`, `fetch_ha_entities`, `proxy_tags` in `main.py` (`shared_http_client()` helper) |
| 1 | Route `background_worker.py` polling loops through shared pool | [x] done | `services/gateway/background_worker.py` (`_shared_http_client()`) |
| 1 | Route `ha_state_cache.py` live-fetch through shared pool | [x] done | `services/gateway/ha_state_cache.py` |
| 1 | Bulk-convert remaining ~70 per-endpoint gateway handlers (preserve per-call timeouts) | [x] done | `main.py` (~60 `async with aiohttp.ClientSession(...)` handlers → `shared_http_client()`; per-call `timeout=` moved onto each request; 2 long-lived streaming-proxy assignments at `main.py:6001/6867` deliberately left as dedicated sessions — they are `finally`-closed and would tear down the shared pool), `agent_loop.py` (14 sites → in-module `shared_http_client()` borrowing the existing pooled `get_http_client()`; `self.timeout`/headers moved to requests), `orchestrator.py` (5), `llm_providers.py` (3, `self.timeout`/headers moved), `history.py` (2), `prompts.py` (1). `ha_state_cache.py` live-fetch also routed through `shared_http_client()`. |
| 1 | Route execution sub-clients through pooled `get_session()` | [x] done | `ha_client.py`, `abs_client.py`, `mass_client.py`, `mass_ha_client.py` — each gained a `_*_session()` helper (no close; reused) over `get_session(host_of(url))`. Per-call timeouts preserved (`_TIMEOUT` on inner requests; 15s REST / 25s WS on `mass_client`; WS `ws_connect` passes `timeout=`). DNS-staleness risk mitigated: `get_session` uses `ttl_dns_cache=60` + `session.closed` guard (re-resolves on dead connection), and aiohttp re-resolves on next call after a dropped keep-alive — unlike the prior httpx persistent pool (commit 4e776815). `requirements.txt` httpx removed (runtime); tests keep httpx as a client + respx. |
| 2 | Redis kill/pause poll → pub/sub / blocking pop | [x] done | `agent_loop.py` `_await_mission_resume` subscribes to `raven:mission:pause:{id}` + `raven:mission:kill:{id}` (subscribe-then-recheck to avoid lost wakeup); `main.py` `pause_mission`/`resume_mission` publish `PAUSED`/`RESUMED`; shared `_get_redis_cmd()` removes per-iteration `redis.from_url()` churn |
| 2 | LLM slot wait poll → `asyncio.Event` / backoff | [x] done | `llm_providers.py` `_wait_for_slot` now uses capped exponential backoff (`OLLAMA_SLOT_POLL_INTERVAL`→`OLLAMA_SLOT_POLL_MAX`); `/api/ps` has no push API so true `asyncio.Event` isn't feasible — backoff is the viable event-driven approximation |
| 2 | Job-status poll → webhook / Redis list-block | [x] done | `messaging.py` `_publish_status` notifies `raven:job:status:{id}` on every status change; `main.py` `stream_chat_job` SSE waits on pub/sub with a periodic `JOB_STATUS_POLL_INTERVAL` fallback GET |
| 2 | `background_worker` loops use shared client + async tasks | [x] done | Already satisfied in Phase 1 row 56 (all HTTP via `_shared_http_client()`; loops are `asyncio.create_task`) — no throwaway sessions remain |
| 2 | DNS refresh interval env-configurable | [x] done | `dns/main.py` `DNS_REFRESH_INTERVAL` env var (default 30s) replaces hardcoded `asyncio.sleep(30)` |
| 3 | Shared in-memory TTL cache for identity/settings/prompts | [x] done | `services/gateway/cache.py` shared TTL cache (`SETTINGS_CACHE_TTL`/`IDENTITY_CACHE_TTL` env-configurable); `get_all_settings` (orchestrator) and `resolve_identity` (main) now use it; invalidated on settings write (`/api/settings`, `/api/config`, DNS endpoints) and `change-password`. `prompts.py` still has its own 30s cache (consolidation deferred to row below) |
| 3 | Reuse cache in `ha_state_cache` / `media_device_cache` | [x] done | `services/gateway/cache.py` gained `get_redis()` + `redis_cache_get/set/set_many/delete`; `ha_state_cache.py` and `media_device_cache.py` now delegate to these (preserving Redis-backed semantics: 60s HA state TTL, 7d device TTL via `HA_STATE_CACHE_TTL`/`MEDIA_DEVICE_CACHE_TTL` env vars). `ha_state_cache` re-exports `get_redis` for `background_worker.py:746`/`main.py:1833` callers |
| 4 | Execution bridge-network spike (validate `.local` DNS) | [x] done | Validated: execution runs `network_mode: host` so it resolves via the host's systemd-resolved (`resolvectl query jeremiah-home-desktop.local` → `192.168.1.216`); bridge-network services resolve `.local` via SharedLLM's own DNS resolver (`172.26.0.254`, static mapping from Identity) with Caddy fronting routing. Gateway already proven end-to-end (`HTTP 200` from Ollama at `jeremiah-home-desktop.local:11434`). No code change needed — architecture already handles it. |
| 4 | De-duplicate `*_SVC_URL` env via shared `env_file` (network-aware) | [x] done | `docker-compose.yml` (rev 68e): each service sets `NETWORK_MODE` (bridge/host) and references `BRIDGE_*`/`HOST_*` sets from `.env` instead of repeating literal URLs; `services/config.py` `_net_url()` resolves the active set at runtime. `.env` holds both `BRIDGE_*` (docker names) and `HOST_*` (localhost) sets; `seed.py` seeds both into the Config DB. Deployed + verified: gateway `NETWORK_MODE=bridge`→`identity:8001`/`rag:8004`/`redis:6379`; execution `NETWORK_MODE=host`→`localhost:8001/8004`/`localhost:6379`; `/api/tags` + `/api/config/models` return `200`. |
| 4 | Soften `recreate_http_client` host refresh | [x] done | `services/gateway/main.py`: connector now uses `ttl_dns_cache=60` (matches `execution/http_client.py`), so each host's DNS re-resolves independently on its own TTL — a stale host refreshes without tearing down other hosts' pooled connections. Added a `_CLIENT_RECREATE_COOLDOWN` (10s) guard so DNS-failure storms don't repeatedly recreate the entire shared pool. `retry_http_client` still recreates as a last resort. |
| 5 | Replace `asyncio.get_event_loop()` in `dns-proxy`/`dns`/`ma_ws_client`/`resolver` | [ ] pending | Same bug class as Phase 1 row 54; only `execution/http_client.py` was fixed. `dns-proxy/main.py:74,296,650`, `dns/main.py:744`, `ma_ws_client.py:242`, `resolver.py:20` still use deprecated `get_event_loop()`. |
| 5 | Propagate pooled HTTP client to non-gateway/execution services | [ ] pending | `storage`, `workspace_runtime`, `identity`, `automation`, `dns`, `execution/main.py` + `execution/handlers/*`, `websearch`, `presence`, `shim_users` still open per-call `aiohttp.ClientSession()`. Same class as Phase 1 rows 55/59; pattern not propagated. |
| 5 | Route all Redis access through shared `get_redis()` (remove `redis.from_url` churn) | [ ] pending | Phase 2 row 60 only migrated kill/pause path. Remaining per-iteration/per-request `redis.from_url()` in `background_worker.py:207,225,382`, `agent_loop.py:773,798,1076,1238,1331,1352`, `main.py:3093,4713,4736,4759,4857,4915`. A shared `get_redis()` exists (`cache.py:111`, `history.py:16`) but unused at these sites. |
| 5 | Event-drive Automation 5s timer-poller | [ ] pending | `automation/main.py:30` busy `while True` scanning `timer:*` every 5s (also per-trigger `aiohttp.ClientSession`). Same class as Phase 2 row 62 job-status poll; should use keyspace notifications / sorted-set blocking wait. |

---

## Verification per phase
- `ruff check services/gateway services/execution`
- `pytest services/tests`
- `pytest tests/integration` (smoke)
- Phase 2/4: full `e2e-tests.yml` run against `docker-compose.test.yml`; DNS failover (`.local`) test passes.
- Each phase independently deployable; keep `microservices` green.
