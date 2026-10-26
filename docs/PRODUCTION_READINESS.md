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
- **ROUGH - the image proxy streamed an upstream error as if it were an image.** Only 404 and the
  redirect statuses were handled; every other non-200 (a 400 from a request we built, a 5xx from a
  broken upstream) fell through to the streaming response, so a cover came back as a broken image
  rather than an error. It now returns 404 for a missing cover or a redirect and the upstream's own
  status otherwise, with a test for each. This was a regression from quietening the 404 flood, so it
  was live for a few hours.

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

- **LOW - L1: the internal secret is compared in constant time everywhere, and is a header only.**
  Health, geo and bible used to return *true* from `_verify_internal_secret` when `INTERNAL_SECRET`
  was empty (fail open) and to accept the secret as a `?x_internal_secret=` query parameter, which
  writes the household's master secret into every access log on the path (the proxy redacts `token`
  and `mt`, not this). All three now use the shared constant-time `secret_matches`, which fails closed
  when the expected secret is unset, and the query form is gone: geo's sixteen routes and all three
  middlewares read the header only. The plain `==` comparisons in identity (four, including the one
  that decides whether a caller may write a device assignment), rag, the control plane (two) and the
  gateway (four, including `_acting_identity` and `_sd_request_authorized`) use the same helper.

**Open, low.**

- **L2** the internal secret is still a master bearer token: identity's `require_internal` and
  `require_admin_or_internal` also accept it as `Authorization: Bearer <secret>`. A separate,
  header-only service credential would be better than one value that is both a bearer token and a
  header.
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

Nothing from this audit is still open.

**Checked, and not a defect: the theme tokens are not bypassed by hardcoded colour classes.**
`services/ui/scripts/gen-theme-css.mjs` reads every `.tsx`/`.ts` file under `src` (tests excluded), finds
every colour class the source actually uses, and writes `src/themes/theme-remap.css`: purple, violet and
indigo follow the theme accent; the neutral families (slate, gray, zinc, neutral, stone) become the
theme's surface and text per scheme, with dim text raised to readable contrast on dark themes and dark
surfaces re-shaded on light ones; the chroma families are adjusted per hue so they stay legible in both
schemes; and white text on a solid fill (the red SOS button) is left alone. `themeRemap.test.ts` runs the
generator with `--check` and fails when the committed file is out of date, so a new colour class cannot
silently escape the theme - which is why the "216 files use hardcoded colours" count is not a defect: the
classes are the input to the generator, not a bypass of it.

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
   accounts and the Fernet-encrypted credentials, so it was the one that mattered most. Logging and
   geo followed, because neither writes anything at all, and rag followed them: it embeds whatever the
   family indexes, so it is the service most exposed to untrusted content, and its model cache now lives
   under `HOME` on the volumes it already shared with the gateway (which were chowned to the host user)
   instead of under `/root`. Caddy followed too, and it is the one a stranger reaches directly: it runs
   as the host user with `NET_BIND_SERVICE` (it still binds 80 and 443 inside the container, published on
   high host ports) and its two state volumes, including the 600-mode `autosave.json`, were chowned to
   match. Telemetry and dns followed, and neither writes anything to disk at all: telemetry keeps its
   state in Redis, and dns reads the Docker socket (group `DOCKER_GID`) to discover containers and binds
   53 inside the container, which is what `NET_BIND_SERVICE` is for. The last one, ui, needed no image
   change after all: nginx only wants a writable cache and pid, so it runs as the host user with two
   tmpfs mounts for exactly those, verified by running the published image as uid 1000 on the sharedllm
   network (static files 200, `/api` proxied to the gateway 200).
8. The base image was built from a moving tag, so the same commit could produce a different image on
   a different day. `docker/Dockerfile.base` now pins the multi-arch index digest of
   `python:3.11-slim`, which fixes the interpreter and the system libraries while leaving both
   architectures resolvable.
