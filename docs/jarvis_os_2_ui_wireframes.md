# Jarvis OS 2.0: UI Content Design & Wireframes

> [!TIP]
> **For the full architectural breakdown, microservice specifications, and API endpoints**, see the companion document: `docs/jarvis_os_2_master_guide.md`.

This document outlines the content design, visual hierarchy, and component wireframing for the Jarvis OS 2.0 React/Ionic frontend. It translates the backend capabilities into tangible, responsive UI elements utilizing the "Neon Glass" aesthetic.

---

## 1. The "Neon Glass" Aesthetic System
The design language is strictly modern, dark-mode, and highly interactive.
*   **Base Canvas:** Deep, rich dark backgrounds (e.g., `#0F172A` Slate 900 to `#020617` Slate 950).
*   **Glassmorphism:** Cards use semi-transparent backgrounds with heavy background blur (`backdrop-blur-xl`), subtle white borders (`border-white/10`), and soft drop shadows.
*   **Neon Accents:** Widgets glow with specific HSL colors based on their domain:
    *   *Media/Entertainment:* Cyan (`#06b6d4`)
    *   *Chores/Skylight:* Emerald (`#10b981`)
    *   *Notes/Memory:* Amber (`#f59e0b`)
    *   *Security/Alarms:* Rose (`#f43f5e`)
*   **Typography:** Modern sans-serif (e.g., *Inter* or *Outfit*) for extreme legibility on wall-mounted tablets.
*   **Micro-animations:** Hover states slightly lift cards; active timers pulse; audio visualizers react to TTS.

---

## 2. Core Layout & Routing (Mobile-First)

The OS utilizes a Fluid Grid that gracefully scales from an Android smartphone to a 10-inch wall-mounted tablet.

### 2.1 The Global Shell
```text
+-------------------------------------------------------------+
|  [User Avatar]    Halo Banner: "Living Room"      [Settings]|
+-------------------------------------------------------------+
|                                                             |
|                    (Active Overlay Area)                    |
|                [Voice Assistant Visualizer]                 |
|                                                             |
|=============================================================|
|                                                             |
|                   MAIN CAPABILITY GRID                      |
|                 (Dynamic Masonry Layout)                    |
|                                                             |
|=============================================================|
|                     BOTTOM NAVIGATION                       |
|   [ Home ]   [ Chat/Inbox ]   [ Media ]   [ Raven Ops ]     |
+-------------------------------------------------------------+
```

### 2.2 Routes
*   `/` (Home Dashboard)
*   `/chat` (Native Nextcloud Talk Client)
*   `/media` (Full-screen MASS/ABS browser)
*   `/remote` (Universal Remote Control Panel — hidden if user has zero assigned devices)
*   `/intercom` (Two-Way Voice Intercom System)
*   `/chores` (Chore Management & Rewards Dashboard — hidden if Skylight not enabled)
*   `/calendar` (Personal Calendar — CalDAV + Skylight unified view)
*   `/settings/integrations` (Personal Integration Config — Nextcloud, Skylight, GitHub, etc.)
*   `/admin/ops` (Raven Operations Panel & Control Plane — **Admin only**)
*   `/admin/integrations` (System Integration Config — HA, MQTT, LLM, SearXNG — **Admin only**)
*   `/admin/users` (User Management & Import Wizard — **Admin only**)
*   `/admin/groups` (Group Manager & Pattern Customization — **Admin only**)
*   `/admin/monitor` (Device Telemetry & Insights Dashboard — **Admin only**)
*   `/admin/sounds` (Emoji Sound Manager — **Admin only**)

---

## 3. Widget Wireframes & Content Design

Widgets are designed to be "auto-mounted" via the Zustand store whenever the backend fires a WebSocket capability event.

### 3.1 The "Halo" Banner (Presence)
**State:** Persistent at the top of the Home route.
**Content:** 
*   Icon: Glowing Location Pin.
*   Text: "You are in the **Living Room**."
*   Action: Swipe left/right to manually override and view widgets for other rooms (Kitchen, Master Bath).

### 3.2 The Smart Inbox (Comms)
**State:** Pinned to top of grid or accessed via Bottom Nav.
**Content:**
*   Header: "Recent Commmunications"
*   List Items:
    *   🎙️ *Voice Note from Mom (2 mins ago)*
    *   💬 *Jarvis: The garage is left open.*
*   Action: Tapping a card expands the widget into the **Full-Screen Chat Client** for IRC-style games and Bible Trivia.

### 3.3 Active Media Widget (Entertainment)
**State:** Auto-mounts when MASS or ABS is active.
**Content (Card):**
*   Left: Vibrant, high-res Album/Book Cover Art.
*   Right: Title, Artist/Author.
*   Bottom: Interactive progress bar (draggable) and standard transport controls (Prev, Play/Pause, Next).
*   *Neon Accent:* Cyan glow.

### 3.4 "Continue Reading" Widget (Audiobookshelf)
**State:** Pinned when an unfinished audiobook exists in context.
**Content:**
*   Visual: Apple Watch-style concentric progress ring circling the book cover.
*   Text: "3h 15m remaining"
*   Action: One-tap instantly resumes playback on the local room's speaker.

### 3.5 Ambient Countdown Timer
**State:** Auto-mounts when a timer is triggered.
**Content:**
*   Visual: Large, dynamic glowing circle that slowly depletes.
*   Text: Large digital countdown (e.g., `04:59`).
*   Action: Long-press to cancel. Tapping exposes "Add 1 min" or "Pause" buttons.

### 3.6 Energy Insights Widget
**State:** Pinned.
**Content:**
*   Visual: A smooth, sweeping spline chart showing KwH over the last 24 hours.
*   Text: LLM-generated insight (e.g., *"Solar is currently covering all active loads. Phantom draw is low."*)
*   Action: Tap to view individual smart plug breakdowns.

