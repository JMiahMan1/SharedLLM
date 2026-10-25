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
  most phase/next-period, never flow, symptoms, notes, period dates or the
  cycle day (today minus it is the last period's start), and not the owner's
  own estimate text; a test pins the projected keys. On Health → Today each
  shared scope (sleep, food, cycle) gets its own line, and a viewer in kid
  mode gets "food logged" without a calorie number. An adult can
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

Built since: recipe-first logging (a recipe or tonight's planned dinner logs
to Health with a portion picker; Phase 4), the weekly digest (Phase 5) and the
weekly family challenge (Phase 7).

Built on Android (needs an APK built after this change and a real-phone check, see Phase 2):
Health Connect import of sleep, resting heart rate, HRV, weight and walks/runs/rides.

Also built on Android (same caveat, Phase 8): the *Health today* home-screen widget.

Built since on the server and web (2026-10-24): per-metric switches, kid-mode hiding, the
fasting window, weekly active minutes, streaks, micro-lessons, the coach (chat answers, weekly
goals, daily reminders), cheers and share-my-day, the dashboard's Health today card, plan ↔ log
↔ shopping list, calendar workouts, and photo and voice food logging.

Not built yet, and not pretended otherwise: step and veg challenges.

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
- [x] Health settings page: turn metrics on/off, family sharing toggles. **Cards on your Health page**
  (Health → Health) switches each card on or off: readiness, check-in, sleep, food, water, steps,
  active minutes, streaks, weekly digest, family challenge, cheers, lessons, coach and fasting
  (`services/health/preferences.py`, kept in `HealthPreferences` so an existing database needs no
  migration; `PUT /profile` takes a partial `metrics` map, `GET /profile` returns every switch
  resolved plus the catalogue). Off hides the card on the person's own screens and keeps the chat
  coach off the subject; data and family grants are untouched. Fasting starts off and is adults
  only: a profile in kid mode or under 18 cannot turn it on and always reads it as off.

### Phase 2 — data in

- [ ] Health Connect import on Android (steps already sync; add sleep sessions, heart rate/HRV, weight and workouts) with a native bridge.
  **Built 2026-10-24, not yet confirmed on a phone** (box stays open until it is).
  - **Control:** Health → Health → *Health Connect* (`HealthConnectCard`, Android only): Connect asks
    Health Connect for read access to sleep, resting heart rate, HRV, weight, exercise and distance,
    plus *read in background* where the phone offers it; *Sync now*, *Choose what to share* (Health
    Connect's own screen) and *Disconnect* (stops the import and revokes access; imported readings
    stay and can be deleted like any other). Missing or outdated Health Connect offers the install.
  - **Native:** `HealthConnectPlugin` (Capacitor, mirrors StepCounter/PhoneUsage), `HealthConnectReader`
    (the only Kotlin file: Health Connect's client is a coroutine API; connect-client 1.1.0, gated to
    Android 9+ with `tools:overrideLibrary` because the app's minSdk is 24), `HealthConnectSync` (one
    run), `HealthConnectSyncWorker` (WorkManager, hourly, network required) and
    `HealthPrivacyActivity` (the privacy text Health Connect insists on before it shows its sheet).
  - **Mapping** (`HealthConnectMapping`, pure, JVM-tested in `HealthConnectMappingTest`): every
    payload carries `source: health_connect` and `external_id` `hc:<kind>:<record id>`. Sleep sessions
    → `POST /api/health/sleep` (night = the local day it ended; with stages, duration = time asleep
    and efficiency = asleep / in bed, stage minutes kept; without stages the whole session and no
    efficiency). Resting HR (bpm), HRV RMSSD (ms) and weight (kg) → `POST /api/health/metrics`,
    with the store's bounds applied on the phone so one odd reading cannot fail a batch. Walks
    (incl. hiking), runs (incl. treadmill) and rides (incl. stationary) → the new
    `POST /api/geo/workouts/import` (geo owns workouts), with Health Connect's de-duplicated distance
    total for the session. Raw heart-rate series are not imported (the store has resting HR, not a
    heart-rate series); other exercise types (swims, strength, yoga) are counted and reported as
    "a kind Fitness does not list yet", not filed under a wrong name.
  - **No double counting:** steps are never read (the pedometer posts them). Geo's import
    (`services/geo/workout_import.py`) is idempotent per user/source/external id, refuses an
    unknown type with a reason, never sets steps, and skips a session that overlaps a workout geo
    already has from another source (the same walk tracked by Jarvis's GPS and a watch), using the
    overlap rule automatic detection already uses (`auto_workouts.overlaps`). Imported sessions
    land in geo's own workout store (`geo:workout:{id}` and the per-user/all indexes) in the same
    record shape as a stopped workout, so Fitness, achievements and the feed read them unchanged.
    Sleep from Health Connect ranks above a hand log and the device-use estimate (`_sleep_rank`).
  - **Incremental:** the first sync reads 30 days; later ones re-read from 48 h before the last good
    sync (late watch uploads), and the server's `external_id` replay answers the overlap as
    duplicates. Only a complete read moves the cursor; a 4xx on one payload is counted as
    `rejected` and skipped, anything else retries later. Runs hourly in the background when
    *read in background* is granted, otherwise on each app open (at most every 15 min) and on
    *Sync now*. The card shows the last sync, what it added and why a run did not happen.
  - **Whose data:** the account that connected owns the import; when someone else is signed in on
    the phone the sync posts nothing (`different_account`).
  - **Known limits:** an edit made later in Health Connect to an already-imported record is not
    re-sent (same id, answered as a duplicate); records older than 30 days before connecting are
    not read (Health Connect's history permission is not requested).
  - **Check on a real phone** (Android 14+ with Health Connect built in, and one Android 9–13 phone
    with the Health Connect app): Connect shows Health Connect's sheet listing the six data types
    (and background reading on 14+); its "privacy policy" link opens the Jarvis text; after
    granting, Health → Sleep shows last night from the watch and Health → Today shows resting HR /
    weight with source `health_connect`; a watch walk appears on Fitness once, and a walk also
    recorded with Jarvis's GPS is not duplicated; *Sync now* twice reports "nothing new"; with the
    app closed for over an hour, a new watch reading arrives (background permission granted) or
    the card says it is waiting for the app; Disconnect removes Jarvis from Health Connect's app
    permissions; signing in as another account on the phone posts nothing for them.
- [x] Manual logs for sleep hours/quality, weight, resting heart rate and water. Workouts remain on Fitness/geo.
  Saved nights can be corrected in place (`PUT /sleep/{id}`, the pencil on Sleep → Recent nights).
- [x] Sleep estimated from device use. Each phone or tablet running Jarvis reports the stretches
  its screen was in use between 18:00 and noon (`PhoneUsage.getNight`, native `NightUse`; times
  only, no app names; needs the APK after 437) to `POST /api/usage/night` with its registered device key;
  telemetry keeps them per device (`tel:night:{device}`, 14 nights). `GET
  /api/health/sleep/estimate?night=` joins the devices **assigned to the person** (identity's
  companion devices with that owner, so an admin can assign a tablet to a child) and takes the
  longest stretch with every screen off as the night; a short check inside it (≤ 10 min, quiet
  again after) is a wake-up, not the end (`services/common/sleep_estimate.py`). Sleep → "Estimate
  from my devices" fills bedtime and wake time to correct before saving: saved untouched it is
  stored as `usage_estimate` (below a hand log, which is below a wearable); changed, it is a hand
  log. Too little data returns nothing rather than a guess.
- [x] A health store the gateway can read per user, with every reading carrying its source.

### Phase 3 — scores and targets

- [x] Sleep score from duration + interruptions (or manual quality), with confidence and missing-signal notes.
- [x] Readiness from resting HR/sleep when available, else the morning check-in. Recent activity is not yet included.
- [x] Weekly cardio target and Active-Zone-like minutes from step/heart-rate data. `ActiveMinutesCard`
  on Health → Today rings the Monday–Sunday week against the profile's weekly active minutes (no
  target: the minutes only, and a prompt naming the guidelines rather than assuming one), with a bar
  per day. The gateway composes `GET /api/health/active-minutes?day=` from geo (hourly steps,
  workouts) and the health store (`heart_rate` readings, a new metric, and device
  `active_minutes`) through the pure `services/common/active_minutes.py`: a device's own daily
  total wins its day; otherwise each hour counts the largest of heart-rate zone minutes (64–76 % of
  220 − age once, 77 %+ twice; no birth year, no heart-rate minutes, said so), workout minutes in
  that hour, and brisk-walking minutes (steps beyond 1,000 in the hour at 100 a minute, labelled an
  estimate). Overlapping step devices take the highest, never a sum. A day with nothing recorded is
  missing, not zero; geo being down is named in `unavailable`.

### Phase 4 — nutrition logging

- [x] Recipe-first logging: a cooked planned meal logs its per-serving macros with a portion picker.
  **Log to Health** on an open recipe (Recipes page) and **Log tonight's dinner** on today's row of
  the week plan open `LogRecipeSheet`: portion ½ / 1 / 1½ / 2, meal (dinner by default), the
  portion's calories and macros, then one tap posts a `FoodEntry` with the note's per-serving
  values, `servings` as the multiplier, `recipe_path` set, brand `Family recipe` and source
  `recipe`. The values are read from the note's `## Nutrition` line as `scripts/import_recipes.py`
  writes it (`src/lib/recipeNutrition.ts`, pure and tested): calories, protein, carbs and fat must
  all be stated (fiber, sugar and sodium are kept when present). A note with no nutrition, or one
  missing any of the four, says so and offers manual entry instead (logged as source `manual`,
  still linked to the recipe). Entries show in the Nutrition day like any other.
- [x] Quick add: USDA seed search (generic foods, household portions), OpenFoodFacts search, cached barcode lookup and manual entry.
- [x] Photo and voice logging on the web, both as editable drafts that are never saved by
  themselves. **Photo** (Nutrition → Add → Photo) reads a nutrition label: the picture goes through
  the same vision OCR as workspaces and Raven (execution `/execute/ocr`, which now also takes inline
  `image_base64` through a temp file it always removes; gateway `POST /api/health/food/photo`, 8 MB
  cap), and `services/common/nutrition_label.py` reads only the printed numbers (calories, fat,
  carbs, protein, fiber, sugars, sodium, serving size; "total" forms win over saturated fat or
  added sugars). They fill the By hand form with the raw transcription beside it; what the label
  did not print stays empty and is named; a per-100 g label says to set the servings. Saved as
  source `photo`. A plate of food is not guessed at: no label, no numbers. **Voice** (Add → Voice)
  records through the shared `useAudioRecorder` hook (also the chat composer's), transcribes with
  the assistant's `/api/stt/transcribe`, keeps the transcript editable, splits it into foods and
  spoken counts (`src/lib/voiceFood.ts`: "two eggs and a slice of toast" → eggs ×2, toast ×1),
  looks each up in the food search and shows a draft row per food — match, portion and servings
  all changeable, any row droppable — and logs only on "Log N foods" (source `voice`). Both
  scale portions through the one `src/lib/foodEntry.ts` the search add uses. On the phone the
  photo input (`accept="image/*" capture`) opens the camera through Capacitor's WebView (the
  app holds `CAMERA` and a `FileProvider`), so no native work is needed.
- [x] Food data depth: the read-only USDA seed is mirrored on the server (13,620 foods, per-100 g plus portions like "1 cup, chopped" = 91 g) and searched before OpenFoodFacts; barcode results are cached permanently. The seed is optional at runtime (`FDC_SEED_PATH`): absent → OFF-only, one log line, never an error.
- [x] Water tracking.
- [x] Fasting window indicator (adults only, off until switched on in Cards). The person starts a
  fast (now, or at a time earlier today/last night) with an optional 12/14/16 h or any 8–24 h
  target and ends it; nothing is inferred from food logs, whose times are when they were typed.
  `FastingCard` on Health → Today shows the elapsed time, the time left to the target and the
  last week's fasts, with neutral copy. `GET /fasting`, `POST /fasting/start`, `POST
  /fasting/stop`, `DELETE /fasting/{id}` (`services/health/fasting.py`, `FastingSession`;
  gateway `/api/health/fasting…`). A profile in kid mode or under 18 gets `allowed: false` and 403s.

### Phase 5 — budget and day view

- [x] Calorie range for adults (Mifflin-St Jeor with optional gentle deficit); guidance ranges for kids — never deficits.
- [x] Day view: eaten vs range, protein/carbs/fat, per-meal breakdown, density colours.
- [x] Weekly digest card: averages, best night's sleep, what changed. `GET /weekly?end=` in the
  health service (`services/health/weekly.py`, gateway `/api/health/weekly`) composes the seven
  days ending on `end` against the seven before: sleep minutes and score, calories, water,
  check-in energy and mood. Averages cover only the days with a value (a day with nothing logged
  is missing, never zero); a change is named only past a threshold (15 min sleep, 5 score points,
  150 kcal, 250 ml, half a point of energy or mood); calories are left out in kid mode. The card
  (`WeeklyDigestCard`) sits on Health → Today.

### Phase 6 — habits and coaching

- [x] Micro-lessons: a small library (nutrition basics, sleep, movement, stress) shown one card at a time.
  `src/lib/healthLessons.ts` holds 24 reviewed cards across food, sleep, movement, calm and water,
  each ending with one small thing to try today. Every card is written for the whole family: nothing
  about calories, weight or dieting and no food called bad (a test enforces the wording), labelled
  general ideas rather than medical advice. `MicroLessonCard` on Health → Today shows the card of
  the day (the same for everyone on a day, so the family can talk about it) with arrows through the
  rest.
- [x] Chat coach grounded in the user's health data; gentle weekly goal setting; reminders for bedtime, water and movement breaks.
  **Chat:** before any model, Jarvis chat answers a first-person question about sleep ("how did I
  sleep?"), food ("how much protein is left?"), water, readiness, the week ("how was my week?") or
  this week's goals from the caller's own `/summary`, `/weekly` and `/goals` only
  (`services/common/health_coach.py`, gateway `_health_coach_answer`, after the guardian recap so
  "how was Emma's week?" still goes to a parent's recap). Nothing logged is said plainly, never
  guessed; kid mode gets what was logged and protein, never calories; a topic whose card is
  switched off, or the coach switched off, falls through to the ordinary chat.
  **Goals:** `HealthGoal` (`services/health/goals.py`; `GET/POST /goals`, `DELETE /goals/{id}`,
  gateway `/api/health/goals…`) holds at most two of the person's own goals a week, of the
  challenge kinds the store can measure (water goal days, sleep goal nights, cook nights), with
  progress computed from their logs on every read. The suggestion is one small step: last week's
  count plus one, for the kind with the most room. **Reminders:** bedtime, water and movement-break
  reminders are ordinary daily reminders in the timers pipeline (`/api/communication/timers`,
  recurrence `daily`, opening the Health page), so they ring wherever reminders already do; the person
  picks each time, nothing is preset. All three sit on `CoachCard` (Health → Today) with the chat
  examples.

### Phase 7 — family

- [x] Weekly family challenge with a leaderboard. The kinds are the ones the health store can
  measure honestly from what people log: **water goal days** (water logged reaches the member's
  water goal), **sleep goal nights** (logged sleep reaches their sleep need) and **cook nights**
  (a dinner logged from a family recipe, i.e. a `FoodEntry` with `meal=dinner` and a
  `recipe_path`). Steps stay in geo and veg servings are not recorded per food, so neither is a
  kind yet. A challenge runs Monday–Sunday, one per kind per week, with a target of 1–7 days
  (`FamilyChallenge`, `ChallengeMember`; pure rules in `services/health/challenges.py`). Only an
  adult starts one: the gateway asks Identity whether the caller's family role is a minor's
  (`X-User-Is-Minor`; Identity unreachable counts as a minor) and the health service also refuses
  a profile in kid mode or under 18; admins may always. Anyone joins or leaves; the starter (or an
  admin) can end it. Progress is computed on every read from members' own logs — a day with
  nothing logged is not counted — and a member with no goal of their own is measured against the
  guideline goal, labelled as such. No kind involves calories or weight. Routes: `GET/POST
  /challenges`, `POST/DELETE /challenges/{id}/join`, `DELETE /challenges/{id}` (gateway
  `/api/health/challenges…`). `FamilyChallengeCard` on Health → Today shows the standings, Join/
  Leave, the start form for adults, and **Share to chat** through the same `ShareToChatSheet` the
  recipes use (`🏆 Family challenge: …` with the ranked standings).
- [x] Cheers/kudos on a family member's day; share a health card to chat. **Cheers**
  (`services/health/cheers.py`, `Cheer`; `GET/POST /cheers`, gateway `/api/health/cheers`) come
  from a fixed set (👏 Well done, 💪 Keep going, ⭐ Proud of you, 😴 Rest well, 🥕 Good eating),
  never free text, so they are kind between any two members, children included. You may cheer
  someone who shares a health scope with you or is in one of this week's challenges with you; once
  per kind per person per day. The gateway sends the person one `health` notice ("Mom cheered your
  day"). A cheer reveals nothing about the day. `CheersCard` on Health → Today lists today's cheers
  and the buttons, and hides when there is nobody to cheer. **Share my day** on Today opens the
  same `ShareToChatSheet` the recipes and challenge use, with sleep, steps, water and energy from
  the cards the person has on (`src/lib/healthShare.ts`) — never calories, weight or cycle data,
  since a family chat includes the kids.

### Phase 8 — integrations everywhere

- [x] Dashboard widget (today card). **Health today** (`health_today`, `HealthTodayWidget`) shows
  readiness, last night, water, food (kid mode: what was logged, never calories), the week's
  active minutes and tonight's planned dinner from the week plan, each only when its card is
  switched on, from the same queries as the Health page; a tap opens the hub. It never reads
  steps, so the existing Health (`health_activity`) widget stays the dashboard's one step card
  (`dashboardHealthWidgets.test.ts` still enforces a single step reader). Registered in the UI and
  identity's `WIDGET_DEFAULT_SIZES`; not in `CHILD_WIDGETS`, so a child's starting board is
  unchanged and they can add it themselves.
- [ ] Android home-screen widget (today card). **Built 2026-10-24, not yet confirmed on a phone.**
  *Health today* (`HealthTodayWidget`, 3×2, resizable) shows four tiles —
  steps (geo, against the goal), readiness, last night's sleep score with its length, and water
  in the profile's units (fl oz for imperial, litres otherwise) — from the same reads the Today tab
  uses (`/api/geo/steps?days=1`, `/api/health/summary`), with the signed-in person's own key the
  way the other widgets read it (TokenBridge → `WidgetApi`). A missing reading prints a dash and
  why ("Check in", "Not logged", "None logged"), never a zero; one failed read leaves the other
  tiles intact. No calories, so a child's home screen never shows one. A tap opens Health
  (`/fitness`); the refresh button and the shared widget tick update it, and a Health Connect sync
  refreshes it. Captions are pure and JVM-tested (`HealthTodayFormat`, `HealthTodayFormatTest`).
  **Check on a real phone:** add *Health today* from the widget picker; signed out it says "Sign in
  to Jarvis OS"; signed in the four tiles match Health → Today (log water and press refresh: the
  water tile changes); at 3×2 and at the smallest resize nothing is cut off; a tap opens Health.
- [x] Recipes: plan ↔ log ↔ shopping list round trip; Calendar: workouts. **Log tonight's dinner**
  from the week plan now offers "Tick {day} cooked in the week plan" (on by default): after the
  log is saved, the plan note is re-read and that day ticked only when its dinner is still that
  recipe, so an unsaved edit is never written (`src/lib/mealPlanStore.ts`, now the one reader and
  writer of `Recipes/Meal plan.md` for Recipes, Health and the dashboard). **Build shopping list**
  leaves out dinners already cooked, so log → plan → list closes the loop. **Plan a workout** on the
  Active minutes card puts "🏃 Run · 30 min" at a chosen day and time on the person's calendar
  through the same add the Calendar page uses (`src/lib/workoutPlan.ts`).
- [x] Family hub entry point: the existing Health nav item now opens the hub.

### Phase 9 — tests, docs, deploy

- [x] Pure tests: BMR/budget maths, scores, portion scaling, density mapping and water.
- [x] Streaks, forgiving by design (the rule in `services/common/streaks.py`, which geo's strict
  `longest_goal_streak` and the Monday week helper now share; habits in `services/health/streaks.py`; `GET /streaks?day=`, gateway
  `/api/health/streaks`): **Showing up** (any food, water, sleep or check-in log), **Morning
  check-in**, **Water goal** (against the person's own goal only; no goal, no streak and the card
  says what to set) and **Sleep logged**. One missed day in any seven is a rest day that keeps a
  streak (it bridges, never counts); two misses in a week end it; today never breaks a streak while
  it is still today. The best run of the last 90 days is kept. `StreaksCard` on Health → Today
  never calls a day failed (`services/common/tests/test_streaks.py`, `StreaksCard.test.tsx`).
- [x] UI tests: log a meal (search + manual), day view, check-in, water and cycle opt-in/log/share. Challenge standings, join, start and share are tested (`FamilyChallengeCard.test.tsx`, `services/health/tests/test_challenges.py`, `services/gateway/tests/test_health_challenges_gateway.py`).
- [x] Kid-mode UI hiding: with kid mode on, Today's food card and the Nutrition day show what was
  logged (and the macros) but no calorie number, range, progress bar, per-food calories or density
  colour; the profile hides weight and the "lose" goal; fasting is off; a kid-mode viewer of a
  shared food day gets "food logged" without a number. Tested in `src/test/HealthHub.test.tsx`.
- [x] **Kid mode is forced on for every minor** (decided 2026-10-24). The gateway sends
  `X-User-Is-Minor` (the family role, from Identity; cached five minutes, an outage is not cached)
  on every health call; a `true` stores `kid_mode` on the profile and the profile reports
  `kid_mode_locked`, as does a birth year under 18. The profile's switch shows on and disabled
  ("Always on for anyone under 18."); a save cannot turn it off. `unknown` never changes a saved
  choice. Tests: `services/health/tests/test_kid_mode_forced.py`,
  `services/gateway/tests/test_health_challenges_gateway.py`, `HealthProfilePanel.test.tsx`.
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
- [x] **Music everywhere, not just Jarvis.** Baseline: everything played through the house
      media stack (local library and `spotify://` through Music Assistant). If possible:
      each member links their Spotify account (Web API `recently-played`, opt-in) so
      listening in the Spotify app itself lands on the profile; every other music app is
      covered by the phone's media-session reader (title, artist, app package).
      **Spotify linking built (2026-10-24, needs the household's Spotify app):** each member
      links their own account, opt-in, from Accounts & Integrations (`/identity`,
      `SpotifyLinkPanel`). The gateway runs the OAuth (`POST /api/spotify/link` gives the
      authorize address with a state signed by the internal secret, ten minutes valid;
      `GET /api/spotify/callback` exchanges the code, scope `user-read-recently-played` only);
      identity keeps the tokens encrypted (`LinkedAccount`; the member sees only linked/name/last
      sync/last error, `DELETE` unlinks and deletes the tokens). Every ten minutes telemetry
      (`spotify_sync.py`) refreshes a token about to run out, reads recently played after the
      last copied play and records each as a `media` play (`source: spotify`, artist, album), so
      the profile, recap, music suggestions and content monitoring read it like any media
      session; a child's feed stays parents-only. A failure is kept on that member's link and
      shown to them; it never stops the others. **Not configured** until an admin registers an
      app at developer.spotify.com and fills `spotify_client_id`, `spotify_client_secret` and
      `spotify_redirect_uri` (the public `…/api/spotify/callback`, registered with the app); until
      then the panel says which settings are missing and nothing is read. The client secret is
      masked in settings reads from the UI, and OAuth codes are masked in request logs.
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
- [x] **Web activity.** The on-device filter sums sites visited and blocked attempts per
      domain and posts them every five minutes to the activity feed (`feature: web`, event
      `visit` or `blocked`, counts in `detail`); parents-only like the rest of the feed.

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
- [x] **App control.** Block apps, per-app daily limits, install approvals, per-permission
      gating; Accessibility-service enforcement on the device with Family Link's one-minute
      grace warning before a block lands.
      **Blocking and per-app limits built (2026-10-24, needs the APK after 437):** a parent
      switches an app off or gives it its own daily minutes from the apps the child's phone
      reported this week (`AppControls`, under the child's screen-time controls; rules
      `blocked_apps` / `app_limits` in identity's `ScreenRules`). `screen_time.apps_state` turns
      today's per-app foreground time into `blocked_apps` and `app_minutes_left` on the status;
      always-allowed apps are never blocked. The guard closes a blocked app (`GuardRules`
      `BLOCK_APP`) with "That app is off for now" while the rest of the phone works, and the
      child's card shows minutes left per limited app.
      **Install approvals, permission gating and the one-minute warning built (2026-10-24, needs
      the next APK and a device check):** one request store for "ask a parent first", identity's
      `GuardRequest` (`POST/GET /api/guard/requests`, `POST /api/guard/requests/{id}`; kinds `app`,
      `permission`, `site`; only the child's own parents answer, a request already waiting is
      returned without a second notice, an answered app or permission is not asked again). Both
      parents get a `family_safety` notice that opens the child's family member page
      (`/family/member/…`, docs/FAMILY_MEMBER_PAGE.md; the old `/guardian?child=…` redirects),
      where `RequestsCard` ("… is asking") approves or denies inline; the child hears the answer.
      *Install approvals* (rule `install_approval`, on unless a parent switches it off under the
      child's apps): the guard records the phone's user-installed apps the first time it runs on a
      protected phone (the baseline); an app installed later stays closed ("… needs a parent's
      OK", or "A parent said no") until a parent approves it, and the parents are asked once per
      app (`InstallApprovals`, JVM-tested; the guard listens for installs and checks every minute;
      needs `QUERY_ALL_PACKAGES`, fine for a sideloaded app). *Per-permission gating* (rule
      `permission_gating`: location, camera, microphone, contacts, sms, phone, photos, calendar,
      nearby): when Android's "Allow … to …?" dialog asks for a gated kind that app has no
      approval for, the guard dismisses it (a denial) and asks the parents; once approved the
      child grants it normally (`PermissionGate`, JVM-tested). Without device-owner rights the
      guard cannot change a permission itself: a grant made from Settings → App info →
      Permissions is not caught, and the dialog is recognised by its English text. *One-minute
      warning*: the status now names the next lock (`next_lock_at`, `next_lock_reason`: bedtime,
      school, or the day's limit at today's pace); the guard checks the clock every ten seconds
      and, a minute before, shows a heads-up notice and a toast ("Bedtime in one minute…"), and
      the same for the app in front when it has one minute of its own left (`LockWarning`,
      JVM-tested). Bedtime and school now also lock on the phone's clock the moment they start,
      without waiting for the next status.
- [ ] **Web filtering.** On-device VPN/DNS filter with category lists, allow/block lists,
      safe-search enforcement, an "ask a parent" request flow, and Qustodio-style bypass
      blocks (unsupported browsers, split-screen, safe mode).
      **DNS filter built (2026-10-24, APK after 437, needs a device check):** a parent switches
      the filter on under the child (`WebFilterControls`; rules `web_filter` with `enabled`,
      `adult`, `safe_search`, `block`, `allow`). On the phone `WebFilterService` is a VPN that
      routes only its own DNS address (10.111.222.2), so every other packet leaves as usual and a
      fault can only stop names resolving. `WebFilterRules` decides each name: the allow list
      wins (open resolver 1.1.1.1), then the block list ("no such site"), then safe search
      (Google, YouTube, Bing and DuckDuckGo answered with their safe-search addresses), then
      everything else through Cloudflare for Families (1.1.1.3: adult and malware). The guard
      starts it once Android's one-time VPN prompt was accepted (Phone protection card → Web
      filter → Allow), covers the VPN's Disconnect dialog, and reports `filter_disabled` if it
      is revoked. DNS and packet framing are pure, JVM-tested classes (`DnsMessage`, `IpUdp`).
      Known gaps: a browser set to its own secure DNS (DoH) or Android's strict Private DNS
      bypasses a DNS filter; categories are Cloudflare's (no per-category choice).
      **"Ask a parent" built (2026-10-24, needs the next APK and a device check):** when the filter
      blocks a site — the parent's block list, or the family-safe resolver's 0.0.0.0 answer, now
      also counted as `blocked` in the web activity — the phone shows "… is blocked" with an "Ask
      a parent" button (once per site every half hour; an ordinary Jarvis notice whose button is
      the push callback `guard_ask`, the same path as the route). A child can also ask from Health → Today (`AskForSiteCard`, shown when their
      phone filters websites; it lists only their own waiting requests). A parent allows the site
      for an hour (`web_filter.allow_until`, `{domain: ISO end}`, pruned when it runs out) or
      always (moved from the block list to the allow list), or says no. The status carries
      `web_allow` (the allow list in force now) and the phone checks the end time itself, so an
      hour ends on time offline (`WebFilterRules.activeAllow`, JVM-tested). Still open:
      Qustodio-style bypass blocks (unsupported browsers, split-screen, safe mode).
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
      then a ten-minute wait, 10-minute window, both parents told; the window also lifts any
      screen-time lock). Each covered uninstall attempt is reported as `uninstall_attempt`. The
      same service enforces
      screen time (4b): while `locked` it sends the child home except for always-allowed apps,
      with the reason and an "Ask for more time" button. Switching device admin or the guard off
      reports to `POST /api/guard/tamper` and both parents get a notice; `GuardWatch` reports a
      guard that was on and is now missing. Decisions live in pure `GuardRules` (JVM-tested).
      Setup is the `PhoneProtectionCard` on the child's Health page (device admin, then
      Accessibility; Android 13+ first needs App info ⋮ → Allow restricted settings for a
      sideloaded app). Needs APK 437 or later on the phone. **Offline parent code (2026-10-24,
      APK after 437):** a parent sets 6–12 digits (`PUT /api/guard/offline-code`, shown under the
      child's controls); identity keeps only a PBKDF2-SHA256 verifier (`GuardOfflineCode`), which
      reaches a child's phone only inside a successful online parent unlock (never through the
      child's own reads). With no connection the guard screen takes the code, checks it on the
      phone (`OfflineCode`, five wrong tries then ten minutes), lifts the guard for ten minutes
      and reports `offline_unlock` to both parents once the phone is online again. Safe mode and
      factory reset still get around Layer 1. Known gap to check on a device: pages
      are recognised by the text "Jarvis OS", and the guard's own Accessibility page is labelled
      "Jarvis screen guard", so it may not be covered on every phone (`GuardRules.APP_LABEL`).
      **Layer 2 (planned, optional per phone): device owner.** Set once over USB on a phone with
      no accounts signed in: `adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver`
      (sign out Google accounts first, sign back in after). Then the app can call
      `setUninstallBlocked` and the `DISALLOW_SAFE_BOOT` / `DISALLOW_FACTORY_RESET` restrictions,
      a hard block Android enforces itself.

### Watching (Bark parity)

- [x] **Content monitoring, local models only.** Analyze the collected streams (messages,
      calls, web, music, app usage) for what a parent needs to know — self-harm, bullying,
      isolation, secret-keeping patterns — classified by concern level with per-child
      sensitivity (all / severe / none); serious categories always on.
      **Built (2026-10-24), local and deterministic:** every activity event recorded for a minor
      is scanned as it lands (`services/common/concern_monitor.py`, run from telemetry's
      activity route): text messages sent and received, media titles, sites and page names,
      against a small reviewed phrase list (whole words, case-insensitive) in levels — severe
      (self-harm, sexual exploitation and grooming, threats), high (bullying, sexual content,
      drugs, secrecy) and medium (alcohol and vaping, gambling). Hits are kept for the recap
      (`tel:concerns:{child}`) with a short excerpt; the child's sensitivity
      (`monitoring` in their rules: `all`, `severe` (default), `off`) decides what pages both
      parents at once (`family_safety`, deduplicated per child, category and day; self-harm and
      exploitation always page, at priority 5). Adults are never scanned.
      **Local-model second pass built (2026-10-24):** each phrase-list hit is read in context by
      the household's own model before anyone is paged (`services/common/concern_review.py`,
      called from telemetry's `guardian.second_pass`): the model at `llm_local_url` named by the
      new setting `guardian_monitor_model` answers whether it is really a concern and how serious
      (JSON: concern, level, reason). A "no" keeps the hit on record marked `dismissed`, pages
      nobody and leaves it out of the recap; a "yes" may change its level, and its reason rides
      along in the parents' notice and on the recap ("Checked by the local model: …"). Self-harm
      and exploitation are never silenced by a model: a doubt is recorded beside the hit and both
      parents are still paged. With no model configured, or when it is unreachable or answers
      nonsense, the phrase list decides exactly as before; the reason is logged and kept on the
      hit (`review: {by: "phrases", reason}`), nothing is dropped. Set `guardian_monitor_model`
      in Settings → AI & Compute to turn it on.
- [ ] **Alerts and reports.** Only the highest concern pages a parent immediately; a Sunday
      weekly recap (telemetry's report pipeline) with top apps, contacts, sites and standout
      moments; Jarvis chat answers "how was Emma's week?" the way Bark Assistant does.
      **Weekly recap built (2026-10-24):** `services/common/guardian_recap.py` builds a child's
      Monday–Sunday from what was reported — screen time (daily average, change from last week,
      late nights after 22:00 household time), top apps, what they played and watched, sites
      visited and blocked, how many calls and messages with how many people (never content),
      Bible days and the week's concerns, plus a few highlights. Telemetry sends it to both
      parents on Sunday from 18:00 household time (once per child and week, kept 12 weeks);
      `GET /api/guardian/recap/{child}` builds it on demand for the parents' page. Jarvis chat
      answers a parent's "how was Emma's week?" / "how is Sam doing?" from the same recap in
      plain words (`guardian_recap.asked_about` / `spoken`, only for the caller's own children,
      before any model is asked).

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
      and a parent can bless or block a lane: identity's `RecommendationLanes` keeps the
      blocked lanes (`GET/PUT /api/recommendation-lanes/{user}`, a minor's parents change them,
      the member reads theirs), each `/api/recommendations/*` route answers
      `{"recommendation": null, "blocked": true}` without reading data for a blocked lane, and
      the parent's switches sit under the child on Health → Today (`SuggestionLanesControls`,
      next to the screen-time controls).
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
- [x] **Prayer and guidance.** The parent view pairs the week's real activity with Scripture:
      prayer prompts grounded in what actually happened, conversation starters from the
      devotional library, and a talk journal. Jarvis drafts gentle notes a parent reviews
      before sending — never sent automatically.
      **Built (2026-10-24):** the parents' page, since replaced by the **family member page**
      `/family/member/{child}` (docs/FAMILY_MEMBER_PAGE.md; Health → Family guardian, concern
      and recap notices open it on the child; `/guardian` redirects). Its Faith & growth section
      shows one child at a time: the week (`RecapCard`),
      **Praying for …** (`services/common/guidance.py`: for each signal in the week — a concern,
      late nights, more phone time, blocked sites, Bible reading — a prayer, a KJV verse and a
      conversation starter, most important first; a quiet week gets a blessing), a **talk
      journal** (identity `GuardianNote`, the writer's own unless shared with the other parent;
      the child never sees it); the timeline and every control have their own sections. "Write … a note" turns a
      starter into an editable note the parent sends through the family chat themselves.
      The starters are reviewed text, not yet drawn from the devotional library.
- [x] **A voice for the quiet ones.** One-tap "I need to talk", burden signals (reusing
      check-ins) and private note lanes (mom↔dad, parent↔child) so nobody has to say it out
      loud first.
      **"I need to talk" built (2026-10-24):** a child's card on Health → Today picks one or both
      parents and an optional short note (`POST /api/guardian/talk`; only the caller's own
      parents can be reached); the parent gets a `family_safety` notice that opens the child's
      family member page. Private lanes are the family chat's direct conversations. **Burden signals:** when a
      child's own morning check-ins show low mood or energy (1–2 of 5) on three of the last
      five, both parents get one gentle notice that week ("… has had a few hard days"), never
      the answers (`services/common/burden.py`, checked when a check-in is saved).
- [ ] **Sharing follows the family roles (`docs/ROLES.md`).** Minor rules stay circle-on and
      locked; children never see parents' private sections; kid mode respected throughout.

**Build order (profile first):** (1) the digital profile store and the activity recorders
around it — DONE (trivia wins, media plays, app pages, Bible chapters, imported devotional/
study-book reads, external media sessions, general phone usage, per-app foreground time,
calls and SMS all land on the profile); (2) the Android collection engine — usage,
media sessions, calls and SMS DONE (video covered by media sessions + per-app foreground
time); (3) Jarvis recommendations from the profile — DONE (music style matching,
video themes redeemed or redirected, podcasts, books, games and activities, and a parent's
bless/block per lane); (4) time budgets and routines;
(5) app control and the web filter — app blocking, per-app limits, install approvals,
per-permission gating, the one-minute warning, the DNS web filter and "ask a parent" for a
blocked site DONE (needs the next APK and a device check);
(6) monitoring alerts and the weekly recap — DONE (local phrase monitoring with a local-model
second pass, Sunday recap);
(7) the parents' page and the discipleship surfaces — DONE (parents' page, prayer and
guidance, talk journal, "I need to talk", burden signals, the Jarvis chat recap); the parents'
page became the family member page (docs/FAMILY_MEMBER_PAGE.md): Aura's layout, every section,
the father's technical Devices section, and the mother's view without technical data enforced
by the server.

## Acceptance

- [x] Each family member has a profile and their own Health day.
- [x] A planned dinner logs with one tap and shows calories, macros and portions (Recipes → Week plan → Log tonight's dinner).
- [x] Barcode lookup works with a permanent per-product cache; photo (nutrition label) and voice logging produce editable drafts.
- [x] Sleep and readiness show from manual data now and are device-ready; check-ins cover the no-wearable case.
- [x] Cycle tracking is opt-in, private, shown only when the profile is female, and minors can share it only with their linked mother account. An adult can share with a spouse, who also sees phase-aware support suggestions clearly labelled as general ideas.
- [x] A family challenge is visible, joinable and shared in chat (Health → Today → Family challenge).
- [ ] A parent can open each child's profile and the shared parents' page; guidance and prayer
      prompts cite real activity, and every alert names its concern level.
- [x] Kid accounts never see calorie deficits or weight-loss framing.
