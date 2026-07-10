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
| 3 | Reuse cache in `ha_state_cache` / `media_device_cache` | [ ] pending | |
| 4 | Execution bridge-network spike (validate `.local` DNS) | [ ] pending | |
| 4 | De-duplicate `*_SVC_URL` env via shared `env_file` | [ ] pending | |
| 4 | Soften `recreate_http_client` host refresh | [ ] pending | |

---

## Verification per phase
- `ruff check services/gateway services/execution`
- `pytest services/tests`
- `pytest tests/integration` (smoke)
- Phase 2/4: full `e2e-tests.yml` run against `docker-compose.test.yml`; DNS failover (`.local`) test passes.
- Each phase independently deployable; keep `microservices` green.
