# Production readiness - the bar, the method, the findings

Written 2026-10-26, ahead of merging `microservices` to `main`.

The owner's bar, verbatim: *"we need every aspect of the app (both frontend/backend and security)
to be production level and have production polish... everything not just works but works and
integrated well and is represented well in the UI in a clean and easy to use way."*

This file is the working document for that. It states the bar, the method, and then the findings
area by area, each with `file:line`, the user-visible symptom, and the fix. Findings are graded:

- **BROKEN** - does not work, or works in a way that would embarrass us. Fix before merge.
- **ROUGH** - works, but unpolished, inconsistent, or confusing. Fix before merge if cheap.
- **FINE** - meets the bar. Listed only where it is worth recording *why*.

## 1. What "production level" means here

This is a self-hosted app for one family of seven behind a proxy, not a public SaaS. So the bar is
not "multi-region, on-call rota, 99.99%". The bar is:

1. **Nothing is broken.** Every feature the UI offers either works end to end or is not offered.
   No dead ends, no buttons that do nothing, no screen that renders blank, no control that silently
   fails. This is the whole complaint that started this pass: the media page "semi-worked".
2. **Nothing is fake.** Every number, image, list and status is real data or an explicit
   loading / empty / error state. No placeholder that looks like data, no invented default.
   (Standing rule in HANDOVER §1.)
3. **Nothing is a dead end.** Every screen answers "what now?" - a next action, a back path, or an
   explanation of why it is empty.
4. **It is honest about failure.** If an upstream (Home Assistant, Music Assistant, Nextcloud,
   Audiobookshelf, the LLM) is down, the user sees a clear, calm message and the rest of the app
   keeps working. No raw stack traces, no silent no-ops, no infinite spinners.
5. **It is safe for the family it holds data for.** Children's health data, photos, chats and
   locations are in here. Authorization is enforced on the server for every object, not hidden in
   the UI.
6. **It is usable by everyone in the house.** A 7-year-old on a tablet and an adult on a laptop both
   get a clean, obvious layout. Meets WCAG 2.2 AA in the practical sense: contrast, accessible
   names, labelled inputs, visible focus, tap targets at least 24px (2.5.8) and ideally 44px.
7. **It is operable.** A failure is visible in logs, a deploy can be rolled back, and state survives
   a restart.

### Reference points (what the good ones do)

- **Navigation**: mobile gets a bottom tab bar of 3-5 primary destinations with icon + label and
  44-48px targets; desktop gets a 220-260px sidebar with a clear primary/secondary hierarchy and an
  obvious active state. Primary features are never buried in an overflow menu. (Alfdesign sidebar
  guide 2026, DesignStudio mobile navigation 2026, UXPin 2026.)
- **Accessibility**: WCAG 2.2 AA. The most common real-world failures, in order, are low-contrast
  text, missing alt text, missing form labels, empty links and empty buttons (WebAIM Million).
  WCAG 2.2 adds target size (2.5.8), focus not obscured (2.4.11) and stronger focus visibility.
- **Parental controls**: Family Link and Apple Screen Time set the free baseline - app limits,
  downtime/bedtime schedules, app blocking, content restrictions, app-approval requests. Qustodio
  and Bark add category-based web filtering, daily/weekly emailed reports, location + geofencing,
  and a panic button. Bark's strength is *reporting* (a readable activity feed) rather than raw
  enforcement; Qustodio's is per-category filtering and a 30-day history. A production-grade family
  guard therefore needs: enforceable limits, a **readable report of what actually happened**, and
  alerts - not just a lock.
- **Media**: Spotify/Apple Music/Plex set the bar for artwork everywhere (never a grey box when an
  image exists), a persistent now-playing surface, a queue you can see and reorder, and a playing
  indicator. An artist or album always plays something.

## 2. Method

1. Read the HANDOVER and the per-feature docs; treat them as claims to verify, not facts.
2. Walk each area in the running app (or its code where a live walk is impossible), on a phone-sized
   viewport and a desktop one.
3. Probe the live system read-only where a claim can be settled by evidence.
4. Grade every finding, fix the BROKEN and cheap ROUGH ones, and record the rest with a reason.

