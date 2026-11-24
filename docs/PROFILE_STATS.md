# Profile stats

Two pages about one person across Jarvis: their **profile** (each person's,
seen by them and, area by area, by the family) and the **admin page** (the
same person through the household admin's eyes, to find what to fix in
Jarvis and to look after the family).

## Firm boundaries

- **End-to-end encrypted content is never on any page.** The parents'
  private chat and sealed secrets are not read, decrypted, counted or shown.
  `services/gateway/profile_stats.py` does not touch them, and a test pins
  that.
- **No new tracking for the profile page.** Every number comes from a stats
  endpoint a service already had. The admin page's request outcomes are the
  one new record (see below): counts and timings only, never what was asked
  or answered.
- **The admin page is admins only, decided on the server**
  (`GET /api/profile/admin/{username}` answers 403 to anyone else; the UI
  hiding it is not the protection).
- No household literals in code; tests use made-up people.

## Data sources

| Area | Where it comes from | Who else may see it |
|---|---|---|
| Bible | bible `/stats`, `/activity/summary`, `/activity/feed`, `/games/leaderboard` | identity activity sharing, scope `bible` (the switch the Bible pages already use) |
| Activity and Wander | geo `/activity/summary`, `/activity/feed` (steps, workouts, drive distance, points) | identity activity sharing, scopes `totals`, `workouts`, `achievements` |
| Health | health `/streaks`, `/weekly`; others via health `/family` | the health service's per-viewer shares (sleep, nutrition, fasting, cycle) |
| Garden | garden `/streaks` (care streaks, logs, badges) | profile sharing `garden` (new; garden had no switch) |
| Recipes | health `/recipes/stats` (times cooked, own stars) | profile sharing `recipes` (new) |
| Games | arcade player cards (both collections), Bible daily quiz | arcade scores: always (they are on the family scoreboard already); daily quiz: its leaderboard's rule |
| Jarvis use | telemetry `/api/telemetry/use/{user}` (top features) | profile sharing `jarvis_use` (new) |

Admin only, on top of the above with no opt-in:

| Section | Source |
|---|---|
| Devices and app versions, features used, things asked for that Jarvis cannot do, assistant conversations per day (counted, never read), app opens | identity `GET /api/internal/people/{username}/usage` |
| Collector and permission health | telemetry `/api/telemetry/device_health/{user}` |
| Feature use (30 days) | telemetry `/api/telemetry/activity/{user}/counts` |
| Screen time (14 days) | telemetry `/api/telemetry/usage/{user}` |
| Guardian (concerns, the weekly recap) | telemetry `/recap/{user}`, `/concerns/{user}` (the Guardian itself, reused) |
| Errors logged against the person | logging `/api/logs?user_id=` |
| Request outcomes and latency | gateway `voice_outcomes` (below) |

## The opt-in model

A person always sees all of their own. For anyone else each area is off
until its owner turns it on, and **each area has exactly one switch**:

- Areas that already had one keep it: Bible, activity and Wander use
  identity's activity sharing; health uses the health service's per-viewer
  shares. The profile's sharing panel shows their state and links to where
  they are set; it never adds a second switch for them.
