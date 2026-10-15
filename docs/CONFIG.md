# SharedLLM Configuration

## The model

1. **Secrets** (`.env` only): `INTERNAL_SECRET` (service-to-service auth) and
   `FERNET_KEY` (encrypts stored credentials). Required at import time
   (`services/config.py`); a service refuses to start without them. They are
   never stored in the database, code, tests or docs.
2. **Bootstrap** (`.env`): `NETWORK_MODE` and the network-scoped service URLs
   (below), so a service can reach Identity at all.
3. **Runtime** (Identity GlobalSettings): everything else. Each service calls
   `resolve_runtime_config()` from its FastAPI `lifespan` at startup, which
   reads `GET {IDENTITY_SVC_URL}/api/settings` and fills the module-level
   variables in `services/config.py`. Settings are edited in Admin > Settings.

`.env` is **seed-only**: on a fresh install `services/identity/seed.py` turns
its values into GlobalSetting rows once; after that the database wins. The
deploy merge (`scripts/merge_env.py`) only appends keys the server's `.env`
lacks, so a value set on the server is never reverted by a deploy.

## Network-scoped service URLs

Execution runs on the host network, everything else on the bridge, so every
service URL exists twice: `BRIDGE_<NAME>_SVC_URL` (Docker names, e.g.
`http://rag:8004`) and `HOST_<NAME>_SVC_URL` (`http://127.0.0.1:8004`, through
Caddy). Each container sets `NETWORK_MODE` (`bridge` default, `host` for
execution) and `_net_url()` reads only `{NETWORK_MODE}_{NAME}_SVC_URL`.

Identity stores the same pairs as settings (`bridge_rag_svc_url`,
`host_rag_svc_url`, …); `resolve_runtime_config()` reads the one for its own
network, and the gateway orchestrator prefers the scoped key over the plain
legacy `rag_svc_url` (which is a bridge address and must not be used from the
host network).

Names: `IDENTITY`, `EXECUTION`, `RAG`, `STORAGE`, `LOGGING`,
`WORKSPACE_RUNTIME`, `GEO`, `TELEMETRY`, `BIBLE`, plus `SEARXNG_URL`.

## Runtime settings (Identity → `services/config.py`)

| Setting key | Variable | What |
|---|---|---|
| `llm_local_url` | `OLLAMA_URL` | Local inference server |
| `{mode}_execution_svc_url`, `{mode}_rag_svc_url`, `{mode}_storage_svc_url`, `{mode}_logging_svc_url`, `{mode}_workspace_runtime_svc_url`, `{mode}_bible_svc_url`, `{mode}_searxng_url` | `*_SVC_URL`, `SEARXNG_URL` | Network-scoped URLs |
| `geo_svc_url`, `control_plane_url` | `GEO_SVC_URL`, `CONTROL_PLANE_URL` | |
| `ha_url`, `ha_token` | `HA_URL`, `HA_TOKEN` | Home Assistant |
| `nextcloud_url`, `nextcloud_user`, `nextcloud_pass` | `NEXTCLOUD_*` | System Nextcloud account |
| `git_url`, `git_user`, `git_token` | `GIT_*` | System git identity |
| `audiobookshelf_url`, `audiobookshelf_user`, `audiobookshelf_pass` | `AUDIOBOOKSHELF_*` | |
| `assistant_model`, `coding_model`, `librarian_model`, `default_model` | `*_MODEL` | Model roles |
| `embedding_model`, `fast_path_threshold` | | Fast-path router |
| `default_tts_voice`, `temp_media_dir` | | TTS |
| `mass_config_entry_id` | | Music Assistant config entry |
| `blb_base_url`, `bible_devotional_dir`, `bible_import_dir`, `bible_provider_cache`, `bible_provider_call_budget`, `calibre_library_path` | | Bible and Calibre |
| `log_retention_days`, `log_max_entries`, `rag_retention_days` | | Retention (30 d, 10000 entries by default) |
| `raven_max_total_seconds`, `raven_iteration_timeout`, `raven_heartbeat_interval`, `raven_hung_threshold`, `raven_error_threshold`, `raven_check_interval` | | Raven limits (1800 s, 600 s, 30 s, 600 s, 5, 300 s) |
| `workspace_root`, `workspace_runtime_root` | | Workspaces |
| `upstream_dns`, `dns_poll_interval` | | DNS |
| `execution_external_host` | | Host used in links to execution media |

Feature settings read directly from Identity by the service that uses them
(not through `resolve_runtime_config`) include `dns_mappings`,
`dns_failover_enabled`, `dns_health_ports` (`docs/DNS_SERVICE.md`) and
`raven_max_iterations` (default 60).

`REDIS_URL` is environment-only (it differs between bridge and host).

**Not here:** Music Assistant and per-service logins are per-user credentials
in Identity, with admin grants for sharing — see `docs/PER_USER_CREDENTIALS.md`.

## Paths and stores

| Variable | Default | Where |
|---|---|---|
| `RAG_DB_PATH` | `/data/rag.db` | RAG's SQLite + sqlite-vec store (`services/rag/db.py`) |
| `IDENTITY_DATABASE_URL` | `sqlite:////data/identity.db` | Identity |
| `BIBLE_DATABASE_URL` | `sqlite:////data/bible.db` | Bible |
| `WORKSPACE_DATABASE_URL` | `sqlite:////data/workspace_runtime.db` | Workspace runtime |
| `MODELS_DIR` | `/workspaces/models` (execution, compose) | Kokoro TTS models |

## Adding a setting

1. Add the variable to `services/config.py` with a safe default (or none, if
   an unset value must be visible as "not configured").
2. Add it to `resolve_runtime_config()`'s `settings_map`.
3. Seed it in `services/identity/models.py` / `seed.py` so it appears in
   Admin > Settings.

Never add a new runtime value to `.env`, and never put a secret value in code,
tests or docs; tests read `INTERNAL_SECRET` from the environment.

## Testing with seeded values

E2E runs use a `test.env` (see `test.env.example`) with throwaway values,
mounted into Identity to seed its database:

```env
INTERNAL_SECRET=<any value for the test stack>
FERNET_KEY=<generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
NETWORK_MODE=bridge
BRIDGE_IDENTITY_SVC_URL=http://identity:8001
```
