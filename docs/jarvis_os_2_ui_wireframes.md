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
*   `/admin/ops` (Raven Operations Panel & Control Plane)
*   `/admin/integrations` (Dynamic Plugin Configs)

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

---

## 4. Interaction Modals

### 4.1 Voice Assistant Overlay
When the user says "Jarvis", the entire UI blurs (`backdrop-blur-3xl`). A central, dynamic audio visualizer appears, reacting to the user's voice input, followed by Kokoro's TTS output.

### 4.2 Security Override Challenge
If the LLM attempts to unlock a door, a stark red modal drops down:
*   Header: "SECURITY OVERRIDE REQUIRED"
*   Text: "Jarvis requested to unlock the Front Door. You must authenticate."
*   Input: Admin PIN pad or biometric prompt.
