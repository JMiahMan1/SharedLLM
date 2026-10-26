# Jarvis OS / SharedLLM - session handover

Updated 2026-10-26 for the next AI assistant taking over this repo.
Every claim below was verified against the live system unless marked **planned**.

## 0. Snapshot

- Repo: `/Users/jeremiahsummers/Code/SharedLLM`, branch `microservices`. The last **deployed**
  commit is `ebe57666`; docs-only commits land on top of it, this file's own commits included, so
  read the tip from `git log` rather than from here.
- Live: `https://jarvis.sumemail.com` - version **1.6.0**, OTA bundle `git_sha` **ebe57666**
  (APK `versionCode` **482**, 2026-10-26; `https://jarvis.sumemail.com/health` is 200 and
  `scripts/check_service_urls.py` is 4/4 PASS). 2026-10-26 shipped, in order: the guard overlay with
  its chat and override paths, the media artwork and artist fixes, the security gates, the health
  fixes, the UI and accessibility pass, the family-comms fixes (including a push when somebody starts
  a family call), and the ops hardening (nightly backups, deploy rollback, SQLite WAL and additive
  migrations, per-loop worker leases, jittered retries, a digest-pinned base image whose Python
  packages are now pinned too, and identity, health, bible, storage, logging and geo now running as
  the host user rather than root). Every deploy was verified with 0 ERROR lines in the touched
  services' logs since it landed. APK: CI builds one per push under `services/ui/**` and the APK
  `versionCode` rebuilds per push (482 is current; 481 was the previous), so the native work below
  needs the newest APK on the phones. **APK 482** carries the guard overlay that draws over a
  tablet's taskbar and navigation strip, with "Ask for more time" and "Message a parent" painted over
  the lock as a sheet instead of taking it down. Family guard Phase 11, the family member page
  (`/family/member/<child>`), the Family Guardian page (`/guardian`) and the MDM/Devices work are
  live.
  - Nextcloud bot account **`jarvis`** (display "Jarvis") exists; its login is only in the
    server `.env` (`JARVIS_BOT_NC_USER`/`JARVIS_BOT_NC_PASS`).
  - **TLS certificate renewed (2026-10-25)**: the public proxy (`PUBLIC_PROXY_IP`, 192.168.2.30) now serves a
    valid certificate for `jarvis.sumemail.com` (issued 2026-10-25 03:10 UTC, valid to 2027-01-23). The previous
    `*.sumemail.com` wildcard expired at 03:29 UTC and briefly failed verification for every endpoint through
    that proxy; renewal is still handled on that proxy, not in this repo.
- Check anytime:
  - `curl -fsS https://jarvis.sumemail.com/api/app-updates/version`
  - `.venv/bin/python scripts/check_service_urls.py` - probes Audiobookshelf,
    Music Assistant, Nextcloud and the gateway; exits 1 on any failure.
    **If Music Assistant alone says `UNREACHABLE (ClientConnectorError)`, it is this Mac, not MA.**
    On 2026-10-26 every `192.168.2.x` address was `[Errno 65] No route to host` from
    `.venv/bin/python` (Homebrew 3.14.8) while the public internet answered fine. The same probe with
    `/usr/bin/python3` (Apple, 3.9.6), `nc` and `curl` reached `192.168.2.20:8095` without trouble,
    and these commands run under the **opencode** app (`zsh` <- `opencode` <- `launchd`), not under a
    terminal - so the missing macOS Local Network permission belongs to **opencode**, and the
    third-party Python binary is the only client it affects. `ha.sumemail.com` is the only probe
    target that resolves to a private address (192.168.2.20), which is why MA is the only one that
    fails. MA itself answered 200 to `curl` and `ping` from this Mac, and both the server and the
    gateway container reached it with 200. Allow **opencode** in System Settings -> Privacy &
    Security -> Local Network, or read MA's result from the server instead.
  - `ssh jeremiah@192.168.2.205`; `docker exec -i sharedllm_<svc> …` (direct
    service calls need `X-Internal-Secret`, present as `INTERNAL_SECRET`).

### Recently shipped (newest first)

- `2026-10-26` **Production-readiness program** (`docs/PRODUCTION_READINESS.md` is the record). The
  whole app was audited against a written bar by five read-only audits (UI/UX, health hub, family
  comms, backend readiness, security); every finding was graded BROKEN / ROUGH / FINE and then fixed.
  **Media:** every Music Assistant cover 404'd, because the gateway fetched the provider's CDN URL and
  the image proxy's SSRF allow-list rebased that onto Home Assistant; covers now use MA's own
  `proxy_id`, book authors and narrators are kept out of Artists by `artist_type`, and an artist is
  playable ("Play all"), because MA expands an artist URI into that artist's tracks. **Security:** the
  note routes let any member read or overwrite files inside the execution container (paths are now
  confined to `LOCAL_NOTES_ROOT`); `/api/dns`, `/api/config*`, `/api/models*`, `/api/generate`, the geo
  vehicle and zone routes, Raven's mission routes, telemetry enrolment, the chat-job reads and the
  admin test runners were anonymous or reachable by any member, and are now gated; sign-in is rate
  limited (5 per username, 20 per source address, 15 minutes) and a password change rotates the
  session key; the internal secret fails closed and is compared in constant time; the APK updater
  refuses an update whose host is not the phone's own server; the image proxy no longer follows
  redirects. **Health:** a metric is bucketed by the household's local day (a 6pm glass of water used
  to count on tomorrow), the cycle, nutrition, steps and shared-with-you panels say what failed
  instead of showing nothing, list reads are capped and say `truncated`, one manual sleep log per
  night, no future-dated days, an export that carries the cheers you *sent*, and Measurements now
  shows every measure with its history and a delete. **UI:** Android Back works, a catch-all route
  replaces the blank page, dialogs trap and restore focus (a text field inside a dialog used to keep
  only its first character), forms are labelled, the crash screen is readable by a child, a failed
  chat load says so, all 24 destructive actions ask in the app's own dialog, phones can search pages,
  the Dashboard's service status and build hash are admin-only, `/recipes` has a navigation entry,
  Settings is grouped, and a failed request carries the server's own words instead of "Request failed
  with status code 500". **Family comms:** an `@Jarvis` failure now speaks in the room, Trivia and
  Memory start from the composer, the game player is the verified caller, an unanswered drop-in call
  stops ringing after a minute, the calls tab admits a failed refresh, timer and place reminders open
  the right screen, a sent bubble no longer blinks out (photos and voice notes included), and joining
  a call pushes one notice to the room's other members. **Ops:** nightly database backups, a deploy
  that rolls back to the running image IDs when its health check fails, SQLite WAL with a busy
  timeout, additive column migrations for health, a 5-second identity timeout, no tracebacks or
  upstream bodies returned to clients, per-loop Redis leases so a second worker cannot double-post, a
  digest-pinned base image, and identity, health, bible, storage, logging and geo running as the host
  user rather than root.

- `2026-10-26` **Repo hygiene**: two stale git worktrees (`.claude/worktrees/agent-a6df28f4bc42c1300`
  and `.tmp/wt-nextcloud`) and their local branches were removed after proving their work was already
  in `microservices` (`git diff --stat microservices <branch> -- <files>` empty; the second was a
  straight ancestor), along with one stale stash and 16 inert `refs/cline/checkpoints/…` refs. The
  repo is back to a single worktree, 6 refs (both heads, both remotes, `origin/HEAD`, one tag) and no
  stashes. Keep it that way: branch, land, delete.
- `2026-10-26` **Bug hunt (live gateway logs): every Music Assistant cover was a 400, and a websocket
  callback threw on every position tick.** MA's imageproxy only resizes to `0/80/160/256/512/1024`;
  the gateway forwarded the UI's width verbatim, so a cover asked for at 44/96/128/760 px came back
  `400 Unsupported size ...` (`[imageproxy] upstream ma status 400` in production). The width is now
  snapped UP to the next size MA accepts (`_ma_image_size` in `services/gateway/main.py`). Separately,
  MA has sent `queue_time_updated` carrying a **bare number**, which raised
  `AttributeError: 'float' object has no attribute 'get'` inside the MA websocket callback on every
  position tick; a payload that is not an object is now skipped (`normalize_ma_event` /
  `normalize_ma_player`). An upstream **404** (a library entry with no artwork) is logged as a
  warning rather than an error, so the post-deploy error count is meaningful again. Details and the
  verified MA size list: docs/MEDIA_UPSTREAM_API_NOTES.md. The same pass quietened the RAG HA-sync
  refusal: two Home Assistant entities whose id is a serialized user record are refused on every sync
  (every five minutes, per user - ~2,400 lines a day), so the warning is now repeated only when the
  set of refused ids changes for that user (`services/rag/main.py`). A third fix closes a safety gap
  in the same area: a parent's own "always allowed" list replaced the default outright, so a
  customised list could drop the dialer and leave a locked phone unable to call for help; the safety
  core (emergency, dialer, in-call, Jarvis) is now always present and a parent's list adds to it
  (`services/common/screen_time.py`).
