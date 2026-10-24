# Health Hub: the best of the health apps in one

Status: **live, partially built (updated 2026-10-24): phases 1–5 core, opt-in cycle tracking, family sharing/export and the USDA food seed are deployed; Phases 10–11 (digital wellbeing, guardianship) are in progress.** See HANDOVER.md for the live commit.
Companion plans: `docs/RECIPES.md` (Phases 10–11 carry the recipe-nutrition and
calorie-budget work that this hub consumes), `docs/HEALTH_STEPS.md` (step counting,
already live).

## Implementation status (2026-10-22)

The hub is a new `health` service (`services/health`, port 8011) with its own
SQLite store, proxied through the gateway at `/api/health/*`. It deliberately
does **not** copy activity: steps, workouts and distance stay in `geo`, and the
Today screen composes geo's number under its existing query key.

Built in this pass:

- **Profiles** per family member: birth year, height, sex, activity, goal,
  units, kid mode, sleep need, water goal, weekly active minutes.
- **Open metric store**: batch ingestion with a `source` on every reading,
  canonical units (lb→kg, oz/cup/L→ml, h→min), sanity bounds, and idempotent
  device replay via `external_id`. This is the Health Connect/wearable door:
  a future bridge posts the same shape; no schema change is needed.
- **Manual inputs** for sleep, weight, resting heart rate and water, so the hub
  works with no device at all.
- **Sleep score** from duration + efficiency + how it felt, with explicit
  confidence and missing signals.
- **Readiness** from sleep, resting HR against the person's own 30-day median,
  and the morning energy/mood/soreness check-in. No signals means no score.
- **Nutrition**: OpenFoodFacts search, barcode lookup, manual entry, per-meal
  day view, per-serving macros, density guides, calorie range from Mifflin-St
  Jeor, and guidance-only ranges for minors (never a deficit).
- **Opt-in cycle tracking**: private by default and shown only for a profile
  whose sex is `female`. Period dates, flow, symptoms, mood/energy, BBT
  and ovulation-test logs are stored per person; phase, cycle day, next-period
  estimate and confidence are computed from logged dates (explicit averages or
  observed 15–90 day gaps). Irregular cycles are described, not diagnosed.
  Fertile windows are deliberately not predicted: every surface says estimates
  only, not contraception or medical advice. The family projection shares at
  most phase/next-period, never flow, symptoms, notes or periods. An adult can
  share with a spouse, who also sees short phase-aware suggestions for being
  supportive, labelled as general ideas rather than medical advice.
- **Family roles and sharing**: `father`, `mother`, `child`, `teen`,
  `adult_child`, `other` on the Identity account, with `family.manage` for a
  Home manager/mother. Minors keep circle activity sharing on (locked in the
  UI and refused by the API); an adult child may opt out. Cycle sharing for a
  minor is limited to the one linked mother account. See
  [ROLES.md](ROLES.md).
- **Family sharing** per scope (`sleep`, `nutrition`, `cycle`), off by
  default; only explicitly granted scopes are projected. Export returns all of
  one person's data as JSON.
- **UI**: `Today / Fitness / Sleep / Nutrition / Health` tabs. The previous
  Fitness page is hosted unchanged under its tab, so hourly step charts,
  workouts, achievements and the family activity feed keep one implementation.

Not built yet, and not pretended otherwise: Health Connect/device import,
recipe↔log round trip and one-tap planned meals, weekly digest, family
challenges/cheers, dashboard and Android home-screen widgets, photo/voice food
logging, fasting windows, micro-lessons and the chat coach. Those remain the
later phases below.

The goal: one local, family-first Health tab that pulls the strengths of the apps
the family has used — Noom, Google Health (formerly Fitbit), MyFitnessPal and friends —
into the app that already has steps, recipes, a calendar, a chat coach and a family hub.

## Strengths worth copying

