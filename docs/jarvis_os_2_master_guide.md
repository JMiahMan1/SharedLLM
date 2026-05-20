# Jarvis OS 2.0: Master Development Guide

## 1. Architectural Vision & Scope
Jarvis OS 2.0 transforms a standard smart home dashboard into an **Ambient Computing Environment**. Built to scale effortlessly for a large household (7+ users), it dynamically adapts to each user's role, preferences, and physical location within the home.

### 1.1 Core Constraints & Stack
The system operates on a highly optimized **Dual-Node Architecture**:

1.  **Application Node (`jeremiah@ai`):** An Intel N150 Mini PC (16GB RAM, 512GB NVMe). This node hosts the core Python/FastAPI microservices (Gateway, Execution, Identity, Storage, RAG).
2.  **Inference Node (LLM Host):** An AMD Ryzen 7 5700G with an NVIDIA RTX 4060 (8GB VRAM) and 32GB RAM. This machine runs the LLM engines as a separate service. All SharedLLM microservices resolve it via a **DNS Sync Sidecar** (`config/dns_sync.py`) that exposes the hostname `ollama-server.local` and supports **multi-IP fallback** (e.g., `192.168.2.114`, `192.168.4.179`, `192.168.1.204`). The DNS sidecar performs live health checks on each IP and only advertises alive IPs, automatically failing over if the primary is unreachable.
    *   **Alpaca Wrapper:** Because we run a specialized image for **TurboQuant** (`ghcr.io/thetom/llama-cpp-turboquant@sha256:fe33d9ca6d2331e1af4cde907475b3d3040eb7498e807165ff89db3770dacb04`), we use a custom wrapper service called Alpaca. This allows us to run standard Ollama models alongside the highly-quantized models.
    *   **TurboQuant Settings:** The inference engine runs with specialized flags: `--n-gpu-layers 999`, `--n-cpu-moe 48`, `--no-mmap`, `--cache-type-k turbo4`, and `--cache-type-v turbo3`.
    *   **Source vs Workspace Rule (Critical):** The SharedLLM Server has two distinct path concepts, configured via the workspace registry settings: the **source repo** (the only valid build/deploy context, path defined by `WORKSPACE_HOST_PATH` in the environment) and the **workspace root** (Raven's runtime scratch space — ephemeral, managed by the `workspace_runtime` service, never used as a Docker build context).

*   **Frontend:** React, Vite, Tailwind CSS ("Neon Glass" aesthetic).
*   **Wrapper Layer:** Ionic Capacitor for Android phones and wall-mounted tablets.

### 1.2 The Three-Tier Request Hierarchy
Based on the system `README.md`, Jarvis utilizes a strict semantic routing hierarchy to balance speed and VRAM:
1.  **Tier 1 (FastPath):** Local semantic matcher using the `BAAI/bge-small-en-v1.5` embedding model. Bypasses the LLM entirely for common home automation queries (e.g., "Turn off lights"). Latency: `<100ms`.
2.  **Tier 2 (Librarian):** Standard single-turn tool access via the LLM context. Latency: `1-3s`.
3.  **Tier 3 (Raven):** Multi-step autonomous agent running up to 30 iterations in a sandboxed `Workspace Runtime`.

### 1.3 Network Routing & Isolation (Caddy)
The entire microservice architecture is strictly isolated behind a Caddy reverse proxy acting as the sole entry point on **Port 80**.
*   **Frontend Traffic (`/*`)**: Routed to the React `ui:8008` container.
*   **API Traffic:**
    *   `/api/chat`, `/api/generate`, `/api/admin/raven`: Routed to `gateway:11435`.
    *   `/execute`: Routed directly to `execution:8003` (FastAPI API port). **Note:** media files (TTS audio, video) are served on the separate **media port `:8888`** by a lightweight `HTTPServer` thread running inside the execution container. This split was introduced to prevent the FastAPI event loop from blocking on large file transfers.
    *   `/api/auth`, `/api/users`: Routed to `identity:8001`.
    *   `/control_plane`: Routed to `control_plane:8008`.
This guarantees that frontend developers only ever need to talk to `localhost:80` (or the server's IP) and Caddy securely routes the requests to the Docker internal network.

> [!IMPORTANT]
> **`ai-server` is a Docker-internal alias only — not a real hostname.** In `docker-compose.yml`, the `extra_hosts` block maps the label `ai-server` to the SharedLLM server's static IP so that containers can resolve it internally. The actual server hostname on the local network is **`ai.local`** (mDNS). Do not attempt to use `ai-server` as a hostname outside of Docker. If mDNS is unavailable, fall back to the static IP configured in `EXECUTION_EXTERNAL_HOST` in your `.env`.

---

## 2. The Universal Integration Architecture

To ensure the frontend remains clean, modular, and future-proof, **we will not hardcode specific integrations into the UI**. Instead, we will implement a decoupled, capability-driven architecture. 

### 2.1 Backend Plugin Registry & Dynamic Forms
*   **Abstract Providers:** The backend utilizes base classes (e.g., `ChoreProvider`, `MediaProvider`).
*   **Zero Frontend Debt:** The backend exposes `GET /api/integrations/available`. This returns a JSON schema of required auth fields. The `/admin/integrations` React page dynamically generates the configuration forms based on these schemas.

### 2.2 Normalized Capability Widgets
The frontend uses generic **Capability Widgets** (`MediaWidget`, `ChoreWidget`). The backend normalizes the raw API response from any integration into a standard JSON payload before sending it to the UI via WebSockets (`/ws/capabilities`).

---

## 3. Code-Level Integration UI/UX Breakdown

Based on an exhaustive analysis of the `SharedLLM/services/execution/` and `SharedLLM/services/gateway/` architectures, here is exactly how the backend connects to the new Jarvis OS 2.0 Neon Glass UI.

### 3.1 Music Assistant (MASS) & Home Assistant Media
*   **Backend Reality:** `execution/handlers/media.py` utilizes `resolve_mass_entity()` to natively call the HA `music_assistant` service via `MASS_CONFIG_ENTRY_ID`. The FastAPI route `/execute/media/play` maps to the `mediaplayrequest` tool.
*   **Jarvis OS 2.0 Enhancements:** 
    *   **Active Media Widget (Cyan Glow):** When MASS initiates playback, this widget drops into the Capability Matrix. 
    *   **Deep UI Linking:** Because MASS returns robust JSON, the React UI will natively display high-res Album Cover Art, the upcoming MASS Queue, and provide tactile transport controls querying `/execute/media/transport`.

### 3.2 Audiobookshelf (ABS)
*   **Backend Reality:** `execution/handlers/audiobookshelf.py` directly interfaces with the ABS API bypassing HA for metadata. It features complex logic to track `duration`, `currentTime`, and resume progress (`_handle_resume`, `_handle_progress`). It pipes the direct MP4 stream back to HA via `play_media`. Maps to the `audiobookshelfrequest` tool.
*   **Jarvis OS 2.0 Enhancements:** 
    *   **"Continue Reading" Widget:** Leverages the precise tracking in `_handle_progress`. Shows the exact progress percentage and a beautifully formatted `_format_time` string (e.g., "3h 15m remaining") underneath the book cover. Tapping the widget instantly triggers `_handle_resume` on the room's default speaker.

### 3.3 Raven Autonomous Engine, Ops & The Control Plane
*   **Backend Reality:** 
    *   `gateway/agent_loop.py` manages multi-step missions, utilizing Redis Checkpoints (`raven:checkpoint:{mission_id}`) and `_compress_context()` to prevent token bloat. 
    *   `execution/handlers/workspace.py` handles AST parsing, ripgrep (`handle_workspace_search`), and `difflib.SequenceMatcher` for fuzzy file patching (`handle_workspace_patch`).
    *   `execution/handlers/git.py` securely injects tokens (`github_token`) and dynamically prevents LLM branch hallucinations.
    *   **The Control Plane (`control_plane/main.py`):** Runs on port 8008 and connects directly to the host Docker socket. Secured via `X-Internal-Secret`, it allows the LLM and the UI to securely fetch logs, restart microservices (`/api/restart/sharedllm_gateway`), and execute shell commands inside running containers (`/api/containers/.../exec`).
*   **Jarvis OS 2.0 Enhancements:** 
    *   **Raven Ops Panel (Admin Center):** Subscribes to the Redis stream (`raven:mission:stream:{mission_id}`) and transforms raw JSON logs into a sleek, vertical Operations Timeline. It also natively integrates with the **Control Plane**, providing UI buttons for Admins to view live Docker logs or restart crashed services directly from the React dashboard.
    *   **Interactive Commits:** When Raven executes `/execute/git` (`git_commit`), the UI generates a "Commit Card" linking directly to the GitHub PR. Admins can view a visual diff natively before allowing Raven to push.

### 3.4 Nextcloud Talk, Jarvis Bot & Communications
*   **Backend Reality:** 
    *   `execution/main.py` hosts `/execute/tts` which utilizes the local Kokoro ONNX model (`kokoro-v1.0.onnx`) for lightning-fast voice generation.
    *   `execution/handlers/talk.py` handles the `talkrequest`. It reads chat via `/ocs/v2.php/apps/spreed/...` and posts via `action="send"`.
    *   Its `send_voice` action integrates directly with the TTS engine. It generates an audio payload, uploads it to `Talk Uploads`, and posts it as a native voice message inside a Nextcloud Talk chat.
    *   *Deep-Dive Architecture Finding (Webhooks):* To eliminate fragile polling loops, the system implements a native Nextcloud Talk Bot (`POST /api/talk/webhook`). When a user @mentions Jarvis in a Nextcloud chat, Nextcloud fires a webhook directly to the Gateway, instantly enqueueing an inference job.
*   **Jarvis OS 2.0 Enhancements:** 
    *   **Jarvis as an Interactive Chat Bot:** Jarvis constantly monitors configured Nextcloud Talk channels. Users can @Jarvis in their Nextcloud app to trigger agent logic externally. Taking inspiration from classic IRC bots, Jarvis will also host **interactive chat games** directly in the channel. This includes Bible Trivia, simple number/letter games for kids, and educational, Bible-oriented games to assist children with math and reading.
    *   **Remote Ambient Voice Notifications:** Instead of sending standard text push notifications to the user's Android phone, Jarvis uses the Kokoro TTS pipeline to drop a native Voice Note directly into the user's Nextcloud Talk app (e.g., "Sir, the garage door has been left open.").
    *   **The Smart Inbox & Native Chat Client:** Moves local IMAP/Talk communication into a card-based widget showing AI-triaged summaries. Crucially, the Neon Glass UI allows this specific widget to **expand into a Full-Screen View**, giving the user a native Nextcloud Talk chat experience directly within Jarvis OS. A future, dedicated **Native Chat Client** app is planned, offering a full-screen, high-fidelity chat experience with Jarvis and other household members without leaving the dashboard.

### 3.5 NotebookLM-Style Context & Nextcloud Notes
*   **Backend Reality:**
    *   `handlers/note.py` directly interfaces with Nextcloud WebDAV to execute the `noterequest` tool. Crucially, it features a `sync_rag` action that recursively walks note directories and pipes them into the local RAG indexing pipeline.
    *   `handlers/calendar.py` directly interfaces with Nextcloud CalDAV (via `calendarrequest`) to parse dates (`dateparser`) and inject events.
    *   *Deep-Dive Architecture Finding:* The system also runs an asynchronous background task (`extract_user_facts`) that continuously monitors conversation history in Redis. It autonomously extracts durable preferences and saves them into a specialized `user_facts` ChromaDB collection.
*   **Jarvis OS 2.0 Enhancements:**
    *   **Long-Term "NotebookLM" Memory:** Jarvis treats Nextcloud Notes as its dynamic brain. The LLM can autonomously execute an `/execute/note` action (`action="append"`) to write a short "memory" file about user preferences (e.g., "User prefers 70-degree climate at night"). Through the `sync_rag` pipeline, this becomes instant semantic context for all future conversations.
    *   **Autonomous Calendar Parsing:** If Jarvis generates or reads a note that contains temporal context (e.g., "Dentist appointment next Tuesday at 4pm"), it possesses the agency to immediately extract that date and execute an `/execute/calendar` (`action="add"`) request to permanently lock it into the user's CalDAV calendar.
    *   **Quick Notes Widget (Yellow Glow):** Tapping a note widget seamlessly expands it into a full-screen markdown editor.

### 3.6 Alarms, Timers & Announcements
*   **Backend Reality:** 
    *   `execution/handlers/timer.py` handles the `timerrequest`. It extracts duration semantics (`10m`, `dateparser` logic) and persists state into Redis under `timer:{user_id}:{timer_id}`. Crucially, the schema differentiates between a **Countdown Timer** (e.g. "10 minutes") and an **Alarm** (e.g. "Tomorrow at 7am"). 
    *   When a timer or alarm expires, the Automation scheduler calls `/execute/trigger` in `main.py`. This endpoint securely resolves the `UserContext` and can either dispatch an audio alert or trigger an entirely separate backend task (e.g., turning on the lights when an alarm fires). *(Note: The current codebase hardcodes a legacy `media_source://tts/google` string here; Jarvis OS 2.0 will upgrade this to route directly through our local Kokoro ONNX `/execute/tts` pipeline).*
    *   `/execute/announce` handles synchronous `AnnouncementRequest` actions. 
    *   **The Seamless Restoration Goal:** When an announcement fires, the system must capture the target media player's exact initial state, cast the audio, and then restore the state. Because different protocols (Sonos, Google Cast, Music Assistant) handle state radically differently, a major technical goal is building **per-device integration logic** that restores the previous media queue and playback position as perfectly as possible, ensuring the interruption to the user is minimal.
*   **Jarvis OS 2.0 Enhancements:** 
    *   **Ambient Countdown vs Alarm Widgets:** A countdown timer drops onto the React dashboard as a visually decreasing, glowing ring. Conversely, an Alarm appears as a static, persistent card in the Time Management panel.
    *   **Task Chaining Triggers:** Both Timers and Alarms will be upgraded to accept "Task Payloads". Instead of just playing an audio alert, an alarm can be configured to autonomously execute a `NightModeRequest` or `LightControlRequest` upon expiration.
    *   **Location-Aware Audio Triggering:** When a timer expires, instead of blindly casting to the `target_device` created 30 minutes ago, Jarvis OS will ping ESPresense first. If the user has moved from the Kitchen to the Living Room, the audio trigger dynamically routes to the Living Room speaker.
    *   **Dual-Mode Announcements & Blacklisting:** The UI will feature an **Announce ALL** button that broadcasts to every media player in the house, explicitly ignoring devices on an admin-configurable **Blacklist** (e.g., kids' bedrooms after 8 PM). Announcements come in two forms:
        *   **Recorded:** The user taps and holds a microphone icon to record their real voice, sending the raw audio file to the `execute/announce` endpoint.
        *   **Emoji-Enhanced TTS:** Standard text-to-speech, but admins can map custom audio files (`.mp3`, `.wav`, `.ogg`) to specific emojis. When a TTS announcement contains a mapped emoji, the backend intercepts it and splices in the sound effect at the exact position in the audio stream. See **Section 6.9** for full backend design and **Section 10.7** for the Admin UI spec.

### 3.7 Skylight (Chore Management)
*   **Backend Provider:** `ChoreProvider`
*   **Jarvis OS 2.0 Enhancements:** 
    *   **Bidirectional Sync:** Completing a task in the UI immediately updates the physical Skylight board API.
    *   **Child Progress Rings:** Apple Watch-style concentric, glowing rings representing daily chore completion percentage.

### 3.8 ESPresense (BLE Localization)
*   **Backend Provider:** `PresenceProvider`
*   **Jarvis OS 2.0 Enhancements:** 
    *   **The "Halo" Hero Banner:** Dynamically renders text like "You are in the Living Room" based on BLE MQTT data.
    *   **Safe Fallback:** If BLE fails, defaults to the user's assigned "Home Room" with secondary rooms available via swipe tabs.

### 3.9 Porcupine (Voice Assistant)
*   **Backend Provider:** Ionic Capacitor Audio Plugin -> `VoiceProvider`
*   **Jarvis OS 2.0 Enhancements:** 
    *   **Assistant Overlay:** When "Jarvis" is spoken, a frosted glass overlay blurs the screen, rendering an audio wave visualizer.
    *   **Voice ID Security Fallback:** If Voice ID confidence is `< 80%`, the system routes the query to a restricted "Guest" profile.

### 3.10 Video & YouTube Casting Engine
*   **Backend Reality:** `execution/handlers/video.py` handles the `videoplayrequest`. It uses `yt-dlp` to search YouTube and extract the most compatible direct MP4 stream (`avc1/mp4a`) for Cast/Roku devices. **Progressive download** (`download_video_progressive()`) returns control after 5MB buffered — the file continues downloading in the background while playback starts immediately. The file is hosted on the Execution service's **media server (port 8888)** via FastAPI `FileResponse` with HTTP Range support. Roku devices are launched via ECP to Media Assistant (app 782875); Cast/WebOS/Samsung/Android devices receive the URL via `media_player.play_media`. Wake-up logic for Roku is consolidated in `roku.roku_wake_device()` and called in parallel with download via `asyncio.create_task()`.
*   **The "Why":** We proxy videos this way because native YouTube apps on smart devices (like Roku) are notoriously difficult to control via APIs. By downloading the direct MP4 and hosting it locally, Jarvis can force *any* media device in the house to play the video or audio, even if that device doesn't have a YouTube app installed.
*   **CRITICAL:** NEVER use `download_video()` — it waits for full download before returning (causes 3+ minute timeouts). Progressive download is the only supported mode.
*   **YouTube Search:** Uses `yt-dlp` `ytsearch:1` for accurate results. SearXNG HTML parsing is a fallback only — the regex-based HTML approach is unreliable and often returns sidebar recommendations instead of the actual search result.
*   **Android TV Video Delegation:** Android TV's `play_media` with local stream URLs often fails (HA 500 error). The proven approach: detect Android TV via `media_player.office_tv` (`device_class=tv`, `app_id` contains `com.google.android.*`), find its Cast sibling via `_find_cast_sibling()` in `handlers/android_tv.py`, and delegate video playback to the Cast entity. The Cast sibling is found by:
    1.  **Capability checks:** `supported_features & 8424` (SUPPORT_PLAY_MEDIA), `cast_type`, entity ID hints (`_chrome`, `_cast`)
    2.  **MA exclusion:** `app_id != "music_assistant"`, no `mass_player_type`, no `active_queue`
    3.  **Name matching:** substring or exact friendly name match (last resort)
    The Android TV handler powers on the TV, sends `nav_home` via ADB, stops any active Cast session, downloads the video, and casts to the sibling.
*   **Fast-Path Routing for Power Commands:** `turn_on`/`turn_off` in the gateway fast path now uses `media_type="power"` in `resolve_media_target()`, which scores `device_class=tv` entities highest (+200) and deprioritizes Cast (-100) and MA wrappers (-200). This ensures power commands target the actual TV entity, not a Cast or MA sibling.
*   **Jarvis OS 2.0 Enhancements:**
    *   **Universal Video Cast Widget:** Because the backend proxies the video, the UI can render a specialized video transport widget showing the YouTube thumbnail and providing standard, highly-responsive controls (skip, pause, stop) that work flawlessly across all devices—bypassing clunky native TV interfaces.

### 3.11 Browser Engine & External Web Search
*   **Backend Reality:** `execution/handlers/browser.py` powers Jarvis's internet access. 
    *   **SearXNG Search:** Search queries are routed to an external, self-hosted SearXNG instance at `search.sumemail.com`. The backend uses the native JSON API first, but features a highly robust fallback that spins up Headless Chromium (Playwright) to physically scrape the SearXNG HTML if the JSON endpoint fails.
    *   **Web Reading & Auth Injection:** When reading a URL, Playwright renders the DOM and uses `html2text` to compress it into Markdown (capped at 15k characters). Crucially, the backend can inject the user's `jarvis_api_key` directly into Chromium's cookies, allowing the LLM to read and summarize authenticated internal dashboards.
*   **Jarvis OS 2.0 Enhancements:**
    *   **Search Results Widget:** When Jarvis performs a web search, the UI renders an expandable carousel of sources, allowing the user to click through to the original articles the LLM used for its answer.

### 3.12 Identity & Credential Vault
*   **Backend Reality:** `services/identity/main.py` is the source of truth for user profiles, API keys, and device assignments. It runs its own SQLite database and uses `crypto.py` (Fernet symmetric encryption) to heavily encrypt all third-party credentials (HA tokens, GitHub tokens, Nextcloud passwords). The Execution module calls `/api/resolve` before *every* tool use. The Identity service attempts to resolve the user in this exact order:
    1.  **Voice ID:** Biometric fingerprint matching from Porcupine.
    2.  **Device ID:** The MAC/UUID of the wall-mounted tablet requesting the action.
    3.  **API Key:** Standard web token.
    4.  **Fallback:** If all fail, defaults to User ID 1 (The generic "Home" System Account).
*   **Jarvis OS 2.0 Enhancements:**
    *   **External User Import:** To prevent manual data entry for large families, Identity can query Nextcloud (`/ocs/v1.php/cloud/users`), Home Assistant (`/api/config/auth/users`), or Skylight APIs to batch-import existing user accounts, automatically creating local profiles and linking their respective authentication tokens.
    *   **Admin Profiles UI:** The frontend will include an Admin User Management panel to manage these encrypted credentials securely without touching `.env` files.

### 3.13 Power Consumption & Energy Intelligence
*   **Backend Reality:** Jarvis actively ingests telemetry from Home Assistant power sensors (smart plugs, main electrical panels, solar inverters, EV chargers). Because the LLM has access to chronological state history via the RAG system and `ha_client.py` logbook queries, it can analyze power draw over time.
*   **Jarvis OS 2.0 Enhancements:**
    *   **Proactive Energy Management:** Jarvis can detect anomalies (e.g., "The garage heater has been drawing 1500W for 4 hours while no presence is detected") and take autonomous action to suspend the device, simultaneously sending a voice notification via Nextcloud Talk.
    *   **Energy Insights Widget:** A sleek, glowing UI card that visualizes real-time household power draw. Instead of just showing a static number, the LLM generates a dynamic, human-readable summary (e.g., "Power usage is 30% lower than yesterday. Solar production is covering all active loads.").
    *   **Device & Group Telemetry Monitoring:** Devices and groups can be tagged for ongoing telemetry tracking. See **Section 3.15** for the full monitoring and LLM pattern analysis system.

### 3.16 Household Intercom System

The intercom system provides two distinct communication modes depending on device type:
- **True two-way voice** (tablet ↔ tablet, web browser ↔ web browser) — requires a dedicated real-time audio server
- **One-way broadcast** (tablet/speaker → TV or smart speaker) — handled by the existing `announce_handlers.py` pipeline

> [!NOTE]
> **Why not Nextcloud Talk?** Although Nextcloud Talk is already in the stack, its WebRTC layer is not programmatically accessible from Python. The signaling protocol (Spreed/NATS) is internal-only and not a supported integration surface. Nextcloud Talk can only be used as a **chat notification bridge** (sending a message that a call is coming in), not for audio streaming.

---

#### Recommended Backend: LiveKit (Primary Recommendation)

**[LiveKit](https://docs.livekit.io/)** is an open-source, self-hosted WebRTC SFU (Selective Forwarding Unit) written in Go. It is the most capable option and the best fit for the existing architecture because it has a **first-class Python SDK and AI Agents framework** — meaning it can be integrated directly with our Kokoro TTS and Kokoro STT pipelines.

| Property | Details |
| :--- | :--- |
| **License** | Apache 2.0 (fully open source) |
| **Self-hosted** | Yes — single Docker container |
| **Python SDK** | `pip install livekit livekit-agents` |
| **Latency** | Sub-100ms (true WebRTC SFU) |
| **AI Agent support** | Native — STT/TTS/LLM pipeline integration |
| **Docs** | [docs.livekit.io](https://docs.livekit.io/) |
| **GitHub** | [github.com/livekit/agents](https://github.com/livekit/agents) |

**Docker deployment (add to `docker-compose.yml`):**
```yaml
livekit:
  image: livekit/livekit-server:latest
  ports:
    - "7880:7880"   # HTTP API + WebSocket signaling
    - "7881:7881"   # TURN/TLS
    - "7882:7882/udp"  # RTP media
  environment:
    - LIVEKIT_KEYS=devkey:secret
  networks:
    - sharedllm_default
```

**How two-way intercom works with LiveKit:**
1. User A taps `[Call Room: Kitchen]` in the Jarvis UI.
2. Gateway calls LiveKit API to create a **Room** (`kitchen-intercom-{session_id}`).
3. Gateway issues a short-lived **JWT token** to User A's browser and to User B's wall tablet (both in the same room).
4. Both browsers join the room via the LiveKit JS client SDK — WebRTC negotiation is handled automatically.
5. **Audio flows directly peer-to-peer** (or via the SFU if behind NAT) — full duplex, sub-100ms latency.
6. When the session ends, the room is destroyed. No audio is stored unless explicitly recorded.

**Jarvis integration hooks:**
- The Gateway exposes `POST /api/intercom/start` → creates LiveKit room, returns tokens for both parties.
- The Gateway exposes `POST /api/intercom/end` → terminates the LiveKit room.
- A **LiveKit Python Agent** can optionally join the room as a silent listener to transcribe the conversation (via Kokoro STT) for Jarvis context — useful for voice-commanded actions during a call (e.g., "Jarvis, turn on the kitchen lights" said during an intercom session).

---

#### Simpler Fallback: Mumble + pymumble

For installations that don't need AI agent integration or low-latency WebRTC, **[Mumble](https://www.mumble.info/)** (server: Murmur) is a battle-tested, extremely low-resource open-source voice chat server with a Python client library.

| Property | Details |
| :--- | :--- |
| **License** | BSD/GPL |
| **Python client** | `pip install pymumble-py3` + `libopus` |
| **Latency** | ~20–60ms (Opus codec) |
| **Headless clients** | Yes — runs on Raspberry Pi, wall tablets, etc. |
| **Resource usage** | Very low (~10MB RAM for server) |
| **Best for** | Always-on whole-house intercom without WebRTC complexity |

**Mumble intercom pattern:**
- Murmur server runs as a Docker sidecar.
- Each wall tablet or smart display runs a headless `pymumble` client auto-connecting to a room matching its room name (e.g., `kitchen`, `bedroom`).
- Pressing `[Talk]` in the UI triggers the `pymumble` client to start transmitting microphone audio.
- All other connected clients in the same channel hear it in real time.

> [!IMPORTANT]
> **Echo cancellation is critical for Mumble.** Without hardware or software AEC (Acoustic Echo Cancellation), speaker feedback creates an unusable loop. For wall tablets with both a speaker and mic, use `webrtcvad` + `speexdsp` for software AEC, or use hardware that includes it (e.g., ReSpeaker mic arrays).

---

#### One-Way TV Overlay Intercom

TVs cannot respond (no mic), so the intercom is strictly one-way. Audio is delivered via the **existing `announce_handlers.dispatch_announce()` pipeline** — no new infrastructure required:

- **Roku:** ECP launch of Media Assistant with `t=a`, sender name as `songName` — already implemented in `announce_roku`.
- **Cast/Android TV/WebOS/Samsung:** `media_player.play_media` with the audio URL.
- **Visual overlay (future):** Android TV via ADB intent, WebOS via `webostv.command` notification banner.

---

#### Intercom Session Mode Comparison

| Mode | Technology | Direction | Devices |
| :--- | :--- | :--- | :--- |
| **True two-way call** | LiveKit (WebRTC SFU) | Full duplex | Browser ↔ Browser / Tablet ↔ Tablet |
| **Always-on intercom** | Mumble + pymumble | Full duplex | Any device with mic + speaker |
| **Broadcast / PA** | `announce_handlers` | One-way out | All speakers + TVs |
| **TV announcement** | `announce_handlers` | One-way | TVs (Roku/Cast/WebOS/Samsung) |

#### ESPresense Integration
If BLE presence is enabled, the intercom defaults the **target** to the room where the recipient was last seen (resolved via ESPresense MQTT → `ha:presence:{user_id}` Redis key), rather than requiring manual device selection.

### 3.17 Native Android Mobile App (Ionic Capacitor)

To support hands-free operations, persistent whole-house voice integration, and lower intercom latencies on Android phones and wall tablets, the React frontend is wrapped in a native **Ionic Capacitor** container (`@capacitor/android`).

#### Core Mobile App Stack & Project Structure

- **WebView Core:** Vite + React + TypeScript + TailwindCSS compiled to static web assets in `services/ui/dist/`
- **Native Bridge:** Capacitor Core (`@capacitor/core`) and CLI (`@capacitor/cli`)
- **Android Target:** Android Studio Gradle project located in `/services/ui/android/`
- **Config file (`capacitor.config.json`):**
  ```json
  {
    "appId": "com.jarvisos.app",
    "appName": "Jarvis OS",
    "webDir": "dist",
    "bundledWebRuntime": false
  }
  ```

---

#### 1. Native Foreground Service (Persistent Connectivity)

To prevent Android's strict Doze Mode or App Standby from killing websocket streams and the local wake-word listener, the app implements a custom **Android Foreground Service** via a Capacitor plugin wrapper (`@capawesome-team/capacitor-background-task` or custom local Java implementation).

- **How it works:** Spawns a persistent notification in Android's system tray detailing active local network connectivity status.
- **Background Tasks:** Keeps the global WebSocket (`/ws/capabilities`) and live telemetry listener actively streaming when the device's screen is locked or the app is minimized.
- **Wakelock:** Acquires a `WAKE_LOCK` (partial CPU wake lock) during active voice intercom calls or audio streaming sessions.

---

#### 2. Local Wake-Word Engine (Picovoice Porcupine)

The app leverages `@picovoice/porcupine-capacitor` to perform **on-device local wake-word processing** (Keyword: `"Jarvis"`).

- **Zero Network Overhead:** The microphone stream is processed locally inside a Web Worker/Native thread via Porcupine. Raw audio is never sent over the network until keyword matches occur.
- **Wake & Haptic Feedback:** Once matched:
  1. The tablet/phone fires native haptic feedback (`@capacitor/haptic` vibration).
  2. The screen is forced on via window flags (`FLAG_KEEP_SCREEN_ON`).
  3. The app displays the dynamic Voice Assistant overlay widget (live audio visualizer).
  4. Triggers native audio capture for the user's intent.

---

#### 3. Native Audio Intercom Pipeline (Two-Way Voice Intercom)

To bypass web browser limits on raw audio recording when the app is backgrounded, the intercom hold-to-talk feature is backed by the Capacitor native microphone interface:

- **WebRTC Full-Duplex:** Incorporates native WebRTC bindings or LiveKit native wrapper. Spawns direct UDP media streams to the SFU when an intercom session goes live.
- **Background Wakeup (FCM Data Pushes):** Leverages Firebase Cloud Messaging (FCM) high-priority data messages. When another room or user triggers an intercom call, the background FCM payload wakes the Foreground Service, initializes the WebRTC peer connection, and plays active voice streams over the system's active alarm/notification channel—even if the screen is off or the phone is locked.
- **Microphone Plugin:** `@capacitor-community/media` or custom native PCM buffer recorder.
- **Output:** Encodes raw recording into standard high-fidelity `16kHz mono WAV` files locally for quick clips.
- **Upload:** Sends the WAV blob directly to `POST /execute/intercom/send` via native HTTP requests (bypassing browser CORS and fetch buffer overheads).

---

#### 4. Background Location Tracking & Geofencing

To feed the ESPresense RAG model and the home automation proximity engine, the mobile application maintains real-time background location updates:

- **Geolocation Core:** `@capacitor/geolocation` resolves current coordinates when the app is active.
- **Background Location Daemon:** Backed by native Android location providers (`FusedLocationProviderClient`), the app continues tracking location in the background.
- **Adaptive Reporting Intervals:** To balance precise home proximity against battery longevity, tracking intervals scale dynamically:
  - *Stationary:* Reports location every 15 minutes or when exiting a 100m geofence circle.
  - *In Transit (High Velocity):* Reports location every 30 seconds when driving or moving, enabling the LLM context to pre-heat the climate control or open the garage gate as the user enters the neighborhood.
- **Gateway Sync Endpoint:** Geolocation coordinates (`latitude`, `longitude`, `speed`, `bearing`, `accuracy`, and `timestamp`) are sent to:
  `POST /api/identity/users/location` (fully encrypted via the user's secure token).

---

#### 5. Deep Mobile Integration Features

The app bridges device-level capabilities into the unified web dashboard shell:

- **Dark Mode Sync:** Listens to native OS theme updates via `@capacitor/device` to dynamically sync the "Neon Glass" Tailwind styling between system light/dark settings.
- **Biometric Lock & Admin PIN Bypass:** Enforces `@capacitor/preferences` tied to native Android biometric prompts (`BiometricPrompt` framework) to allow rapid biometric authorization instead of entering a manual PIN when accessing sensitive admin panels or triggering security locks.
- **ESPresense BLE Pairing:** Integrates the phone's native Bluetooth transmitter as an active BLE beacon, allowing local ESPresense sensors placed around the home to track room-level presence without requiring separate physical tracking tags.
- **NFC Tag Macros:** Employs NFC reading capabilities. Tapping physical NFC stickers placed on desks or walls instantly triggers mapped Jarvis macros (e.g. tapping a bedtime NFC sticker executes `NightModeRequest` on the room's clusters).

---

#### 6. Network Security & SSL Certificate Trust (Self-Signed LAN Routing)

Since Jarvis OS is hosted on local networks using custom `.local` domains (e.g. `https://ollama-server.local` or the standard gateway IP), Android's default security configuration blocks untrusted self-signed SSL certificates.

The app includes a custom Network Security Configuration file (`services/ui/android/app/src/main/res/xml/network_security_config.xml`):

```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">localhost</domain>
        <domain includeSubdomains="true">local</domain>
        <domain includeSubdomains="true">192.168.1.0/24</domain>
        <domain includeSubdomains="true">192.168.2.0/24</domain>
        <!-- Trust user-installed CA root certificates for local HTTPS -->
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </domain-config>
</network-security-config>
```

---

#### 7. Native Android Permissions Map

The app requests the following critical permission groups in `/services/ui/android/app/src/main/AndroidManifest.xml`:

| Android Permission | Feature / Role |
| :--- | :--- |
| `android.permission.RECORD_AUDIO` | Wake-word detection and two-way intercom recording |
| `android.permission.FOREGROUND_SERVICE` | Persistent WebSocket sync and back-channel voice streaming |
| `android.permission.WAKE_LOCK` | Holds CPU wake state during active intercom sessions |
| `android.permission.CAMERA` | Scan QR codes for initial Gateway pairing / Admin profile photos |
| `android.permission.USE_BIOMETRIC` | Instant passcode/fingerprint bypass for Admin PIN override screens |
| `android.permission.ACCESS_FINE_LOCATION` | Precise GPS tracking for home automation proximity engines |
| `android.permission.ACCESS_COARSE_LOCATION` | Low-accuracy cell tower location tracking for battery savings |
| `android.permission.ACCESS_BACKGROUND_LOCATION` | Background proximity tracking when app is closed (Requires Android 10+) |
| `android.permission.POST_NOTIFICATIONS` | Displays persistent foreground status and incoming intercom call banners |

### 3.14 Device & Light Grouping System

This system allows users and admins to define logical groups of devices that act as a single unit for commands, announcements, alarms, and lighting scenes.

#### Media Device Groups (Speakers & TVs)
Currently, `AnnouncementRequest` and alarm triggers operate either on a single `entity_id` or `announce_all=true` (every non-blacklisted player in the house). Groups fill the gap between those two extremes.

*   **System Groups (Admin-defined):**
    *   `announce_all` — The existing wildcard group: every non-blacklisted media player.
    *   `main_floor`, `upstairs`, `kids_rooms` — Room-cluster groups configured by the admin.
*   **User Groups (User-defined):**
    *   Each user can configure personal named groups in their profile (e.g., "My Devices": kitchen speaker + bedroom speaker).
    *   When a user sets a timer or alarm, the target can be their personal group rather than a single device.
    *   Groups are stored in `identity.db` keyed by `user_id`.

**Resolution Priority:** The `AnnouncementRequest` schema will accept a `group_id` field. The execution handler resolves it to a list of `entity_id`s by querying the Identity service's group registry, then fans out the command in parallel (asyncio `gather`) to all members.

**Backend Schema Addition:**
```python
class MediaGroupRequest(BaseRequest):
    action: Literal["create", "delete", "list", "add_member", "remove_member"]
    group_id: str                        # e.g. "my_devices" or "main_floor"
    group_name: Optional[str] = None
    member_entity_ids: Optional[List[str]] = None
    scope: Literal["user", "system"] = "user"  # system = admin-only
```

#### Light Clusters (Groups of Lights Acting as One)
A Light Cluster is a named collection of `light.*` entities that the LLM treats as a single controllable unit. When a command targets a cluster, the execution handler fans out to all members simultaneously.

*   **Storage:** `identity.db` table `light_clusters`: `cluster_id`, `cluster_name`, `entity_ids` (JSON array), `owner_user_id`, `scope` (user/system/room).
*   **LLM Tool Extension:** `LightControlRequest` gains an optional `cluster_id` field. If provided and no `entity_id` is given, the handler resolves the cluster and dispatches to all member lights.
*   **Example:** A cluster called `kitchen` with 6 `light.*` entities. The LLM receives "set kitchen lights to warm white 50%" and dispatches one `asyncio.gather()` call to all six.

**Backend Schema Addition:**
```python
class LightClusterRequest(BaseRequest):
    action: Literal["create", "delete", "list", "add_member", "remove_member"]
    cluster_id: str                      # e.g. "kitchen"
    cluster_name: Optional[str] = None
    member_entity_ids: Optional[List[str]] = None
    room: Optional[str] = None           # auto-populates from HA area registry
    scope: Literal["user", "system", "room"] = "room"
```

#### Light Patterns & Scenes
Light patterns apply a *sequence of colors* across multiple lights in a cluster, enabling effects that go beyond uniform color changes. Patterns are stored as named templates.

*   **Storage:** `identity.db` table `light_patterns`: `pattern_id`, `pattern_name`, `cluster_id`, `steps` (JSON), `loop` (bool), `transition_ms`.
*   **`steps` Schema (JSON array):** Each step maps one or more entity positions to a specific color/brightness. Position is 0-indexed within the cluster's ordered member list.
    ```json
    [
      {"positions": [0, 1], "rgb": [255, 140, 0], "brightness_pct": 80},
      {"positions": [2, 3], "rgb": [255, 80, 0], "brightness_pct": 60},
      {"positions": [4, 5], "rgb": [200, 40, 0], "brightness_pct": 40}
    ]
    ```
*   **Built-in Pattern Library (System Defaults):**

| Pattern Name | Description |
| :--- | :--- |
| `sunset` | Warm orange at the top rows, deep red at the bottom rows, simulating a gradient |
| `christmas` | Alternates red (`#FF0000`) and green (`#00AA00`) across sequential lights |
| `ocean` | Gradual wave from deep blue → cyan → teal across the cluster |
| `daylight` | Uniform neutral white at full brightness (5500K equivalent) |
| `night_mode` | Dim red-tinted light (sleep-safe wavelength) at 10% brightness |
| `party` | Random RGB cycling across all lights simultaneously |

*   **LLM Tool Extension:** `LightControlRequest` gains a `pattern_id` field. If provided, the handler loads the pattern steps and fans out individual color calls per position group.
*   **Admin UI:** A pattern editor in `/admin/groups` allows admins to create and preview custom patterns visually before saving.

---

### 3.15 Device Telemetry Monitoring & LLM Pattern Analysis

This system allows any device or group (media players, lights, smart plugs, sensors) to be enrolled in continuous telemetry monitoring. The collected data feeds an LLM-powered insight engine that surfaces observable behavioral patterns.

#### What is Monitored
For each enrolled device or group, the monitoring pipeline tracks three dimensions:

| Dimension | Data Collected | Source |
| :--- | :--- | :--- |
| **Power Consumption** | Wattage over time, peak draw, idle draw, anomalous spikes | HA power/energy sensors (`sensor.*_power`, `sensor.*_energy`) |
| **Availability** | Online/offline transitions, downtime duration, frequency of outages | HA `state` attribute (entity `unavailable` / `unknown`) |
| **Usage Frequency** | How often activated, active duration, time-of-day distribution | HA state change logbook queries |

#### Data Storage
*   Raw telemetry snapshots are stored in a new `device_telemetry` table in the existing `/data/device_registry.db` SQLite DB.
*   **Schema:**
    ```sql
    CREATE TABLE device_telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT NOT NULL,
        recorded_at REAL NOT NULL,          -- Unix timestamp
        power_w REAL,                       -- Current wattage (null if not a power device)
        is_available INTEGER NOT NULL,      -- 1 = online, 0 = offline
        state TEXT,                         -- Raw HA state string
        source TEXT                         -- "poll", "ha_event", "webhook"
    );
    CREATE INDEX idx_telemetry_entity_time ON device_telemetry(entity_id, recorded_at);
    ```
*   **Retention Policy:** Raw telemetry is retained for 30 days. After 30 days, it is downsampled to hourly averages and stored in a `device_telemetry_archive` table for long-term trend analysis.

#### Collection Mechanism
The existing **Cleanup Loop** in `RavenWorker` (which already polls all HA entities every 5 minutes) is extended to write telemetry snapshots for enrolled devices. Additionally, a dedicated **HA WebSocket event subscriber** (`state_changed` events) provides real-time availability change detection without polling lag.

#### LLM Pattern Analysis Engine
The monitoring data alone is raw numbers. The LLM layer converts it into human-readable intelligence:

1.  **Scheduled Analysis:** Nightly, the system runs a Raven `analysis_mission` that queries the last 7 days of telemetry for all enrolled entities.
2.  **Pattern Prompt:** It constructs a structured prompt summarizing usage patterns per device and asks the LLM: *"Based on the following usage data, what observable behavioral patterns exist, and what do they likely mean about the household's habits?"*
3.  **Insight Generation:** The LLM produces named insights (e.g., *"The kitchen TV is on every weekday between 7–8 AM, suggesting a morning news routine"*, *"The garage smart plug shows power spikes every 3 days, consistent with a battery charger cycle"*).
4.  **Storage:** Insights are stored in the RAG `system_learnings` collection (same as Raven mission learnings) so they become permanent ambient context for future Jarvis conversations.
5.  **Alerts:** If an enrolled device goes offline for longer than its configured threshold (e.g., `offline_alert_after_minutes: 30`), Jarvis immediately sends a voice notification via Nextcloud Talk/TTS.

#### UI: Device Monitor Dashboard (`/admin/monitor`)
*   **Enrollment Panel:** Admins select any HA entity or group and enroll it in monitoring. They configure `power_tracking`, `availability_tracking`, `usage_tracking`, and `offline_alert_threshold`.
*   **Telemetry Cards:** One card per enrolled device/group showing: current state (online/offline), real-time power draw, 7-day usage sparkline, last outage event.
*   **LLM Insights Feed:** A dedicated panel showing the most recent LLM-generated pattern insights, each tagged with the device name, date generated, and a confidence indicator.

---

## 4. Backend Architecture: LLM Preemption & Constraints
Running complex models on a distributed Dual-Node setup with a strict 8GB VRAM limit on the Inference Node requires rigorous memory management. `gateway/agent_loop.py` currently checks `api/ps` via `get_vram_safe_params()` to scale context limits. While the **Alpaca Wrapper** handles the TurboQuant KV-cache optimizations (`turbo4`/`turbo3`), the Gateway utilizes a global `INFERENCE_LOCK` to serialize all LLM requests, ensuring only one context window is loaded at a time.

### 4.1 The "Pause/Resume" Dilemma
*   **Interruptible Jobs (LLM Generation):** If Raven is generating text or parsing ASTs, the Gateway can safely pause inference, force `AgentLoop` to save state to `raven:checkpoint:{mission_id}`, yield the GPU to Jarvis via the `INFERENCE_LOCK`, and resume later.
*   **Atomic Jobs:** Subprocesses like Kokoro TTS generation or compiling cannot be paused without corruption. 

### 4.2 The Lock Manager Strategy
*   **Preemption Logic:** If Jarvis wakes up while Raven holds an `interruptible: false` lock, the Gateway routes Jarvis's voice request to a highly quantized CPU fallback model to prevent crashing the atomic job.

### 4.3 The Raven Background Worker (`background_worker.py`)
Operating entirely asynchronously inside the Gateway, the `RavenWorker` is the true autonomous heart of the system. It runs four continuous loops:
1.  **The Inference Loop:** A singleton worker pulling jobs off the Redis queue. It manages the strict lock acquisition (`TIER3_LOCK` for Raven, `TIER2_SEMAPHORE` for Jarvis) before passing payloads to `orchestrator.py`.
2.  **The Talk Monitor Loop:** Continuously polls Nextcloud Talk using the system identity. If it detects an `@jarvis` mention in any chat room, it autonomously injects the query directly into the Inference Queue.
3.  **The Health Loop:** Actively scrapes Docker logs via the Control Plane. If it detects an error threshold (e.g., 5+ exceptions in `ha_client.py`), it autonomously generates a self-repair mission and pushes it to the Identity Service's **Triage Queue** for Admin approval.
4.  **The Cleanup Loop:** Every 5 minutes, it pulls all live entities from Home Assistant, syncs them to the RAG Vector DB, prunes orphaned entities, and caches live states in Redis (`ha:state:{eid}`).

### 4.4 Dynamic Model Auto-Upgrade (Latest: Raven commit `2223ee8`)
Raven now detects when a mission is failing due to schema/tool-format errors (e.g., `422`, `"no valid tool call found"`, `"agent failed to produce"`). When detected, rather than failing silently or retrying with the same model, the worker calls a new `_get_upgrade_model()` method that:
1.  Queries the live Ollama `/api/tags` endpoint to discover all currently-downloaded models.
2.  Filters out the currently-failing model.
3.  Selects the **largest available model by binary size** as the upgrade target — fully dynamic, no hardcoded model names.
4.  Re-enqueues the mission with the upgrade model for a fresh attempt.

This is fully self-healing: if you pull a new, larger model on the Inference Node overnight, Raven will automatically discover and prefer it.

### 4.5 DNS Sync Sidecar & Multi-IP Inference Failover
A lightweight Python sidecar container (`config/dns_sync.py`) provides internal DNS resolution for the Inference Node hostname `ollama-server.local`. It reads the `dns_mappings` setting from the Identity service (a JSON object mapping hostnames to IP lists) and runs a pure-Python DNS server inside the Docker network.

**Health-check behaviour:** The sidecar continuously pings all configured IPs for each hostname. It only advertises **alive IPs** in DNS responses. If the primary IP goes down, DNS automatically returns only the surviving fallback IPs — no config change or container restart required. If all IPs are dead, it returns all of them as a last resort (preserving the old behaviour for unexpected failures).

**Key setting in `identity.db`:**
```json
{
  "ai.local": ["192.168.2.205"],
  "ollama-server.local": ["192.168.2.114", "192.168.4.179", "192.168.1.204"]
}
```
This replaces the old `extra_hosts` static IP mapping in `docker-compose.yml` and makes the Inference Node address fully dynamic and UI-configurable.

---

## 5. Master Implementation Task List

### Phase 1: Universal Integration Architecture
*   [ ] **Task 1.1:** Build abstract base classes in the Gateway (`ChoreProvider`, `MediaProvider`, `MQTTProvider`).
*   [ ] **Task 1.2:** Refactor existing clients to adhere to these Provider interfaces.
*   [ ] **Task 1.3:** Build the `/api/integrations/available` JSON schema endpoint.
*   [ ] **Task 1.4:** Build the React `/admin/integrations` view that dynamically renders forms.
*   [ ] **Task 1.5:** Build the `MQTTPublishRequest` schema and handler for direct ESPHome communication.

### Phase 2: React Frontend UI Overhaul ("Neon Glass")
*   [ ] **Task 2.1:** Implement the Fluid Grid / Bottom Nav responsive shell.
*   [ ] **Task 2.2:** Build the Normalized Capability Widgets (MASS Media, ABS Progress, Notes, Chores).
*   [ ] **Task 2.3:** Overhaul `Communication.tsx` into the Smart Inbox with Full-Screen Expansion.
*   [ ] **Task 2.4:** Build the Nextcloud Avatar Picker and Vision stylization UI.
*   [ ] **Task 2.5:** Build the Ambient Timer Widget and dynamic routing logic.

### Phase 3: Real-Time State Sync & Presence
*   [ ] **Task 3.1:** Build a centralized WebSocket manager (`/ws/capabilities`).
*   [ ] **Task 3.2:** Build the React Zustand global store to auto-mount widgets.
*   [ ] **Task 3.3:** Integrate ESPresense MQTT to drive the "Halo" Hero Banner.

### Phase 4: LLM Preemption & The Lock Manager
*   [ ] **Task 4.1:** Implement the Asyncio/Redis Global Inference Lock Manager inside `orchestrator.py`.
*   [ ] **Task 4.2:** Update all 30+ execution handlers in `services/execution/handlers/` to include the `interruptible` flag.
*   [ ] **Task 4.3:** Build the Context Suspension protocol for `agent_loop.py`.

### Phase 5: Voice ID & Ionic Capacitor Wrapper
*   [ ] **Task 5.1:** Initialize Capacitor and install Picovoice Porcupine.
*   [ ] **Task 5.2:** Build the Assistant Overlay (Audio Visualizer).
*   [ ] **Task 5.3:** Build Voice ID Routing Logic and Admin PIN Pad.

### Phase 6: Device Grouping, Light Clusters & Telemetry Monitoring
*   [ ] **Task 6.1:** Add `media_groups` and `user_device_groups` tables to `identity.db`. Build `MediaGroupRequest` schema and `/execute/groups/media` endpoint. Update `AnnouncementRequest` to accept `group_id` and fan out via `asyncio.gather()`.
*   [ ] **Task 6.2:** Add `light_clusters` table to `identity.db`. Build `LightClusterRequest` schema and `/execute/groups/lights` endpoint. Extend `LightControlRequest` to accept `cluster_id`.
*   [ ] **Task 6.3:** Add `light_patterns` table to `identity.db`. Seed the system default patterns (`sunset`, `christmas`, `ocean`, `daylight`, `night_mode`, `party`). Extend `LightControlRequest` to accept `pattern_id` and dispatch per-step fan-out.
*   [ ] **Task 6.4:** Build the **Group Manager UI** (`/admin/groups`): media group creator, light cluster builder with drag-and-drop entity assignment, and the pattern step editor with a live preview panel.
*   [ ] **Task 6.5:** Add `device_telemetry` and `device_telemetry_archive` tables to `/data/device_registry.db`. Extend the `RavenWorker` Cleanup Loop to write telemetry snapshots for enrolled entities. Add HA WebSocket `state_changed` subscriber for real-time availability tracking.
*   [ ] **Task 6.6:** Build the nightly LLM pattern analysis Raven mission: query 7-day telemetry → construct pattern prompt → store insights in RAG `system_learnings`.
*   [ ] **Task 6.7:** Build the **Device Monitor Dashboard UI** (`/admin/monitor`): enrollment panel, per-device telemetry cards with sparklines, and the LLM Insights Feed.

---


## 6. LLM Tool Schemas & UI Mapping

To bridge the gap between the LLM's raw intent and the UI's state, here is an exact breakdown of the Pydantic schemas found in `services/execution/schemas.py`, their options, and how they drive the Jarvis OS UI capability widgets.

### 6.1 Media & Entertainment
*   **`MediaPlayRequest` / `MediaTransportRequest` / `MediaStatusRequest`**:
    *   *Options:* `entity_id` or `device_name`, `query`, `media_type` (music, podcast, url), `enqueue` (add, next, replace).
    *   *UI Correlation:* Instantiates the **Active Media Widget** on the dashboard. The frontend polls `/ws/capabilities` to show what the LLM just triggered.
    *   *Deep-Dive Finding:* To ensure reliable casting, `execution/main.py` implements a robust `verify_playback` polling loop. After issuing a play command, it polls the target HA entity for up to 10 seconds to verify its state transitions to `playing` and the `media_content_id` matches the expected payload. This guarantees the UI Remote is never out of sync with physical hardware.
*   **`AudiobookshelfRequest`**:
    *   *Options:* `action` (search, play, resume, progress), `book_id`, `query`.
    *   *UI Correlation:* Triggers the **"Continue Reading" Widget**. The LLM tracks exact duration progress which the React UI renders as an Apple Watch-style ring.
*   **`VideoPlayRequest`**: Extracts direct MP4 streams via `yt-dlp` for casting to TVs based on natural language queries.

### 6.2 Communications & Notifications
*   **`TalkRequest`**:
    *   *Options:* `action` (send, send_voice, read, messages, list), `token`, `message`, `text_to_voice`.
    *   *Action Auto-Inference (Recent Fix):* The `BaseRequest` validator and the Gateway orchestrator both auto-infer the missing `action` field from payload shape: if `message` or `text_to_voice` is present → `send`; if `token` is present alone → `messages`; otherwise → `list`. This prevents LLM hallucinations where the action field is omitted.
    *   *UI Correlation:* Sends data to Nextcloud Talk. Populates the **Smart Inbox Widget** which can expand into the **Native Chat Client** full-screen app.
*   **`AnnouncementRequest` / `TTSRequest`**:
    *   *Options:* `message`, `target_device`, `announce_all` (boolean), `recorded_audio_path` (for raw voice notes), `voice` (e.g. `af_heart`), `storybook` (boolean).
    *   *Single-Turn Routing:* `TTSRequest` is now registered in the Gateway's `SINGLE_TURN_TOOL_ENDPOINTS` map (`ttsrequest → /execute/tts`), meaning the Gateway can dispatch TTS directly without escalating to the full Raven agent loop.
    *   *UI Correlation:* Drives the Dashboard Intercom Widget. If `announce_all` is true, the backend checks the HA entity blacklist before broadcasting. If it's a TTS payload, the backend parses the text for mapped emojis and splices in custom admin-uploaded `.mp3` files (e.g., replacing 🚗 with a honk sound).
*   **`LLMInfoRequest`** *(New Tool)*:
    *   *Options:* `action` (list, ps, version, show), `model` (required for `show`).
    *   *Purpose:* Allows the LLM to introspect the Ollama/Alpaca inference backend — listing available models, checking what is currently loaded in VRAM (`ps`), querying server version, or showing detailed model metadata (parameters, quantization level).
    *   *Single-Turn Routing:* Registered as `llminforequest → /execute/llm_info` in `SINGLE_TURN_TOOL_ENDPOINTS`.
    *   *UI Correlation:* Powers the **Model Selector** in Settings, showing which models are currently downloaded and loaded.

### 6.3 Memory, Notes & Time Management
*   **`NoteRequest`**:
    *   *Options:* `action` (create, append, read, list, sync_rag), `title`, `content`.
    *   *UI Correlation:* Creates the **Quick Notes Widget**. The `sync_rag` action is the mechanism for the NotebookLM-style long-term memory.
*   **`CalendarRequest`**:
    *   *Options:* `action` (add, list), `summary`, `start_time` (supports human strings via dateparser).
    *   *UI Correlation:* Driven directly by the LLM parsing a `NoteRequest`. Shows up in the UI as the **Upcoming Events Widget**.
*   **`TimerRequest`**:
    *   *Options:* `type` (timer vs alarm), `duration_str` (e.g., "10m"), `time_str` (e.g., "7:00 AM"), `target_device`, `trigger_task` (Optional nested tool payload).
    *   *UI Correlation:* `type="timer"` spawns a dynamic **Ambient Countdown Widget**. `type="alarm"` generates a static **Persistent Alarm Card**. When either fires, they can execute their `trigger_task` payload (e.g. running a light control or macro).

### 6.4 Smart Home Execution & Security
*   **`LightControlRequest` / `ClimateRequest` / `SecurityRequest`**:
    *   *Options:* `action`, `brightness_pct`, `rgb_color`, `temperature`.
    *   *UI Correlation:* Direct backend actions that update the React Zustand store, visibly flipping toggles in the UI. 
    *   *Security Note:* `ha_client.py` implements a strict Role-Based Access Control (RBAC) interceptor via `authorize_action()`. If the LLM attempts to execute a sensitive action (e.g., `unlock` on a `lock` domain, or `alarm_disarm` on `alarm_control_panel`) and the `UserContext` does not have `is_admin=True`, the request is hard-blocked. The UI will render a "Security Override Required" warning instead.
*   **`MQTTPublishRequest` (Planned Integration)**:
    *   *Options:* `topic`, `payload`, `qos`, `retain`.
    *   *UI Correlation:* This is a low-level hardware tool. It allows the LLM to send raw JSON payloads directly to an MQTT broker, specifically targeting custom **ESPHome** hardware (e.g., custom LED matrix displays, DIY sensors, or bespoke actuators) bypassing Home Assistant entirely.

### 6.5 System Diagnostics & Execution Logs
*   **`DiagnosticRequest` / `ExecutionLogRequest`**:
    *   *Deep-Dive Finding:* `diagnostics.py` exposes a specific `ExecutionLogRequest` that runs `docker logs --tail 100 sharedllm_execution` via subprocess, allowing the LLM to actively verify if a tool it called actually succeeded at the Docker level.

### 6.6 Composite Macro-Actions (`composite.py`)
The system supports chaining multiple atomic tools into high-level macros to reduce LLM token usage and latency:
*   **`DocumentBroadcastRequest`**: Reads a text file from Nextcloud storage, utilizes the LLM to generate a summary, converts the summary to speech via Kokoro TTS, and casts the audio to a specific Home Assistant speaker, all in one API call.
*   **`NightModeRequest`**: Simultaneously queries and shuts off all active `light.*` entities, adjusts the `climate.*` entity to sleep temperature, and optionally queues up an audiobook or sleep sound playlist.

### 6.7 Raven Autonomous Ops (Admin UI)
*   **Mission Lifecycle & RAG Learning System**:
    *   *Deep-Dive Architecture Finding:* Raven's long-running tasks are explicitly tracked as "Missions" in the Identity DB. When a mission succeeds, an autonomous `_persist_learning()` hook injects the mission's summary and solution directly into the RAG `system_learnings` collection. This means Jarvis permanently learns from Raven's coding successes. Crucially, deleting a mission from the UI removes the heavy JSON audit log from the DB but intentionally leaves the RAG learning intact for future context.
*   **Live Tracing & Redis PubSub**:
    *   *Deep-Dive Architecture Finding:* The `AgentLoop` streams real-time execution telemetry (including `reasoning` tokens and `action` payloads) to the React frontend via a WebSocket (`/api/raven/missions/{id}/stream`) backed by a Redis PubSub channel (`raven:mission:stream:{id}`). The UI features **Stop, Pause, and Resume** controls that send signals to instantly terminate or suspend runaway loops.
*   **Triple-Layer Sanitization**:
    *   *Security Note:* Credentials are redacted at three distinct layers before they ever reach the frontend or logging DB: 1) `sanitize_for_llm()` inside the AgentLoop, 2) `emit_log()` before transmission, and 3) Ingest-time sanitization at the `logging` microservice boundary.
*   **`WorkspaceFileReadRequest` / `WorkspaceFilePatchRequest`**:
    *   *Options:* `offset_lines`, `limit_lines`, `chunks` (difflib SequenceMatcher inputs).
    *   *UI Correlation:* Triggers the **Raven Ops Panel** timeline to show the LLM actively modifying the `SharedLLM` repository.
    *   *Deep-Dive Architecture Finding (Post-Write Lint Hook):* Inside `orchestrator.py`, anytime a file write/patch occurs, an automatic hook is triggered calling the `workspace_lint` execution handler (now utilizing `ruff`). If syntax errors are found, the raw linter output is forcefully appended to the LLM's context, ensuring it immediately catches and corrects syntax failures on the next turn.
*   **`GitOperationRequest` / `WorkflowWriteSyncCommitRequest`**:
    *   *Deep-Dive Architecture Finding:* The `workspace_runtime` service enforces a strict, atomic workflow for self-editing: **Write -> Lint -> Pytest -> Commit -> Push -> Provider Sync**. 
    *   *Autonomous Guardrails:* Raven is physically blocked from pushing directly to protected branches (e.g., `main`). If a file fails linting or Pytest >3 times in 10 attempts, the system triggers an **Automated Quarantine**, locking the file from further LLM edits until an Admin overrides it.
    *   *UI Correlation:* Generates the interactive **Commit Card** UI allowing the Admin to review Raven's autonomous work.

### 6.6 Browser & Web Operations
*   **`WebSearchRequest`**:
    *   *Options:* `query`, `category` (images, news, it), `engines`, `time_range`.
    *   *UI Correlation:* Renders the **Search Results Widget** with source links retrieved from `search.sumemail.com`.
*   **`WebReadRequest`**:
    *   *Options:* `url`, `use_current_user_auth` (boolean).
    *   *UI Correlation:* Triggers headless Playwright extraction. If auth is requested, securely passes the React session token to Chromium's cookie jar so the LLM can read internal pages.

### 6.9 Self-Hosted Media Architecture: Why We Host Our Own Files

Home Assistant cannot request audio or video from external services (auth walls, app restrictions, device codec limits). Instead, the `execution` service acts as its own media server — generating or downloading files locally and serving them at a public URL that HA devices can cast directly.

---

#### TTS Announcements — Kokoro + In-Memory Cache

**Why self-host?** Standard HA TTS engines (Google, Nabu Casa) are cloud-dependent and offer no ability to inject custom sound effects mid-stream. We generate everything locally with no external calls.

**Pipeline (from `main.py` + `tts.py`):**

```
User request → /execute/announce (POST)
  │
  ├─ 1. Kokoro TTS engine generates WAV bytes in-memory
  │      (via KokoroTTSEngine.generate() using kokoro-v1.0.onnx)
  │      Text is normalized first (abbreviations, roman numerals)
  │      Storybook mode: splits dialogue/narrative, switches voice per speaker
  │
  ├─ 2. [Future] Emoji-splice pass (Section 6.9)
  │      Emoji positions are replaced with mapped .mp3 bytes, concatenated via np.concatenate()
  │
  ├─ 3. WAV bytes stored in TEMP_AUDIO_CACHE dict: {media_id → bytes}
  │      media_id = "tts-{8 hex chars}"  (e.g. "tts-a3f2b19c")
  │      This is purely in-memory (no disk write for TTS)
  │
  ├─ 4. Public URL built: http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}
  │      Port 8888 = dedicated media HTTPServer (separate from FastAPI on :8003)
  │      EXECUTION_EXTERNAL_HOST is resolved from the .env / Identity config
  │
  ├─ 5. Pre-flight self-check: execution pings its own /media/{media_id} endpoint
  │      (up to 5 retries, 0.5s apart) to confirm the bytes are being served correctly
  │      before dispatching to HA — prevents HA from receiving a broken URL
  │
  └─ 6. URL passed to announce_handlers.dispatch_announce()
         → device type detected (Roku, Samsung, WebOS, Cast, DLNA, Speaker, etc.)
         → appropriate HA service call made with media_content_id = the URL
         → verify_playback() polls HA state for up to N seconds to confirm
```

**`/media/{media_id}` endpoint behaviour:**
- Checks `TEMP_AUDIO_CACHE` dict first → serves `audio/wav` bytes directly from RAM.
- If not in cache, falls through to disk video check (below).
- Returns **503** (not 404) if media isn't ready yet — tells HA to retry instead of giving up permanently.

**Lifetime:** Audio is held in `TEMP_AUDIO_CACHE` for the process lifetime (no TTL). On container restart the cache is empty. This is intentional — TTS audio is ephemeral by design.

---

#### Video Playback — yt-dlp + Progressive Disk Streaming

**Why yt-dlp?** Direct YouTube / Rumble / Vimeo playback on Cast, Roku, and Android TV requires being logged into the corresponding app on that device. Many devices lack the YouTube app entirely (generic Cast sticks, some Roku models). yt-dlp bypasses all of this by extracting a direct, publicly-accessible MP4 stream and downloading it to disk, where the execution service streams it directly.

**CRITICAL: Progressive Download Only** — ALWAYS use `download_video_progressive()` for ALL video playback (Roku, WebOS, Cast, Android, Samsung). NEVER use `download_video()` — it waits for full download before returning, causing 3+ minute timeouts. Progressive download returns control after 5MB buffered, enabling fast startup. The media server on port 8888 serves `.mp4.part` files with proper HTTP Range headers — Cast devices can stream partial files.

**Pipeline (from `handlers/video.py` and `handlers/media.py`):**

```
User request → /execute/media/play (VideoPlayRequest)
  │
  ├─ 1. URL resolution
  │      If query is already a YouTube/Rumble/Vimeo URL → use it directly
  │      Otherwise → run: yt-dlp --dump-json --no-download "ytsearch1:{query}"
  │                        to find the top YouTube result URL
  │
  ├─ 2. PROGRESSIVE download (returns after 5MB buffered)
  │      Format preference: best[ext=mp4][vcodec^=avc1][acodec^=mp4a]
  │         (H.264 + AAC = broadest Cast/Roku hardware decoder compatibility)
  │      Falls back to: best[ext=mp4] → best (any container)
  │      --merge-output-format mp4 ensures a single clean .mp4 output
  │      Saved to disk: {TEMP_MEDIA_DIR}/{media_id}.mp4.part (while downloading)
  │         TEMP_MEDIA_DIR is set via config (volume-mounted at /data/media/)
  │         media_id = "vid-{8 hex chars}"  (e.g. "vid-7d9c3e1a")
  │      Returns IMMEDIATELY after 5MB buffered — download continues in background
  │
  ├─ 3. Title retrieval
  │      Second yt-dlp --dump-json call to get the human-readable video title
  │      (used in the ExecutionResult message sent back to the user)
  │
  ├─ 4. Device-specific routing
  │      │
  │      ├─ ROKU:
  │      │   ├─ Wake device in parallel (roku_wake_device via asyncio.create_task)
  │      │   │   → media_player.turn_on if off/idle/unavailable
  │      │   │   → remote.send_command("Home") on remote.* sibling
  │      │   ├─ Wait for wake task to complete
  │      │   ├─ Build URL: http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}
  │      │   └─ ECP launch: POST http://<roku_ip>:8060/launch/782875
  │      │       params: t=v, u=<mp4_url>, videoName=<title>, videoFormat=mp4
  │      │
  │      ├─ WebOS / Samsung / Cast / Android:
  │      │   ├─ media_stop (clear active session)
  │      │   ├─ Power on if off/idle/standby/unavailable
  │      │   ├─ Volume safeguard (unmute, boost to 20% if too low)
  │      │   ├─ Build URL: http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}
  │      │   └─ HA service: media_player.play_media
  │      │       media_content_id = the local MP4 URL
  │      │       media_content_type = "video/mp4"
  │      │
  │      └─ All paths: background download continues after playback starts
  │
  └─ 5. Background download completes → file renamed from .mp4.part to .mp4
```

**`/media/{media_id}` endpoint behaviour for video:**
- Served via **FastAPI `FileResponse`** (not custom HTTP server) with `Accept-Ranges: bytes` header enabled — this is critical. Streaming players (Cast, Roku) send range requests (`bytes=0-`) to seek and buffer. Without `Accept-Ranges`, playback may stall or fail immediately.
- Checks disk for `{TEMP_VIDEO_DIR}/{media_id}.mp4` — if not found, checks for `{media_id}.mp4.part` (partial download in progress).
- Returns **503** if media isn't ready yet — tells HA to retry instead of giving up permanently.

**Codec choice rationale:** H.264 (`avc1`) + AAC (`mp4a`) is the lowest common denominator across all Cast, Roku, and Android TV hardware decoders. yt-dlp's format selector prioritizes this explicitly.

**Supported sources:** YouTube, YouTube Shorts, Rumble, Vimeo (any source yt-dlp supports — the handler is source-agnostic via URL pattern detection).

**Roku-specific details:**
- Uses **Media Assistant app (ID 782875)** via ECP — not native Roku media player.
- ECP param is `videoName` (NOT `songName` — that's for audio).
- Wake-up logic consolidated in `roku.roku_wake_device()` — called in parallel with download via `asyncio.create_task()`.
- Device IP discovered via 10-strategy pipeline (registry → HomeKit diagnostics → HA attrs → ARP → SNMP → mDNS → SSDP → port scan).
- Video titles are sanitized to remove emojis (prevents URL encoding issues on Roku ECP).

#### How to Check What's Playing on Roku (State Verification)

Roku state is reported through **two independent channels**. Always cross-reference both for accurate status:

**1. Home Assistant `media_player` entity** (`media_player.28_tcl_roku_tv`):
```
State: "playing" | "on" | "idle" | "off" | "unavailable"
Key attributes:
  - app_id: "782875" (Media Assistant) or other app ID
  - app_name: "Media Assistant", "YouTube", "Netflix", etc.
  - source: "Media Assistant" (matches app_name)
  - media_content_type: "app" | "video" | "music"
  - media_duration: total duration in seconds
  - media_position: current playback position in seconds
  - media_position_updated_at: timestamp of last position update
  - source_list: all installed apps on the Roku
```
**Limitation:** HA polls the Roku every ~10-30 seconds. `media_position` can be stale. The `state` field is reliable for "playing" vs "idle" vs "off" but doesn't tell you *what* is playing beyond the app name.

**2. Roku ECP `/query/media-player` endpoint** (`http://<roku_ip>:8060/query/media-player`):
```xml
<player state="play" error="false">
  <plugin id="782875" name="Media Assistant" bandwidth="13214901 bps" />
  <format audio="aac" video="mpeg4_15" ... container="mp4" />
  <buffering target="0" max="1000" current="1000" />
  <position>253617 ms</position>
  <duration>43643994 ms</duration>
  <is_live>false</is_live>
</player>
```
**This is the ground truth.** The `state="play"` attribute is real-time (not polled). `position` and `duration` are in milliseconds. `error="false"` confirms no playback errors.

**3. Roku ECP `/query/active-app` endpoint** (`http://<roku_ip>:8060/query/active-app`):
```xml
<active-app>
  <app id="782875" type="appl" version="1.2.1">Media Assistant</app>
</active-app>
```
Confirms which app is currently in the foreground.

**4. Music Assistant sibling player** (`media_player.gracies_tv`):
For music/announcements routed through MASS, check the sibling MA player:
```
State: "playing" | "paused" | "idle"
Key attributes:
  - mass_player_type: "player" (confirms it's a real MA output)
  - active_queue: "ROKU_2N0062385487" (the queue ID for this Roku)
  - media_content_id: the URI being played (e.g., "library://track/613" or "builtin://track/http://...")
  - media_title: track title or media_id
  - media_position: current position in seconds
  - source: "Music Assistant Queue"
```

**Quick diagnostic commands:**
```bash
# Check HA state (all attributes)
curl -s -H "Authorization: Bearer <token>" https://ha.sumemail.com/api/states/media_player.28_tcl_roku_tv

# Check ECP media-player (real-time playback state)
curl -s http://192.168.2.166:8060/query/media-player

# Check ECP active app
curl -s http://192.168.2.166:8060/query/active-app

# Check MASS sibling
curl -s -H "Authorization: Bearer <token>" https://ha.sumemail.com/api/states/media_player.gracies_tv
```

**State interpretation guide:**

| HA State | ECP player state | Active App | Interpretation |
|---|---|---|---|
| `playing` | `state="play"` | Media Assistant | Actively playing media via MA |
| `playing` | `state="play"` | YouTube/Netflix/etc | Playing via native app (not MA) |
| `on` | `state="stop"` or no media | Any app | App is open but nothing playing |
| `idle` | N/A | Home | On home screen, no app active. **Note:** "idle" can also mean the TV is on but the screen is off (screensaver/standby). The Roku is powered on but not actively playing media. |
| `off` | N/A | N/A | TV is powered off (black screen, no response to remote) |
| `unavailable` | Connection refused | N/A | Roku unreachable (network/power issue) |

---

#### Summary: Two Storage Strategies

| | TTS Audio | Video |
|---|---|---|
| **Generated by** | Kokoro ONNX (local, offline) | yt-dlp CLI download |
| **Stored as** | RAM (`TEMP_AUDIO_CACHE` dict) | Disk file (`/data/media/*.mp4`) |
| **Served via** | FastAPI `Response(bytes, media_type="audio/wav")` | FastAPI `FileResponse` with `Accept-Ranges` |
| **URL pattern** | `http://{host}:8888/media/tts-{id}` | `http://{host}:8888/media/vid-{id}` |
| **Lifetime** | Process lifetime (ephemeral) | Until container restart or manual cleanup |
| **Auth bypass** | N/A (fully local) | yt-dlp bypasses YouTube/platform auth |
| **Pre-flight check** | Yes (5-retry self-ping) | No (progressive download returns after 5MB) |
| **Download mode** | N/A (in-memory generation) | **Progressive only** — never full-wait |

---

### 6.10 Emoji Sound Manager (TTS Audio Splice System)

This feature allows admins to upload audio files and map them to emojis. When a TTS announcement contains a mapped emoji, the execution service splices the audio file into the Kokoro-generated WAV stream at the exact position of the emoji before it is served to the speaker.

#### Backend Storage Architecture
*   **Persistent Audio Store:** Uploaded sound files are stored in a dedicated, volume-mounted directory on the `execution` container: `/data/emoji_sounds/` (e.g., `/data/emoji_sounds/car_honk.mp3`). This directory is backed by the same `execution_data` Docker volume as the device registry, ensuring files survive container restarts.
*   **Emoji-to-File Mapping DB:** The mapping table is stored in the existing `/data/device_registry.db` SQLite database as a new `emoji_sounds` table:
    ```sql
    CREATE TABLE emoji_sounds (
        emoji TEXT PRIMARY KEY,        -- e.g., "🚗"
        label TEXT,                    -- e.g., "Car Honk"
        filename TEXT NOT NULL,        -- e.g., "car_honk.mp3"
        duration_ms INTEGER,           -- pre-computed for sequencing
        uploaded_at REAL,
        uploaded_by TEXT               -- admin user_id
    );
    ```

#### Execution Service API Contract
Four new endpoints on the `execution` service (all require `X-Internal-Secret` + `is_admin=True`):

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `GET` | `/emoji-sounds` | List all emoji → file mappings |
| `POST` | `/emoji-sounds/upload` | `multipart/form-data`: `file` (audio) + `emoji` + `label`. Validates MIME type, saves to `/data/emoji_sounds/`, inserts DB row. Returns `{emoji, filename, duration_ms}`. |
| `DELETE` | `/emoji-sounds/{emoji}` | Remove mapping and delete file from disk |
| `GET` | `/emoji-sounds/audio/{filename}` | Serve the raw audio file (used for admin preview playback in UI) |

#### TTS Splice Pipeline (in `tts.py`)
The `text_to_speech()` function is extended with an emoji-splice pass:
1.  **Pre-scan:** Before calling `KokoroTTSEngine.generate()`, scan the text for any emoji characters present in the `emoji_sounds` table.
2.  **Segment:** Split the text into alternating segments: `[text_chunk, emoji, text_chunk, emoji, ...]`.
3.  **Generate:** For each text segment, generate a Kokoro WAV byte stream. For each emoji segment, load the mapped `.mp3`/`.wav` from disk and decode to a NumPy sample array at the same sample rate.
4.  **Concatenate:** `np.concatenate()` all segments in order to produce the final combined WAV.
5.  **Strip emoji from TTS text:** The emoji character itself is stripped from the text passed to Kokoro (so it isn't read aloud as "car emoji").

#### Accepted Audio Formats
`.mp3`, `.wav`, `.ogg`, `.m4a`. The backend re-encodes all uploads to WAV at 24kHz (matching Kokoro's sample rate) using `soundfile` + `librosa` on ingest to avoid runtime conversion latency.

---

### 6.8 TV Brand Handlers & Device Discovery (Major New Architecture)

This is the largest architectural addition in recent commits. Raven refactored the entire media execution layer from a single monolithic `media.py` into brand-specific handler files with a dedicated device discovery pipeline.

#### TV Brand Handler Architecture
Four new dedicated handler files replace the old catch-all logic in `media.py`:

| Handler | File | HA Integration | Key Service |
| :--- | :--- | :--- | :--- |
| **Roku** | `handlers/roku.py` | `roku` | ECP `launch/{app_id}` + Music Assistant sibling delegation |
| **Samsung Tizen** | `handlers/samsung.py` | `samsungtv` | `samsungtv.send_key` (e.g., `KEY_POWER`, `KEY_HOME`) |
| **LG WebOS** | `handlers/webos.py` | `webostv` | `webostv.command` (e.g., `HOME`, `BACK`) |
| **Android TV** | `handlers/android_tv.py` | `androidtv_remote` | `androidtv_remote.send_command` + Cast sibling delegation for video |

Each handler includes `is_<brand>_device()` for runtime platform detection via HA entity attributes (app IDs, source lists).

---

#### Roku: Full Implementation Detail

The Roku handler (`handlers/roku.py`) is the most complex of the brand handlers because Roku's native API (`roku` HA integration) has no direct audio playback route — it can only control the UI. All media must go through the **ECP (External Control Protocol)** HTTP API at `http://<roku_ip>:8060`.

> [!IMPORTANT]
> **We use [Music Assistant (MASS)](https://music-assistant.io/) as the audio engine for all Roku media playback.** Music Assistant is a self-hosted music library manager that integrates with Home Assistant as a first-class integration. It handles library search, URI resolution, transcoding, and streaming to Roku hardware automatically. The HA integration exposes the `music_assistant.*` services we call directly.
> - **HA Integration docs:** [home-assistant.io/integrations/music_assistant](https://www.home-assistant.io/integrations/music_assistant/)
> - **MASS Python client (API reference):** [pypi.org/project/music-assistant-client](https://pypi.org/project/music-assistant-client/)
> - **MASS Server & developer docs:** [music-assistant.io](https://music-assistant.io/)
>
> All direct Roku device commands use the **ECP (External Control Protocol)** — Roku's local HTTP API running on port `:8060`.
> - **Roku ECP API reference:** [developer.roku.com/docs/developer-program/debugging/external-control-api.md](https://developer.roku.com/docs/developer-program/debugging/external-control-api.md)

**Roku App ID Registry (built-in):**
Netflix `12`, YouTube `837`, Hulu `2285`, Disney+ `291097`, Prime Video `13`, Spotify `22297`, Plex `13535`, Tubi `26079`, Peacock `427192`, HBO Max `301921`, Apple TV `472192`, **Media Assistant `782875`**.

##### Music Playback — Five-Step Pipeline (`roku_play_music`)

```
1. RESOLVE MA CONFIG ENTRY
   ha_client.find_mass_config_entry() — finds the MASS HA integration entry ID at runtime
   (not hardcoded — dynamically queried from HA config entries)

2. DISCOVER ROKU IP
   device_discovery.discover_device(entity_id, device_type="roku")
   → 10-strategy pipeline: registry cache → HomeKit diagnostics → HA attrs → ARP → SNMP → mDNS → SSDP → port scan
   If IP not found: falls back to MA-only playback (skips ECP step)

3. FIND MA PLAYER SIBLING
   find_ma_player_sibling(ha_url, ha_token, roku_entity)
   → Scans all HA media_player states to find one that:
     - Has an active_queue attribute (must be a live MA output, not just any MA entity)
     - Shares a name with the Roku entity (friendly_name substring match)
     - Has music_assistant in its integration/source/mass_player_type attrs
   → If no sibling found: returns FAILURE (can't delegate audio without it)

4. MA SEARCH
   ha_client.call_service("music_assistant", "search", ..., return_response=True)
   → Resolves the user's query to a full library:// URI (e.g. library://track/12345)
   → Uses MASS search across: tracks, albums, artists, playlists (limit=1)
   → Extracts: song_name, artist_name, full_library_uri, ma_media_type
   → ECP params populated: songName, artistName, albumArt (if available)
   Ref: https://www.home-assistant.io/integrations/music_assistant/#action-music_assistantsearch

5. ECP LAUNCH + MA AUDIO DELEGATION
   POST http://<roku_ip>:8060/launch/782875
     params: t=a, autoplay=true, songName=..., artistName=..., albumArt=...
   → Opens Media Assistant app on Roku with the rich music UI (album art, title)
   → asyncio.sleep(3) — waits for the app to load

   Then: music_assistant.play_media on the MA sibling entity
     media_id = full_library_uri (e.g. library://track/12345)
     enqueue = "replace"
   → MA handles transcoding and streaming to the Roku output automatically (MA 2.7+)
   Ref: https://www.home-assistant.io/integrations/music_assistant/#action-music_assistantplay_media

   Fallback: if music_assistant.play_media fails →
     media_player.play_media with media_content_type="music"
```

**Failure modes handled:**
- No Roku IP → skips ECP, falls back to pure MA sibling call
- No MA sibling with `active_queue` → returns `FAILURE` (not silent)
- ECP launch HTTP error → logs warning, `device_registry.invalidate_device()` marks IP stale for re-discovery

##### Video Playback — Four-Step Pipeline (`roku_play_video`)

```
1. DISCOVER ROKU IP (same 10-strategy pipeline)

2. SMART POWER SYNC (called in parallel with download via asyncio.create_task)
   get_state() checks if entity is "off", "idle", "unavailable", or "unknown"
   → if yes: media_player.turn_on → asyncio.sleep(2)
   → then: remote.send_command("Home") on the remote.* sibling entity → asyncio.sleep(2)
   (wakes from screensaver without requiring full boot)
   Note: Wake logic is in roku_wake_device() — callers create_task() it in parallel
         with download_video_progressive() for optimal latency

3. ECP LAUNCH with video params
   POST http://<roku_ip>:8060/launch/782875
     params: t=v, u=<mp4_url>, videoName=<title>, videoFormat=mp4
   → Media Assistant app plays the MP4 directly from execution service port 8888
   → Note: videoName (NOT songName) — songName is for audio only
   → Title is sanitized to remove emojis (prevents URL encoding issues)

4. Post-launch state verification
   get_state() confirms device transitioned to "on" or "playing"
   Failure: device_registry.invalidate_device(), return FAILURE
```

**Key distinction from music:** Video uses `t=v` + `videoName` + direct MP4 URL. Music uses `t=a` + `songName` + `artistName` + MA sibling delegation. They never share the same code path.

##### Android TV: Video Delegation Pattern (`handlers/android_tv.py`)

Android TV's native `play_media` with local stream URLs often fails (HA 500 error). The proven approach: detect Android TV, find its Cast sibling, and delegate video playback to the Cast entity.

```
1. DETECT ANDROID TV
   is_android_tv(ha_url, ha_token, entity_id) checks:
   - app_id contains "com.google.android.", "com.google.tv.", "com.android.", "backdrop", "tvlauncher"
   - device_class == "tv" (when device is off and app_id unavailable)
   - corresponding remote.office_tv_remote entity exists (androidtv_remote creates both)

2. FIND CAST SIBLING
   _find_cast_sibling(ha_url, ha_token, atv_entity_id) scans all media_player states:
   a. Exclude MA wrappers: app_id != "music_assistant", no mass_player_type, no active_queue
   b. Capability checks:
      - supported_features & 8424 (SUPPORT_PLAY_MEDIA)
      - cast_type in ("cast", "audio", "group", "chromecast")
      - entity_id contains _chrome, _cast, or _chromecast
      - friendly_name contains "cast" or "chrome"
   c. IP/MAC match from device_registry (strongest signal, if available)
   d. Name-based fallback: substring or exact friendly_name match
   e. Single candidate = confident match
   → Returns media_player.office_tv_chrome (Cast entity for the same physical TV)

3. POWER ON ANDROID TV
   media_player.turn_on → asyncio.sleep(2)
   androidtv_remote.send_command("home") → ensures TV is on home screen
   (Note: ADB home command may return 400 if device doesn't support it — non-fatal)

4. STOP ACTIVE CAST SESSION
   media_player.media_stop on the Cast sibling → asyncio.sleep(1)
   (Prevents session conflicts when switching from music to video)

5. DOWNLOAD VIDEO
   download_video_progressive(youtube_url) → returns (media_id, title)
   → yt-dlp format: 22/18/best[ext=mp4][height<=720] (H.264/AAC, single-file)
   → Returns after 5MB buffered, continues downloading in background

6. CAST TO SIBLING
   media_player.play_media on Cast entity:
     media_content_id = http://192.168.2.205:8888/media/{media_id}
     media_content_type = "video/mp4"
   → Cast device streams the MP4 from execution service port 8888
   → HTTP Range headers support progressive streaming
```

**Why delegation works:** The Cast entity (`media_player.office_tv_chrome`) is a Chromecast built into the Android TV hardware. It handles `video/mp4` URLs natively, while the Android TV remote integration (`androidtv_remote`) only supports transport commands (home, back, power) and app launching — not direct URL playback.

**Failure modes handled:**
- No Cast sibling found → falls back to direct `play_media` on Android TV entity (may fail)
- ADB home command returns 400 → logged as warning, non-fatal
- `media_stop` returns 500 → device was already idle, non-fatal
- yt-dlp download fails → returns FAILURE with descriptive message

##### Announcement (`announce_roku` in `announce_handlers.py`)

Announcements on Roku use a **separate, simpler ECP path** from the music pipeline — no MA sibling is involved. The Kokoro-generated WAV is hosted by the execution service and passed directly to Media Assistant as an audio URL:

```
1. DISCOVER ROKU IP via device_discovery

2. WAKE DISPLAY
   ECP POST http://<roku_ip>:8060/keypress/Home  (wakes from screensaver)
   media_player.turn_on via HA
   asyncio.sleep(3)

3. LAUNCH MEDIA ASSISTANT with audio URL
   POST http://<roku_ip>:8060/launch/782875
     params:
       t=a                          ← audio type
       u=<kokoro_wav_url>           ← the execution service /media/tts-{id} URL
       songName="SharedLLM Announcement"  (or the actual TTS message text)
       songFormat=wav
       autoplay=true
   → Media Assistant opens and plays the WAV directly from the execution service URL

4. Fallback: media_player.play_media(media_content_type="url")
```

**Key distinction:** Music playback uses the `library://` URI + MA sibling delegation. Announcements use the raw `http://` WAV URL directly. They never share the same code path.

##### TV Type Auto-Detection (`detect_tv_type` in `announce_handlers.py`)

Before any dispatch, the system runs a 12-priority detection pass on the entity's attributes:
1. Cast: `chrome`/`_cast` in entity_id OR known Cast app_id
2. **Roku**: `roku` in entity_id OR Roku sources in source_list OR ≥5 streaming apps OR MA player with Roku `active_queue`
3. Android TV: Android package prefix in app_id
4. WebOS: `lg_`/`webos` in entity_id
5. Samsung: `samsung`/`tizen` in entity_id
6. Sony Bravia: `bravia`/`sony` in entity_id
7. ESPHome/DLNA/Music Assistant: entity_id substring
8. Generic TV: `device_class=tv` OR TV inputs (HDMI, AV) in source_list
9. Speaker: `device_class=speaker`
10. Loaded HA components (`cast.media_player`, `roku`, `webostv.media_player`, etc.)
11. **Web search fallback**: If still unknown, constructs a search query from entity attrs and uses SearXNG to identify the platform
12. Final fallback: `unknown` handler (generic `media_player.play_media`)

**TCL and Sharp TVs** are mapped to Roku in the manufacturer pattern table (both brands use Roku OS), so they automatically follow the Roku announcement path.

---


#### Multi-Strategy Device Discovery Pipeline (`device_discovery.py`)
To find physical device IPs for direct API and remote control calls, a **10-strategy ordered pipeline** runs in sequence — stopping at the first successful match:

1.  **Persistent Registry Cache:** Instant lookup from the local SQLite device registry.
2.  **HA Device Registry:** Queries HA's configuration entries and matching device IDs to resolve hosts and extract MAC addresses.
3.  **HomeKit Controller Diagnostics:** Resolves LG WebOS TV host IPs (which HA redact by default) by extracting `AccessoryIPs` from homekit_controller diagnostics.
4.  **HA Entity Attributes:** Extracts IP/host directly from attributes like `ip_address`, `ip`, or `host`.
5.  **ARP Table Scan:** Reads `/proc/net/arp` (or standard `arp -a` CLI), probes matching ports, and correlates friendly names.
6.  **Subnet ARP Scan:** Actively scans the local subnet (and adjacent subnets) using `arp-scan`, probing responsive IPs.
7.  **Router SNMP Walk:** Queries the local router's `ipNetToMediaPhysAddress` table (via SNMP walk `1.3.6.1.2.1.4.22.1.2`) to fetch the router's current ARP table.
8.  **mDNS / Bonjour:** Resolves `<friendly_name>.local` or `<entity_id>.local` hostnames.
9.  **SSDP Broadcast:** Uses UDP multicast (`239.255.255.250:1900`) searching for SSDP targets (Roku, Cast, DLNA).
10. **Batched Network Port Scan:** Probes a fast parallel TCP scan of common ports across active IPs in the subnet:
    *   **Port Maps:** Roku `:8060`, WebOS `:3000`/`:3001`/`:9080`, Samsung `:8001`/`:8002`, ADB `:5555`, Cast `:8009`, DLNA `:9197`/`:8200`, ESPHome `:6053` (native API), Tasmota `:80`, MQTT `:1883`/`:8883`.
    *   **ESPHome Native Integration:** Probes port `6053` using `aioesphomeapi` to retrieve software versions, board compile platform, and identifies if noise encryption keys (`encryption_required` flag) are required.

**Dynamic Subnet Resolution:** The pipeline dynamically detects the active local subnet by inspecting the default routing table (`/proc/net/route`) or falling back to local interface IP blocks. The environment variables `SCAN_SUBNET` or `LOCAL_SUBNET` can override this detection block at runtime.

**Execution network mode:** The `execution` container now runs in **host network mode** (required for SSDP multicast and ARP to function correctly across the local network).

#### Persistent SQLite Device Registry (`device_registry.py`)
Discovered devices are stored in a persistent `aiosqlite` SQLite database (`/data/device_registry.db`, WAL mode) keyed by `entity_id`. Schema captures: `ip`, `mac`, `hostname`, `integration`, `friendly_name`, `discovery_method`, `metadata` (JSON), `last_seen`, `last_verified`, and `ip_stale` flag. On connection errors, the registry marks IPs as stale (`invalidate_device()`), triggering re-discovery on next access.

#### Identity as the Sole Credential Source (`.env` is Seed-Only)
A critical architectural formalization: all services (especially `execution`) now resolve HA credentials at runtime via `resolve_internal_user()` against the Identity service. The `.env` file is only read **once** during initial seed (`POST /api/admin/seed`). No service reads `.env` in production. Tests use `PYTEST_CURRENT_TEST` placeholders, never `.env`.

---

## 7. Known Technical Debt & Stubbed Features

Based on a deep-dive audit of the `services/execution/handlers/` code, the following features exist in the LLM tool schemas but are purposefully stubbed out (`"Not yet implemented"`) and must be built:

1.  **Timer Management (`timer.py`):** 
    *   The `TimerRequest` schema supports `pause` and `resume`.
    *   *Current State:* These actions immediately return a `FAILURE` stub. 
    *   *Fix Required:* Implement Redis TTL extraction and re-insertion logic to pause and resume active timers.
2.  **Calendar Management (`calendar.py`):**
    *   The `CalendarRequest` schema supports `delete` and `update`.
    *   *Current State:* These actions immediately return a `FAILURE` stub.
    *   *Fix Required:* Implement proper CalDAV UID resolution to allow the LLM to modify or delete existing Nextcloud calendar events.
3.  **Storage Providers (`storage/providers.py`):**
    *   *Current State:* Contains `NotImplementedError` base stubs for cloud object storage.
    *   *Fix Required:* Build out the concrete Nextcloud/S3 provider classes.
4.  **Device Registry Subnet Configuration (Resolved):**
    *   *Resolution:* Fully resolved in `device_discovery.py` and `handlers/network_scan.py` via dynamic interface and default routing table (`/proc/net/route`) inspection, with optional environment variable overrides (`SCAN_SUBNET` or `LOCAL_SUBNET`). Hardcoded defaults have been completely removed.

---

## 8. Microservice Breakdown & UI Representation

The SharedLLM backend is organized into strictly isolated, purpose-built containers. Here is the current status of each service, required additions, and exactly how they manifest in the Jarvis OS 2.0 UI.

| Microservice | Current Status & Role | Required Additions / Fixes | Jarvis UI Representation |
| :--- | :--- | :--- | :--- |
| **`gateway`** | **Solid.** The central orchestrator routing requests. *Deep-Dive Finding:* It features a **Pre-Flight Capability Check** that evaluates credentials *before* dispatching. If missing, it short-circuits to Identity. It scales VRAM context via `api/ps`, uses aggressive credential sanitization, and has a global `INFERENCE_LOCK`. Fast-path `turn_on`/`turn_off` now uses `media_type="power"` in `resolve_media_target()`, scoring `device_class=tv` highest (+200) and deprioritizing Cast (-100) and MA wrappers (-200) to ensure power commands target the actual TV entity. | **Needs Lock Manager.** Must implement the Redis async preemption logic to manage VRAM constraints on atomic jobs. | *Invisible Brain.* The UI connects to it via `/v1/chat` and `/ws`. It drives all chat interfaces. |
| **`execution`** | **Greatly Expanded.** Houses 35+ tool handlers. *Deep-Dive Finding (Major):* A fully new **TV Brand Handler** architecture has been introduced with dedicated files for `roku.py`, `samsung.py`, `webos.py`, and `android_tv.py`, each with brand-specific transport command maps and platform detection logic. Roku music now uses a two-part ECP + Music Assistant sibling-delegation pattern. **Android TV video playback** delegates to Cast sibling via `_find_cast_sibling()` (capability-based detection, not name matching). A **multi-strategy Device Discovery Pipeline** (`device_discovery.py`) and persistent **SQLite Device Registry** (`device_registry.py`) have been added, discovering device IPs via 7 ordered strategies (cache → HA registry → entity attrs → ARP → mDNS → SSDP → network scan). Credentials are now resolved from Identity at runtime — `.env` is seed-only. Gateway's `.env` file removed; all settings come from Identity DB. YouTube search uses `yt-dlp ytsearch:1` for accuracy (SearXNG HTML regex is unreliable). | Subnet (`DEFAULT_SUBNET`) is hardcoded; move to env var. | **Capability Widgets.** Powers Media, Timer, Notes, and Smart Home toggle widgets. |
| **`identity`** | **Solid.** Manages `identity.db`, user sessions, and heavily encrypts tokens via `crypto.py` Fernet. Acts as the **Triage Queue** for Raven self-repair missions. | Add an explicit device-revocation endpoint for stolen/lost tablets. | **Admin Profiles / Settings.** Renders the user management panel where admins securely inject tokens without touching `.env`. |
| **`storage`** | **Stubbed.** Meant to abstract file/object storage across local disk, Nextcloud, and S3. | Complete the base class implementations (`providers.py` throws `NotImplementedError`). | **File Manager Widget.** Will allow users to drop files into the chat and have them safely persisted to Nextcloud. |
| **`rag`** | **Operational.** Manages ChromaDB and `SentenceTransformer` embeddings across 4 collections. *Deep-Dive Finding:* Implements advanced Hybrid Search, combining standard vector querying with keyword `$contains` querying, scored via a Reciprocal Rank Fusion (RRF) algorithm. | Better garbage collection for stale vectors when Nextcloud notes are permanently deleted. | **NotebookLM Indicators.** When the LLM cites a note, a small pill appears in the chat linking to the source document. |
| **`capabilities-sync`** | **Script Runner.** An ephemeral Docker container defined in `docker-compose.yml` that runs `scripts/index_capabilities.py` on startup to seed the RAG DB. | Standardize to run periodically or on a webhook hook. | *Invisible.* Seeds the LLM's system prompt with available tool schemas. |
| **`control_plane`** | **Functional.** Manages the host Docker socket (port 8008), retrieves logs, and executes container restarts. | Tighten `exec_run` sanitization to prevent arbitrary command injection. | **System Admin Dashboard.** Provides the "Live Logs" viewer and physical "Restart Service" buttons in the Raven Ops Panel. |
| **`workspace_runtime`** | **Operational.** A secure sandbox (port 8007) for executing raw Python/shell scripts and receiving webhooks. Enforces granular, per-workspace security policies (read, write, git_status). | Isolate network access so sandboxed code cannot probe the internal `192.168.x.x` home subnet. | **Code Execution Blocks.** Renders live `stdout`/`stderr` terminal windows inside the chat when Raven runs a script. |
| **`automation`** | **Operational.** A dedicated background processor container that handles scheduled polling events without blocking the Gateway. | Needs robust retry queues for flaky APIs. | *Invisible.* Triggers scheduled alarms and macros. |
| **`logging`** | **Functional.** Aggregates logs across all containers. | Implement log retention policies (logrotate) to prevent NVMe exhaustion. | **Telemetry Viewer.** A searchable log table inside the Admin Panel. |
| **`ui`** | **Legacy.** Currently serving the older React dashboard. | **Complete Overhaul.** Needs to be entirely rewritten into the mobile-first "Neon Glass" aesthetic with Zustand state management. | **The Shell.** The entire visual experience of Jarvis OS 2.0. |
| **`Alpaca` (External)** | **Operational.** Running remotely on the Ryzen/RTX 4060 Inference Node managing the TurboQuant image. | No immediate backend fixes, but needs rigorous monitoring for OOM errors. | **Model Selector.** Appears in Settings as a dropdown to toggle between standard Ollama models and TurboQuant models. |

---

## 9. Cross-Domain Ambient Scenarios (The Jarvis Synergy)

The true power of Jarvis OS 2.0 isn't in isolated widgets—it's how the LLM autonomously ties multiple backend domains together to create seamless, ambient intelligence.

### Scenario A: The "Phantom Load" Intervention (Power + Presence + Comms)
*   **Trigger:** The backend detects a high power draw (1000W+) from the basement entertainment center via a smart plug.
*   **Context Check:** Jarvis queries the `ESPresense` network and realizes no user has been in the basement for over two hours.
*   **Action:** Jarvis executes a `LightControlRequest` to power down the smart plug. It immediately generates an audio payload via Kokoro TTS ("Sir, I detected the basement TV was left on. I have suspended the power.") and executes a `TalkRequest` to drop the voice note natively into the user's Nextcloud Chat.

### Scenario B: Contextual Security (Memory/RAG + Calendar + Smart Home)
*   **Trigger:** The user dictates a quick Nextcloud Note via the mobile app: *"Leaving for vacation to Hawaii from Friday to next Tuesday."*
*   **Context Check:** The `sync_rag` pipeline indexes this note. The LLM extracts the temporal data, recognizes the intent, and autonomously executes a `CalendarRequest` to block out the family CalDAV calendar.
*   **Action:** On Friday at 5:00 PM (based on the calendar event), Jarvis autonomously executes a `SecurityRequest` to arm the alarm, sets the `ClimateRequest` to eco-mode, and enables a randomized lighting schedule.

### Scenario C: The Automated Parent (Skylight + Media + Announcements)
*   **Trigger:** A child checks off their final daily chore ("Clean Room") on the physical Skylight board.
*   **Context Check:** The `ChoreProvider` syncs the state change. Jarvis identifies the user who completed the chore.
*   **Action:** Jarvis executes an `AnnouncementRequest` routed specifically to the child's bedroom speaker via ESPresense routing, using a custom emoji-mapped sound effect (🎉 -> `cheer.mp3`) followed by TTS ("Great job cleaning your room!"). Immediately after, it executes a `MediaPlayRequest` to cast the child's favorite song via Music Assistant.

---

## 10. UI Content Design & Page-by-Page Wireframes

This section breaks down the React/Ionic frontend page by page. It details exactly what UI components exist, their target audience (Standard User vs. Admin), what they control, and how they interact with the backend.

### 10.1 Global Shell & Navigation (Responsive Layout)
*   **Target Audience:** Standard Users & Admins
*   **Responsive Framework:** The UI is strictly cross-platform. On Mobile/Tablet (Portrait), it utilizes a Bottom Navigation Bar. On Desktop or Tablet (Landscape), this gracefully transitions into a persistent Left-Hand Sidebar to maximize vertical screen real estate.
*   **"Halo" Presence Banner (Top Bar):** 
    *   *Type:* Dynamic Text Block with Swipe Gesture (Mobile) / Click Arrows (Desktop).
    *   *Controls:* Displays current room based on ESPresense (e.g., "Living Room"). Overriding the room context causes the grid below to re-render for the selected room.
*   **Voice Assistant Overlay (Hidden/Modal):**
    *   *Type:* Frosted Glass Modal (`backdrop-blur-3xl`) with Audio Wave Canvas.
    *   *Controls:* Triggered by the "Jarvis" wake word (Porcupine) or clicking a persistent mic icon on Desktop. Captures mic audio and streams it to the Gateway.
*   **Navigation Links (Bottom Nav / Sidebar):**
    *   *Type:* Icon + Text Buttons.
    *   *Routes:* `[ Home ]` | `[ Chat ]` | `[ Media Controller ]` | `[ Admin Ops ]` (hidden for non-admins).

### 10.2 Home Dashboard (`/`)
*   **Target Audience:** Standard Users
*   **Purpose:** The ambient capability matrix. Widgets auto-mount here via the Zustand store. On Mobile, widgets stack vertically. On Desktop/Tablet, they form a multi-column masonry grid.
*   **Energy Insights Widget:**
    *   *Type:* Spline Chart (React-Recharts) + Dynamic Text Block.
    *   *Controls:* Displays real-time KwH. Text shows LLM-generated summaries. Tapping the card opens a detailed breakdown modal.
*   **Ambient Countdown Timer:**
    *   *Type:* Circular Progress SVG + Large Digital Text.
    *   *Controls:* Pulses visually. Tapping reveals `[+1 Min]`, `[Pause]`, and `[Cancel]` buttons. Calls `timer.py` backend.
*   **Quick Notes Widget:**
    *   *Type:* Masonry Grid Cards.
    *   *Controls:* Shows recent Nextcloud Notes. Tapping expands into a full-screen markdown text editor. Includes a `[+ New Note]` floating action button.

### 10.3 Native Chat Client & Smart Inbox (`/chat`)
*   **Target Audience:** Standard Users
*   **Purpose:** Direct interface to Nextcloud Talk and Jarvis LLM interactions.
*   **Message Feed:**
    *   *Type:* Infinite Scroll List View.
    *   *Controls:* Renders text, markdown, and interactive chat games (Bible Trivia, Kids Math).
*   **Voice Note Player:**
    *   *Type:* Inline Audio Player.
    *   *Controls:* `[Play/Pause]` button with a waveform scrubber for Kokoro TTS notifications.
*   **Input Bar:**
    *   *Type:* Text Box + `[Mic]` Button + `[Send]` Button.
    *   *Controls:* Typing sends a standard text message. Holding `[Mic]` records raw audio for Recorded Announcements.

### 10.4 Music Assistant (MASS) Controller (`/media`)
*   **Target Audience:** Standard Users
*   **Purpose:** A dedicated, full-screen remote control for Music Assistant and Audiobookshelf, completely replacing the need for native Roku or Sonos apps.

#### Active Session Player
*   **Purpose:** Displays and controls any active Music Assistant sessions currently running in the house (especially those launched autonomously by Jarvis or via the Web/App UI).
*   **Now Playing Hero:**
    *   *Type:* Large Image (Album/Book Art) + Scrolling Marquee Text (Title/Artist).
*   **Universal Transport Controls:**
    *   *Type:* Large, tactile touch buttons: `[Prev]`, `[Play/Pause]`, `[Next]`, `[Shuffle]`, `[Repeat]`.
    *   *Controls:* Maps directly to `MediaTransportRequest`. Because the backend proxies the media, these buttons work flawlessly regardless of the target speaker brand.
*   **Queue Viewer:**
    *   *Type:* Scrollable list below the transport controls.
    *   *Controls:* Shows the upcoming tracks in the MASS queue. Tapping a track jumps to it.
*   **Target Device Selector:**
    *   *Type:* Dropdown Menu / Carousel.
    *   *Controls:* Allows the user to select which device (or speaker group) the music should cast to (e.g., "Kitchen Speaker" vs "Whole House").
*   **Volume Slider:**
    *   *Type:* Horizontal Range Slider.
    *   *Controls:* Adjusts target device volume via `ha_client.py`.

### 10.5 Universal Remote Control (`/remote`)
*   **Target Audience:** Standard Users (only shown if the user has at least one media device assigned to their profile)
*   **Purpose:** A single, device-aware remote control UI that replaces the physical remote for any TV, speaker, or media player assigned to the user. Operates entirely through the existing `MediaTransportRequest` schema — no new backend endpoints required.
*   **Visibility Rule:** The `/remote` route is hidden from navigation if the user has zero assigned `media_player.*` entities. When a device is assigned via the Admin User Management panel (Section 10.9), the route becomes visible automatically.

#### Device Picker
*   *Type:* Horizontal scrollable card row at the top of the page.
*   *Each card shows:* Device friendly name + brand icon (Roku, Samsung, LG, etc.) + current state (On/Off/Playing).
*   *Controls:* Tapping a card selects it as the active remote target. The button layout below adapts to the selected device's brand.

#### Power & Input
*   `[Power On]` / `[Power Off]` — maps to `MediaTransportRequest.command = "power_off"` + `media_player.turn_on` via HA.
*   `[Input / Source]` — opens a bottom sheet listing the device's `source_list` from HA state. Selecting a source calls `media_player.select_source`.

#### Navigation D-Pad
*   *Type:* Classic circular D-Pad with center `[OK/Select]` button.
*   *Controls:* `[▲]` `[▼]` `[◄]` `[►]` `[OK]` — maps to `MediaTransportRequest` commands (`up`, `down`, `left`, `right`, `enter`). For Roku, these translate to ECP key presses (`Up`, `Down`, `Left`, `Right`, `Select`). For other brands, the handler maps to platform-native equivalents.
*   `[Home]` button — `MediaTransportRequest.command = "home"`.
*   `[Back]` button — `MediaTransportRequest.command = "back"`.

#### Volume Controls
*   *Type:* Three-button cluster: `[Vol −]`, `[Mute]`, `[Vol +]`.
*   *Controls:* Maps to `MediaTransportRequest.command = "volume_down"` / `"volume_up"` and `media_player.volume_mute`.
*   *Optional:* Inline volume slider for precise control (calls `media_player.volume_set` with `volume_level` float).

#### Playback Controls
*   *Type:* Standard media button row: `[⏮ Prev]`, `[⏸ Play/Pause]`, `[⏭ Next]`, `[⏹ Stop]`.
*   *Controls:* Maps to `MediaTransportRequest` `previous`, `resume`/`pause`, `next`, `stop`.

#### Channel Controls (TV devices only)
*   *Type:* `[Ch +]` / `[Ch −]` buttons — shown only when the target device `source_list` contains TV-input-style sources (Live TV, antenna, cable).
*   *Controls:* Sends platform-specific channel commands (Roku: `ChannelUp`/`ChannelDown` ECP keys; others: HA `media_player.media_next_track` as a fallback).

#### Quick App Launcher
*   *Type:* Row of app icon buttons (Netflix, YouTube, Hulu, Plex, etc.).
*   *Controls:* Populated from the Roku App ID Registry or HA `source_list` for non-Roku devices. Tapping launches the app via `roku_launch()` or `media_player.select_source`.
*   *Only shown* for TV-class devices (not speakers).

#### Brand Adaptation
The backend already detects device brand via `detect_tv_type()`. The remote UI reads this from the device's HA state attributes and renders only the controls relevant to that platform:

| Platform | Navigation | Volume | Channel | App Launcher |
| :--- | :--- | :--- | :--- | :--- |
| **Roku** | D-Pad (ECP) | ✅ | ✅ (ECP) | ✅ (App ID list) |
| **Samsung / WebOS / Bravia** | D-Pad (HA service) | ✅ | ✅ | ✅ (source_list) |
| **Android TV** | D-Pad (ADB) | ✅ | ✅ | ✅ (source_list) |
| **Cast / Speaker** | Play controls only | ✅ | ❌ | ❌ |

### 10.5b Intercom (`/intercom`)
*   **Target Audience:** Standard Users (shown only if user has ≥1 assigned media device)
*   **Purpose:** Room-to-room voice communication. Sends raw voice clips from the browser microphone directly to any speaker, tablet, or TV in the house. No phone call infrastructure required — built entirely on the existing announcement pipeline.

#### Contact List
*   *Type:* Grid of avatar cards — one per household user or room.
*   *Each card shows:* User avatar + name + current room (from ESPresense) + whether their device is online.
*   *Tapping a card* selects that user/room as the target.

#### Hold-to-Talk Button
*   *Type:* Large central push-to-talk button (microphone icon, pulses red while recording).
*   *Behaviour:*
    - `pointerdown` / `touchstart` → `MediaRecorder.start()` → green recording ring animates.
    - `pointerup` / `touchend` → `MediaRecorder.stop()` → blob assembled → `POST /execute/intercom/send`.
    - Max clip duration: 30 seconds (client-enforced). Countdown ring appears at 25s.
    - If released before 0.5s: clip discarded, toast shows "Too short".
*   *Fallback:* `[Record]` toggle button for devices without reliable pointer events (tablets).

#### Target Selector
*   *Type:* Multi-select chip row below the contact grid.
*   *Options:* Individual users, rooms, device groups, or `[Broadcast All]` (maps to `action="broadcast"`).
*   TV-class devices in the list are tagged with a 📺 icon to indicate one-way only.

#### Incoming Clip Player
*   *Type:* Floating toast + inline audio player.
*   *Behaviour:* When another user sends a clip and the current user's tablet is the target, a WebSocket push (`/ws/capabilities`) triggers a toast: "📣 [Name] from [Room]" with auto-play enabled.
*   The clip plays immediately. The user can tap `[Reply]` to open hold-to-talk targeted back at the sender.

### 10.6 Personal Calendar (`/calendar`)
*   **Target Audience:** Standard Users
*   **Purpose:** A unified, user-specific view of all upcoming events, tasks, and chores.
*   **Data Aggregation:** This page actively filters data. It pulls from Nextcloud (CalDAV) and Skylight (Chores), but **only displays items assigned to or belonging to the currently authenticated user**. It strips out other family members' noise to provide a focused daily agenda.
*   **Daily Agenda View:**
    *   *Type:* Vertical Timeline / List.
    *   *Controls:* Chronological flow of the day. Integrates both hard calendar events (Nextcloud) and floating daily tasks (Skylight).
*   **Month Grid:**
    *   *Type:* Standard 30-day Calendar Grid.
    *   *Controls:* Color-coded dots indicate event density. Swiping left/right navigates months.

### 10.7 Raven Ops Panel (`/admin/ops`)
*   **Target Audience:** Admins Only (Requires `is_admin=True`)
*   **Purpose:** Interface for monitoring the autonomous LLM and managing the Docker Control Plane.
*   **Operations Timeline:**
    *   *Type:* Vertical Stepper / Feed.
    *   *Controls:* Subscribes to Redis `raven:mission:stream`. Displays items like "AST Parsed `utils.py`".
*   **Interactive Commit Review:**
    *   *Type:* Expanding Diff Viewer + `[Approve]` / `[Reject]` Buttons.
    *   *Controls:* When Raven prepares a code patch, the Admin reviews the Git diff here before the system commits to the master branch.
*   **Control Plane Dashboard:**
    *   *Type:* Data Table with Status Dots (Green/Red).
    *   *Controls:* Lists all `sharedllm_` Docker containers. Includes `[View Logs]` (opens a terminal-style text box modal) and `[Restart]` (calls the Port 8008 Control Plane API) buttons.

### 10.8 Dynamic Integrations Config (`/admin/integrations`)
*   **Target Audience:** Admins Only
*   **Purpose:** Configure backend integrations (HA, Nextcloud, GitHub) without touching `.env` files.
*   **Dynamic Forms:**
    *   *Type:* Auto-generated Form fields based on JSON Schema.
    *   *Controls:* Text inputs for URLs, secure password masking fields for Tokens. Includes a `[Test Connection]` button (calls `/api/auth/test-connection` in Identity) and a `[Save]` button to persist encrypted credentials into `identity.db`.

### 10.8a LLM Settings (`/admin/integrations` → LLM tab)
*   **Local Inference URL:** Text field for `ollama-server.local:11434` (or any `/api/chat`-compatible endpoint). Default updated from `llama-server` to `ollama-server.local` to reflect DNS sidecar.
*   **System Timezone Selector *(New)*:** A `<select>` dropdown (powered by `Clock` icon, Lucide) lets admins choose the system timezone stored in `identity.db`. The Gateway reads this at **runtime** via `get_all_settings()` rather than from `.env` or a hardcoded default. The dropdown covers all major US zones plus Europe/Asia/Pacific/UTC. Default: `America/Phoenix` (MST, no DST). This value is used for all time/date queries and log timestamps.

### 10.9 Admin User Management (`/admin/users`)
*   **Target Audience:** Admins Only (requires `is_admin=True`)
*   **Purpose:** Securely manage household users, Voice ID assignments, and third-party credentials.
*   **User Roster:**
    *   *Type:* Data Table / List.
    *   *Controls:* Lists all active Jarvis OS users. Indicates admin status and which third-party tokens are currently bound to their profile.
*   **External Import Wizard:**
    *   *Type:* Step-by-step Modal.
    *   *Controls:* Three large buttons for the source integration: `[Import from Nextcloud]`, `[Import from Home Assistant]`, `[Import from Skylight]`. 
    *   *Action:* The backend queries the selected service's API, returns a list of discovered users, and allows the admin to select which ones to batch-create inside Jarvis OS. It automatically links their source IDs to their new Jarvis profile.

### 10.10 Emoji Sound Manager (`/admin/sounds`)
*   **Target Audience:** Admins Only (requires `is_admin=True`)
*   **Purpose:** Map emoji characters to custom audio files for TTS splicing. This is the configuration interface for the system described in Section 6.9.
*   **Mobile/Desktop:** Two-column grid on desktop; single-column list on mobile.

#### Sound Library Grid
*   *Type:* Card grid, one card per mapping.
*   *Each card displays:*
    *   The emoji character (large, 48px)
    *   Admin-assigned label text (e.g., "Car Honk")
    *   Audio waveform visualization or duration badge (e.g., "1.2s")
    *   `[▶ Preview]` button — plays the audio file directly in the browser via the `/emoji-sounds/audio/{filename}` endpoint.
    *   `[Delete]` button (icon, red) — calls `DELETE /emoji-sounds/{emoji}`, removes card with animation.

#### Add New Sound Panel
*   *Type:* Inline form panel at top of page (always visible, not a modal).
*   **Emoji Picker Input:**
    *   *Type:* Text input accepting a single emoji character, with a small emoji picker button that opens a searchable emoji palette.
    *   *Validation:* Rejects non-emoji characters. Warns if emoji is already mapped.
*   **Label Field:**
    *   *Type:* Text input.
    *   *Purpose:* Admin-friendly name for the sound (e.g., "Celebration Cheer").
*   **Audio File Upload:**
    *   *Type:* Drag-and-drop file drop zone with a fallback `[Browse Files]` button.
    *   *Accepted types:* `.mp3`, `.wav`, `.ogg`, `.m4a` (enforced client-side and server-side).
    *   *Max size:* 5MB (enforced).
    *   *Feedback:* Shows filename + file size after selection. Plays a preview before submission.
*   **`[Save Mapping]` Button:**
    *   Submits `multipart/form-data` to `POST /execute/emoji-sounds/upload`.
    *   Shows an animated progress bar during upload.
    *   On success: clears form, new card animates into the grid.
    *   On error: displays inline error message (e.g., "Emoji already mapped — delete the existing one first").

#### Live Preview Test
*   *Type:* Text area + `[Test TTS]` button.
*   *Purpose:* Admins can type a sentence containing mapped emojis (e.g., `"The car is here 🚗 let's go!"`), hit `[Test TTS]`, and hear the full spliced audio output in the browser before deploying to speakers.
*   *Controls:* Calls the existing `/execute/announce` endpoint with `target="browser_preview"` flag (no HA dispatch), returns the WAV bytes directly to an `<audio>` element.

---

## 11. Testing & CI/CD Pipeline

The system employs a rigorous, multi-tiered testing strategy encompassing both unit tests and integration tests, designed to work seamlessly across local development environments and GitHub Actions CI pipelines.

### 11.1 Pytest Configuration & Security (`pytest.ini` & `conftest.py`)
*   **Dynamic Secret Generation:** Tests *never* use hardcoded secrets. `conftest.py` utilizes `Fernet.generate_key().decode()` to dynamically spin up a fresh encryption key for every test run, ensuring CI logs cannot leak valid keys.
*   **Environment Mocking:** All microservices (Identity, Execution, RAG, Storage) are explicitly mapped to `localhost:<port>` to prevent test pollution against the live homelab.
*   **Execution Markers:** Tests are heavily decorated with markers to separate fast logic from heavy IO:
    *   `@pytest.mark.local_only`: Flags tests requiring local hardware or heavy environment setup. These are gracefully skipped in standard CI runs and must be invoked via `pytest --run-local`.
    *   `@pytest.mark.server_only`: Flags tests requiring a fully deployed remote server instance (`pytest --run-server`).
    *   `@pytest.mark.integration`: Used to verify inter-service communication (e.g., Gateway talking to Execution).

### 11.2 CI/CD Workflows (`.github/workflows/`)
The GitHub Actions pipeline is divided into four distinct workflows to parallelize testing and minimize merge times:
1.  **`python-tests.yml`:** The core backend unit test suite. Executes tests across the Python microservices without requiring the full Docker stack.
2.  **`soa_tests.yml`:** The Service-Oriented Architecture integration suite. Likely spins up lightweight containers or mocks to verify inter-service HTTP communication.
3.  **`ui-tests.yml`:** Dedicated pipeline for the React/Vite frontend.
4.  **`docs.yml`:** Verifies markdown linting and documentation integrity.

### 11.3 Capability Awareness Testing
As evidenced by `docs/capability_test_report.md`, the autonomous LLM's understanding of its own tools is explicitly tested. The system runs validation queries (e.g., *"Trigger a re-index of your own tool capabilities"*) to ensure the model successfully parses its JIT-injected tool schemas and returns `SUCCESS` payloads with the correct `Intent` classification.

### 11.4 Server Deployment Workflow
The deployment strategy strictly separates the **Source Repository** (the local checkout where builds originate) from the **Workspace Root** (the ephemeral runtime `WORKSPACE_HOST_PATH` where Raven executes). You must *never* run `docker compose up --build` from the workspace.

**Standard Deployment Flow:**
1.  Code is pushed to the `microservices` branch.
2.  On the server (`ai.local`), a `git pull origin microservices` is executed inside the Source Repository.
3.  A Post-Merge Git Hook automatically triggers `scripts/deploy.sh`.
4.  **Smart Service Change Detection:** The deploy script analyzes the git diff and only rebuilds affected containers (e.g., changes to `services/gateway/*` rebuilds only the gateway; changes to `docker-compose.yml` rebuilds everything).