- The areas with no switch of their own (garden, recipes, Jarvis use) are
  in identity's new `UserProfileSharing` table: `GET`/`PUT
  /api/users/me/profile-sharing` (`{"areas": [...]}`), read by the gateway
  through `GET /api/internal/profile-sharing?username=`. Private by default,
  part of the account export, removed with the account.

## Endpoints (gateway, `services/gateway/profile_stats.py`)

- `GET /api/profile/stats/{username}` (`me` for the caller): cards in page
  order, each `{area, state, data, reason?}` where `state` is `ok`, `empty`,
  `hidden` (not shared; `data` is null) or `unavailable` (its source did not
  answer). The caller's own page also carries `sharing`.
- `GET /api/profile/admin/{username}`: `{profile, flags, sections}`; each
  section is `{state: ok|unavailable, data|reason}`.
- `GET`/`PUT /api/profile/sharing`: the caller's choice plus the state of the
  existing switches.

The admin route lives under `/api/profile/` because Caddy sends `/api/admin*`
to the workspace runtime.

## Fan-out, time limits, caching

Every source is asked at once, each with its own limit
(`PROFILE_STATS_SOURCE_TIMEOUT_S`, default 4 s). A source that fails or is
slow makes only its card `unavailable`; the page always answers. Another
person's unshared areas are never fetched at all. Answers are cached per
(viewer, person): `PROFILE_STATS_CACHE_S` (60 s) and `ADMIN_STATS_CACHE_S`
(30 s); `?fresh=1` asks again. All three are read once at startup.

## Trend flags (admin)

Said once, in plain words: a Bible reading streak that stopped (best 3 days
or more), reading this week under half of last week's, sleep, mood or energy
down on last week (from the health weekly digest), screen time up 30% and 30
minutes a day on last week, and open capability gaps.

## The profile page (`/profile`, `/profile/{username}`)

In the nav as **Profile**. A strip of the family across the top (you first,
as "Me"), then one card per area. Every stat is a link, at least 44px tall and
reachable by keyboard, into the page that already does that thing; nothing is
a parallel screen:

| Stat | Opens |
|---|---|
| Bible reading streak, "pick up where you left off" | the reader at that chapter (`/bible?tab=read&ref=`) |
| Days and chapters read, badges, points | Bible progress (`/bible?tab=progress`) |
| Today's daily Bible quiz | the quiz (`/bible?tab=play`); someone else's score adds `beat`, `score` and `of`, and the quiz shows "Challenge: beat Robin's 8/10" and, once played, how it went |
| Steps, workouts, activity points | Health, Fitness (`/fitness?tab=fitness`) |
| Miles driven | Wander for that person (`/wander?person=`) |
| Health habits, sleep, meals | Health (`/fitness`, `?tab=sleep`, `?tab=nutrition`) |
| Garden streak, care, badges | the Garden (`/garden`) |
| A recipe cooked most | that recipe (`/recipes?recipe=`) |
| A Family Game score | that game on the family shelf (`/family?tab=games&play=`) |
| An arcade score | the games tab (`/family?tab=games`) |
| Something used most | that part of Jarvis (Music, Bible, Map...) |

On your own page, **What your family sees** has a switch for garden, recipes
and Jarvis use, and shows the state of the switches that already exist
(activity, Wander and Bible in Settings > Family; health in Health > Health)
with a link to each. On someone else's page, the areas they keep private are
named in one line under their cards.

## The admin page (Admin > People, `/admin/people/{username}`)

One person at a time, picked from a strip of the family. Admins only: the
gateway refuses anyone else, and the app shows a member its usual refusal
without asking for the stats. In order:

1. **Worth a look**: the trend flags, or "Nothing stands out".
2. **Requests to Jarvis**: how many, how many did not go through, the
   typical and slowest waits, failures by day, the tools that failed most and
   the slowest hours (from the request outcomes record; empty until it has
   data).
3. **Devices and app**: each device, its app version (amber when behind the
   person's newest), last seen, and the phone's own collection problems in
   plain words.
4. **What they use** (30 days, and features not used for a month),
   **Asked for, not there yet** (capability gaps), **Talking to Jarvis**
   (conversations counted per day, never read; app opens).
5. **Screen time** (14 days, with the average), **Guardian** (concerns on
   record, the recap's headline; links to the family member page), **Errors
   logged for them**.
6. **Across Jarvis**: the person's profile cards, every area, no opt-in.

Each section has its own loading, empty and unavailable state.

## Request outcomes (`services/gateway/voice_outcomes.py`)

The one record kept for these pages. For each request to Jarvis (a voice
satellite's or typed chat), per person and day: the outcome (`ok`, `timeout`,
`error`, `tool_failure`, or `offline`, when it fell back to the "the AI is
offline" answer), whether it waited in line before a worker took it
(`queued`, over `VOICE_OUTCOME_QUEUED_MS`, 3 s), its latency bucket, the hour,
and which single-turn tools ran and which failed. **Counts and timings only:
never the question, the answer or a tool's arguments.** A refused request
(400, 401, 403) and a job that was not a person's request (a Raven mission)
are not counted.

- **Off the request's path.** The handler only adds to an in-memory counter;
  a background task writes the counters to Redis every
  `VOICE_OUTCOME_FLUSH_S` (10 s) as one pipeline of `HINCRBY`. A failed write
  keeps the counters for the next try and never touches the request.
- **Bounded.** One hash per person and day, `outcomes:{user}:{day}`, kept
  `VOICE_OUTCOME_RETENTION_DAYS` (90) days.
- **How a request is followed.** `chat_handler` starts a record; a request
  answered in the handler is counted there (a stream when it ends); one handed
  to the job queue carries its record in the job and is counted by
  `complete_job` / `fail_job`, with the worker resuming it so its tool calls
  count. `_execute_single_tool` names each tool and whether it did the thing.
- **On the admin page:** totals, the share that did not go through, the
  typical and 90th-percentile wait (from the buckets), failures by day, the
  tools that failed most and the slowest hours (over `VOICE_OUTCOME_SLOW_MS`,
  8 s). A person whose requests fail `PROFILE_STATS_VOICE_FAILURE_FLAG` (10%)
  of the time or more, over at least ten requests, gets a flag.

All settings are read once at startup.
