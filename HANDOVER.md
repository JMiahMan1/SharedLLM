# Jarvis OS / SharedLLM — session handover

Updated 2026-10-23 for the next AI assistant taking over this repo.
Every claim below was verified against the live system unless marked **planned**.

## 0. Snapshot

- Repo: `/Users/jeremiahsummers/Code/SharedLLM`, branch `microservices`.
- Live: `https://jarvis.sumemail.com` — version **1.5.2**, OTA bundle `git_sha`
  **0c901c38** (verified 2026-10-24), APK available.
  - Containers: execution **fc7ba693** (image built at `8ccb342a`+), gateway/identity/ui
    **0c901c38**, storage **8fc73700** (image `8ccb342a`), health **d766a19f**.
  - Nextcloud bot account **`jarvis`** (display "Jarvis") exists; its login is only in the
    server `.env` (`JARVIS_BOT_NC_USER`/`JARVIS_BOT_NC_PASS`).
- Check anytime:
  - `curl -fsS https://jarvis.sumemail.com/api/app-updates/version`
  - `.venv/bin/python scripts/check_service_urls.py` — probes Audiobookshelf,
    Music Assistant, Nextcloud and the gateway; exits 1 on any failure.
  - `ssh jeremiah@192.168.2.205`; `docker exec -i sharedllm_<svc> …` (direct
    service calls need `X-Internal-Secret`, present as `INTERNAL_SECRET`).

### Recently shipped (newest first)

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
  (per-command, admin/TV paths). The gateway's one "Unclosed connection" (to `rag`) at every
  restart is pre-existing and harmless.
- `HEAD` Chat + trivia: **instant, fair, and Jarvis speaks as Jarvis.** Reviewed the live
  "Jeremiah & Michele's Chat" game (18:39–18:42 MST, which ran *before* the 18:44 deploy):
  Michele won all ten rounds because (1) late answers burned the next question's guess (fixed
  by the late-answer guard, now live) and (2) answers were judged only after the player's own
  post finished (2–5 s with the old re-login), so a later answer could win. Answers are now
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
  data removed** — the four suggestion cards left the Health page and are now one
  `recommendations` widget (`RecommendationsWidget`), default-visible for every role
  (identity `CHILD_WIDGETS` + `WIDGET_DEFAULT_SIZES`). The artists Jeremiah saw were not real:
  earlier "verified live" steps had posted fake events into his production feed (Drake,
  PewDiePie, a "smoke test" play, a `/notes` beacon smoke, 555 call/SMS, and a `source: smoke`
  usage day). Those 6 events and the usage record were deleted 2026-10-24 by exact value
  (backup on the server: `/home/jeremiah/telemetry-backup-20261024T013229Z/`).
- `HEAD` Fix: **chat and Nextcloud latency — stop re-logging in on every request** — measured
  from execution, any authenticated Nextcloud call took 2–5 s (`status.php` 0.1 s): the
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
- `HEAD` Fix: **review of `c68f55e4`** — (1) a missed or timed-out trivia round revealed the
  *next* question's answer (the reveal read `state.answers` after `advance_trivia`), and a
  missed final question raised `IndexError` inside the unawaited game task, so the game never
  said "Game over"; the answer is now read first and background game-task failures are logged
  (`_game_task_done`). (2) Chat brisk polling never ended: `sentRecently` compared against
  `now`, which only ticks while the Jarvis indicator shows, so every room polled at 2 s after
  any send; it now reads the real clock. The game detector also looked for `❓ Question`,
  which the server never posts (prompts are `🧠 Trivia — question N of M`), and scanned the
  unsorted feed; it is now `gameRunning()` in `chatModel.ts`, tested. (3) Podcast/book
  suggestions matched substrings over every media event, so playing *Newsboys* on Spotify
  produced a "worldview news" podcast pick, `drama` shadowed `audio drama` and `fantasy`
  shadowed `epic fantasy`; triggers now match whole words, longest first, and only real
  podcast/reading events reach them (`podcast_events` / `book_events`: podcast/audiobook/
  reading features, or media from a podcast/audiobook app or naming a show/book). Jarvis
  audiobook plays (`feature: audiobook`) now count for books.
