# Echo Satellites

## Overview

This guide covers upcycling two Amazon Echo devices into Home Hub voice satellites using `echo-dot-assist` (`hassmic`), while keeping Home Assistant supported as an **optional** integration:

- **Echo Dot 3rd Gen** — codename `donut`, Model `D9N29T`
- **Echo Show 8 1st Gen** — codename `crown`, Model `C7H6N3`

Home Hub is the primary control plane. Home Assistant remains an optional integration alongside it (see `services/homehub/ha_ws_compat.py`).

## Hardware Notes & Warnings

### Echo Dot 3 (`donut`, `D9N29T` only — NOT `C78MP8` or `36EBT3`)

- Keep the device offline from Amazon.
- Check `Gamer92000/echo-dot-assist#112` before unlocking multiple units.
- Power: the Dot 3 stays powered by its **12 V / 1.25 A barrel jack**. The USB-C modification below must **not** supply VBUS.

### Bottom USB-C Female Port Mod

- Pinout: `GND -> TM3`, `D- -> TM4`, `D+ -> TM5`. **`VBUS` (+5 V) MUST remain disconnected.**
- The USB-C female breakout board MUST include **dual 5.1 kΩ pull-down resistors on `CC1`/`CC2`** (e.g. Adafruit PID 4090 or Treedix/HiLetgo 6-pin 5.1 kΩ boards) so USB-C-to-C cables enumerate properly.

### Echo Show 8 1st Gen (`crown`, `C7H6N3`)

- Built-in micro-USB port — no soldering required.
- Pair with `kiosk-satellite` pointed at Jarvis `/display`.
- Leave `kiosk-satellite`'s own voice assistant **disabled** so `hassmic` retains the microphone.

## Home Hub & Jarvis Software Capabilities

Implemented in this repository:

- Auto-provisioned Noise PSK (`services/homehub/flows.py`), `EventInfo`/`UpdateInfo`, zero-touch `tag_scanned` Echo mesh vouching (`services/homehub/drivers/esphome.py`), Music Assistant Sendspin pairing (`GET/POST /hub/music_assistant/sendspin/{device_id}`).
- Native Echo↔Echo Drop-In (`services/execution/handlers/drop_in.py`, FastPath `services/gateway/fast_drop_in.py`, `DropInRequest` tool, BLE room-presence target resolution with `may_see_room` privacy enforcement).
- True Whisper Mode (`HOMEHUB_VOICE_WHISPER_VOLUME`, `HOMEHUB_VOICE_WHISPER_TTS_VOICE` in `services/homehub/voice_pipeline.py`).
- Ambient Light Ring services (`services/homehub/echo_ring.py`: `notify`, `progress`, `music_mode`, `flash_all`, `clear`).
- Corroborated Sound Guard (`services/homehub/sound_guard.py`: cross-room emergency corroboration + nursery/pet monitor alerts).
- Phone/Web ↔ Echo Dot encrypted UDP Opus Drop-In Bridge (`services/homehub/echo_dropin_bridge.py`, `HOMEHUB_ECHO_MESH_KEY`, `/hub/echo_dropin/status`, `/hub/echo_dropin/ws/{node}`).
- Echo Show 8 `/display` Kiosk UI (`services/ui/src/pages/DisplayKiosk.tsx`) & Echo Fleet Panel (`services/ui/src/components/devices/EchoFleetPanel.tsx`).

## Environment Variables

No hardcoded defaults or secrets are documented here; set these in your environment/config.

| Variable | Purpose |
|---|---|
| `HOMEHUB_VOICE_WHISPER_VOLUME` | Whisper Mode ducking volume (0–1] for an Echo satellite's assistant volume. |
| `HOMEHUB_VOICE_WHISPER_TTS_VOICE` | TTS voice used when a run is whispered. |
| `HOMEHUB_ECHO_MESH_KEY` | Shared key for the encrypted UDP Opus Drop-In Bridge between phone/web and Echo Dot. |
| `HA_URL` | Optional Home Assistant URL (only when HA is used as an optional integration). |
| `HA_TOKEN` | Optional Home Assistant token (only when HA is used as an optional integration). |