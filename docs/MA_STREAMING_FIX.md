# Music Assistant Streaming Architecture & Fix Plan

## Executive Summary

The current local browser music playback in Jarvis uses a Flow proxy architecture to enable full-track playback from Music Assistant. The gateway resolves the MA stream URL via WebSocket, then proxies audio bytes from MA's stream server to the browser.

---

## Current Architecture

### Flow

```text
Browser <--> Gateway (port 8000) <--> MA Stream Server (port 8097)
               ^
               |
        MA WebSocket (ws://ma:8095/ws) — URL resolution only
```

### Implementation

- **Endpoint:** `GET /api/media/stream/music-assistant?uri=library://track/1759`
- **Handler:** `services/gateway/main.py` → `stream_music_assistant()` (lines 5218-5466)
- **Steps:**
  1. Resolve credentials (mass_url, mass_token) from identity service
  2. Discover MA players via JSON-RPC `players/all` + `player_queues/all`
  3. Select idle/playing player
  4. Connect to MA WebSocket and authenticate
  5. Send `player_queues/play_media` command with URI
  6. Poll `queue_updated` events for resolved stream URL (constructed from session_id + queue state)
  7. Proxy audio bytes from MA stream server (port 8097) through the gateway

### Problem (Resolved)

MA's `/preview` endpoint only provides ~30 second previews, not full tracks. This was replaced with the WebSocket flow stream proxy approach.

---

## Flow Proxy Architecture

### How It Works

The gateway acts as a WebSocket bridge to resolve the stream URL, then proxies audio bytes from MA's stream server to the browser. The browser plays via native `<audio src>` bound to the gateway endpoint.

#### MA Web-Player Architecture Reference

**Source:** `music-assistant/frontend/src/` components

#### Key Components Mapped

| Component | File | Purpose |
| --- | --- | --- |
| PlayerBrowserMediaControls | `layouts/default/PlayerOSD/PlayerBrowserMediaControls.vue` | Native `<audio :src="audio">` element |
| WebSocket Client | `plugins/api/index.ts` | Connects to `ws://ma:8095/ws`, handles auth + command dispatch |
| Active Audio Source | `composables/activeAudioSource.ts` + `activeSource.ts` | Reactive track/state tracking |
| Player Store | `plugins/store.ts` | Reactive queue state, current track, playback state |
| Play Button | `layouts/default/PlayerOSD/PlayBtn.vue` | Sends `player/play_media` via WebSocket |

#### WebSocket Protocol (URL Resolution)

```text
ws://ma-server:8095/ws
```

1. **Connection:** Gateway connects to `ws://ma:8095/ws` with token as query parameter
2. **Auth:** Token validated by MA WebSocket middleware on connect
3. **Command:** Sends `player_queues/play_media` with `{queue_id, media: uri, option: "replace", radio_mode: false}` (e.g., `library://track/1759`)
4. **State Sync:** MA broadcasts queue state updates via WebSocket containing:
   - Current track metadata
   - Queue state with `current_item` containing `queue_item_id` and `player_id`
   - Playback state (playing/paused/idle)
   - Seek position
5. **Browser:** Receives audio stream from gateway, plays via native HTML5 audio

#### Stream URL Construction (Gateway-side)

The gateway constructs the MA stream URL from the queue state after `player_queues/play_media`:

```python
session_id = str(uuid.uuid4())  # Generated per request
queue_item_id = current_item["queue_item_id"]
queue_id = queue_state.get("queue_id", target_player_id)
player_id = queue_state.get("player_id", target_player_id)
http_base = mass_url.replace("http://", "").replace("https://", "")
stream_url = f"http://{http_base}/flow/{session_id}/{queue_id}/{queue_item_id}/{player_id}.mp3"
```

#### Stream URL Extraction Order

The `MAWebSocketClient._extract_stream_url()` method checks these sources (highest priority first):

1. `current_item.media_item.stream_url`
2. `current_item.stream_url`
3. `items[*].stream_url`
4. `audio_player.stream_url`

---

## MA Streaming Server Reference

### Music Assistant Stream Server

**Source:** `music_assistant/controllers/streams/controller.py`

MA hosts an unprotected HTTP-only webserver (default port 8097) for streaming audio packets to players.

