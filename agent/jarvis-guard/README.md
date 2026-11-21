# Jarvis Guard - the Windows 10+ companion for family PCs

`docs/WINDOWS_LOCK_SCREEN.md` describes the design. This directory is the
client it describes, at the scope agreed for this first pass: the agent plus
the minimal hub deltas it depends on. It is deliberately **not** the whole
plan (no web filter, no update push - see "What this is not").

## What it is

One honest program for a Windows 10/11 family PC whose child is a standard
user and whose parent is a local administrator. `jarvis-guard.exe` runs in
three modes:

- `--serve` (SYSTEM, boot task) - the guard loop: polls the hub once a
  minute, publishes a world-readable status file, meters foreground apps for
  usage, and enforces the decision: a locked PC signs its interactive
  sessions out. Offline, bedtime/school are re-evaluated against the local
  clock from the last cached rules (fail closed); everything else fails open.
- `--tray` (per sign-in, all users) - the always-on signage: a tray shield
  whose tooltip shows time left and used today; a click opens the time panel
  (left, used of how much, what locks next, "Ask for more time"); a
  right-click menu (screen time, ask for more time, open the hub, a parent
  can unlock, about). Ten and five minutes before a lock it raises the panel
  as a warning; a minute before, a balloon. It also measures foreground use
  in the signed-in session - only while the session is unlocked, the display
  on and the user not idle for ten minutes - and hands it to the service over
  the pipe (a SYSTEM process cannot see the desktop). "Ask for more time"
  goes through the service (`ask-time`) to `POST /api/screen-time/requests`.