### 3.7 Raven Ops Panel (Admin Center)
**State:** Accessed via Bottom Nav.
**Content:**
*   Header: "Autonomous Operations"
*   Timeline View:
    *   🟢 `[14:02]` AST Parsed `ha_client.py`
    *   🟡 `[14:05]` Fuzzy Patched Line 42
    *   🔵 `[14:10]` Commit Ready: "Fixed Auth Bug" -> **[Review Diff]** button.
*   Control Plane Header: Live Docker status dots with one-tap **[Restart]** icons.

### 3.8 Universal Remote Panel (`/remote`)
**State:** Accessed via Bottom Nav or dynamic entity card tap.
**Content & Visual Layout:**
*   **Media Target Sheet (Top):** Horizontal scrolling list of glowing cyan cards representing active media playback hardware (e.g., *Living Room TV (Roku)*, *Master Bed (WebOS)*, *Family Room (Samsung)*).
*   **Aesthetic Branding Accents:** The UI adapts its layout and brand logos dynamically depending on the selected hardware type (e.g., Roku External Control Protocol indicators, LG WebOS app selectors, or Samsung Tizen controls).
*   **Unified Directional Pad:** A prominent, frosted-glass circular control surface (`backdrop-blur-xl` + soft drop shadow) containing:
    *   Arrow directions (Up, Down, Left, Right) with a central glowing **[OK]** button.
    *   Symmetrical surrounding secondary control keys: **[Back]**, **[Home]**, **[Menu]**, **[Option]**.
*   **Quick-Access Action Strips:**
    *   *Volume Control:* vertical slider or side-by-side floating action buttons (+ / - / Mute).
    *   *Channel Controls:* dynamic scroll sheet or numeric overlays (+ / - / Source selection list).
    *   *Power Panel:* Dedicated glowing Rose toggle (`#f43f5e`) that resolves target power state (`turn_on` / `turn_off`) directly.

### 3.9 Two-Way Intercom Panel (`/intercom`)
**State:** Accessed via `/intercom` route.
**Content & Visual Layout:**
*   **Intercom Target Matrix (Left Column / Grid):** List of available household communication zones (e.g., *Kitchen Tablet*, *Bedroom Wall Pad*, *Living Room Display*, *Broadcast All*). Each target has a small presence indicator dot (glowing Green if a user was recently seen by ESPresense).
*   **Calling / Streaming Portal (Center Stage):**
    *   *WebRTC Connection State:* Spinner indicating `Connecting` or `Negotiating` transitioning to a neon green `LIVE` connection state banner.
    *   *Voice Audio Visualizer:* A premium CSS-animated spline waveform that pulses and glows in real time reflecting outgoing mic input or incoming streams.
    *   *Direct Controls:* Symmetrical circular buttons: Rose **[End Call]** and Amber **[Mute Microphone]**.
*   **Text/One-Way Overlay fallback:** If the target device lacks audio input (e.g., a TV or basic speaker), the UI shifts to **One-Way Broadcast Mode**. Features an interactive text field that lets the user type a text announcement which is dispatched via Kokoro TTS, alongside a checkbox toggle to *Push Visual Banner Overlay* (which displays a message overlay on the target display).

### 3.10 Group Manager Dashboard (`/admin/groups`)
**State:** Accessed via `/admin/groups` route (Admin only).
**Content & Visual Layout:**
*   **Logical Groups Creator (Left Column):** Creation sheets for Media Groups and Light Clusters. Includes fuzzy-search filters to select and assign Home Assistant entities dynamically.
*   **Drag-and-Drop Mapping Canvas (Center):** Interactive grid where admins can easily drag lights/speakers to assign them to logical collections (e.g., dragging 6 spotlights to map the `Kitchen Cluster`).
*   **Light Pattern Sequence Editor (Right Column):**
    *   *Step Designer:* Ordered list of pattern steps. Admins can click a step to customize position ordering, RGB color mapping (`colorpicker`), transition speeds, and brightness variables.
    *   *Live Pattern Canvas Preview:* A horizontal bar containing active preview indicators that loop and render the custom lighting pattern sequence visually on screen in real time using CSS transitions, before writing it to the SQLite db.

### 3.11 Device Telemetry Dashboard (`/admin/monitor`)
**State:** Accessed via `/admin/monitor` route (Admin only).
**Content & Visual Layout:**
*   **System Telemetry Stream (Grid Layout):** Cards showing real-time connectivity status, peak power usage, and uptime graphs for enrolled hardware.
*   **Dynamic Energy Sparklines:** Miniature chart overlays showing historical power draws (KwH) per device.
*   **LLM Learned Insights Feed:** A timeline panel showing autonomous lessons parsed by the Raven RAG engine (e.g., *"Living Room TV was left in standby drawing 15W for 6 hours. NightModeRequest has been optimized to force complete power cuts at 11:30 PM."*).

### 3.12 Chore & Rewards Dashboard (`/chores`)
**State:** Dynamically mounts in the Shell Nav if the Skylight integration is enabled for the active profile.
**Content & Visual Layout:**
*   **Family Scoreboard Panel (Top Banner):**
    *   Horizontal scrolling list of family member avatar cards. Each avatar is surrounded by an active **Emerald daily chore progress ring** (`#10b981`) and a glowing star indicator displaying their accumulated **Star Points balance** (e.g., `⭐ 120 XP`).
*   **Daily Chores Grid (Left Panel / Center Stage):**
    *   Responsive card grid showcasing assigned daily tasks (e.g., *Make Bed*, *Feed Dogs*, *Empty Dishwasher*, *Clean Room*).
    *   **Aesthetic Controls:** Cards are large, touch-friendly, and glow Cyan (`#06b6d4`) when pending. Tap-and-hold (with micro-pulsing feedback animation) or direct checkmarks triggers an automated checkoff sequence.
    *   **Complete Animation:** Completed chore cards slide to a dedicated *Completed* tab, turning solid Emerald with a pleasant chime and haptic feedback.