- `2026-10-26` **Screen time: a full-screen lock on any phone, with a way out and a way to talk**
  (`c47d90b6` + `f673e877`, then `GuardOverlay` forced full screen and the allowed flows moved
  inside it; details in docs/HEALTH_HUB.md, "Tamper resistance"). A phone that is
  not a device owner now gets a full-screen `TYPE_ACCESSIBILITY_OVERLAY` lock (`GuardOverlay`,
  owned by the Accessibility guard) that covers the **home screen too**, so Home and Back cannot
  leave it and no extra permission is needed. The window draws over every system bar (a tablet's
  taskbar and navigation strip used to stay visible and usable, so the lock was not really full
  screen), with only the keyboard inset still fitted so a parent can type their login with the
  lock up. The lock screen shows why it is locked and offers
  "Ask for more time" and **"Message a parent"** - both painted **over** the lock as a sheet inside
  the same view (a request to `POST /api/screen-time/requests`, or a chat message to each parent
  through `POST /api/guard/message`), so the lock stays up underneath and the child never lands on
  a page of its own. It also offers "Open Jarvis", and it scrolls
  so a parent's login or offline code stays reachable with the keyboard up - the lock is never a
  dead end. The screen-time pushes and the old lock buttons deep-linked to `/health`, which had no
  route at all and rendered a blank page; the health hub is `/fitness`, `/health` now redirects
  there, and the pushes point at `/fitness` directly. A lock is only logged (and only called
  "Locked" on Devices) when the phone can really
  lock: `mdm.real_lock_reported` is true for a device owner (Android's own lock plus lock task) or
  any phone with the Accessibility guard on; with the guard off it is only a notification. Needs a
  new APK on the phone, and the guard still has to be switched on by hand on the tablet.
- `2026-10-25 latest` (the round that closed the day: recipes, a real guard lock, media fixes, the
  MDM follow-ups, the 1.6.0 release and the Health phase; all on `origin/microservices`, tip
  `6932452c`).
  - **Recipes: skip duplicates, show what was planned, estimate nutrition** (`6932452c`; Recipes
    Phase 8 duplicate-import guard + Phase 10 planned-vs-eaten + a USDA-seed estimate): the import
    path refuses a recipe whose source URL is already on the shelf; the MealsCard shows today's
    planned dinner beside what was actually logged (`services/ui/src/lib/recipeEaten.ts`); and a
    clearly-labelled **USDA-seed nutrition estimate** is offered when a note states none, with every
    ingredient the seed cannot match left **unknown** with the reason, never a guessed number
    (`services/health/recipe_estimate.py`, MealsCard/Recipes UI, docs/RECIPES.md + docs/FAMILY_HUB.md).
    This commit also carries the Phase 12 MDM follow-up below.
  - **A parent's lock is a real lock, not a message** (`613edbb0`, Phase 11): a device-owner phone
    now enters lock-task/kiosk mode while locked (`DeviceOwnerRules.lockTask`, `DeviceOwnerGuard`
    set/clear the lock-task package, `GuardScreenActivity` enters kiosk while locked and releases it
    on destroy with a self-check); a parent's lock is immediate (the guard `tick()` plus a static
    `refreshNow()` the `PushService` calls on a screen-time notice); the false "Locked" success is
    gone (Devices.tsx says "Locked" only when the phone can really lock — a device owner, or any
    phone with the Accessibility guard on — and the gateway writes the `device_lock` audit only
    then, via `mdm.real_lock_reported`); the protection card shows
    `adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver` and the "no accounts
    signed in / factory reset" caveat. JVM 26 tests pass. **Caveat:** a real OS lock still needs the
    phone provisioned as **device owner** (a one-time adb, or QR/`ACTION_PROVISION_MANAGED_DEVICE`
    at a factory reset - not implemented); without it the lock is the full-screen Accessibility
    overlay (it covers the home screen too and Home/Back cannot leave it), not Android's own lock.
  - **Media: the widget transport, the phantom speaker and the audiobook art** (`84bced59`): the
    Android home-screen widget's transport buttons were 422s and now send the route's command
    vocabulary plus the player's `entity_id`; this device's own Music Assistant player no longer
    appears as a phantom speaker (the store remembers the Sendspin id and drops its player events);
    MA error frames on read endpoints surface as errors instead of a silent empty; a transient
    `players/all` failure keeps the previous speaker list; audiobook artwork (`media.coverPath`) and
    the library-row cover/duration survive. This commit also carries the Phase 12 MDM follow-up
    below.
  - **Phase 12 MDM follow-ups** (`84bced59` + `6932452c`, see `docs/MDM.md`): a child-facing surface
    for the pushed School/Library files (`components/mdm/SharedFilesPanel.tsx`: open in an in-app
    viewer or download, carrying the API key a bare link could not); the Library set sourced
    read-only from the Calibre shelf (`GET /api/mdm/library/books`, `POST
    /api/mdm/library/books/file`); an append-only, capped MDM audit log (`audit.json`, `GET
    /api/mdm/audit`) shown as the page's Activity log; and `./data/mdm_files` mounted so the folders
    and the log survive a redeploy. Per-device update notices are **not** done (the notice and push
    plumbing is per user and the app-update version is global, so there is no clean device dimension
    - reported, not faked).
  - **Release 1.6.0** (`c9c640dd`, Phase 9): the UI version bump (`services/ui/package.json`
    1.5.2 -> 1.6.0) plus handover edits. **Honest note:** `git add -A` was used, so this commit
    ALSO carries the in-flight Health-phase work below; the commit is mislabelled but was
    deliberately left as pushed (history was not rewritten).
  - **Health phase** (committed inside `c9c640dd`): "What you do in Jarvis"
    (`services/common/jarvis_use.py`, `GET /api/telemetry/use/{user}`, `GET /api/activity/use`,
    `JarvisUseCard` behind a new `jarvis_use` switch); an adult opt-in `digital` family share scope
    (minors refused 422; `/family` projects `{"digital":{"shared":true}}`; the gateway fills screen
    time from telemetry saying "no report"/"unavailable" rather than a zero; `SharedWithYouCard`;
    `HealthProfilePanel` offers "Phone use" to adults only); find-phone
    (`POST /api/guardian/find-phone/{user}`, the priority-5 `find_phone` notice, a 30 s cooldown,
    `FindPhoneCard`). The gateway health-hub and usage-proxy tests came with it; docs/HEALTH_HUB.md
    Phase 10 (digital wellbeing) is now built and the find-phone lands on the Phase 11 member page.
  - **Media polish** (`0316f940`): P7-T5 image sizing (srcSet/sizes/sized proxies via a new
    `features/media/components/image.ts`), P7-T3 now-playing screen-reader announcements
    (`NowPlayingAnnouncer.tsx`), the P7-T4 render-count regression test, and P6-T5's 53-row UX ledger
    (`docs/MEDIA_OVERHAUL_QA.md`). In docs/MEDIA_OVERHAUL.md terms P6-T5, P7-T3, P7-T4 and P7-T5 are
    now shipped.
  - **Recipes from a photo, and small follow-ups** (`b891938b`): Recipes Phase 9 photo/handwriting
    import (local vision OCR only, nothing leaves the house; `services/common/recipe_ocr.py`,
    gateway `POST /api/recipes/photo`, an editable draft beside the raw transcription) and the
    Phase 10 "Dinner tonight" card; plus the small items - docs corrected for `/guardian` (its own
    top-level page, not a redirect), the pause notice now distinguishes a self-pause from a parent
    pause, recommendation lanes are self-editable by a grown member, and a new app-updater
    regression test (`services/ui/src/test/appUpdaterInit.test.ts`).
  - **MDM first slice** (`ccedf489`, Phase 12): device inventory, remote update push, shared files,
    lock and wipe - a parent Devices page and gateway `services/gateway/mdm.py` (`GET
    /api/mdm/devices`, `POST /api/mdm/devices/update` (409 when no build), `/api/mdm/files*`
    upload/list/download/delete/push/for-me, `POST /api/mdm/devices/{key}/wipe` confirm-gated
    device-owner-only); the phone polls `wipe_requested` and the guard wipes.
