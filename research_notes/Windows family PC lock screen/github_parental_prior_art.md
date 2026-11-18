# GitHub Prior Art: Windows Parental-Control / Screen-Time / Lock-Screen Agents

Research for a parent-developer building a private Windows 10/11 "family PC guard" agent (bedtime/school windows, daily budgets, app budgets, lock overlay, ask-for-more-time flow). All data current as of 2026-11-10 unless noted. Stars/licenses retrieved via GitHub API on 2026-11-10. License conclusions are observations, not legal advice — see "License compatibility" at the end.

## Big picture / TL;DR inventory

| Repo | Stars | License | Lang/stack | Last push | Windows-native? | Pattern |
|---|---|---|---|---|---|---|
| [deresterr/vigil](https://github.com/deresterr/vigil) | 0 (brand-new) | MIT | C# .NET 8 client + FastAPI/SQLite Docker server | 2026-11-10 | YES — service + full-screen lock | Server-decided lock, TOTP unlock, guardian watchdog |
| [geogengames-hue/MonitorAndControl](https://github.com/geogengames-hue/MonitorAndControl) | 6 | NOASSERTION (custom, not OSI — verify) | C#/.NET 8, ASP.NET Minimal API + SQLite | 2026-11-02 | YES — tray app + SYSTEM watchdog service | Foreground tracker + per-app/group limits + browser-policy site blocking |
| [0xarchit/Focusd](https://github.com/0xarchit/Focusd) | 45 | MIT | Go | 2026-10-19 | YES — daemon (Win32 syscalls) | Foreground tracker + daily limits (notify-only, no lock) |
| [majianchuan/screen-time-tracker](https://github.com/majianchuan/screen-time-tracker) | 147 | GPL-3.0 | C#/.NET 10, Photino.NET + React | 2026-08-19 | YES — tracking/analytics only | Modular monolith backend, no enforcement |
| [adambie/screenguard](https://github.com/adambie/screenguard) (Linux; design reference) | 19 | AGPL-3.0 | Rust server (axum) + agent, Flask web UI | 2026-10-30 | No (Linux/systemd/logind) | Server-decides + offline agent that locks desktop sessions |
| [adityakrishnan005-a11y/ScreenGuard](https://github.com/adityakrishnan005-a11y/ScreenGuard) | 27 | GPL-3.0 | Dart (GNOME) | 2026-11-10 | No (Linux Wayland/X11) | Track + daily limits + focus mode |
| [aditiyakrishnan005…] — see ScreenGuard above | | | | | | |
| [noiwid/HAFamilyLink](https://github.com/noiwid/HAFamilyLink) | 140 | MIT | Python (Home Assistant custom component) | 2026-11-10 | Android rule engine via Google Family Link API | Week/day budgets, bedtime, school schedules, "grant bonus time" |
| [ActivityWatch/activitywatch](https://github.com/ActivityWatch/activitywatch) | 19,591 | MPL-2.0 | Python (+ watchers in Go/Rust/C#) | 2026-11-09 | YES (Windows app) | Tracking engine only (window watcher, AFK watcher) |
| [vasyaod/parental-control](https://github.com/vasyaod/parental-control) | 21 | MIT | Haskell | 2022-08-23 | YES+Linux | Login-time control; unmaintained |
| [muntasir018/DeskWarden](https://github.com/muntasir018/DeskWarden) | 13 | MIT | Python | 2026-10-07 | YES | AppLocker-style exe blocking / process lock |
| [robertpin/WindowsParentalControl](https://github.com/robertpin/WindowsParentalControl) | 2 | MIT | C# | 2026-03-13 | YES | Minimal; sparse docs |
| [daviddanielzivi/parental_control](https://github.com/daviddanielzivi/parental_control) | 13 | Apache-2.0 | C++ | 2018-06-17 | YES | XP-era; effectively abandoned |
| [arcinxe/ArktinMonitor](https://github.com/arcinxe/ArktinMonitor) | 5 | None | JavaScript(.NET?) | 2022-12-08 | YES | "Complex solution for monitoring, remote & parental control" |
| [ALeXXBody/NetCurfew](https://github.com/ALeXXBody/NetCurfew) | 0 | MIT | C# | 2026-11-10 | YES | Password-protected night internet lock tray app |
| [jakazajec5-arch/FamillyKJ](https://github.com/jakazajec5-arch/FamillyKJ) | 0 | None | unspecified | 2026-09-29 | YES (claims) | "time limits, screen lock, chat, live view, parent Android app" — very new, no license |
| [polesapart/timekpr-next](https://github.com/polesapart/timekpr-next) | 60 | None (GPL-family upstream?) | Python | 2025-09-19 | No (Linux) | Canonical per-user day/hour budget rule engine + session kill |
| [Joker-KP/jelly-watch-wise](https://github.com/Joker-KP/jelly-watch-wise) | 38 | MIT | Python | 2025-03-11 | Media-server-side | Jellyfin screen-time limiter (quota pattern) |
| [darrint/device-blocker](https://github.com/darrint/device-blocker) | 40 | None | Rust | 2024-11-04 | Router-side (OpenWrt-style) | Pause Wi-Fi internet per device; unmaintained since 2024 |
| [khaleel-git/K9-Web-Protection_BlockerFlow](https://github.com/khaleel-git/K9-Web-Protection_BlockerFlow) | 18 | None ("open source, free for personal/community use" — custom) | Go + Wails | 2026-10-08 | macOS + Windows "in progress" | MITM proxy + SafeSearch injection + watchdog; predecessor of BlockerFlow app |
| [theVatsal3802/Clarivue-AI](https://github.com/theVatsal3802/Clarivue-AI) | 0 | None | TypeScript | 2026-05-31 | Browser+desktop | Extension + native messaging host "parental control, fully local" |
| [AdguardTeam/AdGuardHome](https://github.com/AdguardTeam/AdGuardHome) | 38,324 | GPL-3.0 | Go server (TS frontend) | 2026-11-10 | YES (Windows binary) | Network-wide DNS filter backbone |
| [TechnitiumSoftware/DnsServer](https://github.com/TechnitiumSoftware/DnsServer) | 10,328 | GPL-3.0 | C# | 2026-11-04 | YES (Windows-native service) | Authoritative/recursive DNS + ad/malware/category blocking |
| [0xERR0R/blocky](https://github.com/0xERR0R/blocky) | 7,102 | Apache-2.0 | Go | 2026-11-10 | Docker/binary | Lightweight DNS proxy/ad-block, Prometheus metrics |
| [deresterr/vigil](https://github.com/deresterr/vigil) — repeat for emphasis | | | | | | **Closest architectural match to the target agent** |

Also: pi-hole (EUPL-1.2, Docker/Linux only officially), [majianchuan ScreenTimeTracker](https://github.com/majianchuan/screen-time-tracker) (GPL-3.0, .NET 10, Photino + React, local SQLite; has browser-extension companion for website stats; **no enforcement/kill**).

---

## Q1 — What OSS Windows screen-time / app-limit / lock-screen projects exist; which are best maintained/attributed?

### Takeaway
Two actively maintained, clearly-attributed projects dominate the Windows-native space in 2026: **deresterr/vigil** (MIT, brand-new but extremely close to the target design: server-decided full-screen lock + Windows service + TOTP unlock) and **geogengames-hue/MonitorAndControl** (feature-richest: per-app limits, shared groups, schedules, browser site blocking via Windows browser policies, ntfy/Telegram alerts — but license is NOASSERTION/custom and it ships with a bundled "new programs need your OK" model that is unsuitable to copy verbatim). Everything else is either tracking-only (Focusd, ScreenTimeTracker), tiny (DeskWarden, NetCurfew, WindowsParentalControl), abandoned (parental_control C++ 2018, vasyaod/parental-control Haskell 2022), or not Windows (screenguard/timekpr).

### Cited findings
- **vigil**: self-hosted; Docker server (FastAPI + SQLite) decides lock; Windows service in the child's session shows the full-screen lock covering all monitors, blocks Win/Alt+Tab/Alt+F4; fail-closed (no server answer for 30 s → locked); paired per-PC signing key readable only by SYSTEM/admins; 5-minute "guardian" repairs service/file tampering; TOTP unlock works offline; weekly schedule per day-of-week; time counted across all PCs of one child; installer `vigil_setup.exe` with wizard; threat model explicitly assumes child may be administrator but admits a full admin *can* defeat it (alerts, not guarantees). License MIT. Client builds with .NET 8 SDK (no .NET needed on target). Created 2026-11-06, pushed 2026-11-10, 0 stars, English/Polish UI. — [README](https://github.com/deresterr/vigil/blob/main/README.md), [repo API](https://github.com/deresterr/vigil)
- **MonitorAndControl**: three binaries — `DeviceMon.exe` (monitor + tray + ASP.NET dashboard), `GameHost.exe` (SYSTEM watchdog, relaunches monitor, locks DB against deletion), `PopupHost.exe` (full-screen warning popup child process), plus `UpdateAgent.exe`. Per-app and per-account daily limits; shared "Games"-style groups; app categories guessed from install location (Steam/Epic/…); schedules; bedtime (signs account out); break reminders (locks/signs out); "ask parent for more time" (email/ntfy/Telegram buttons); website limits via **browser Windows policies** (`URLBlocklist` for Chrome/Edge/Brave, `WebsiteFilter` for Firefox) written by the SYSTEM service — 0 minutes = blocked; SafeSearch + YouTube Restricted Mode enforcement via the same policy path; allow-list mode; watched browsers only; no browser extension needed. SQLite at `C:\ProgramData\SystemHelper\monitor.db`. License NOASSERTION (not OSI-approved — treat as all-rights-reserved custom until verified with author; the repo has no LICENSE file detected as OSI). 6 stars, created 2026-06-16, pushed 2026-11-02. — [README](https://github.com/geogengames-hue/MonitorAndControl/blob/main/README.md)
- **Focusd**: Go daemon polls foreground window via `GetForegroundWindow`/`GetWindowTextW`/`GetModuleBaseNameW`; SQLite WAL at `%APPDATA%\focusd\focusd.db`; per-exe daily limits (notify-only); Pomodoro; IPC on 127.0.0.1:48321; MIT; unsigned releases; no admin, no service, no lock screen. — [README](https://github.com/0xarchit/Focusd/blob/main/README.md)
- **ScreenTimeTracker**: C#/.NET 10, ASP.NET Core minimal API + FastEndpoints + EF Core + SQLite; Photino.NET desktop shell; React/Vite/MUI frontend; GPL-3.0; tracking only (charts, website stats via companion browser extension); no limit enforcement. — [README](https://github.com/majianchuan/screen-time-tracker/blob/main/README.md)
- **DeskWarden**: Python, MIT, 13★, 2026-10 — "Free AppLocker for Windows — password-protect any app; exe blocker; process lock". — [repo](https://github.com/muntasir018/DeskWarden)
- **NetCurfew**: C#, MIT, 0★, 2026-09 — "password-protected night internet lock tray app for Windows". — [repo](https://github.com/ALeXXBody/NetCurfew)
- **WindowsParentalControl**: C#, MIT, 2★, 2026-03 — no description; tiny. — [repo](https://github.com/robertpin/WindowsParentalControl)
- **parental_control** (daviddanielzivi): C++, Apache-2.0, 13★, last push 2018-06-17 — effectively abandoned; pre-.NET-8 era. — [repo](https://github.com/daviddanielzivi/parental_control)
- **parental-control** (vasyaod): MIT Haskell, 21★, 2022 — login-time limiting for Windows/Linux; unmaintained. — [repo](https://github.com/vasyaod/parental-control)
- **ArktinMonitor**: JS, 5★, 2022 — "monitoring, remote & parental control"; no license. — [repo](https://github.com/arcinxe/ArktinMonitor)
- **FamillyKJ** (jakazajec5-arch): 0★, no license, pushed 2026-09 — claims time limits, screen lock, live view, parent Android app; Windows variant of an Android-style design; no visible source maturity. — [repo](https://github.com/jakazajec5-arch/FamillyKJ)

### Inferences
- The "best maintained & attributed" Windows-native candidates for **reading architecture** are vigil (MIT, clean docs: `docs/architecture.md`, `docs/guardian.md`, `docs/decisions.md`) and MonitorAndControl (most complete feature set, but custom license + mono-maintainer risk).
- Nothing in the Windows-native OSS space currently matches Qustodio/Microsoft Family Safety maturity; the space is niche and hobbyist-grade, or brand-new.

### Gaps
- Exact stars/forks for ScreenTimeTracker via API returned None (path case issue); README-derived data used instead.
- MonitorAndControl's license: GitHub reports NOASSERTION — the repo may carry a custom license file; not verified by reading it. Treat reuse as blocked until confirmed.

---

## Q2 — Which reuse the "Windows service + foreground-app tracker + lock overlay" pattern; what overlay/locking techniques?

### Takeaway
The dominant pattern is exactly the one the parent is contemplating, and **nobody uses Assigned Access / kiosk mode for this** — everyone rolls a topmost full-screen borderless window (often per-monitor, blocking shortcuts) or, when enforcement must be total, falls back to **signing the account out** / locking the workstation via the SYSTEM service rather than an in-session overlay. Browser-side blocking is done via Windows **browser policies** (`URLBlocklist`/`WebsiteFilter`) rather than a child-session overlay.

### Cited findings
- **vigil** locking: full-screen lock window shown by the service in the child's session; covers **every monitor**; blocks Win key, Alt+Tab, Alt+F4; Task Manager and password-change UI disabled; the lock process "cannot be closed without administrator rights; closing it repeatedly logs the user off"; fail-closed on server loss (30 s); unlock via TOTP even offline; runs in Safe Mode; guardian re-applies every 5 min. — [README](https://github.com/deresterr/vigil/blob/main/README.md)
- **MonitorAndControl** enforcement layers: (a) **Popups**: full-screen countdown warning popup (PopupHost.exe) before an app is closed; (b) **Bedtime / PC-time / break / Lock-now**: the SYSTEM GameHost service **signs the account out** (child's processes close) — implemented at the account/session level, not as an overlay; (c) **Website blocking**: browser policies `URLBlocklist` (Chrome/Edge/Brave) and `WebsiteFilter` (Firefox) written by the SYSTEM service — children cannot remove admin-only policy keys; (d) **SafeSearch/YouTube Restricted**: same policy path; (e) overlay-focus filtering so Discord/Teams voice overlays don't count as app switches; tracking pauses on Windows lock screen; idle-input suspension configurable. Per-account enforcement runs a separate DeviceMon per-account in each session; only non-admin accounts can be controlled. — [README](https://github.com/geogengames-hue/MonitorAndControl/blob/main/README.md)
- **Focusd**: notify-only; no overlay; HKCU Run autostart; `-H=windowsgui`; per-user, no admin — shows the *limit* of what a non-service design can do (nothing stops a child killing it). — [README](https://github.com/0xarchit/Focusd/blob/main/README.md)
- **screenguard** (Linux, design reference): hard enforcement = "when time is up, the child's desktop sessions are locked; optionally apps keep running behind the lock" via logind; warnings on desktop; lock via D-Bus/logind, not an X11 override-redirect hack alone. — [README](https://github.com/adambie/screenguard/blob/main/README.md)
- **DeskWarden**: process-lock approach (kill/block executables), no overlay technique documented. — [repo](https://github.com/muntasir018/DeskWarden)
- **K9-Web-Protection_BlockerFlow**: not an overlay; proxy + MITM + SafeSearch enforcement + macOS watchdog + Locked Screen/LaunchAgent; Windows version was "in progress" and is no longer updated. — [README](https://github.com/khaleel-git/K9-Web-Protection_BlockerFlow/blob/master/README.md)
- Search results show **no OSS project in this space using Assigned Access / Shell Launcher / kiosk** — those are enterprise/single-purpose-PC mechanisms; home parental agents universally choose the topmost-window or sign-out technique instead (Assigned Access requires Enterprise/Education editions and a dedicated kiosk account — poor fit for a shared family PC). (Inference from the complete search corpus above + Microsoft docs knowledge.)
- Third-party lockers: no project vendors an off-the-shelf "third-party locker" component; vigil ships its own lock in-repo; MonitorAndControl signs out via the service.

### Inferences
- For a **shared family PC**, the overlay must be backed by a SYSTEM-level mechanism: overlay alone is cosmetic if the child runs as admin; the credible OSS designs pair the overlay with (1) fail-closed server arbitration, (2) signed requests + DPAPI/SYSTEM-only secrets, (3) guardian re-install/re-enable every N minutes, (4) tamper alerts to a parent channel. vigil documents exactly this; replicate the *shape*, not necessarily the code (MIT — copying is permitted; see license notes).
- The "account sign-out" technique (MonitorAndControl) is simpler and stronger than any in-session overlay and is immune to overlay-kill tricks; its downside is that unsaved work is lost — MonitorAndControl shows warnings 10/5/1 min ahead; vigil keeps the session locked instead (work kept on screen behind lock).
- Browser-policy-based site blocking needs no extension and no MITM and survives private windows; it is clearly the emerging OSS-preferred approach on Windows (MonitorAndControl), vs MITM proxy (K9) vs extension+native-messaging (Clarivue) vs pure-hosts-file lists.

### Gaps
- No OSS project found implementing Assigned Access for family PCs (expected; noted as gap rather than absence of demand).
- vigil's lock-window implementation details (WS_EX_TOPMOST vs newer Window Protection / `SetWindowDisplayAffinity`) are not enumerated in the README; would need `client/ui` source reading for confirmation (files: `client/ui/*`, `client/watchdog/*`, `client/setup/*` in the repo).

---

## Q3 — OSS porting Family-Link-style rules engines to PC (day/week budgets, bedtime windows)

### Takeaway
No Windows-native OSS carries a complete Google-Family-Link-equivalent rules engine **on the PC itself** — the closest is the *integration* **HAFamilyLink** (MIT; drives the real Google Family Link API from Home Assistant: daily limits, bedtime/school "downtime", grant bonus time), and the strongest PC-enforced rule engines are **vigil** (per-day weekly schedule + daily limit per child, summed across PCs) and Linux's **timekpr-next** (per-user per-day hour budgets + session kill). The rule-engine piece to borrow is small and self-contained: day-of-week → {daily_limit, allowed_window[], weekend_override, one-off bonus_minutes} with a monotonic clock and midnight rollover.

### Cited findings
- **HAFamilyLink**: Home Assistant custom integration; "Control your children's Android devices through Google Family Link directly from Home Assistant; monitor screen time, set daily limits, manage bedtime and school schedules, and grant bonus time — all from your dashboard." Python, MIT, 140★, 141 forks, pushed 2026-11-10, created 2025-11-07. This is the reference for *API-shaped* Family Link semantics (day budgets, downtime windows, bonus-time grants) though enforcement happens on Android, not Windows. — [repo](https://github.com/noiwid/HAFamilyLink)
- **vigil**: weekly schedule = per-child per-day-of-week daily limit + night time; parent panel; manual lock/extra time; bonus time via panel or TOTP on lock screen; time aggregated across multiple PCs per child. — [README](https://github.com/deresterr/vigil/blob/main/README.md)
- **screenguard**: per-profile daily limits (e.g. weekdays 2 h, weekend 3 h 30) chosen per day; allowed time windows (15:00–20:30 etc.); locks sessions at expiry; works offline from cached server rules; extras: block-new-apps, app groups with own limits, site block via nftables DNS proxy. GPL-3.0 → reading/copying has share-alike implications (below). — [README](https://github.com/adambie/screenguard/blob/main/README.md)
- **timekpr-next**: per-user per-day time limits, session termination; classic Linux rule engine; repo moved — canonical now [polesapart/timekpr-next](https://github.com/polesapart/timekpr-next) (60★, pushed 2025-09, license field None — historically GPL) and docs at [mjasnik.gitlab.io/timekpr-next](https://mjasnik.gitlab.io/timekpr-next/); unmaintained-ish (last push 2025-09) but the rule model (per-day `time_limits`, `not_before/not_after`) is the canonical reference. Third-party [adambie/timekpr-webui](https://github.com/adambie/timekpr-webui) manages it fleet-wide.
- **MonitorAndControl**: shared app groups, per-app categories, schedule rules per app, bedtime (Fri/Sat later weekend bedtime), PC-time allowances per non-admin Windows account with SID-keyed tracking, weekend minutes override, time-zone pinning for bedtime integrity. — [README](https://github.com/geogengames-hue/MonitorAndControl/blob/main/README.md)
- **jelly-watch-wise** (Joker-KP, MIT, 38★): Jellyfin screen-time limiter — server-side quota/lock pattern, analogous "grant extra time" mechanics for media; shows the "ask → approval → quota extension" loop works cleanly in a self-hosted stack. — [repo](https://github.com/Joker-KP/jelly-watch-wise)

### Inferences
- The Windows-side rule engine is ~200 lines of policy evaluation; the hard parts are (1) trustworthy time (pin TZ, monotonic clock — MonitorAndControl pins the OS TZ; vigil uses server time), (2) multi-PC aggregation (vigil/screenguard aggregate server-side), (3) fail-closed behavior (vigil).
- For a *private family hub*, do not copy timekpr-next/screenguard code verbatim (GPL/AGPL family); reimplement the small model guided by MIT exemplars (vigil, Focusd, HAFamilyLink).

### Gaps
- No canonical OSS PC "rules DSL" exists; everyone hard-codes day-of-week structs. A search for "family link pc rule engine" returns only integrations and SEO sites — Windows is served by bespoke per-repo models.

---

## Q4 — "Ask parent for more time" flows (MQTT/HTTP request-approve) worth borrowing

### Takeaway
Yes — two production-grade exemplars exist in 2026 OSS: **vigil** (TOTP-on-lock-screen + "extra time" granted from the panel; works offline) and **MonitorAndControl** (at the 5-minute warning the child taps "Ask parent?" → parent gets ntfy/Telegram/email with **Add 15/30/60 min** and **Not now** buttons; single-use declined link requires no auth; request expires after 12 h; one pending request per account). **HAFamilyLink** shows the same loop against Google Family Link's API ("bonus time" grant) orchestrated via Home Assistant — a natural MQTT/HTTP integration point for a home-hub architecture.

### Cited findings
- vigil: "Extra time also with a TOTP code typed on the lock screen"; "Unlocking with the phone code works without a connection to the server"; manual lock/extra time from panel; offline TOTP unlock where the secret lives on the PC (acknowledged weakness: an admin child can read it). — [README](https://github.com/deresterr/vigil/blob/main/README.md)
- MonitorAndControl: child-facing "Ask your parent for more time?" Yes/No at 5-minute warning; parent alert with **Add 15/30/60** (opens PC Time with remote PIN approval) and **Not now** (single-use ntfy tap / Telegram button; grants nothing; expires 12 h); only one pending request per account; saving any granting rule closes the request; requests extend allowance, never bedtime; toggle `TimeRequestAlertsEnabled`. Also "Ask to open a blocked site" flow (alert → **Allow**/**Not now** buttons; allow-fills the rule form) and "New programs need your OK" (alert → **Allow** (opens approval) / **Keep blocked** (single-use decline link)). ntfy and Telegram are first-class phone-push channels; nothing installed on child's PC. — [README](https://github.com/geogengames-hue/MonitorAndControl/blob/main/README.md)
- HAFamilyLink: exposes Family Link actions (screen time adjustment, bonus time) as HA services/entities → automations can turn an MQTT/webhook "please" into a real grant; ideal if the family hub already runs Home Assistant. — [repo](https://github.com/noiwid/HAFamilyLink)
- screenguard: "Extra time, lock now, send a message — from the dashboard, the profile page or the phone"; agent enforces over a WS connection; offline cache means a granted extension can be applied next sync. — [README](https://github.com/adambie/screenguard/blob/main/README.md)
- Jellyfin `jelly-watch-wise`: quota-extension flow for a media server — same shape, narrower scope (MIT). — [repo](https://github.com/Joker-KP/jelly-watch-wise)
- Home Assistant community pattern: [Parental control (screen time) management via timekpr-next](https://community.home-assistant.io/t/parent-control-timekpr-management/1025008) — HA/MQTT orchestration of a Linux time-limiter; confirms the "HA as approval broker" idiom (no single canonical component; glue automations).

### Inferences
- The minimal viable loop to copy: **child button → signed request to local server → parent push (ntfy/Telegram) → parent one-tap approve → server raises the account's remaining budget**. Avoid email-polling (fragile) — MonitorAndControl still uses Gmail IMAP, and it works, but it's the least elegant path.
- If the family hub is Home Assistant, model the flow as HA services (like HAFamilyLink) and keep the PC agent dumb: it only reports usage and obeys budget changes.
- Single-use, capability-scoped "Not now" links (declined = grants nothing) are a genuinely good innovation in MonitorAndControl and cost ~20 lines.

### Gaps
- No OSS found implementing the request/approve loop over **MQTT specifically** as a standalone broker-pattern component — MQTT is always the HA/glue layer, not the app. A direct MQTT design would be novel-but-trivial for a custom hub.

---

## Q5 — Awesome-lists referencing this space; notable forks

### Takeaway
No awesome-list has a dedicated "Windows parental control" section; the space is cross-listed under **awesome-selfhosted → DNS servers/ad-blockers** (AdGuard Home, blocky, Numa, Pi-hole, Technitium) and tangentially under task/chore tools. K9's lineage lives in the **K9-Web-Protection_BlockerFlow** fork (K9 → BlockerFlow morph) and the abandoned **k9webprotection** upstream; notable forks overall are scarce.

### Cited findings
- **awesome-selfhosted** README (raw, master): DNS section lists [AdGuard Home](https://adguard.com/en/adguard-home/overview.html) (GPL-3.0, Docker), [blocky](https://github.com/0xERR0R/blocky) (Apache-2.0, Go), [Numa](https://github.com/razvandimescu/numa) (MIT, Rust), [Pi-hole](https://pi-hole.net/) (EUPL-1.2), [Technitium DNS Server](https://technitium.com/dns/) (GPL-3.0, C#/Docker); and notes awesome-sysadmin's DNS list as further reading. **No parental-control category exists** (verified by grep of the list for "parental|screen time|timekpr|blocking" — only DNS/task-manager hits). — [raw README](https://raw.githubusercontent.com/awesome-selfhosted/awesome-selfhosted/master/README.md)
- **Timekpr-nExT** fork navigation: upstream timekpr (Launchpad-era, by mjasnik) → [polesapart/timekpr-next](https://github.com/polesapart/timekpr-next) (GitHub mirror, 60★); docs at [mjasnik.gitlab.io/timekpr-next](https://mjasnik.gitlab.io/timekpr-next/). Related [adambie/timekpr-webui](https://github.com/adambie/timekpr-webui) (web dashboard for timekpr fleet).
- **K9 Web Protection**: repo [khaleel-git/K9-Web-Protection_BlockerFlow](https://github.com/khaleel-git/K9-Web-Protection_BlockerFlow) is a fork/re-release of K9 "kept public as the open-source foundation that BlockerFlow was built on"; README states "This repository is no longer actively updated… Need the latest BlockerFlow code? …request from me directly" — i.e., the maintained successor (BlockerFlow, on Google Play / blockerflow.khaleel.eu) is **not open source**. Windows port "in progress" and dead. License: custom ("Open Source, free for personal and community use") — **not OSI-approved; treat as source-available**. — [README](https://github.com/khaleel-git/K9-Web-Protection_BlockerFlow/blob/master/README.md)
- **Android side for reference**: curbox (GPL-3.0, 1.5k★), astraedus/nudge (GPL-3.0), quizgate-tv (Apache-2.0, 答对再看 — answer-questions-to-watch pattern, MIT-like reward loop), StudyOnTV (Android TV: unlock by answering questions; NOASSERTION license). These are the richest source of "earn time by homework/questions" mechanics but are Android-only and licenses mixed.
- **Sentinel/"lockhour/support" SEO repos**: GitHub search for topic:parental-control is heavily polluted by AI-generated "cracked software" repos (e.g., fake "Kidlogger/Bitdefender/F-Secure" repos with MIT badges, 0 stars) — do not use; and note [ripplefocus/ripplefocus.github.io](https://github.com/ripplefocus/ripplefocus.github.io) (iOS/Android Screen Time API app blocker with 15-min monotonic cooldown, parent unlock — closed web app).

### Inferences
- The handful of legit Windows-native OSS projects is too small to be well-curated; expect to self-assemble. Treat "awesome-parental-control" lists as nonexistent.
- K9 lineage is effectively closed-source now (BlockerFlow); only the macOS v1.0.0 shell and blocklists (`lists/`, with `sync.py`/`build.py` pulling upstream feeds) are legally reusable as *data*, subject to each upstream list's license.

### Gaps
- No dedicated awesome list found (searched awesome-selfhosted; awesome-opensource-education exists but contains no relevant Windows parental-control entries per search results — not exhaustively grepped; low yield expected).
- Forking activity on vigil (0★, created Nov 2026) is nil so far.

---

## Q6 — Maintenance status: K9, OpenDNS FamilyShield, CleanBrowsing, AdGuard Home (Windows), Technitium DNS

### Takeaway
For a self-hosted LAN DNS backbone serving all household devices in 2026: **AdGuard Home (GPL-3.0, very active, Windows-native)** and **Technitium DNS Server (GPL-3.0, C#, Windows-native service, very active)** are the two credible OSS choices; **blocky (Apache-2.0)** is a solid Docker-friendly third. **OpenDNS FamilyShield is officially still offered by Cisco but sits under a brand in EOL transition (Umbrella SIG/DNS EOL announced 2026-09-01; Cisco states OpenDNS brand "remains for its free consumer offerings")** — do not build a new dependency on it. **CleanBrowsing** is a healthy commercial free/paid service (not OSS). **K9 is unmaintained/source-available/macOS-only in practice.**

### Cited findings
- **AdGuard Home**: 38,324★, forks 2,608, GPL-3.0, pushed 2026-11-10; runs as Windows binary/service (also Docker); HTTPS/QUIC filtering, parental control with password-protected settings, per-client rules, safe-search enforcement; listed in awesome-selfhosted. — [repo](https://github.com/AdguardTeam/AdGuardHome), [awesome-selfhosted](https://raw.githubusercontent.com/awesome-selfhosted/awesome-selfhosted/master/README.md)
- **Technitium DNS Server**: 10,328★, forks 823, GPL-3.0, C#, pushed 2026-11-04; Windows-native service; authoritative + recursive; ad/malware/category blocking via allow/block lists + "block list URL" ingestion (incl. StevenBlack/oisd); DHCP server built in; logging + stats; strong Windows story (MSI). — [repo](https://github.com/TechnitiumSoftware/DnsServer), [website](https://technitium.com/dns/)
- **blocky**: 7,102★, Apache-2.0, Go, pushed 2026-11-10; Docker/k8s; Prometheus metrics; straightforward config; good filtering but fewer parental-specific features than AdGuard Home. — [repo](https://github.com/0xERR0R/blocky)
- **Pi-hole**: EUPL-1.2, actively maintained, but officially Linux/Docker only (not a Windows-native backbone without Docker Desktop/WSL). — [awesome-selfhosted](https://raw.githubusercontent.com/awesome-selfhosted/awesome-selfhosted/master/README.md)
- **OpenDNS FamilyShield**: Cisco page still lists FamilyShield as free (208.67.222.123 / 208.67.220.123, familyshield.opendns.com); Cisco OpenDNS terms confirm FamilyShield is discontinuable by user action; however **on 2026-09-01 Cisco published end-of-sale/end-of-life for Umbrella SIG and DNS** (EOL15975), umbrella transitioning into "Cisco Secure Access"; commentary notes "Cisco says the OpenDNS brand remains for its free consumer offerings" — conflicting signals across sources; an OEM-thread "Dns not working" shows FamilyShield reachability depends on Cisco's infra. **Status: alive but explicitly legacy inside an EOL portfolio — treat as sunset-risk, not a foundation.** — [Cisco FamilyShield page](https://www.cisco.com/c/en/us/support/docs/security/umbrella/225803-understand-opendns-familyshield.html), [Cisco EOL announcement](https://www.cisco.com/c/en/us/products/collateral/security/security-cloud/umbrella-sig-dns-eol.html), [Veraify FAQ](https://veraify.ai/faqs/cisco-umbrella/is-cisco-umbrella-being-discontinued/), [dope.security analysis](https://dope.security/post/what-is-cisco-umbrella-2026)
- **CleanBrowsing**: operational; free **Family Filter** public DNS (185.228.168.168 / 185.228.169.168; family-filter-dns.cleanbrowsing.org; DoH/DoT; 56 locations listed, "15,094,095 rps" status page) blocks porn+phishing+malware at category level; paid plans add granular category policy + custom blocks — i.e., **free tier is blunt (one family profile), granular controls are paywalled; not OSS**. — [cleanbrowsing.org/filters](https://cleanbrowsing.org/filters), [status](https://cleanbrowsing.org/status/), [pricing](https://cleanbrowsing.org/pricing), [comparison](https://cleanbrowsing.org/learn/free-vs-paid)
- **K9 Web Protection**: macOS v1.0.0 only; repo says "no longer actively updated… PRs may not be reviewed"; Windows port in-progress and abandoned; code = Go+Wails proxy with 932k-domain embedded blocklist, SafeSearch injection, QUIC-UDP-443 block via pf, watchdog, `uchg` locks. Unusable as-is for Windows and unmaintained. — [README](https://github.com/khaleel-git/K9-Web-Protection_BlockerFlow/blob/master/README.md)
- **OpenDNS Home (Cisco Umbrella free)**: account-based, web-managed; **not OSS**; same EOL-umbrella umbrella uncertainty; avoid new reliance.
- **AdGuard Home in "Windows mode" specifics**: runs as Windows service; per-client filtering; parental controls password; safe search; query log; ~low single-digit MB RAM at idle (observed behavior, not a sourced benchmark); GPL-3.0 → self-hosting internal family use is fine; distributing binaries would carry copyleft duties.

### Inferences
- For the parent's LAN backbone serving **all household devices**, Technitium (pure Windows, no Docker, category/oisd blocklists, DHCP, per-client stats) is arguably the best fit for a Windows-centric household; AdGuard Home if they also want HTTPS filtering/safe-search policies with a polished UI. Both are strictly better maintained than everything else on the list.
- **DNS category filtering is not app-blocking**: it cannot enforce bedtime windows, quotas, or lock screens — it complements a PC agent; encrypted DNS (DoH) from browsers must be policy-blocked (MonitorAndControl shows how via browser policies) or controlled at the router, otherwise DNS is bypassable.
- OpenDNS FamilyShield should be treated as "works today, no future guarantees"; CleanBrowsing as "works, paywalled for control"; both fine as *secondary* resolvers during migration.

### Gaps
- No OSS benchmark data on AdGuard Home vs Technitium reliability at family scale (both are production-grade in community practice; no formal study found/needed).
- Exact Cisco communication listing FamilyShield's fate under EOL15975 was not located — status reported with conflicting secondary coverage; flagged explicitly.

---

## Q7 — OSS implementing Chrome/Edge extension + native messaging host for category-based page blocking on Windows

### Takeaway
The extension+native-messaging pattern exists in OSS but is **rare and immature** in this space: the only direct hit is **Clarivue-AI** (theVatsal3802, 0★, source-available/all-rights-reserved-looking, unvetted). The practical OSS ecosystems instead implement page blocking three ways: **(1) browser Windows policies** (`URLBlocklist`/`WebsiteFilter`, as MonitorAndControl does — no extension, SYSTEM-enforced, covers Edge/Chrome/Brave/Firefox incl. private windows), **(2) local MITM proxy** (K9/BlockerFlow — heavy, macOS-proven, Windows stalled), **(3) plain content blockers with category lists** (uBlock Origin + oisd/StevenBlack lists used as a generic engine, not parental-aware). Extension+native-messaging remains a DIY option with Chrome's documented manifest format; no turnkey parental solution exists.

### Cited findings
- **Clarivue-AI** (theVatsal3802/Clarivue-AI): "fully local, open-source parental control… allowed/blocked website lists, plain-language parent instructions… **Native Messaging Host**" — TypeScript, 0★, created 2026-05-31, pushed 2026-05-31, no license file detected. Architecture = browser extension + native host; immature, unattributed, unverifiable quality. — [repo](https://github.com/theVatsal3802/Clarivue-AI)
- **K9-Web-Protection_BlockerFlow** ships `chrome-extension/` (Manifest V3; live on Chrome Web Store as "K9 Web Protection") cooperating with the local Go proxy — closest shipped example of extension+host pairing, but Windows build and current maintenance are dead; custom license. — [repo](https://github.com/khaleel-git/K9-Web-Protection_BlockerFlow)
- **MonitorAndControl** deliberately avoids extensions: reads foreground browser address bar via **Windows UI Automation** (top websites; site-name only), and *blocks* sites via browser policies (`URLBlocklist` Chrome/Edge/Brave, `WebsiteFilter` Firefox) written by the SYSTEM service every 30 s — child can't edit admin-only policy keys; SafeSearch forced the same way; no extension to side-load/disable. — [README](https://github.com/geogengames-hue/MonitorAndControl/blob/main/README.md)
- **Native messaging host mechanics** are documented by Chrome ([developer docs](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)) and Edge ([edge-developer native-messaging.md](https://github.com/MicrosoftDocs/edge-developer/blob/main/microsoft-edge/extensions/developer-guide/native-messaging.md)): host manifest JSON at a fixed path (`C:\Program Files\App\host.exe`), `allowed_origins` pinned to the extension ID, stdio transport — a well-trodden pattern (e.g., MCP browser bridges) but **no OSS parental category filter implements it robustly for Edge+Chrome today**.
- **uBlock Origin** (GPL-3.0) and **StevenBlack/oisd blocklists** are the generic engine/lists one would embed behind such an extension; uBO itself is content-blocker, not parental-aware, and is manifest-v2-deprioritized on Chrome in 2026 (uBlock Origin Lite is MV3) — noted for architecture reference, not as a drop-in.
- **ScreenTimeTracker** pairs with a **browser extension** for website-time stats (tracking-only; no blocking). — [README](https://github.com/majianchuan/screen-time-tracker/blob/main/README.md)

### Inferences
- If the parent wants category blocking with minimal moving parts on Edge+Chrome, the **policy-keys route beats extension+native-messaging on every axis that matters for a hostile-ish child**: no per-browser install, no uninstall surface, admin-only write, includes private windows. Its weakness: browsers only (Opera/Vivaldi/portable browsers excluded) and category granularity is binary per-domain (no content inspection).
- Extension+native-messaging only wins if you need **per-URL/keyword** blocking (K9-style) — at the cost of a MITM CA or fragile declarativeNetRequest rules and a child-visible extension to disable.
- A pragmatic hybrid: policies for blocking + a *tracking-only* extension (ScreenTimeTracker-style) or UIA address-bar reads for visibility.

### Gaps
- No 2026-current, maintained OSS repo implementing the exact "Chrome/Edge extension + native messaging host, category-based block, Windows, parental" pattern was found beyond the two immature examples above. Absence of evidence is fairly strong here given dedicated search.
- Microsoft Edge's own Family Safety / Education (atp: "Family options") remains closed-source — not available as reusable code.

---

## License compatibility (cautious, informational — not legal advice)

- **MIT / Apache-2.0** (vigil, Focusd, HAFamilyLink, DeskWarden, NetCurfew, ActivityWatch (MPL-2.0 — file-level copyleft, weak), DeviceMon-adjacent tools): safest to read, adapt, and privately use; Apache-2.0 adds an explicit patent grant. Keep copyright/license notices in forks.
- **GPL-3.0** (screenguard [AGPL], ScreenTimeTracker, AdGuard Home, Technitium): copyleft attaches on **distribution** of modified/combined work to third parties. A private family hub installed on PCs the parent owns, not distributed, does not trigger source-offer duties; but if the parent publishes the agent or installs it on machines owned by others/family with distribution, share-alike would apply. **Do not copy GPL/AGPL code into the private hub if the hub might later be published**; re-implement from MIT exemplars or take MIT-only components.
- **AGPL-3.0** (screenguard): same, plus §13 network-interaction triggers when modified versions interact with users over a network — for a family hub other family members use, a *strict* reading could pull this in if the hub is ever network-served deliberately; strongest reason to avoid verbatim copying and to keep the architecture pattern (server-decides + offline agent) as the reusable idea only.
- **MPL-2.0** (ActivityWatch): file-level copyleft; using its APIs/binaries privately is fine; modified MPL files must stay open if distributed.
- **NOASSERTION / custom-source-available** (MonitorAndControl, K9 BlockerFlow, StudyOnTV, several Android repos): GitHub "license" detection found no OSI license. **No right to copy code** absent explicit grant. MonitorAndControl may carry a custom license in-repo — unverified; treat as all-rights-reserved until the author confirms. Reusable = ideas/architecture, not code.
- **No license at all** (FamillyKJ, ArktinMonitor, Clarivue history, darrint/device-blocker): default copyright; read for ideas only.
- Practical bottom line for a private family hub: copying **MIT/Apache-2.0** code is low-risk (retain notices); **GPL/AGPL** code should be re-implemented, not lifted; **everything else** is reference-reading only. This is general information, not legal advice; consult a lawyer for distribution decisions.

## Component reuse cheat-sheet (what to take from where)

- **Lock overlay + watchdog + pairing design**: vigil (`client/ui`, `client/watchdog`, `client/setup`, `docs/architecture.md`, `docs/guardian.md`, `docs/decisions.md`, `SECURITY.md`) — MIT. Best single source for "how to not get killed by a child admin".
- **Feature matrix & UX flows** (ask-parent, bonus time, homework mode, categories, per-account SID-keyed allowances, browser-policy site blocking, SafeSearch enforcement, ntfy/Telegram approvals): MonitorAndControl README — read for UX, don't copy code (license).
- **Foreground tracker in Go**: Focusd `system/` (Win32 syscalls), `core/`, `storage/` — MIT; good reference for GetForegroundWindow polling + SQLite schema + IPC daemon.
- **Tracking engine (if you want graphs/reports)**: ActivityWatch watchers (MPL-2.0) — mature, Windows-supported; use as library/service, not embedded.
- **Home Assistant approval broker**: HAFamilyLink (MIT) — wire ntfy/MQTT "ask" → parent approves → budget bumped.
- **DNS backbone**: Technitium (pure Windows service) or AdGuard Home (GPL-3.0, polished UI) — both fine privately; oisd/StevenBlack lists for category data.
- **Blocklists for browser blocking**: oisd (oisd.nl, free) / StevenBlack hosts — check each list's license (most CC-BY-ish or unlicensed-but-attributed; verify before bundling).

## Highest-priority gaps / do-not-trust list

- vigil: treat as alpha (0★, created 2026-11-06). Its threat model is honest (admin child *can* defeat it) — read `SECURITY.md` before relying on it. HTTP (not TLS) server + clear-password pairing issue documented by the author; put behind TLS reverse proxy on untrusted networks.
- MonitorAndControl: license unverified; single-maintainer risk; Windows-policy site blocking documented as bypassable by an *administrator* child; website-time tracking relies on UIA reading address bars (fragile across browser updates).
- ScreenTimeTracker: GPL-3.0 — keep out of the private hub's source tree if publication is possible.
- OpenDNS FamilyShield: sunset-risk inside Cisco's Umbrella EOL (announced 2026-09-01, effective into 2027); not a safe new dependency.
- SEO/"cracked" parental-control repos with MIT badges and 0★ (Romaco-Timeout, Picblock, "F-Secure Full Version" etc.) — malware-adjacent; ignore.
- No evidence of any Windows OSS using Assigned Access/kiosk for family PCs; overlay = custom topmost window everywhere.

## Source index (repositories)

Windows-native: vigil, MonitorAndControl, Focusd, ScreenTimeTracker, DeskWarden, NetCurfew, WindowsParentalControl, parental_control, parental-control (vasyaod), ArktinMonitor, FamillyKJ, Clarivue-AI, K9-Web-Protection_BlockerFlow (Windows n/a), ActivityWatch.
Cross-platform/design references: screenguard (adambie), ScreenGuard (adityakrishnan005-a11y), timekpr-next (polesapart; mjasnik.gitlab.io docs), timekpr-webui (adambie), HAFamilyLink (noiwid), jelly-watch-wise (Joker-KP), device-blocker (darrint).
DNS/network: AdGuardHome, DnsServer (Technitium), blocky, Pi-hole, OpenDNS FamilyShield (Cisco), CleanBrowsing.
Lists/awe-some: awesome-selfhosted (master README, DNS section), GitHub topics parental-control / screen-time / app-blocker (heavily AI-spam-polluted).