- `c68f55e4` Family guard: **podcasts and books recommendations from the digital profile** —
  Jarvis now reads recent podcast listening and reading activity from telemetry and suggests
  redeemed alternatives. `services/common/podcast_recommendations.py` and `services/common/book_recommendations.py`
  map themes to vetted Christian shows/authors (news worldview, theology study, church history,
  parenting, audio dramas, classic apologetics, epic fantasy, etc.) or redirects when unredeemed.
  New routes `GET /api/recommendations/podcast` and `GET /api/recommendations/book` return only the
  derived suggestion (raw stream is parents-only). Rendered via `PodcastRecommendationCard` and
  `BookRecommendationCard` on the Health page. Verified with unit & integration tests.
- `c68f55e4` Fix: **chat trivia lag and location upload timeout storms** —
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
  "Visual editor") — notes open as formatted text with tickable checkboxes and a formatting
  toolbar (bold, italic, strike, heading, bullet/number lists, checklist, text colour,
  highlight, size); the stored file stays Nextcloud Notes compatible markdown (`lib/noteRichText.ts`).
  Colour/highlight/size are stored as whitelisted inline `<span style>` tags; notes with
  unsupported markdown (tables, code fences, etc.) open as text with a notice so they are
  never flattened. Widgets and `check_off` ignore the spans. Deployed and verified live.
- `cc816bdf` CI fix: Android `PhoneUsagePlugin` `JSONException` compile error and `HEALTH_HUB.md`
  heading lint (all four workflows green again).

- `07b8feb5` Family guard: **imported devotional and study-book pages land on the activity
  feed** — the shared imported-book reader posts one activity event as each page opens
  (feature `bible`, item "Book — Page N", `read`), so the family profile sees devotional
  reading next to chapters. The in-app recorders are now complete: trivia wins, media plays,
  app pages, chapters, imported reads. Verified live.
- `eb3be87f` Family guard: **Bible chapters land on the activity feed** — opening a chapter
  in the reader posts one activity event (feature `bible`, item "Book chapter", `read`)
  alongside the existing `chapter_complete` event, so the family profile sees reading.
  Trivia wins, media plays, app pages and chapters are wired; devotionals come next.
  Verified live.
- `fb29fa6b` Family guard: **the app's own pages land on the activity feed** — the web bundle
  carries a quiet beacon (one fire-and-forget view per visited page, feature + path) posted
  through the gateway's `/api/activity` to execution's `activity.record`; the caller's identity
  is recorded, never a body-supplied user. With trivia wins and media plays, the feed now sees
  games, media and app pages; verses come next. Verified live.
- `d14f4476` Family guard: **media plays land on the activity feed** — a successful music,
  video, podcast or audiobook play through Jarvis records to the person's stream after the
  play succeeds (`activity.record`; failures record nothing). Trivia wins were the first
  recorder; app feature pages and Bible verses come next. Deployed and verified.
- `6ae23fe6` Family guard: **external app media sessions land on the activity profile** — a
  notification-access media-session reader (mirroring `PhoneUsagePlugin`) pulls what YouTube
  and other apps publish while playing (title, artist, album, playing state) and posts it to
  the activity feed (`feature: media`, `source: external`), so listening outside Jarvis lands
  on the digital profile. Android-only; the sync stays quiet without notification access.
  Per-app foreground time and deeper metadata follow on the same engine. Verified live
  (served bundle ships `MediaSession`).
- `2d80bf92` Fix: **HA "login works again" notice no longer spams while flapping** —
  `auth_failed` re-armed the credential flag during the quiet window, so every later accepted
  call fired another recovery notice. A rejection inside the quiet window now stays quiet
  without re-arming; recovery only fires for a genuine new outage after the window passes.
  No "login works again" lines since deploy.
- `3de98f13` Family guard: **video themes redeemed or redirected** — Jarvis now reads a
  member's recent watching (media sessions) and suggests a redeemed alternative for a
  creator or theme that is not appropriate: first try to redeem the theme (a wholesome
  creator with the same kind of content — gaming, comedy, science, vlog, fitness, music
  video, film, animation), and where no redeemed equivalent exists (news, pranks) redirect
  to a different wholesome activity. A reviewed theme/creator map
  (`services/common/video_recommendations.py`) maps themes to a vetted creator with a
  plain-language reason and a redeemed/redirected flag. `GET /api/recommendations/video`
  resolves the signed-in member, pulls recent activity from telemetry, runs the matching,
  and returns only the derived suggestion (the raw feed stays parents-only). A
  `VideoRecommendationCard` on the Health page surfaces it to the member. Music and video
  recommendations now both live on the Health page. Verified live (PewDiePie → DanTDM).
