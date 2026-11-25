# Echo Satellites

## Overview

This guide covers upcycling two Amazon Echo devices into Home Hub voice satellites using `echo-dot-assist` (`hassmic`), while keeping Home Assistant supported as an **optional** integration:

- **Echo Dot 3rd Gen** - codename `donut`, Model `D9N29T`
- **Echo Show 8 1st Gen** - codename `crown`, Model `C7H6N3`

Home Hub is the primary control plane. Home Assistant remains an optional integration alongside it (see `services/homehub/ha_ws_compat.py`).

## Flashing and hardware

How to flash both models, what to solder on the Dot 3, the USB-C port mod and its parts list:
[ECHO_FLASHING.md](ECHO_FLASHING.md) (`scripts/echo_flash.sh donut|crown`).

## Home Hub & Jarvis Software Capabilities

Implemented in this repository:

- Auto-provisioned Noise PSK (`services/homehub/flows.py`), `EventInfo`/`UpdateInfo`, zero-touch `tag_scanned` Echo mesh vouching (`services/homehub/drivers/esphome.py`), Music Assistant Sendspin pairing (`GET/POST /hub/music_assistant/sendspin/{device_id}`).
- Native Echo↔Echo Drop-In (`services/execution/handlers/drop_in.py`, FastPath `services/gateway/fast_drop_in.py`, `DropInRequest` tool, BLE room-presence target resolution with `may_see_room` privacy enforcement).
- Whisper Mode, heard from the command's own audio (see "Whisper mode" below; `services/homehub/voice_whisper.py`, `services/homehub/voice_pipeline.py`).
- Ambient Light Ring services (`services/homehub/echo_ring.py`: `notify`, `progress`, `music_mode`, `flash_all`, `clear`).
- Corroborated Sound Guard (`services/homehub/sound_guard.py`: cross-room emergency corroboration + nursery/pet monitor alerts).
- Phone/Web ↔ Echo Dot encrypted UDP Opus Drop-In Bridge (`services/homehub/echo_dropin_bridge.py`, `HOMEHUB_ECHO_MESH_KEY`, `/hub/echo_dropin/status`, `/hub/echo_dropin/ws/{node}`).
- Echo Show 8 `/display` Kiosk UI (`services/ui/src/pages/DisplayKiosk.tsx`) & Echo Fleet Panel (`services/ui/src/components/devices/EchoFleetPanel.tsx`).

## Environment Variables

No hardcoded defaults or secrets are documented here; set these in your environment/config.

| Variable | Purpose |
|---|---|
| `HOMEHUB_VOICE_WHISPER_VOLUME` | The default whisper reply volume (0 to 1] until one is saved in HomeHub's voice settings. |
| `HOMEHUB_VOICE_WHISPER_TTS_VOICE` | TTS voice used when a run is whispered. |
| `HOMEHUB_ECHO_MESH_KEY` | Shared key for the encrypted UDP Opus Drop-In Bridge between phone/web and Echo Dot. |
| `HA_URL` | Optional Home Assistant URL (only when HA is used as an optional integration). |
| `HA_TOKEN` | Optional Home Assistant token (only when HA is used as an optional integration). |

## Whisper mode

Whisper "Hey Jarvis, what time is it" at night and the answer comes back quietly. It is a HomeHub
voice setting, on by default: Admin > Users & Devices > Whisper mode, or `GET/PUT /hub/voice/settings`
(`{"whisper": {"enabled", "volume", "level_drop_db"}}`, kept in the hub's store, used from the next
command, no restart).

**Detection** (`services/homehub/voice_whisper.py`), on the command's own 30 ms frames, numbers only:

- *voicing*: the share of speech frames with a pitch (normalised autocorrelation of at least 0.45 at
  70 to 400 Hz) and their median harmonics-to-noise ratio;
- *level*: the median speech level against that satellite's usual level, an exponential average of
  its clearly spoken commands (`voice_level:<entry>` in the hub's store; three commands before it is
  trusted, whispers never move it);
- *tilt* (2 to 5 kHz over 100 Hz to 1 kHz) is measured and logged but does not decide.

A command is whispered when at most 40% of its frames are voiced, its HNR is at most -1.5 dB, and (once
the usual level is known) it is at least `level_drop_db` (default 8 dB) under it. The satellite's own
`last_request_whispered` sensor, where the firmware has one, also counts.

