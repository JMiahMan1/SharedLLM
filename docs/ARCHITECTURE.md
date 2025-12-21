# SharedLLM Home Automation System - Architecture Documentation

## Overview
A FastAPI-based voice assistant that integrates with Home Assistant, Music Assistant, and various media devices (Roku, Android TV, Cast) to provide intelligent home control via natural language.

## Core Components

### 1. Request Flow
```
User Voice Input (via HA) 
  → FastAPI Endpoint (/api/chat)
  → Intent Recognition (regex + semantic)
  → Entity Resolution (ChromaDB similarity search)
  → Integration Router
  → Home Assistant Service Call
  → Response to User
```

### 2. Key Files

#### Entry Point
- **`app/main.py`**: FastAPI application, endpoints, lifespan management

#### Intent Processing
- **`app/intent_engine/`**: Intent detection engine
  - Uses regex patterns for high-confidence matching
  - Falls back to semantic similarity search
  - Returns intent + confidence score

#### Media Commands
- **`app/domains/media/commands.py`**: Orchestrates media command execution
  - `handle_media_command()`: Main entry point
  - Entity resolution fallback chain:
    1. Explicit device name in query
    2. Redis `last_media_entity` (for transport commands)
    3. Active/playing media players
  - Updates Redis context after successful commands
  - Routes to appropriate integration based on device type

#### Entity Resolution
- **`app/domains/media/devices.py`**: Device resolution and metadata
  - `smart_resolve_entity()`: ChromaDB similarity search + filtering
  - `get_active_media_players()`: Queries HA for currently playing devices
  - `_set_last_media_entity()`: Updates Redis context
  - `_filter_by_area()`: Area-aware filtering

#### Integrations (Factory Pattern)
- **`app/domains/media/integrations/base.py`**: Base class for all integrations
- **`app/domains/media/integrations/music_assistant.py`**: Music Assistant (MA)
  - Handles music playback via MA
  - **CRITICAL**: Must update `last_media_entity` in Redis on successful play
  - Implements query cleaning to remove device names
- **`app/domains/media/integrations/standard.py`**: Generic HA media players
  - Fallback integration for any media_player entity
  - Implements transport commands (play, pause, next, previous)
  - **Has MA wrapper fallback**: If command fails, tries `media_player.mass_{device_name}`
- **`app/domains/media/integrations/roku.py`**: Roku-specific
  - Video playback via direct URL casting
  - Delegates music to MusicAssistantIntegration if MA wrapper detected
  - Implements transport commands with MA fallback
- **`app/domains/media/integrations/cast.py`**: Google Cast
  - Inherits from StandardIntegration (gets transport commands + fallback)
  - Delegates music to MusicAssistantIntegration if MA wrapper detected

### 3. Redis Context System
**Purpose**: Remember which device the user last used for media commands

**Keys**:
- `rag:last_entity:{user}` - Last entity (any domain)
- `rag:last_media_entity:{user}` - Last media_player entity (transport commands use this)

**Update Points**:
1. **`commands.py:350-357`**: Updates both keys when entity_id is resolved
2. **`music_assistant.py:54-62`**: **MUST** update on successful MA playback

**Lookup Priority** (for transport commands like "skip"):
1. Check `rag:last_media_entity:{user}`
2. Fall back to `rag:last_entity:{user}`
3. Fall back to active media players
4. Fail with error

### 4. Music Assistant Wrapper System
Music Assistant creates "wrapper" entities that control the actual hardware.

**Example**:
- Hardware: `media_player.office_tv` (Android TV remote)
- Cast Device: `media_player.office_tv_chrome_2` (Cast integration)
- **MA Wrapper**: `media_player.office_tv_chrome_2` becomes MA-controlled when music plays

**Detection**:
- Attribute `app_id: "music_assistant"` or `mass_player_type` present
- Underlying device in `active_queue` attribute