## 3. Findings

Area-by-area findings are appended below as each audit lands.

### 3.1 Media

Fixed in this pass (commits `9dbf4243`, `bd5a9dc9`):

- **BROKEN - no artwork loaded at all.** Music Assistant hands the gateway the *provider's* CDN URL
  (`https://r2.theaudiodb.com/...`) in `image.path`, and the image proxy's SSRF guard refuses any
  host the household did not configure, so every cover 404'd. The same entries carry MA's own
  `proxy_id`, which MA serves from its own host at `/imageproxy/<proxy_id>`. `_media_ma_image` in
  `services/gateway/main.py` now prefers `proxy_id`.
- **BROKEN - book authors and narrators listed as Artists.** MA's 500 artists are 198 `singer`,
  161 `narrator` and 140 `author`; `is_playable` is True for all of them, so `artist_type` is the
  only honest signal. An author has nothing to open, so the entry was a dead end. The Artists tab,
  the search artists bucket and favorited artists now drop `author`/`narrator`; the books remain in
  the Audiobooks tab where the author is already shown.
- **BROKEN - a real artist could not be played.** `playRequestFor` refused every artist, so the
  artist page had no Play button. MA expands an artist URI into every track that artist has, so the
  page now offers Play all and Shuffle, and the row menu offers Play next and Add to queue.

### 3.2 Merging `microservices` into `main` (investigated 2026-10-26)

**A plain merge is wrong and must not be used.** Measured facts:

- `main` tip is `52d3688b` (2026-01-05) - ten months stale. `git rev-list --left-right --count
  origin/main...origin/microservices` -> `374 3887`. The merge base is `2e6c7efb` (2025-12-20).
- All 374 `main`-only commits are from a 2.5-week window (2025-12/2026-01). `git cherry
  origin/microservices origin/main` reports **372 of them as patch-equivalent already present** in
  microservices, so `main` holds no work we have lost - only commit objects.
- **351 of those 374 commits touch the legacy `app/` tree**, which microservices deleted in
  `56d64620` (2026-04-30, an ancestor of microservices but *not* of main). `app/` is 0 files on
  microservices and 95 on main.
- A real merge in a throwaway worktree (`git merge origin/microservices --no-commit --no-ff` on
  `origin/main`) exits **1 with 60 conflicts** (`44 UD`, `11 AU`, `4 UU`, `1 AA`, plus `106 D`), and
  **resurrects `app/` with 58 files**. (`git merge-tree --write-tree` misleadingly reported exit 0.)

**The verified recipe** (tested 2026-10-26 in a throwaway worktree; result tree byte-identical to
`origin/microservices`, both parents kept, `app/` absent, and `microservices` becomes an ancestor of
`main` so every later merge is clean):

```bash
git checkout main
git merge -s ours --no-commit origin/microservices   # keeps both parents, tree still = main
git read-tree -u --reset origin/microservices        # take microservices' tree wholesale
git commit -m "Merge microservices into main"        # parents: main, microservices
git push origin main
```

Do this only after the production-readiness work below has landed and the tree is green, since a
push to `main` runs the same image-build pipeline as `microservices`.

### 3.3 Security

Audited 2026-10-26: every gateway route (552) and identity route (168) was enumerated and each
handler checked for whether it authenticates the caller. The structural fact behind most findings is
that **the gateway is the authentication boundary** - the other services trust `X-Internal-Secret`
and read `X-User-Id` as gospel, so a gateway route that reaches an upstream with the internal secret
but never checks who is asking is an open door.

**Fixed this pass.**

- **BROKEN - C1: any signed-in member could read or overwrite any file in the execution container.**
  The note routes forward `storage` from the request body, and with `storage: "local"` the handler
  used `Path(req.path)` with no containment check, so
  `POST /api/communication/notes/read {"storage":"local","path":"/proc/self/environ"}` returned the
  container environment (which carries the internal secret and the household's HA/Nextcloud
  credentials) and append/delete/write could clobber any file. Reachable even though the deployment
  uses Nextcloud, because the branch is chosen by the body. Fixed with `_local_note_path()` in
  `services/execution/handlers/note.py`, which resolves inside `LOCAL_NOTES_ROOT` and refuses
  anything outside it.
