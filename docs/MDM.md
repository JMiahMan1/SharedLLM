# Device management (MDM)

Phase 12 of the device-management plan: a parent's view and control of the family's phones,
tablets and companion devices, on the family's own server. Nothing here talks to
a paid MDM; it is the existing device registry, screen rules and OTA flow with
one surface over them.

The gateway owns the routes (`services/gateway/mdm.py`); identity remains the
one source of truth for devices, screen rules and the family list. The page is
`services/ui/src/pages/Devices.tsx`.

## What it does

| Piece | Route | Who |
|---|---|---|
| Device inventory | `GET /api/mdm/devices` | father / admin |
| Device kind / label / owner update | `PATCH /api/user-panel/devices/{key}` | admin |
| App-update push | `POST /api/mdm/devices/update` | admin |
| A phone's own queued notices | `POST /api/mdm/devices/notices/ack` | the device's owner |
| Direct ESPHome management | `POST /execute/esphome` (`list`, `call`, `configure_jarvis`, `ota`) | authenticated / admin |
| ESPHome OTA firmware upload | `POST /api/mdm/esphome/{device}/ota` | admin |
| Shared School and Library folders | `GET/POST/DELETE /api/mdm/files*` | admin |
| A child's own folders | `GET /api/mdm/files/for-me` | any member |
| One shared file | `GET /api/mdm/files/{folder}/{name}` | any member |
| Family Library set (Calibre) | `GET /api/mdm/library/books` | any member |
| One Library book | `POST /api/mdm/library/books/file` | any member |
| Remote wipe | `POST /api/mdm/devices/{key}/wipe` | father / admin |
| Audit log | `GET /api/mdm/audit` | admin |

Two MDM actions are deliberately **not** reimplemented, because a second copy of
an existing mechanism is how the two drift apart:

- **Lock / unlock** is the existing screen-time pause
  (`POST /api/screen-time/{user}/pause`). The Devices page's Lock button calls
  it; the pause route appends to the audit log.
- **App install / uninstall and COSU kiosk control** is the existing screen rules
  (`install_approval`, `blocked_apps`, `uninstall_apps`, `install_apps`, `kiosk_mode`
  and `kiosk_apps` on `PUT /api/screen-time/{user}/rules`). On a device-owner phone,
  `DeviceOwnerGuard` silently uninstalls packages in `uninstall_apps` via
  `PackageInstaller.uninstall`, suspends `blocked_apps` at the OS level via
  `setPackagesSuspended`, installs verified APKs silently via `PackageInstaller.Session`,
  and sets the COSU lock-task allowlist (`setLockTaskPackages`) when `kiosk_mode` is on.
- **OS updates, VPN lockdown, Wi-Fi provisioning, and CA certificates (Batch 8)**:
  - `os_update_policy` (`default`, `automatic`, `windowed`, `postpone`) configures Android system update installation via `DevicePolicyManager.setSystemUpdatePolicy`, with optional maintenance window start and end times in minutes from midnight.
  - `vpn_lockdown`: when enabled, `DeviceOwnerGuard` passes `lockdownEnabled: true` to `setAlwaysOnVpnPackage`, blocking any device network traffic if the Jarvis web filter VPN is disconnected.
  - `wifi_networks`: household Wi-Fi credentials (`ssid`, `password`, `security`, `hidden`) provisioned directly to child devices via `WifiManager`.
  - `ca_certs`: household X.509 root CA certificates installed directly into the device's trusted credentials store via `dpm.installCaCert` and `dpm.uninstallCaCert`.
  - Parents and administrators manage these policies via `DevicePolicyCard.tsx` on the Guardian page.

## Device awareness: Phones vs Tablets vs ESPHome companion nodes

Not every registered device is an Android phone:

1. **Android Phones (`kind: "phone"`, `platform: "android"`) & Android Tablets (`kind: "tablet"`, `platform: "android"`)**
   - `services/identity/main.py:infer_mobile_kind` resolves mobile form factors from `form_factor` when reported by `JarvisDeviceInfo`, and falls back to model-string inference for known tablet models (`SM-T*`, `SM-X*`, `SM-P*` Samsung Galaxy Tab models such as `SM-P620` and `SM-X520`, `Galaxy Tab`, `Pixel Tablet`, `iPad`, `TB-*`). Startup migration `_migrate_device_kinds` upgrades existing self-registered tablet rows in SQLite that were created by older app builds before `form_factor` existed.
   - Admins can also reclassify any mobile device between `phone` and `tablet` in one click on `Devices.tsx` via `PATCH /api/user-panel/devices/{device_key}`.
   - Tablets render with a `<Tablet>` icon, `Android · Tablet` badge, `Form factor: Tablet` fact row, and tablet-accurate lock/wipe copy (`"this tablet"` instead of `"this phone"`).
2. **ESPHome Companion Devices (`kind: "watch" | "speaker" | "screen" | "assistant" | "light"`, `platform: "esphome"`)**
   - Devices such as the ESPHome-based `Jarvis Watch` (`esphome:744dbd2c9728`, `esp32-s3-devkitc-1`, `../ESP32-S3-Touch-LCD-1.69-Watch`), smart speakers (`../Home-Assistant-Smart-Speaker`), and smart screens / LVGL wall panels (`../jarvis-screen`, `../cyd`, `../esphome-waveshare7`) do not run Android and do not have Android `Protection` flags (`Device admin`, `Device owner`, `Screen guard`, `Web filter`) or Android APK/lock/wipe actions.
   - `GET /api/mdm/devices` tags every device with `platform: "esphome" | "android"`. `POST /api/mdm/devices/update` and `POST /api/mdm/devices/{key}/wipe` refuse ESPHome targets (`422` / `409`).
   - Admins can reclassify any ESPHome device between `watch`, `speaker`, `screen`, `assistant` and `light` on `Devices.tsx` via `PATCH /api/user-panel/devices/{device_key}`.
   - On `Devices.tsx` (and `DevicesSection.tsx` on the family member page), ESPHome devices show their hardware (`esp32-s3-devkitc-1`), ESPHome build (`2026.9.0`), project firmware version (`1.4.6`), and battery state, alongside native ESPHome remote monitoring, control, and OTA update:
     - **Ping, live telemetry & entity list** (`POST /execute/esphome` `action: "list"`): connects over the ESPHome native TCP API (`aioesphomeapi`), subscribes to live entity states (`subscribe_states`), infers the device profile (`infer_esphome_kind`: `watch`, `speaker`, `screen`, `light`, `assistant`), and returns live values + metadata (`unit_of_measurement`, `options`, `min_value`/`max_value`/`step`, `brightness_pct`, `volume_pct`, `muted`, `installed_version`/`latest_version`) alongside exposed user services (`services`).
     - **Interactive domain & user-service controls** (`POST /execute/esphome` `action: "call"`):
       - Controllable entities: `button` (`Press`), `switch` (`Toggle`), `light` (`Toggle` + brightness slider + effect), `select` (option dropdown), `number` (numeric input + `Set`), `text` (text input + `Send`, e.g. `jarvis_chat_input` on `cyd` / `jarvis-screen`), `media_player` (`Play`/`Pause`/`Stop`, `Mute`/`Unmute`, and volume slider for `s3-speaker` / `jarvis-screen`), and `update` (`Install`).
       - Watch quick actions (when the watch exposes `find_watch`, `set_time`, `set_glance`, or `notify`): **Find watch**, **Sync clock**, **Set glance**, and **Send notice**.
       - Smart screen / wall panel quick actions (when the screen exposes `show_page`, `set_rotation`, or `show_dlg`, e.g. `esphome-waveshare7` / `cyd`): page switcher (`Home`, `Now Playing`, `Lights`, `Settings`, `Weather`, `Intercom`), orientation (`0°` / `180°`), and dialog trigger (`Drop-In`, `Broadcast`, `Close`).
     - **Remote OTA firmware update** (`POST /api/mdm/esphome/{device}/ota` -> `POST /execute/esphome` `action: "ota"`): uploads a compiled `.bin` (or `.bin.gz` / `.ota`) image and streams it directly to the node using ESPHome's native TCP OTA v1/v2 binary wire protocol on port `3232` (with gzip compression and optional MD5/SHA256 password authentication), falling back to the ESPHome `web_server` `POST /update` multipart endpoint on port `80` when TCP `3232` is closed. Every OTA flash is recorded in `audit.json` under `esphome_ota`.
     - **Sync Jarvis key & URL** (`POST /execute/esphome` `action: "configure_jarvis"`) to re-provision the Jarvis endpoint URL and a fresh device API key over the encrypted Noise channel.
     - **Battery, steps & history** (`DeviceDetailSheet`) to inspect 24-hour battery discharge, watch step counts, and recent telemetry events.