| Source | What it does best | How Jarvis adopts it |
| --- | --- | --- |
| **Noom** | Psychology-first habit building: bite-sized daily lessons, a personalized calorie range, green/yellow/orange calorie-density colours, water tracking, step goals that ramp up, check-ins with a coach, weekly commitments | Daily micro-lessons on the Health tab; Mifflin-St Jeor calorie range with an optional gentle deficit for adults; density colours as *guides, not verdicts*; water log; weekly goals; Jarvis chat as the coach, grounded in the day's data |
| **Google Health (Fitbit)** | Clear four-tab layout (Today / Fitness / Sleep / Health), customizable metric dashboards, Sleep Score, Daily Readiness (HRV + sleep + recent activity), Active Zone Minutes, a flexible **weekly** cardio target instead of rigid daily goals, aggregation through Health Connect, family leaderboards, AI coach | Same tab shape, per-user pinned metric cards; Sleep and Readiness cards from Health Connect data when a phone or watch provides it (manual morning check-in otherwise); weekly activity targets; Android Health Connect import; family leaderboards and challenges |
| **MyFitnessPal** | The 14M+ food database and fast search, barcode scanning, Meal Scan (camera), voice logging, recipe importer + recipe builder (bulk-paste ingredients to per-serving macros), meal planner, macro goals by gram/percent and per day, per-meal macro breakdowns, fasting tracker, weekly digests, data export | Recipe-first logging: the imported `Recipes/` already carry per-serving macros — a planned dinner logs in one tap with portions; barcode lookup via OpenFoodFacts; photo logging through the family's Alpaca vision (recipes Phase 9 pattern); voice logging through the existing assistant; weekly digest card; macro goals by day with per-meal breakdown |
| **Apple Health** | The aggregation hub: everything in one store, sharing with family, medical records, cycle and medication tracking | One health store per person with family sharing toggles; medical documents are a later, opt-in phase |
| **Strava** | Social motivation: kudos, clubs, segments, challenges | Family cheers and a weekly family challenge (steps, veg servings, cook nights); share a health card to a family chat in one tap |
| **Garmin / Whoop** | Training load, training status, recovery/strain and journal behaviours | Simple fitness-fatigue and readiness once heart-rate data flows in; a "how do you feel" journal when it does not |
| **Cronometer** | Honest micronutrient tracking on verified data | Show a small set of micronutrients when the data exists, free, no paywall games |
| **WW (WeightWatchers)** | Simplifying decisions with free/ZeroPoint foods | A "free foods" list for snacks and kids, so nobody counts carrot sticks |

## Ground rules

- **Local and free.** No subscription, no ads, no data leaving the house — the same
  promise the rest of Jarvis makes.
- **Adults and kids are different.** Calorie budgets and deficits are adult-only,
  opt-in tools. Kids and teens get balance, habits and guidance ranges — never
  weight-loss targets, weigh-in pressure or moral colouring of food.
- **Guides, not judgements.** Density colours and scores are framing aids; the copy
  must never turn food or a missed day into a failure.
- **Opt-in and quiet.** Every metric can be turned off; empty cards hide; the tab
  starts with what the household actually has (steps today), and grows with data.
- **One day, one screen.** Today answers "how is today going?"; trends live a tap away.

## Design sketch

- Tabs: **Today / Fitness / Sleep / Health** (nutrition lives on Today with a
  deeper nutrition view; the recipe link makes it the natural home).
- Today: steps ring, Active-Zone-like minutes against a weekly target, readiness,
  sleep score, water, and the next planned meal with one-tap "cooked" logging.
- Fitness: activity log, weekly plan, family challenge, leaderboard.
- Sleep: sleep sessions, score, bedtime reminder (the timer subsystem exists).
- Health: weight trend, measures (resting HR, HRV, SpO2 when available), notes,
  and the family sharing toggles.
- Nutrition: day log with per-meal macros, calorie range for adults, density
  colours, quick add (search / barcode / photo / voice), weekly digest.

## Interface principles (what the best-rated apps prove)

Researched Oct 2026 across Noom, Oura (4.9★ / 164k ratings), WHOOP (4.8★),
Google Health (ex-Fitbit), Apple Health & Fitness, Garmin Connect, Strava,
MyFitnessPal, plus UX award winners and published case studies.

1. **One score that interprets itself.** The most-loved apps close the "now what?"
   gap for you. WHOOP's Recovery ring and Oura's Readiness give a colour, a number
   and one plain sentence; 300+ App Store reviews of Apple Health keep repeating
   "I have all this data but I don't know what it means." The Today screen opens
   with a check-in or score and a one-line reading — not a chart.
2. **Layers: glance → focus → explore.** Oura's award-winning framework
   (at-a-glance rings/bars → focused metric views → interactive charts) and the
   Apple Health redesign (daily summary → personal baselines → weekly recap).
   Jarvis: Today glanceable → card detail → Trends, each one tap deeper.
   "Designing for moments, not metrics."
