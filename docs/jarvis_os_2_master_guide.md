# Jarvis OS 2.0: Master Development Guide

## 1. Architectural Vision & Scope
Jarvis OS 2.0 transforms a standard smart home dashboard into an **Ambient Computing Environment**. Built to scale effortlessly for a large household (7+ users), it dynamically adapts to each user's role, preferences, and physical location within the home.

### 1.1 Core Constraints & Stack
The system operates on a highly optimized **Dual-Node Architecture**:

1.  **Application Node (`jeremiah@ai`):** An Intel N150 Mini PC (16GB RAM, 512GB NVMe). This node hosts the core Python/FastAPI microservices (Gateway, Execution, Identity, Storage, RAG).
2.  **Inference Node (LLM Host):** An AMD Ryzen 7 5700G with an NVIDIA RTX 4060 (8GB VRAM) and 32GB RAM. This machine runs the LLM engines as a separate service, reachable at **`192.168.1.216`** (updated from `.204` in latest Raven commit). All SharedLLM microservices connect to it via Docker `extra_hosts` mapping (`ollama-server`, `llama-server`).
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
    *   `/execute`: Routed directly to `execution:8003`.
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
*   **Backend Reality:** `execution/handlers/video.py` handles the `videoplayrequest`. It uses `yt-dlp` to search YouTube and extract the most compatible direct MP4 stream (`avc1/mp4a`) for Cast/Roku devices. It temporarily hosts this file on the Execution service (`port 8003`) and natively commands Home Assistant to power on the target TV before pushing the stream URL.
*   **The "Why":** We proxy videos this way because native YouTube apps on smart devices (like Roku) are notoriously difficult to control via APIs. By downloading the direct MP4 and hosting it locally, Jarvis can force *any* media device in the house to play the video or audio, even if that device doesn't have a YouTube app installed.
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
    *   *Options:* `action` (send, send_voice, read), `token`, `message`, `text_to_voice`.
    *   *UI Correlation:* Sends data to Nextcloud Talk. Populates the **Smart Inbox Widget** which can expand into the **Native Chat Client** full-screen app.
*   **`AnnouncementRequest` / `TTSRequest`**:
    *   *Options:* `message`, `target_device`, `announce_all` (boolean), `recorded_audio_path` (for raw voice notes), `voice` (e.g. `af_heart`), `storybook` (boolean).
    *   *UI Correlation:* Drives the Dashboard Intercom Widget. If `announce_all` is true, the backend checks the HA entity blacklist before broadcasting. If it's a TTS payload, the backend parses the text for mapped emojis and splices in custom admin-uploaded `.mp3` files (e.g., replacing 🚗 with a honk sound).

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
  ├─ 4. Public URL built: http://{EXECUTION_EXTERNAL_HOST}:8003/media/{media_id}
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

#### Video Playback — yt-dlp + Disk File Streaming

**Why yt-dlp?** Direct YouTube / Rumble / Vimeo playback on Cast, Roku, and Android TV requires being logged into the corresponding app on that device. Many devices lack the YouTube app entirely (generic Cast sticks, some Roku models). yt-dlp bypasses all of this by extracting a direct, publicly-accessible MP4 stream URL and downloading it to disk, where the execution service streams it directly.

**Pipeline (from `handlers/video.py`):**

```
User request → /execute/media/play (VideoPlayRequest)
  │
  ├─ 1. URL resolution
  │      If query is already a YouTube/Rumble/Vimeo URL → use it directly
  │      Otherwise → run: yt-dlp --dump-json --no-download "ytsearch1:{query}"
  │                        to find the top YouTube result URL
  │
  ├─ 2. yt-dlp download
  │      Format preference: best[ext=mp4][vcodec^=avc1][acodec^=mp4a]
  │         (H.264 + AAC = broadest Cast/Roku hardware decoder compatibility)
  │      Falls back to: best[ext=mp4] → best (any container)
  │      --merge-output-format mp4 ensures a single clean .mp4 output
  │      Saved to disk: {TEMP_MEDIA_DIR}/{media_id}.mp4
  │         TEMP_MEDIA_DIR is set via config (volume-mounted at /data/media/)
  │         media_id = "vid-{8 hex chars}"  (e.g. "vid-7d9c3e1a")
  │
  ├─ 3. Title retrieval
  │      Second yt-dlp --dump-json call to get the human-readable video title
  │      (used in the ExecutionResult message sent back to the user)
  │
  ├─ 4. Public URL built: http://{EXECUTION_EXTERNAL_HOST}:8003/media/{media_id}
  │
  ├─ 5. Device power-on
  │      If entity state is "off" → call media_player.turn_on, wait 2s
  │
  └─ 6. Cast the URL
         HA service: media_player.play_media
           media_content_id = the local MP4 URL
           media_content_type = "video/mp4"
```