9. The background worker started seven loops in-process, so a second replica would have posted every
   bot reply, notification and admin alert twice. The inference loop is safe anywhere (it claims jobs
   from a queue), but the other six - health scans, the `@jarvis` monitor, chat notifications, admin
   alerts, song notices and cleanup - now each hold a short Redis lease under their own name
   (`InferenceJobQueue.claim_loop`), refreshed while they run, and stand down while somebody else
   holds it. A Redis failure fails open: losing every notification would be worse than a duplicate.
10. Retries backed off exactly, so every request in flight when a dependency blinked came back at the
    same instant and hit it together again. `retry_http_request` now jitters its backoff by up to 50%
    (`_retry_delay`), with a test that two calls at the same attempt do not wait the same time.
11. The base image installed about thirty-five Python packages unpinned, so a rebuild of the same
    commit could silently jump a major version. Every package the Dockerfile names is now pinned to the
    version the built image actually resolves (read from the image on 2026-10-26), so the interpreter,
    the system libraries and the named libraries are all fixed.
12. **The loudest warnings in the whole stack were false alarms.** Caddy logged
    `aborting with incomplete response` at WARN every time a long-lived stream ended - the media events
    feed and the push stream, whenever a phone closed the app or its screen slept: **1348 of the 1348
    lines Caddy wrote in 24 hours** were that one message, which buries a real proxy warning when one
    happens. Caddy's global `log` block now raises that one logger
    (`include http.handlers.reverse_proxy`) to ERROR, so expected stream ends are silent and genuine
    failures still show; verified in a throwaway container before applying (a killed stream produced 1
    warning without it, 0 with it, and the access log was untouched). Two startup warnings went with
    it: the redundant `header_up X-Forwarded-Proto` lines Caddy told us about on every start are gone
    (it already forwards that header). The image proxy's 404 warnings were checked too and are honest:
    **5 in seven days**, each a library entry that really has no artwork upstream
    (`/audiobooks/Psalms [B005FXZPPM]/cover.jpg`, `/podcasts/Haunted Cosmos/cover.jpg`, two
    `/metadata/items/<id>/cover.jpg`), not a URL we build wrongly. What remains at startup is
    informational and once-per-start: the Caddyfile uses four spaces where `caddy fmt` wants tabs (the
    file is left as it is rather than reflowing every line for a cosmetic notice), and the plaintext
    internal ports report that HTTP/2 and HTTP/3 are skipped because they require TLS.
    Every listener in this Caddyfile is plaintext (TLS terminates on the public proxy), so
    HTTP/2 and HTTP/3 could never be negotiated and Caddy said so 22 times on every start. The
    global `servers` block and the `:80` server block now ask for HTTP/1.1, which took a restart
    from 24 warnings to 2 (the Caddyfile-formatting notice and the informational auto-HTTPS note),
    measured in a throwaway container against the real file.

13. Several list reads accepted any `limit`, so one caller could ask a service for everything it had:
    the admin log tail, the workspace mission list, Raven's lessons, the notification feed, the two
    media searches, the unified search and the favourites list are all bounded now (`Query(..., ge=1,
    le=...)`), and an absurd page size is a 422 rather than a whole table.

