# Family member page

One page per child that brings everything Jarvis knows and every control a
parent has into one place, the way the commercial family-safety apps do. It
replaces the old single-column `/guardian` page (its cards are reused, not
copied); the guarding side now has its own top-level `/guardian` page.

## What we modelled it on (researched 2026-10-24)

| App | Rating (Security.org 2026) | What its parent view does best |
|---|---|---|
| **Aura** | **9.6 / 10, ranked #1** | One dashboard with every family member at a glance; each child's profile has a **Pause** button at the top, **Bonus Time** as a reward, daily limit plus per-app and per-category limits, Bedtime / Focus Time / custom Schedules, screen time broken down by app and website, safe-zone location alerts |
| Bark | 9.5, #2 | Alerts with the triggering context, a severity and talking points; weekly summary of top apps, contacts and trends |
| Qustodio | 9.3, #3 | A minute-by-minute **activity timeline** (sites, apps, searches, YouTube), 7/15/30-day averages, weekday vs weekend rules |
| Google Family Link | free baseline | A child profile with a **Controls** tab: daily limit by weekly schedule, bedtime, app limits, app-install approvals, device location, remote lock |

We follow **Aura**, the highest rated, for the structure: members at a glance,
then one profile per child with the urgent actions on top and controls one
tap away. Two things Aura is weaker at are taken from the next in line:
Bark's concern cards (context + severity + what to say) and Qustodio's
timeline.

Sources: security.org/parental-controls/best, aura.com/parents,
support.google.com/families, techadvisor.com Qustodio review.

## Who sees what

The server decides; the page only hides what the server already refuses.

- **Which members:** identity's `may_view_raw_data` rule. A minor's page is
  open to an admin, the father and the linked mother (any mother when none is
  linked). The father and the mother also share their own collected data with
  each other, so each sees the other's page: read-only (`can_manage` is false,
  so no pause, bonus, lock or rules), with the technical side for the father
  only, as for a child. Any other adult's collected data stays their own.
- **Father / admin:** every section, including **Devices** (technical data).
- **Mother:** every personal section (time, apps, web, activity, location,
  health, faith, journal, all controls) but **no technical data**: no Devices
  section, and the server strips technical fields everywhere else (device
  ids, package names, app versions, IP/network, permission and service
  states, sync timestamps, tokens).

## Layout

Desktop: the member strip on top, a left rail of sections, a two-column card
grid. Phone: the member strip scrolls sideways, sections are a sticky
horizontally scrolling tab bar, one column. Every chart has a text summary and
a loading, empty and error state; nothing is invented when data is missing.

1. **Member strip (at a glance):** avatar, name, a status dot (in use,
   locked, paused, bedtime, offline) and today's screen time against the
   limit as a small ring.
2. **Profile header:** name, age and role; what they are doing now (device,
   app, for how long) and where (place name), last seen; quick actions:
   **Pause / Resume**, **Bonus time**, **Lock now**, **Talk**.
3. **Overview:** today's screen time ring vs the limit; a 7-day bar chart with
   the daily limit line and the 7-day average; top apps today; open requests
   (more time, app installs, websites) with approve/deny inline; concerns
   (severity, context, what to say); location mini-map; last night's sleep;
   the next routine ("Bedtime in 2 h 10 min").
4. **Time:** weekly daily-limit schedule (Family Link style, a row per day),
   Bedtime / Focus / School routines, per-app and per-category limits, bonus
   time, an hour × day usage heatmap for the last 14 days, request history.
5. **Apps & web:** apps with today's and the week's time, category and state
   (allowed, limited, blocked, awaiting approval); install approvals; web
   filter state, blocked categories, the family's own lists, recent web
   activity and "ask a parent" requests.
6. **Activity:** the timeline (media, Bible, games, calls and texts, web, app
   sessions), filterable by kind, one day at a time, with 7/15/30-day totals.
7. **Location:** map with the current position and family places, today's
   trips, place arrivals/departures.
8. **Health & wellbeing:** sleep (last 14 nights), steps, check-ins, digital
   wellbeing, under the health sharing rules.
9. **Faith & growth:** the weekly recap, prayer and guidance, the talk journal,
   suggestion lanes and recommendations.
10. **Devices (father/admin only):** every device of the child: model, OS, app
    version and build, last seen, battery, guard state (device admin,
    Accessibility guard, web filter VPN, usage access, notification access,
    Health Connect), permissions, last sync per feed, tamper events, offline
    unlock code set or not.

## As built (2026-10-24)

**Route:** `/family/member/{child}` (`pages/FamilyMember.tsx`), with
`?section=time|apps|activity|location|health|faith|devices`. `/family/member`
opens the first child the viewer may see; `/guardian?child=…` (older notices)
opens that child's page. Concern, recap, request, tamper and "I need to talk"
notices open the child's page. `/guardian` is now its own top-level page (a nav
entry after Family, offered to every role); Health → Family guardian links to
it, and it links on here.

### Who sees what, as enforced

| Viewer of a child | Page | Technical side | Devices endpoint |
|---|---|---|---|
| Father, admin | yes | yes | 200 |
| Linked mother (any mother when none is linked) | yes | stripped | 403 |
| Sibling, other adult | 403 on every data route | | 403 |

| Viewer of a parent | Page | Technical side | Devices endpoint |
|---|---|---|---|
| The father, viewing the mother | yes, read-only | yes | 200 |
| The mother, viewing the father | yes, read-only | stripped | 403 |
| The child themselves | 403 on raw data (`may_view_raw_data`) | | 403 |

