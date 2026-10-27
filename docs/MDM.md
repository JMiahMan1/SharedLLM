# Device management (MDM)

Phase 12 of `HANDOVER.md`: a parent's view and control of the family's phones,
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
| App-update push | `POST /api/mdm/devices/update` | admin |
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

## What is still open

- **Per-device update notices.** The update push is per owner today: selecting
  one of a person's two devices still notifies that person. The notice and push
  plumbing is per user (telemetry stores subscriptions and the FCM token under
  the username, and the app-update version is global), so there is no clean
  device dimension to target; this needs a device-scoped delivery channel before
  it can be honest.
- **Wipe needs device owner.** Like the Phase 11 Layer 2 restrictions, it only
  takes effect on a phone provisioned as device owner
  (`adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver`).
- **App install / uninstall** stays the screen rules; there is no per-app
  install queue.
