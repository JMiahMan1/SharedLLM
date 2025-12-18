# Integration Architecture Documentation

## What is an Integration?
In this system, an **Integration** is a specialized handler class responsible for executing media commands on a specific type of device. 

The architecture follows a **Router-Delegate** pattern:
1.  **Router (`commands.py`)**: Receives the natural language intent (e.g., "Play X on Y"), resolves the target entity, and determines *which* integration handler to use.
2.  **Factory (`IntegrationFactory`)**: Instantiates the correct handler class based on the device's Home Assistant `integration` attribute (e.g., `cast`, `music_assistant`, `roku`).
3.  **Handler (The Integration)**: Executes the actual logic, handling device-specific quirks like power management, query formatting, or app launching.

This modularity ensures that adding features for one device type (like turning on a TV before casting) doesn't complicate the logic for others (like playing music on a smart speaker).

---

## 1. Cast Integration (`CastIntegration`)
**Target Devices**: Google Chromecast, Google Home, Nest Hub, Cast-enabled TVs (Vizio, Sony, etc.).

### Features
*   **SmartPowerSync**: The defining feature of this integration. Cast devices often cannot turn themselves on if they are built into a "dumb" TV or powered by USB.
    *   **Logic**: Before playing media, the integration looks for a "physical TV sibling" of the Cast device.
    *   **Discovery**: It searches ChromaDB for other devices in the same physical group (e.g., "Office") that look like a TV. If that fails, it tries name matching (stripping `_cast` or `_chrome` suffixes).
    *   **Action**: If a sibling TV is found and is `off`, the integration sends a `media_player.turn_on` command to the *TV* and waits 4 seconds before sending the media to the *Cast* device.
*   **Standard Playback**: Supports standard HASS `media_player.play_media` commands.

### User Guide & Voice Commands

| Feature | Natural Speech Example | What Happens |
| :--- | :--- | :--- |
| **SmartPowerSync** | "Play Brandon Lake on the Office TV" | 1. System finds `media_player.office_tv_chrome` (Cast).<br>2. Finds sibling `media_player.office_tv_chrome` (TV).<br>3. TV is OFF? **Turns it ON**.<br>4. Waits 4s.<br>5. Plays music. |
| **Standard Play** | "Play a fireplace video on the Living Room TV" | Plays video content directly. |
| **Power Control** | "Turn on the Chromecast" | Turns on the device (and likely the TV via CEC). |

---

## 2. Music Assistant Integration (`MusicAssistantIntegration`)
**Target Devices**: All players controlled via the Music Assistant add-on (Sonos, AirPlay, Cast, etc.).
**Key Feature**: "Smart Routing" - deeply integrates with the Router to steal "music" commands from hardware devices.

### User Guide & Voice Commands

| Intent | Natural Speech Example | Internal Logic |
| :--- | :--- | :--- |
| **Music Search** | "Play some jazz on the kitchen speaker" | Cleans query to "jazz", searches MA, plays Radio/Playlist. |
| **Artist Radio** | "Play The Midnight on the Office Speaker" | Starts "The Midnight Radio". |
| **Smart Swap** | "Play music on the Office TV" | Router detects "Office TV" is a Cast device, but "music" intent prefers highest quality. **Swaps target** to `mass_office_speaker` automatically. |

---

## 3. Standard Integration (`StandardIntegration`)
**Target Devices**: Roku, WebOS (LG), Samsung (Tizen), Apple TV.

### User Guide & Voice Commands

| Intent | Natural Speech Example | Notes |
| :--- | :--- | :--- |
| **App Launch** | "Open Netflix on the Bedroom TV" | Uses `media_player.select_source` or specialized app launch service. |
| **Navigation** | "Go down", "Select", "Go Home" | Sends standard remote control codes. |
| **Stop/Pause** | "Pause the TV" | Standard media control. |