- **BROKEN - C2: household DNS records could be created, changed or deleted with no credentials.**
  All four `/api/dns` routes went straight to the mappings that the household resolver reads, and
  `/api/*` is internet-reachable, so an anonymous `POST /api/dns` was a household-wide DNS hijack.
  All four now require an admin. Safe because the panel is only reachable from the admin tab.
- **HIGH - H1: `/api/config*`, `/api/models*` and `/api/generate` were anonymous.** `POST /api/config`
  rewrote the household's global model settings, `/api/models/unload` was a denial of service,
  `/api/generate` was free anonymous inference on the household GPU, and `/api/config/status`
  disclosed which keys are unset. Writes now require an admin, reads require a signed-in caller, and
  `/api/generate` requires the same key the image generator already uses.
- **HIGH - H2: the geo vehicle and zone routes were anonymous.** Anyone could list the family's
  vehicles, delete one, assign a vehicle to any user, read fuel prices, or read the home and school
  geofences. Reads now require a signed-in caller, writes an admin, and
  `GET /api/geo/vehicles/assigned/{user_id}` answers 403 unless it is your own vehicle or you are an
  admin.
- **HIGH - H3: raven missions were editable by anyone.** The gateway's
  `PATCH /api/raven/missions/{id}` checked only that the caller was signed in and then forwarded an
  arbitrary body to Identity, whose update model accepts `status`, `result`, `user_id` and
  `workspace_id` - so a child could rewrite another member's mission, including missions that run
  code with the family's GitHub token. And Identity's own mission CRUD (published on the LAN at port
  8001) had no auth at all. The PATCH is now admin-only and Identity's six mission routes require the
  internal secret.
- **HIGH - H4: sessions were permanent and there was no sign-in rate limit.** Login returned the
  user's permanent key, `require_api_key` accepted it forever, and changing the password did not
  rotate it, so "change my password" did not sign anybody out. Login is now rate limited (five
  failures per username and twenty per source address in fifteen minutes, cleared on success) and a
  password change rotates the key; the client stores the new key so the device you are on stays
  signed in while every other session is invalidated. A short-lived signed session token with a real
  revocation endpoint is the durable fix and is still open.
- **MEDIUM - M1: telemetry enrollment was readable and writable by any member.** A child could list
  every enrolled entity, read any entity's telemetry, trigger analysis and delete any enrollment.
  Those routes now require an admin, which matches the panel (admin tab only).
- **MEDIUM - M2(a): the APK updater trusted the URL it was handed.** The download URL comes from the
  web bundle, so a rogue or machine-in-the-middle response could have pointed it at an arbitrary
  APK. `ApkInstallPlugin` now refuses any update URL whose host is not the host this phone is
  configured for (host only, so a different port or scheme on our own server still works), in both
  the verified flow and the legacy unverified one.
- **MEDIUM - M4: chat job results were readable by anyone who guessed a job id.** `job_id` is short
  and the completion carries the model's full answer, including any RAG context from the family's own
  data. Both routes now require the caller to own the job (admins may read any).
- **MEDIUM - M5: the admin test runners were anonymous.** `POST /api/admin/tests/smoke` and
  `/api/admin/tests/unit` now require an admin.

**Accepted, documented risk.**

- **M2(b) cleartext is left on.** `cleartext: true` in `capacitor.config.ts` and
  `usesCleartextTraffic="true"` in the manifest stay. Turning them off would break any phone pointed
  at a plain `http://` LAN server, and Android's network-security-config cannot express "the private
  address ranges are fine", so the choice is a real trade-off rather than an oversight. The household
  itself uses `https://jarvis.sumemail.com`. The update-URL host check above is the mitigation that
  matters most here.

**Open, low.**

- **L1/L2** internal-secret handling: health, geo and bible return *true* from `_verify_internal_secret`
  when `INTERNAL_SECRET` is empty (fail open), all three also accept it as a `?x_internal_secret=`
  query parameter (which the proxy does not redact), and several places compare it with `==` instead
  of the constant-time helper. If the gateway ever started with an empty secret, an empty header
  would match and a caller could assert any identity. Fix: use `secret_matches` everywhere, fail
  closed when unset, and drop the query-string and `Bearer` forms.
