# Windows 10+ lock screen - Jarvis for family PCs

Phase XR of the device-management plan: a Windows 10/11 member of the Jarvis family
hub with the same parent-time-management capabilities the Android app carries today.
It is the same control (a parent times how long a child may use a device), on the one
family machine type that does not have it yet.

The gateway stays the one source of truth for rules, the identity service stays the
one source of truth for devices, and the agent is a dumb-but-honest enforcement
endpoint that asks the server what to do and does exactly that.

## What this is, and what it deliberately is not

This is a **managed family PC** agent: the parent owns the machine, installs the
agent elevated, and enrolls the device into the family hub the same way a child's
phone is enrolled today. The design commitment is *managed-device signage* - the
device is visibly managed. That means:

- Installation requires a parent's Windows administrator account. It is not a
  covert install, and cannot be done from an unprivileged account.
- The machine shows that it is managed: a "This PC is managed by the family hub"
  notice at install time, on the lock/overlay screens, in the MDM device page, and
  as a persistent system-tray shield while the agent runs.
- Kids have no off switch, but any local administrator can remove it - because
  administrators own the machine. There is no anti-uninstall bypass for the parent's
  own tools; tamper handling is *tamper-evident* (the parent is told), not covert.
- The agent collects no keystrokes, screenshots, messages, or browsing history, and
  transmits nothing about content. It reports: presence (screen on/off), foreground
  app minutes, and the rule decisions it was told to enforce.
- Out of scope by design: remote full-disk wipe (a shared family PC is not a kiosk;
  the v1 plan adds no wipe), covert microphone/camera capture, keylogging, and any
  evasion of the parent administrator's visibility of their own machine.

Safe mode is the residual hole an unprivileged-but-logged-in child can reach on
Windows Home; there is no device-owner equivalent. The plan treats safe mode as
detectable (boot-count + wallclock anomaly) and reports it as tamper; it does not
pretend to close it.

## Authority model and enrollment

| Aspect | Design |
|---|---|
| Install | Parent runs the CI-built `jarvis-guard.exe` elevated. It self-installs: copies to `%ProgramFiles%\Jarvis Guard`, registers a SYSTEM Windows service + a boot scheduled task, locks down ACLs so non-admins cannot modify or delete, and reboots nothing. |
| Enrollment | Same as the phone: on first run the parent enters the **family pairing code** generated from the Jarvis hub (10-minute, single-use). The agent registers itself via `POST /api/user-panel/devices/register` with a stable machine GUID (`HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`), reports `kind:"windows-pc"` (new enum value - see § Server work) and its capabilities, and is thereafter visible on the Devices page with guard state. |
| Update | The MDM "Push update" flow delivers the new exe; agent verifies signature + SHA-256, swaps, restarts service, reports new build. |
| Removal | Admin-only (`sc delete "Jarvis Guard"` / uninstaller exe). Removing it reports `guard_missing` via the same `POST /api/guard/tamper` channel the phone uses. |

The child's account on the machine must be a **standard user**. The install-time
check refuses to complete on a machine whose non-owner accounts are administrators
and writes a loud warning on the Devices page.

## Architecture

```
                 ┌───────────────────────────────┐
   Device page → │  gateway (python, FastAPI)    │  pause/resume, rules, requests,
   push notices  │  services/gateway/mdm.py      │  bonus, approvals, wipe (n/a v1)
                 │  services/gateway/main.py     │  status aggregate:
                 └───────────────┬───────────────┘  GET /api/screen-time/{u}/status
                                 │ HTTPS, API-key bearer
                 ┌───────────────┴───────────────┐
                 │  jarvis-guard service (SYSTEM) │  polls 60 s (+ push refresh)
                 │  Windows service, boot task     │  cache prefs for offline
                 └──┬─────────┬───────────┬──────┘
        tray shield │         │           │  informational overlay + signage;
        signage     │   usage │      real lock = session sign-out
                    │   meter │      (rear-guard rechecks at logon)
                    ▼         ▼           ▼
             tray app    foreground    GuardSurface UI
```

### Client components

1. **Jarvis Guard service** (Windows service, SYSTEM). Boots at startup, holds the
   polling loop, evaluates rules locally on its own clock, enforces locks by
   signing out the child's session when a session-level rule engages, and
   launches the informational overlay process on the interactive session for
   signage and the parent unlock UI. C# / .NET 10, single-file self-contained
   win-x64 publish - no runtime to install (net8.0 reached EOL in November
   2026; net10.0 is the LTS choice).
