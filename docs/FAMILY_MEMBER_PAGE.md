# Family member page

One page per child that brings everything Jarvis knows and every control a
parent has into one place, the way the commercial family-safety apps do. It
replaces the single-column `/guardian` page (its cards are reused, not copied).

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

- **Which members:** identity's `may_view_raw_data` rule, unchanged. A
  minor's page is open to an admin, the father and the linked mother (any
  mother when none is linked). An adult's collected data stays their own, so
  adults do not get a monitored page.
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