**`/media/{media_id}` endpoint behaviour for video:**
- Falls through cache miss → checks disk: `{TEMP_VIDEO_DIR}/{media_id}.mp4`.
- Serves via **`FileResponse`** with `Accept-Ranges: bytes` header enabled — this is critical. Streaming players (Cast, Roku) send range requests (`bytes=0-`) to seek and buffer. Without `Accept-Ranges`, playback may stall or fail immediately.

**Codec choice rationale:** H.264 (`avc1`) + AAC (`mp4a`) is the lowest common denominator across all Cast, Roku, and Android TV hardware decoders. yt-dlp's format selector prioritizes this explicitly.

**Supported sources:** YouTube, YouTube Shorts, Rumble, Vimeo (any source yt-dlp supports — the handler is source-agnostic via URL pattern detection).

---

#### Summary: Two Storage Strategies

| | TTS Audio | Video |
|---|---|---|
| **Generated by** | Kokoro ONNX (local, offline) | yt-dlp CLI download |
| **Stored as** | RAM (`TEMP_AUDIO_CACHE` dict) | Disk file (`/data/media/*.mp4`) |
| **Served via** | `Response(bytes, media_type="audio/wav")` | `FileResponse` with `Accept-Ranges` |
| **URL pattern** | `http://{host}:8003/media/tts-{id}` | `http://{host}:8003/media/vid-{id}` |
| **Lifetime** | Process lifetime (ephemeral) | Until container restart or manual cleanup |
| **Auth bypass** | N/A (fully local) | yt-dlp bypasses YouTube/platform auth |
| **Pre-flight check** | Yes (5-retry self-ping) | No (download is synchronous before URL is built) |

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
| **Android TV** | `handlers/android_tv.py` | `androidtv_remote` | `androidtv_remote.send_command` |

Each handler includes `is_<brand>_device()` for runtime platform detection via HA entity attributes (app IDs, source lists).

**Roku Music Two-Part Pattern:** Roku music is particularly complex. The handler: 1) Calls `music_assistant.search` to resolve the track URI, 2) Launches the Music Assistant app (`782875`) on the Roku via ECP HTTP call to `http://<roku_ip>:8060/launch/782875`, 3) Delegates actual audio playback to the Music Assistant `media_player` *sibling entity* found by `find_ma_player_sibling()`.

#### Multi-Strategy Device Discovery Pipeline (`device_discovery.py`)
To find physical device IPs for ECP/direct API calls, a 7-strategy ordered pipeline runs in sequence — stopping at the first hit:

1. **Persistent Registry Cache** (aiosqlite, instant)
2. **HA Device Registry** (REST API config entries + ESPHome)
3. **HA Entity Attributes** (`ip_address`, `ip`, `host` fields)
4. **ARP Table Scan** (`arp -a`, requires host network mode)
5. **mDNS / Bonjour** (resolves `<friendly_name>.local`)
6. **SSDP Broadcast** (UDP multicast for Roku/DLNA/Chromecast)
7. **Batched Network Port Scan** (subnet scan, 30 hosts/batch, slowest fallback)

Device type port maps: Roku `:8060`, WebOS `:3000`, Samsung `:8001`, ADB `:5555`, Cast `:8009`, ESPHome `:80`, Tasmota `:80`, MQTT `:1883`.

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
4.  **Device Registry Subnet Configuration:**
    *   `device_discovery.py` currently defaults to hardcoded subnet `192.168.2.0/24`.
    *   *Fix Required:* Move `DEFAULT_SUBNET` to an environment variable (`DISCOVERY_SUBNET`) configurable per deployment.

---

## 8. Microservice Breakdown & UI Representation

