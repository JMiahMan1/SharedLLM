# SharedLLM Code Review — 2026-08-22

Scope: all Python services + React UI. Method: parallel deep reads of each service
(entry points, handlers, websocket paths, auth, subprocess/docker/file handling),
findings verified against source with file:line evidence. Report only — no code changed.

---

## Executive Summary

The architecture is sound (tiered inference, capability-gated workspaces, provider
abstraction), and recent hardening work (TTS timeouts/magic bytes, STT verify,
fuzzy-hijack guard, pause/resume race handling) is genuinely solid. However, the
review found **~120 verified issues**, including several critical ones:

| # | Service | Critical finding |
|---|---------|------------------|
| 1 | control_plane | Sandbox reaper calls a nonexistent endpoint → deletes ALL active workspace sandboxes hourly |
| 2 | storage | Zero auth on mirror/write endpoints exposed on host port 8005 → unauthenticated file exfiltration |
| 3 | identity | Any user can self-promote to admin (`PATCH /api/users/me {"is_admin": true}`) |
| 4 | logging | Log read/stream endpoints unauthenticated incl. `user_id=admin` bypass → all users' logs readable |
| 5 | identity | `GET /api/settings/{key}` unauthenticated → plaintext `huggingface_token` readable anonymously |
| 6 | automation | Recurring timers re-fire every second forever (expires_at never advanced) |
| 7 | execution | Path traversal in `resolve_safe_path` (`..` never normalized) — reproduced |
| 8 | execution | `len(resp.content)` TypeError → every Kokoro announcement fails verification — reproduced |
| 9 | execution | Broken bare imports kill TV announce handlers, voice/command, discovery endpoints |
| 10 | gateway | Mission control endpoints have no ownership check; mission WS auth is optional |
| 11 | workspace_runtime | Full service env (INTERNAL_SECRET/FERNET_KEY) injected into every sandbox exec |
| 12 | gateway | Chat history dead for Tier 2/3; inference fully serialized behind Raven worker (G4/G5) |