- **L3** the working tree's gitignored `.env` holds a live `GITHUB_TOKEN` and `GITLAB_PASS`. Not a
  merge blocker (it is untracked and never committed), but rotate the GitHub token if it is live.

**Checked and found sound** (so the next reader does not re-audit them): PBKDF2-SHA256 password
hashing with a constant-time compare; role and family escalation (a member cannot promote themselves,
`family_role` is not in the self-update model, family changes require `family.manage` and cannot
promote to father/mother); screen time (a minor cannot read or edit another member's rules, and the
parent unlock verifies that the login is one of that child's parents); health family sharing (opt-in
per scope, projected, with minor-cycle and digital restrictions enforced server-side); `as_user` is
admin-only; the gateway and the proxy both redact credentials and tokens from logs; and markdown
renders with no raw-HTML passthrough, so notes and chat are not an XSS surface.

### 3.4 Health hub

**Fixed.**

- **BROKEN - A1: anything logged after about 5pm local counted on the next day.** The day boundary
  was a raw string comparison on `recorded_at`, and the client sends a UTC instant, so a 6pm Phoenix
  glass of water was stored as `...T01:00:00+00:00` on the following date and then counted tomorrow.
  That corrupted today's water ring, the weekly water average, the water streak and the challenge day
  counts, and `_latest_metric` had the same bug for weight and resting heart rate. The service now
  buckets by the household's local day (`_household_tz()`, `_local_day()`,
  `_metric_rows_between()`) everywhere it previously compared strings, and a regression test pins the
  zone so it fails against the old behaviour on any machine.

- **BROKEN - A2/A3: a failed health fetch looked like an empty day.** The cycle panel rendered
  nothing at all on an error, which is indistinguishable from "not applicable"
  (`components/health/HealthCycle.tsx`), and nutrition showed `0 kcal` with "Loading…" forever while
  every meal said "Nothing logged." (`components/health/HealthNutrition.tsx`). Both now say what went
  wrong and offer a retry.
- **BROKEN - A4: a validation error read "Request failed with status code 422".** `lib/errorText.ts`
  now unwraps FastAPI's validation array (its first `msg`), and the axios response interceptor puts
  the server's own words on `error.message` once, so the ~171 places that display it say something
  useful.
- **ROUGH - B4** a second manual sleep log for the same night added a row instead of correcting it,
  so the night was listed twice; a manual log for a night now updates that night's row in place.
- **ROUGH - B5** future-dated check-ins, food and sleep were accepted; `_day()` now refuses a day more
  than one ahead (one day of slack, matching `_instant`), as the cycle routes already did.
- **ROUGH - B2** `GET /food` with the default `days=0` applied no filter at all, so it returned every
  entry the person had ever logged, and `GET /metrics/{metric}` was unbounded in sample count. Both
  reads are capped (`MAX_FOOD_ENTRIES`, `MAX_METRIC_SAMPLES`) and say `truncated: true` rather than
  dropping rows without a word.
- **ROUGH - B1** imported measures (HRV, SpO2, body fat) were stored and never shown - the profile
  panel printed weight and resting heart rate and nothing else - and a mistyped reading could not be
  removed, though `GET /metrics/{metric}` and `DELETE /metrics/{id}` both existed with no caller. The
  Measurements section now shows every measure the summary returns, each with a history (the newest
  thirty days, with the source) and a delete behind the app's confirm dialog.

- **ROUGH - B3/B10** the export carried the cheers a person received but not the ones they sent, so a
  backup or a move lost half of that history; it now has both directions. The README also described
  the sharing scopes as `sleep|nutrition`, which stopped being true when cycle and digital wellbeing
  were added.
- **ROUGH - B6/B7/B8/B9** a failed steps fetch read as "No reading yet" - a claim about the phone,
  when the number was simply unknown; the "Shared with you" card rendered nothing on a failed read,
  which reads the same as a family that has shared nothing; the digital-wellbeing week bars had no
  axis, so a taller bar meant nothing; and `api.getCheckins` and `api.getCheckin` had no caller at
  all. The steps card and the shared card now name the failure and offer a retry, the week bars are
  labelled with the day they stand for, and the two dead reads are gone.

