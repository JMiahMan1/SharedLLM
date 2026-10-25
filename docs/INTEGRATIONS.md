# Accounts & Integrations

The **Accounts & Integrations** page (`/identity`, also reached from the
account menu and from Settings) is where each person connects the services
Jarvis signs in to on their behalf, records their voice, links devices and
makes API keys. The help (?) buttons on that page open the matching section
below.

Credentials are per person and stored encrypted (Fernet) by the identity
service. Secrets are never sent back to the browser: a saved password or token
shows as **Saved**, and leaving the field blank on save keeps it. **Test** uses
the saved secret when the field is blank. How shared (household) credentials
are borrowed is described in `docs/PER_USER_CREDENTIALS.md`.

## Home Assistant

- **Instance URL**: the address Jarvis can reach, e.g. `http://homeassistant.local:8123`.
- **Long-Lived Token**: Home Assistant → your profile → Security → Long-lived access tokens.

Test calls `GET /api/config` with the token.

## Home alerts

Phone notices (category `home`, docs/PUSH.md) from Home Assistant entities.
Nothing is guessed from entity names: an admin maps each entity in
**Admin > Users & Devices > Home alerts**, stored as the `home_alert_entities`
setting, a JSON list:

```json
[{"entity_id": "binary_sensor.front_doorbell", "event": "doorbell", "people": []},
 {"entity_id": "cover.garage_door", "event": "garage_open", "people": ["michele"], "minutes": 20},
 {"entity_id": "sensor.washer_power", "event": "appliance_done", "people": [], "watts": 10}]
```

`people` empty means everyone (every account but the system one). Execution's
`home_alerts` loop reads all states every 15 seconds with the system
account's Home Assistant login (`GET /api/states`; there is no event
subscription) and compares each mapped entity with the previous round.

| event | Entity | Sends when | Tuning |
|---|---|---|---|
| `doorbell` | binary sensor or `event.*` | pressed (on, or on and off between two rounds; an event entity's new timestamp) | |
| `motion_away` | motion binary sensor | it turns on while nobody is home; at most once per 10 minutes per sensor | |
| `garage_open` | cover (`open`) or binary sensor (`on`) | open longer than `minutes`, once per opening | `minutes` (15) |
| `door_unlocked_night` | lock | `unlocked` for `minutes` between 22:00 and 6:00 household time, once per night per unlock | `minutes` (5) |
| `appliance_done` | power sensor (W), or a state like `running` / `idle` | power above `watts` for at least 5 minutes, then below it for `minutes` (a pause mid-cycle does not count) | `watts` (10), `minutes` (5) |
| `water_leak` | moisture binary sensor | `on`, including on first sight after a restart; priority 5 | |

"Nobody home" is the presence tracker execution already keeps (ESPresense
rooms, else each phone's GPS against Home Assistant's home location): true
only when everyone it knows of is `not_home`. With no presence data at all,
motion never notifies. Each notice carries `tag` `home:<entity_id>`, so a
newer one replaces the older on the phone.

## Nextcloud

- **Cloud Base URL**: your Nextcloud address.
- **Cloud Username**: your Nextcloud login (pre-filled from your Jarvis name).
- **App Password**: Nextcloud → Personal settings → Security → Create new app password.

Used for files, Talk (family chat), notes and calendars. Test performs an OCS API request.

## Audiobookshelf

- **Server URL**
- **API Key** *or* **Username** and **Password**. An API key alone is enough
  (Audiobookshelf → Settings → Users → your user → API token).

Test calls `GET /api/me` with an API key, or `POST /login` with a
username and password (Audiobookshelf logs in at the root `/login`; `/api/login`
answers 401).

## Music Assistant

- **Server URL**: e.g. `http://music-assistant.local:8095`.
- **Access Token**: a long-lived token from Music Assistant (Settings → Users / tokens).

Test makes one JSON-RPC read to `<server>/api` with the token.

## Private Git

- **Git Endpoint**, **Auth Username**, **Auth Password** (or token) for a
  self-hosted git server Jarvis may read and push branches to. There is no
  connection test for this one; it is checked when used.

## GitHub

- **GitHub Username** and a **Personal Access Token** (repo scope).
- **Protected Branches**: branch names Jarvis must never push to directly
  (type a name and press Enter or comma).

Test calls GitHub's `GET /user` and reports the login.

## GitLab

- **GitLab URL** (blank for gitlab.com), **GitLab Username**, **Access Token**.

Test calls GitLab's `GET /api/v4/user`.

## Skylight

One household account, set up by an admin on the system default user. Everyone
else can only turn Skylight chores and rewards on or off for themselves; the
fields are read-only for them.

## OpenRouter

OpenRouter is configured once for the whole household, not per user: pick the
cloud provider and its API key under Settings > LLM (the `llm_cloud_url` and
`llm_cloud_api_key` global settings). There is no per-user OpenRouter tile.

## External Client Setup

API keys let OpenAI-compatible clients (OpenWebUI, LobeChat, scripts) talk to
Jarvis as you. **Generate New Key** asks which client the key is for and then
shows the key **once** with a copy button; only its prefix is kept on the
server. Point the client at `https://<your server>/v1` with the key as the
bearer token. Revoke a key with the bin icon (it asks to confirm).