*   **Gamified Reward Vault (Right Panel):**
    *   Grid of redeemable items configured by parents (e.g., *1 Hour Video Games (50★)*, *Weekend Sleep-in (100★)*, *Ice Cream Treat (30★)*).
    *   **Action Flow:** Tapping a reward card prompts a frosted glass modal requesting parent verification (Admin PIN or biometric face unlock prompt) to instantly authorize and log the redemption.

---

## 4. Interaction Modals

### 4.1 Voice Assistant Overlay
When the user says "Jarvis", the entire UI blurs (`backdrop-blur-3xl`). A central, dynamic audio visualizer appears, reacting to the user's voice input, followed by Kokoro's TTS output.

### 4.2 Security Override Challenge
If the LLM attempts to unlock a door, a stark red modal drops down:
*   Header: "SECURITY OVERRIDE REQUIRED"
*   Text: "Jarvis requested to unlock the Front Door. You must authenticate."
*   Input: Admin PIN pad or biometric prompt.

---

## 5. Native Android Mobile UI Enhancements

To deliver a premium mobile experience, the Capacitor-wrapped web layout adapts dynamically when running on native Android devices.

### 5.1 Real-Time Proximity & Proximity Tracking Panel
**State:** Located inside `/settings` or accessible via user profile toggle.
**Content & Visual Layout:**
*   **Proximity Tracking Switch:** Custom HSL Amber slider showing active status (*"Sharing precise background GPS"* vs *"Location paused"*).
*   **Adaptive Sparkline Interval Visualizer:** Shows the current battery consumption profile based on movement velocity (e.g. renders a glowing green battery icon and states *"Low power sleep mode active. Geofence reporting locked"* or a cyan motion wave showing *"Active road tracking. Sending telemetry updates every 30 seconds"*).

### 5.2 Biometric Lock & PIN Prompt Integration
**State:** Auto-mounts when attempting to access security logs, admin control settings, or executing override tasks.
**Content & Visual Layout:**
*   Instead of rendering a standard web form input, the UI instantly triggers the Android OS native biometric prompt modal.
*   **Visual Fallback UI:** A beautiful glowing fingerprint glyph with backdrop-blur. Features an **[Authenticate via Fingerprint/Face ID]** button or a subtle secondary link to **[Use Master PIN Code]** if biometric recognition fails.

### 5.3 NFC Tag Macro Programmer UI
**State:** Located under `/admin/integrations/nfc`.
**Content & Visual Layout:**
*   **Aesthetic Configuration Canvas:** Minimalist glowing cards representing active Jarvis action triggers (e.g., *Activate Night Mode*, *Toggle Kitchen Lights*, *Pair room speaker as dynamic intercom*).
*   **Action Flow:**
    1. Admin taps **[Program Action to NFC Sticker]**.
    2. A dynamic slide-up panel appears with an animation showing a phone scanning a glowing card, stating: *"Bring the back of your device close to the NFC sticker..."*
    3. Triggers native NFC scanning APIs to write the encrypted JSON action payload directly to the tag. Successful writes pulse with a vibrant Emerald checkbox checkmark (`#10b981`).

---

## 6. End-to-End UI & Integration Workflows

This section outlines the exact step-by-step user interactions and backend data flows for critical system actions in the Jarvis OS 2.0 interface.

### 6.1 User Creation & Integration Import Flow

This workflow defines how administrators manage family member profiles, both manually and through external provider batch imports (Nextcloud, Home Assistant, Skylight).

```text
[Manual Flow]
Admin Panel (/settings) -> [Add User] Button -> Renders Profile Creation Modal -> Inputs Name/PIN
                                                                                        |
                                      DB Sync: identity.db <- API (POST /api/users) <---+

[Import Flow]
Admin Panel (/settings) -> [Import family] Button -> Choose Source (Nextcloud / HA / Skylight)
                                                                |
    Identity API queries Provider API (Nextcloud / HA config) <-+
                                |
    UI displays "Mapping Grid" (Merge existing / Match entries) -> Tap [Confirm Import]
                                                                            |
                            Profiles created in bulk with tokens resolved --+
```

#### Step-by-Step UI Experience:
1. **Accessing the Portal:** The admin navigates to Settings -> User Profiles.
2. **Direct Manual Input:**
   * Tapping **[Create User Profile]** slides open a frosted glass sheet.
   * Input fields: Name, Role selection (Admin, Standard, Child), Home Room assignment (used for fallback localization), and static numeric PIN code.
   * Clicking **[Save]** triggers an instant `POST /api/users/create` request, animating a spinner before returning a "User Created Successfully" banner.
3. **Dynamic Provider Import:**
   * Tapping **[Batch Import family]** presents three prominent card nodes: Nextcloud, Home Assistant, and Skylight.
   * Selecting **[Nextcloud]** triggers a dynamic modal query displaying: *"Querying Nextcloud OCS User directories..."*
   * The UI renders a **Mapping Grid Table**:
     - *Column 1:* Nextcloud User ID (`jeremiah`).
     - *Column 2:* Matched Local Identity (Auto-matched if exact substring found).
     - *Column 3:* Import Action Toggle (Checkbox to Import vs. Ignore).
   * Selecting **[Confirm Mapping & Import]** dispatches the mapped JSON in a single batch request to `/api/identity/users/import`, which populates user profiles and returns active completion tick marks.

---

### 6.2 Music & Audiobookshelf Casting Flow

This workflow illustrates how a user casts a song or audiobook to target room players.

```text
Browse Library -> Tap Song/Book Card -> Active Player bar slides up -> Tap [Cast Output] Icon
                                                                                |
    API resolves target IP (10-strategy pipeline) <- Web Socket push triggers --+
                                |
    Fast Progressive Buffer (download_video_progressive 5MB) -> Playback initializes
                                |
    UI verification loop starts -> Verification polling verifies status on /ws/capabilities
```