2. **Tray shield + signage** (`jarvis-tray.exe`, per-user from the service, or a
   shell start entry). Small shield icon; hover = "Managed by the family hub";
   right-click → open Jarvis, ask a parent, parent-unlock. No off switch.
3. **Lock surface** (`jarvis-lock.exe`). A per-monitor, topmost, non-draggable
   informational overlay that explains why the machine is locked ("Screen time is
   up for today", "Bedtime"), offers "Ask for more time", "Message a parent" and
   the parent sign-in fields, and grabs keyboard focus for the parent unlock
   fields only. **The real enforcement is at the session level, not the pixel
   level**: when a lock engages, the service marks the session as "screen up"
   and Windows is told to sign the child's session out (`WTSLogoffSession` /
   `shutdown -l` style action), backed by a boot-time task that re-checks the
   rules before any interactive session starts for that account. A child cannot
   keep using software that session-out already closed. The overlay is the
   courtesy surface that explains and collects the unlock; it is not the lock.
   Beta builds may add a borderless topmost lock for a visible surface, but it
   must never be the only thing keeping the child out - OSS and Microsoft
   prior art (Family Safety, all shipped parental suites) fail on real PCs
   exactly this way: the *session* is the enforcement surface; the overlay is
   for communication. For bedtime/school/limit reasons the policy is
   *fail-closed*; for app-block failures it is *fail-open* (a wedged component
   must never brick the family PC).
4. **Rules engine** - a port of `services/common/screen_time.py`'s `evaluate()` to
   a small C# module, driven by **mirrored JSON fixtures** extracted from the
   Python unit tests so the Windows verdict is bit-identical to the phone's
   (bedtime windows incl. overnight, `reason`, `until`, `next_lock_at`, daily vs
   weekend vs per-day limits, granted-time suspension of sleep locks).
5. **Usage meter** - foreground process (`GetForegroundWindow` → exe name) polled
   every ~2 s + `GetLastInputInfo` idle detection; cumulative minutes per exe per
   day, posted every 30 min via `POST /api/usage` with `device_key` and
   `source:"windows"` (night idle stretches → `POST /api/usage/night`). It does not
   attempt UsageStats-grade per-app attribution; best-effort, visibly labeled
   "approximate" on the child's time page.
6. **Web filter** - **AdGuard Home** running as a Windows service on the machine
   itself is the recommended backbone (Technitium DNS is a close, more regex-rich
   runner-up; blocky a distant third). The router/DNS pin (`netsh advfirewall`
   rules blocking outbound 53/853 for all non-AdGuard traffic + the adapter
   pinned to 127.0.0.1) is the anti-bypass layer, and the following browser
   policy keys are mandatory, because Chrome/Edge/Firefox DoH would silently
   neutralize the filter: Chrome/Edge HKLM `URLBlocklist`/`WebsiteFilter`
   (or per-user HKCU), `DnsOverHttpsMode=off`, `ForceBingSafeSearch`=2 /
   `ForceGoogleSafeSearch=true`; Firefox gets a `policies.json` next to
   `firefox.exe` and is not covered by Chrome/Edge policy keys. HaGeZi's
   DoH-bypass list goes in AdGuard Home. Wheel in a report-only soak before any
   enforcement: two weeks of AGH query-log review (domain-only logging),
   whitelisting the CDNs the child's software actually needs (Store/Xbox/
   streaming launchers - most share CDN domains; oisd small / HaGeZi PRO +
   StevenBlack social/gambling + a small hand-curated ~200-domain list is the
   recommended category set). Enforcement flips on after the parent reviews
   false positives. No URL logging; the resolver filters domains only. GPL/AGPL
   code is consumed as an external daemon (a separate service executable), not
   vendored into the .NET build.
7. **Offline** - the service caches the last status JSON (`guard_status`) exactly
   like the phone does; bedtime/school still lock from the local clock; spends
   gathered while offline are reported on reconnect.
8. **Auto-update** - MDM push → exe downloads → signature verify (Authenticode,
   timestamped) → atomic swap → service restart. Fails closed to the old build.

### Server work

No new rules engine - reuse everything the phone uses, add Windows deltas:

| Piece | Change |
|---|---|
| `ScreenTime` status poll | Reuse `GET /api/screen-time/{u}/status?device_key=` verbatim. |
| Device registration | Add `windows-pc` to the device-kind enum (identity `Device` model + `/api/user-panel/devices/register` validation) + migration of the CHECK constraint. |
| Capabilities | Windows reports `guard:{serviceRunning, lockCapable, trayRunning, webFilterMode: off\|report\|enforce, version, reportedAt}`; Devices page renders a third row next to phone/tablet. |
| Usage ingest | `POST /api/usage` already accepts `device_key`; add `source:"windows"` acceptance (no schema change if none needed; keep payload as-is). |
| Tamper taxonomy | Reuse `guard_tamper` events (`uninstall_attempt`, `safe_mode_boot`, `admin_revoked`, `service_stopped`, `clock_skew`) → devices page + `family_safety` notices, same as phone. |
| Ask-to-time flow | Unchanged: `/api/screen-time/requests` + Approve/Disapprove push; granted minutes extend today's budget and suspend sleep locks (`granted_until`). The lock screen buttons call the same endpoints. |
| Bonus / earn / chores | Unchanged: `/api/screen-time/{u}/bonus`, `EARN_KEYS`, chore-check approvals - Windows inherits step/exercise/chore earning automatically. |
| Parent unlock on device | Unchanged: `/api/guard/parent-unlock` (parent Jarvis login, 5-fail → 10-minute wait), PBKDF2 offline codes cached locally (10-minute lifts) reported later via `offline_unlock`. |
| MDM | `Devices.tsx` gets a "Windows PC" section (Lock now / Lift / Ask log / Tamper log / Update). **No wipe button** for windows-pc v1. |
| Publish endpoint | New `POST /api/app-updates/windows` (internal-secret, mirrors the APK publish step): stores `data/app_updates/jarvis-guard.exe` + `version.json`; the exe is not served to children, only fetched by enrolled machines. |

### CI - how the exe gets built and shipped

New workflow **`.github/workflows/windows-build.yml`** (models `android-build.yml`):

1. `windows-latest` runner, `.NET 10` SDK (setup-dotnet@v4).
2. `dotnet restore` → `dotnet publish -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -p:Version=<version>`.
3. Publish version source of truth: a new `services/ui/android`-style version constant in
   `agent/JarvisGuard/JarvisGuard.csproj` (`<Version>`), bumped with the release train;
   computed `FileVersion`/`AssemblyInformationalVersion` derive from it.