**Open.**

Nothing from this audit is still open.

### 3.5 UI/UX

The newer surfaces (media, family chat, guardian/member, devices, remote) are the model: they have
loading, empty and error states, busy buttons, accessible names and safe-area handling. The problems
are the app shell, navigation plumbing and the older admin pages.

**Fixed.**

- **BROKEN - Android Back did nothing.** The Capacitor back-button listener was registered (which
  disables Capacitor's own handling) with an inverted condition, so on a phone a person who opened a
  book, a conversation or an admin tab was stuck unless the page had its own back control.
- **BROKEN - there was no catch-all route**, so an old bookmark or a stale deep link rendered the
  shell with an empty page. There is now a "That page does not exist" panel with a way home.
- **BROKEN - dialogs and sheets did not manage focus.** Tab walked out of an open dialog into the page
  behind it and focus was lost on close. A shared `useDialogFocus` hook now focuses the panel, traps
  Tab, closes on Escape and restores focus; it is wired into the modal, the chat sheet, the account
  menu and the mobile More sheet.
- **BROKEN - form labels were not associated with their inputs** (162 `<label>`s app-wide, 20 with
  `htmlFor`, and no `id` on the inputs), so a screen reader announced "edit text, blank". Login is
  done; Identity and the rest of the app are still open.
- **BROKEN - the crash screen was a developer console shown to children**: the raw stack trace, the
  React component stack, a platform dump, a "For Developers" block and a GitHub Issues link, with the
  first section expanded. It is now "Something went wrong" with one plain sentence, Try again and Go
  home, and the technical material behind a collapsed "Technical details" disclosure.
- **"Devices" meant two different things.** It is device management for a parent and a list of the
  files a parent shares for a child, so the same word sent a child somewhere they had no reason to
  look. The navigation and the page header now read "Shared files" for a member who is not an admin.

**Checked, and not a defect.** The audit reported the developer pages (Workspaces, Artifacts,
Knowledge, Docs) as offered to every role. They are not developer pages: `Workspaces` retitles itself
for a member who is not an admin ("My Workspaces", "View your workspaces and shared resources",
`services/ui/src/pages/Workspaces.tsx:590-593`), and `KnowledgeHub` (`:853`) and `Artifacts` (`:275`)
hide their admin-only controls behind `isAdmin` checks; `Docs` is the documentation page. The one page
that is genuinely admin-only, `Lab`, is already gated in both the navigation and the route. `Guardian`
likewise has a test asserting it is shown to every role
(`services/ui/src/components/layout/navItems.test.ts`), because a person's own week, prayer and
wellbeing live there.

**Open (rough).**

- The theme tokens are bypassed by hardcoded colour classes in 216 files.

**Fixed since the audit** (each gated and shipped): `/recipes` has a navigation entry; the Dashboard's
service-status and workspaces sections are admin-only and the placeholder subtitle is gone; the build
hash is admin-only in the sidebar and the dashboard header; the page finder works on a phone; the
media detail pages carry a Back control; and every destructive action now asks in the app's own
`confirmDialog` rather than the browser's `window.confirm` - all twenty-four call sites across the
app, with the tests that stubbed `window.confirm` updated to stub the dialog instead; the admin tab
bar wraps instead of scrolling sideways, so all twelve sections are visible; and Settings is grouped
into Notifications, Appearance, Family and This device instead of one flat column; a failed request now carries the server's
own words rather than "Request failed with status code 500" - fixed once in the axios interceptor where every request
passes, not at the ~171 places that display it; and the two knowledge searches use a brain icon and say what they
search, so they no longer look like the app-wide page finder; the workspace row's star, share and delete
buttons show a busy state instead of inviting a second click.

### 3.6 Family comms, notes and calls

**Fixed.**

- An open chat thread used to say "No messages yet" when Nextcloud was down, while the inbox correctly
  showed an error - the two halves disagreed. The thread's fetch now throws on a FAILURE body, and the
  thread shows an inline error with a retry instead of the empty text.
- An `@Jarvis` failure was completely silent: the orchestrator logged it, nothing was posted to the
  room, and the "Jarvis is thinking…" bubble simply vanished after three minutes. A failure now posts
  one short line into the room ("I could not answer that just now. Try again in a moment.").
- Regular trivia and the memory game could not be started from the UI at all; only Bible trivia had a
  chip, so the backend for the other two was unreachable. All three now start from the composer's
  chips (Trivia, Memory, Bible trivia) through one call.
- The game endpoint trusted a client-supplied player identity, so any member could bank stars or
  answer as another member. The player is now the verified caller.
- An unanswered drop-in call chimed every 2.5 seconds until somebody dismissed the banner, so a phone
  left on a table rang all afternoon. The banner stays up; the ringing stops after a minute.
- The calls tab hid a failed refresh whenever it still had a list. It now says the list may be out of
  date, and a disabled "Drop in" explains itself ("Not available to call right now").
- Timer and place-reminder notifications deep-linked to `/communication`, which redirects to the Chat
  tab - so tapping a finished timer opened a conversation about nothing. Timers now open the Family
  page's Announce tab and place reminders open Notes.
- The notes list treated only a returned FAILURE body as an error, so a thrown request showed "No
  notes yet. Take one above." over a full cache. It now warns that the cached list may be out of date
  and offers a retry.
- A sent text message dropped out of the feed as soon as the request succeeded and only came back on
  a later poll, because the optimistic bubble was removed straight after a refetch that often did not
  contain the message yet. The bubble now stays until a fetch actually has it (with a twenty-second
  backstop), so the message never blinks out.
- A photo or a voice note appeared in the feed only once the upload returned and the thread refetched,
  so a large one looked like nothing had happened. Both now show a pending bubble as soon as they are
  sent, which goes away as soon as the real message is in the thread.
- A phone with the app closed never learned that a call was happening: the in-app banner is a
  client-side poll. Joining a call now sends one push to the room's other members (category `call`,
  "Michele is on a family call", opening the calls tab), claimed by the first joiner for fifteen
  minutes so a group where everybody taps Join does not produce a notice each.

