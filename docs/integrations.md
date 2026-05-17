<!-- markdownlint-disable MD013 -->
# Integration Architecture Documentation

## What is an Integration?

In this system, an **Integration** is a specialized handler class responsible for
executing media commands on a specific type of device.

The architecture follows a **Router-Delegate** pattern:

1. **Router (`commands.py`)**: Receives the natural language intent (e.g., "Play
    X on Y"), resolves the target entity, and determines *which* integration
    handler to use.
2. **Factory (`IntegrationFactory`)**: Instantiates the correct handler class
    based on the device's Home Assistant `integration` attribute (e.g., `cast`,
    `music_assistant`, `roku`).
3. **Handler (The Integration)**: Executes the actual logic, handling
    device-specific quirks like power management, query formatting, or app
    launching.

This modularity ensures that adding features for one device type (like turning
on a TV before casting) doesn't complicate the logic for others (like playing
music on a smart speaker).

---

## 🔒 Integration Enforcement & Capability Mapping

As the Jarvis SOA expands, all new services and intents must adhere to **Capability-Based Routing**.

### 1. Identity Credential Requirement
Every new microservice that requires external authentication (e.g., a new "Slack" service) must:
1. Define which fields in `ResolvedCredentials` it requires.
2. If those fields don't exist, they must be added to the `User` model in `services/identity/models.py` and the `ResolvedCredentials` schema in `services/identity/schemas.py`.

### 2. Gateway Capability Map
When a new `Intent` is added to the system (via the Intent Engine or Regex), developers **MUST** update the `INTENT_CAPABILITY_MAP` in `services/gateway/main.py`.

Failure to map an intent will result in the Gateway attempting to call downstream services without validating credentials, which can lead to unstable behavior and 500 errors.

### 3. Graceful Degradation
By mapping an intent, the Gateway can automatically intercept missing credentials and guide the user to the **Identity Hub** with a persona-driven message, preventing a "crash" experience.

---

## 1. Cast Integration (`CastIntegration`)

**Target Devices**: Google Chromecast, Google Home, Nest Hub, Cast-enabled TVs
(Vizio, Sony, etc.).

### Cast: Features

* **SmartPowerSync**: The defining feature of this integration. Cast devices
    often cannot turn themselves on if they are built into a "dumb" TV or powered
    by USB.
  * **Logic**: Before playing media, the integration looks for a "physical
        TV sibling" of the Cast device.
  * **Discovery**: It searches ChromaDB for other devices in the same
        physical group (e.g., "Office") that look like a TV. If that fails, it
        tries name matching (stripping `_cast` or `_chrome` suffixes).
  * **Action**:
    * If TV is **OFF**: Sends `media_player.turn_on`, waits 2-4s for boot, then plays.
    * If TV is **ON**: Skips power commands to avoid interrupting active sessions (e.g., typically avoids "Home" pulses for video intents).
    * If TV is **Android/Deep Sleep**: Uses a specialized "Home" pulse to wake the ADB connection without power toggling, unless a video session is imminent.
* **Standard Playback**: Supports standard HASS `media_player.play_media`
    commands.

### Cast: User Guide & Voice Commands

| Feature | Natural Speech Example | What Happens |
| :--- | :--- | :--- |
| **SmartPowerSync** | "Play Brandon Lake on the Office TV" | 1. Finds sibling TV; 2. intelligently manages power/wake state; 3. Plays music. |
| **Video Intent** | "Watch Big Buck Bunny on Office TV" | **Fast Path** detects video match, extracted URL, and streams directly to Chromecast. bypasses LLM. |
| **Auto-Search Video** | "Watch a fireplace video on the Living Room TV" | **System searches YouTube** for "fireplace video", extracts the first URL, and plays it. |
| **Power Control** | "Turn on the Chromecast" | Turns on the device (and likely the TV via CEC). |

---

## 2. Music Assistant Integration (`MusicAssistantIntegration`)

**Target Devices**: All players controlled via the Music Assistant add-on (Sonos,
AirPlay, Cast, etc.).

**Key Feature**: "Smart Routing" - deeply integrates with the Router to steal
"music" commands from hardware devices. Also supports **Library Management**.

### Music Assistant: User Guide & Voice Commands