- `2026-10-25 late` (the day's round: repo hygiene, integrations finished, a live identity outage
  fixed, and a broad Family-guard, digital-wellbeing and device push; every item below was verified
  by tests, a live deploy and a smoke test, and is on `origin/microservices` at `41115ca6`).
  - **Repo-wide em/en dash removal** (`ec27a2c2`): no `U+2013`/`U+2014` characters remain in the
    source (66 files); the few functional regexes keep `\u2013`/`\u2014` escapes, so behaviour is
    unchanged.
  - **Integrations finished and hardened** (`547f7465`; docs/INTEGRATIONS.md): the Audiobookshelf
    connection test now logs in at the root `/login` (it was hitting `/api/login`, which ABS rejects);
    secret masking covers `*_token`, `*_pass`, `*_api_key`, `*_key`, `api_key` and any value filled
    from a saved `*_enc`; the Nextcloud, GitHub and GitLab tests have 5 s timeouts; the Nextcloud
    test uses the user's own `/ocs/v2.php/cloud/user` and honours `ocs.meta.statuscode`; connection
    failures no longer echo the exception; GitHub and GitLab are tested over the TLS-verifying client
    while self-signed hosts (Home Assistant, Nextcloud, Audiobookshelf, Music Assistant, Skylight)
    keep the insecure one; GitHub "Protected Branches" is now stored and resolved; the Private Git
    endpoint, username and port are used and credentials are redacted in every logged git command;
    a non-admin can turn Skylight on/off for themselves; and the audiobook library is no longer
    capped at 50 (the gateway pages through ABS). The dead per-user OpenRouter tile was removed
    (`d517166f`).
  - **Identity live-outage fix** (`81a1ec12`, the same fix as the `2026-10-25` bullet below): the
    deployed identity service crash-looped with `no such column: user.forbidden_branches` and took the
    gateway down; shipping the missing `forbidden_branches` column migration and redeploying identity
    + gateway fixed it.
  - **Phase 10 - digital wellbeing / screen time** (`e6949f70`): Android screen time is now measured
    from the day's own events. `PhoneUsagePlugin.getToday()` used to sum `getTotalTimeInForeground()`
    over un-clipped `INTERVAL_DAILY` buckets, so overnight and pre-waking foreground time counted as
    today (Jeremiah's own screen time claimed over an hour before he got up at 7:30). It now derives
    the total and the per-app rows from the clipped `queryEvents(start, now)` stream through a new
    `ForegroundTime` class, with JVM tests.
  - **Phase 11 - family guard** (the bulk of the round):
    - **Tamper escapes closed and a second user blocked** (`cfc7dc20`): on a device-owner phone the
      guard also sets `DISALLOW_USER_SWITCH`, `DISALLOW_ADD_USER`, `DISALLOW_DEBUGGING_FEATURES`,
      `DISALLOW_CONFIG_VPN` and `DISALLOW_CONFIG_PRIVATE_DNS`; `GuardRules.isTamperScreen` also covers
      the guard's own Accessibility detail page, the factory-reset/erase pages, the usage-access page
      and the Users/Add-user pages (Settings packages only); a second user or guest is reported as the
      tamper event `multi_user`.
    - **Device-owner lock (Layer 2)** (`ecf6584f`): the guard blocks its own uninstall, sets
      `DISALLOW_SAFE_BOOT` and `DISALLOW_FACTORY_RESET`, and calls a real `lockNow()` at lock onset;
      non-device-owner phones get the full-screen Accessibility overlay lock (`GuardOverlay`).
    - **Approve from the notification; ops notices stay off children's phones** (`41115ca6`): a parent
      gets Approve/Disapprove buttons in the notification for a child's asks (new app, permission,
      blocked site) and for more time, sent to EVERY parent; new push callbacks `guard_decide` and
      `more_time_decide` answer as the parent through the same routes the app uses, and the child is
      still told the answer. Notices carry an `audience` enforced by telemetry at delivery (a
      "parents" notice addressed to a minor is DROPPED), so app-update/system/ops notices never reach a
      child while a parent still hears about updates and problems; the minor set comes from identity's
      `is_minor` and an unreadable identity fails closed (503).
    - **Family Guardian gets its own top-level page** (`bf11c921`; `/guardian`, nav entry after
      Family, offered to every role): it no longer lives inside Health; Health keeps its
      digital-wellbeing card and a link to the Guardian page, and `/guardian?child=` opens the member
      page. UI full suite 275 files / 2130 tests passed.
    - **Tamper events for non-minors dropped, and self-editing** (`3b1aa18b`): identity
      drops a tamper report for an adult child or a parent (no `DeviceEvent`, empty notify, explicit
      `dropped: "not_minor"`), so only child/teen reports reach parents; an adult child can remove and
      disable their OWN monitoring settings (new `_may_edit_screen_time`: a parent-of-minor, or a grown
      member editing their own rules), clearing their rules and setting `monitoring: "off"` for
      themselves, and nobody can edit another member's. Data collection is unaffected by monitoring
      (usage and steps keep collecting on the device and recommendations keep working; the
      `monitoring` flag only decides whether to page a parent). identity + gateway 2179 passed.
  - **Child safety** (`ecf6584f`): cycle data is now recorded without opt-in - a `CyclePeriod`/`CycleLog`
    is stored even when tracking was never enabled, `CycleSettings.enabled` now only governs
    display/sharing, and every exposure gate is unchanged.
  - **Devices report the real form factor** (`72835c17`): "tablet" joins the device kinds with one
    `MOBILE_DEVICE_KINDS = (phone, tablet)`; a device may declare only its form factor (phone|tablet,
    else 422) and can be corrected between the two; admin/assistant/light/watch can never be
    self-claimed; the sleep estimate and the "is this person's device registered" checks now cover the
    whole mobile set; Android reports tablet from `Configuration.smallestScreenWidthDp >= 600` (null
    when unreadable, never guessed); the UI shows the real type and surfaces device capabilities.
  - **App updater / OTA** (`0afd1da6`): the OTA guard is now cleared when the APK upgrades - the
    recurring "Update on the server is mislabelled. Skipping to avoid a restart loop." block came from
    `reconcilePendingUpdate()` running BEFORE the native-build-change check, so on the first launch
    after EVERY APK upgrade the stale pending SHA (from the old APK's web layer) could never match the
    new APK's SHA, the server's version got blacklisted and updates were refused. The native-build
    check now runs first and a native upgrade clears the pending + rejected SHAs. (The immediate
    trigger this session was also that an Android-only release never republished the web OTA bundle,
    so the APK's embedded SHA and the advertised bundle SHA disagreed; the `ui` service was redeployed
    to republish.)
- `2026-10-25` (live fix): the deployed identity service crash-looped on startup (`no such column:
  user.forbidden_branches`) because a model change shipped without its migration, which also took the gateway
  down (the site returned 502). Fixed by shipping the missing `_add_column` migration (commit `81a1ec12`) and
  redeploying identity + gateway; `/health` and all four `check_service_urls.py` probes are green again.
- `2026-10-25 early` (bug sweep, integration fixes and the missing integrations wired up).
  - **Integrations hardened and completed** (docs/INTEGRATIONS.md): the Audiobookshelf connection test now
    logs in at the root `/login` (it was hitting `/api/login`, which ABS rejects); the Nextcloud test uses the
    user's own `/ocs/v2.php/cloud/user` and honours `ocs.meta.statuscode` instead of an admin-only endpoint that
    returns HTTP 200 even on a permission error; Nextcloud/GitHub/GitLab tests have 5 s timeouts; GitHub and
    GitLab are tested over a TLS-verifying client while self-signed hosts (Home Assistant, Nextcloud,
    Audiobookshelf, Music Assistant, Skylight) keep the insecure one; connection failures return a short message
    and never echo the exception; and no secret value (`*_token`, `*_pass`, `*_api_key`, `*_key`, `api_key`) is
    written to the log.
  - **GitHub "Protected Branches" is now saved**: the field is stored on the user and resolved to the workspace
    runtime (it used to fall back to the defaults because nothing persisted it).
  - **Private Git endpoint and username are used**: credentials are injected as
    `https://<user>:<token>@host:port/...` from the configured endpoint, and every logged git command has its
    credentials redacted (the timeout/error paths used to print the token).
  - **Skylight**: a non-admin can now turn Skylight on/off for themselves from the Accounts page.
  - **Media UI bug sweep**: an empty voice take no longer hangs the recorder panel; the tab-lock owner expires
    after 6 s so a crashed tab cannot leave every other tab stuck on "Playing in another tab"; tapping a chapter
    seeks only when playback actually started; the output sheet, output card and dashboard widget show an
    explicit "unknown" volume instead of a made-up 50; a genuine MA `volume_level` of 1 is no longer shown as
    100%; the status poll is silent (no repeated connectivity toast); the widget shows an error instead of an
    endless skeleton; the event stream no longer reports "closed" while the browser is reconnecting; and a failed
    JSON-RPC connect no longer leaks a WebSocket.
  - **Audiobook library no longer capped at 50**: the gateway pages through ABS (`page`/`limit`) and the UI
    fetches the next page, so a large library is fully browsable.
  - **Repo-wide em/en dash removal**: no `U+2013`/`U+2014` characters remain in the source; the few functional
    regexes use `\u2013`/`\u2014` escapes so behaviour is unchanged.
  - **OpenRouter's per-user tile**: the dead tile was removed this session (`d517166f`); per-user
    credentials would be dead storage because the only consumer is the global `llm_cloud_*` settings.
- `2026-10-24 evening session` (lead + parallel agents; every change cherry-picked onto
  `microservices`, no other branches kept). Details in the docs named.
  - **Media - playing on the phone works again** (BUG-74…79): play waits for MA to see the web
    player; Next/Previous on a Cast TV go to its MA twin; playlists fill the right queue on every
    target; Play next / Add to queue on every row; the output sheet shows MA groups, removes a
    speaker, ungroups, sets group volume, and only offers speakers in the leader's
    `can_group_with` (so grouped audio stays in sync). Gateway allows `players/cmd/set_members`
    and `group_volume` with per-player scope checks.
  - **Media - native background audio (P8-T2)**: swiping the app away hands the stream to
    `NativeSendspinPlayer` (same player id, PCM, clock-synced AudioTrack); reopening reclaims it.
    Emulator-verified against a mock server; **needs a real phone check**.
  - **Media - signed media tokens everywhere** (`lib/mediaToken.ts`); `?token=<api key>` is
    refused on `/api/media/*` (Sendspin/JSON-RPC sockets unchanged). Widget "null" title fixed.
  - **Lint**: ruff is clean repo-wide (three cleanup branches merged).
  - **Notes**: place reminders ("when I get home", fired by geo), attachments beside the note the
    Nextcloud Notes way (photos, files, drawings, voice), offline edit queue with If-Match replay.
  - **Bible**: side-by-side panes that scroll together by verse; reference-chip popover.
  - **Health**: per-card switches, kid-mode hiding, **kid mode forced on for every minor**
    (family role or birth year), fasting window (adults), weekly active-minutes ring, forgiving
    streaks, a lesson a day, the coach (goals, reminders, chat answers from own data), cheers and
    share-my-day, plan↔log↔shopping round trip, calendar workouts, a Health today dashboard card,
    photo (nutrition label OCR) and voice food logging as editable drafts. Shared cycle now gives
    phase and next period only (it leaked `cycle_day`).
  - **Health on Android**: Health Connect import (sleep, resting HR, HRV, weight, workouts into
    geo's store) and a Health today home-screen widget - **need a phone check**.
  - **Family guard (Phase 11)**: install approvals, permission gating, the one-minute warning,
    "ask a parent" for a blocked site, Spotify linking (needs a Spotify app registered and the
    three `spotify_*` settings), and a local-model second pass on content monitoring (needs
    `guardian_monitor_model`) - **need phone checks**. The ways around the web filter
    (unsupported browsers, split screen, safe mode) are now blocked.
  - **Family member page** (docs/FAMILY_MEMBER_PAGE.md, modelled on Aura, the top-rated app):
    `/family/member/<child>` replaces the single-column `/guardian` page (`/guardian` is now its
    own top-level page); father/admin see Devices
    (technical), the mother gets everything else with technical fields stripped server-side
    (`gateway/member_page.py`, identity `may_view_technical_data`).
  - **Media phases 3, 5, 6 and 7 shipped** (docs/MEDIA_OVERHAUL.md): the
    foundations (api client + keys, MA JSON-RPC, sendspin engine, media store and
    ingest, adapters, event stream, tab lock, media session, overlays/live
    position), the screens (Listen Now, Search, Library, Album/Artist/Playlist,
    Book, Podcast, item context menu, rebuilt dashboard widget, routes), and the
    polish (lint colour rule, artwork accent with a WCAG contrast check, visual
    baselines, reduced motion, axe on every screen, the whole journey by
    keyboard). Remaining: P6-T5 (UX ledger pass), P7-T3 to P7-T5 (screen-reader
    labels and the now-playing announcement, performance budget, image sizing),
    the Phase 8 device checklist and Phase 9 (release).
  - **Bible**: per-pane text size; verses worded differently between the two
    translations are marked (`verseDiff.ts`, a Dice-coefficient word diff).
  - **Health**: step goal days joined the weekly family challenge (veg stayed
    out). The voice-signature panel now records with the shared recorder.
  - Shared helpers created while removing copies: `lib/focusTrap.ts`, `lib/base64.ts`,
    `lib/blobUrlCache.ts`, `hooks/useAudioRecorder.ts`, `services/common/streaks.py`,
    `services/common/links.py`, `lib/mealPlanStore.ts`, `withShareNote`.
- `2026-10-24 session` Everything below up to "Suggestion lanes (server)" landed in this session; all
  deployed and smoke-checked unless noted. Details live in the docs named.
  - **Phone app: the screen no longer drifts under the bars** - the phone shell is `fixed inset-0`
    and locks the document (`mobile-shell-lock` on `<html>`), so only `<main>` scrolls; native: the
    legacy `windowTranslucent*` theme flags are gone and Capacitor's `SystemBars` gets
    `initialViewportFitValueHint: 'cover'`. Hermetic spec `e2e/layout/mobile-viewport.spec.ts`.
    The web half is live in the OTA bundle; the native half needs the new APK. **Not confirmed on
    a real phone** - ask Jeremiah whether dragging still moves the app.
  - **Health:** suggestion-lane switches for parents (`SuggestionLanesControls`); the **weekly
    digest** card (`GET /weekly`, `services/health/weekly.py`); **sleep estimated from device
    use** (phones/tablets report overnight screen-on stretches per device; the estimate joins the
    devices assigned to the person; editable before saving, saved untouched as `usage_estimate`);
    saved nights are editable (`PUT /sleep/{id}`); **recipe ↔ Health logging** (log a recipe or
    tonight's planned dinner with a portion picker); a **weekly family challenge** (water days,
    sleep nights, cook nights; leaderboard; share to chat). See `docs/HEALTH_HUB.md`.
  - **Family guard (Phase 11) steps 5-7:** app blocking and per-app minutes; a parent's **offline
    unlock code** (PBKDF2 verifier delivered only inside an online parent unlock); the **web
    filter** (`WebFilterService`, a DNS-only VPN: family-safe resolver, safe search, own lists,
    web activity on the feed); **content monitoring** (reviewed phrase list, severe concerns page
    both parents); the **Sunday weekly recap**; the parents' page **`/guardian`** (week, prayer
    and guidance with KJV verses, talk journal, activity, all controls); a child's **"I need to
    talk"**; **burden signals** from a child's own check-ins; Jarvis chat answers a parent's
    "how was Emma's week?" from the recap. See `docs/HEALTH_HUB.md` Phase 11.
    Guard, web filter and app blocking **need a device check** (no phone was available).
  - **Media:** podcast **episode lists** (BUG-43, `GET /api/media/podcast-episodes`); pending MA
    JSON-RPC calls fail at once on close (BUG-48).
  - **Notes:** list/grid toggle, undo toasts, autosave with on-device drafts and If-Match conflict
    handling, desktop shortcuts, checklist drag/reorder, version history, timed reminders,
    Nextcloud sharing. See `docs/NOTES.md`.
  - **Bible:** plan reflections and promoted verse notes go into study memory (RAG, citable ids).
  - Android lint error at `ConfigureActionButtonActivity.java:357` fixed (API-26 guard).
- `15865192` Family guard: **suggestion lanes, server side** - a parent blesses or blocks each kind of
  suggestion per child; blocked lanes return nothing (app screen added 2026-10-24).
- `HEAD` Family guard: **Jarvis protected on a child's phone + screen-time enforcement (Layer 1, 4b)**
  - device admin + the `GuardService` Accessibility guard, a parent-login cover screen, tamper
  alerts and screen-time locks; see `docs/HEALTH_HUB.md` "Tamper resistance". Compiles and the
  `GuardRules` JVM tests pass; **not yet exercised on a real phone** (Accessibility events and
  OEM Settings layouts vary). Identity's `verify_password` now compares digests in constant time.
  Pre-existing Android lint error (not fixed): `ConfigureActionButtonActivity.java:357`
  `setTooltipText` needs API 26 (minSdk 24); `lintDebug` fails on it with or without this change.
- `HEAD` Family guard: **screen-time budgets and routines, step 4a** (rules, live status, more-time
  requests; no phone enforcement yet). See `docs/HEALTH_HUB.md` "Time budgets and routines" for
  the full shape. Identity `ScreenRules`/`ScreenTimeRequest` tables are created on startup; the
  gateway's `/api/screen-time/*` routes forward the caller's own credentials so identity's
  parents-only rule applies, then add status and notices (`family_safety` category).
- `HEAD` Android: **white status and navigation bars fixed (needs the new APK).** Capacitor 8's core
  `SystemBars` pads the window by the bar insets and paints the strip with the theme's
  `windowBackground` whenever the WebView is older than 140 (`viewport-fit=cover` alone is not
  enough), and under the `DayNight` theme in light mode that is white. The theme's window
  background is now the app's dark colour, and the app's bar plugin (renamed
  `JarvisSystemBars`: it collided with Capacitor's `SystemBars`) paints the strip with the page's
  own background and re-applies after Capacitor's reset on configuration changes.
- `HEAD` Family guard: **game and activity suggestions** (Phase 11 step 3 complete apart from a
  parent's bless/block per lane). Games: reviewed themes over the week's per-app time and
  media-session titles (`game_recommendations.py`; Fortnite → Rocket League, GTA → LEGO City
  Undercover, Roblox → a family Minecraft world, casino/loot → a board game night; good fits get
  nothing). Activities: late nights → a phone-free wind-down, long days → time outside
  (`activity_recommendations.py`, household `TIMEZONE`). Both render in the For You widget;
  routes read only the caller's own data and return only the suggestion.
- Data repair 2026-10-24: trivia wins that were filed under display names
  (`tel:activity:Jeremiah`, `tel:activity:Michele`) were merged by time into the account feeds
  (`jeremiah` 65 events / 9 wins, `michele` 43 / 36) and the stray keys removed; backup on the
  server at `/home/jeremiah/telemetry-backup-merge/`.
- `HEAD` Health + privacy: **digital wellbeing card; usage is parents-only on the server;
  media proxies pool connections.** `GET /api/usage/{user}` let any signed-in member read anyone's
  phone usage (a child a parent's, siblings each other's); identity now owns the rule
  (`may_view_raw_data`, `GET /api/internal/may-view-raw`) and the gateway refuses with 403.
  `DigitalWellbeingCard` (Health → Today) shows screen time, first/last use, sessions, top apps and
  the week, for yourself (adults) or each child (parents). The ABS/HLS and Music Assistant
  stream proxies borrowed a new `ClientSession` per track and per HLS segment; they now share one
  pooled session per event loop (`_PooledMediaSession`, no cookie jar). Left as-is on purpose:
  execution's MA search websocket (LAN, token auth, tens of ms) and webOS/ha_config websockets
  (per-command, admin/TV paths). The gateway's "Unclosed connection" (to `rag`) at every
  restart was the Raven warm-up never reading its response; fixed 2026-10-24.
- `HEAD` Chat + trivia: **instant, fair, and Jarvis speaks as Jarvis.** Reviewed the live
  "Jeremiah & Michele's Chat" game (18:39-18:42 MST, which ran *before* the 18:44 deploy):
  Michele won all ten rounds because (1) late answers burned the next question's guess (fixed
  by the late-answer guard, now live) and (2) answers were judged only after the player's own
  post finished (2-5 s with the old re-login), so a later answer could win. Answers are now
  judged on arrival (`_judge_game_answer`, then `announce`). Bot posts (questions, verdicts,
  @Jarvis answers) go through `_post_as_jarvis` using a new Nextcloud account **`jarvis`**
  (created 2026-10-24; login in the server `.env` as `JARVIS_BOT_NC_USER`/`_PASS`, backup
  `.env.bak-jarvis-bot`); 1:1 rooms fall back to marked posts. Chat long-polls Talk
  (`lookIntoFuture=1`, `GET /api/communication/talk/messages/wait`, `useTalkLongPoll`), so a
  new question reaches every phone at once; the timer drops to a 15 s safety net while the
  long poll is healthy. Bible trivia grew from 17 to 174 reviewed questions and the matcher
  was tightened (plurals/typos forgiven, fragments refused). Wins now record under the account
  id, not the display name (old wins sit under `tel:activity:Jeremiah`/`Michele`).
  HA `get_areas` 401s were a **false positive**: HA's `/api/template` is admin-only
  (`@require_admin`), so a valid non-admin token is refused there; it no longer raises a
  credential alert and falls back to the household `HA_TOKEN`.
- `HEAD` Family guard: **recommendations moved to a "For You" dashboard widget; seeded test
  data removed** - the four suggestion cards left the Health page and are now one
  `recommendations` widget (`RecommendationsWidget`), default-visible for every role
  (identity `CHILD_WIDGETS` + `WIDGET_DEFAULT_SIZES`). The artists Jeremiah saw were not real:
  earlier "verified live" steps had posted fake events into his production feed (Drake,
  PewDiePie, a "smoke test" play, a `/notes` beacon smoke, 555 call/SMS, and a `source: smoke`
  usage day). Those 6 events and the usage record were deleted 2026-10-24 by exact value
  (backup on the server: `/home/jeremiah/telemetry-backup-20261024T013229Z/`).
- `HEAD` Fix: **chat and Nextcloud latency - stop re-logging in on every request** - measured
  from execution, any authenticated Nextcloud call took 2-5 s (`status.php` 0.1 s): the
  pooled aiohttp session uses `DummyCookieJar` (so one user's cookies never ride another's
  request), which made Nextcloud re-check the password on every Talk fetch, every bot post,
  every note read/write and every thumbnail. `http_client.request`/`fetch_bytes` now keep
  Nextcloud's session cookies per host + user + password hash (`_LOGIN_COOKIES`), drop and
  retry once on a 401, and do this by default whenever `auth` is sent (all such callers
  are Nextcloud). Live probe: ~0.3 s per call with the cookie; WebDAV PUT/MOVE/DELETE with
  cookie + Basic pass Nextcloud's CSRF check. The CalDAV client is cached per login for
  10 min (it was rebuilt, reconnected and re-authenticated per calendar action), and the
  storage service caches Nextcloud/Calibre providers per login + library (each request had
  built a new client and, for Calibre, re-downloaded `metadata.db`). Trivia also takes a
  late answer to the question that just closed (within 20 s, `TRIVIA_LATE_ANSWER_SECONDS`)
  as late: it neither scores nor burns the player's one guess on the new question, and the
  bot says a new question is up. Still open from the audit: Music Assistant search opens a
  new websocket + auth per search (`handlers/mass_client.py:169`), webOS/ha_config open a
  new HA websocket per command, the gateway ABS/HLS proxy builds a new `ClientSession` per
  segment (`gateway/main.py` ~14121/14260/15063), and Talk still has no push (clients poll
  2 s during games, 10 s otherwise; Nextcloud's long-poll `lookIntoFuture=1` is the next step).
- `HEAD` Fix: **review of `c68f55e4`** - (1) a missed or timed-out trivia round revealed the
  *next* question's answer (the reveal read `state.answers` after `advance_trivia`), and a
  missed final question raised `IndexError` inside the unawaited game task, so the game never
  said "Game over"; the answer is now read first and background game-task failures are logged
  (`_game_task_done`). (2) Chat brisk polling never ended: `sentRecently` compared against
  `now`, which only ticks while the Jarvis indicator shows, so every room polled at 2 s after
  any send; it now reads the real clock. The game detector also looked for `❓ Question`,
  which the server never posts (prompts are `🧠 Trivia - question N of M`), and scanned the
  unsorted feed; it is now `gameRunning()` in `chatModel.ts`, tested. (3) Podcast/book
  suggestions matched substrings over every media event, so playing *Newsboys* on Spotify
  produced a "worldview news" podcast pick, `drama` shadowed `audio drama` and `fantasy`
  shadowed `epic fantasy`; triggers now match whole words, longest first, and only real
  podcast/reading events reach them (`podcast_events` / `book_events`: podcast/audiobook/
  reading features, or media from a podcast/audiobook app or naming a show/book). Jarvis
  audiobook plays (`feature: audiobook`) now count for books.
- `c68f55e4` Family guard: **podcasts and books recommendations from the digital profile** -
  Jarvis now reads recent podcast listening and reading activity from telemetry and suggests
  redeemed alternatives. `services/common/podcast_recommendations.py` and `services/common/book_recommendations.py`
  map themes to vetted Christian shows/authors (news worldview, theology study, church history,
  parenting, audio dramas, classic apologetics, epic fantasy, etc.) or redirects when unredeemed.
  New routes `GET /api/recommendations/podcast` and `GET /api/recommendations/book` return only the
  derived suggestion (raw stream is parents-only). Rendered via `PodcastRecommendationCard` and
  `BookRecommendationCard` on the Health page. Verified with unit & integration tests.
- `c68f55e4` Fix: **chat trivia lag and location upload timeout storms** -
  (1) Trivia chat lag: Server-side execution now decouples game answer processing (`_apply_game_answer`)
  into background tasks (`asyncio.create_task`) when a short message arrives in a room with an active game,
  so the user's `send` HTTP request returns immediately in <1.5s instead of 16s. Star banking and
  telemetry activity run concurrently with Nextcloud Talk post via `asyncio.gather(..., return_exceptions=True)`.
  Bot answer and next round prompt are merged into a single Talk message, halving OCS round trips.
  Client-side `ChatPanel.tsx` now polls every 2s (instead of 10s) whenever a game is active in the room or
  for 15s after sending a message.
  (2) Location timeout storms: In `services/identity/main.py` (`update_user_location`), Home Assistant
  and Geo location forwarding are offloaded to FastAPI `background.add_task` instead of being awaited
  inline, preventing gateway middleware timeouts during phone reconnection bursts or HA token outages.
- `73ef660c` CI fix: **pin BIBLE_BOOKS_PATH in conftest** so full-suite test_config_env_isolation passes.
- `7b5dc17e` CI fix: **give five service tests unique basenames** (`test_gateway_artifacts.py`,
  `test_storage_artifacts.py`, `test_geo_activity_feed.py`, `test_telemetry_activity.py`,
  `test_telemetry_api.py`) so pytest --collect-only in SOA Microservices CI succeeds without
  duplicate module collisions.
- `451e671b` Notes: **Keep-style visual note editor over Nextcloud markdown** (see `docs/NOTES.md`
  "Visual editor") - notes open as formatted text with tickable checkboxes and a formatting
  toolbar (bold, italic, strike, heading, bullet/number lists, checklist, text colour,
  highlight, size); the stored file stays Nextcloud Notes compatible markdown (`lib/noteRichText.ts`).
  Colour/highlight/size are stored as whitelisted inline `<span style>` tags; notes with
  unsupported markdown (tables, code fences, etc.) open as text with a notice so they are
  never flattened. Widgets and `check_off` ignore the spans. Deployed and verified live.
- `cc816bdf` CI fix: Android `PhoneUsagePlugin` `JSONException` compile error and `HEALTH_HUB.md`
  heading lint (all four workflows green again).

- `07b8feb5` Family guard: **imported devotional and study-book pages land on the activity
  feed** - the shared imported-book reader posts one activity event as each page opens
  (feature `bible`, item "Book - Page N", `read`), so the family profile sees devotional
  reading next to chapters. The in-app recorders are now complete: trivia wins, media plays,
  app pages, chapters, imported reads. Verified live.
- `eb3be87f` Family guard: **Bible chapters land on the activity feed** - opening a chapter
  in the reader posts one activity event (feature `bible`, item "Book chapter", `read`)
  alongside the existing `chapter_complete` event, so the family profile sees reading.
  Trivia wins, media plays, app pages and chapters are wired; devotionals come next.
  Verified live.
- `fb29fa6b` Family guard: **the app's own pages land on the activity feed** - the web bundle
  carries a quiet beacon (one fire-and-forget view per visited page, feature + path) posted
  through the gateway's `/api/activity` to execution's `activity.record`; the caller's identity
  is recorded, never a body-supplied user. With trivia wins and media plays, the feed now sees
  games, media and app pages; verses come next. Verified live.
- `d14f4476` Family guard: **media plays land on the activity feed** - a successful music,
  video, podcast or audiobook play through Jarvis records to the person's stream after the
  play succeeds (`activity.record`; failures record nothing). Trivia wins were the first
  recorder; app feature pages and Bible verses come next. Deployed and verified.
- `6ae23fe6` Family guard: **external app media sessions land on the activity profile** - a
  notification-access media-session reader (mirroring `PhoneUsagePlugin`) pulls what YouTube
  and other apps publish while playing (title, artist, album, playing state) and posts it to
  the activity feed (`feature: media`, `source: external`), so listening outside Jarvis lands
  on the digital profile. Android-only; the sync stays quiet without notification access.
  Per-app foreground time and deeper metadata follow on the same engine. Verified live
  (served bundle ships `MediaSession`).
- `2d80bf92` Fix: **HA "login works again" notice no longer spams while flapping** -
  `auth_failed` re-armed the credential flag during the quiet window, so every later accepted
  call fired another recovery notice. A rejection inside the quiet window now stays quiet
  without re-arming; recovery only fires for a genuine new outage after the window passes.
  No "login works again" lines since deploy.
- `3de98f13` Family guard: **video themes redeemed or redirected** - Jarvis now reads a
  member's recent watching (media sessions) and suggests a redeemed alternative for a
  creator or theme that is not appropriate: first try to redeem the theme (a wholesome
  creator with the same kind of content - gaming, comedy, science, vlog, fitness, music
  video, film, animation), and where no redeemed equivalent exists (news, pranks) redirect
  to a different wholesome activity. A reviewed theme/creator map
  (`services/common/video_recommendations.py`) maps themes to a vetted creator with a
  plain-language reason and a redeemed/redirected flag. `GET /api/recommendations/video`
  resolves the signed-in member, pulls recent activity from telemetry, runs the matching,
  and returns only the derived suggestion (the raw feed stays parents-only). A
  `VideoRecommendationCard` on the Health page surfaces it to the member. Music and video
  recommendations now both live on the Health page. Verified live (PewDiePie → DanTDM).
- `8d5bc4f6` Family guard: **music style matching, the first recommendation brick** - Jarvis now
  reads a member's recent listening and suggests a Christian artist with a similar style
  (genre, era, energy) instead of an artist whose lyrics are not appropriate. A reviewed
  genre/artist map (`services/common/music_recommendations.py`) maps lanes (pop, hip hop,
  rock, country, electronic, indie, acoustic, latin, classical, R&B) to a vetted Christian
  artist with a plain-language reason. `GET /api/recommendations/music` resolves the
  signed-in member, pulls recent activity from telemetry, runs the matching, and returns
  only the derived suggestion (the raw feed stays parents-only). A `MusicRecommendationCard`
  on the Health page surfaces it to the member. Music first, per the build order; video
  themes (redeemed or redirected) follow. Verified live (Drake → Lecrae).
- `ac963b2c` Family guard: **calls and SMS land on the activity profile** - the sideloaded
  app now reads the call log and SMS store with runtime permissions (READ_CALL_LOG, READ_SMS)
  and posts each new call and message to the activity feed (feature calls/sms), with direction,
  duration (calls) and body (SMS), contacts resolved best-effort. A `CommsPlugin`
  (hasAccess/requestAccess/getNewCalls/getNewSms) plus `CommsSync` (5-min poll) reuses the
  existing activity pipeline; a per-store timestamp cursor keeps it incremental and caps each
  pass at 200 rows. Android-only: the sync stays quiet without permission or a token.
  Reuses `/api/activity`, no backend changes. Verified live (call + SMS events round-tripped).
- `eb0d9071` Family guard: **per-app foreground time joins the digital profile** - the phone's
  `getToday()` now also returns the day's top 20 apps by foreground time (UsageStatsManager),
  posted through `/api/usage` and merged per package into the day record's `apps` array
  (longest millis, latest last-use wins; re-report never shrinks a row). Top-apps view lands
  on the parents' surface later. Verified live (POST+GET usage round-trip including the apps
  array).
- `8c8c81c1` Family guard: **general phone usage lands on the activity profile** - the phone's
  Usage Access now feeds the digital profile: a `PhoneUsagePlugin` (mirroring
  `StepCounterPlugin`) reads `UsageStatsManager` (daily screen time, when the day started and
  stopped via first/last use, and the session count) and syncs through `/api/usage` to
  telemetry's `tel:usage:{user}` store, merged by day. A `PhoneUsageSync` component posts it
  every five minutes from the web bundle; the gateway injects the authenticated user.
  Android-only; the card hides elsewhere and the plugin exposes `openSettings` for a future
  Settings button. Per-app detail follows. Verified live (POST + GET via the deployed route).
- `e54d8cd6` Execution runs on the **host network** (the only service that does) and had no
  telemetry address - telemetry publishes no host port, so the new activity recorder and
  every execution-origin admin notice were silently failing. `TELEMETRY_SVC_URL` now points
  at telemetry's fixed bridge address (`172.26.0.253:11438`), the `HOST_` form is documented,
  and the feed was verified live (a recorded event read back over HTTP).
- `b200f8d4` Health: **collected data is parents-only, suggestions are the member's** - the
  Phase 11 visibility rule: a minor's raw feed is open to both parents (including the linked
  mother) and no one else; the member sees their suggestions and recommendations, never the
  stream behind them; the father-mother parents' page stays mutual and revocable.
- `590c7de1` Health: **video themes get redeemed or redirected too** - a YouTube video or
  creator with a negative theme is first redeemed (the same theme told well by someone who
  lifts people up) and only redirected when redeeming is not possible; creators are
  first-class, reviewed once and applied everywhere.
- `620eace8` Family guard: **the activity feed and the full-parity guardianship plan** -
  telemetry keeps a capped per-person activity stream (`POST/GET /api/telemetry/activity`),
  execution records through `activity.record` (trivia wins wired first; music, video, pages,
  verses next), and Phase 11 was rewritten to full Bark/Qustodio/Family Link parity,
  **profile-first**: device collection → one living digital profile per person → Jarvis
  recommendations with positive Biblical direction (style-matched music, video themes
  redeemed or redirected). Deployed `telemetry` + `execution`.
- `48657a16` Health: **digital guardianship** is planned (Phase 11) - the Family
  Link/Qustodio/Bark class kept on our own server: an activity feed for music,
  games, pages and verses; three-audience profiles (the person, their parents, the
  linked mother for a minor) plus a mutual father-mother page; boundaries through
  the house DNS and the app; local-only, gentler concern guidance; a prayer and
  discipleship surface grounded in real activity; private note lanes for the quiet
  kids and spouses. Docs only.
- `5a4e4748` Recipes: **search the offline index first** - a local SQLite FTS index over
  **318,830 CC0 Food.com recipes** (`scripts/build_recipe_seed.py` → `data/recipes/recipe-seed.db`,
  bound into execution; ingredients carry real units joined from the Karo8870 mirror) answers
  `recipe_search` instantly and TheMealDB only fills what is left; `shelf_fetch` reads one full
  recipe by id. The Recipes panel tags cards Food.com/TheMealDB and opens Food.com picks locally.
  Verified live (blueberry search returned 12 offline hits; a fetch returned the full recipe).
- `753624fb` Family: the tab row is icon-only below `sm` (labels return from `sm` up, aria-labels
  keep the names), so five tabs finally fit a phone; the Announce panel dropped its calendar and
  notes link cards - timers and announcements are the whole panel.
- `93e6b7d3` Health: a **digital wellbeing** phase is planned (phone usage sessions and per-app
  time from Android's `UsageStatsManager`, a hub card with first/last use, screen time, a session
  timeline and top apps, plus what each person does most with Jarvis). Docs only so far.
- `99306dbd` Recipes: **search TheMealDB from the Add-a-recipe panel** - when a link is
  not to hand the panel searches TheMealDB and shows photo results; picking one saves like
  any imported recipe with attribution to themealdb.com. Verified live through the
  deployed route (12 results with full ingredients and steps).
- `50db931f` Recipes follow-ups: a **name filter** over the shelf on the Recipes page
  (instant, offline) and a yield normalizer for imported recipes (a site sending
  `["12", "12 cookies"]` now reads "12 cookies"). Verified live by re-importing the
  pinchofyum recipe through the deployed route.
- `40d0a447` Recipes: import a recipe from a pasted link - an **Import a link** panel
  on the Recipes page posts the URL to `/api/recipes/import` (execution's new
  `recipe_import` handler), which fetches the page and reads its schema.org `Recipe`
  JSON-LD (arrays and `@graph` included), previews title/site/time/yield/counts, and
  **Save to Recipes** writes the note with guessed level labels and a source line.
  Fails plainly on pages without recipe data. Verified live (imported a pinchofyum
  cookie recipe through the deployed route).
- `69ce717a` Location uploads coalesce (one request in flight, one trailing -
  the same guard the step sync got) and identity's SQLite branch pool is
  widened to 20+40 with a 60 s wait. Michele's phone had fired parallel
  location posts that exhausted the default 5+10 pool, so identity served
  500s and the gateway logged timeout bursts. `ui` + `identity` deployed.
- `bf035858` Recipes share: the week plan and the shopping list can go into a
  family chat in one tap - Share plan / Share list on the Meal plan panel send
  a short `🍽 This week's dinners:` / `🛒 Shopping list:` summary (capped at 25
  items) through the same sheet recipes use. Live on OTA 1.5.2.
- `e6e98290` + `56b30756` Credential-expiry alerts: a stored login that stops
  being accepted (Home Assistant long-lived tokens first) raises one deduped
  admin notice through telemetry; repeats stay quiet, a recovery notice with
  the same tag replaces it on the phone, and a ten-minute quiet window keeps a
  flapping login from ringing twice. Live-verified.
- `a3e4921b` Family fold: timers and announcements moved in as the Family
  page's **Announce** tab (next to Chat), so `/communication` now redirects to
  `/family` and the standalone page is retired. Live on OTA 1.5.2.
- `a20f7e30` Recipes: plan the week and build the shopping list - the Recipes
  page's **Week plan** picks a dinner per day from the shelf (a tick marks it
  cooked, a surprise-me fill, repeats flagged) and saves `Recipes/Meal plan.md`
  as a `#meal-plan` checklist; **Build shopping list** turns the week's
  ingredients into `Recipes/Shopping list.md`, merging duplicate lines and
  keeping ticked items by text. Live on OTA 1.5.2 (bundle `a20f7e30`).
