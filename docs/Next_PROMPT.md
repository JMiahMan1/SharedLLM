# Cursor AI Prompt - Fix "Skip" Command Regression

## Context
You are working on a FastAPI-based home automation voice assistant that integrates with Home Assistant and Music Assistant. A regression was introduced where the "Skip" transport command targets the wrong media player device.

## The Problem

When user says:
1. "Play Brandon Lake on Office TV" (music plays successfully on `media_player.office_tv_chrome_2`)
2. "Skip" (targets WRONG device: `media_player.office_tv` instead of `office_tv_chrome_2`)

**Root Cause**: Redis context (`rag:last_media_entity:admin`) is not being set correctly after music playback.

## Architecture Overview

See `ARCHITECTURE.md` for full details. Key points:

### Redis Context Keys
- `rag:last_media_entity:{user}` - Stores last media player used
- Updated in two places:
  1. `app/domains/media/commands.py:350-357` (generic handler)
  2. `app/domains/media/integrations/music_assistant.py:54-62` (MA specific)

### Entity Resolution
- `app/domains/media/devices.py:smart_resolve_entity()`
- When "Office TV" query, finds TWO entities:
  - `media_player.office_tv` (AndroidTV remote, integration=androidtv)
  - `media_player.office_tv_chrome_2` (Cast device, integration=cast)
- **Current priority**: AndroidTV > Cast
- **Needed for music**: Cast > AndroidTV (Music Assistant uses Cast devices)

### Integration Flow for Music
```
User: "Play X on Office TV"
  → Entity Resolution → office_tv_chrome_2 (Cast)
  → CastIntegration.play_media()
    → Detects MA wrapper
    → Delegates to MusicAssistantIntegration.play_media()
      → Calls MA service (SUCCESS)
      → SHOULD update Redis to office_tv_chrome_2
      → Returns SUCCESS to Cast
  → SHOULD update Redis again in commands.py
```

## Bugs to Fix

### Bug #1: Entity Resolution Priority for Music
**File**: `app/domains/media/devices.py`
**Function**: `smart_resolve_entity()`
**Lines**: ~555-570 (integration priority sorting)

**Current Code**:
```python
def _integ_priority(item):
    if "roku" in integ or "androidtv" in integ: return 10
    if "cast" in integ: return 8
    # ...
```

**Fix**: When `is_music=True`, prefer Cast > AndroidTV; for video, keep AndroidTV > Cast:
```python
def _integ_priority(item):
    integ = item[1]
    
    # CRITICAL: Different priorities for music vs video
    if is_music:  
        # Music: Prefer Cast/MA (Music Assistant uses Cast devices)
        if "cast" in integ or "music_assistant" in integ: return 10
        if "roku" in integ or "androidtv" in integ: return 5
    elif is_video:
        # Video: Prefer TV devices (AndroidTV/Roku) over Cast speakers
        if "roku" in integ or "androidtv" in integ: return 10
        if "cast" in integ: return 8
    else:
        # Default: Prefer TV devices
        if "roku" in integ or "androidtv" in integ: return 10
        if "cast" in integ: return 8
    # ...
```

**Key Point**: This ONLY changes priority for music requests. Video requests still prioritize AndroidTV/Roku over Cast.

### Bug #2: Redis Not Updated in MusicAssistantIntegration
**File**: `app/domains/media/integrations/music_assistant.py`
**Lines**: 54-62

**Current Code**:
```python
if result and result.get("status") == "SUCCESS":
    redis_client = kwargs.get("redis_client")
    if redis_client:
        from app.domains.media.devices import _set_last_entity, _set_last_media_entity
        user = user_creds.get("user", "admin")
        _set_last_entity(redis_client, user, entity_id)
        _set_last_media_entity(redis_client, user, entity_id)
        log.info(f"[MusicAssistantIntegration] Context updated: {user} -> {entity_id}")
```

**Investigation Needed**:
1. Add debug log BEFORE the if block: `log.info(f"[MA DEBUG] redis_client in kwargs: {redis_client is not None}, entity_id: {entity_id}")`
2. Check if `kwargs.get("redis_client")` is None
3. Trace back through `cast.py` to see if redis_client is passed in kwargs

### Bug #3: Compound Command Splitting
**Problem**: "Play Brand and Lake" splits into two commands, updates Redis twice

**Find**: Search for compound command handler (likely in `app/logic/`)
**Fix**: Don't split on "and" when it's part of an artist name, OR only update Redis once after all commands complete

## Important Notes for Cursor AI

### Limitations of Previous AI (Gemini)
⚠️ **The previous AI (Gemini) has a critical bug**: It cannot read command output unless piped to a file with `> output.txt 2>&1`. 

When running commands, ALWAYS redirect output to files:
```bash
# WRONG (Gemini can't see output):
git log -3

# RIGHT (Gemini can see it):
git log -3 > git_log.txt 2>&1
cat git_log.txt
```

This caused massive debugging delays and frustration.

### Testing Procedure
1. Deploy changes: `bash tools/utils/deploy_remote.sh > deploy_log.txt 2>&1`
2. Wait ~60 seconds for container rebuild
3. Test: "Play Brandon Lake on Office TV"
4. Check logs for: `[MusicAssistantIntegration] Context updated: admin -> media_player.office_tv_chrome_2`
5. Check Redis: Should have `rag:last_media_entity:admin = media_player.office_tv_chrome_2`
6. Test: "Skip"
7. Verify it targets `office_tv_chrome_2` NOT `office_tv`

### Accessing Logs
Remote logs: `ssh jeremiah@192.168.2.211 "docker logs unified_rag_api --tail 200" > remote_logs.txt 2>&1`

### Branch Info
- Working branch: `timer`
- Remote: `jeremiah@192.168.2.211:/home/jeremiah/SharedLLM`

## Success Criteria

✅ "Play music on Office TV" → Uses `media_player.office_tv_chrome_2` (Cast device)
✅ Redis updated to `media_player.office_tv_chrome_2`
✅ "Skip" → Targets `media_player.office_tv_chrome_2` from Redis
✅ Skip command works and music advances

## Files to Modify

1. `app/domains/media/devices.py` - Fix entity resolution priority for music
2. `app/domains/media/integrations/music_assistant.py` - Debug/fix Redis update
3. Possibly compound command handler - Fix "Brand and Lake" splitting

Good luck! This should be a straightforward fix once the entity resolution prioritizes Cast for music requests.
