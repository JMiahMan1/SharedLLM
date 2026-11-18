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
  "chores first" lock opens on them); and "A parent can unlock" (a parent's
  sign-in, or a parent's offline code when the hub cannot be reached). It
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
- A parent's unlock uses the parent's own username and password, checked by
  identity (`POST /api/guard/parent-unlock`, five strikes → ten-minute wait),
  and every unlock notifies the other parents, exactly like a phone unlock.
- The tray and the lock surface reach the agent over a named pipe
  (`JarvisGuard`) with an Authenticated Users ACL. The pipe carries commands
  and verdicts, never keys.
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
- A parent's sign-in on the lock surface (`POST /api/guard/parent-unlock`)
  lifts the lock on this PC until the time the hub answers (ten minutes),
  even though the hub keeps reporting bedtime or the limit meanwhile, the
  same as the phone. The answer carries the parents' offline-code verifiers,
  kept in `%ProgramData%\Jarvis Guard\offline.codes.json` (SYSTEM and
  Administrators only), so a parent's offline code works later with no
  connection: five wrong codes wait ten minutes, a good one unlocks for ten
  minutes and is reported (`offline_unlock`) once the hub answers.
- Offline (hub unreachable): the last cached rules are re-evaluated on the
  local clock in the hub's order: a parent's pause (on, or until a time)
  locks; bedtime and school lock unless the last answer carried time a parent
  gave (`granted_until`) that has not run out. The daily limit and "chores
  first" are not enforced offline (fail open). With no cache at all it fails
  open.
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