- `831e29e6` Chat: @Jarvis answers from the room itself - both answer paths
  (the app's send and the gateway's Talk monitor) now lead with a live
  snapshot of the room's recent messages: replies appear as assistant turns
  with the envelope stripped, everyone else as "Name: text". The stored
  room transcript is only the fallback. This is the fix for "the bot has no
  clue about the room it is in".
- `cb530641` Recipes: share to chat and teach Jarvis - the Recipes page can
  send a recipe into a family chat through the generalized
  `ShareToChatSheet` (a short `🍽 title: excerpt` message with an optional
  note, never the whole file), and a header **Teach Jarvis** button runs the
  notes RAG sync over `Recipes/`. OTA 1.5.2 republished on this bundle.
- `d8acfcab` Steps and version 1.5.2: the app's step sync coalesces to one
  request in flight (a walk used to POST one per pedometer reading - an ~11/s
  storm; the one-minute burst of upstream timeouts it caused was logged as
  hundreds of ERROR tracebacks). The gateway now logs `TimeoutError` as a
  single line and keeps tracebacks for real faults. `ui` + `gateway` deployed,
  OTA 1.5.2 published.
- `be729dcd` Chat: @Jarvis in Nextcloud Talk keeps a room transcript - a
  room-scoped Redis memory (last ten messages, two hours), shared by the
  gateway's queued Talk answers and execution's app-sent path, so replies
  follow what the room has been saying. Personal chat history stays per-user.