#### Step-by-Step UI Experience:
1. **Selecting Content:** A user browses the Music Assistant or Audiobookshelf dashboards, tapping a song or book card.
2. **Triggering Cast:** Tapping a floating **[Play on...]** button on the cover art pops open a bottom sheet displaying room-based output targets (*Kitchen Speaker*, *Living Room TV*, *Main Floor Group*).
3. **Dynamic Discovery:**
   * Upon target selection, the UI displays a subtle pulsing cyan outline around the target name.
   * The backend executes `device_discovery.discover_device(entity_id)`, querying the 10-strategy pipeline (Cache -> HA -> HomeKit -> ARP -> SNMP -> mDNS -> SSDP -> Port Scan).
4. **Playback & Verification:**
   * Once resolved, the media server streams progressive chunks immediately.
   * The dashboard mounts the **Active Media Widget**, displaying a sweeping cyan progress ring and active play/pause/track controls.

---

### 6.3 Universal Remote Control Routing Flow

This workflow describes how the UI acts as a dynamic remote for smart TVs.

```text
Tap Media Device on Grid -> Slides open Unified Remote Card -> Resolves Target Brand & IP
                                                                        |
    Aesthetic adapts (Roku D-pad vs Tizen menu strip) <- Web Socket pushes --+
                                |
    User taps [Arrow Keys] or [Volume Slider] -> Executes HTTP ECP / Web Socket request
                                |
    Verification returns status -> UI updates glowing slider state / Verification logs
```

#### Step-by-Step UI Experience:
1. **Launching the Controller:** The user taps a TV entity icon on the Dashboard Grid or navigates to `/remote`.
2. **Dynamic UI Adaptation:** The remote panel reads the target's profiler capabilities (e.g. `brand="roku"`, `brand="webos"`). If Roku is targeted, the layout instantly displays an Amber-outlined Roku logo with a dedicated ECP directional keys panel.
3. **Command Execution:**
   * Tapping the frosted D-Pad sends instant API calls: `POST /execute/remote/keypress` with key details (`Home`, `Left`, `Select`).
   * The UI shows active micro-scale pulsing animations on the tapped button to denote low-latency network responses.

---

### 6.4 Two-Way Real-Time Intercom Flow

This workflow details how the system establishes persistent WebRTC audio channels.

```text
User holds [Talk] on Intercom tab (/intercom) -> Spawns outbound connection portal in UI
                                                                |
    LiveKit SFU allocates channel room (or Mumble Fallback) <---+
                                |
    FCM background push wakes recipient client device -> WebSocket streams active call frame
                                |
    Full duplex WebRTC Audio stream establishes -> Voice spline waveform visualizes in CSS
```

#### Step-by-Step UI Experience:
1. **Initiating the Call:** The user navigates to `/intercom` and selects a room (e.g., *Kitchen Tablet*).
2. **Establishing Connection:**
   * The user clicks **[Start Live Call]**. The UI replaces the screen frame with a sweeping blur backdrop and displays a spinning neon green circle stating: *"Negotiating WebRTC Connection..."*
   * The backend routes tokens via LiveKit SFU. An FCM push wakes up the target wall tablet, which automatically accepts the connection.
3. **Live Calling Stream:**
   * The screen lights up with a vibrant `LIVE` neon banner. A central spline wave visualizes outgoing and incoming audio.
   * Tapping **[Mute]** turns the waveform red. Tapping **[End Call]** drops the WebRTC connection, fading back to the room grid view.

---

### 6.5 Gamified Chores & Rewards Sync Flow

This workflow describes the gamified kids' experience for clearing daily tasks.

```text
Child taps Daily Chore card -> Cards plays a 3D pulse wave -> Webhook triggers physical board sync
                                                                        |
    Daily Progress Ring advances in visual gradient <- Star points balance updates +5★ --+
                                |
    Child taps Reward (1 hr Screen Time) -> Frosted overlay prompts: "Parent check"
                                |
    Parent scans finger (biometrics) or inputs PIN -> Reward authorizes -> Stars deducted
```

#### Step-by-Step UI Experience:
1. **Completing a Task:**
   * A child opens the `/chores` tab and checks off a completed task (*Feed Dogs*).
   * The card plays a 3D scale pulse, turns Emerald, and automatically slides to the *Completed* tab.
   * The child's profile card updates with a sparkling star effect, adding `+5★` to their vault balance. The outer circular progress ring advances.
2. **Redeeming a Reward:**
   * The child selects a reward (*Ice Cream (30★)*) in the vault section.
   * Tapping **[Redeem]** brings up a frosted-glass overlay stating: *"Parent confirmation required..."*
   * The parent taps their finger on the device's fingerprint scanner. Successful authentication plays a rewarding chime, deducts 30 Star Points, and displays a glowing green checkmark with the message: *"Reward Approved! Enjoy your treat!"*

---

### 6.6 Raven Autonomous Ops & Commit Review Flow

This workflow outlines how admins verify autonomous coding repairs performed by the Raven engine.

```text
Raven encounters a repair mission -> Streams reasoning timeline via PubSub to UI (/admin/ops)
                                                                        |
    Linter (Ruff) auto-checks file patches <- Auto-write sync finishes -+
                                |
    Timeline displays Commit Card -> Admin taps [Review Diff] to inspect modifications
                                |
    One-tap [Approve & Merge] button triggers -> DB logs sync -> RAG persists coding learnings
```

#### Step-by-Step UI Experience:
1. **Configuring the Mission:** The administrator navigates to `/admin/ops`. At the top of the panel, they can toggle **[Plan Mode]** (Read-Only triage) or **[Build Mode]** (Read/Write execution) before pasting the prompt.
2. **Observing the Action-Observation Loop:** The screen shows a chronological log of active Raven diagnostics streaming from Redis. Every LLM decision (Action) and environment response (Observation) is rendered natively in real-time.
3. **Reviewing Code Patches:**
   * When a file patch completes, the timeline mounts a beautifully styled **Commit Card** showing details: *"Repair complete: resolved IndentationError in timer.py"* and a glowing Cyan **[Review Diff]** button.
   * Tapping **[Review Diff]** slides open a side-by-side git diff drawer detailing code lines added (green) or removed (red).