- `--overlay` (raised by the tray as soon as the PC locks) - the lock
  surface, the same ways out as the phone's lock screen: the reason, when the
  lock lifts and a countdown to the sign-out; "Ask for more time" (15, 30 or
  60 minutes, with a note); "Message a parent" (a chat message to the
  child's parents, `/api/guard/message`); "Chores" (today's chores with a
  Done button each; a parent then confirms each one, as on Android; a
  "chores first" lock opens on them); and "Ask a parent to unlock" (how long
  and why; both parents approve or deny on their phones, nothing is typed
  here; or a parent's offline code when the hub cannot be reached). It
  explains the lock; the session sign-out holds it. One centred column that
  wraps and scrolls, sized from the screen's DPI (PerMonitorV2), so it works
  from 1366x768 to 4K and at 150% scaling; buttons are at least 56 px tall.

The hub, identity and MDM stay exactly as they are for every other device.
The only hub changes behind this client are the same scoped-key enrolment the
phones use.

## Install (the intended path)

1. A parent who manages devices opens Jarvis, Devices, Family: "Family PC
   (Windows)" shows the version and the SHA-256 and downloads
   `jarvis-guard-windows.zip` (exe, its `.sha256`, `install.ps1`,
   `uninstall.ps1`, this README). The deploy copies it there from the latest
   green CI build. (Before a deploy has done that, the same files are the
   `jarvis-guard-windows` artifact of the "Windows Guard Build" run.)
2. Unzip it on the PC and check the hash (`Get-FileHash .\jarvis-guard.exe`).
3. In an elevated PowerShell in that folder:

```
Unblock-File .\*
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

It asks for the hub address (for example `https://hub.example.com`), a Jarvis
account that may manage devices (admin, father, or `devices.assign`), and the
Jarvis username of the child the PC belongs to. It lists the PC's Windows
administrators: the child's Windows account must not be one of them.

The installer signs in as the parent (that key is never written to disk),
registers this PC as a Windows PC, assigns it to the child, mints a key that
is scoped to this one device, and keeps only that scoped key in
`%ProgramData%\Jarvis Guard\guard.config.json` (SYSTEM + Administrators only).

`%ProgramData%` lets any user create a folder, and its creator owns it, so a
child could make `Jarvis Guard` there first and keep it (read the key, plant
`offline.codes.json` or a cached status). The installer never trusts the
folder as it finds it: a link in its place is removed (never its target);
Administrators take ownership; its ACL becomes SYSTEM + Administrators only
with nothing inherited; and if anything inside is owned by someone else, is a
link, or cannot be read by an administrator, everything in it is deleted and
the installer says what it found. A fresh install (no guard task yet) empties
it too. A reinstall over a folder that passes keeps its files (today's
minutes, the offline-code verifiers and their lockout).

The exe goes to `%ProgramFiles%\Jarvis Guard` (default ACL: everyone may run
it, only administrators may change it). The child's own key and the parent's
key are never stored.

Then it registers the task `Jarvis Guard (service)` (SYSTEM, at startup, no
time limit, runs on battery, restarted if it exits, re-triggered every five
minutes without ever running twice) and the per-sign-in tray Run entry, and
starts the guard. It appears on the Devices page assigned to the child.

Running it again upgrades in place (it stops the guard, replaces the exe and
mints a fresh key). `uninstall.ps1` (also copied into the install folder)
tells the hub first (`guard_missing`, the parents are notified) and refuses
to remove anything while the hub cannot be told, unless `-Offline` is given.

## Layout

```
Program.cs            single entry point: --serve / --tray / --overlay
jarvis-guard.csproj   net10.0-windows, UseWindowsForms, single file, version 0.1.0
JarvisGuard.Core/     config, status parsing, offline evaluator, pipe protocol, hub client
JarvisGuard.Agent/    the SYSTEM guard loop (poll, enforce, usage meter, named-pipe server)
JarvisGuard.Tray/     signage (NotifyIcon)
JarvisGuard.Overlay/  the lock surface (WinForms)
build.ps1             dotnet publish win-x64 single-file into ./dist
install.ps1           elevated installer (register, assign, mint scoped key, install)
uninstall.ps1         elevated removal; reports guard_missing to the hub first
tests/                xunit tests for JarvisGuard.Core (plain net10.0, any OS)
```

## Security model

- This PC holds **only** its own scoped agent key (`wg_…`, minted at install
  time by an admin). It cannot read the child's personal key, cannot edit
  rules, cannot mint keys, and cannot call admin routes - the hub resolves it
  as the assigned child with `is_admin` stripped.
- `%ProgramData%\Jarvis Guard\public\state.json` is the only user-readable file: locked, reason, until,
  theme, hub URL and username. It is written by the agent.
- No password is ever typed on the PC. "Ask a parent to unlock" on the lock
  surface sends a request (how long, an optional why) through the agent to
  the hub with this PC's device key (`POST /api/guard/unlock-requests`); the
  hub binds it to this device. Both parents get a notice in the Jarvis app
  with **Approve N min** and **Deny**, and the child's page in the app shows
  the same request with a choice of length. Only a parent's own session can
  answer (never a device key, never the child); only this PC can read the
  answer (`GET /api/guard/unlock-requests/{id}`). A request expires after ten
  minutes, a newer one supersedes it, an answer is final, and a PC may ask
  once every 30 seconds and ten times an hour.
- The tray and the lock surface reach the agent over the named pipe
  `\\.\pipe\JarvisGuard`: one line of JSON each way per connection
  (`PipeProtocol`), read with a size cap and a time limit on both ends. The
  pipe is owned by SYSTEM; SYSTEM and Administrators have full control;
  Authenticated Users may only read and write data (not create another
  instance, change its permissions or take it over). The agent creates it
  before anything else with `FirstPipeInstance`, so if another program holds
  the name the agent stops, logs it and reports `guard_pipe_taken`; and it
  always keeps a listening instance (the next is made before a client is
  served), so the name is never free to take. The tray and the lock surface
  send nothing until they have checked the pipe is owned by SYSTEM (and, where
  Windows lets them read it, that the serving process runs as SYSTEM). The
  pipe carries commands and verdicts, never the PC's key.
- If the hub answers 401 or 403 the PC says so ("the hub turned this PC's
  key down", `Source: "auth"`): that is a revoked or removed device, not a
  network problem. It keeps enforcing the cached rules meanwhile.
- A config that cannot be read (access denied) or is damaged is reported as
  that, on the console, in `guard.log` and in `state.json`
  (`Source: "config-error"`); only a missing file means "not set up".
- Windows starts that bypass the guard (safe mode) are detected via the
  `SafeBoot\Option` registry key (and `SAFEBOOT_OPTION`) and reported as
  `boot_without_guard` through the existing tamper channel.
- No keystrokes, screenshots, messages or browsing history are collected -
  only foreground app minutes, posted as ordinary phone-shaped usage with
  `source: "windows"`.
- A wipe flag from the hub is ignored, and the ignore is logged. There is no
  wipe in this build.

## Enforcement notes

- `locked` from the hub is authoritative. When the PC locks, the tray raises
  the lock surface at once and the agent signs the child's session out two
  minutes later (time to read why, ask for more time or fetch a parent). A
  child who signs back in while the lock holds gets the lock surface again
  and is signed out after 45 seconds, as often as it takes. The lock is
  enforced every 15 seconds; the hub is asked every 60.
- Sessions of local administrators (the parents' own Windows accounts) are
  never signed out.
- A parent's approval lifts the lock on this PC until the hub's `until`
  (fixed when the parent answered), starting at the hub's `decided_at`, even
  though the hub keeps reporting bedtime or the limit meanwhile, the same as
  the phone. The agent follows a waiting request every few seconds (pipe
  `unlock-request` / `unlock-status`, and its own loop, so it unlocks even with
  the lock surface closed) and unlocks only on the hub's approval of its own
  request; an approval it cannot trust (another request's, no time it was
  given, longer than 240 minutes) never unlocks. The approval carries the
  parents' offline-code verifiers,
  kept in `%ProgramData%\Jarvis Guard\offline.codes.json` (SYSTEM and
  Administrators only), so a parent's offline code (set and seen in the
  parent's Jarvis app) works later with no connection. Five wrong codes lock code entry for ten minutes, the next
  five for twenty, then forty, up to a day; only a good code resets it, and
  all of it is kept on disk by the SYSTEM agent, so closing the lock surface,
  killing the tray or restarting resets nothing. A good code unlocks for ten
  minutes. Both are reported, queued while offline and sent once the hub
  answers (and kept until the hub says it stored them): `offline_unlock`
  with whose code it was, and `offline_code_wrong` with how many.
- A parent's pause set after an unlock on this PC (the rules' `paused_at`)
  overrides that unlock at once and ends it. The pause the unlock was for
  (older than the unlock) does not.
- Offline (no answer, a refused key or a hub error): the last cached rules
  are re-evaluated on the local clock in the hub's order: a parent's pause
  (on, or until a time) locks; bedtime and school lock unless the last answer
  carried time a parent gave (`granted_until`) that has not run out; then the
  day's limit. The limit is counted from what the hub last said was left
  less the minutes this PC has counted since (`limit.snapshot.json`); on a
  new day with no answer yet, the cached rules' limit for that day against
  this PC's own minutes (no bonus or earned time assumed). "Chores first" is
  not enforced offline. With no cache at all only a known limit applies.
- The tray reports to the agent every 30 seconds, even with nothing to
  report. A signed-in child session whose tray has gone quiet for two
  minutes (three after sign-in) means that session's minutes are not being
  counted: the state says so (`usageUnknown`), the hub is told
  (`trayRunning: false` and a `tray_missing` report the parents see), and
  `trayRunning` goes back to true when the tray returns.
- Usage is reported as the day's running total for this PC (kept in
  `usage.today.json`, so a restart carries on), because the hub keeps the
  largest reading per device per day. Within 15 minutes of the limit it is
  reported every poll so the hub locks on time; the tray counts unsent
  minutes so its warnings and "time left" are current.
- Screen-time grants reach this PC through the same `granted_until` the
  phone obeys; the hub's evaluate() suspends bedtime/school while it holds.

## Build in CI

`.github/workflows/windows-build.yml` runs the Core tests, then builds on
windows-latest on every push to main|microservices (and on manual dispatch),
and uploads `jarvis-guard.exe` (single file, compressed, about 50 MB) with
its SHA-256, `install.ps1`, `uninstall.ps1` and this README as the
`jarvis-guard-windows` artifact (kept 30 days). When the `WINDOWS_PUBLISH_URL`
and `INTERNAL_SECRET` secrets are set it also posts the exe to the hub's
`POST /api/app-updates/windows`, which stores it with `windows-version.json`.
Neither secret is set today. What parents download is what
`scripts/deploy_remote.sh` copies from the artifact into
`data/app_updates/windows/` (docs/MOBILE_OTA_UPDATES.md).

Locally: `dotnet test tests` (any OS) and
`dotnet build -p:EnableWindowsTargeting=true` (builds the exe on macOS/Linux too).

## What this is not (deferred)

- No web/DNS filter (Phase P5).
- No signing (the exe is unsigned; SmartScreen and Defender may warn, see
  "Unblock-File" above), no update push channel: upgrade by running the new
  `install.ps1` on the PC.
- No calls from the lock surface (a PC has no phone line).
- No Assigned Access / kiosk (Pro/Education-only opt-in).
- No remote wipe, by design.
- No offline grant tracking: an offline PC cannot honour a grant that has
  not been seen, like a phone that dies before syncing.