**Routes:**

| Route | Purpose |
| --- | --- |
| `/flow/{session_id}/{queue_id}/{queue_item_id}/{player_id}.{fmt}` | Continuous flow stream (crossfade, gapless) |
| `/single/{session_id}/{queue_id}/{queue_item_id}/{player_id}.{fmt}` | Single track stream |
| `/command/{queue_id}/{command}.mp3` | Command responses (next/silence) |
| `/announcement/{player_id}.{fmt}` | Announcement audio |

**Stream URL Generation:**

```python
# From StreamsController.resolve_stream_url()
base_path = "flow" if flow_mode else "single"
return f"{self._server.base_url}/{base_path}/{session_id}/{queue_id}/{queue_item_id}/{player_id}.{fmt}"
```

These URLs require valid `session_id`, `queue_id`, `queue_item_id`, and `player_id` that are generated when media is queued for playback on a player.

---

## Testing Plan

### Test URIs

| URI | Track | Artist |
| --- | --- | --- |
| `library://track/1759` | Thank You | Brandon Lake |
| `library://track/1011` | Just Like Heaven | The Cure |
| `library://track/735` | Help! | The Beatles |

### Verification Steps

1. Connect to MA WebSocket (`ws://ma:8095/ws`) with JWT token
2. Send `player_queues/play_media` with a known URI
3. Receive `queue_updated` event with queue state
4. Gateway constructs valid `http://ma:8097/flow/...` or `http://ma:8097/single/...` stream URL
5. Gateway proxies audio bytes from stream URL to browser
6. Browser `<audio src>` plays full track via gateway proxy
7. Test seeking functionality (Range headers supported)
8. Deploy via CI/CD

### Unit Tests

- `test_stream_abs_uses_dict_get_not_dot_notation` — ABS credential access via `.get()`
- `test_stream_ma_uses_dict_get_not_dot_notation` — MA credential access via `.get()`
- `test_stream_abs_missing_credentials_returns_400` — Graceful error for missing ABS config
- `test_stream_ma_missing_credentials_returns_400` — Graceful error for missing MA config
- `test_stream_ma_no_players_returns_404` — No players available error
- `test_stream_abs_identity_failure_returns_401` — Identity resolution failure
- `test_stream_ma_identity_failure_returns_401` — Identity resolution failure
- `test_stream_abs_credential_fields_accessed_correctly` — All ABS credential fields via `.get()`

---

## Key Files

| File | Purpose |
| --- | --- |
| `services/gateway/main.py` | `stream_music_assistant()` endpoint (lines 5218-5466), `stream_audiobookshelf()` (lines 5091-5204) |
| `services/gateway/ma_ws_client.py` | MA WebSocket client for stream URL resolution (607 lines) |
| `services/ui/src/pages/Media.tsx` | Frontend media player — local playback resolves MA stream URLs via gateway |
| `services/ui/public/ma-stream-test.html` | Sandbox test page for manual stream URL validation |
| `services/gateway/tests/test_media_streaming.py` | Unit tests for credential access and error handling |

## Environment Details

| Setting | Value |
| --- | --- |
| MA Server | `192.168.2.20:8095` (v2.8.9, HA addon) |
| MA Stream Port | 8097 (default) |
| MA WebSocket Port | 8095 (default) |
| Gateway Port | 8000 |
| JWT Token | Stored in `.env` |
| WebSocket Auth | Token via URL query parameter (`?token=xxx`) |

---

## Timeline

- [x] Research MA streaming architecture
- [x] Review official MA source code (`controllers/streams/controller.py`)
- [x] Implement MAWebSocketClient (607 lines)
- [x] Implement `stream_music_assistant()` with WebSocket flow stream proxy
- [x] Fix `stream_audiobookshelf` credential access (dict `.get()` pattern)
- [x] Fix indentation error in `get_ma_recent` endpoint (line 4736)
- [x] Write 8 unit tests for streaming endpoints
- [x] Update `Media.tsx` for local playback with dynamic MA/ABS stream URLs
- [x] Create sandbox test page (`ma-stream-test.html`)
- [x] All 8 tests passing
- [ ] Deploy via CI/CD (push microservices branch)