Measured on 30 Kokoro clips (10 voices, 3 commands) at -26 dBFS, against the same clips whispered (each
frame's LPC envelope driven by noise instead of the voice) 12, 18 and 24 dB quieter, with room noise:

| | voiced frames | HNR | level vs usual | tilt | called a whisper |
|---|---|---|---|---|---|
| Spoken | 0.55 to 0.92 | 0.0 to 11.7 dB | 0 | -22 to -11 dB | 0 of 30 |
| Spoken across the room (-14 dB) | 0.56 to 0.93 | 0.2 to 11.6 dB | -10 to -16 dB | -22 to -11 dB | 0 of 30 |
| Whispered | 0.00 to 0.25 | -5.5 to -2.7 dB | -9 to -24 dB | -16 to -4 dB | 90 of 90 |
| Whispered right at the microphone | 0.02 to 0.22 | -4.6 to -2.8 dB | about 0 | -16 to -4 dB | 0 of 30 (not quieter) |

In a noisier room (noise at -50 dBFS) nothing spoken was taken for a whisper; the two quietest whisper
levels sank into the noise and were answered at the normal volume (53 of 90 detected).

**The reply**: before Jarvis starts on the answer the satellite's `assistant_volume` (else its media
player volume) is set to the whisper volume, so the holding phrase and hold music are quiet too. It is
put back once the reply should have finished playing on the node (the stream's length from when it
started, or the clip's length plus a second), not when the hub has merely handed it over. A failed run
puts it back after whatever it said, or at once; a run that is cut off puts it back at once; a
whispered follow-up keeps it low; an ordinary command puts it back first. A satellite with no volume
to set gets the audio itself scaled by the whisper volume. Every run logs one line:

```
voice satellite office run 3f2a: whisper=yes (level -44.1 dBFS, usual -29.8, voiced 0.08, hnr -3.9 dB, tilt -8.7 dB, by audio); new_voice=no; volume=lowered 0.8 to 0.22, restored to 0.8
```

## Mobile / Off-LAN Mode (HTTPS/WSS Tunnel)

Allows Jarvis to expose LAN services (e.g. ESPHome API on port :26053) to the internet
over a single WebSocket connection using WSS on port 443. This is useful for mobile
connectivity when the user is away from the home network.

- **Server**: `services/homehub/echo_tunnel.py` - `EchoTunnelManager` binds an asyncio TCP
  server on `127.0.0.1:0` and multiplexes TCP connections over a binary WebSocket link.
- **Protocol**: binary frames with 1-byte type header:
  - `0x01` OPEN - start a new TCP session with a conn_id
  - `0x02` DATA - pipe payload data on the conn_id'd TCP session
  - `0x03` CLOSE - close the conn_id'd TCP session
  - `0x04` PING - keep-alive
  - `0x05` PONG - respond to PING
- **Gateway routes** (`services/gateway/echo_tunnel_routes.py`):
  - `GET /api/homehub/echo_tunnel/status` - admin-auth status
  - `POST /api/homehub/echo_tunnel/disconnect/{node}` - admin-auth disconnect
  - `GET /api/homehub/echo_tunnel/tokens` - admin-auth: list nodes that have a token (never hashes)
  - `POST /api/homehub/echo_tunnel/tokens/{node}` - admin-auth: mint this Echo's own token (shown once)
  - `DELETE /api/homehub/echo_tunnel/tokens/{node}` - admin-auth: revoke this Echo's token
  - `WS /api/homehub/echo_tunnel/ws/{node}` - binary WS proxy; admin token proceeds, otherwise the
    bearer is forwarded upstream for HomeHub to accept or close 1008
- **HomeHub routes** (`services/homehub/api.py`):
  - `GET /hub/echo_tunnel/status` - internal status
  - `POST /hub/echo_tunnel/disconnect/{node}` - internal disconnect
  - `POST/DELETE /hub/echo_tunnel/tokens/{node}`, `GET /hub/echo_tunnel/tokens` - internal token
    mint / revoke / list (stored only as sha256 hex)
  - `WS /hub/echo_tunnel/ws/{node}` - binary WS handler; admin/internal, or a per-device token
- **Per-device tokens**: an Echo away from home opens its tunnel with its OWN token, never an
  admin key. An admin mints it once with `POST /api/homehub/echo_tunnel/tokens/<node>` (in
  Jarvis); the token is shown once, stored on the Echo, and the hub keeps only its sha256 hex.
- **Standalone client** (`scripts/echo_mobile_tunnel.py`):
  - CLI: `--server wss://... --node <name> --token-env JARVIS_ECHO_TOKEN`
  - `--token-env` holds the Echo's own device token from `POST /api/homehub/echo_tunnel/tokens/<node>`.
  - Exponential backoff reconnect, tunnels local ESPHome API (:26053) over HTTPS/WSS 443.

## Bluetooth Speaker Mode (A2DP Sink)

Enables Echo devices as A2DP sink speakers for Jarvis voice output, with voice-
controlled pairing and disconnect commands.

- **Command matcher** (`services/gateway/fast_echo_bluetooth.py`):
  - `match_echo_bluetooth(text) -> EchoBluetoothCommand | None`
  - Actions: `pair_on`, `pair_off`, `disconnect`, `status`
  - Supports optional room qualifiers: 'in the kitchen', 'on kitchen echo'
- **Entity resolution** (`resolve_bt_entities(states, node)`):
  - Locates `switch.<node>_bluetooth_pairing` / `switch.<node>_pairing`
  - `button.<node>_bluetooth_disconnect`
  - `sensor.<node>_bluetooth_device`
  - `binary_sensor.<node>_bluetooth_connected`
- **Fleet UI** (`services/ui/src/components/devices/EchoFleetPanel.tsx`):
  - Renders a Bluetooth Speaker row with:
    - Pair Bluetooth toggle (when `btPairingEntityId` exists)
    - Connected device badge (when `btConnectedDevice` exists)
    - Disconnect button (when `btDisconnectEntityId` exists)
- **Fleet entity extraction** (`services/ui/src/lib/echoFleet.ts`):
  - Extracts `btPairingEntityId`, `btPairingOn`, `btDisconnectEntityId`,
    `btConnectedDevice` from Echo device entity states.
