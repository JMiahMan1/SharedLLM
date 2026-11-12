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
- `--overlay` (launched by the tray) - the lock surface: full screen signage
  with the reason and a parent sign-in. It shows state; it does not hold the
  lock (the session sign-out does).

The hub, identity and MDM stay exactly as they are for every other device.
The only hub changes behind this client are the same scoped-key enrolment the
phones use.

## Install (the intended path)

```
.\install.ps1   # elevated; asks for the hub URL (for example https://ai.example.com),
                # a Jarvis admin/father/mother username + password, and the
                # child username to assign the PC to
```

The installer signs in as the admin (that key is never written to disk),
registers this PC as a Windows PC, assigns it to the child, mints a key that
is scoped to this one device, and keeps only that scoped key in
`%ProgramFiles%\Jarvis Guard\guard.config.json` (ACL'd to SYSTEM +
Administrators). The child's own key and the admin's key are never stored.

Then it creates the SYSTEM boot task `Jarvis Guard (service)` and the
per-sign-in tray Run entry, and starts the guard. It appears on the Devices
page assigned to the child - Devices, pause/resume, rules, same as a phone.

## Layout

```
Program.cs            single entry point: --serve / --tray / --overlay
jarvis-guard.csproj   net10.0-windows, UseWindowsForms, single file, version 0.1.0
JarvisGuard.Core/     config, status parsing, offline evaluator, pipe protocol, hub client
JarvisGuard.Agent/    the SYSTEM guard loop (poll, enforce, usage meter, named-pipe server)
JarvisGuard.Tray/     signage (NotifyIcon)
JarvisGuard.Overlay/  the lock surface (WinForms)
build.ps1             dotnet publish win-x64 single-file into ./dist
install.ps1           elevated installer (register → assign → mint scoped key → install)
```

## Security model

- This PC holds **only** its own scoped agent key (`wg_…`, minted at install
  time by an admin). It cannot read the child's personal key, cannot edit
  rules, cannot mint keys, and cannot call admin routes - the hub resolves it
  as the assigned child with `is_admin` stripped.
- `public\state.json` is the only user-readable file: locked, reason, until,
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

- `locked` from the hub is authoritative. The agent signs out interactive
  console sessions once per lock transition (`WTSLogoffSession`). A child who
  signs back in while still in a locked window signs out again on the next
  poll - the phone's behavior, one step louder.
- Usage is reported as the day's running total for this PC (kept in
  `usage.today.json`, so a restart carries on), because the hub keeps the
  largest reading per device per day. Within 15 minutes of the limit it is
  reported every poll so the hub locks on time; the tray counts unsent
  minutes so its warnings and "time left" are current.
- `until` is shown but not enforced client-side beyond the hub's answer on
  the next poll (60 s).
- Screen-time grants reach this PC through the same `granted_until` the
  phone obeys; the hub's evaluate() suspends bedtime/school while it holds.

## Build in CI

`.github/workflows/windows-build.yml` builds on windows-latest on every push
to main|microservices (and on manual dispatch), publishes
`dist/jarvis-guard.exe` as the `jarvis-guard-windows` artifact, and - when
`WINDOWS_PUBLISH_URL` and `INTERNAL_SECRET` secrets are configured - posts
the exe to the hub's publish endpoint exactly like the APK flow does. PR
builds skip the publish step.

## What this is not (deferred)

- No web/DNS filter (Phase P5).
- No signing (the exe is unsigned; SmartScreen will warn), no update push channel.
- No Assigned Access / kiosk (Pro/Education-only opt-in).
- No remote wipe, by design.
- No offline grant tracking: an offline PC cannot honour a grant that has
  not been seen, like a phone that dies before syncing.