3. **Colour as meaning, tuned to the person.** Semantic hues per domain (Oura's
   teal/indigo/amber), thresholds against **your** baseline ("within your range"),
   never population averages. Colour appears only when it carries information —
   restraint over alarmism; the LabViz award win was specifically about moving
   away from red/green good/bad semaphore.
4. **Five-second design.** ~70% of tracker interactions are sub-5-second glances.
   Home ≈ 60% the day's state, 40% the next action; large hero numbers with quiet
   labels; the ring sweeps and the number counts up together (reduced-motion
   respected) as a satisfying reveal.
5. **Forgiving by default.** Case studies agree: "failed" days and red warnings
   cause deletion. Forgiving streaks, rest-day states and recovery coaching beat
   guilt — gently suggesting a 10-minute walk after two skipped days lifted return
   rates by 31% in one project. Rings celebrate consistency; they never punish.
6. **Friction is the enemy.** Quick-log bottom sheets for one-handed entry; smart
   defaults (last night's portion, the usual bedtime); oversized targets (56px+)
   and gesture adjusters for sweaty hands; a five-second undo; 60-second onboarding
   that ends with a first log, not a form.
7. **Tell me what to do next.** The 2026 differentiator: WHOOP's strain target and
   Google's Coach feed answer "what now?" on the first screen. Jarvis is already a
   coach — the Health tab should ask it ("given your sleep, a walk beats a hard
   workout today"), grounded in the user's own data, never a black box.
8. **Close the loop (journal → insight).** WHOOP's Journal and Oura's Tags turn
   logged behaviours into personal cause-and-effect ("reading before bed → +8%
   recovery"). Jarvis's check-ins can do the same locally: sleep vs. late meals,
   steps vs. bedtime.
9. **Personal first, weekly second.** Personal baselines over averages; a weekly
   recap that spots one trend and suggests one small action; a monthly story card.
10. **Family and friends, kindly.** Achievement beats competition (case-study
    finding); family/partner streaks with a shared stake motivate; cheers for kids;
    leaderboards opt-in and never about weight.
11. **Customizable, but composed.** Pinnable/reorderable cards (Google Health) —
    yet a small, deliberate set: every card earns its place, empty ones hide, so
    the tab never reads like a spreadsheet.
12. **Accessible and calm.** WCAG contrast, font scaling, screen-reader labels on
    rings and charts, dark-first canvas (Oura's near-black), restrained typography
    (hero number, small caps labels), privacy explained in plain language. The
    top-rated health apps win on "easy to read, simple to operate", not feature
    volume.

## Ties into the rest of Jarvis

- **Recipes** — planned meals log into the day; the shopping list knows what is
  planned; `#level-1/2/3` recipes make "what can a 12-year-old cook tonight?"
  answerable in chat.
- **Chat** — Jarvis is the coach: "how much protein is left today?", "plan a lighter
  dinner", "did we hit the step challenge?" — grounded in the user's own data.
- **Calendar** — workout plans and challenges get real slots; sleep/bedtime reminders
  reuse the reminder pipeline.
- **Dashboard & widgets** — a today card (steps, readiness, next meal) for the
  dashboard and the Android home screen.
- **Family hub** — challenges, cheers and shared goals live where the family already is.

## Food data sources (decided 2026-10-22)

Nutrition today is live OpenFoodFacts only: one source, rate-limited (10 searches/min,
15 product reads/min per IP) and weak on generic whole foods. The decided shape is
**two sources, one door**, from the 2026-10-22 comparison research (12 candidates):

| Source | Shape | Licence / cost | Local use | Verdict |
| --- | --- | --- | --- | --- |
| USDA FDC non-branded | 13,620 foods (measured), 22,046 FNDDS portion weights | CC0 public domain | bulk zips allowed | **mirror: the seed** |
| USDA FDC Branded | 433,916 products | CC0 | allowed | skip (OFF covers brands) |
| Open Food Facts | 4.83M products, barcodes | ODbL/DbCL, free | dumps + caching encouraged | **live search + barcode, permanent cache** |
| Food.com dataset | 522k recipes + nutrition | CC0 | allowed | **recipe corpus** (see [RECIPES.md](RECIPES.md)) |
| TheMealDB | 793 meals, no nutrition | dev key, attribution | cacheable | parser test set only |
| Nutritionix | 1.27M items | $499/mo | caching prohibited | reject |
| Edamam | per-ingredient analysis | $14–$299/mo | 4-macro cap, delete on cancel | reject |
| FatSecret | food + diary | free 5k calls/day | 24 h / IDs only; bans private-network apps | reject |
| Spoonacular | recipes + ingredients | $29–$149/mo | 1 h cache cap | reject |
| API Ninjas (CalorieNinjas) | nutrition | $99/mo for storage | no caching below Business | reject |
| CNF / CoFID / AFCD | national food tables | OGL / CC BY | allowed | optional, later |

How it lands:

- `scripts/build_fdc_seed.py` downloads the three bulk archives (~14 MB zipped),
  verifies them, imports a read-only SQLite seed at `/data/fdc-seed.db` (beside
  `health.db` on the existing `./data/health` mount), asserts the known counts
  (395 / 7,793 / 5,432) and writes a `meta` table with release, source URLs,
  licence and build time. FTS5 over names; the NDB number / FNDDS food code are
  the stable keys (FDC_ID churns on every record change). Missing nutrients stay
  NULL — rendered "not reported", never zero.
- `/food/search` serves seed hits first (each with household portions, e.g.
  "1 cup, chopped" → grams) then appends OpenFoodFacts results once per
  submitted query; every result carries `source` (`usda_fdc` | `openfoodfacts`)
  and the response says whether the seed is present. Seed missing → OFF only,
  one log line, no error; OFF down → the seed still answers.
- Barcodes: check the service's own permanent per-product cache, else read the
  OFF product endpoint (still v2 there: it normalizes `*_100g` values and keeps
  `serving_quantity`, which the v3 payload drops), then cache forever. OFF and
  USDA rows are never mixed in one record. Unknown barcodes arrive as HTTP 200
  with `status: 0` — the body decides, not the status.
- Attribution "Contains data from Open Food Facts, ODbL" (and USDA FDC, public
  domain) lives in the nutrition About surface.

Self-hosting all of Open Food Facts (Product Opener: 8 GB RAM / 6 cores in its
own dev guide, 16 GB dumps) is out for a family box; the per-product cache plus
live search is the ecosystem-standard pattern.

## Phases

### Phase 0 — decisions

- [x] Confirm profiles: one per family member; age, height, sex, weight and activity are optional; kid mode is explicit.
- [x] Decide data sources: an open, source-tagged ingestion API now (manual, Health Connect, watches, scales), with manual logging always available. No device is required.
- [x] Decide the first slice: all of the dependency-ordered core — profiles, sleep, readiness and nutrition — while reusing the existing activity stack.

### Phase 1 — people and profiles

- [x] Health profile per user (age, height, sex, activity level, goal, units), stored in the health service; kid mode flags.
- [ ] Health settings page: turn metrics on/off, family sharing toggles. Profile and sharing toggles are built; per-metric on/off is not.

### Phase 2 — data in

- [ ] Health Connect import on Android (steps already sync; add sleep sessions, heart rate/HRV, weight and workouts) with a native bridge. The ingestion API and idempotent `external_id` replay are ready for it.
- [x] Manual logs for sleep hours/quality, weight, resting heart rate and water. Workouts remain on Fitness/geo.
- [x] A health store the gateway can read per user, with every reading carrying its source.

### Phase 3 — scores and targets

- [x] Sleep score from duration + interruptions (or manual quality), with confidence and missing-signal notes.
- [x] Readiness from resting HR/sleep when available, else the morning check-in. Recent activity is not yet included.
- [ ] Weekly cardio target and Active-Zone-like minutes from step/heart-rate data. The target is stored in the profile; no progress ring yet.

### Phase 4 — nutrition logging

- [ ] Recipe-first logging: a cooked planned meal logs its per-serving macros with a portion picker. `FoodEntry` carries `recipe_path`; the recipe import/one-tap flow is not built.
- [x] Quick add: USDA seed search (generic foods, household portions), OpenFoodFacts search, cached barcode lookup and manual entry. Voice and photo logging remain future work.
- [x] Food data depth: the read-only USDA seed is mirrored on the server (13,620 foods, per-100 g plus portions like "1 cup, chopped" = 91 g) and searched before OpenFoodFacts; barcode results are cached permanently. The seed is optional at runtime (`FDC_SEED_PATH`): absent → OFF-only, one log line, never an error.
- [x] Water tracking. Fasting window indicator is not built.

### Phase 5 — budget and day view

- [x] Calorie range for adults (Mifflin-St Jeor with optional gentle deficit); guidance ranges for kids — never deficits.
- [x] Day view: eaten vs range, protein/carbs/fat, per-meal breakdown, density colours.
- [ ] Weekly digest card: averages, best day, what changed.

### Phase 6 — habits and coaching

- [ ] Micro-lessons: a small library (nutrition basics, sleep, movement, stress) shown one card at a time.
- [ ] Chat coach grounded in the user's health data; gentle weekly goal setting; reminders for bedtime, water and movement breaks.

### Phase 7 — family

- [ ] Weekly family challenge (steps, veg servings, cook nights) with a leaderboard.
- [ ] Cheers/kudos on a family member's day; share a health card to chat. Scope-limited sleep/nutrition/cycle sharing is built; cheers and cards are not.

### Phase 8 — integrations everywhere

- [ ] Dashboard widget and Android home-screen widget (today card). The existing activity widget is unchanged.
- [ ] Recipes: plan ↔ log ↔ shopping list round trip; Calendar: workouts.
- [x] Family hub entry point: the existing Health nav item now opens the hub.

### Phase 9 — tests, docs, deploy

- [x] Pure tests: BMR/budget maths, scores, portion scaling, density mapping and water. Streaks are not built.
- [x] UI tests: log a meal (search + manual), day view, check-in, water and cycle opt-in/log/share. Challenge join and kid-mode UI hiding are not built.
- [x] `npm run typecheck`, `npm run lint`, full UI suite (191 files / 1538 tests), `npm run build`, backend health tests (108), identity family-role tests and docs.
- [x] Deploy `health gateway ui`, then verify `/health/ready`, the version endpoint and an authenticated `/api/health/profile` (health live since `d766a19f`).

### Phase 10 — digital wellbeing (planned 2026-10-23)

- [x] Phone usage on Android: the built `PhoneUsagePlugin` (mirroring `StepCounterPlugin`) reads
      `UsageStatsManager` — the first brick is in (daily total, first and last use, session
      count land on the profile via the Usage Access permission, which the member grants once
      in Android Settings). Per-app foreground time for the day now follows on the same engine:
      `getToday()` also returns the top twenty apps by foreground time, merged per package in
      telemetry (`tel:usage:{user}` day records carry an `apps` array), posted by
      `PhoneUsageSync` every five minutes. The top-apps view itself lands on the parents'
      surface (step 7).
      Android-only: the card hides everywhere else, exactly like the steps sensor.
- [x] Ingestion mirrors the steps pipeline: the phone owns the day arithmetic, uploads batched,
      idempotent payloads (`source: phone`), coalesced — stored per user, every reading source-tagged.
      Phone usage (`PhoneUsageSync`, 5-min) and per-app foreground time land this way; media
      sessions, calls and SMS reuse the activity pipeline.
- [x] Digital wellbeing card in the hub (`DigitalWellbeingCard`, Health → Today): first use, last
      use, total screen time, sessions, the top five apps by foreground time and a seven-day bar.
      An adult sees their own; a parent switches to each child; a minor sees no card. A per-session
      timeline needs the phone to report sessions (it reports first/last use and a count today).
- [ ] Jarvis itself: log feature events from the web bundle (chat, recipes, notes, timers, games,
      verses) — no permission needed — and show what each person does most inside Jarvis.
- [x] Visibility follows Phase 11's rule, enforced on the server: identity's
      `may_view_raw_data` (`GET /api/internal/may-view-raw`) opens a minor's usage to an admin,
      the father and the linked mother (any mother when none is linked) and to no one else, an
      adult's to themselves; `GET /api/usage/{user}` answers 403 otherwise (any signed-in member
      could read anyone's before). An adult's opt-in `digital` family scope is still to come.

### Phase 11 — family guardianship and discipleship (full Bark / Qustodio / Family Link parity; planned 2026-10-23)

The whole feature set of the parental-control apps — **surveillance included** — kept on our
own server: no cloud account, no subscription, nothing leaves the house. On top of the
watching sits a discipleship layer those apps do not have (prayer grounded in real life, a
voice for the quiet ones): the goal is not just knowing what a child does but guiding and
praying for them well. Our Android app is sideloaded, so calls, SMS, media sessions and
usage stats are all ours to read — the mechanisms Qustodio gets from its sideloaded APK,
without Play-policy limits.

**The focus is the digital profile.** Everything below collects from the mobile devices
first and lands in one living per-person profile — what they listen to, play, watch and
browse, when and for how long, and the patterns across a week. Jarvis then reads that
profile to make recommendations and give positive, Biblical direction (music first: an
artist whose lyrics are not appropriate becomes a Christian artist with a similar style;
video themes get the same treatment — redeemed or redirected).
The guarding mechanics are the collection substrate; the profile and the guidance are the
point.

### Collection

- [ ] **The digital profile (the point of all of it).** Every signal below — device usage,
      music, media sessions, calls and SMS, web, in-app activity — lands on one per-person
      profile: a living picture of what they listen to, play, watch and browse, when and for
      how long, and the week's patterns. This is the data model Jarvis reads for
      recommendations and for the discipleship surfaces further down.
- [x] **Activity feed (music, games, pages, verses).** Telemetry stores a capped per-person
      stream (`POST/GET /api/telemetry/activity`) and execution records through
      `activity.record`; trivia wins, media plays (music/video/podcast/audiobook) and app
      feature pages (the route beacon, posted from the web bundle) are wired; Bible chapter
      reads and imported devotional or study-book pages land as they open in the reader.
- [ ] **Music everywhere, not just Jarvis.** Baseline: everything played through the house
      media stack (local library and `spotify://` through Music Assistant). If possible:
      each member links their Spotify account (Web API `recently-played`, opt-in) so
      listening in the Spotify app itself lands on the profile; every other music app is
      covered by the phone's media-session reader (title, artist, app package).
- [x] **YouTube and every external app (the phone pulls it).** First brick: a
      notification-access media-session reader on the phone pulls what YouTube
      and any other external app publishes while playing — title and creator
      where provided, tracks and artists in music apps, podcast episodes,
      browser media — and posts it to the activity feed (`feature: media`,
      `source: external`) for the family profile. Deeper metadata and per-app
      foreground time follow on the same engine.
- [x] **Device usage (Android).** First brick: daily screen time with when the day started and
      stopped (first and last use) and the session count land via the phone's Usage Access
      (`PhoneUsagePlugin` → `POST /api/usage` → telemetry `tel:usage:{user}`). Per-app
      foreground time now follows on the same engine (top twenty apps per day, merged per
      package, stored in the day record's `apps` array); the top-apps view lands on the
      parents' surface (step 7).
- [x] **Calls and SMS (Android).** First brick: the sideloaded app reads the call log and SMS store with runtime permissions (READ_CALL_LOG, READ_SMS) and posts each new call and message to the activity feed (`feature: calls` / `sms`), with direction, duration (calls) and body (SMS), contacts resolved best-effort. A `CommsPlugin` + `CommsSync` (5-min poll) reuses the existing activity pipeline; a timestamp cursor keeps it incremental. Content monitoring and the parents' surface follow.
- [ ] **Web activity.** The on-device filter reports domains visited and blocked attempts.

### Guarding

- [ ] **Time budgets and routines (Family Link / Qustodio).** Daily limits per device, bedtime
      and school weekly schedules, pause/grace, bonus time, child extension requests a parent
      grants — with Qustodio's semantics (midnight reset, "always allowed" list, lock
      navigation vs lock device).
      **4a built (server + app, 2026-10-24):** a parent sets a child's daily and weekend limits,
      bedtime and school hours, pauses the phone and adds bonus minutes; the child asks for more
      time (with a note) and both parents get a notice; a grant becomes today's bonus. The one rule
      is `services/common/screen_time.py` (midnight reset in the household `TIMEZONE`, overnight
      bedtime, weekday school hours, pause first, bonus extends only the limit, an always-allowed
      list for the dialer, messages and Jarvis). Identity stores rules and requests
      (`ScreenRules`, `ScreenTimeRequest`; only a minor's parents may change them, the child may
      read theirs); the gateway serves `GET /api/screen-time/{user}/status` from the rules plus
      today's reported use, and sends the notices. Screens: `ScreenTimeCard` (the child, Health →
      Today) and `ScreenTimeControls` (a parent, under the child in the phone-use card).
      **4b (next): enforcement on the phone** — the Android app reads the status and locks
      everything but the always-allowed apps; this needs an Accessibility service and a device
      check, and pairs with tamper resistance below.
- [ ] **App control.** Block apps, per-app daily limits, install approvals, per-permission
      gating; Accessibility-service enforcement on the device with Family Link's one-minute
      grace warning before a block lands.
- [ ] **Web filtering.** On-device VPN/DNS filter with category lists, allow/block lists,
      safe-search enforcement, an "ask a parent" request flow, and Qustodio-style bypass
      blocks (unsupported browsers, split-screen, safe mode).
- [ ] **Location guardian.** The geo service already holds live location, history, zones and
      trips; add parent-facing place alerts (arrive/leave), ring device and battery, and the
      one-device-at-a-time live view.
- [ ] **Tamper resistance.** Device-admin uninstall protection, restart on boot
      (`BootReceiver` exists), split-screen/multi-user detection, tamper alerts to parents —
      with a parent access code as the offline unlock/verify escape hatch.
      **Layer 1 built (2026-10-24, needs a device check):** on a minor's phone
      (`status.protected`), `GuardAdminReceiver` (device admin: no plain uninstall) and
      `GuardService` (Accessibility) cover Jarvis's uninstall dialog, its App info /
      device-admin / Accessibility pages with `GuardScreenActivity`, which takes a parent's own
      Jarvis login (`POST /api/guard/parent-unlock`: only this child's parents, five wrong tries
      then a ten-minute wait, 10-minute window, both parents told). The same service enforces
      screen time (4b): while `locked` it sends the child home except for always-allowed apps,
      with the reason and an "Ask for more time" button. Switching device admin or the guard off
      reports to `POST /api/guard/tamper` and both parents get a notice; `GuardWatch` reports a
      guard that was on and is now missing. Decisions live in pure `GuardRules` (JVM-tested).
      Setup is the `PhoneProtectionCard` on the child's Health page (device admin, then
      Accessibility; Android 13+ first needs App info ⋮ → Allow restricted settings for a
      sideloaded app). Not yet: the offline parent code; safe mode and factory reset still get
      around Layer 1.
      **Layer 2 (planned, optional per phone): device owner.** Set once over USB on a phone with
      no accounts signed in: `adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver`
      (sign out Google accounts first, sign back in after). Then the app can call
      `setUninstallBlocked` and the `DISALLOW_SAFE_BOOT` / `DISALLOW_FACTORY_RESET` restrictions,
      a hard block Android enforces itself.

### Watching (Bark parity)

- [ ] **Content monitoring, local models only.** Analyze the collected streams (messages,
      calls, web, music, app usage) for what a parent needs to know — self-harm, bullying,
      isolation, secret-keeping patterns — classified by concern level with per-child
      sensitivity (all / severe / none); serious categories always on.
- [ ] **Alerts and reports.** Only the highest concern pages a parent immediately; a Sunday
      weekly recap (telemetry's report pipeline) with top apps, contacts, sites and standout
      moments; Jarvis chat answers "how was Emma's week?" the way Bark Assistant does.

### Discipleship (what those apps do not have)

- [ ] **Recommendations and positive, Biblical direction.** Jarvis reads the digital profile
      and suggests rather than scolds: an artist whose lyrics are not appropriate maps to a
      Christian artist with a similar style (genre, era, energy); a **YouTube video or
      creator** with a negative theme is first *redeemed* — the same theme told well, by a
      creator who lifts people up — and only redirected when redeeming is not possible; a
      show becomes a better-fit alternative, a game a family pick, and the devotional library
      answers what the week actually showed. Matching works on themes and styles, not names:
      music first (a small reviewed genre map), then video (topic and creator themes), then
      podcasts, books, games and activities. Every suggestion says why it was made, the
      member it is for sees it (suggestions are theirs; the raw feed behind them is not),
      and a parent can bless or block a lane.
      First brick (music): a reviewed genre/artist map (`services/common/music_recommendations.py`)
      plus `GET /api/recommendations/music` returning a suggestion derived from the member's
      listening, and a suggestion in the dashboard's For You widget. Raw feed stays parents-only.
      Second brick (video): a reviewed theme/creator map (`services/common/video_recommendations.py`)
      plus `GET /api/recommendations/video` returning a suggestion derived from the member's
      watching — themes first redeemed (wholesome creator, same kind of content), then
      redirected to a wholesome activity when no redeemed equivalent exists — and a
      suggestion in the dashboard's For You widget. Raw feed stays parents-only.
      Third brick (podcasts): a reviewed theme/show map (`services/common/podcast_recommendations.py`)
      plus `GET /api/recommendations/podcast` returning a suggestion derived from the member's
      listening — themes first redeemed (wholesome Christian shows, similar topic/style), then
      redirected to an audio drama or family walk when no redeemed equivalent exists — and a
      suggestion in the dashboard's For You widget. Raw feed stays parents-only.
      Fourth brick (books): a reviewed theme/author map (`services/common/book_recommendations.py`)
      plus `GET /api/recommendations/book` returning a suggestion derived from the member's
      reading — themes first redeemed (wholesome/classic literature, similar genre), then
      redirected to uplifting family reading when no redeemed equivalent exists — and a
      suggestion in the dashboard's For You widget. Raw feed stays parents-only.
      Fifth brick (games): `services/common/game_recommendations.py` places the week's games
      (per-app foreground time plus media-session titles) into reviewed themes — shooter, horror,
      mature open world, open sandbox, fighting, gambling-style — weighted by time played; the
      most-played one gets a like-for-like swap (Fortnite → Rocket League, GTA → LEGO City
      Undercover, Roblox → a family Minecraft world) or, for spins and loot boxes, a family board
      game night. Games already in good shape get nothing. `GET /api/recommendations/game`.
      Sixth brick (activities): `services/common/activity_recommendations.py` reads the week's
      phone pattern in the household timezone — late nights (last use at or after 22:00 on two or
      more days) suggest a phone-free wind-down, long days (four hours or more on three days)
      suggest time outside — and never repeats the numbers. `GET /api/recommendations/activity`.
      All six kinds render in one `recommendations` dashboard widget ("For You",
      `RecommendationsWidget`), on by default for every role; it shows only what the server
      derived, an explicit empty state when there is nothing yet, and per-kind failures.
- [ ] **Who sees what (the visibility rule).** The collected data is parents-only: a minor's
      profile — every raw section — is open to both parents (including the linked mother
      under `docs/ROLES.md`) and to no one else; a minor does not browse their own raw feed,
      and siblings never see each other. The member's own view is the *output*, not the
      collection: their suggestions, recommendations and their own lanes are theirs to see
      and act on, never the stream behind them. A father–mother shared parents' page stays
      mutual and revocable for accountability; an adult child's data is their own to share
      or hold.
- [ ] **Prayer and guidance.** The parent view pairs the week's real activity with Scripture:
      prayer prompts grounded in what actually happened, conversation starters from the
      devotional library, and a talk journal. Jarvis drafts gentle notes a parent reviews
      before sending — never sent automatically.
- [ ] **A voice for the quiet ones.** One-tap "I need to talk", burden signals (reusing
      check-ins) and private note lanes (mom↔dad, parent↔child) so nobody has to say it out
      loud first.
- [ ] **Sharing follows the family roles (`docs/ROLES.md`).** Minor rules stay circle-on and
      locked; children never see parents' private sections; kid mode respected throughout.

**Build order (profile first):** (1) the digital profile store and the activity recorders
around it — DONE (trivia wins, media plays, app pages, Bible chapters, imported devotional/
study-book reads, external media sessions, general phone usage, per-app foreground time,
calls and SMS all land on the profile); (2) the Android collection engine — usage,
media sessions, calls and SMS DONE (video covered by media sessions + per-app foreground
time); (3) Jarvis recommendations from the profile — IN PROGRESS (music style matching,
video themes redeemed or redirected, podcasts, books, games and activities DONE; a parent's bless/block per lane is still to come); (4) time budgets and routines;
(5) app control and the web filter;
(6) monitoring alerts and the weekly recap; (7) the parents' page and the discipleship
surfaces.

## Acceptance

- [x] Each family member has a profile and their own Health day.
- [ ] A planned dinner logs with one tap and shows calories, macros and portions.
- [x] Barcode lookup works with a permanent per-product cache; photo and voice logging are not built.
- [x] Sleep and readiness show from manual data now and are device-ready; check-ins cover the no-wearable case.
- [x] Cycle tracking is opt-in, private, shown only when the profile is female, and minors can share it only with their linked mother account. An adult can share with a spouse, who also sees phase-aware support suggestions clearly labelled as general ideas.
- [ ] A family challenge is visible, joinable and shared in chat. Only opt-in scope sharing exists.
- [ ] A parent can open each child's profile and the shared parents' page; guidance and prayer
      prompts cite real activity, and every alert names its concern level.
- [x] Kid accounts never see calorie deficits or weight-loss framing.