- `8d5bc4f6` Family guard: **music style matching, the first recommendation brick** — Jarvis now
  reads a member's recent listening and suggests a Christian artist with a similar style
  (genre, era, energy) instead of an artist whose lyrics are not appropriate. A reviewed
  genre/artist map (`services/common/music_recommendations.py`) maps lanes (pop, hip hop,
  rock, country, electronic, indie, acoustic, latin, classical, R&B) to a vetted Christian
  artist with a plain-language reason. `GET /api/recommendations/music` resolves the
  signed-in member, pulls recent activity from telemetry, runs the matching, and returns
  only the derived suggestion (the raw feed stays parents-only). A `MusicRecommendationCard`
  on the Health page surfaces it to the member. Music first, per the build order; video
  themes (redeemed or redirected) follow. Verified live (Drake → Lecrae).
- `ac963b2c` Family guard: **calls and SMS land on the activity profile** — the sideloaded
  app now reads the call log and SMS store with runtime permissions (READ_CALL_LOG, READ_SMS)
  and posts each new call and message to the activity feed (feature calls/sms), with direction,
  duration (calls) and body (SMS), contacts resolved best-effort. A `CommsPlugin`
  (hasAccess/requestAccess/getNewCalls/getNewSms) plus `CommsSync` (5-min poll) reuses the
  existing activity pipeline; a per-store timestamp cursor keeps it incremental and caps each
  pass at 200 rows. Android-only: the sync stays quiet without permission or a token.
  Reuses `/api/activity`, no backend changes. Verified live (call + SMS events round-tripped).
- `eb0d9071` Family guard: **per-app foreground time joins the digital profile** — the phone's
  `getToday()` now also returns the day's top 20 apps by foreground time (UsageStatsManager),
  posted through `/api/usage` and merged per package into the day record's `apps` array
  (longest millis, latest last-use wins; re-report never shrinks a row). Top-apps view lands
  on the parents' surface later. Verified live (POST+GET usage round-trip including the apps
  array).
- `8c8c81c1` Family guard: **general phone usage lands on the activity profile** — the phone's
  Usage Access now feeds the digital profile: a `PhoneUsagePlugin` (mirroring
  `StepCounterPlugin`) reads `UsageStatsManager` (daily screen time, when the day started and
  stopped via first/last use, and the session count) and syncs through `/api/usage` to
  telemetry's `tel:usage:{user}` store, merged by day. A `PhoneUsageSync` component posts it
  every five minutes from the web bundle; the gateway injects the authenticated user.
  Android-only; the card hides elsewhere and the plugin exposes `openSettings` for a future
  Settings button. Per-app detail follows. Verified live (POST + GET via the deployed route).
- `e54d8cd6` Execution runs on the **host network** (the only service that does) and had no
  telemetry address — telemetry publishes no host port, so the new activity recorder and
  every execution-origin admin notice were silently failing. `TELEMETRY_SVC_URL` now points
  at telemetry's fixed bridge address (`172.26.0.253:11438`), the `HOST_` form is documented,
  and the feed was verified live (a recorded event read back over HTTP).
- `b200f8d4` Health: **collected data is parents-only, suggestions are the member's** — the
  Phase 11 visibility rule: a minor's raw feed is open to both parents (including the linked
  mother) and no one else; the member sees their suggestions and recommendations, never the
  stream behind them; the father–mother parents' page stays mutual and revocable.
- `590c7de1` Health: **video themes get redeemed or redirected too** — a YouTube video or
  creator with a negative theme is first redeemed (the same theme told well by someone who
  lifts people up) and only redirected when redeeming is not possible; creators are
  first-class, reviewed once and applied everywhere.