- `0ea5a938` Family trivia: ten-question sessions with a roster locked from
  the room's participants when the session starts (the bot and late joiners
  sit out; a display name and account id resolve to one player). Questions
  start easy and the difficulty steps up every three correct answers and
  never falls; one guess per player per question; the first correct answer
  wins the round, all-wrong or a two-minute quiet round reveals the answer
  and moves on; stars still bank in geo.
- `fb95eccd` Recipes Phase 0-1: `scripts/import_recipes.py` selects ~40
  kid-tiered recipes per level from the CC0 Food.com dataset (522,517 recipes,
  fetched via anonymous `kagglehub`), renders Nextcloud-Notes markdown from
  each live food.com JSON-LD (ingredients with units, steps, per-serving
  `foodcom` nutrition, ×7 servings) and wrote 120 level-tagged notes
  (`#level-1/2/3`) which were uploaded to Nextcloud `Recipes/`. App-side
  Recipes phases (2+) are still planned. Script-only commit - no deploy.
- `d766a19f` Health food seed: `scripts/build_fdc_seed.py` builds a read-only
  USDA FoodData Central SQLite seed (Foundation 395 + SR Legacy 7,793 + FNDDS
  5,432 = 13,620 foods, CC0), served from the server at
  `data/health/fdc-seed.db` (not in git; rebuild + re-copy on USDA releases).
  `/food/search` merges seed hits (with household portions like "1 cup,
  chopped" = 91 g) ahead of OpenFoodFacts branded results; barcode lookups
  cache each product permanently in the `offproduct` table; the UI shows USDA
  tags, portion chips and an offline note for branded search.
- `8e4e8a27` Dashboard role-aware starting widgets (child/teen/parent/admin).
- `091e5d85` Notes bulk multi-select, Keep-style markdown preview, single-note
  Android widget, calls ring until answered/denied, family roles as real RBAC
  roles.
- `f5304149` Family game (trivia/bible/memory) answers applied from the app's
  chat-send path, so replies work in any room.
- `84919557` Gateway talk-monitor forwarding + queue art via Music Assistant
  `proxy_id` (the forwarding part was superseded by `f5304149`).
- `174c4feb` Notes meta retry, bible hint fix, mobile player insets, desktop
  device grid, Nextcloud probe in admin alerts.
- `81498e85` MiniPlayer above the measured bottom bar, proxied cover art,
  queue track titles, ABS timeouts and clear "unreachable" errors.
- `8c6f5dc7` Classified service probes, admin media-down alerts,
  `scripts/check_service_urls.py`.
- `ba2c7cf7` De-gatekeeping pass (READMEs, copy, comments).
- `62f11187` Spouse cycle sharing with phase-aware partner suggestions; cycle
  shown only for a `female` profile.
- `21f6b51e` Health sex is female/male/unspecified ("other" removed).
- `df427426` Family roles (`father/mother/child/teen/adult_child/other`) and
  opt-in cycle tracking.
- Health hub slice `4dc84d17` + timezone/share fixes `b0c0a78c`: profiles, open
  metric ingestion, sleep, readiness, check-ins, nutrition, sharing, export.

## 1. Working rules

- **No hardcoded fallbacks** (standing user instruction). Show real data or an
  explicit loading/empty/error state - never fake values.
- **Never act through a family member's account** to inspect or test (reading their rooms,
  posting as them). Use the household account, the `jarvis` bot, or ask Jeremiah first.
