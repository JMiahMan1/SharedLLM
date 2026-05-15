# Scripts Directory

Operational scripts, benchmarks, and ad-hoc test utilities for SharedLLM.

## Directory Structure

### Root Scripts

| Script | Purpose |
|--------|---------|
| `deploy.sh` | Production deploy script — builds and restarts Docker services |
| `install-hooks.sh` | Installs git post-merge hook for auto-deploy on pull |
| `post-merge.hook` | Git hook that triggers `deploy.sh` after `git pull` |
| `index_capabilities.py` | Indexes tool schemas into RAG for agent discovery |
| `run_ci_unit_tests.sh` | CI test runner — iterates `services/*/tests/` |
| `patch_container.sh` | Patches files into running Docker containers |
| `test_raven_pipeline.sh` | Raven hardening validation — tests container UIDs and Redis kill switch |
| `test_ollama_direct.py` | Resolves Ollama URL from docker-compose and tests connectivity |
| `test_frontend_api.py` | Tests gateway API endpoints from frontend perspective |
| `test_gateway.py` | Basic gateway chat endpoint test |
| `test_integration.py` | Multi-service integration test (gateway, identity, execution, RAG) |
| `test_remote.py` | Tests production gateway at `jarvis.sumemail.com` |
| `test_stream.py` | Tests streaming chat endpoint |
| `test_live_api.py` | Tests production API endpoints |
| `fetch_logs.py` | Fetches and filters logs from the gateway |
| `fetch_remote_logs.py` | Fetches logs from remote production server |
| `deploy_remote.sh` | Deploys code to remote production server |
| `wait_for_health.py` | Waits for service health endpoint to return READY |

### Benchmarks

| Script | Purpose |
|--------|---------|
| `benchmark_models.py` | Benchmarks Ollama models on a coding prompt |
| `benchmark_single.py` | Single-model benchmark with timing |
| `benchmark_validation.py` | Validates benchmark results for consistency |
| `benchmark_raven.py` | Benchmarks Raven autonomous pipeline |

### Audit Scripts

| Script | Purpose |
|--------|---------|
| `audit_pipeline.py` | Audits the Raven execution pipeline |
| `audit_rag.py` | Audits RAG service configuration and connectivity |
| `delegate_audit_to_raven.py` | Delegates an audit task to Raven agent |

### Integration Test Scripts

These scripts test specific hardware integrations. They require the corresponding devices to be on your network.

#### Home Assistant
| Script | Purpose |
|--------|---------|
| `test_ha_integration.py` | Full HA integration test — lights, media, climate, security |
| `test_ha_connectivity.py` | Basic HA API connectivity test |
| `ha_production_validator.py` | Validates HA production configuration |

#### Roku
| Script | Purpose |
|--------|---------|
| `test_roku_power.py` | Tests Roku power state via HA |
| `test_roku_power_focused.py` | Focused Roku power state test |
| `test_roku_rmp.py` | Tests Roku Remote Mobile Protocol |
| `test_roku_state_diagnostic.py` | Diagnoses Roku device state |
| `test_roku_variants.py` | Tests multiple Roku device variants |
| `roku_remote_play.py` | Tests remote playback on Roku |
| `roku_watch_flow.py` | Tests watch-to-Roku flow |

#### Music Assistant
| Script | Purpose |
|--------|---------|
| `test_ma_types.py` | Tests MA media type resolution (artist/track/album) |
| `test_ma_500.py` | Tests MA 500 error handling |
| `test_ma_cleaning.py` | Tests MA query cleaning logic |
| `test_ma_search_and_play.py` | Tests MA search and playback flow |
| `ma_video_playback.py` | Tests video playback through MA |

#### Android TV
| Script | Purpose |
|--------|---------|
| `android_auto_on.py` | Tests Android TV auto power-on |
| `android_complete_flow.py` | Tests complete Android TV flow |
| `android_home.py` | Tests Android TV home screen interaction |
| `android_play_watch.py` | Tests play-from-watch on Android TV |
| `android_video_download.py` | Tests video download on Android TV |
| `android_watch_youtube.py` | Tests YouTube playback on Android TV |
| `test_android_routing.py` | Tests Android TV device routing |

#### Video & Media
| Script | Purpose |
|--------|---------|
| `test_video_all_devices.py` | Tests video playback across all devices |
| `test_video_switching.py` | Tests video source switching |
| `test_video_search_logic.py` | Tests video search resolution logic |
| `test_music_and_video.py` | Tests music and video playback flow |
| `test_media_live.py` | Live media playback test |
| `test_media_playback.py` | Comprehensive media playback test |
| `test_volume.py` | Tests volume control intent resolution |
| `test_media_playback.py` | Media playback via execution service |

#### Verification Scripts
| Script | Purpose |
|--------|---------|
| `verify_autonomous_evolution.py` | Verifies Raven autonomous evolution pipeline |
| `verify_cast_playback.py` | Verifies Cast device playback |
| `verify_fireplace.py` | Verifies fireplace integration |
| `verify_fix_devices.py` | Verifies device routing fixes |
| `verify_ma_cleaning_import.py` | Verifies MA cleaning imports |
| `verify_pause_fix.py` | Verifies pause/resume fix |
| `verify_resolution_logic.py` | Verifies media resolution logic |
| `verify_robustness.py` | Robustness verification |
| `verify_rumble_cast.py` | Verifies Rumble Cast integration |
| `verify_youtube_click.py` | Verifies YouTube click-through |

#### Live/End-to-End Tests
| Script | Purpose |
|--------|---------|
| `live_test.py` | End-to-end system test (auto-installs dependencies) |
| `full_system_verify.py` | Full system verification suite |
| `rigorous_verify.py` | Rigorous end-to-end verification |
| `live_combined_verify.py` | Combined live verification tests |
| `run_test.py` | Test runner orchestrator |

#### Utilities
| Script | Purpose |
|--------|---------|
| `test_llm.py` | Direct Ollama API test |
| `test_chat_lifecycle.py` | Tests chat session lifecycle |
| `test_gracies_single.py` | Single ad-hoc test for Gracie's device |
| `test_listen_fix.py` | Tests listen mode fix |
| `test_skip_command.py` | Tests command skip logic |
| `test_ssdp_discovery.py` | SSDP device discovery utility |
| `code_helper_fix_eval.py` | Tests code helper fix evaluation |
| `dump_metadata.py` | Dumps RAG metadata for inspection |
| `wol_device.py` | Wake-on-LAN utility (set MAC address in script) |

## Usage

Most scripts require environment variables. Set them before running:

```bash
export OLLAMA_URL=http://localhost:11434
export HA_URL=https://ha.example.com
export HA_TOKEN=your-token
export INTERNAL_SECRET=your-secret
python scripts/test_ha_integration.py
```

Or create a `.env` file and source it:

```bash
set -a && source .env && set +a
python scripts/test_ha_integration.py
```

## Running Benchmarks

```bash
# Benchmark all configured models
python scripts/benchmark_models.py

# Benchmark specific models
export BENCHMARK_MODELS="qwen3:8b,qwen2.5-coder:7b"
python scripts/benchmark_models.py
```

## Running CI Tests

```bash
# Run all CI-safe unit tests
./scripts/run_ci_unit_tests.sh

# Run a specific test file
pytest services/tests/test_gateway_model_selection.py -v
```
