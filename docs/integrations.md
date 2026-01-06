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

## 3. Roku Integration (`StandardIntegration` / `RokuIntegration`)

**Target Devices**: All Roku TVs and streaming players.

### Roku: Features

* **Music Assistant Delegation**: When a music intent is detected for a Roku, the
  system launches the **Media Assistant Channel (782875)** via ECP to provide a
  visual UI, while delegating high-quality audio streaming to Music Assistant.
* **ECP Playback Control**: Standard playback commands (Play, Pause, Stop) are
  sent via Roku's External Control Protocol.
* **Intelligent Query Cleaning**: Content searches are cleaned to remove room
  names and prepositions, ensuring accurate YouTube or Music Assistant searches.

### Roku: User Guide & Voice Commands

| Intent | Natural Speech Example | What Happens |
| :--- | :--- | :--- |
| **Play Music** | "Play Brandon Lake on Gracies TV" | Launches MA App on Roku + Starts MA audio stream. |
| **App Launch** | "Open Netflix on the Bedroom TV" | Sends ECP launch command for specific app ID. |
| **Navigation** | "Go down", "Select", "Go Home" | Sends standard remote control codes via ECP. |

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