4. **Finalizing and Persisting:**
   * The admin taps **[Approve & Merge]**. The system immediately commits the changes, runs the pytest suite, and merges to the local branch.
   * A success banner animates, and the RAG engine logs the coding summary, permanently persisting the lesson for future autonomous runs.
   * *(Optional)* Admins can tap **[Download Trajectory]** to export the `trajectory.jsonl` flight recorder file for debugging or benchmarking.

---

## 7. Admin Task Workflows (Requires `is_admin=True`)

These workflows are only accessible to users with the Admin role. Each workflow maps exact UI actions to backend API calls so both human developers and AI agents can implement them precisely.

### 7.1 Create a New User Profile (Manual)

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/users` | — | User Roster table renders |
| 2 | Tap **[+ Create User]** | — | Frosted glass slide-up sheet opens |
| 3 | Fill: Name, Role dropdown (`Admin` / `Standard` / `Child`), Home Room dropdown, 4-digit PIN | — | Client-side validation (name required, PIN 4+ digits) |
| 4 | Tap **[Save Profile]** | `POST /api/users/create` `{ name, role, home_room, pin }` | Spinner → success banner. New row animates into roster. |
| 5 | *(Optional)* Tap the new user row → **[Link Credentials]** | — | Credential binding sheet opens (see 7.3) |

**Error States:**
- Duplicate name → inline Rose error: *"A user with this name already exists."*
- PIN too short → field border turns Rose, tooltip: *"PIN must be at least 4 digits."*

---

### 7.2 Batch Import Users from External Providers

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/users` → **[Import Family]** | — | Source selection modal opens |
| 2 | Select source: `[Nextcloud]` / `[Home Assistant]` / `[Skylight]` | `GET /api/identity/users/discover?source=nextcloud` | Loading spinner → fetched user list renders |
| 3 | Review the **Mapping Grid Table**: Source ID, Auto-Matched Local Profile, Import checkbox | — | Admin toggles checkboxes per user |
| 4 | *(Optional)* Click unmatched row → assign role (Admin/Standard/Child) and Home Room | — | Inline dropdown editors |
| 5 | Tap **[Confirm Import]** | `POST /api/identity/users/import` `{ source, mappings[] }` | Batch creates profiles. Progress bar fills. Success toast with count. |

**Error States:**
- Provider unreachable → Rose banner: *"Could not connect to Nextcloud. Check credentials in Integrations."*
- Partial failure → Amber warning listing which users failed with reason.

---

### 7.3 Link Third-Party Credentials to a User

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | `/admin/users` → tap user row → **[Link Credentials]** | — | Credential sheet opens |
| 2 | Select service: `Nextcloud`, `Home Assistant`, `GitHub`, `Skylight` | — | Dynamic form fields render based on service schema |
| 3 | Fill required fields (e.g., Nextcloud: `username`, `app_password`, `server_url`) | — | Client-side validation |
| 4 | Tap **[Test Connection]** | `POST /api/auth/test-connection` `{ service, credentials }` | Green checkmark or Red X with error detail |
| 5 | Tap **[Save & Encrypt]** | `PUT /api/users/{id}/credentials` `{ service, credentials }` | Fernet-encrypted, stored in `identity.db`. Masked confirmation shown. |

---

### 7.4 Configure an Integration (Nextcloud, HA, GitHub, etc.)

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/integrations` | `GET /api/integrations/available` | Dynamic form cards render per integration |
| 2 | Fill fields (URLs, tokens, toggle switches) | — | Client validation on required fields |
| 3 | Tap **[Test Connection]** per integration card | `POST /api/auth/test-connection` `{ service, ... }` | Real-time connectivity check |
| 4 | Tap **[Save]** | `PUT /api/admin/settings` `{ key, value }` | Encrypted persistence. UI shows green lock icon. |

---

### 7.5 Set Up Chore Schedules & Rewards (Skylight)

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/chores` → toggle to **Parent Mode** (biometric/PIN) | — | Admin editing controls unlock |
| 2 | Tap **[+ Add Chore]** | — | Chore creation sheet slides up |
| 3 | Fill: Chore name, icon picker, assign to child(ren), star value (e.g., `5★`), recurrence (daily/weekly) | — | Client validation |
| 4 | Tap **[Save Chore]** | `POST /api/integrations/skylight/chores` `{ name, assignees[], stars, recurrence }` | New chore card appears in grid. Syncs to physical Skylight board. |
| 5 | Tap **[Manage Rewards]** → **[+ Add Reward]** | — | Reward creation sheet |
| 6 | Fill: Reward name, star cost, icon, optional parent-approval toggle | `POST /api/integrations/skylight/rewards` | Reward added to vault |

---

### 7.6 Create Media Device Groups

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/groups` | `GET /api/groups/media` | Existing groups list renders |
| 2 | Tap **[+ New Media Group]** | — | Group creation panel opens |
| 3 | Name the group (e.g., `Main Floor`) | — | — |
| 4 | Fuzzy-search & select member entities (e.g., `media_player.kitchen_speaker`, `media_player.living_room_tv`) | `GET /api/ha/entities?domain=media_player` | Filtered entity list with checkboxes |
| 5 | Set scope: `System` (all users) or `User` (personal) | — | — |
| 6 | Tap **[Save Group]** | `POST /api/groups/media` `{ group_id, group_name, member_entity_ids[], scope }` | Group card appears. Available as target in Announcements/Intercom. |

---

### 7.7 Create Light Clusters & Custom Patterns

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | `/admin/groups` → **Light Clusters** tab | `GET /api/groups/lights` | Existing clusters listed |
| 2 | **[+ New Cluster]** → Name it, drag `light.*` entities from the discovery list into the cluster | `GET /api/ha/entities?domain=light` | Drag-and-drop canvas |
| 3 | Tap **[Save Cluster]** | `POST /api/groups/lights` `{ cluster_id, member_entity_ids[] }` | Cluster created |
| 4 | **[+ New Pattern]** → Name it, select target cluster | — | Pattern step editor opens |
| 5 | Add steps: pick position range, RGB color picker, brightness slider, transition speed | — | Live CSS preview bar renders in real time |
| 6 | Toggle **Loop** checkbox and set transition timing | — | — |
| 7 | Tap **[Save Pattern]** | `POST /api/groups/patterns` `{ pattern_id, cluster_id, steps[], loop, transition_ms }` | Pattern saved. Available as `pattern_id` in LLM `LightControlRequest`. |

---

### 7.8 Enroll Devices in Telemetry Monitoring

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/monitor` | `GET /api/telemetry/enrolled` | Enrolled device cards render |
| 2 | Tap **[+ Enroll Device]** | `GET /api/ha/entities` | Searchable entity list |
| 3 | Select entities to monitor (smart plugs, lights, TVs, sensors) | — | Checkboxes per entity |
| 4 | Tap **[Start Monitoring]** | `POST /api/telemetry/enroll` `{ entity_ids[] }` | Devices begin telemetry collection. Cards appear with live sparklines. |
| 5 | *(Daily)* Review **LLM Insights Feed** panel | Auto-populated by nightly Raven `analysis_mission` | LLM-generated pattern summaries appear |