- `620eace8` Family guard: **the activity feed and the full-parity guardianship plan** —
  telemetry keeps a capped per-person activity stream (`POST/GET /api/telemetry/activity`),
  execution records through `activity.record` (trivia wins wired first; music, video, pages,
  verses next), and Phase 11 was rewritten to full Bark/Qustodio/Family Link parity,
  **profile-first**: device collection → one living digital profile per person → Jarvis
  recommendations with positive Biblical direction (style-matched music, video themes
  redeemed or redirected). Deployed `telemetry` + `execution`.
- `48657a16` Health: **digital guardianship** is planned (Phase 11) — the Family
  Link/Qustodio/Bark class kept on our own server: an activity feed for music,
  games, pages and verses; three-audience profiles (the person, their parents, the
  linked mother for a minor) plus a mutual father–mother page; boundaries through
  the house DNS and the app; local-only, gentler concern guidance; a prayer and
  discipleship surface grounded in real activity; private note lanes for the quiet
  kids and spouses. Docs only.
- `5a4e4748` Recipes: **search the offline index first** — a local SQLite FTS index over
  **318,830 CC0 Food.com recipes** (`scripts/build_recipe_seed.py` → `data/recipes/recipe-seed.db`,
  bound into execution; ingredients carry real units joined from the Karo8870 mirror) answers
  `recipe_search` instantly and TheMealDB only fills what is left; `shelf_fetch` reads one full
  recipe by id. The Recipes panel tags cards Food.com/TheMealDB and opens Food.com picks locally.
  Verified live (blueberry search returned 12 offline hits; a fetch returned the full recipe).
- `753624fb` Family: the tab row is icon-only below `sm` (labels return from `sm` up, aria-labels
  keep the names), so five tabs finally fit a phone; the Announce panel dropped its calendar and
  notes link cards — timers and announcements are the whole panel.
- `93e6b7d3` Health: a **digital wellbeing** phase is planned (phone usage sessions and per-app
  time from Android's `UsageStatsManager`, a hub card with first/last use, screen time, a session
  timeline and top apps, plus what each person does most with Jarvis). Docs only so far.
- `99306dbd` Recipes: **search TheMealDB from the Add-a-recipe panel** — when a link is
  not to hand the panel searches TheMealDB and shows photo results; picking one saves like
  any imported recipe with attribution to themealdb.com. Verified live through the
  deployed route (12 results with full ingredients and steps).
- `50db931f` Recipes follow-ups: a **name filter** over the shelf on the Recipes page
  (instant, offline) and a yield normalizer for imported recipes (a site sending
  `["12", "12 cookies"]` now reads "12 cookies"). Verified live by re-importing the
  pinchofyum recipe through the deployed route.
- `40d0a447` Recipes: import a recipe from a pasted link — an **Import a link** panel
  on the Recipes page posts the URL to `/api/recipes/import` (execution's new
  `recipe_import` handler), which fetches the page and reads its schema.org `Recipe`
  JSON-LD (arrays and `@graph` included), previews title/site/time/yield/counts, and
  **Save to Recipes** writes the note with guessed level labels and a source line.
  Fails plainly on pages without recipe data. Verified live (imported a pinchofyum
  cookie recipe through the deployed route).
- `69ce717a` Location uploads coalesce (one request in flight, one trailing —
  the same guard the step sync got) and identity's SQLite branch pool is
  widened to 20+40 with a 60 s wait. Michele's phone had fired parallel
  location posts that exhausted the default 5+10 pool, so identity served
  500s and the gateway logged timeout bursts. `ui` + `identity` deployed.
- `bf035858` Recipes share: the week plan and the shopping list can go into a
  family chat in one tap — Share plan / Share list on the Meal plan panel send
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
- `a20f7e30` Recipes: plan the week and build the shopping list — the Recipes
  page's **Week plan** picks a dinner per day from the shelf (a tick marks it
  cooked, a surprise-me fill, repeats flagged) and saves `Recipes/Meal plan.md`
  as a `#meal-plan` checklist; **Build shopping list** turns the week's
  ingredients into `Recipes/Shopping list.md`, merging duplicate lines and
  keeping ticked items by text. Live on OTA 1.5.2 (bundle `a20f7e30`).
