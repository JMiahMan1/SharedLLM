# Scripts Directory

Operational scripts, benchmarks and ad-hoc diagnostics in `scripts/`. Only the
deploy, CI and indexing scripts are wired into anything; the rest are manual
tools run against a live stack. Every script reads secrets (`INTERNAL_SECRET`,
API keys, HA token) from the environment; none are hard-coded.

## Deploy and operations

| Script | Purpose |
|---|---|
| `deploy_remote.sh` | **The deploy.** `bash scripts/deploy_remote.sh <user@host> <dir> <service…>`: waits for the CI image build of the commit, merges `.env` append-only (`merge_env.py`), pulls and recreates the named services, publishes the latest APK for the in-app updater, verifies |
| `merge_env.py` | Append-only `.env` merge used by `deploy_remote.sh` (never overwrites a key the host already has) |
| `deploy.sh` | Restart the stack on the server after a `git pull` (used by the post-merge hook); does not build |
| `deploy_local_build.sh` | Emergency only: build images **on the server** when GHCR/Actions are down. Normal deploys never build on the server (see `CONTRIBUTING.md`) |
| `install-hooks.sh`, `post-merge.hook` | Install the git post-merge hook that runs `deploy.sh` |
| `build-base.sh` | Build the shared base image (`docker/Dockerfile.base`) for local development; CI builds the published one |
| `index_capabilities.py` | Index tool schemas into RAG (run by the `capabilities-sync` service) |
| `patch_container.sh` | Copy files into a running container for a quick experiment (not a deploy) |
| `wait_for_health.py` | Wait until a service's health endpoint reports ready |
| `fetch_logs.py`, `fetch_remote_logs.py` | Pull recent logs (admin logs endpoint) |
| `rvcurl.sh` | Resilient curl wrapper for the gateway/RAG |
| `net_path_test.py` | Network path test to the server |
| `wol_device.py` | Wake-on-LAN helper |
| `seed_ma_identity.py` | Seed Music Assistant credentials into Identity |

## Tests and CI

| Script | Purpose |
|---|---|
| `run_ci_unit_tests.sh` | Quick unit-test subset (used by Workspace Runtime's `/api/admin/tests/unit`) |
| `android_functional_test.sh` | Android smoke tests over adb (artifacts in `.tmp/android-func`) |
| `test_raven_pipeline.sh` | Raven hardening checks (container UIDs, Redis kill switch) |

## Benchmarks

`benchmark_models.py` (all configured models; `BENCHMARK_MODELS` to pick),
`benchmark_single.py`, `benchmark_validation.py`, `benchmark_raven.py`,
`code_helper_fix_eval.py`.

## Live diagnostics (need the real services)

| Area | Scripts |
|---|---|
| Gateway / API | `test_gateway.py`, `test_stream.py`, `test_frontend_api.py`, `test_integration.py`, `test_live_api.py`, `test_remote.py`, `test_remote_endpoint.py`, `test_chat_lifecycle.py`, `test_llm.py`, `test_ollama_direct.py` |
| RAG / Raven | `audit_rag.py`, `test_capabilities_awareness.py`, `verify_autonomous_evolution.py` |
| Home Assistant | `test_ha_integration.py`, `test_ha_connectivity.py`, `ha_production_validator.py`, `test_gracies_single.py`, `test_listen_fix.py` |
| Music Assistant / ABS | `test_ma_types.py`, `test_ma_cleaning.py`, `test_ma_search_and_play.py`, `test_abs_auth.py`, `live_test_media_ui_flow.py`, `test-media-player.js` (Playwright; `npm install` in `scripts/` first, via `scripts/package.json`) |
| Media / video | `test_media_playback.py`, `test_music_and_video.py`, `test_video_all_devices.py`, `test_video_switching.py`, `test_volume.py` |
| Roku | `test_roku_power.py`, `test_roku_power_focused.py`, `test_roku_state_diagnostic.py` |
| Network / DNS | `test_ssdp_discovery.py`, `test_dns_record_upsert.py` |

These are diagnostics, not CI: they assume a reachable stack and may change
real device state. Run them with the environment loaded:

```bash
set -a && source .env && set +a
python scripts/test_ha_integration.py
```