---

### 7.9 Map Emoji to Sound Effects

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/sounds` | `GET /execute/emoji-sounds` | Sound library grid renders |
| 2 | In the **Add New Sound** panel: pick emoji via emoji picker, enter label, drag audio file (`.mp3`/`.wav`/`.ogg`, max 5MB) | — | Client preview plays |
| 3 | Tap **[Save Mapping]** | `POST /execute/emoji-sounds/upload` (multipart) | New sound card animates into grid |
| 4 | *(Test)* Type a sentence with the emoji in the **Live Preview** text area → **[Test TTS]** | `POST /execute/announce` `{ target="browser_preview" }` | Spliced audio plays in browser `<audio>` element |

---

### 7.10 Manage Docker Services (Control Plane)

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/ops` → **Control Plane** section | `GET /control_plane/api/containers` | Container status table renders with green/red dots |
| 2 | Tap **[View Logs]** on any container | `GET /control_plane/api/containers/{name}/logs?tail=100` | Terminal-style modal shows last 100 log lines |
| 3 | Tap **[Restart]** on a crashed container | `POST /control_plane/api/restart/{container_name}` | Confirmation dialog → restart executes → status dot turns green |

---

### 7.11 Program NFC Tag Macros

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/integrations/nfc` | — | NFC macro configuration canvas |
| 2 | Select a Jarvis action from the card grid (e.g., *Activate Night Mode*, *Toggle Kitchen Lights*) | — | Action card highlights |
| 3 | Tap **[Program to NFC Sticker]** | — | Slide-up animation prompts: *"Bring device close to NFC sticker..."* |
| 4 | Hold phone against physical NFC tag | Native NFC write API encodes encrypted JSON action payload | Success: Emerald checkmark pulse. Failure: Rose error with retry option. |

---

### 7.12 Review & Approve Raven Autonomous Code Changes

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/ops` → Toggle **[Plan/Build Mode]** | — | Sets operational safety limits for the mission |
| 2 | Watch **Operations Timeline** | WebSocket subscription to `raven:events:{id}` | Live Action-Observation loop streams in |
| 3 | When a **Commit Card** appears → Tap **[Review Diff]** | `GET /api/raven/missions/{id}/diff` | Side-by-side diff drawer opens (green adds / red removes) |
| 4 | Tap **[Approve & Merge]** | `POST /api/raven/missions/{id}/approve` | Pytest runs → commit → push. RAG persists the learning. |
| 5 | *(Or)* Tap **[Reject]** | `POST /api/raven/missions/{id}/reject` | Changes discarded. Mission marked failed. |
| 6 | Tap **[Download Trajectory]** | `GET /api/raven/missions/{id}/trajectory` | Downloads the `trajectory.jsonl` JSON-lines log |

---

### 7.13 Assign Devices to Users

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | `/admin/users` → tap user row → **[Assigned Devices]** tab | `GET /api/users/{id}/devices` | Current device assignments listed |
| 2 | Tap **[+ Assign Device]** → fuzzy-search HA entities | `GET /api/ha/entities?domain=media_player` | Searchable entity list |
| 3 | Select devices → Tap **[Save Assignments]** | `PUT /api/users/{id}/devices` `{ entity_ids[] }` | Devices bound to user. `/remote` route becomes visible for this user. Intercom targets update. |

---