### Multi-User Access Control & Device Assignment

1. **Admin Assignment & Authority:**
   - Administrators (users with `is_admin: true`, `is_system_default: true`, `family_role: "father"`, or `username: "default"`) have full control over all registered devices.
   - Admins can assign ESPHome devices to specific family members via the owner dropdown on `Devices.tsx` or `PATCH /api/user-panel/devices/{device_key}`. This updates both the user-panel device registry in identity and synchronizes the owner in `GlobalSetting("esphome_devices")`.
   - Admins see the comprehensive MDM inventory, full device controls, OTA firmware upload panels, Jarvis URL/key sync buttons, and workspace flashing dialogs.
2. **Family Member (Non-Admin) Permissions:**
   - Non-admin users see only their assigned ESPHome companion devices (plus unassigned devices) in the "My Devices" section on `Devices.tsx`.
   - Everyday functions are permitted through `esphome_client.member_guard`: volume adjustments, mute/unmute, media player controls (`play`, `pause`, `stop`), light toggles and brightness sliders, option selects, numbers, and text/chat messaging.
   - Maintenance and destructive controls are strictly blocked for non-admins (`EsphomePermissionError` / HTTP 403): device restarts, factory resets, safe mode, configuration buttons, and Jarvis key/URL re-provisioning.
   - **OTA updates are strictly Admin-only**: non-admins cannot upload OTA firmware binaries or trigger flashing; the UI hides OTA controls, and backend execution enforces admin authorization on all OTA actions.

### Workspace ESPHome Studio: Edit, Test with Jarvis, and Flash

Workspaces (`services/ui/src/components/workspace/WorkspaceIDE.tsx`) provide an integrated ESPHome Studio for developing and maintaining firmware configurations:

1. **Edit & Assist with Jarvis:**
   - Workspace IDE includes an "Ask Jarvis" button on ESPHome YAML configurations (`.yaml` / `.yml`), launching an interactive session with Jarvis to review schemas, configure pins, sensors, displays, and LVGL pages.
2. **Test & Validate Configurations:**
   - `POST /api/workspaces/{workspace_id}/esphome/validate` invokes workspace verification via `services/execution/handlers/workspace.py:handle_workspace_lint`.
   - Automatically detects ESPHome YAML files and executes `esphome config`, falling back gracefully to `yamllint` or PyYAML syntax checks if the full ESPHome CLI is not pre-installed.
3. **Flash ESPHome Devices (Admin Only):**
   - `POST /api/workspaces/{workspace_id}/esphome/flash` allows admins to select a compiled `.bin` firmware artifact from the workspace and push it directly to any ESPHome companion device.
   - Streams the binary to the device via native ESPHome TCP port 3232 (falling back to web server port 80).
   - Records the flash action, workspace ID, firmware path, and target device in `audit.json` under `esphome_ota`.

## No invented values

The rule for the whole page: show real data or an explicit loading, empty or
error state.

- A member whose devices identity will not hand over is `readable: false` with
  the reason, not an empty list.
- An update push with no published build is refused (409) rather than announced.
- A blank `calibre_library_path` is reported as "not configured" with the
  setting's name, not shown as an empty shelf.

## The shared folders