4. Signing: two realistic options, in preference order -
   **Azure Artifact Signing** (~$9.99/mo for Basic, individual or org
   validation; verify the price and country eligibility before budgeting) and
   **SignPath Foundation** (free, manual per-release approval, publishes under
   SignPath's name) - and both are materially better than an EV cert, which no
   longer skips SmartScreen. Timestamp with an `http://` RFC 3161 URL
   (`http://timestamp.acs.microsoft.com` or `timestamp.digicert.com`, `/td
   SHA256`); HTTPS timestamps are rejected by signtool. If neither pipeline is
   set up yet, publish the build **unsigned with a loud warning** and require
   the parent to acknowledge it in install notes (never a silent bypass).
   `attest-build-provenance` + SBOM ride along on every build.
5. Compute SHA-256, `upload-artifact` (30 d retention), then on `microservices` push
   only: POST to `PUBLISH_WINDOWS_EXE_URL` (mirrors `PUBLISH_URL` APK step) with
   `X-Internal-Secret`; server stores exe + writes `version.json {version, sha256, notes}`.
6. PRs build and test (unit tests for the ported rules engine run on Linux too) but
   never publish.
7. The server-side gate is the same GitHub-checks discipline as everything else: the
   publish endpoint only accepts uploads from the workflow's OIDC/secret.

### Enforcement map: feature parity

| Phone (today) | Windows v1? | Mechanism |
|---|---|---|
| Bedtime / school windows (overnight) | ✅ | service clock + session sign-out enforcement (+ rear-guard at logon); overlay carries the message |
| Daily / weekend / per-day limits | ✅ | ported `evaluate()` + 30-min usage sync |
| Pause / resume / Lock now / Lift | ✅ | poll + tray + overlay; `paused_until` |
| Ask for more time | ✅ | lock-screen buttons → `/api/screen-time/requests` → push Approve/Disapprove |
| Message a parent | ✅ | `/api/guard/message` (same Nextcloud Talk 1:1 rooms) |
| 10/5-minute warnings | ✅ | tray dialog at T−10 and T−5 min with time used/left and "Ask for more time"; balloon at T−1 min (`LockWarning`, same rules as the phone) |
| Check time used | ✅ | tray tooltip ("45m left · used 1h 15m"); one click on the shield opens the time panel |
| Bonus minutes / earned time (steps/exercise/chores) | ✅ | server-side; PC inherits automatically (earns on the phone, spends on the PC) |
| App allowlist / per-exe minute budgets | ✅ (coarse) | exe-path + window-title match; kill non-allowed process + overlay on violation; install approvals via "new shortcut in Start Menu" scan |
| Always-allowed apps | ✅ | config: browsers? No - browsers are filtered, dialer-analogs (Teams/Discord for help) allowed by default |
| Install approval for new apps | ⚠️ phase 5 | scan Start Menu + `Program Files` daily + on login; unmatched exe → overlay "Ask a parent first" until approved |
| Web filter (categories, report → enforce) | ✅ phase 4 | AdGuard Home on-machine + DoH-off browser policies + firewall pinhole, as above |
| VPN/proxy/DNS bypass blocking | ✅ | block outbound 53/853/443-to-known-VPN-endpoints for user accounts; report `bypass_configured` as tamper |
| Permission gating (camera/mic/etc.) | ❌ v1 | Windows per-app capability is not a hard block surface without enterprise GPO; tray-only "used the webcam" notices are a possible phase 6 item |
| Remote wipe | ❌ ever (this device class) | deliberately omitted |
| Safe mode | ⚠️ detect, report | `BOOT_COUNT` via `SYSTEM\Setup` non-volatile key + service-start gap detection; reported as tamper |
| Clock tamper | ⚠️ detect, report | unprivileged clock rollback detection (monotonic boot time vs wallclock drift); reported as tamper |
| Kiosk / COSU lock task | ⚠️ phase 5 | Windows **Assigned Access** (single-app kiosk) on Pro/Enterprise; not on Home |
| Device owner (real OS lock) | ❌ impossible | no DPM equivalent; session sign-out is the enforcement, fail-closed for sleep reasons |
| OTA updates | ✅ | MDM push as above |
| One-minute-before-lock tray heads-up | ✅ | reuses `next_lock_at` |

### Risks and edge cases

- **Family Safety failed on account-binding, so this design must not.** Microsoft's
  own parental offering broke at scale (June 2025 Chrome block, December 2025
  mass policy-sync outage, dead `WpcMonSvc` with no UI warning) precisely
  because enforcement clung to accounts, browsers-by-name and cloud sync.
  This plan's lock is local: the service signs the session out on the local
  clock; approvals arrive via the family hub but the sleep policy locks with
  the server unreachable. Assigned Access is documented as Pro/Edu-only
  "school mode", not Home's path. Windows Home is the baseline target.
- **Fail-open vs fail-closed:** sleep-policy failures lock; app-block failures open
  (a wedged overlay must not brick the machine). Unremovable from child accounts:
  service runs SYSTEM; child has no service-control rights; tray is not closable by
  the child's session token.
- **Local admin child account** = plan defeats itself. Install-time gate + page
  warning; MDM page shows "child is admin on this PC - fix this first" in red.
- **Home vs Pro/Edu:** Assigned Access and AppLocker are Pro/Enterprise features;
  v1 works on Home with the SYSTEM service + session-level sign-out enforcement. Pro adds kiosk mode + update
  policy control (phase 5).
- **RDP / other OS boot:** a child with another OS boots past everything. Document,
  don't solve.
- **Antivirus interference:** signed exe + standard service registration; document
  an AV allowlist step if the build trips SmartScreen/Defender.
- **Parent unlock brute force:** same PBKDF2 + 5-fail/10-minute rule as phone;
  offline codes are mac-only? No - same offline-code set, verified locally.
- **Time source:** bedtime uses NTP-synced wallclock; Windows requires admin to
  change the clock, which closes the common bypass.
- **Monitoring ethics:** tray shield is always present, guard logs nothing about
  content, and the Privacy section of the install consent screen says exactly that.

### Phases

- **P0 - server deltas (1 PR):** `windows-pc` device kind + registration migration,
  capabilities payload + Devices page render row, `windows` usage source
  acceptance, publish endpoint + `data/app_updates` plumbing, boot-time migration.
  Verify: registered test device appears with guard state pending; notices ack
  round-trip.
- **P1 - skeleton (1 PR):** .NET 10 service skeleton, enrollment code flow, 60 s
  poll, cached status, tray shield + managed signage, uninstall-reports-tamper.
  Verify: enrolled Windows arm64? no - x64. `windows-latest` CI green; unit tests.
- **P2 - rules + lock (1 PR):** ported `evaluate()` with mirrored JSON fixtures,
  session-level sign-out enforcement (with rear-guard at logon), fail-closed
  sleep policy, informational overlay + one-minute heads-up, usage
  ingest, 30-min posting. Verify: bedtime engages overnight on demand, lifts on
  `paused_until`, fixtures pass.
- **P3 - ask-to-time + approvals (1 PR):** lock-screen Ask / Message / parent
  sign-in; approvals round-trip (`guard_decide` push); offline codes.
  Verify: end-to-end request → push → approve → PC unlocks.
- **P4 - web filter (1 PR):** report-only → enforce flip; firewall pinhole; bypass
  tamper reporting. Verify: listed domains blocked, allowlist unaffected, no URL
  hits logged.
- **P5 - app control (1 PR):** exe budgets, new-app scan + approval, Pro/Edu kiosk
  (Assigned Access). Verify: unapproved new exe overlays instead of running.
- **P6 - CI exe + rollout (1 PR):** `windows-build.yml`, signing secrets, publish
  endpoint live, docs + parent-facing setup guide; delete nothing of the phone flow.
- **Then:** hardware-in-the-loop soak - one week on a real family PC, parent reviews
  tamper report log before calling v1.0.

### Prior art to mine (with licensing)

- **Copy, with notices:** vigil (MIT; fail-closed 30 s arbitration pattern),
  robertpin/WindowsParentalControl (MIT; SYSTEM service, session logoff, Inno
  installer with `sc failure` recovery), SecureAppLocker (MIT; drop-in
  `EnforceSecurityACLs()` - SYSTEM-only ACLs on the install dir), ActivityWatch
  (MPL-2.0; foreground process tracking loop - file-level copy of the tracking
  loop, not the app).
- **Consume as tools, never vendor:** AdGuard Home / Technitium (GPL-3.0) run as
  external daemons.
- **Ideas only, NOASSERTION/unlicensed:** MonitorAndControl, K9 - read for
  design, do not copy code.
- **Do not build on:** Microsoft Family Safety (account-binding failures),
  OpenDNS FamilyShield (Cisco Umbrella EOL, announced 2026-09-01), K9
  (unmaintained).

## Open questions for the household, not the code

- Whether the family PC is used for schoolwork that needs a browser - if so, the
  browser is never app-blocked, only web-filtered.
- Whether the enrollment code should be single-use machine-bound (current plan) or
  reusable for multiple household PCs.
- Whether to include tablet-mode/whimsical "Jarvis dog" tray animations (cute) or
  the somber shield (minimal).


## Implementation status (uncommitted, 2026-11-09)

Client code in `agent/jarvis-guard/` (single-file .NET 10, BCL-only):

- `JarvisGuard.Agent` - SYSTEM process under a boot task (not a Windows
  service control registration): 60-second poll of
  `/api/screen-time/{u}/status?device_key=`, hub decision authoritative;
  offline bedtime/school re-evaluation against the local clock from the
  last cached rules (fail closed for those two reasons only); signs
  interactive console sessions out on lock transitions
  (`WTSLogoffSession`); posts phone-shaped usage with `source:
  "windows"` every 30 minutes; detects safe-mode boots (SafeBoot\Option,
  SAFEBOOT_OPTION) and reports `boot_without_guard`; named-pipe server
  for the tray/overlay (Windows ACLs: Authenticated Users, pipe carries
  locked/reason/parent-unlock verdicts, never the API key).
- `JarvisGuard.Tray` - the always-on managed-device signage (NotifyIcon):
  shield tooltip, open the hub, ask a parent for more time, a parent can
  unlock, about. Reads only `public\state.json`.
- `JarvisGuard.Overlay` - the lock surface: full-screen signage (reason +
  until + ask/message/parent-unlock). Enforcement is the session sign-out;
  the overlay communicates. User-close is vetoed while locked.

Hub deltas shipped with it: `windows-pc` device kind in identity (device
self-registration, `SELF_REGISTER_KINDS`), the Devices page renders and
assigns Windows PCs (owner select, no phone wipe button), and
`POST /api/app-updates/windows` (internal-secret upload + SHA-256; no
child-facing GET advertisement).

What's verified: `npm run typecheck` clean; deviceKind unit tests green
(18/18, windows-pc covered). What's NOT verified: the agent itself has
never been built - the dev machine has no .NET SDK. First build expects a
repair round. Remaining phases P2 (logon rear-guard edge cases), P4
(web filter), P5 (app control), P6 (signed CI exe + publish flow) are
untouched by design.

Install: `agent/jarvis-guard/install.ps1 -HubUrl <hub> -Username <child>
-ApiKey <child key>` run elevated on a PC whose child account is a
standard user.