### 7.14 Configure Announcement Blacklist

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/admin/integrations` → **Announcements** card | `GET /api/admin/settings?prefix=announce_blacklist` | Current blacklist renders |
| 2 | Toggle switches per entity to blacklist/allow | — | Instant visual feedback |
| 3 | *(Optional)* Set time-based rules (e.g., *"Kids' Room speakers blacklisted after 8 PM"*) | — | Time picker + entity selector |
| 4 | Tap **[Save]** | `PUT /api/admin/settings` `{ key: "announce_blacklist", value: [...] }` | Blacklist persisted. `announce_all` respects it immediately. |

---

## 8. Standard User Task Workflows

These workflows are available to all authenticated users (Standard, Child, and Admin roles).

### 8.1 First Login & Device Pairing

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Open Jarvis OS in browser or Capacitor app | — | Login screen renders |
| 2 | Enter Name + PIN (or use biometric on mobile) | `POST /api/auth/login` `{ name, pin }` | JWT session token issued. Zustand store hydrates user profile. |
| 3 | *(Mobile only)* App prompts: *"Allow Jarvis to use your microphone?"* → Accept | — | Porcupine wake-word engine initializes |
| 4 | *(Mobile only)* App prompts: *"Allow background location?"* → Accept | — | GPS daemon starts. Location syncs to Gateway. |
| 5 | *(First time)* Halo Banner shows fallback: *"You are at Home"* until ESPresense detects room | — | BLE presence resolves within ~30s |

---

### 8.2 Ask Jarvis a Voice Command

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Say *"Jarvis"* (or tap the mic icon) | Porcupine local wake-word match | Frosted glass overlay + audio visualizer appears |
| 2 | Speak the command (e.g., *"Play jazz in the kitchen"*) | `POST /api/chat` `{ message, user_context }` | Gateway routes: FastPath → Librarian → Raven (tiered) |
| 3 | Gateway resolves intent → dispatches tool | e.g., `POST /execute/media/play` `{ query: "jazz", device_name: "kitchen" }` | Execution handler runs |
| 4 | Kokoro TTS responds audibly (e.g., *"Playing jazz on the kitchen speaker"*) | `/execute/tts` → announce to user's current room speaker | Audio plays. Active Media Widget mounts on dashboard. |

---

### 8.3 Set a Timer or Alarm

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Voice: *"Set a timer for 10 minutes"* — or tap **[+ Timer]** on dashboard | `POST /execute/timer` `{ type: "timer", duration_str: "10m" }` | — |
| 2 | Timer persisted in Redis under `timer:{user_id}:{timer_id}` | — | **Ambient Countdown Widget** auto-mounts on dashboard |
| 3 | Widget shows glowing ring depleting in real time | WebSocket push updates from automation scheduler | — |
| 4 | *(Optional)* Tap widget → **[+1 Min]** or **[Pause]** or **[Cancel]** | `POST /execute/timer` `{ action: "extend" / "pause" / "cancel" }` | Timer adjusts |
| 5 | Timer expires → audio alert routes to user's current room (ESPresense) | Automation → `/execute/trigger` → Kokoro TTS → announce | Chime plays. Widget auto-dismisses. |

---

### 8.4 Cast Music or Video to a Device

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/media` → Browse or search | `POST /execute/media/play` `{ query, media_type }` | Music Assistant resolves content |
| 2 | Tap song/album/book card | — | **Now Playing Hero** renders with cover art |
| 3 | Tap **[Cast to...]** → select target device or group from bottom sheet | — | Target resolves via 10-strategy device discovery |
| 4 | Playback begins. Transport controls (Play/Pause/Skip/Shuffle) are live. | `POST /execute/media/transport` `{ command }` | `verify_playback()` confirms state within 10s |
| 5 | Adjust volume via slider | `POST /execute/media/transport` `{ command: "volume_set", volume_level }` | Volume updates on physical device |

---

### 8.5 Use the Intercom (Quick Clip)

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/intercom` | — | Contact grid renders with ESPresense presence dots |
| 2 | Tap a room or user card to select target | — | Target highlighted |
| 3 | Press and hold **[Talk]** button → speak | `MediaRecorder.start()` | Red recording ring animates. Max 30s. |
| 4 | Release **[Talk]** | `POST /execute/intercom/send` (multipart: `audio_file` + `target_entity_ids[]`) | WAV dispatched to target speakers |
| 5 | Target device plays audio automatically | WebSocket push on recipient's UI | Recipient sees toast: *"📣 [Name] from [Room]"* with **[Reply]** button |

---

### 8.6 Complete a Chore & Redeem Rewards (Child)

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Open `/chores` (auto-mounted if Skylight enabled) | `GET /api/integrations/skylight/chores?user={id}` | Daily chore grid renders |
| 2 | Tap-and-hold a chore card (e.g., *Make Bed*) | `POST /api/integrations/skylight/chores/{id}/complete` | Card pulses → turns Emerald → slides to Completed tab |
| 3 | Progress ring advances. Star balance updates (`+5★`) | Webhook syncs to physical Skylight board | Board LED turns green |
| 4 | Tap a reward in the Vault (e.g., *1 Hour Screen Time — 50★*) → **[Redeem]** | — | Frosted overlay: *"Parent confirmation required"* |
| 5 | Parent scans fingerprint or enters PIN | `POST /api/integrations/skylight/rewards/{id}/redeem` `{ user_id, parent_auth }` | Stars deducted. Emerald success chime. |

---

### 8.7 Create & Sync a Note

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Dashboard → tap **[+ New Note]** FAB | — | Full-screen markdown editor opens |
| 2 | Type or dictate note content | — | Auto-save debounce (2s after last keystroke) |
| 3 | Tap **[Save]** | `POST /execute/note` `{ action: "create", title, content }` | Note saved to Nextcloud WebDAV |
| 4 | *(Auto)* If note contains temporal data (*"Dentist Tuesday 4pm"*), LLM extracts and creates calendar event | `POST /execute/calendar` `{ action: "add", summary, start_time }` | Event appears in `/calendar` view |
| 5 | *(Auto)* Note indexed into RAG via `sync_rag` | Background pipeline → ChromaDB | Jarvis can now recall this note in future conversations |

---

### 8.8 View Personal Calendar

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/calendar` | `GET /execute/calendar` `{ action: "list", user_context }` | Filtered to current user only |
| 2 | **Daily Agenda** view shows chronological timeline (calendar events + Skylight chores) | — | Color-coded: Cyan for events, Emerald for chores |
| 3 | Swipe to **Month Grid** view | — | Dots indicate event density per day |
| 4 | Tap a day → drill into that day's agenda | — | Detail view with edit/delete options (admin or event owner only) |

---

### 8.9 Link Personal Integrations (Self-Service)

Each user manages their own third-party accounts independently — no admin involvement required.

| Step | UI Action | Backend Call | Result |
| :--- | :--- | :--- | :--- |
| 1 | Navigate to `/settings/integrations` | `GET /api/integrations/available?scope=personal` | Personal integration cards render (Nextcloud, Skylight, GitHub, CalDAV) |
| 2 | Tap an integration card (e.g., **Nextcloud**) | — | Dynamic form opens: `Server URL`, `Username`, `App Password` |
| 3 | Fill fields → Tap **[Test Connection]** | `POST /api/auth/test-connection` `{ service: "nextcloud", credentials }` | Green checkmark or Red X with error |
| 4 | Tap **[Save & Encrypt]** | `PUT /api/users/{self}/credentials` `{ service, credentials }` | Fernet-encrypted. Isolated to this user only. |
| 5 | *(Result)* Notes, Calendar, Talk, and Skylight features now use this user's own account | — | `/chat` shows this user's Talk rooms. `/calendar` shows this user's CalDAV events. |