A Home Assistant outage used to be invisible too: the two places that hit it on a timer logged at
debug. They now warn once and then stay quiet for fifteen minutes
(`services/common/logging_utils.py`'s `warn_periodically`, used by execution's media-player
discovery and geo's zone cache).

**Open, should fix before merge.** The base image's apt tools are still installed unpinned (the
`FROM` line, every Python package the Dockerfile names, and the interpreter are pinned, but the
Debian packages `apt-get install` resolves at build time can move - the digest fixes the release, not
the tool versions - and the Python transitive dependencies are free to move too, which needs a
constraints file reconciled with each service's own `requirements.txt`).

**Acceptable for this deployment.** Readiness reports 200 while degraded; the error envelope differs between services; no request
correlation id; two N+1 queries; a deploy whose commit has not finished building yet falls back to
`:latest` with a warning rather than waiting indefinitely (which is also what lets a docs-only commit
deploy without rebuilding anything) - and when the commit's own image tag is missing it now pulls
`:latest` from the registry first, because a stale local `:latest` is how a recreate once came up on an
image four hours older than the one published; and a handful of deliberate best-effort `except Exception: pass`
blocks. The gateway's media layer is the model to copy for honest failure: a named cause and a clean
502 rather than a traceback. State also lives in the process in places - the gateway's live media push
keeps a hub per user and a registry of web players (`media_events.py`, `ma_scope.py`), and the caches in
`cache.py`, `prompts.py`, `apk_manifest.py`, `services/health/main.py` and
`services/common/circuit_breaker.py` are per-process - which is correct for one replica per service
(what `docker-compose.yml` runs) and would need moving into Redis before a second replica of the
gateway or health was added. The six background-worker loops are already safe: each holds a short Redis
lease, so a second replica stands down rather than double-posting.

### 3.8 Recipes, calendar, Bible, location, admin and workspaces (audited 2026-10-26)

**Fixed.**

- **Recipes: opening one could save it over another.** Switching recipes set the title immediately
  but left the previous body in the editor, so a Save in that window wrote recipe A's text to recipe B
  (and a failed read left the stale body there to be saved). The editor now clears on switch, Save is
  disabled while the read is in flight, and a failed read clears it rather than leaving it.
- **Recipes: a failed list read looked like an empty cookbook** ("No recipes yet. Add the first one"),
  and the delete button had no busy state. The list says it could not load, with a retry, and the
  delete disables for its own row. A failed duplicate check now says so instead of silently offering
  already-imported recipes again.
- **JarvisLab: "Last Run Result" showed the wrong run.** It read `runSmokeMutation.data ||
  runUnitMutation.data`, so after a passing smoke test a failing unit run still showed the smoke
  test's PASS and output. It now tracks the most recent run and names it.
- **JarvisLab: the stack overview, the missions list and the log panel rendered failures as emptiness**
  (blank panels, "No missions yet", "Waiting for log lines…"). Each now says what failed.
- **Workspaces: a failed list read said "No Workspaces Found"** - a claim about the household rather
  than about the request. It now says it could not ask, with a retry. "Sync All" and "Launch Scan"
  could also be double-fired, launching every update twice; both disable while in flight.
- **Admin: four destructive deletes asked nothing** (a device assignment, a media group, a light
  cluster, a light pattern). They now use the app's own dialog. The account list has error and empty
  states, "Add person" and the new-setting Add disable while saving, the password field refuses fewer
  than four characters, and five inputs that had only a placeholder now have a name.
- **Calendar: a refused write toasted success.** The server answers a FAILURE body with HTTP 200 and
  the UI's calendar writes never checked `status`, so an event whose time could not be parsed was
  reported as added and nothing was saved. A refused write now raises, and the date-parsing refusal is
  reworded ("Try 'tomorrow 5pm' or 2026-10-27 17:00") instead of surfacing as a generic failure.
- **Calendar: the desktop month's event click also opened the day view** (the event span sat inside
  the day button with no `stopPropagation`, which is also invalid nested interactive markup), the
  mobile month's event dots were invisible to a screen reader, the "Nothing coming up" empty state
  rendered beside the error, and the iCal/default/integration controls could be double-fired.
- **Bible: a one-letter search said "Nothing found"** although the query is disabled below two
  characters - it now asks for two letters. The tab bar claimed to be a tablist with no panels; it is
  a group of pressed buttons now. Closing a study asks first.
- **Location: the sensor toggle always reported success.** It returned true even when no watch
  attached, so Settings toasted "Location tracking enabled" over a failure. The map also said "No one
  is sharing location right now" when the feed had simply failed; it now says it could not read the
  feed.
