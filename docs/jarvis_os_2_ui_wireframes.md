# Jarvis OS 2.0: UI Content Design & Wireframes

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
*   `/remote` (Universal Remote Control Panel)
*   `/intercom` (Two-Way Voice Intercom System)
*   `/chores` (Chore Management & Rewards Dashboard)
*   `/admin/ops` (Raven Operations Panel & Control Plane)
*   `/admin/integrations` (Dynamic Plugin Configs)
*   `/admin/groups` (Group Manager & Pattern Customization)
*   `/admin/monitor` (Device Telemetry & Insights Dashboard)

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
1. **Observing the Loop:** An administrator accesses `/admin/ops`. The screen shows a chronological log of active Raven diagnostics.
2. **Reviewing Code Patches:**
   * When a file patch completes, the timeline mounts a beautifully styled **Commit Card** showing details: *"Repair complete: resolved IndentationError in timer.py"* and a glowing Cyan **[Review Diff]** button.
   * Tapping **[Review Diff]** slides open a side-by-side git diff drawer detailing code lines added (green) or removed (red).
3. **Finalizing and Persisting:**
   * The admin taps **[Approve & Merge]**. The system immediately commits the changes, runs the pytest suite, and merges to the local branch.
   * A success banner animates, and the RAG engine logs the coding summary, permanently persisting the lesson for future autonomous runs.