**Open (rough).**

- There is no typing indicator, and it is not a cheap add: Talk's typing signal is a websocket
  feature while this integration is HTTP-only, so it needs a websocket client rather than a poll.
  Read state is real either way (`receiptFor`/`last_common_read`).
### 3.7 Backend and operational readiness

Judged as one host, one replica per service, seven users, behind a proxy - not as a SaaS.

**Fixed since the audit.**

1. The identity-resolve timeout was 300s with two retries, so a slow identity made every request wait
   up to fifteen minutes and exhausted the pool - the same shape as the 2026-10-25 outage. It is now
   a 5s total with a 1s connect timeout. The shared client's own 300s default is deliberately kept:
   LLM generation, media streaming and image jobs legitimately run for minutes, and the endpoints that
   need a short wait pass their own timeout.
2. The health service had no migration path (`create_all` only), so the next model change would
   crash-loop with `no such column`. `services/common/sqlite.py` now offers `add_missing_columns()`,
   which adds every column the models declare but the table lacks, and health calls it on startup.
   Identity and bible keep their hand-written migrations, which do more than add columns.
3. Stack traces and internal strings no longer reach clients from the execution, rag or logging
   services, and the gateway's identity-resolution failure no longer echoes the identity service's
   body - the detail is logged, the caller gets a plain message.
4. The identity, health and bible SQLite databases now run WAL with a 5s busy timeout and NORMAL
   synchronous (`apply_sqlite_pragmas()`), the same settings rag already set by hand.
5. There was no automated backup of `./data/*.db`. `scripts/backup_databases.sh` takes a consistent
   `sqlite3 .backup` snapshot of every non-empty database under `./data` except the rebuildable seed
   files, tars them, keeps the newest fourteen, and fails loudly if any database cannot be copied.
   Verified on the server: identity, bible, health and the RAG vector store, 25 MB. A nightly cron
   entry is installed on the host.