- **Live test data is always cleaned up** (standing user instruction). Prefer engine-level
  checks that write nothing; if a smoke test must post to the live server, note the exact
  record and delete it by exact value right after (`redis-cli lrem <key> 1 "<json>"`, not
  `-x`), then confirm it is gone. Never leave seeded artists/creators/calls in a real feed.
- **UI gates**, run in `services/ui`: `npm run typecheck`, `npm run lint`
  (baseline 0 errors / 11 pre-existing warnings), `npx vitest run`
  (288 files / 2208 tests), `npm run build` (also packages the OTA bundle).
  After adding Tailwind colour classes run `npm run gen:theme-css` or
  `src/themes/themeRemap.test.ts` fails.
- **Backend tests**: `.venv/bin/python -m pytest <paths>` - `python` is not on
  PATH. Baselines verified 2026-10-26:
  `services/gateway/tests services/common` -> **1865 passed**;
  `services/execution` -> **1053 passed / 60 skipped**;
  `services/rag/tests` -> **117 passed / 14 skipped**;
  `services/health services/identity services/telemetry services/bible services/execution`
  -> **2587 passed / 60 skipped**;
  `services/identity services/tests` -> **891 passed / 24 skipped**.
  A full `pytest services` run also passes - but note it does **not** collect
  `tests/unit/`, and CI does: run `.venv/bin/python -m pytest tests/unit -q` as
  well before pushing. `tests/unit/test_config_env_isolation.py` is the guard
  that every env var `services/config.py` reads is pinned in the root
  `conftest.py` `_TEST_ENV_DEFAULTS`; it was red in CI for a day (2026-10-25 ->
  2026-10-26) because `ALERT_TLS_HOSTS` was read without a pin, and a green
  local `pytest services` hid it. Ignore
  `services/execution/tests/test_calendar_window_and_uid.py`; it needs the
  optional `vobject` package.
- **Deploy**: `./scripts/deploy_remote.sh jeremiah@192.168.2.205 /home/jeremiah/SharedLLM <services>`
  in the background (~5 min; waits for GitHub Actions *Build & Push Images* and
  pins the server to the **branch tip**). Land code and docs together; a
  docs-only tip makes un-rebuilt images fall back to `:latest` with a warning.
  Docs-only changes need no deploy.
- **Post-deploy verification** (added after a missed log spike): after every
  deploy, beyond the script's own health check, (1) aggregate ERROR/Traceback
  counts per hour for each touched service from
  `docker logs --since … --timestamps` on the server (pass Z-suffixed UTC
  timestamps - the daemon reads bare timestamps as local), (2) smoke-test the
  shipped path itself (engine-level checks inside the container are fine; do
  not post into family rooms), and (3) check admin notifications. One slow
  minute that logs a burst of middleware tracebacks is exactly what this
  catches.
- **Android unit tests**: use Homebrew `openjdk@21`
  (`JAVA_HOME=/usr/local/opt/openjdk@21` and
  `-Dorg.gradle.java.installations.paths=/usr/local/opt/openjdk@21`) in
  `services/ui/android`. CI `.github/workflows/android-build.yml` runs on push
  to `microservices` under `services/ui/**` and builds the debug APK.
- **Playwright**: hermetic projects against `npx vite --port 5179`; chromium
  cache symlink workaround; iPhone/WebKit cannot run locally.
- **LLM stack**: text-only assistant model; home desktop runs Alpaca
  (Ollama-compatible `llama.cpp`) behind
  `llm_local_url = http://jeremiah-home-desktop.local:11434`; vision via
  Alpaca `/api/vision/ocr` (Qwen2.5-VL). `llm_cloud_api_key` is empty.
- Report concisely; verify live before claiming success; the user is Jeremiah
  (family of 7, kids 7-18) and prefers local/free, kid-safe, no-subscription
  designs.

## 2. Live features worth knowing

- **Health hub** (`services/health`, port 8011, SQLite):
  Today / Fitness / Sleep / Nutrition / Health tabs. Profiles, source-tagged
  idempotent metric ingestion, sleep scoring, readiness, morning check-ins,
  food search/barcode/manual nutrition with a local USDA food seed
  (`data/health/fdc-seed.db`, built by `scripts/build_fdc_seed.py`; searched
  first, and OpenFoodFacts answers the branded tail) and a permanent
  per-barcode product cache, water, adult calorie ranges,
  kid guidance ranges (never deficits), scoped family sharing, JSON export.
  Steps/workouts/distance stay in **geo** and are composed in the UI; the
  summary returns `activity: null` on purpose.