The shared sets a parent pushes are one **School** folder and one **Library**
folder, under `MDM_FILES_DIR` (default `/app/data/mdm_files`). Each has a
`manifest.json`, and `pushes.json` records who each folder is pushed to. The
store is plain files: no database, no secrets.

The folder lives in a bind mount (`./data/mdm_files:/app/data/mdm_files` in
`docker-compose.yml`) so the files and the audit log survive a redeploy.

## The Library set, read-only from Calibre

`docs/CALIBRE_LIBRARY.md` describes the family shelf at `Books/Text`, read over
WebDAV and owned by Calibre. The Library set is sourced from that shelf:

- The gateway does not talk WebDAV itself. It browses `calibre_library_path`
  through the storage service's existing Nextcloud provider
  (`POST /providers/list`, `POST /providers/fetch`), which owns the credentials.
  This is the same path the RAG index and the Bible importer use.
- Only book-format files are shown (epub, pdf, mobi, azw3, fb2, djvu, cbz, cbr,
  rtf, txt, htmlz, docx). Calibre's `metadata.db`, cover images and `.opf`
  sidecars are not books and are left out rather than listed as unreadable.
- The shelf root is re-checked on every fetch, so a crafted path cannot leave
  it. Nothing here writes to the library.
- A listing is bounded (500 books); when the shelf holds more, the answer says
  `truncated` with the true `count` rather than shipping all of them.
- A child sees the Library set when a parent has pushed the Library folder to
  them; the pushed files and the shelf books sit in the same card.
- A book opens in the app where it can (text and PDF render; epub/mobi and other
  formats are offered as a download), because the shelf file is the real book,
  not a rewritten copy.

## The audit log

Every parent action is appended to `audit.json` beside the shared folders: who
pushed an update or a folder, uploaded or removed a file, or locked or wiped a
device, and when. It is append-only and capped (500 entries), like the telemetry
activity feed, and the Devices page reads it back as the Activity log.

The log holds small scalars only (the action, the actor, the target, a short
detail object) and never a secret. A failed write is logged and the action still
completes: the log is best-effort rather than a way to make an MDM action fail.

## Remote wipe

A wipe is destructive and irreversible, so it is gated on an explicit
`confirm` and on the device having reported `guard.deviceOwner`. Without device
owner the phone's own Android refuses `wipeData`, and pretending otherwise would
be a lie. The request is queued with a 24 hour TTL and read by the screen-time
status the phone already polls; the guard erases the phone on its next
check-in. An old queued wipe is ignored so a phone that was offline for longer
than that does not erase itself unexpectedly later.

## Per-device notices

An update notice can name a person or a device, and the two go to different
places:

- **A person** is pushed to (`sent`): the notice reaches every device they are
  signed in on, because telemetry publishes it to that person's one ntfy topic.
- **A device** is queued for (`queued`): the notice is written under the device
  key in `notice_requests.json` and handed to that phone on its own check-in -
  the screen-time status its guard already polls, the same way a queued wipe is.
  Nothing is pushed to the person, so their other devices never hear about a
  build that was meant for one of them.

The phone names itself with `?device_key=…` on the status poll. The key only
counts when identity says it belongs to the member whose status is being read,
so one phone cannot read another's queue, and a check-in without a key gets no
`notices` field at all. The phone acks what it has shown
(`POST /api/mdm/devices/notices/ack`, the owner or an admin only), and an
unshown notice is forgotten after seven days, so a phone that was off for a
week does not announce a long-gone build. At most 20 notices wait per device,
and the Devices page shows them as "Waiting" on that device until its next
check-in.

## What is still open

- **The phone showing a queued notice.** The gateway queues it, hands it over on
  the check-in and the Devices page shows it waiting; the guard does not yet
  read `notices` on its poll or ack it (`GuardApi` / `GuardNotices`), so a
  device-scoped notice is not yet shown on the phone itself.
- **Wipe needs device owner.** Like the Phase 11 Layer 2 restrictions, it only
  takes effect on a phone provisioned as device owner
  (`adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver`).
- **App install / uninstall** stays the screen rules; there is no per-app
  install queue.