6. A failed deploy left the broken image running. `scripts/deploy_remote.sh` now records the image ID
   each service is running before it recreates anything and, on any of its failure paths (a gateway
   traceback, a startup timeout, a missing execution container), retags those images back and recreates
   the stack, so a bad deploy leaves the last working build live.
7. Identity, health, bible and storage ran as root. They now run as the host user (`user:` in
   `docker-compose.yml`), and the three data directories that were root-owned on the host
   (`data/health`, `data/bible`, `data/storage`) were chowned to match - identity holds the family's
   accounts and the Fernet-encrypted credentials, so it was the one that mattered most.
8. The base image was built from a moving tag, so the same commit could produce a different image on
   a different day. `docker/Dockerfile.base` now pins the multi-arch index digest of
   `python:3.11-slim`, which fixes the interpreter and the system libraries while leaving both
   architectures resolvable.

A Home Assistant outage used to be invisible too: the two places that hit it on a timer logged at
debug. They now warn once and then stay quiet for fifteen minutes
(`services/common/logging_utils.py`'s `warn_periodically`, used by execution's media-player
discovery and geo's zone cache).

**Open, should fix before merge.** The base image's Python packages and apt tools are still installed
unpinned (the `FROM` line itself is now digest-pinned, so the interpreter and system libraries cannot
drift, but the ~35 pip packages can - pinning those needs a constraints file generated from a
known-good build, not a blind edit); the background worker starts seven loops in-process,
so a second replica would double-post bot replies and alerts; a dozen pieces of state live only in
memory; and rag, telemetry, geo, logging, ui, dns and caddy still run as root, because their data
lives in root-owned named volumes.

**Acceptable for this deployment.** No retry jitter; readiness reports 200 while degraded; some
list endpoints accept an uncapped `limit`; the error envelope differs between services; no request
correlation id; two N+1 queries; and a handful of deliberate best-effort `except Exception: pass`
blocks. The gateway's media layer is the model to copy for honest failure: a named cause and a clean
502 rather than a traceback.

## 4. Deliberately left, with reasons

These are known and recorded rather than fixed, so the next person does not have to rediscover them.
Everything here is a deliberate call, not an oversight.

- **LOW - the internal secret is still accepted as a query parameter** (`?x_internal_secret=`) on
  about fifteen geo routes (and by the health and bible middlewares). The comparison is now
  constant-time and fails closed when the secret is unset, and nothing in the gateway, the UI or the
  scripts sends it that way, so the query form has no caller - but it still exists, and Caddy only
  redacts `token` and `mt` from URLs, so a caller who used it would write the master secret into the
  access log. Removing it is a mechanical change across those routes and is worth doing the next
  time one of them is touched.
- **ACCEPTED RISK - Android still permits cleartext** (`services/ui/capacitor.config.ts`
  `cleartext: true`, `services/ui/android/app/src/main/AndroidManifest.xml`
  `usesCleartextTraffic="true"`). Android's network-security-config cannot express "plain HTTP is
  fine for private ranges only", and a phone pointed at a LAN `http://` server URL would stop
  working entirely if it were switched off. The household's own URL is
  `https://jarvis.sumemail.com`, and the APK updater now refuses any download URL whose host is not
  the phone's configured server.
- **LOW - the live `.env` holds a GitHub token and a GitLab password.** `.env` is gitignored and
  untracked, so it is not a merge blocker; rotate the GitHub PAT if it is still live and keep `.env`
  out of archives and backups. The 1.9 GB `.tmp/` scratch directory holds the same class of secret
  and is likewise gitignored.
- **The remaining audit findings are recorded above, not fixed.** §3.4 Health (A2-A4 and the B
  items), §3.5 UI/UX (the B items), §3.6 Family comms (the A and B items) and §3.7 Backend readiness
  (the A and B items) each list what is still below the bar with `file:line` and the smallest fix.
  The highest-value ones from those lists are: the identity resolve timeout and the missing
  `./data/*.db` backup (backend readiness), a chat thread that says "No messages yet" when Nextcloud
  is down and an `@Jarvis` failure that says nothing at all (family comms), the developer crash
  screen and the unlabelled form fields (UI), and the metric-history gap that makes imported HRV,
  SpO2 and body-fat readings invisible (health).