- **Cycle tracking** (opt-in, in the Health tab): period and symptom logs,
  phase estimates (menstrual/follicular/ovulation/luteal/late) as calendar
  estimates, irregular support, owner-only summary. Shown only when the profile
  sex is `female`; existing data is hidden and kept otherwise. Family sharing
  is opt-in per person: adults may share with any chosen viewer and the viewer
  sees phase plus short partner suggestions; known minors (or `kid_mode`) may
  share only with their one designated guardian. Family projection never
  includes flow, symptoms, BBT or notes.
- **Family roles**: `father|mother|child|teen|adult_child|other`, seeded as
  real RBAC roles (`Father (admin)`, `Mother`, `Teen`, `Adult child`, `Child`).
  Setting a family role syncs role membership automatically. Mother gets the
  device permissions plus `family.manage`; child/teen family sharing is locked
  on (circle, cannot opt out); adult child can opt out. One linked mother per
  minor. Managed in Admin → Users → Family (admins) or Settings → Family roles
  (mothers).
- **Family guard / screen time** (phone side is Android-only, and needs the current APK):
  rules per child - a daily limit, weekend limit, per-weekday limits, bedtime, a school
  window, pause, and parent-granted bonus minutes - are evaluated by
  `services/common/screen_time.py` and served as `GET /api/screen-time/{user}/status`;
  parents set them from the member page (`/family/member/<child>`) or the Devices page.
  On the phone the **Accessibility guard** (`GuardService`) enforces them: while the lock
  is up it draws a **full-screen overlay** (`GuardOverlay`, a `TYPE_ACCESSIBILITY_OVERLAY`
  window) over everything including the home screen, a tablet's taskbar and the navigation
  strip, so Home and Back cannot leave it. The lock names the reason and offers "Ask for
  more time" (`POST /api/screen-time/requests`), "Message a parent" (`POST /api/guard/message`,
  which opens the 1:1 family chat with each parent and sends the text as the child) and
  "Open Jarvis" - all painted over the lock as a sheet, so the lock is never taken down -
  plus a parent's own login (`POST /api/guard/parent-unlock`: ten minutes, five tries per
  ten) or their 6-12 digit offline code. A parent's own always-allowed list can only **add**
  to a safety core (dialer, emergency, in-call, Jarvis), never remove it. On a **device-owner**
  phone the lock is Android's own (`lockNow`) plus lock-task kiosk mode, uninstalling the
  guard is blocked, and the UserManager restrictions and the remote wipe apply. With the
  guard off a lock is only a notification, which is why the Devices page says "Locked" only
  when the phone reports a device owner or the guard on (`mdm.real_lock_reported`). Also
  live: app blocking, the DNS-only web filter, install approvals, permission gating, the
  one-minute warning, and tamper / guard-missing reports.
- **Dashboard role defaults**: child sees chores, calendar, media, notes, verse
  of the day, assigned device control; teen adds climate; non-admin adults see
  everything except Workspaces; admins see Workspaces too. Any saved choice
  wins, and hidden widgets (including Workspaces for teens and up) can be added
  from the dashboard widget list.
- **Notes**: Google-Keep-style cards and editor with markdown rendering
  (`react-markdown` + `remark-gfm`) and a Write/Preview toggle; markdown files
  stay Nextcloud-Notes compatible (`# Title\nCategory: X\n\n`). Pin/colour/
  archive/trash/restore/duplicate via `Notes/.jarvis-notes.json`; bulk
  multi-select for archive/trash/restore/delete-forever; archive paths keep
  their subfolder and moves never overwrite (server renames on collision); the
  index has an async lock. Android home-screen widgets: the notes **list**
  widget and the **single-note** widget (tap checklist rows to `check_off`
  without opening the app; plain notes deep-link to `/notes?note=<path>`).
- **Media**: global MiniPlayer, full now-playing sheet (safe-area aware),
  output picker, reorderable queue with artwork and real track titles, keyboard
  shortcuts; covers and MediaSession art route through the gateway image proxy;
  queue art prefers MA `proxy_id`. ABS status/timeouts hardened.
- **Calls**: incoming family calls ring (chime + vibration every 2.5 s) until
  answered, dismissed or ended.
- **Family games (chat trivia/bible/memory)**: trivia runs ten-question
  sessions - a roster locked from the room's participants at start (the bot
  and late joiners sit out; one guess per player per question), difficulty
  rising every three correct answers and never falling, the first correct
  answer wins the round, and all-wrong or a two-minute quiet round reveals
  the answer and moves on. Answers in app-sent messages are applied by
  execution's send path (`_apply_game_answer`).
- **@Jarvis in family chat**: both answer paths (the app's send and the
  gateway's Talk monitor) lead with a live snapshot of the room's recent
  messages, so replies follow the conversation; the stored per-room transcript
  (last ten turns, two hours) is the fallback. Personal chat history stays
  per-user.
- **Credential alerts**: stored logins that stop being accepted (Home
  Assistant long-lived tokens first) raise one deduped admin notice through
  telemetry; repeats stay quiet, a recovery notice with the same tag replaces
  it on the phone, and a ten-minute quiet window keeps a flapping login from
  ringing twice.
- **Service health**: classified probes in the gateway; admin alerts when
  Audiobookshelf, Music Assistant or Nextcloud go down; `check_service_urls.py`
  for a local pass/fail.
- **MDM / Devices (Phase 12)**: every family device is inventoried (kind, owner, app
  version, reported capabilities, last seen) on the Devices page; a parent can push an app
  update to one, push shared School/Library files to a child's device (they open in the
  in-app viewer or download), lock/unlock a device (that is the screen-time pause, audited
  only when the phone can really lock) and request a wipe (needs a device-owner phone).
  `docs/MDM.md`.
- **Family pages and OTA**: `/family/member/<child>` is the parent's single page for one
  child (chores, screen time, devices, wellbeing, sharing) and `/guardian` is the parent's
  own view (the week, prayer, digital wellbeing, concerns, journal, weekly recap,
  find-my-phone). The Android app updates itself over the air from
  `/api/app-updates/version` (`docs/MOBILE_OTA_UPDATES.md`).
- **Also live**: Bible reader/imported books/devotionals, calendar, geo steps,
  chat/voice, devices, recipes plan docs.

## 3. Planned programs

### Food database investigation (decided 2026-10-22, built)

The comparison lives in `docs/HEALTH_HUB.md` ("Food data sources") and
`docs/RECIPES.md` ("Food and nutrition data"): mirror USDA FDC's three
non-branded types (Foundation + SR Legacy + FNDDS, 13,620 foods, CC0) as a
read-only seed built by `scripts/build_fdc_seed.py`; Open Food Facts stays the
branded/barcode layer with a permanent per-product cache; paid APIs (all forbid
caching) and a full OFF self-host are rejected. Shipped in `d766a19f`.
Corpus side: the CC0 Food.com dataset carries per-recipe nutrition (verify
per-serving vs per-recipe on first download); computed values only as fallback
via recipe-scrapers → ingredient-parser-NLP → seed match.

### `docs/RECIPES.md` - Phases 0-10 largely built, rest planned

Phases 0-1 are shipped: the importer (`scripts/import_recipes.py`) filters the
CC0 Food.com corpus by the kid level rules, ranks by staples/few ingredients/
short time and renders level-tagged markdown notes (per-serving `foodcom`
nutrition, scaled to 7 servings) - 120 notes live in Nextcloud `Recipes/`.
The Recipes page shares any recipe into a family chat and has a Teach Jarvis
RAG-sync button (`cb530641`). Phases 2-10 are now largely built: the weekly meal
plan and shopping list (Phases 3-4), search & import over the offline FTS index
plus TheMealDB and schema.org paste-a-link with a duplicate guard (Phase 8,
`6932452c`), photo/handwriting import through Alpaca OCR with an editable draft
(Phase 9, `b891938b`), and the Health tab connection - per-serving nutrition, the
"Estimate from ingredients (USDA)" fallback, the "Dinner tonight" card and
planned-vs-eaten logging (Phase 10, `b891938b` + `6932452c`). Remaining: the
Noom-style calorie budget and logging (Phase 11) and the Phase 10 optional daily
targets / weekly averages.

### `docs/HEALTH_HUB.md` - largely built

Phases 1-5 core are live (profiles, open ingestion, sleep/readiness, nutrition,
budgets/day view, sharing/export) plus opt-in cycle tracking, and Phases 6-11 are
now largely built: Health Connect/device import, the recipe↔log round trip, the
weekly digest, family challenges/cheers, the dashboard health widget,
photo/voice logging, fasting windows, micro-lessons and the chat coach; **digital
wellbeing** (Phase 10 - phone usage sessions, top apps and "What you do in
Jarvis"; built 2026-10-25, `c9c640dd`); and **digital guardianship** (Phase 11 -
the Family Link/Qustodio/Bark class on our own server, focused on collecting
mobile device data into a living per-person profile Jarvis reads to make
recommendations and give positive Biblical direction (an inappropriate artist
becomes a like-styled Christian one; a video theme is redeemed - same theme told
well by a better creator - or redirected): activity feeds, music (Jarvis playback
+ optional Spotify linking + media sessions), video and creator entities, device
usage, calls/SMS, web, budgets and routines, app control, filtering, location
alerts, local-model monitoring with a weekly recap, plus three-audience profiles,
the shared parents' page, prayer surfaces and a voice for the quiet ones;
collected data is parents-only while suggestions belong to the member they are
for, and the phone pulls external-app activity (YouTube, Spotify, podcasts)
through media sessions). Find-my-phone (`POST /api/guardian/find-phone/{user}`,
the priority-5 `find_phone` notice and `FindPhoneCard` on the member page) landed
with the 2026-10-25 Health work. Remaining: the Phase 10 optional daily targets,
and the Phase 11/12 device checks that need a real phone (see docs/HEALTH_HUB.md).

### Phase 12 - device management (MDM) (first slice and follow-ups shipped; per-device targeting open)

The user asked "is there a means we can push application (Jarvis OS) updates to devices remotely and
maybe some other MDM related tasks like push books and other files to be available on devices for
school work and Bible studies etc." and chose to **build full MDM-style device management**: device
inventory, remote app-update push, file/book push, app install/uninstall control, and remote
lock/wipe. See `docs/MDM.md`.

Shipped in `ccedf489`: the device inventory (`GET /api/mdm/devices`), the remote app-update push
(`POST /api/mdm/devices/update`, 409 when there is no build), the shared School/Library folders
(`/api/mdm/files*`, `/api/mdm/files/for-me`), the remote wipe
(`POST /api/mdm/devices/{key}/wipe`, confirm-gated device-owner-only, read by the screen-time status
the phone polls), the `Devices.tsx` page, and the Android `DeviceOwnerRules.shouldWipe` /
`DeviceOwnerGuard.wipeData`.

Follow-ups shipped (`84bced59` + `6932452c`):

