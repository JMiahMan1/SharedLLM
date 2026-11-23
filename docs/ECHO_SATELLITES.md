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
- True Whisper Mode (`HOMEHUB_VOICE_WHISPER_VOLUME`, `HOMEHUB_VOICE_WHISPER_TTS_VOICE` in `services/homehub/voice_pipeline.py`).
- Ambient Light Ring services (`services/homehub/echo_ring.py`: `notify`, `progress`, `music_mode`, `flash_all`, `clear`).
- Corroborated Sound Guard (`services/homehub/sound_guard.py`: cross-room emergency corroboration + nursery/pet monitor alerts).
- Phone/Web ↔ Echo Dot encrypted UDP Opus Drop-In Bridge (`services/homehub/echo_dropin_bridge.py`, `HOMEHUB_ECHO_MESH_KEY`, `/hub/echo_dropin/status`, `/hub/echo_dropin/ws/{node}`).
- Echo Show 8 `/display` Kiosk UI (`services/ui/src/pages/DisplayKiosk.tsx`) & Echo Fleet Panel (`services/ui/src/components/devices/EchoFleetPanel.tsx`).

## Environment Variables

No hardcoded defaults or secrets are documented here; set these in your environment/config.

| Variable | Purpose |
|---|---|
| `HOMEHUB_VOICE_WHISPER_VOLUME` | Whisper Mode ducking volume (0 to 1] for an Echo satellite's assistant volume. |
| `HOMEHUB_VOICE_WHISPER_TTS_VOICE` | TTS voice used when a run is whispered. |
| `HOMEHUB_ECHO_MESH_KEY` | Shared key for the encrypted UDP Opus Drop-In Bridge between phone/web and Echo Dot. |
| `HA_URL` | Optional Home Assistant URL (only when HA is used as an optional integration). |
| `HA_TOKEN` | Optional Home Assistant token (only when HA is used as an optional integration). |

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
