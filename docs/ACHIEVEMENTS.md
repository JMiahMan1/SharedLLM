# Achievements, Goals, and Opt-In Activity Sharing

Status: **phases 1-3 and 5 shipped, 4 partly shipped** (see Progress).
Companion docs: `docs/HEALTH_STEPS.md` (data + fusion), `docs/GEO_SERVICE.md`
(endpoints), `docs/FAMILY_HUB.md` (chat surfaces and the card envelope).

## Principles

1. **Derived, not stored.** Achievements are computed from the same day buckets
   and workouts the app already records. Nothing to backfill or migrate, and a
   bug fix in the rules instantly corrects history.
2. **Data-driven definitions.** Achievement definitions live in a JSON
   document (like theme packs), so new badges/thresholds need no code change.
3. **Opt-in or invisible.** Activity is private by default. Nothing (totals,
   workouts, achievements) is visible to another user until the owner enables
   sharing, and then only to the audience they choose (circle / specific
   users).
4. **Two currencies, one ledger.** Achievements award **points**. Points are
   the bridge to Skylight stars so chores and fitness reward the same vault.

## Goal model

| Goal | Where | Notes |
|---|---|---|
| Daily steps | `geo:steps_goal:{user}` | default 10,000; edited on the Health page's steps card |
| Weekly steps | `geo:goals:{user}` hash | default 70,000 |
| Workouts / week | `geo:goals:{user}` hash | default 4 |
| Distance / week | `geo:goals:{user}` hash | default 15 miles |

`GET/PUT /api/geo/goals` returns/sets all of them at once. Defaults and bounds
are `DEFAULT_GOALS` / `GOAL_BOUNDS` in `services/geo/achievements.py`.

## Achievement definitions (data)

`services/geo/achievements.json` (validated on load, served for the UI). An
excerpt; the file defines 19 achievements:

```json
{
  "schemaVersion": 1,
  "kind": "jarvis.achievements",
  "achievements": [
    { "id": "first_steps",   "name": "First Steps",     "points": 1,  "rule": {"type": "steps_any",        "value": 1} },
    { "id": "goal_1",        "name": "Goal Day",        "points": 3,  "rule": {"type": "goal_days",        "value": 1} },
    { "id": "goal_streak_7", "name": "Week of Wins",    "points": 20, "rule": {"type": "goal_streak",      "value": 7} },
    { "id": "goal_streak_30","name": "Unstoppable",     "points": 75, "rule": {"type": "goal_streak",      "value": 30} },
    { "id": "steps_10k_day", "name": "Ten Thousand",    "points": 5,  "rule": {"type": "steps_in_day",     "value": 10000} },
    { "id": "week_50k",      "name": "Fifty-K Week",    "points": 20, "rule": {"type": "steps_in_week",    "value": 50000} },
    { "id": "workout_1",     "name": "First Workout",   "points": 5,  "rule": {"type": "workouts_total",   "value": 1} },
    { "id": "workout_week_4","name": "Four This Week",  "points": 15, "rule": {"type": "workouts_in_week", "value": 4} },
    { "id": "distance_100",  "name": "Century",         "points": 50, "rule": {"type": "distance_miles",   "value": 100} },
    { "id": "comeback",      "name": "Back At It",      "points": 10, "rule": {"type": "goal_after_gap",   "value": 3} }
  ]
}
```

Rules are computed in `services/geo/achievements.py` from:
`daily_steps` buckets, `workouts`, and the goal settings. Each unlocked
achievement carries the date it was earned (`earned_on`) so it is stable.

## Endpoints (geo, proxied by the gateway)

| Endpoint | Purpose |
|---|---|
| `GET /api/geo/achievements?user_id=&days=30` | unlocked + next-up with progress (`4/7 days`), points and goals (`days` 1-120) |
| `GET /api/geo/goals` / `PUT /api/geo/goals` | the goal set above |
| `GET /api/geo/points` | points ledger total + recent awards |
| `GET /api/geo/activity/summary?user_id=&window=today\|week\|month\|all` | running totals (steps, distance, workouts, points) |
| `GET/PUT /api/users/me/activity-sharing` | opt-in state for the caller: `{enabled, audience: circle\|users, user_ids[], share: [totals, workouts, achievements]}` |
| `GET /api/geo/activity/feed?window=` | opt-in activity of others you may see, scope-projected |