- **A child-facing surface that consumes the pushed files**: `components/mdm/SharedFilesPanel.tsx`
  lists the folders pushed to the child and opens a file in an in-app viewer (reusing the workspace
  PDF and image viewers) or downloads it. A plain link cannot carry the API key the route needs, so
  the old bare `<a href>` download never worked; both actions now fetch a blob with the key.
- **The Library set sourced read-only from Calibre** (`docs/CALIBRE_LIBRARY.md`): the gateway
  browses `calibre_library_path` through the storage service's existing Nextcloud provider, keeps
  only book-format files, and re-checks the shelf root on every fetch. A blank setting is "not
  configured", not an empty shelf.
- **An audit log** (`audit.json`): who pushed an update or a folder, uploaded or removed a file, or
  locked or wiped a device, and when; shown as the Devices page's Activity log. Append-only and
  capped, like the telemetry activity feed.
- **Persistence**: `docker-compose.yml` now mounts `./data/mdm_files`, so the shared folders and the
  audit log survive a redeploy.

Still open: **per-device update notices** (the notice and push plumbing is per user, and the
app-update version is global, so there is no clean device dimension yet); a wipe still needs a
device-owner phone; app install/uninstall stays the screen rules.

- For file push the chosen model is **shared School and Library folders per child device**: one
  shared set pushed to each selected child device (not per-file targeting).
- Candidate reuse: the existing OTA / app-updates flow (`docs/MOBILE_OTA_UPDATES.md`), the
  workspace/files surfaces, and the read-only Calibre library over WebDAV (`docs/CALIBRE_LIBRARY.md`).
- This is where the "Devices" service's real-form-factor work (`72835c17`, phone vs tablet) and the
  app-updater OTA work (`0afd1da6`) point next.

## 4. Open small items

- **`.tmp/` at the repo root is a 1.9 GB gitignored scratch directory and it contains secrets**
  (`server_env_full.env`, `server_env_reconstructed.env`, `keystore_password.txt`, `keystore.b64`,
  `nc-creds.txt`, `probe_key.txt`, `local_env_keys.txt`, plus old logs, probes, patches, epubs and
  screenshots). It was deliberately **not** deleted - it needs Jeremiah's explicit go-ahead. Do not
  commit it, and do not paste its contents anywhere.
- **Noah's tablet still has the guard switched off, so nothing locks on it yet.** Device
  `81587280fd22d5e457c09a64e820e46e` (Samsung SM-X520, Android 16) reports device admin / device
  owner / Accessibility guard / usage access all **false**, and `screenrules.data` is `"{}"` (no
  rules at all). The full-screen lock needs, in order: the newest APK installed on the tablet, then
  on the tablet **Settings -> Apps -> Jarvis -> ⋮ -> "Allow restricted settings"** (Android 13+
  refuses to switch on an Accessibility service for an app that was sideloaded - the toggle simply
  flips back with "app was denied access"; Samsung also needs **Settings -> Security and privacy ->
  Auto Blocker -> Off** for the ⋮ entry to appear at all), then **Settings -> Accessibility ->
  "Jarvis screen guard" -> On**, the app's **Device protection** (device admin) -> On, **Usage
  access** -> Allow; and then a rule set for him (daily limit / bedtime / school) from the member
  page. Android forbids the app enabling any of this for itself, so it has to be done by hand on the
  device; installing the APK with `adb install -r` instead of tapping the download skips the
  restricted-settings gate entirely.
- Media: the **Phase 8 device checks** (swipe-away playback carries on, then reopening reclaims it;
  the home-screen widget's transport on a real phone) and **Phase 9's** final gates - one `LIVE=1`
  E2E run (`LIVE=1 UI_URL=… TEST_USER=… TEST_PASS=… npx playwright test --project=live`) - are still
  outstanding; the 1.6.0 version bump is done (`c9c640dd`). P6-T5 (UX ledger pass) and P7-T3 to
  P7-T5 (screen-reader labels and the now-playing announcement, performance budget, image sizing)
  shipped 2026-10-25 (`0316f940`). The hermetic media E2E passes for `hermetic-desktop` and
  `hermetic-mobile` apart from known-flaky specs; the `hermetic-iphone` project needs
  `npx playwright install` locally.
- Health: the veg challenge (steps shipped as a family challenge); the family challenge's labelled
  "guideline goal" for members with no goal of their own still needs Jeremiah's yes/no under the
  no-fallback rule.
- **Headless-Chrome mystery closed (2026-10-25):** the "headless Chrome with an expired API key"
  polling ~360 refused calls/hour was **Jeremiah's own phone** - a Samsung Galaxy S26 (SM-S942U1)
  running the Jarvis APK on cellular from 172.59.203.208. `/api/users/me/theme` returns 200 (a
  public default theme with an empty `user_id`) and there were no 401s, so there is nothing to
  stop. The old UA-based guess (another tool's Windows test browser) was wrong.
- **TLS:** every host (jarvis, abs, ai, books, docket, games) serves its own nginx-validated certificate
  (to 2027-01-23, auto-renew), so the old `*.sumemail.com` wildcard is not needed. Its renewal is skipped.
- Stale MA player and HA entity named after a user JSON blob: remove by hand in MA → Settings →
  Players.
- **Phase 12 (device management / MDM)**: the first slice (`ccedf489`) and the follow-ups
  (`84bced59` + `6932452c`: the child-facing file surface, the Calibre-sourced Library set and the
  audit log) have shipped (see "Phase 12 - device management (MDM)" above and `docs/MDM.md`). Open:
  per-device update notices (the push plumbing is per user), and a wipe still needs a device-owner
  phone.
- Devices: the tablet/phone split is a screen-width heuristic, so a very large phone reads as a
  tablet (`72835c17`).
- Family guard (device owner): the Layer 2 restrictions, the new Settings-page tamper matches, the
  **kiosk lock-task mode** and the **real OS lock** all require a one-time device-owner provisioning
  (`adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver` on a phone with no accounts
  signed in, or QR/`ACTION_PROVISION_MANAGED_DEVICE` at a factory reset, which is not implemented).
  Without it a lock is the full-screen Accessibility overlay (`GuardOverlay`), which covers the home
  screen and a tablet's taskbar and navigation strip and cannot be left with Home or Back, but is not
  Android's own lock screen.
- **Family Link / Qustodio / Bark research (2026-10-25):** there are no supported public APIs for
  device control (only reverse-engineered Home Assistant bridges), so their best features are being
  rebuilt natively. Build order (cheap parenting wins first): (1) the cheap parenting wins - an
  "Ask a parent" unblock on the blocked-app screen, a child's own weekly screen-time report, and a
  repeated-blocked-site alert; (2) web-filter categories; (3) notification/social monitoring;
  (4) a device-owner hardening batch; (5) remote app install/uninstall; (6) kiosk/COSU;
  (7) geofences + photo/video scanning; (8) Wi-Fi/cert/VPN config + OS-update policy.
- **OpenRouter**: the per-user OpenRouter tile is removed (`d517166f`) and there is no per-user
  OpenRouter credential; OpenRouter is the global Settings > LLM setting.

## 5. Immediate next steps

1. **Device checks (need a person with the phones; install the newest APK first):**
   - **Device-owner provisioning first** (see item 3): on a phone with no accounts signed in,
     `adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver`, so the Layer 2
     restrictions, kiosk lock-task mode and the real OS lock actually engage;
   - background music: play on the phone, swipe the app away (music continues), reopen it, and the
     home-screen widget's transport buttons on a real phone (media Phase 8);
   - the **real lock** (`613edbb0`): bedtime/pause enters kiosk lock-task mode on a device-owner
     phone, a parent's lock is immediate, and Devices says "Locked" only when the phone can really
     lock (a device owner, or any phone with the Accessibility guard on — the full-screen overlay);
   - Health Connect connect/sync and the Health today widget (list in docs/HEALTH_HUB.md);
   - Phase 11: install approval, camera permission gating, the one-minute warning, "ask a
     parent" from a blocked site (list in docs/HEALTH_HUB.md Phase 11);
   - Phase 12 MDM: a pushed School/Library file opens in the in-app viewer or downloads on the
     child's device, and a remote wipe lands on a device-owner phone;
   - Notes: a place reminder on arriving home, a voice note, drawing, an offline edit;
   - the screen fix: the app must not drag under the status bar or the Android buttons;
   - the Jarvis guard: uninstall attempt → parent-login screen; parent login and the **offline
     code** (airplane mode) unlock; bedtime/pause covers the whole screen (the home screen too)
     with the overlay, over the tablet taskbar and navigation strip, and cannot be left with Home
     or Back; calls and texts still work; the lock
     screen offers "Ask for more time" and "Message a parent", and both stay on the lock (a sheet
     painted over it) instead of taking it down;
   - app blocking: a blocked app closes with "That app is off for now";
   - the **web filter**: Phone protection card → Web filter → Allow; a blocked site fails, Google
     search is SafeSearch, ordinary browsing keeps working (DNS-only VPN), Disconnect is covered;
   - sleep estimate: after a night with the new APK, Sleep → "Estimate from my devices".
2. **First live trivia game** on the new code (questions/verdicts from the `jarvis` account,
   arrival-order answers, late answers not burned).
3. Phase 11: the device-owner Layer 2 restrictions shipped (block the guard's own uninstall,
   `DISALLOW_SAFE_BOOT`/`FACTORY_RESET`/`USER_SWITCH`/`ADD_USER`/`DEBUGGING_FEATURES`/`CONFIG_VPN`/
   `CONFIG_PRIVATE_DNS`, a real `lockNow()` at lock onset, and the new Settings-page tamper matches),
   and a parent's lock is now a real lock (`613edbb0`: kiosk lock-task mode on a device-owner phone,
   an immediate lock, and the false "Locked" success gone; a phone without the device owner gets the
   full-screen Accessibility overlay instead). All of it only takes effect on a phone
   provisioned as device owner (`adb shell dpm set-device-owner
   com.jarvisos.app/.GuardAdminReceiver`, one-time adb, or QR/`ACTION_PROVISION_MANAGED_DEVICE` at a
   factory reset, not implemented). The web-filter bypass blocks (unsupported browsers, split
   screen, safe mode) shipped.
4. Finish the media Phase 8 device checks (above) and Phase 9: the final full gate, the full hermetic
   E2E on all projects, and one `LIVE=1` run against the dev server
   (`LIVE=1 UI_URL=… TEST_USER=… TEST_PASS=… npx playwright test --project=live`).
5. Keep the plan docs and this handover updated in the same commit as the code.

## 6. Where the details live

`docs/MEDIA_OVERHAUL.md`, `docs/NOTES.md`, `docs/BIBLE_STUDY.md`,
`docs/RECIPES.md`, `docs/HEALTH_HUB.md`, `docs/HEALTH_STEPS.md`,
`docs/FAMILY_HUB.md`, `docs/ROLES.md`, `docs/DASHBOARD.md`,
`docs/PENDING_TASKS.md`, `docs/MOBILE_OTA_UPDATES.md`, plus service READMEs
under `services/*/README.md`.
