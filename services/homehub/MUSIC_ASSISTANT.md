# Music Assistant without Home Assistant

Music Assistant (MA) ran as the HA add-on `d5369777_music_assistant`, version **2.10.9**, image
`ghcr.io/music-assistant/server` (both read from the Supervisor's `apps.json`). With HA gone the add-on is
gone too, so MA moves to a standalone container on **192.168.2.205** with a copy of the add-on's data.
The homehub `music_assistant` driver then talks to it directly.

## 1. Compose service

Use the same image and tag the add-on ran, so the copied databases need no migration. Host networking is
required: MA discovers Cast, DLNA, AirPlay and Sendspin players by multicast, and every player fetches its
audio back from MA's stream server.

```yaml
services:
  music-assistant:
    image: ghcr.io/music-assistant/server:2.10.9
    container_name: music-assistant
    restart: unless-stopped
    network_mode: host
    volumes:
      - /opt/music-assistant/data:/data
      # Only if the "Local disk" (filesystem_local) provider is kept: in the add-on it read HA's /media share.
      # - /opt/music-assistant/media:/media:ro
    # MA's own compose example: needed only to mount SMB/NFS music shares from inside the container.
    cap_add:
      - SYS_ADMIN
      - DAC_READ_SEARCH
    security_opt:
      - apparmor:unconfined
    environment:
      - TZ=America/Phoenix
```

Ports (all on the host, nothing to publish):

| Port | What |
|---|---|
| 8095 | Web UI **and** the API: WebSocket `/ws` (what homehub uses) and JSON-RPC `POST /api` |
| 8097 | Stream server the players pull audio from |
| others | Player protocols (AirPlay, Sendspin, Cast callbacks) that MA opens itself |

The HA entry's URL, `http://d5369777-music-assistant:8094`, was the add-on's internal listener for HA on
the Supervisor network. It does not exist standalone: the API is on **8095**.

## 2. Copy the add-on data into the volume

The source is `/home/jeremiah/ha-export/2026-11-11/supervisor/apps/data/d5369777_music_assistant/` on
192.168.2.43. If that copy is still empty (it was while this was written), the same directory is mounted
read-only at `/mnt/haos-data/supervisor/apps/data/d5369777_music_assistant/` (sudo).

```bash
# on 192.168.2.43
SRC=/mnt/haos-data/supervisor/apps/data/d5369777_music_assistant   # or the ha-export path once complete
sudo tar -C "$SRC" \
  --exclude='musicassistant.log*' --exclude='library.db.backup' --exclude='options.json' --exclude='.cache' \
  -czf /tmp/ma-data.tgz .
sudo chmod 600 /tmp/ma-data.tgz && sudo chown jeremiah /tmp/ma-data.tgz
scp /tmp/ma-data.tgz jeremiah@192.168.2.205:/tmp/ && rm /tmp/ma-data.tgz

# on 192.168.2.205
sudo mkdir -p /opt/music-assistant/data
sudo tar -C /opt/music-assistant/data -xzf /tmp/ma-data.tgz && rm /tmp/ma-data.tgz
sudo chmod 700 /opt/music-assistant/data
```

What is in the directory and what to do with it:

| File | Keep? | Why |
|---|---|---|
| `settings.json` | **yes, whole** | Providers, players, queues. Its `encryption_key` decrypts the provider secrets stored in the same file (Alexa, OpenSubsonic, Audiobookshelf...): copy it unchanged or those providers lose their logins. `server_id` (`7efb34a6...`) is also the HA entry's unique_id. |
| `library.db` **+ `library.db-wal`** | **yes, together** | The library. The WAL (4.3 MB) holds recent writes not yet folded into the 9.7 MB database. |
| `auth.db` **+ `auth.db-wal`** | **yes, together** | Users, `jwt_secret` and token hashes. The WAL is 565 KB against an 86 KB database: recently issued tokens exist **only** in the WAL. Copy both (SQLite replays the WAL on open) or checkpoint the copy first: `sudo sqlite3 auth.db 'PRAGMA wal_checkpoint(TRUNCATE);'`. Copying `auth.db` alone silently loses tokens. |
| `playlists/`, `playlist_metadata_images/` | yes | Playlists MA stores itself. |
| `sendspin/`, `.alexa/`, `audible_auth_*.json`, `webrtc_certificate.pem`, `webrtc_private_key.pem` | yes | Provider state and credentials (Sendspin clients, Alexa, Audible, the remote-access WebRTC identity). |
| `.cache/` | optional, left out above | Rebuilt on demand. |
| `musicassistant.log*`, `library.db.backup` (August), `settings.json.backup` | no | Logs and stale backups. |
| `options.json` | no | Add-on options only (`log_level`, `safe_mode`). |

The archive holds secrets: keep it 0600 and delete it after unpacking.

## 3. What in settings.json still points at Home Assistant

Read-only inspection of the copy (secrets not printed):

1. **`providers["hass--emN8MGbB"]`** (domain `hass`, the Home Assistant plugin). In the add-on it connected
   through the Supervisor with no stored URL or token (its `values` are empty), so standalone it has nothing
   to connect to and fails to load.
2. **`providers["hass_players--Tpp4ngcm"]`** (domain `hass_players`) exposes 7 HA entities as MA players and
   depends on the plugin above:
   `media_player.loft_tv`, `media_player.office_tv_chrome`, `media_player.master_bedroom_tv`,
   `media_player.28_tcl_roku_tv`, `media_player.watch_watch_speaker`, `media_player.tv_loft_samsung_q900`,
   `media_player.office_jarvis`.
   Six of them have a native MA player already (Chromecast `81dfea8f...`, `16b032a3...`; DLNA
   `uuid:fdf567be...` for the Loft Samsung; `ROKU_2N0000000001` from the Roku Media Assistant provider;
   the `up...` universal players). **`media_player.office_jarvis` (the ESPHome "Office Jarvis Smart
   Speaker") has no other route.**
3. **`core.players.values.announce_tts_engine = "hass--emN8MGbB/tts.google_en_com"`**: HA's Google TTS,
   used only when MA itself speaks text. Jarvis sends announcement URLs, so nothing breaks, but MA logs an
   error if the engine is ever used. Clear it or pick another engine (Settings > Core > Players).

Choose one, before or right after the first start:

- **Disable** (simplest): the HA-backed players disappear; the Office Jarvis speaker then needs another
  path into MA (Sendspin firmware on the speaker, or the homehub esphome driver).

  ```bash
  sudo python3 - <<'EOF'
  import json
  p = "/opt/music-assistant/data/settings.json"
  s = json.load(open(p))
  for k in ("hass--emN8MGbB", "hass_players--Tpp4ngcm"):
      s["providers"][k]["enabled"] = False
  s["core"].get("players", {}).get("values", {}).pop("announce_tts_engine", None)
  json.dump(s, open(p, "w"), indent=2)
  EOF
  ```

- **Repoint at homehub**: in the MA UI, Settings > Providers > Home Assistant, give homehub's URL and a
  long-lived token. This keeps all 7 players, but only works if homehub's WebSocket API covers what MA's
  `hass_client` uses (auth, `get_config`, `get_states`/`subscribe_entities`, `subscribe_events`,
  `call_service`, `config/entity_registry/list`, `config/device_registry/list`) and its esphome driver can
  `media_player.play_media` on the speaker. Verify that before relying on it.

The add-on also flagged itself `HA add-on: True`; standalone MA detects no Supervisor and runs as a normal
server (no ingress, its own webserver on 8095, already enabled: `core.webserver.values.expose_server = true`).

## 4. Logins and tokens

- **API tokens keep working.** `auth.db` holds the `jwt_secret` and the hashes of all 8 long-lived tokens:
  "Home Assistant Integration" (user `homeassistant_system`; this is the token in the HA config entry, which
  homehub uses), "SharedLLM" (`summers`), and the per-user "Jarvis" tokens (`michele`, `madelyn`, `gracie`,
  `zachary`, `noah`, `testuser`). Nothing about them depends on HA, provided `auth.db` and its WAL are copied
  (section 2).
- **UI logins do not.** Every human user is linked only to the `homeassistant` auth provider (login through
  HA's ingress). Standalone, nobody can sign in to the web UI until an admin has a local password. Set one
  with an admin token, for example the SharedLLM token (user `summers`, role admin), over the API:
  `{"command": "auth/user/update", "args": {"user_id": "<summers' user_id>", "password": "<8+ chars>"}}`
  (send it on `/ws` after `auth`, or `POST /api` with `Authorization: Bearer <token>`; `auth/users` lists
  user ids). Then sign in at `http://192.168.2.205:8095` as `summers`.

## 5. Pointing homehub and Jarvis at it

- homehub's `music_assistant` driver reads the URL from, in order: the entry's `options["url"]`, the
  environment variable **`MUSIC_ASSISTANT_URL`**, then the imported `data["url"]` (the dead add-on address).
  Set `MUSIC_ASSISTANT_URL=http://192.168.2.205:8095` in the homehub container's environment. The token
  stays the imported one (`MUSIC_ASSISTANT_TOKEN` or `options["token"]` override it if ever needed).
- Jarvis's own direct MA client (`mass_url`/`mass_token`, used by `services/execution/handlers/mass_client.py`
  when HA was down) should point at the same `http://192.168.2.205:8095`.
- After the first start, check Settings > Core > Streams: the published IP should be 192.168.2.205 (the
  add-on published 192.168.2.20), or Cast and DLNA players will be told to fetch audio from the old address.

## 6. Checks

```bash
curl -s http://192.168.2.205:8095/info          # server_version 2.10.9, homeassistant_addon false
docker logs music-assistant 2>&1 | grep -iE "error|hass|webserver" | head
```

In homehub: `media_player.office_speaker` and the other MA entities leave `unavailable`, and
`music_assistant.search` with `{"name": "test", "limit": 1}` returns results.