Server-side enforcement: the identity sharing rows are authoritative.
`/activity/summary` for another user and `/activity/feed` only return data the
target opted into. A single-user (non-circle) audience requires the viewer in
`user_ids`; a caller reading someone else's summary without access gets
**404**, and the feed simply omits them (never errors). Each projected entry
contains only the declared `share` scopes: `totals` → steps/distance tiles,
`workouts` → workout count + recent list, `achievements` → points + badge
count. Sharing state lives in Identity (`UserActivitySharing`, mirroring
`UserThemeSetting`); geo reads it over `GET /api/internal/activity-sharing`.

## Chat / @Jarvis integration

What is built:

- **Achievement cards.** When a badge is first earned,
  `achievements.announce_awards()` posts an `activity` card (the typed chat
  envelope, `docs/FAMILY_HUB.md`) to the room named by `FAMILY_CHAT_TOKEN`.
  Other Talk clients see plain text.
- **Reactions** on cards and messages use Talk's own reactions API, so no
  separate reaction message type was needed.
- `@Jarvis` questions in chat are answered by the general assistant.

Not built: a dedicated `@Jarvis steps` / `@Jarvis steps @sam` command that
answers from the caller's (or a consenting user's) activity summary, and
encouragements that reference a card id so Jarvis can summarise them.

## Skylight bonuses

Skylight already models chores, stars and a reward vault
(`/api/integrations/skylight/*`, per-user credentials in Identity).

As built (see "Bonus stars" below): stars are granted through geo's ledger
(`POST /api/geo/stars`, reasons `bonus | achievement | game | chore | manual`)
and mirrored to Skylight through execution
(`POST /api/integrations/skylight/stars`). Everything is recorded in the
ledger first, so a Skylight outage cannot lose an award. Creating Skylight
chores and rewards from Jarvis is not built.

## Progress

- **Phase 1, goals + achievements (server): SHIPPED.** Rules engine
  (`services/geo/achievements.py`), data-driven definitions
  (`services/geo/achievements.json`), endpoints `GET /goals`, `PUT /goals`,
  `GET /achievements`, `GET /points` (gateway proxies under `/api/geo/*`),
  points ledger in `geo:points:{user}`, tests in
  `services/geo/tests/test_achievements.py`.
- **Phase 2, opt-in activity sharing (server): SHIPPED.**
  Per-user sharing state (`GET/PUT /api/users/me/activity-sharing`,
  `UserActivitySharing` table, defaults fully private), audience enforcement in
  geo (`circle` = everyone who opted in, `users` = explicit list), scope
  projection so only the shared slices travel, `GET /activity/summary` for
  consented cross-user reads (404 otherwise) and `GET /activity/feed` for the
  friend surface. Tests: `services/identity/tests/test_activity_sharing.py`,
  `services/geo/tests/test_activity_feed.py`.
- **Phase 3, UI: SHIPPED.** `AchievementsPanel` on the Health page
  shows points, earned badges, next-up progress, the star balance and an
  inline weekly/workout goal editor
  (`src/components/wander/AchievementsPanel.tsx`,
  `src/test/AchievementsPanel.test.tsx`). The daily goal is edited on the steps
  card. `ActivitySharingPanel` in Settings controls the opt-in state
  (audience picker, scope checkboxes), and the Health page's Family Activity
  card (`components/health/FamilyActivityCard.tsx`) shows the running totals
  and the feed.
- **Phase 4, chat: PARTLY SHIPPED.** Typed envelope and achievement cards
  (`announce_awards`, `docs/FAMILY_HUB.md`); the `@Jarvis steps` command is not
  built (see above).
- **Phase 5, stars: SHIPPED** (below).