- `831e29e6` Chat: @Jarvis answers from the room itself — both answer paths
  (the app's send and the gateway's Talk monitor) now lead with a live
  snapshot of the room's recent messages: replies appear as assistant turns
  with the envelope stripped, everyone else as "Name: text". The stored
  room transcript is only the fallback. This is the fix for "the bot has no
  clue about the room it is in".
- `cb530641` Recipes: share to chat and teach Jarvis — the Recipes page can
  send a recipe into a family chat through the generalized
  `ShareToChatSheet` (a short `🍽 title: excerpt` message with an optional
  note, never the whole file), and a header **Teach Jarvis** button runs the
  notes RAG sync over `Recipes/`. OTA 1.5.2 republished on this bundle.
- `d8acfcab` Steps and version 1.5.2: the app's step sync coalesces to one
  request in flight (a walk used to POST one per pedometer reading — an ~11/s
  storm; the one-minute burst of upstream timeouts it caused was logged as
  hundreds of ERROR tracebacks). The gateway now logs `TimeoutError` as a
  single line and keeps tracebacks for real faults. `ui` + `gateway` deployed,
  OTA 1.5.2 published.
- `be729dcd` Chat: @Jarvis in Nextcloud Talk keeps a room transcript — a
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
- `fb95eccd` Recipes Phase 0–1: `scripts/import_recipes.py` selects ~40
  kid-tiered recipes per level from the CC0 Food.com dataset (522,517 recipes,
  fetched via anonymous `kagglehub`), renders Nextcloud-Notes markdown from
  each live food.com JSON-LD (ingredients with units, steps, per-serving
  `foodcom` nutrition, ×7 servings) and wrote 120 level-tagged notes
  (`#level-1/2/3`) which were uploaded to Nextcloud `Recipes/`. App-side
  Recipes phases (2+) are still planned. Script-only commit — no deploy.
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
  explicit loading/empty/error state — never fake values.
- **Never act through a family member's account** to inspect or test (reading their rooms,
  posting as them). Use the household account, the `jarvis` bot, or ask Jeremiah first.
- **Live test data is always cleaned up** (standing user instruction). Prefer engine-level
  checks that write nothing; if a smoke test must post to the live server, note the exact
  record and delete it by exact value right after (`redis-cli lrem <key> 1 "<json>"`, not
  `-x`), then confirm it is gone. Never leave seeded artists/creators/calls in a real feed.
- **UI gates**, run in `services/ui`: `npm run typecheck`, `npm run lint`
  (baseline 0 errors / 11 pre-existing warnings), `npx vitest run`
  (204 files / 1602 tests), `npm run build` (also packages the OTA bundle).
  After adding Tailwind colour classes run `npm run gen:theme-css` or
  `src/themes/themeRemap.test.ts` fails.
- **Backend tests**: `.venv/bin/python -m pytest <paths>` — `python` is not on
  PATH. Current baselines: health **108**, identity **416**, gateway **1532**,
  execution **914 passed / 60 skipped** (ignore
  `services/execution/tests/test_calendar_window_and_uid.py`; it needs the
  optional `vobject` package).
- **Deploy**: `./scripts/deploy_remote.sh jeremiah@192.168.2.205 /home/jeremiah/SharedLLM <services>`
  in the background (~5 min; waits for GitHub Actions *Build & Push Images* and
  pins the server to the **branch tip**). Land code and docs together; a
  docs-only tip makes un-rebuilt images fall back to `:latest` with a warning.
  Docs-only changes need no deploy.
- **Post-deploy verification** (added after a missed log spike): after every
  deploy, beyond the script's own health check, (1) aggregate ERROR/Traceback
  counts per hour for each touched service from
  `docker logs --since … --timestamps` on the server (pass Z-suffixed UTC
  timestamps — the daemon reads bare timestamps as local), (2) smoke-test the
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
  (family of 7, kids 7–18) and prefers local/free, kid-safe, no-subscription
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
  sessions — a roster locked from the room's participants at start (the bot
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

### `docs/RECIPES.md` — Phases 0–1 built, rest planned