- Identity: `may_view_technical_data(viewer, subject)` next to
  `may_view_raw_data` (raw-data access, and admin or father; a member's own is
  theirs). `GET /api/internal/may-view-raw` returns `{allowed, technical}`;
  `GET /api/screen-time/{u}/rules` returns `technical` for the caller.
- Gateway: one `_raw_data_access` check for `/api/usage/{u}` and
  `/api/guardian/recap|concerns|activity/{u}` (and guidance), then
  `member_page.strip_technical` for anyone without the technical side. The
  stripped keys (any depth) are device ids and keys, model, manufacturer,
  OS and app versions and builds, capabilities, IP and network, guard,
  permission and approval states (`protected`, `app_approvals`,
  `permission_approvals`), sync sources and timestamps, tokens, and the model
  that reviewed a concern. `/api/screen-time/{u}/status` strips the same and
  says `technical` so the page knows whether to offer Devices.
- `GET /api/family/member/{u}/devices` (identity `GET /api/family/devices/{u}`)
  serves the Devices section to the father and admins only, 403 otherwise.
- **Package names stay** for the mother: every app control (block, minutes,
  approve an install) is keyed by one. The page shows app names only.

### Sections and their sources

| Section | Shows | From |
|---|---|---|
| Member strip | avatar, state dot and word (on the phone, not on the phone, locked, paused, bedtime, no report today), today's ring | `/api/screen-time/{u}/status`, `/api/usage/{u}` |
| Header | name, role, now (lock reason or last app in use, last use), where (family place or "away", fix age), Pause/Resume, Bonus (+15/30/60), Lock now (30 min, 1 h, 2 h), Talk (family chat) | status, usage, `/api/users/location/all`, `/api/geo/zones`, `pause`/`bonus` |
| Overview | today ring vs limit with time left, bonus and the next routine; open requests (more time, apps, permissions, sites) answered inline; 7-day bars with limit line, average and over-limit days; concerns (severity word and icon, context, what to say from the week's guidance); top apps today; mini-map; last night's sleep | status, usage, `/api/screen-time/requests`, `/api/guard/requests`, `/api/guardian/concerns` and `/guidance`, `/api/health/family` |
| Time | weekly schedule (a row per day: own limit or the everyday/weekend one it falls back to, school day), bedtime and school routines; 14-day hour × day heatmap; answered time requests; the viewer's offline unlock code | rules (`day_limits` is new), `/api/guardian/activity`, requests, `/api/guard/offline-code` |
| Apps & web | open app/permission/site requests; each app with today's and the week's time and state (allowed, limited, blocked, awaiting approval), install approvals and permission gating; web filter, lists; recent websites (visited, blocked); answered requests | status rules, usage, guard requests, activity (`web`) |
| Activity | timeline one day at a time, filter by media, Bible, games, calls & texts, web, app; totals over 7/15/30 days | `/api/guardian/activity` (last 200 events, said when the record is shorter than the window) |
| Location | map centred on the child, today's trips as departures and arrivals, family places | locations, `/api/geo/trips?user_id=`, zones |
| Health | sleep for 14 nights (bars, average), steps for 14 days (bars, goal, average), today's phone habits | `/api/health/family?day=` (sharing rules), `/api/geo/steps?user_id=` (activity sharing), usage |
| Faith & growth | weekly recap, praying for …, talk journal, suggestion lanes | existing cards |
| Devices (father, admin) | each device: model, Android and build, app version and build, last seen, address, first seen, battery; protection the phone reported (device admin, device owner, Accessibility guard, web filter allowed and running, usage access); tamper reports; which parents set an offline code | `/api/family/member/{u}/devices` |

Charts are recharts (`components/member/charts.tsx`): one sky hue for the
measure, amber plus the words "over the limit" for a status, a dashed limit
or goal line, a dotted average, a tooltip on every bar, a text summary and a
screen-reader list. A day with no report is a gap, never a zero. Desktop: a
left rail and a two-column grid; phone: sticky sideways tabs, one column,
44 px controls (`e2e/family-member.spec.ts` checks both, for the father and
the mother).

### New data sources added

- The phone reports its protection (Guard plugin status plus usage access) as
  the device's `capabilities.guard` whenever it changes, from the five-minute
  usage sync (`PhoneUsageSync`); needs the next APK.
- Tamper reports are kept: the gateway's `/api/guard/tamper` records a
  `guard_tamper` device event on the child through identity's
  `POST /api/internal/guard/tamper/{u}` before telling the parents.
- Rules take `day_limits` (`{mon..sun: minutes}`), which wins over the daily
  and weekend limits in `common/screen_time.evaluate`.

### Not built (no source yet)

- **Age**: birth year is in the child's private health profile only.
- **Per-category limits and app categories**: the phone reports packages, not
  categories.
- **Focus Time** and custom schedules: only bedtime and school routines exist.
- **Hourly screen time**: the phone reports a daily total; the heatmap shows
  recorded activity per hour instead, and says so.
- **"For how long"** in the header: no session start is reported.
- **Check-ins** for parents: they are not shared beyond burden signals.
- **Notification access, Health Connect, per-permission states and last sync
  per feed** in Devices: the phone does not report them yet.
- **Battery** stays in `/api/users/location/all` for every family member (the
  family map uses it); the page shows it only under Devices.
