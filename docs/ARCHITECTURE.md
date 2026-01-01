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

### 5. Entity Resolution Strategy (New Scoring Logic)

The system now uses a **Score-Based Resolution** mechanism (`_score_candidate_for_intent_and_media_type`) rather than simple priority lists. This ensures robust handling of ambiguous queries like "Watch vs Listen".

**Scoring Factors**:
1. **Intent Matching**:
   - **"search/watch/view"** (Video Intent):
     - **+100**: Hardware TVs (Roku, Android TV, WebOS).
     - **+90**: Cast Video devices (Chromecast).
     - **-100 (HARD REJECT)**: Audio-only devices (Sonos, Music Assistant, Generic Speakers).
   - **"play/listen"** (Audio Intent):
     - **+200**: Music Assistant (Native support).
     - **+150**: MA-capable wrappers.
     - **+50**: Generic Speakers.
   - **"turn_off/on"** (Power):
     - **+100**: Infrared/Bluetooth Remotes.
     - **+20**: Physical TVs.
     - **-10**: Software players (Music Assistant).

2. **Refined Matching Flow**:
   1. **Exact Match**: Checks for exact friendly name match. If found, scores it.
   2. **Prefix Match**: Checks for prefix.
   3. **Similarity Search**: Queries ChromaDB for semantic matches.
   4. **Scoring & Filtering**: All candidates are scored. Any candidate with Score < -50 is discarded. The highest score wins.

**Dynamic Grouping**:
Legacy HA groups are supported, but the system now prioritizes **Ad-Hoc Grouping** via pattern matching (e.g., "Kitchen and Living Room", "All Lights").

#### Entity Resolution Flow
```mermaid
graph TD
    A[User Query] --> B{Pattern Detection}
    B -- "All/Every/Location" --> C[Return Multiple Candidates]
    B -- No Pattern --> D{Exact or Prefix Match?}
    
    D -- Yes --> E[Score & Filter Candidates]
    D -- No --> F[Vector Search (ChromaDB)]
    F --> E
    
    E --> G{Check Intent}
    G -- "Watch/Video" --> H(Score: TV +100, Cast +90, Audio -100)
    G -- "Play/Listen" --> I(Score: MusicAssistant +200, Speaker +50, TV +20)
    G -- "Turn Off" --> J(Score: Remote +100, TV +20, MA -10)
    
    H --> K[Select Highest Score > -50]
    I --> K
    J --> K
    
    K --> L[Return Entity ID]
```

### 6. Verification & Robustness
A dedicated tool `tools/verify_robustness.py` is used for **Live Integration Testing** without hardware side-effects where possible.
- **Dynamic Discovery**: Fetches *real* entity IDs from the live HA instance (via `app.utils.ha_fetch`).
- **Mock ChromaDB**: Simulates the vector database in-memory to test resolution logic without polluting the production DB.
- **Ping Test**: Verifies API health.

## Resolved Issues (v2 Refactor)

### ✅ Fixed: Wrong Entity for "Watch" vs "Listen"
**Old Behavior**: "Watch Office TV" often selected the generic `media_player.office_tv` (Android Remote) even if `media_player.office_tv_chrome` was better for casting, or vice-versa.
**New Behavior**: 
- "Watch..." -> Prioritizes Android/Roku integration (Score: 100).
- "Play music..." -> Prioritizes Music Assistant/Cast (Score: 150+).

### ✅ Fixed: Synthetic Entity Pollution
**Old Behavior**: `ha_ingest.py` created "logical" entities for every group/zone, cluttering the DB.
**New Behavior**: Strict filtering of `group.` domain. Only physical/system-level entities are ingested.

### ✅ Fixed: Dependency Resilience
**Old Behavior**: Heavy AI dependencies (`langchain_chroma`) prevented lightweight tool usage.
**New Behavior**: `app/utils/ha_fetch.py` allows fetching HA data with only `requests`, enabling robust monitoring tools.

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
