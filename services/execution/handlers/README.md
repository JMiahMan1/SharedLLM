# Execution Handlers

Platform-specific media transport and announcement handlers. Each file is
isolated to prevent cross-contamination between device types.

## Handler Files

| File | Platform | Purpose |
| :--- | :--- | :--- |
| `roku.py` | Roku TVs/players | ECP launch, MA sibling delegation, transport commands |
| `android_tv.py` | Android TV / Google TV | ADB commands, media_player services |
| `webos.py` | LG WebOS TVs | media_player services, power management |
| `samsung.py` | Samsung Tizen TVs | media_player services, KEY_POWER fallbacks |
| `media.py` | All platforms | Unified routing — detects platform and delegates |
| `video.py` | All platforms | yt-dlp URL resolution and local streaming |

## Roku Music Playback Architecture

Roku devices do NOT support direct URL streaming via `media_player.play_media`.
Music playback uses a **two-part approach**:

### Step 1: ECP Launch (UI on Roku)
```
POST http://{roku_ip}:8060/launch/782875
Params: t=a, autoplay=true, songName=..., artistName=..., albumArt=...
```
This launches the Media Assistant channel (782875) with the rich music UI.

### Step 2: MA Sibling Delegation (Audio)
```
POST {ha_url}/api/services/music_assistant/play_media
Body: {
  "entity_id": "<ma_player_sibling>",
  "media_id": "<uri or query>",
  "media_type": "track",
  "enqueue": "play"
}
```
This streams the actual audio through Music Assistant to the Roku's audio output.

### MA Player Sibling Resolution
The system finds the MA player by:
1. Getting the Roku entity's `friendly_name`
2. Searching all `media_player.*` entities for one with matching name AND
   `active_queue`, `mass_player_type`, or `integration == "music_assistant"`

### Key Functions in `roku.py`

| Function | Description |
| :--- | :--- |
| `is_roku_device()` | Detects Roku via entity_id, app_id, source_list patterns |
| `get_roku_ip()` | Discovers IP via HA device registry or SSDP broadcast |
| `find_ma_player_sibling()` | Resolves MA player entity for audio delegation |
| `roku_play_music()` | Orchestrates the full two-part music flow |

## Routing (`media.py`)

`media.py` detects the platform and delegates:

```python
is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
if is_roku:
    return await roku_handler.roku_play_music(...)
# ... other platforms handled separately
```

This keeps each platform's logic isolated and maintainable.
