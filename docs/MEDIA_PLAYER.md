# Jarvis OS 2.0: Media Player System & UI Specifications

This document outlines the architecture, visual guidelines, and API interfaces for the Jarvis OS 2.0 Media Player. The system supports casting playback to remote devices via Home Assistant (HA) and Music Assistant (MA), as well as playing streamable audio locally inside the browser or native mobile client using a secure gateway proxy.

---

## 1. Architectural Overview

The Jarvis Media Player is powered by a dual-backend architecture integrated into a unified frontend interface:

```mermaid
graph TD
    UI[React/Ionic Frontend Pages/Media.tsx] -->|REST & WebSocket| GW[Gateway Service services/gateway/main.py]
    GW -->|Bearer auth / API proxy| MA[Music Assistant http://192.168.2.20:8095]
    GW -->|Bearer auth / API proxy| ABS[Audiobookshelf]
    GW -->|Command Dispatch| EX[Execution Service services/execution/main.py]
    EX -->|Home Assistant API| HA[Home Assistant 192.168.2.20:8123]
    HA -->|Controls physical speakers| Player[media_player.* / mass_* entities]
```

### 1.1 Remote Media Playback (Casting)
Remote playback targets physical speakers or TV media players managed by Home Assistant. 
* **Announcements & TTS:** Handled via Kokoro TTS, broadcasted through local room speakers.
* **Music Casting:** Controlled via Music Assistant v2. The execution service dispatches play commands directly using the HA integration helper (`music_assistant.play_media`), targeting specific media player entities (e.g. `media_player.mass_living_room`).
* **Audiobook Casting:** Dispatched directly to Audiobookshelf servers or casted to target players.

### 1.2 Local Media Playback (Browser Mode)
Local playback uses the browser's native `<audio>` element (or Capacitor's Native Audio player on Android). 
* Streamed data is brokered via the Gateway proxy, injecting credentials and handling HTTP range requests.
* **Audiobookshelf Stream:** Fetched directly from ABS endpoints with token auth.
* **Music Assistant Stream:** Proxied via the `/api/media/stream/music-assistant` gateway endpoint, which maps library URIs to Music Assistant's native `/preview` HTTP endpoint.

---

## 2. Visual Design & UI Specifications

In alignment with the **"Neon Glass"** design system, the Media Player uses semi-transparent cards, sharp typography, and cyan-glowing accents.

### 2.1 The Dashboard Layout (`/media`)

```text
+-------------------------------------------------------------+
| [Back]                    MEDIA HUB                         |
+-------------------------------------------------------------+
|  +-------------------------------------------------------+  |
|  |               ACTIVE PLAYBACK TARGET                  |  |
|  |  [Icon] Currently Playing: Living Room Speaker   [Cast] |  |
|  +-------------------------------------------------------+  |
|                                                             |
|   RECENTLY PLAYED (MA)            PLAYLISTS (MA)            |
|   +-------------------+           +-------------------+     |
|   | Thank You         |           | Morning Vibes     |     |
|   | Brandon Lake      |           | 14 Tracks         |     |
|   +-------------------+           +-------------------+     |
|   | Graves Into G...  |           | Workout Mix       |     |
|   | Elevation Wors... |           | 32 Tracks         |     |
|   +-------------------+           +-------------------+     |
|                                                             |
|   AUDIOBOOKS (ABS)                                          |
|   +-------------------------------------------------------+ |
|   | [Cover Art]  The Hobbit                               | |
|   |              J.R.R. Tolkien (84% completed)           | |
|   +-------------------------------------------------------+ |
|                                                             |
|=============================================================|
|                    PERSISTENT PLAYER BAR                    |
|   [Cover] Title - Artist          [Prev] [Play/Pause] [Next]|
|   Progress bar [================------------------------]   |
+-------------------------------------------------------------+
```

### 2.2 Aesthetic Guidelines

* **Theme Color:** Cyan (`#06b6d4`) glows are used strictly for media-related elements. 
* **Backdrop Blur:** Floating action drawers and player sheets use `backdrop-blur-xl` with a semi-transparent dark background (`rgba(15, 23, 42, 0.75)` / Slate 900).
* **Player Hero Card:**
  * Displays high-resolution cover art fetched through the `/api/media/imageproxy` endpoint.
  * Adds a subtle radial shadow using the dominant color of the cover art to simulate an ambient light halo.
* **Sliders & Controls:**
  * Draggable progress bars and volume sliders glow cyan when active.
  * Symmetrical, frosted-glass circular control keys.

---

## 3. Backend Interfaces & API Protocols

### 3.1 Streaming Gateway Endpoints

#### Local Music Assistant Stream
* **Endpoint:** `GET /api/media/stream/music-assistant`
* **Query Params:** `uri` (e.g. `library://track/1759`)
* **Headers:** `Authorization: Bearer <jarvis_api_token>`
* **Description:** Resolves the track's media provider details via MA's JSON-RPC endpoint (`music/item_by_uri`), retrieves the active provider instance and item ID, and proxies the chunks directly from MA's `/preview` endpoint.
* **Supported response headers:** `Content-Range`, `Content-Length`, `Content-Type` (properly proxied to support seeking in browser/mobile audio players).

#### Local Audiobookshelf Stream
* **Endpoint:** `GET /api/media/stream/audiobookshelf/{id}`
* **Query Params:** `token` (Jarvis API key)
* **Description:** Streams direct audio files from ABS to the local client.

---

### 3.2 Execution Service Commands

All remote casting requests are sent through `POST /execute/media/play`:

```json
{
  "user_context": {
    "user": "default",
    "is_admin": true
  },
  "entity_id": "media_player.mass_kitchen_speaker",
  "query": "library://track/1759",
  "media_type": "music"
}
```

* **Direct URI Playback:** If `query` contains a scheme identifier (`://`), the handler immediately bypasses text search and invokes the Home Assistant `play_media` service with `media_id` set to the URI.
* **Text Fallback:** If `query` is a simple text string (e.g. "play some jazz"), the execution service calls `music_assistant.search` to resolve the best match before playing.

---

### 3.3 State Syncing & Polling

The frontend tracks the active playback state using two mechanisms:
1. **Long Polling (`GET /api/media/status`):**
   * Dispatched every 3-5 seconds to check the state of the active speaker.
   * Returns details of active `media_title`, `media_artist`, `position`, `duration`, `volume_level`, and `state` (`playing` / `paused` / `idle`).
2. **Local Audio Sync (`POST /api/media/state/sync`):**
   * When in Local (browser) playback mode, the client reports its own `<audio>` player state to the backend.
   * Registers `entity_id: "local_player"` so that other dashboards in the house know the active user is currently listening to music locally.

---

## 4. Troubleshooting and Edge Cases

* **Audio/Video Mismatch (Resolved):** In previous iterations, requesting a local stream for a library track would fall back to a YouTube search if a direct stream URL was not returned in the track metadata. This resulted in video audio being played. The current system resolves this by fetching audio directly from Music Assistant's native `/preview` streaming route.
* **Token Suffixes:** Always attach the `token` parameter when requesting streaming audio through media tags in HTML, as the browser's source tags do not allow custom HTTP request headers.
