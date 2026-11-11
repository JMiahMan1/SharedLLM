# When Home Assistant is down

Jarvis drives most of the house through Home Assistant (HA). This page says
what keeps working while HA is unreachable, how, and what does not.

## Detecting it (`services/execution/ha_health.py`)

- A connection error or a timeout is HA *unreachable*. Any HTTP answer, even
  a 401 or a 500, means HA is up: the request was wrong, so no fallback runs.
- Two unreachable results in a row mark the HA host down. While it is down,
  calls fail at once with "Home Assistant is unreachable (down for Ns: ...)"
  instead of waiting out the 45 s timeout. The connect timeout is 5 s.
- Every 15 s one real call goes through as a probe; the first answer marks HA
  up again. Background pollers keep probing on their own schedule.
- Admins get one notice after HA has been down for a minute (a restart takes
  about that long), and a "back" notice under the same tag.
- Execution `/health` reports `checks.home_assistant` (`ok`/`down`) and the
  per-host detail. It never makes execution itself unhealthy.

## What still works

| Capability | Direct route | Code |
|---|---|---|
| Lights, switches, fans, covers, climate on ESPHome boards | ESPHome native API | `hardware_router.py`, `handlers/climate.py` |
| Music on speakers | Music Assistant `player_queues/play_media`, search over MA's WebSocket | `handlers/media.py` `play_music_direct` |
| Announcements | Kokoro TTS (local), then MA `players/play_announcement`, then Google Cast | `direct_announce.py` |
| Timer and alarm alerts | ESPHome alert, then the speaker via MA or Cast | `main.py` `execute_trigger` |
| TV remote and media keys | Roku ECP, LG webOS (stored pairing key), Samsung (stored token), Cast, MA `players/cmd_*`, Wake-on-LAN | `tv_direct.py` |
| "Is the garage open?" | The last states HA reported, always with their age | `ha_snapshot.py`, entity search |
| The people map | Jarvis's own phone fixes and zones | `services/geo/main.py` `_people_from_history` |
| Home alerts (leak, doorbell, power) on ESPHome boards | Read the sensors from the board | `home_alerts.py` `esphome_states` |
| Weather | Open-Meteo is the primary source anyway | `weather.py` |

A speaker or TV is matched by the HA entity's name: HA names entities after
the device (`media_player.kitchen_speaker` is MA's "Kitchen Speaker"), so the
slug is the join key.

## What does not

- **Locks, alarm panels, garage openers on HA integrations.** Deliberately
  HA-only; they fail with a clear message rather than take a less-tested path.
- **Voice satellites.** They stream audio to HA's Assist pipeline, which calls
  Jarvis's Wyoming server; with HA down no audio arrives. The Jarvis app's own
  voice path does not use HA.
- **Devices Jarvis has never seen directly.** An LG TV that was never paired
  (casting a game to it once pairs it), a TV with no address or MAC in the
  device registry, or a non-ESPHome sensor cannot be reached without HA.
- **Android TV keys over ADB.** Jarvis holds no ADB keys; Cast covers media.
- **Last-known states are for answering, never for acting.** No route toggles
  a device based on a snapshot.