Awards are banked on first read into `geo:points:{user}`, so a badge keeps the
date it was first earned even as the window rolls forward.

## Phases

1. **Goals + achievements (server)**: rules engine, definitions JSON, endpoints,
   tests. No UI changes; existing steps data drives it.
2. **Sharing (server)**: `UserActivitySharing`, summary/feed endpoints, strict
   opt-in enforcement, tests.
3. **UI**: achievements panel, goal editor, "Share my activity" in
   Settings (audience + scope), running-total cards.
4. **Chat**: typed envelope, `@Jarvis steps[@user]`, achievement cards,
   encouragement references; renderer updates on mobile + desktop.
5. **Skylight**: bonus endpoint (admin + achievement), star mirroring, chores
   page wiring.

## Bonus stars (shipped)

- `GET /api/geo/stars`: balance plus the ledger of who granted what, when and
  why. `POST /api/geo/stars` grants or deducts (-100 to 100, non-zero, reason
  from `bonus | achievement | game | chore | manual`); you cannot remove more
  stars than someone has. Grants are recorded in Jarvis first, so a Skylight
  outage can never lose one the family was already told about.
- Gateway proxies both. `StarBalance` (`components/wander/StarBalance.tsx`)
  shows the balance in the Health page's Achievements panel; admins grant from
  the **Bonus Stars** section of the Admin page.
- Skylight write-through (`POST /api/integrations/skylight/stars`) posts through
  the private frame API **at a configured path**, `skylight_stars_path`. There is
  no default: an unset path returns a clear failure naming the setting, because
  posting to a guessed path would look like a grant that never landed.

## Skylight chore completions (contract): timed instances need `instance_time` (2026-10-05)

Cross-checked against two community references, `joshuaswarren/pyskylight`
(`client.py`) and `chrischall/skylight-mcp` (`src/tools/chores.ts`), then
verified live from inside the execution container.

**Contract** (both references agree):

- `PUT /api/frames/{frame}/chores/{series}/completions`: the path takes the
  numeric series id, not the instance id.
- Body: `{status: "complete" | "pending", instance_date: "YYYY-MM-DD", instance_time: "HH:MM"}`.
  `status` is `complete`/`pending` (not `completed`).
- `instance_date` is **date-only**. For a time-of-day routine (recurrence set
  with `BYHOUR`, e.g. `RRULE:FREQ=DAILY;INTERVAL=1;BYHOUR=20`) the time must
  go in its own `instance_time` field; omitting it fails with
  **422 `{"errors":{"instance_time":["can't be blank"]}}`**.
- `category_id` must be omitted for a normally-assigned chore (422 "must be
  blank" otherwise); it is only for up-for-grabs chores.
- Our bodies also carry an extra `id` field (the two references omit it); the
  API tolerates it (live 200) and it is kept deliberately.

**Instance id shapes** returned by `GET /chores?after=&before=`:

- plain occurrence: `{series}-{YYYY}-{MM}-{DD}` (e.g. `52409380-2026-10-04`)
- timed routine occurrence: `{series}-{YYYY}-{MM}-{DD}-{HHMM}` (e.g.
  `46464093-2026-10-05-2000`, "Brush Teeth" at 20:00)

**The bug this replaces:** `_skylight_chore_ids` split the id naively and
returned `instance_date="2026-10-05-2000"` with no `instance_time`. Live probe
2026-10-05 (pending→pending no-op, state verified unchanged afterwards):

- old body → 422 `instance_time can't be blank` → execution maps non-2xx to
  `None` → route returns `{"status":"FAILURE","message":"Failed to uncomplete chore"}`
- corrected body (`instance_date="2026-10-05"`, `instance_time="20:00"`) →
  **200 with the updated chore JSON**

Implementation: `_skylight_chore_ids` in `services/execution/main.py` now
returns `(series, date, "HH:MM" | None)` via a strict regex; the complete and
uncomplete routes add `instance_time` only when the id carried one. Exact-body
tests live in `services/execution/tests/test_skylight_proxy.py`.
