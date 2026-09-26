# Achievements, Goals, and Opt-In Activity Sharing

Status: **design + phased plan** (server side first). Companion docs:
`docs/HEALTH_STEPS.md` (data + fusion), `docs/GEO_SERVICE.md` (endpoints),
`docs/jarvis_os_2_ui_wireframes.md` (chat/chores surfaces).

## Principles

1. **Derived, not stored.** Achievements are computed from the same day buckets
   and workouts the app already records. Nothing to backfill or migrate, and a
   bug fix in the rules instantly corrects history.
2. **Data-driven definitions.** Achievement definitions live in a JSON
   document (like theme packs), so new badges/thresholds do not need a deploy.
3. **Opt-in or invisible.** Activity is private by default. Nothing — totals,
   workouts, achievements — is visible to another user until the owner enables
   sharing, and then only to the audience they choose (circle / specific
   users).
4. **Two currencies, one ledger.** Achievements award **points**. Points are
   the bridge to Skylight stars so chores and fitness reward the same vault.

## Goal model

| Goal | Where | Notes |
|---|---|---|
| Daily steps | `geo:steps_goal:{user}` (exists) | edited inline on Wander |
| Weekly steps | `geo:goals:{user}` hash | default 70k |
| Workouts / week | `geo:goals:{user}` hash | default 4 |
| Distance / week | `geo:goals:{user}` hash | optional |

`GET/PUT /api/geo/goals` returns/sets all of them at once.

## Achievement definitions (data)

`services/geo/achievements.json` (validated on load, served for the UI):

```json
{
  "schemaVersion": 1,
  "kind": "jarvis.achievements",
  "achievements": [
    { "id": "first_steps",   "name": "First Steps",     "points": 1,  "rule": {"type": "steps_any",        "value": 1} },
    { "id": "goal_1",        "name": "Goal Day",        "points": 3,  "rule": {"type": "goal_days",        "value": 1} },
    { "id": "goal_streak_7", "name": "Week of Wins",    "points": 15, "rule": {"type": "goal_streak",      "value": 7} },
    { "id": "goal_streak_30","name": "Unstoppable",     "points": 60, "rule": {"type": "goal_streak",      "value": 30} },
    { "id": "steps_10k_day", "name": "Ten Thousand",    "points": 5,  "rule": {"type": "steps_in_day",     "value": 10000} },
    { "id": "week_50k",      "name": "Fifty-K Week",    "points": 20, "rule": {"type": "steps_in_week",    "value": 50000} },
    { "id": "workout_1",     "name": "First Workout",   "points": 5,  "rule": {"type": "workouts_total",   "value": 1} },
    { "id": "workout_25",    "name": "Regular",         "points": 25, "rule": {"type": "workouts_total",   "value": 25} },
    { "id": "distance_100",  "name": "Century",         "points": 40, "rule": {"type": "distance_miles",   "value": 100} },
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
| `GET /api/geo/achievements?user_id=` | unlocked + next-up with progress (`4/7 days`) |
| `GET /api/geo/goals` / `PUT /api/geo/goals` | the goal set above |
| `GET /api/geo/points` | points ledger total + recent awards |
| `GET /api/geo/activity/summary?user_id=&window=today\|week\|month\|all` | running totals (steps, distance, workouts, points) |
| `PUT /api/users/me/activity-sharing` | opt-in: `{enabled, audience: circle\|users, user_ids[], share: [totals, workouts, achievements]}` |
| `GET /api/geo/activity/feed?audience=` | opt-in activity of others you may see |

Server-side enforcement: `/activity/feed` and any cross-user reads consult
`activity-sharing` and return **404/empty** for users who have not opted in.
Sharing state lives in Identity (`UserActivitySharing`, mirroring
`UserThemeSetting`).

## Chat / @Jarvis integration

The existing `@Jarvis` mention flow becomes the delivery surface:

- **`@Jarvis steps`** (or `my week`) — Jarvis replies in the room with the
  caller's own summary. Always allowed (it is your own data).
- **`@Jarvis steps @sam`** — only works if `sam` opted in and the caller is in
  their audience; otherwise Jarvis answers "Sam doesn't share activity."
- **Achievement cards** — when an achievement unlocks, an `activity` message is
  posted to the opt-in feed/room. It is a typed chat message, so the chat
  client can render a card and others can reply with ordinary messages
  (encouragements). A lightweight `reaction` message type lets the card show
  a count without needing a full reactions backend yet.
- **Encouragements** — plain messages that reference `card_id`; Jarvis can
  summarise them ("Sam and 2 others cheered your streak") on request.

Chat rework slice 1 (prerequisite): typed message envelope
(`{type: text|activity|system, body, meta}`) with the renderer falling back to
text, so old clients keep working.

## Skylight bonuses

Skylight already models chores, stars and a reward vault
(`/api/integrations/skylight/*`, per-user credentials in Identity).

- **Achievement → stars**: points convert to Skylight stars at a fixed rate
  (default 1:1) through `POST /api/integrations/skylight/bonus`
  `{user_id, stars, reason, source: achievement|admin}`.
- **Admin grants**: the same endpoint with `source: admin` (admin-only), so
  bonus stars can be given for anything — a completed week, a kind act, a
  rough day.
- **Gap to close**: the gateway currently exposes only chores read/complete and
  rewards read/redeem; creating chores/rewards and granting stars must be
  added in the execution Skylight client, with double-auth fixed (see
  `docs/code_review_2026-08-22.md` E27) while we are in there.
- Everything is recorded in the points ledger first, then mirrored to
  Skylight, so a Skylight outage cannot lose the award.

## Phases

1. **Goals + achievements (server)**: rules engine, definitions JSON, endpoints,
   tests. No UI changes; existing steps data drives it.
2. **Sharing (server)**: `UserActivitySharing`, summary/feed endpoints, strict
   opt-in enforcement, tests.
3. **UI**: achievements row on Wander, goal editor, "Share my activity" in
   Settings (audience + scope), running-total cards.
4. **Chat**: typed envelope, `@Jarvis steps[@user]`, achievement cards,
   encouragement references; renderer updates on mobile + desktop.
5. **Skylight**: bonus endpoint (admin + achievement), star mirroring, chores
   page wiring.