The SharedLLM backend is organized into strictly isolated, purpose-built containers. Here is the current status of each service, required additions, and exactly how they manifest in the Jarvis OS 2.0 UI.

| Microservice | Current Status & Role | Required Additions / Fixes | Jarvis UI Representation |
| :--- | :--- | :--- | :--- |
| **`gateway`** | **Solid.** The central orchestrator routing requests. *Deep-Dive Finding:* It features a **Pre-Flight Capability Check** that evaluates credentials *before* dispatching. If missing, it short-circuits to Identity. It scales VRAM context via `api/ps`, uses aggressive credential sanitization, and has a global `INFERENCE_LOCK`. | **Needs Lock Manager.** Must implement the Redis async preemption logic to manage VRAM constraints on atomic jobs. | *Invisible Brain.* The UI connects to it via `/v1/chat` and `/ws`. It drives all chat interfaces. |
| **`execution`** | **Greatly Expanded.** Houses 35+ tool handlers. *Deep-Dive Finding (Major):* A fully new **TV Brand Handler** architecture has been introduced with dedicated files for `roku.py`, `samsung.py`, `webos.py`, and `android_tv.py`, each with brand-specific transport command maps and platform detection logic. Roku music now uses a two-part ECP + Music Assistant sibling-delegation pattern. A **multi-strategy Device Discovery Pipeline** (`device_discovery.py`) and persistent **SQLite Device Registry** (`device_registry.py`) have been added, discovering device IPs via 7 ordered strategies (cache → HA registry → entity attrs → ARP → mDNS → SSDP → network scan). Credentials are now resolved from Identity at runtime — `.env` is seed-only. | Subnet (`DEFAULT_SUBNET`) is hardcoded; move to env var. | **Capability Widgets.** Powers Media, Timer, Notes, and Smart Home toggle widgets. |
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

### 10.5 Personal Calendar (`/calendar`)
*   **Target Audience:** Standard Users
*   **Purpose:** A unified, user-specific view of all upcoming events, tasks, and chores.
*   **Data Aggregation:** This page actively filters data. It pulls from Nextcloud (CalDAV) and Skylight (Chores), but **only displays items assigned to or belonging to the currently authenticated user**. It strips out other family members' noise to provide a focused daily agenda.
*   **Daily Agenda View:**
    *   *Type:* Vertical Timeline / List.
    *   *Controls:* Chronological flow of the day. Integrates both hard calendar events (Nextcloud) and floating daily tasks (Skylight).
*   **Month Grid:**
    *   *Type:* Standard 30-day Calendar Grid.
    *   *Controls:* Color-coded dots indicate event density. Swiping left/right navigates months.

### 10.6 Raven Ops Panel (`/admin/ops`)
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

### 10.7 Dynamic Integrations Config (`/admin/integrations`)
*   **Target Audience:** Admins Only
*   **Purpose:** Configure backend integrations (HA, Nextcloud, GitHub) without touching `.env` files.
*   **Dynamic Forms:**
    *   *Type:* Auto-generated Form fields based on JSON Schema.
    *   *Controls:* Text inputs for URLs, secure password masking fields for Tokens. Includes a `[Test Connection]` button (calls `/api/auth/test-connection` in Identity) and a `[Save]` button to persist encrypted credentials into `identity.db`.

### 10.8 Admin User Management (`/admin/users`)
*   **Target Audience:** Admins Only (requires `is_admin=True`)
*   **Purpose:** Securely manage household users, Voice ID assignments, and third-party credentials.
*   **User Roster:**
    *   *Type:* Data Table / List.
    *   *Controls:* Lists all active Jarvis OS users. Indicates admin status and which third-party tokens are currently bound to their profile.
*   **External Import Wizard:**
    *   *Type:* Step-by-step Modal.
    *   *Controls:* Three large buttons for the source integration: `[Import from Nextcloud]`, `[Import from Home Assistant]`, `[Import from Skylight]`. 
    *   *Action:* The backend queries the selected service's API, returns a list of discovered users, and allows the admin to select which ones to batch-create inside Jarvis OS. It automatically links their source IDs to their new Jarvis profile.

### 10.9 Emoji Sound Manager (`/admin/sounds`)
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