Phases 0–1 are shipped: the importer (`scripts/import_recipes.py`) filters the
CC0 Food.com corpus by the kid level rules, ranks by staples/few ingredients/
short time and renders level-tagged markdown notes (per-serving `foodcom`
nutrition, scaled to 7 servings) — 120 notes live in Nextcloud `Recipes/`.
The Recipes page now shares any recipe into a family chat and has a Teach
Jarvis RAG-sync button (`cb530641`). Remaining phases: weekly meal-plan
note, shopping list generator, Jarvis/sharing polish, search & import (FTS +
TheMealDB + schema.org paste-a-link), photo/handwriting import (Alpaca OCR),
Health tab connection (planned meals log nutrition), Noom-style calorie
budgets and logging.

### `docs/HEALTH_HUB.md` — largely built

Phases 1–5 core are live (profiles, open ingestion, sleep/readiness, nutrition,
budgets/day view, sharing/export) plus opt-in cycle tracking. Remaining
documented phases: Health Connect/device import, recipe↔log round trip, weekly
digest, family challenges/cheers, widgets (dashboard health widget exists),
photo/voice logging, fasting windows, micro-lessons and chat coach,
digital wellbeing (Phase 10 — phone usage sessions, top apps and Jarvis
feature use; planned 2026-10-23), and digital guardianship (Phase 11 — the
Family Link/Qustodio/Bark class on our own server, focused on collecting mobile
device data into a living per-person profile Jarvis reads to make recommendations
and give positive Biblical direction (an inappropriate artist becomes a
like-styled Christian one; a video theme is redeemed — same theme told well by a
better creator — or redirected): activity feeds, music (Jarvis playback +
optional Spotify linking + media sessions), video and creator entities, device
usage, calls/SMS, web, budgets and
routines, app control, filtering, location alerts, local-model monitoring with a
weekly recap, plus three-audience profiles, the shared parents' page, prayer
surfaces and a voice for the quiet ones; collected data is parents-only while
suggestions belong to the member they are for, and the phone pulls external-app
activity (YouTube, Spotify, podcasts) through media sessions; planned 2026-10-23).

## 4. Open small items

- Notes "Still open" (docs/NOTES.md): reminders time/place, attachments/images/
  drawings/audio, Nextcloud sharing, list/grid toggle, autosave/drafts, toolbar,
  keyboard shortcuts, per-item checklist editing/reordering, undo toasts,
  versions, offline.
- Media leftovers (docs/MEDIA_OVERHAUL.md): BUG-43 podcast episode list,
  BUG-48 pending JSON-RPC calls on close, P8-T2 native Android background audio.
- docs/PENDING_TASKS.md: RAG ingest of promoted notes and plan reflections.
- Health: periodic cycle data never appears in family projections; the family
  cards render sleep/calories/cycle/support — watch for layout regressions.
- Chat latency: players still see a new question up to one poll apart (2 s in a game). Talk long-polling
  (`lookIntoFuture=1`) or Nextcloud notify_push would make it near-instant; see the `HEAD` latency entry.
- Home Assistant `get_areas` 401s: resolved — not an expired token. HA serves `/api/template`
  to admins only; see the `HEAD` chat + trivia entry.

## 5. Immediate next steps

1. The Android collection engine is complete: usage, media sessions, calls and SMS all
   land on the digital profile (commits through `ac963b2c`). Video watching is covered by
   media sessions (YouTube title/creator/duration) plus per-app foreground time.
2. Jarvis recommendations from the profile: music style matching (`8d5bc4f6`), video
   themes redeemed or redirected (`3de98f13`), podcast recommendations, and book
   recommendations are done — games and activities follow. Suggestions are the member's
   to see; the raw feed behind them stays parents-only.
3. Keep the plan docs (`docs/HEALTH_HUB.md`, this handover) updated in the same commit as
   the code when scope changes.

## 6. Where the details live

`docs/MEDIA_OVERHAUL.md`, `docs/NOTES.md`, `docs/BIBLE_STUDY.md`,
`docs/RECIPES.md`, `docs/HEALTH_HUB.md`, `docs/HEALTH_STEPS.md`,
`docs/FAMILY_HUB.md`, `docs/ROLES.md`, `docs/DASHBOARD.md`,
`docs/PENDING_TASKS.md`, `docs/MOBILE_OTA_UPDATES.md`, plus service READMEs
under `services/*/README.md`.
