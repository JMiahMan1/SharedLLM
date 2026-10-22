# Health Hub: the best of the health apps in one

Status: **planned — nothing built yet.** Last live app commit `152451cf`.
Companion plans: `docs/RECIPES.md` (Phases 10–11 carry the recipe-nutrition and
calorie-budget work that this hub consumes), `docs/HEALTH_STEPS.md` (step counting,
already live).

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

## Phases

### Phase 0 — decisions

- [ ] Confirm profiles: one per family member, ages/heights/weights optional, kids vs adults.
- [ ] Decide data sources: Health Connect on Android (plugin or native bridge), Google
      Fit, Apple Health later; manual logging always works.
- [ ] Decide the first slice: activity + sleep + readiness, or nutrition logging first.

### Phase 1 — people and profiles

- [ ] Health profile per user (age, height, sex, activity level, goal, units),
      stored like other preferences; kid mode flags.
- [ ] Health settings page: turn metrics on/off, family sharing toggles.

### Phase 2 — data in

- [ ] Health Connect import on Android (steps already sync; add sleep sessions,
      heart rate/HRV, weight, workouts) with a native bridge.
- [ ] Manual logs for everything (sleep hours/quality, weight, workout, water).
- [ ] A health store the gateway can read per user (mirroring the steps store).

### Phase 3 — scores and targets

- [ ] Sleep score from duration + interruptions (or manual quality) — simple, honest.
- [ ] Readiness from HRV/resting HR/sleep/recent activity when available, else a
      two-tap morning check-in.
- [ ] Weekly cardio target and Active-Zone-like minutes from step/heart-rate data.

### Phase 4 — nutrition logging

- [ ] Recipe-first logging: a cooked planned meal logs its per-serving macros with a
      portion picker (this is recipes Phase 11's logging, surfaced here).
- [ ] Quick add: search the imported recipe set, barcode lookup (OpenFoodFacts),
      voice logging via the assistant, photo logging via Alpaca (recipes Phase 9).
- [ ] Water tracking; fasting window indicator (opt-in).

### Phase 5 — budget and day view

- [ ] Calorie range for adults (Mifflin-St Jeor with optional gentle deficit);
      guidance ranges for kids — never deficits.
- [ ] Day view: eaten vs range, protein/carbs/fat, per-meal breakdown, density colours.
- [ ] Weekly digest card: averages, best day, what changed.

### Phase 6 — habits and coaching

- [ ] Micro-lessons: a small library (nutrition basics, sleep, movement, stress) shown
      one card at a time; Jarvis can also write a lesson from the day's data.
- [ ] Chat coach grounded in the user's health data; gentle weekly goal setting;
      reminders through the timer subsystem (bedtime, water, movement breaks).

### Phase 7 — family

- [ ] Weekly family challenge (steps, veg servings, cook nights) with a leaderboard.
- [ ] Cheers/kudos on a family member's day; share a health card to chat.

### Phase 8 — integrations everywhere

- [ ] Dashboard widget and Android home-screen widget (today card).
- [ ] Recipes: plan ↔ log ↔ shopping list round trip; Calendar: workouts.
- [ ] Family hub entry point.

### Phase 9 — tests, docs, deploy

- [ ] Pure tests: BMR/budget maths, scores, portion scaling, density mapping, streaks
      that never shame.
- [ ] UI tests: log a meal, day view, challenge join, kid mode hides budgets.
- [ ] `npm run typecheck`, `npm run lint`, full suites, `npm run build`; docs; deploy
      `execution gateway ui` (health/identity as needed); verify live.

## Acceptance

- [ ] Each family member has a profile and their own Health day.
- [ ] A planned dinner logs with one tap and shows calories, macros and portions.
- [ ] Barcode, photo and voice logging all work with the local stack (Alpaca vision,
      OpenFoodFacts, the assistant).
- [ ] Sleep and readiness show from phone/watch data when available; check-ins cover
      the rest.
- [ ] A family challenge is visible, joinable and shared in chat.
- [ ] Kid accounts never see calorie deficits or weight-loss framing.
