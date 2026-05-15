# SharedLLM Tools

Utility scripts for debugging and verifying system functionality.

## Directory Structure

### `debug/`

Diagnostic scripts for inspecting system state, network, and integrations.

- `debug_android_off.py` — Debug Android device power state
- `debug_chroma_group.py` — Inspect ChromaDB metadata by group
- `debug_remote.py` — Debug remote API connectivity
- `diagnose_resolution.py` — Diagnose DNS/service resolution issues
- `list_devices.py` — List discovered devices
- `scan_remotes.py` — Scan remote endpoints

### `verify/`

Verification scripts for confirming fixes and system behavior.

- `verify_cast_playback.py` — Verify Cast device playback
- `verify_fireplace.py` — Verify fireplace integration
- `verify_fix_devices.py` — Verify device routing fixes
- `verify_ma_cleaning_import.py` — Verify Music Assistant cleaning imports
- `verify_pause_fix.py` — Verify pause/resume fix
- `verify_resolution_logic.py` — Verify media resolution logic
- `verify_robustness.py` — Robustness verification
- `verify_rumble_cast.py` — Verify Rumble Cast integration
- `verify_youtube_click.py` — Verify YouTube click-through

## Related

- `scripts/` — Operational scripts (deploy, benchmark, CI, legacy tests)
- `test/` — pytest test suites (unit, integration, local hardware tests)