| Intent | Natural Speech Example | Internal Logic |
| :--- | :--- | :--- |
| **Music Search** | "Play some jazz on the kitchen speaker" | Cleans query to "jazz", searches MA, plays Radio/Playlist. |
| **Playlist List** | "What playlists do I have?" | **Fast Path** fetches all MA playlists and injects the list into the LLM context for a natural response. |
| **Radio List** | "List my radio stations" | Fetches favorited radio stations from Library. |
| **Smart Swap** | "Play music on the Office TV" | Router detects "Office TV" is a Cast device. **Swaps target** to `mass_office_speaker` for high-res audio. |

---

## 3. Roku Integration (`roku.py` / `media_assistant_roku.py`)

**Target Devices**: All Roku TVs and streaming players.

### Roku: Architecture

Roku uses a **two-part approach** for music playback because Roku devices do NOT
support direct URL streaming via `media_player.play_media`:

1. **ECP Launch (UI)**: Launches the **Media Assistant Channel (782875)** on the
   Roku device via HTTP POST to `http://{roku_ip}:8060/launch/782875` with params:
   - `t=a` — Audio mode (triggers rich music UI)
   - `autoplay=true` — Auto-start playback
   - `songName`, `artistName`, `albumArt` — Metadata for display

2. **MA Sibling Delegation (Audio)**: Finds the Music Assistant player entity
   that is a sibling of the Roku entity (same friendly name, has `active_queue`
   or `mass_player_type` attribute), then calls
   `music_assistant/play_media` on that entity for actual audio streaming.

### Roku: Key Functions (`services/execution/handlers/roku.py`)

| Function | Purpose |
| :--- | :--- |
| `is_roku_device()` | Detects Roku via entity_id patterns, app_id, source_list |
| `get_roku_ip()` | Discovers Roku IP via HA device registry or SSDP broadcast |
| `find_ma_player_sibling()` | Finds MA player entity by matching friendly_name + MA attributes |
| `roku_play_music()` | Orchestrates ECP launch + MA audio delegation |
| `roku_press()` / `roku_launch()` | Transport commands via HA roku domain |

### Roku: ECP Parameters

| Param | Value | Purpose |
| :--- | :--- | :--- |
| `t` | `a` (audio) / `v` (video) | Media type mode |
| `autoplay` | `true` | Auto-start playback |
| `songName` | Track title | Displayed on Roku UI |
| `artistName` | Artist name | Displayed on Roku UI |
| `albumArt` | Image URL | Album art on Roku UI |
| `u` | Stream URL | Direct URL (video only, NOT for music/library URIs) |
| `videoName` | Video title | Displayed for video |
| `videoFormat` | `mp4` / `hls` | Video format hint |

### Roku: User Guide & Voice Commands

| Intent | Natural Speech Example | What Happens |
| :--- | :--- | :--- |
| **Play Music** | "Play Brandon Lake on Gracies TV" | 1. Finds Roku IP; 2. Launches MA App via ECP; 3. Finds MA player sibling; 4. Delegates audio to MA |
| **App Launch** | "Open Netflix on the Bedroom TV" | Sends ECP launch command for specific app ID |
| **Navigation** | "Go down", "Select", "Go Home" | Sends standard remote control codes via ECP |
| **Video** | "Watch a fireplace video on Gracies TV" | yt-dlp resolves URL → local stream → ECP launch with `t=v` |

---

## 4. Android TV Integration (`AndroidTVIntegration`)

**Target Devices**: Nvidia Shield, Chromecast with Google TV, Sony/TCL Android
TVs.

### Android TV: Features

* **ADB-Based Remote**: Uses Home Assistant's `androidtv.adb_command` or
  standard media player services for deep interaction.
* **App Orchestration**: Launches specific applications via their activity
  intent or package name.

| Intent | Natural Speech Example | What Happens |
| :--- | :--- | :--- |
| **App Launch** | "Launch YouTube on the Shield" | Dispatches `media_player.select_source` for YouTube. |
| **Navigation** | "Scroll left", "Press OK" | Sends ADB or standard navigation commands. |

---

## 5. Hardware & Lighting (`HardwareIntegration`)

**Target Devices**: All Home Assistant lights, switches, and fans.

### Hardware: Features

* **Advanced Lighting**: Supports setting specific HSL colors and
  absolute/relative brightness levels.
* **Capability Routing**: Automatically detects if a device supports `set_color`
  or `set_brightness` before attempting the call.

| Feature | Natural Speech Example | What Happens |
| :--- | :--- | :--- |
| **Color Control** | "Make the office light red" | Sends `light.turn_on` with `color_name: red`. |
| **Brightness** | "Set light to 50%", "Dim it" | Adjusts `brightness_pct` absolutely or relatively. |
| **Toggling** | "Toggle the desk lamp" | Inverts the current logical state. |