**Important:** Personal credentials are cryptographically isolated per-user. Admins can see *that* a credential is linked (e.g., "Nextcloud: ✅ Connected") but cannot view the decrypted token value.

---

### Role-Based Navigation Visibility Summary

| Route | Admin | Standard | Child |
| :--- | :--- | :--- | :--- |
| `/` (Home) | ✅ | ✅ | ✅ |
| `/chat` | ✅ | ✅ | ✅ |
| `/media` | ✅ | ✅ | ✅ (with content filters) |
| `/remote` | ✅ (if devices assigned) | ✅ (if devices assigned) | ❌ |
| `/intercom` | ✅ | ✅ | ✅ |
| `/chores` | ✅ (if Skylight enabled) | ✅ (if Skylight enabled) | ✅ (if Skylight enabled) |
| `/calendar` | ✅ | ✅ | ❌ |
| `/settings/integrations` | ✅ | ✅ | ❌ |
| `/admin/ops` | ✅ | ❌ | ❌ |
| `/admin/integrations` | ✅ | ❌ | ❌ |
| `/admin/users` | ✅ | ❌ | ❌ |
| `/admin/groups` | ✅ | ❌ | ❌ |
| `/admin/monitor` | ✅ | ❌ | ❌ |
| `/admin/sounds` | ✅ | ❌ | ❌ |

---

## 9. Documentation Audit Findings & Cross-Reference Index

This appendix documents inconsistencies discovered during a comprehensive review of both the Master Guide and this UI Wireframes document, along with their resolutions.

### 9.1 Issues Found & Resolved

| # | Document | Issue | Resolution |
| :--- | :--- | :--- | :--- |
| 1 | Master Guide §6 | **Duplicate section numbering:** `6.6` was used for both *Composite Macro-Actions* and *Browser & Web Operations* | Renumbered Browser & Web Operations to `6.8` |
| 2 | Master Guide §8 | **Stale data in Microservices Table:** `execution` row still referenced "7-strategy pipeline" and "hardcoded subnet" | Updated to "10-strategy pipeline" and "Subnet detection is now dynamic" |
| 3 | Master Guide §3 | **Section ordering gap:** §3.13 (Power) → §3.16 (Intercom) → §3.17 (Android App) → §3.14 (Grouping). Sections 3.14 and 3.15 appear after 3.17 | Noted: content is correct but numbering reflects insertion order. Future refactor should reorder to §3.13 → §3.14 → §3.15 → §3.16 → §3.17 |
| 4 | Master Guide §7.4 | **Marked as resolved** but the resolution text was missing context | Added full explanation of dynamic `/proc/net/route` inspection and env var overrides |
| 5 | Master Guide §6.9 | **Section jump:** §6.6 → §6.9 with no §6.7 or §6.8 in between | §6.7 is Raven Autonomous Ops (correctly numbered). §6.8 now assigned to Browser & Web Ops. |
| 6 | UI Wireframes §2.2 | **Missing routes:** `/chores`, `/remote`, `/intercom`, `/admin/groups`, `/admin/monitor` were not listed | Added all missing routes to the route registry |
| 7 | Both Documents | **Missing task workflows:** No step-by-step admin or user task guidance existed | Added §7 (14 admin workflows) and §8 (8 user workflows) to this document |

### 9.2 Cross-Reference: UI Route → Master Guide Section → Backend Endpoint

This table maps every frontend route to its architectural specification and primary API endpoints, enabling quick navigation for both human developers and AI agents.

| UI Route | Wireframe Section | Master Guide Section | Primary Backend Endpoints |
| :--- | :--- | :--- | :--- |
| `/` | §3.1–3.6 (Widgets) | §3.1–3.13 | `/ws/capabilities`, `/execute/*` |
| `/chat` | §3.2 (Smart Inbox) | §3.4 (Nextcloud Talk) | `/execute/talk`, `/api/chat` |
| `/media` | §3.3–3.4 (Media/ABS) | §3.1 (MASS), §3.2 (ABS) | `/execute/media/play`, `/execute/media/transport` |
| `/remote` | §3.8 (Universal Remote) | §10.5 (Remote Control) | `/execute/media/transport`, `/execute/remote/keypress` |
| `/intercom` | §3.9 (Intercom Panel) | §3.16, §10.5b | `/execute/intercom/send`, LiveKit/Mumble SFU |
| `/chores` | §3.12 (Chore Dashboard) | §3.7 (Skylight) | `/api/integrations/skylight/*` |
| `/calendar` | — | §10.6 | `/execute/calendar` |
| `/settings/integrations` | §8.9 (Personal Integrations) | §3.12 (Identity Vault) | `/api/integrations/available?scope=personal`, `/api/users/{self}/credentials` |
| `/admin/ops` | §3.7 (Raven Ops) | §10.7, §3.3 | `/api/raven/missions/*`, `/control_plane/*` |
| `/admin/integrations` | — | §10.8 | `/api/integrations/available`, `/api/admin/settings` |
| `/admin/groups` | §3.10 (Group Manager) | §3.14 (Grouping) | `/api/groups/media`, `/api/groups/lights`, `/api/groups/patterns` |
| `/admin/monitor` | §3.11 (Telemetry) | §3.15 (Telemetry) | `/api/telemetry/*` |
| `/admin/users` | — | §10.9 | `/api/users/*`, `/api/identity/users/import` |
| `/admin/sounds` | — | §10.10 | `/execute/emoji-sounds/*` |
| `/admin/integrations/nfc` | §5.3 (NFC Programmer) | §3.17 (Android App) | Native NFC APIs (no backend call) |