**Problem**: When music plays via MA on `office_tv_chrome_2`, Redis should store `office_tv_chrome_2`, NOT `office_tv`.

### 5. Entity Resolution Priority

For **"Office TV"** query:
1. **Exact Name Match**: Searches all entities for `friendly_name == "Office TV"`
2. **Integration Priority** (if multiple matches):
   - Roku/AndroidTV/WebOS > Cast > Others > MA/DLNA
3. **For MUSIC**: Should prefer Cast (MA-capable) over Android TV remote

**Current Bug**: Exact name match returns `media_player.office_tv` (Android TV) instead of `media_player.office_tv_chrome_2` (Cast device that MA uses)

## Current Issues & Regression

### Issue #1: Redis Not Updated on MA Playback
**File**: `app/domains/media/integrations/music_assistant.py`
**Line**: 54-62
**Problem**: `_set_last_media_entity()` is called, but context not persisting
**Suspect**: `redis_client` not being passed through kwargs from CastIntegration delegation

**Fix Needed**: Verify kwargs chain:
```
CastIntegration.play_media() 
  → MusicAssistantIntegration.play_media(kwargs)
  → kwargs.get("redis_client") should work
```

### Issue #2: Wrong Entity Resolved for Music
**File**: `app/domains/media/devices.py`
**Function**: `smart_resolve_entity()`
**Line**: ~536 (exact match section)
**Problem**: When query is "Office TV", returns `media_player.office_tv` (Android) instead of `media_player.office_tv_chrome_2` (Cast)

**Why**: Exact name match finds BOTH entities with friendly_name "Office TV":
1. `media_player.office_tv` (Android TV, integration=androidtv)
2. `media_player.office_tv_chrome_2` (Cast, integration=cast)

Integration priority (line 555-564) prefers Roku/AndroidTV > Cast.

**Fix Needed**: For `is_music=True` requests, reverse priority to prefer Cast > AndroidTV (since MA uses Cast devices).

### Issue #3: Compound Command Splitting
**File**: Likely in `app/logic/` (compound command handler)
**Problem**: "Play Brand and Lake on Office TV" gets split into:
- "Play Brand" → Resolves to wrong device (Loft TV)
- "Play Lake on Office TV" → Resolves correctly (Office TV)

Each sub-command updates Redis, so final value is from LAST command, not the successful one.

**Fix Needed**: Either:
1. Don't split artist names (detect "and" in music context)
2. Only update Redis from successful commands
3. Update Redis AFTER all batch commands complete, using the successful device

## Successful Working Parts

✅ Transport command aliases (`skip` → `media_next_track`)
✅ MA wrapper fallback in StandardIntegration (if HA command fails, retries on `media_player.mass_{name}`)
✅ Integration routing (Cast → MA delegation for music)
✅ Roku video playback with DLNA casting

## Files to Review for Fix

1. **`app/domains/media/integrations/music_assistant.py`**
   - Verify redis_client is in kwargs
   - Add debug logging for context update

2. **`app/domains/media/devices.py:536-570`**
   - Modify integration priority for music requests
   - Prefer Cast > AndroidTV when `is_music=True`

3. **`app/domains/media/commands.py:350-357`**
   - Consider: Should batch commands update Redis per sub-command or once at end?

4. **Compound command handler** (find location)
   - Fix splitting logic for artist names
   - Or change Redis update strategy

## Testing Commands

```bash
# Good test sequence:
1. "Play Brandon Lake on Office TV"     # Should resolve to office_tv_chrome_2, update Redis
2. Check Redis: `rag:last_media_entity:admin` should be `media_player.office_tv_chrome_2`
3. "Skip"                               # Should use Redis value from step 2
```

## Deployment
```bash
git add <files>
git commit -m "message"
git push origin timer
bash tools/utils/deploy_remote.sh > deploy_log.txt 2>&1
```

Remote server: `jeremiah@192.168.2.211:/home/jeremiah/SharedLLM`
Branch: `timer`