Recommended order: fix the auth surfaces (#2–#5) first, then the data-loss bugs
(#1, #6), then the execution-service breakages (#7–#9).

---

## 1. Gateway (port 11435)

**Purpose:** Orchestrator & intent classifier. Three tiers: FastPath (semantic match
via nomic embeddings, <100 ms automation commands), Librarian (single-turn LLM +
tools), Raven (autonomous agent loop, sandboxed workspaces). Proxies Music
Assistant (REST + sendspin WS), ABS streaming, STT/TTS, chat history (Redis).
Talks to: Identity (auth/settings/missions), Execution (all tools), RAG, Storage,
Workspace Runtime, Control Plane, Redis, MA/ABS/HA, Ollama/OpenRouter.
Users/UI reach it through Caddy for nearly all `/api/*`, `/v1/*`, websockets.

### Gateway — HIGH
- **G1 BUG/AUTHZ** Mission control has no ownership check — any authenticated user can kill/cancel/pause/refine/delete anyone's missions (`main.py:5740-5743` and same pattern :5800, :5856, :5879, :5905, :5982).
- **G2 BUG/AUTH** Mission stream WebSocket accepts connections without a token (`token: str = ""` with no else-rejection, `main.py:6094-6110`) then replays full mission history — contrast terminal WS which correctly rejects (:8469-8473).
- ~~G3 BUG Chat streaming race~~ **RETRACTED after verification**: `LRANGE` then `ltrim(key, len(chunks), -1)` (`messaging.py:199-208`) keeps everything `LRANGE` did not see, so chunks the producer `RPUSH`es between the two calls survive for the next poll. For the single-consumer drain used here, no tokens are lost. Empirically proven safe by test (`test_g3_lrange_ltrim_drain_is_safe_for_single_consumer`). Only cosmetic issue: the misleading `# Atomic pop all` comment.
- **G4 GAP** Chat history dead in main pipeline: `update_history` only called in time/date + fast-path branches; queued-job path never persists; Tier 2 runs with `short_term = []` placeholder (`orchestrator.py:293`); `extract_and_store_user_facts` has zero callers.
- **G5 DESIGN** All inference serialized behind Raven: worker claims jobs serially (`background_worker.py:271-276`); TIER2_SEMAPHORE(3)/TIER3_LOCK are decorative; one Raven mission head-of-line-blocks every chat job up to its wall-clock cap.

### Gateway — MEDIUM
- **G6 BUG** Streaming retry re-emits already-streamed content: `buf = ""` reset per attempt but partial chunks already delivered via callback (`agent_loop.py:479-518`) → duplicated SSE text.
- **G7 BUG** Job loss → silent truncation (stream gen breaks with no done/[DONE], `main.py:3160-3207`) or bogus "queued" 202 after TTL expiry (non-stream path :3215-3229; TTL 3600 s).
- **G8 BUG** FastPath never checks exec HTTP status → failures reported as "Action completed." (`main.py:2966-2968`); non-JSON error escapes to global handler. Contrast proper formatting in orchestrator.py:790-792.
- **G9 BUG** `DELETE /api/history` reports success while lazy `_redis` global may still be None → delete skipped (`main.py:746-751`, history.py:14-23).
- **G10 BUG** sendspin proxy lifecycle: sibling direction never cancelled when one ends (`asyncio.gather` w/o cancellation, `main.py:7607-7611`; same in terminal WS :8531-8534); MA close logged as ERROR traceback (starlette-vs-websockets exception class mismatch :7578-7581); malformed first frame kills handler outside try (`json.loads` at :7450); hello-timeout branch never closes browser socket (:7508).
- **G11 SEC** Tokens in logs: URL query string not redacted by logging middleware while `?token=` auth is accepted (`main.py:2097` vs :1892-1894); terminal proxy logs raw API key in target_url (:8492-8494).
- **G12 AUTH** `POST /api/stt/transcribe` (:6728) and `/api/voice/command` (:6753) perform no identity resolution → open relays forwarding payloads under gateway INTERNAL_SECRET.
- **G13 BUG** assert-before-check turns missing `audio` form field into 500 instead of intended 400 (`main.py:6731-6738`).
- **G14 BUG** Possible-unbound `data` in `_wait_for_model` raises NameError on non-200 /api/ps, swallowed every 2 s poll → each generate() burns full 120 s deadline (`agent_loop.py:360-391`).
- **G15 BUG** Heartbeat task exception after completion flips completed jobs to failed (finally suppresses only CancelledError, stored exception re-raises → outer fail_job, `background_worker.py:703,724-728,1133-1136`).
- **G16 PERF/BLOCKING** Sync redis client used in async paths: history.py (lrange/rpush/ltrim) and cache.py get/set called from chat fast path & entity enrichment → event-loop stalls under Redis latency (messaging/agent_loop correctly use redis.asyncio).
- **G17 PERF/DESIGN** Uncached per-chat threshold fetch hits Identity every request (5 s timeout, no TTL) and mutates shared matcher state (`main.py:2864-2868`, :224-238) despite SETTINGS_CACHE_TTL infra existing.

### Gateway — LOW
- **G18 DEAD** ~540-line duplicate `AgentLoop` in main.py:2209-2747 with hardcoded mission residue ("IMMEDIATELY apply the WorkspaceFilePatchRequest for get_collection_docs", :2235); `state_machine.py` unreferenced; INFERENCE_LOCK only used inside the dead function.
- **G19 DESIGN** Two divergent `OllamaProvider`s: llm_providers.py returns errors as content ("[PROVIDER ERROR: …]", :177/:204) while agent_loop.py raises RuntimeError (:432/:495) — Tier 2 can treat error strings as assistant answers.
- **G20 BUG** Coroutine objects logged instead of bodies (`resp.text` unawaited: agent_loop.py:4936/:4970, llm_providers.py:333) — useless diagnostics in exactly the failure paths.
- **G21 GAP** `_identity_cache` never evicts expired entries (cache.py:33,86-101) — unbounded growth per key/token seen.
- **G22 DESIGN** Fuzzy/substring tool-name capture remains: regexes like `.*note.*`→noterequest (agent_loop.py:3711-3713) and cutoff=0.6 fuzzy fallback (:3744) can hijack hallucinated names (same class as the fixed repocreate bug).
- **G23 DESIGN** Contradictory mission cap defaults: RAVEN_MAX_TOTAL_SECONDS=1800 (config.py:74-80) vs "raven_max_total_seconds": "14400" (orchestrator.py:42); AgentLoop reads raw Identity values so effective default differs by reader.
- **G24 PERF** Duplicated sequential RAG context assembly: chat_handler runs 4×10 s-capped searches inline (main.py:2997-3014, worst case +40 s) then worker re-runs bounded retrieval again (orchestrator.py:328-552).
- **G25 BUG(minor)** Fire-and-forget tasks without strong references can be GC'd mid-run (main.py:1986,2004,2098,2116; background_worker.py:689).
- **G26 DESIGN** Query-driven model override available to all users, triggerable by prose ("compared with model outputs…"), accepts arbitrary model strings (orchestrator.py:285-288).
- **G27 SEC(minor)** Global exception handler returns tracebacks to clients (main.py:727-735).

---

## 2. Identity (port 8001)

**Purpose:** Users, API keys (HMAC-fingerprint lookup), device assignments,
global settings (Fernet-encrypted at rest), DNS records, Raven missions, widgets,
telemetry. Core contract `POST /api/resolve` exchanges INTERNAL_SECRET for
decrypted per-user credentials with mass_token→admin-ID-1 fallback. Serves UI
Settings/auth/users/devices/widgets via Caddy. Talks to HA and Nextcloud;
every service pulls boot config from it.

### Identity — HIGH
- **I1 BUG/AUTHZ** Privilege escalation via self-update: `is_admin`/`is_system_default` exposed on UserUpdate (schemas.py:121-122) and applied verbatim via setattr loop (main.py:564-603) → any valid API key can become admin.
- **I2 BUG/AUTH** `GET /api/settings/{key}` takes no admin/internal dependency (main.py:1263-1277); masking covers exactly one key; Caddy routes /api/settings* publicly → anonymous read of all raw settings incl. plaintext `huggingface_token` (seed.py:303-315).
- **I3 HIGH/DESIGN** mass_token_enc→admin ID-1 fallback also returns the admin's decrypted session api_key to the caller (main.py:478) → any INTERNAL_SECRET holder obtains admin's key; combined with I1 blast radius is total. Recommendation: never echo api_key; make fallback explicit opt-in; log WARNING (currently INFO, :452).
- **I4 BUG** Password sent as URL query param: bare scalar `new_password` on POST /api/auth/change-password (main.py:853-858) → lands in Caddy/access logs.

### Identity — MEDIUM
- **I5** CORS reflects any origin with allow_credentials=True (main.py:236-242).
- **I6** Non-constant-time secret comparisons throughout (main.py:69,340,345,354,360; seed.py:32) — use hmac.compare_digest.
- **I7 BUG** GitHub branch of test_connection references undefined user/password locals → always fails with NameError, swallowed by broad except (main.py:909-957).
- **I8 DESIGN** Any user can create/delete/revoke any device (no ownership/admin checks, main.py:734-827) — device_id drives resolve_identity (:437-445).
- **I9 BUG** DNS record validation bypass on update when record_type omitted (main.py:1446-1458); create path validates correctly.
- **I10 BUG** SQLite read-modify-write races: telemetry blob append (:2254-2276), device upsert select-then-insert (:808-819) → lost updates / constraint 500s.
- **I11 DESIGN** decrypt() swallows everything returning None (crypto.py:43-47) → rotated FERNET_KEY silently yields empty creds everywhere; no rotation story (key_hash derived from same key).
- **I12 BUG** Settings write accepts arbitrary keys with no allowlist/type checks (main.py:1238-1261) — injected keys distribute to all services via config resolution.
- **I13 GAP** Raven mission CRUD has zero auth (only Depends(get_session)) — mitigated by Caddy routing to gateway, but violates defense-in-depth for bridge-network callers (main.py:1689-1765).

### Identity — LOW
- **I14** No login rate limiting (PBKDF2-260k fine offline; online guesses unthrottled, main.py:831-851).
- **I15 GAP** Voice enrollment is a mock (sha256 prefix stored, never checked; voice matching = username equality, main.py:550-554, 429-435).
- **I16 BUG** Dead migration branch in `_find_user_by_hash` (`if not user.api_key_hash:` unreachable after hash match, :300-302); writes during auth GET paths; UserRead.api_key still in schema (schemas.py:150).
- **I17 GAP** Stub endpoints return fake SUCCESS (intercom_broadcast/announce, telemetry analysis, main.py:2453-2473, 2359-2364).
- **I18 BUG** Widget create can 500: UserWidgetUpdate.user_id passed to UserWidget(**data) which lacks the column (schemas.py:317, main.py:1620-1624).

No SQL injection found (ORM throughout; text() usages static).

---

## 3. Logging (port 8006)

**Purpose:** Structured log ingest (internal-secret gated, sanitized) into Redis
sorted set, retention trim, pub/sub fan-out to WebSockets. UI reads via Caddy
/api/logs*.

- **L1 HIGH/BUG** Read endpoints unauthenticated: GET /api/logs and /api/admin/logs take no dependency, and `user_id == "admin"` bypasses the per-user filter (main.py:142,152-162) → anyone on LAN pulls all users' logs via public route.
- **L2 HIGH/BUG** WebSocket log stream unauthenticated (main.py:214-247).
- **L3 MED/GAP** Redaction misses shapes: exact-match SECRET_FIELD_NAMES omits mass_token/skylight_pass/audiobookshelf_pass/etc.; no MA JWT/generic long-token patterns (main.py:61-64,96-97) — combined with L1 these would be world-readable.
- **L4 MED/GAP** Runtime settings never apply: LOG_RETENTION_DAYS/LOG_MAX_ENTRIES imported by value before resolve_runtime_config mutates them (main.py:18 vs config.py:241) — Settings UI changes require container restart silently.
- **L5 LOW/GAP** Query inefficiency/correctness: fetch limit*10 then filter in Python → short results despite older matches; 50k JSON deserializes/request; no level param; int(value) 500s on garbage (main.py:133-148).
- **L6 LOW/BUG** str(exc) leaked to clients (main.py:57).

Positive: score-based trim bounds memory; duplicate-second entries unique; pubsub cleanup correct.

---

## 4. DNS stack

Deployed: services/dns (15353→53, docker.sock ro, UPSTREAM_DNS, static mappings
from Identity, health-aware multi-IP failover). NOT deployed (dead): dns_sync,
dns-proxy, dns-forwarder.

### services/dns
- **D1 MED/HIGH-impact BUG** Unauthenticated registry injection: POST /registry/register on 0.0.0.0:8009 updates the registry consulted BEFORE upstream → any east-west foothold can spoof identity/caddy records for all services using this resolver; /registry/status leaks full IP map (main.py:225-242,586-592,730-735).
- **D2 MED/BUG** `.docker` suffix off-by-one: `name[:-6]` leaves trailing dot ('.docker' is 7 chars) → feature always misses (main.py:223-224).
- **D3 MED/BUG** Forwarded responses unvalidated: constant TXID 0x1234, no QR/TXID/question checks, recvfrom(512) truncates (main.py:347,373,376-422) → LAN on-path poisoning possible.
- **D4 LOW** No negative answers (silence instead of NXDOMAIN); case-sensitive exact-match lookups (main.py:210,497-503).
- **D5 LOW/DESIGN** Hardcoded host.docker.internal→172.26.0.1 and 8.8.8.8 upstream fallback violate repo rules (main.py:186,247,614).
- **D6 LOW/BUG** Registry never prunes missed containers; put_nowait on asyncio.Queue from non-loop thread (not thread-safe) (main.py:79-86,150).
- **D7 LOW** Five unconditional DEBUG prints per query in hot path (main.py:198-207).

### dns_sync (undeployed)
- Empty-default INTERNAL_SECRET authenticates everyone if env unset (config/dns_sync.py:31,268-269). Sends X-Internal-Secret to arbitrary discovered ip:port on health fallback (:380). Substring port-matching mismaps ("storage" contains "rag" → probes RAG port; :340-346). Dead gateway-upstream branch (:520-521). Constant upstream TXID, response stamped without validation (:529,:579). dnsmasq+python both bind UDP 53 (EADDRINUSE, entrypoint.sh); DNsmasq_PID typo; fail-open health policy (:467).

### dns-proxy (undeployed — non-functional)
- Calls nonexistent `docker_client.get_containers()` (AttributeError every cycle, main.py:233); event client opens AF_UNIX socket and never connects (:70-74). Recommend deletion.

### dns-forwarder (undeployed)
- Cleanest implementation (dnspython, SERVFAIL on bad responses); blocking retry loop serializes queries; UPSTREAM_HOST default references nonexistent compose service.

---

## 5. Workspace Runtime (port 8007)

**Purpose:** Sandboxed dev workspaces for Raven: SQLite registry, file CRUD,
provider sync to Nextcloud, webhook git pull, quarantine on repeated failures.
Git ops run inside per-workspace Docker sandboxes (docker.sock mounted);
WebSocket PTY terminals. All endpoints internal-secret gated; redaction ASGI
middleware. Exposed raw via Caddy :8007 and /api/workspace*, /api/webhook*.

### Workspace Runtime — HIGH
- **W1 HIGH/BUG** pytest and lint run on the privileged HOST, not in sandbox: `_run_command` = subprocess.run(cwd=workspace_path) on runtime host (main.py:1229,1261,1268,2665-2671) executing attacker-controllable repo code in the docker.sock-holding container — contradicts git_ops.py:1-6 policy.
- **W2 HIGH/BUG** Full service environment (INTERNAL_SECRET, FERNET_KEY) copied into every sandbox exec: `env = os.environ.copy()` (git_ops.py:85-87) → sandbox `full_env = dict(os.environ)` in exec_run (workspace_sandbox.py:472-484) → same-uid execs can read /proc/self/environ.
- **W3 HIGH/GAP** Absolute paths bypass containment: `resolve_safe_path` returns any absolute path as-is with no scope check (main.py:819-825) → file APIs reach arbitrary filesystem paths.
- **W4 HIGH/DESIGN** Ad-hoc workspaces resolve to workspace ROOT with default full capabilities incl. write+pytest (main.py:951-956,974-975,1139) → trusted-caller chain to CI script execution (/api/admin/tests/unit :2701).

### Workspace Runtime — MEDIUM
- **W5 BUG** create_workspace accepts arbitrary local_path + caller-supplied owner_user → cross-user workspace takeover via ownership checks (main.py:1878-1880,1040,968-971).
- **W6 BUG** Webhook secret: plain string compare (:2738-2741); compose default 'change-me-in-production' live because .env sets empty GIT_WEBHOOK_SECRET (docker-compose.yml:435, .env:106, main.py:2733); token also accepted as URL query param (:2715).
- **W7 GAP** Slug collisions: distinct ids map to identical wsbox-* container/network names → cross-wire between workspaces (main.py:3005, workspace_sandbox.py:142-144).
- **W8 BUG** Redaction middleware buffers entire response bodies incl. FileResponse/zip streams → memory DoS (main.py:242-247,2047,2076).
- **W9 GAP** /ports/expose skips capability enforcement; binds host ports 9000-9199 and TCP proxies on 0.0.0.0 (main.py:2108-2114, workspace_sandbox.py:752-772).

### Workspace Runtime — LOW
- **W10** Unbounded read_text before max_bytes slice OOMs on multi-GB files (main.py:2024-2030).
- **W11 BUG** `select().where(Workspace.webhook_token is not None)` is vacuously true (InstrumentedAttribute truthiness) — should be `.isnot(None)` (main.py:665).
- **W12 GAP** Quarantine counters keyed by relative path only — cross-workspace cross-quarantine (main.py:299).
- **W13 BUG** Webhook pull path skips workspace lock used by mutating endpoints → interleaving with concurrent writes (main.py:2709-2841 vs 2133,2298,2458).
- **W14 GAP** decrypt swallows all exceptions; str(exc) returned to clients; verification-clear helpers swallow all (:315-336, crypto.py:39-41).
- **W15 DEAD/BROKEN** `startswith()` missing argument → resize_workspace_terminal always False (workspace_sandbox.py:737); duplicated docstring block (:237-244).

Positive: secret-redaction middleware, pytest arg sanitization, protected-branch enforcement in both push paths.

---

## 6. Control Plane (port 8008)

**Purpose:** Privileged Docker management: list/restart/recreate `sharedllm_*` containers, background image pulls + GHCR update checks, log retrieval, arbitrary exec into stack containers, hourly orphaned-sandbox reaper. Single shared INTERNAL_SECRET; exposed via Caddy /control_plane* and :8008.

- **C1 HIGH/BUG** Reaper destroys ALL active sandboxes: `_workspace_exists` GETs `/workspaces/{id}` — a route that does not exist (405 → False for everything) → stops/removes every wsbox-* container and network hourly; compounding: ws_id derived from slugified name so even a working endpoint would 404 for non-slug ids (main.py:31-36,42-82,25,60; workspace_sandbox.py:142-144).
- **C2 MED/GAP** /api/webhooks/dns-sync is the sole unauthenticated endpoint on a privileged service (main.py:1158-1159).
- **C3 MED/DESIGN** Arbitrary root shell in any stack container behind one static secret; container-name-prefix check is the only scoping (main.py:1104-1143,423-430).
- **C4 LOW** Tests are source-string greps, cannot catch regressions (tests/test_pull_and_deploy.py:23-48, test_token_resolution.py:27-60).
- **C5 LOW/BUG** Unbounded log tail (tail=-1 fetches everything, main.py:1078).
- **C6 LOW/GAP** Recreate flow clones old Env verbatim (misses rotations/compose changes); failed restore leaves service down (main.py:458-565).

---

## 7. Storage (port 8005)

**Purpose:** Provider-abstraction persistence: Nextcloud WebDAV list/search/write/mirror, content classification/chunking, RAG index sync. Credentials per-request in body with env fallback. Exposed publicly via Caddy :8005.

- **S1 HIGH/GAP** Zero authentication on ANY endpoint (contrast outbound-only use of INTERNAL_SECRET, main.py:10,56) + unconfined `upload_directory(local_path)` → unauthenticated exfiltration of any readable directory to attacker-controlled WebDAV (providers.py:27-50, nextcloud_client.py:259-311, Caddyfile:36-38).
- **S2 MED/HIGH/GAP** Transient DAV failure wipes user's RAG index: list_files swallows errors → [], purge executes BEFORE sync regardless (nextcloud_client.py:115-117, main.py:94-141 esp.132-137). Purge should be skipped when scan failed/empty.
- **S3 MED/BUG** Write "verification" fabricated: `"verified": True if verify else None, # Simplified` — no follow-up GET/PROPFIND; callers report it upstream as fact (nextcloud_client.py:242; workspace_runtime/main.py:2390-2397).
- **S4 MED/BUG** Per-request aiohttp ClientSession created in sync `__init__`, never closed, provider rebuilt every request → socket/FD leak + deprecation (nextcloud_client.py:37-41, providers.py:50).
- **S5 LOW/BUG** Unencoded f-string query interpolation of user-derived user_id (main.py:203).
- **S6 LOW/BUG** Prefix stripping collides on sibling usernames ("/user" strips from "/user2/x" leaving "2/x") (nextcloud_client.py:46,141-142).
- **S7 LOW/GAP** Indexer declares pdf_parser capability but extraction feeds raw binary resp.text() into chunker; checkpoint JSON rewritten per item (O(n²)); chunk_text infinite-loops if overlap>=chunk_size ever configured; search returns HTTP 200 + status:"ERROR" (indexer.py:88-103,170,186-200; main.py:244-246).
- **S8 LOW/GAP** Mirror always reports SUCCESS even with per-file upload failures (nextcloud_client.py:297-310).

---

## 8. Automation

**Purpose:** Bare scheduler loop (no HTTP server): polls Redis timer:* keys, fires due timers at Execution /execute/trigger, deletes one-shots.

- **A1 HIGH/BUG** Recurring timers re-fire ~1 Hz forever: expires_at never advanced on fire (verified writer side timer.py sets it only on add/pause/resume); due branch matches every iteration with 1 s floor backoff → trigger storm (automation/main.py:46-50,88-89,21,102).
- **A2 MED/BUG** Timezone mixing: duration timers store naive local time (timer.py:28-29) while time_str timers store UTC-aware (timer.py:42-46); reader blindly strips tzinfo and compares to naive Phoenix now → UTC timers fire ~7 h off (automation/main.py:71,84-88).
- **A3 LOW/GAP** KEYS in hot loop (O(N) blocking) (automation/main.py:70; timer.py:76,91,107,121,159) — should SCAN.
- **A4 LOW/GAP** At-least-once delivery without dedup: key deleted only on 200; timeout → refire (main.py:46-49,32).
- **A5 LOW/GAP** No health endpoint — crash-loop indistinguishable from healthy externally (main.py:107-108).

---

## 9. Execution (port 8003, host network)

**Purpose:** The "hands": HA bridge (lights/media/climate/security), announce
pipeline (power-on → volume → Kokoro TTS → dispatch Roku/Samsung/webOS/Cast/
ESPHome/DLNA → verify → restore), Kokoro TTS + document extraction (pdftotext/
pandoc/html2text), workspace tools (read/write/search/fuzzy-patch/shell in
sandboxes), device discovery/profiling (registry→HA→HomeKit→ARP→SNMP→mDNS→SSDP→
port scan into aiosqlite WAL DB), timers, presence (ESPresense MQTT), Skylight
OAuth/PKCE, Whisper STT, audiobook regenerate. Second uvicorn instance on :8888
serves media to HA devices. Auth: single shared internal secret;
verify_entity_access is an allow-all stub.

### Execution — HIGH (several empirically reproduced)
- **E1 BUG (reproduced)** Path traversal: `os.path.join(root, rel)` never normalizes `..` before startswith check (handlers/workspace.py:291-293) → read/write/patch/search/shell cwd escape for all consumers incl. transcribe/audiobook paths (workspace.py:371,554,591,663,865; main.py:2585,2655,2683,2703). Fix direction: realpath + commonpath.
- **E2 BUG (reproduced)** `len(resp.content)` on aiohttp StreamReader raises TypeError → all 5 self-verification attempts fail → every Kokoro announcement returns FAILURE "Media endpoint not accessible" (main.py:1454,1460,1466-1468). Should be len(await resp.read()). Test masks it by mocking TTS empty (test_execution_main.py:156).
- **E3 BUG (verified)** Broken bare imports under uvicorn PYTHONPATH=/app: `from ha_client import call_service` (announce_handlers.py:204 + 10 more sites), `from announce_handlers import dispatch_announce` (main.py:1476), `from schemas import …` (main.py:2757), `import device_discovery` (main.py:1785/1807; device_profiler.py:262,368,415), `from schemas_groups import …` (main.py:2451/2464/2477) → ModuleNotFoundError. Effects: announcements silently degrade to MA/Piper; /execute/voice/command guaranteed 500; `/discovery/*` refresh/scan/profile and /execute/groups/* 500. Correct pattern exists in git.py:36-39/talk.py:9-16.
- **E4 BUG** Discovery probes inert: `resp.content.lower()` on StreamReader (device_discovery.py:789), ET.fromstring(StreamReader) (:792), unawaited `resp.text` coroutine passed to .lower()/re.search (:827-840) — all swallowed by blanket except (:865-866) → Roku/Tasmota/ESPHome probe identification and bulk_scan mapping silently broken.
- **E5 BUG** Invalid pandoc flag `--split-level=paragraph` (takes a number; meaningless for -t plain) → RuntimeError exit 2 for EVERY EPUB/DOCX/RTF/ODT extraction (document_text.py:59) — workspace doc reads + audiobook regeneration from such sources broken; tests mock subprocess so never catch it.
- **E6 BUG** Timer creation via time_str always fails: aware expires_at minus naive now → TypeError swallowed into generic "Timer error" (handlers/timer.py:28,44-52,152-154); mixed datetime.now() vs datetime.now(UTC) is root cause; keys stored without TTL; KEYS per request.
- **E7 BUG/GAP** Presence pipeline fully inert: paho thread calls asyncio.create_task (RuntimeError, swallowed) (presence.py:147-171); init_presence_tracker never invoked anywhere; /execute/presence/* serve GPS fallback only. Needs run_coroutine_threadsafe + lifespan wiring.

### Execution — MEDIUM
- **E8 BLOCKING** Sync subprocess/Docker/socket calls freeze the event loop: docker-compose build in async def (main.py:890), 300 MB model download curl (main.py:1322), sync Docker SDK restart/logs (deployment.py:98,136,157; docker_logs.py:68), no-timeout subprocess (diagnostics.py:24,40), arp-scan/snmpwalk/SSDP recvfrom loops (device_discovery.py:503-506,429,568-571,682-696), subnet detect (network_scan.py:32), toolchain probes ×20, `_check_ports` 16 ports ×0.5 s per profiled device (device_profiler.py:300) — combined ≫60 s full-service freeze possible during discovery.
- **E9 SEC** Plaintext GitHub tokens: CLI args visible in ps AND logged verbatim (code_search.py:51-63); token embedded in remote URLs persisted into .git/config and workspace metadata (git.py:640,650,661,694,697,740,742); redaction only catches ghp_/github_pat_ prefixes in logs. gh.py:193-198 shows the correct GH_TOKEN-env pattern.
- **E10 BUG** Capability escalation in gh handler: missing git_write falls back to checking mere `read` for WRITE_ACTIONS (pr merge, release create…) defeating capability model (handlers/gh.py:183-190).
- **E11 BUG** Stale normalized_command after `cd x &&` strip → destructive commands classified as read-capability (e.g. `cd /ws && rm -rf build/` → required_cap=read) (handlers/workspace.py:685,699-719); SYSTEM_BLOCKLIST exact-token matches bypassable via sh -c.
- **E12 SECURITY/DESIGN** Full os.environ copied into child processes/sandboxes despite explicit contrary policy comments (workspace.py:788,721-724,780-784; gh.py:117; webscraper.py:60) incl. GH_TOKEN seeded at startup (main.py:391-403).
- **E13 BUG** TEMP_AUDIO_CACHE grows forever (entries at main.py:454,798,1439; composite.py:78; zero eviction anywhere) + disk copies never pruned → eventual 3 GB mem_limit exhaustion.
- **E14 BUG** Whisper loads weights + transcribes synchronously in async endpoints, reloading per request (main.py:2533,2546,2599-2606) → multi-second stalls; needs to_thread + cached model.
- **E15 BUG** save_path writes mojibake: WAV bytes .decode('utf-8', errors='replace') into StorageFileWriteRequest.content:str (main.py:1485).
- **E16 BUG** Composite broadcast announces storage-read log message ("Read N bytes…") instead of document content (composite.py:49 vs storage.py:30 detail["content"]).

### Execution — LOW / DESIGN
- **E17 BUG** Voice messages uploaded MP3-labeled but are WAV (talk.py:275-289; tts.py:504).
- **E18 BUG** webOS notify shows the media URL instead of the message text (announce_handlers.py:273-275).
- **E19 LOGIC** Samsung/webOS double power-on + doubled boot waits (pre-power state re-evaluated in handler) (main.py:1413-1415; announce_handlers.py:315-322).
- **E20 LEAK** http_client session cache replaces aged sessions without closing → periodic FD leak; close_all_sessions never called (http_client.py:92-118).
- **E21 LEAK** Playwright browsers not closed on error paths (video.py:118-135,199-206; browser.py:142-165,265-288).
- **E22 BUG** yt-dlp subprocesses lack wait_for watchdog → hung downloads pin requests (video.py:239-330,392-419).
- **E23 GAP** Cross-workspace filename disclosure + blocking os.walk of global WORKSPACE_ROOT in suggestion helper (workspace.py:520-522, called from fail paths).
- **E24 PERF** Sync unbounded file IO in async paths: full readlines before 300-line output chunk; difflib O(lines×chunk) patch matching on loop; 128 MB plain-text read; audiobook chapter/WAV IO (workspace.py:393-394,869-888; document_text.py:107-119; main.py:2661-2706).
- **E25 DESIGN** Silent config fallbacks violate fail-fast rule: nextcloud getattr-or-env chain; localhost defaults for STORAGE/Redis/SearXNG; hardcoded 192.168.1.0/24 subnet on detection failure; SNMP router/community; America/New_York timezone (nextcloud_client.py:15-17; main.py:1950,1995,3367; browser.py:51; device_discovery.py:75,554; network_scan.py:54).
- **E26 GAP** Inconsistent error contracts: failures wrapped in _ok/SUCCESS (timer trigger :774-820; MA browse :2224-2246; ABS :2321-2323); some routes raise HTTPException while siblings return FAILURE bodies; GET /execute/timers exposes any user's timers by user_id param (timer.py:156-165; main.py:752-754).
- **E27 GAP** Misc: webscraper tautology forces --mobile always + wrong timeout message (webscraper.py:33,76,146-150); voice routing stop==pause and unmatched verbs return None, "on"-substring matches "song" (main.py:2807-2821,2776); ha_client digit-penalty tests a list instead of chars (ha_client.py:381); storybook gender context uses find-first (tts.py:259); pointless POST-failure GET to services URL in bare except (ha_client.py:102-108); git diff/checkout path option-injection (--output=…) (git.py:526-527,550); os.system at import time (git.py:44-45); websockets imported but absent from requirements (network_scan.py:288); device_registry read-modify-write races (109-165); diagnostics ls -la arbitrary paths; double auth declarations on Skylight routes.
- **E28 DESIGN** Per-request TTS engine construction defeats lazy-load cache (fresh KokoroTTSEngine per call; pipeline always rebuilt; init race, no lock) (tts.py:508-517,175-202).
- **E29 DESIGN** bulk_scan binds same-type entities to first matching IP (two Rokus → same IP) despite `_discover_via_network_scan` comment saying prevented (device_discovery.py:941-956,708-711).
- **E30 GAP** Workspace lint silently degrades to UNSANDBOXED host execution on resolution failure (workspace.py:944-955,355-356).

---

## 10. UI (services/ui/src)

**Structure:** React 18 + TS + Vite; react-query + Zustand; pages: Dashboard,
admin/lab (PIN/biometric AdminElevation), identity, communication, calendar,
media (MA web player via sendspin + ma-jsonrpc WS), remote, settings, knowledge,
workspaces (full IDE: explorer/git/tools/Raven-chat + tabbed viewers + TerminalPane).
AuthContext stores api key + internal_secret in localStorage; single axios client
with retry interceptor; five separate hand-rolled WS lifecycles (a generic
wsManager.ts exists but most components bypass it).

### UI — HIGH
- **U1 BUG** Terminal context menu force-closes 300 ms after opening: inverted `if (!menuLeaveRef.current)` closes while cursor is over menu (TerminalPane.tsx:56-60, commit 3bed4a20; no e2e coverage of the menu).
- **U2 BUG** Menu items unclickable via mouse: mousedown-outside check uses document.activeElement which hasn't moved yet at dispatch time (TerminalPane.tsx:40-50) — WorkspaceIDE.tsx:1884-1893 shows the correct event.target containment pattern.
- **U3 BUG** Browser geolocation synced to nonexistent endpoint `/api/identity/users/location` (context/LocationContext.tsx:58; real route is /api/users/{user_id}/location) with bare catch hiding every 404 → web location never reaches geo service.
- **U4 SEC/GAP** Biometric login reads plaintext `jarvis_saved_password` from storage — dead feature (nothing ever writes it) but dangerous pattern; remove or replace with server-issued refresh token (pages/Login.tsx:98).

### UI — MEDIUM
- **U5 SEC** Bearer tokens in WS query strings everywhere (terminal/RavenTrace/sendspin/jsonrpc/logstream) + localStorage-stored api key and internal_secret shipped from browser (TerminalPane.tsx:184; RavenLiveTrace.tsx:47; maWebPlayer.ts:91,103; api.ts:415,174-176; lib/storage.ts:22).
- **U6 BUG** JarvisLab log-stream reconnect loop survives unmount: cleanup closes socket, onclose unconditionally schedules reconnect → zombie chain calling setState forever; JSON.parse unguarded (JarvisLab.tsx:308-336).
- **U7 GAP** Media player sockets have no auto-reconnect despite header comment claiming it; manual recovery only (maWebPlayer.ts:352,432 vs 8,456-461,842-870).
- **U8 BUG** Timed-out JSON-RPC commands leak message listeners (removeEventListener only in success/error branches) (maWebPlayer.ts:299-321).
- **U9 BUG** getUserMedia mic stream never stopped in useVoiceAssistant — OS mic indicator stays on after deactivate (hooks/useVoiceAssistant.ts:74-123; Communication.tsx:375 shows correct pattern).
- **U10 BUG** 3 s init timeout permanently locks out valid sessions: success path never clears initError; ProtectedRoute treats any initError as logged out (AuthContext.tsx:27-31; App.tsx:42).
- **U11 DESIGN** Retry interceptor re-fires non-idempotent POSTs on ERR_NETWORK (chat/media/timers/intercom duplicated up to 3×) (services/api.ts:212-240).

### UI — LOW
- **U12** isLoggingOut flag never resets; window.location redirect breaks Capacitor webview (api.ts:181,289-297).
- **U13 BUG** Blob URLs leak on IDE unmount (revoked only in closeTab); setActiveTab called inside setTabs updater (impure, StrictMode double-invoke) (WorkspaceIDE.tsx:405-435,576-600).
- **U14 BUG** Cleanup skips CONNECTING sockets (`readyState === 1` should be `<= 1`) (RavenLiveTrace.tsx:113).
- **U15 BUG** Async per-frame Blob.text() decode can reorder rapid PTY output (TerminalPane.tsx:197-211) — use arraybuffer + TextDecoder in order.
- **U16 GAP** Modal lacks Escape close, focus trap, role="dialog"/aria-modal (components/ui/Modal.tsx:26-59).
- **U17 NOTE** Admin PIN elevation is cosmetic client-side (fine given server RBAC, but first PIN typed becomes the PIN silently) (lib/adminPin.ts:60-63).

### Design improvements
1. Converge all five WS lifecycles onto wsManager.ts (fixes U6/U7/U14 classes).
2. Move WS auth off query strings (short-lived ticket or Sec-WebSocket-Protocol); stop shipping internal_secret to browsers.
3. Consume backend streaming for QuickAssistantWidget (currently blocking POST).
4. Narrow Media.tsx effect deps; throttle 1 Hz position ticker fan-out (Media.tsx:1114-1121; maWebPlayer.ts:169-171).
5. Keep TerminalPane mounted across dock/sidebar moves (remount drops PTY session; two render sites WorkspaceIDE.tsx:1353,1843).

---

## Cross-cutting themes

1. **Sibling-task lifecycle in the three WS proxies** (gateway sendspin/terminal/mission-stream): gather-without-cancellation, optional auth, misclassified close frames.
2. **Sync-Redis / sync-subprocess on async event loops** (gateway history/cache; execution everywhere) — directly undermines the <100 ms FastPath goal.
3. **Success/error contract divergence** between fast path, librarian, agent loop, and across services (SUCCESS bodies containing failures; HTTP 200 + status:ERROR; fabricated verified flags).
4. **Secrets hygiene**: tokens in URLs/logs/.git-config/child-process environments; plaintext settings readable unauthenticated; empty-default internal secrets.
5. **Dead/undeployed code accumulating**: app/ monolith remnants, dns-proxy/dns_sync/dns-forwarder, duplicate AgentLoop, state_machine.py — recommend deletion or explicit archival.

## Priority fix list (recommended order)

1. identity: strip is_admin/is_system_default from self-update (I1); auth-gate GET /api/settings/{key}; stop storing huggingface_token plaintext (I2).
2. logging: require auth on reads + WS stream; remove admin bypass (L1/L2).
3. storage: gate all endpoints with internal secret; confine mirror/upload paths; skip purge on failed scans (S1/S2).
4. identity: stop returning api_key from /api/resolve; reconsider silent ID-1 fallback (I3).
5. control_plane: fix reaper to use a real route + unsalted id (C1) — active sessions depend on it.
6. automation: advance expires_at for recurring timers; unify timestamps to UTC-aware (A1/A2).
7. gateway: enforce mission ownership; make mission WS auth mandatory (G1/G2); fix the misleading "Atomic pop all" comment on the chunk drain (G3 was retracted).
8. execution: fix resolve_safe_path realpath (E1), announce verification read (E2), bare imports (E3), pandoc flag (E5), timer tz math (E6).
9. workspace_runtime/control_plane/execution: stop leaking service env into sandboxes (W2/E12); move pytest/lint into sandbox (W1).
10. Adopt hmac.compare_digest everywhere; delete dns-proxy + prune dead code.

---

## Verification results (empirical, 2026-08-22)

Findings were tested rather than trusted. Verification harness: `.tmp/test_verify_findings.py`
(9 tests: 8 passed, 1 skipped) plus targeted source/route inspection and the pandoc manual.

**Confirmed empirically (reproduced in-process):**
- **E1** `resolve_safe_path` traversal — `resolve_safe_path("../outside.txt", root)` returns
  unnormalized `<root>/../outside.txt` without raising; `open()` reads content outside the
  workspace (`handlers/workspace.py:291-293`).
- **E2** `len(resp.content)` on aiohttp raises `TypeError: object of type 'StreamReader' has no len()`
  → every Kokoro announce fails self-verification (`main.py:1454`).
- **E4** `ClientResponse.text` is a coroutine function; `.lower()`/`re.search` on it raise —
  discovery probes inert as described.
- **E6 / A2** naive-vs-aware datetime subtraction raises
  `TypeError: can't subtract offset-naive and offset-aware datetimes` — timer `time_str`
  path always fails; automation timezone mixing real.
- **D2** `'api.docker'[:-6] == 'api.'` — trailing dot breaks all `.docker` lookups.
- **W11** `Workspace.webhook_token is not None` evaluates `True` at Python level (vacuous WHERE;
  `.isnot(None)` required).
- **C1** Route inventory confirms workspace_runtime has NO `GET /workspaces/{workspace_id}`
  → control_plane reaper's existence check always 405 → mass sandbox deletion logic confirmed.

**Confirmed by documentation:**
- **E5** pandoc MANUAL.txt: `--split-level=` takes a NUMBER (integer heading level); passing
  `paragraph` is invalid → EPUB/DOCX/RTF/ODT extraction broken as reported.

**Retracted after verification:**
- **G3** chunk-drain race — see gateway section above; LRANGE+LTRIM is safe for this
  single-consumer drain pattern.

**False positives ruled out:** repeated LSP "Unexpected indent line 127" errors across multiple
files are tool artifacts — `py_compile` passes cleanly on all flagged files; the mypy LSP crash
was an internal AssertionError in mypy itself, not a file issue.

**Local test-suite run (services/execution/tests/):** 55 failed / 190 passed / 14 skipped /
45 deselected under full-suite runs. Adjudicated file-by-file:
- **43 = order-dependent test pollution** (skylight_proxy ×16, device_control ×14,
  execution_main ×10, media_search ×3) — ALL pass when run isolated (48 passed combined);
  global state leaks between test files.
- **7 = connection-class, expected locally** per constraint that SharedLLM services do not run
  locally: identity_proxy ×3 (`Cannot connect to identity:8001`), ABS handlers ×3
  (`abs.local:13378`), health endpoint ×1 (correctly 503 with Redis/Identity/RAG/Storage down).
- **5 = stale tests, product code correct**: calendar_handlers ×3 assert an old message format
  ("Upcoming Events:") while the handler now returns "Loaded N event(s)." with correctly
  merged/sorted events; player_integration gateway-stream ×2 patch
  `services.gateway.main.httpx.AsyncClient` but the gateway migrated to pooled aiohttp clients.

**Zero product-code bugs among the 55 failures.**