- **Companion devices: a failed read looked like no data** ("No devices yet", "Not enough battery
  reports in the last day yet"). Both now say the read failed.
- **TheMealDB search was a second, unreachable path to data `recipe_search` already fetches.** The
  `themealdb_search` action, its handler and its four tests are gone.
- **Import a recipe from a photo, including handwriting - verified working end to end (2026-10-26).**
  The Recipes page has a **Photo or handwriting** button whose file input carries
  `capture="environment"`, so on a phone or tablet it opens the camera rather than a file picker; the
  shot goes to `POST /api/recipes/photo` (multipart, 8 MB cap, 422/413 on a missing or oversized
  image), which sends it to execution's `/execute/ocr` with `task="recipe"`. That prompt is written to
  transcribe only what is on the page and to list anything it was unsure of;
  `services/common/recipe_ocr.py` then turns the transcription into an editable draft, naming what is
  missing rather than inventing it, and the raw text stays beside the draft so a person can check the
  page. The vision model is the household's own (`vision_ocr_model` = `qwen2.5-vl:7b` on
  `vision_ocr_proxy_url`, the home desktop's OpenAI-compatible endpoint - the same local model the
  Health food label and the workspace screenshots use), so a photographed recipe never leaves the
  house. **Measured live:** a rendered recipe card (title, "Serves 8", "Prep 15 min / Cook 55 min",
  six ingredients, four steps) came back as title `Grandma's Banana Bread`, those six ingredients,
  those four steps, `prep_minutes 15`, `cook_minutes 55`, `total_minutes 70`, `yield "8"`, with
  `missing: []` and `uncertain: []`. When the model cannot be reached the route answers 502 with the
  reason and the page says "Type the recipe instead", so a dead desktop is a message rather than a
  blank draft.

**Open (rough).**

- The calendar's "When" field is still free text validated on the server; the client only explains the
  server's parse error after the fact rather than catching it first.
- `docs/RECIPES.md`'s Phase 6 (tests) and Phase 7 (Skylight) remain as that file describes.

### 3.9 The Android client and the account lifecycle (audited 2026-10-26)

**Fixed.**

- **A1 - signing out did not reliably sign out.** `logout` removed the stored key
  and then navigated immediately, but on Android the key is mirrored into the native preferences that the
  background services read, and that write is a bridge round-trip: it never finished. The phone kept
  streaming notifications, uploading location and steps, and running widgets with the previous person's
  key. `logout` is async now and waits for both removals before it navigates.
- **A2 - a slow start could trap the app at the login screen.** A three-second timeout set `initError`,
  and nothing ever cleared it: a successful sign-in was still bounced back to `/login` until the app was
  reloaded. The error is cleared on a successful restore and on a successful login.
- **A3 - step sync died silently on Android 15.** `StepSyncService` held a `dataSync` foreground service
  all day, and Android 15 stops those after six hours; there was no `onTimeout`, so the service was
  killed with the "Step counting is on" notice still up. Both `onTimeout` overloads now stop deliberately.
- **A4 - signing out left the location and step services running.** `setCredentials` stopped the push
  stream only, so a signed-out phone still powered the GPS and still advertised that location sharing was
  on. Sign-out now stops both services and clears their switches.
- **A5 - a rejected key spun the push stream forever.** A 401 or 403 was treated as a blip and retried
  every five minutes, holding a foreground service on a credential that could not work. It now clears the
  mirrored key, clears the notices and stops.
- **A6 - the previous person's notifications stayed on the lock screen** after a sign-out (they were only
  cleared when the account *changed*). Sign-out clears them too.
- **A7 - an admin password reset did not rotate the session key.** Changing your own password rotated it;
  a parent resetting a child's password left the old phone fully signed in and still reporting, which is
  the opposite of what a reset is for. The admin route now rotates the key the same way.
- **A8 - `Comms.requestAccess` answered before the dialog.** It read the permissions immediately after
  asking, so it reported the state from before the choice; a caller could believe access was refused the
  moment after it was granted. The answer now comes from the permission result.
- **A12 - the step counter left its sensor registered** when the bridge went away, keeping the plugin and
  the sensor alive. It unregisters in `handleOnDestroy`.
- **A10 - the family's home coordinates were baked into the APK.** They were the fallback home for the
  "away from home" check, so the real address shipped in every download. With no home learned, the check
  now answers "not away" instead of comparing against it.
- **B1 - a new account could be created with no password and then simply could not sign in.** The field
  was marked required but nothing enforced it, and the server stores no hash, so the only feedback was the
  generic "Invalid username or password" - forever. Creating an account now requires a password of at
  least four characters, and importing a discovered user mints a temporary one and shows it.
- **B2 - a role change reached the client only on a full reload**, so a demoted member kept being offered
  actions the server refused. The profile is re-read when the app returns to the foreground and every five
  minutes.
- **B4 - a wrong password was reported twice** (a toast and the inline alert). The login page keeps the
  inline one.
- **B5 - `role` reported `'user'` when nobody was signed in.** It is null now, as its type says.
- **B6 - per-person device preferences survived a sign-out** (`jarvis_guard_seen_on`, the location
  tracking switch, the step backfill marker), so on a shared phone the next person inherited the previous
  person's state. Sign-out clears them, and the native switches are cleared with the key.
- **A signed-in account with no password, and a guessable administrator password, were both silent.**
  Read from the live database on 2026-10-26: two members imported from Nextcloud had `password_hash` set
  to nothing, so they were listed, appeared on the family pages, and could never sign in - the login just
  answered "invalid username or password" - and the `default` administrator still signed in with the
  eight-character, all-lowercase placeholder word in `DEFAULT_ADMIN_PASSWORD`. Neither was visible
  anywhere. Identity now logs both at startup (`_warn_about_sign_in`: a short or obvious
  `DEFAULT_ADMIN_PASSWORD`, an administrator still using it, and every account that has no password), the
  account list reports `has_password`, and the Admin page marks those rows **No password** so a parent can
  set one from the same screen. Rotating the administrator's password itself is the household's call: the
  `.env` value seeds a new database and is not meant to stay in use.

**Open (rough).**

- **A9** cleartext traffic and user-installed certificate authorities are enabled for every host
  (`AndroidManifest.xml`, `capacitor.config.ts`, `res/xml/network_security_config.xml`). The household uses
  HTTPS, so this is the accepted risk recorded in §4 rather than an oversight - but the user-CA trust in
  particular is broader than it needs to be.
- **A11** `WidgetApi.DEFAULT_SERVER` falls back to the public domain for an unconfigured widget, so a
  widget on a phone that has never signed in calls out rather than rendering "Sign in". (The baked-in home
  coordinates are gone: with no home learned, the "away from home" check now answers "not away" instead of
  comparing against the family's real address.)
- **B3** a first run with no accounts gives no in-app guidance; the seeded administrator is `default` with
  the password from `DEFAULT_ADMIN_PASSWORD`, which the person has to know from the docs (the startup
  warning above now says when that value is short or obvious, and when the administrator is still using
  it). A hint on the
  login screen would need an endpoint that says whether any account exists, and that is exactly what an
  unauthenticated caller should not be able to learn, so this stays documented rather than built.
- The Android fixes above compile and are covered by the existing unit tests, but they need a real phone
  (Android 15 or 16 for the `dataSync` cap) to be confirmed in the field.

## 4. Deliberately left, with reasons

These are known and recorded rather than fixed, so the next person does not have to rediscover them.
Everything here is a deliberate call, not an oversight.

- **LOW - the internal secret is still a bearer token as well as a header.** Identity's
  `require_internal` and `require_admin_or_internal` accept it as `Authorization: Bearer <secret>`, so
  one value is both. A separate header-only service credential would be better. (The query-parameter
  form, the fail-open checks and the plain `==` comparisons are all fixed - see §3.3.)
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
- **The remaining audit findings are recorded above, not fixed.** §3.5 UI/UX (the theme tokens
  bypassed by hardcoded colour classes in 216 files), §3.6 Family comms (the typing indicator, which
  needs Talk's websocket rather than a poll) and §3.7 Backend readiness (the apt tool versions and the
  Python transitive dependencies, the per-process state that would misbehave under a second replica,
  and the services still running as root) each list what is still below the bar with `file:line` and
  the smallest fix.
