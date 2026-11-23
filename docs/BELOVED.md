# Beloved

"I am my beloved's, and my beloved is mine." (Song of Solomon 6:3)

Beloved is a couples page inside the parents' private chat, in the spirit of the
"Between" app but written for Christian married couples. It opens from the
**Beloved** button (a lily) in the private chat's bar, next to Gallery, once the
chat is unlocked.

## What is in it

| Section | What it does |
|---|---|
| **Us** (home) | Both spouses' profile pictures side by side, their names, how many days they have been married (and the years and months), a cover photo they choose, the next milestone and the next special day, today's verse, the latest love note, and a **Thinking of you** button that sends a heart to the other phone. |
| **Days** | The wedding anniversary, both birthdays, their children's birthdays ("Sky's 9th birthday"), the first date, and any days of their own (yearly or once), each with a countdown and, for the coming week, that day's weather. Milestones are added automatically: every 100 days to 1,000, every 500 to 10,000, then every 1,000, plus every yearly anniversary. A 29 February date is kept on 28 February in other years. |
| **Today** | A verse (fetched from the family's Bible service), a short devotion and prayer, and a question to talk about together. Each spouse can mark it done and write an answer the other can read. The same content shows on both phones for a given calendar day. |
| **Notes** | Love notes (the other spouse is told one arrived) and prayer requests for each other, with "Prayed for this" counts and answered-prayer tracking (with how it was answered). |
| **Dates** | 54 date night ideas (at home, out, outdoors, serving together, faith), a fresh handful each day; tap one to suggest it to your spouse, mark it done, or suggest your own. |
| **Just us** | The couple's own private calendar (a moon on the tab). See below. |
| **Our details** | Wedding date, first date, both birthdays, their children's birthdays, what the couple calls themselves, the cover photo, and this phone's reminder setting. |

Content (`services/ui/src/lib/beloved/content.ts`): 64 devotions (so the
devotion does not repeat for two months), 66 discussion questions, 54 date
ideas. Only Scripture **references** are in the repository; the verse text comes
from the Bible service in whichever translation it serves. The reflections,
prayers, questions and ideas are original. The Song of Solomon is treated the
way a pastor treats it from the pulpit: a celebration of faithful married love,
never explicit.

## Who can see it

Only the two spouses: **one father and one mother**, both members of the private
chat. This is enforced on its own, not only inherited from the private chat:

- **Server** (`services/gateway/beloved.py`): every route goes through
  `private_chat._member_chat` (a member of this chat who is still a parent:
  family role father or mother, not a minor, not the system account, with a
  chat account) and then `private_chat.is_couple` (the chat's two members are
  one `father` and one `mother`). Everyone else gets the same `404 Not
  available.` a missing chat gets, so nothing says the feature exists: an
  administrator who is not one of the two, an adult child, a home manager,
  other relatives, teens, children, guests, the household (system) account,
  a signed-out or internal caller with no parent user, and either spouse of a
  chat between two fathers or two mothers. Identity being down stays an honest
  503.
- **App**: the Beloved button renders only when the signed-in account's
  `family_role` is `father` or `mother`, it is not a minor or the household
  account, it is a member of this chat, and the server marked the chat
  `beloved: true` (`lib/beloved/access.ts`). It appears nowhere else: not in
  navigation, search, settings or the notification list of any other account.
  The Android "Special days" notification channel is created only when the
  first reminder is shown, so it never appears on another family member's
  phone.
- **Notices** (hearts, notes, prayer requests, date ideas) go only to the other
  spouse.

## Privacy model

Beloved uses the private chat's end-to-end encryption unchanged
(described at the top of `services/gateway/private_chat.py`):

- **Every record is sealed on the device** with the chat's data key
  (`PrivateVault.sealRecord`, AES-GCM with a fresh IV) in the same envelope as a
  private message. The server keeps `beloved/<token>/<kind>/<id>.json`: the
  envelope, the kind, who wrote it and when. It checks only that the envelope
  is AES-GCM shaped (a 12-byte IV, a ciphertext longer than its tag) and under
  16 KB. It never sees a wedding date, a birthday, a special day's name, a
  note, a prayer, an answer, a date idea choice or the cover photo.
- **Kinds**: `profile` (wedding and first date, birthdays, title, cover photo
  pointer), `day`, `note`, `heart`, `prayer`, `devotion` (one per spouse per
  day), `idea`, and Just us's `together` and `plan`. The kind is visible to
  the server (it has to name a notice); the contents are not. Notes, hearts,
  devotion answers and marked days can be changed or removed only by their
  author; profile, days, prayers, ideas and plans by either spouse. At most 5,000 records per kind per couple.
- **The cover photo** is encrypted on the device and kept as an opaque blob in
  the chat's blob store, exactly like a private chat picture.
- **Opened only while the chat is unlocked**: Beloved lives inside the unlocked
  chat, so a lock (the button, leaving the chat, the app going to the
  background, the lock timer) unmounts it and drops every decrypted record and
  picture. Screenshots and the recents thumbnail are blocked while it is open
  (the private chat's `FLAG_SECURE`).
- **Deleted with the chat**: deleting the private chat removes the `beloved`
  folder with everything else.
- **Notices carry no content**: "Sam is thinking of you", "Sam left you a
  love note", "Sam shared a prayer request with you", "Sam has a date night idea for
  you". They use the private chat's `nudge` category (the heartbeat vibration,
  and the heart over the screen when Jarvis is open) and carry no chat token,
  so they never add an unread count to the thread. One notice per kind per
  sender per minute (hearts) or 30 seconds (others); a heart sent sooner is
  still kept, just not announced again.

## Activity and read signals

The page can tell the other spouse, in a few words and never any content, when
something is read or looked at. It is the same metadata-only idea as the
notices above: the server learns only what was looked at, who looked and when.

- **Reading signals** (`POST /api/private-chat/chats/{token}/beloved/seen`,
  body `{"what": "gallery" | "note" | "prayer" | "idea" | "page"}`):
  - `gallery`: "Sam is looking through your photos", at most once per 30 minutes
    per viewer.
  - `note`: "Sam read your love note"; `prayer`: "Sam read your prayer request";
    `idea`: "Sam saw your date night idea". At most once per 10 minutes per kind
    per viewer.
  - `page`: no notice. A visit to the page is only recorded, for the
    encouragements below.
- **Each spouse controls their own sharing**: a per-user preference
  `share_activity` (default true). With it off, `/seen` records nothing visible
  and sends nothing.
- **Activity log** (`GET /api/private-chat/chats/{token}/beloved/activity`):
  the last 50 entries, each `{who, what, at}` and newest first, kept as metadata
  only (what, who and when, never any content) in the Beloved store directory
  next to the records, capped at the last 200 entries per couple. It powers the
  "Today with <spouse>" line in the UI and is visible only to the couple.

## Encouragements

A gentle daily line inside Beloved, chosen from metadata alone and sent at most
once per local day, inside the spouse's chosen hour and never in the quiet hours
(22:00 to 07:00). It runs in the gateway's background worker
(`services/gateway/beloved_encouragements.py`, a new `_beloved_encouragements_loop`
modelled exactly on the private chat's reply-reminder loop: env flag
`BELOVED_ENCOURAGEMENTS` default on, a look every 300 seconds, the job queue
lease). A `once` key per user per day means a restart never sends twice.

Only an enrolled Beloved couple (the two members of a private chat who pass
`private_chat.is_couple`: one father and one mother) is ever encouraged, the
same check the routes use. For each spouse with encouragements on, the line is
chosen in this order, from metadata only:

a. no heart or note from this spouse in two days or more: "Send Sam a little
   love today: a heart takes one tap."
b. the other spouse sent something in the last day this one has not answered:
   "Sam reached out today. Send something back?"
c. no visit (`page` activity) in three days or more: "Today's verse and question
   are waiting in Beloved. Talk it over tonight?"
d. otherwise a rotating feature tip, one per day, cycling a list of ten and
   deterministic per couple per day (plan an evening, add a prayer request, pick
   a date night idea, leave a love note, answer today's question, mark a day,
   add the children's birthdays, change the cover photo, read the devotion, send
   a heart before bed).

The wording is warm, never guilt-trippy, names no content and never mentions how
long since they wrote in a shaming way. It is a `nudge` titled "Beloved" that
links to the page, exactly like the other Beloved notices. The pure decision
function (`decide`) takes the metadata, now and preferences and returns the
message or None, so every rule is unit tested without a network.

## Preferences

Each spouse has their own Beloved preferences, for the caller only
(`GET`/`PUT /api/private-chat/chats/{token}/beloved/prefs`):

- `share_activity` (default true): whether this spouse's reading signals are
  shared with the other, as above.
- `encouragements` (default true): whether this spouse receives the daily
  encouragement.
- `encourage_hour` (integer 7 to 21, default 19): the local hour the
  encouragement may arrive.

They are stored per user per couple in the Beloved store directory
(`prefs.json`, not secret and not sealed) and are never carried in a record.

## Reminders

The server cannot read the dates, so it cannot send reminders. Instead the
phone schedules them itself:

- Whenever Beloved is opened (and so decrypted) in the Android app, the page
  works out the coming reminders (`lib/beloved/reminders.ts`: the evening before
  at 19:00 and the morning of at 08:00, up to 120 days ahead, at most 40) and
  hands them to the app's own scheduler (`BelovedRemindersPlugin.java` and
  `BelovedReminderReceiver.java`: `AlarmManager`, re-armed after a reboot or an
  app update). Each sync replaces the last set. Nothing goes to a server or a
  push service.
- **The text is generic by default**: "Something special is coming up. Open
  Jarvis to see." A phone's owner can turn on **Show what the day is in
  reminders** (Our details, per phone, off by default, like the private chat's
  "Show message text in notifications"); then it says "Tomorrow: 10th wedding
  anniversary". Even then the lock screen shows only the generic line (the
  notification is `VISIBILITY_PRIVATE` with a generic public version). The
  scheduled list, including that text when details are on, is kept in the
  app's private storage on the phone so it survives a reboot.
- Tradeoff: reminders are only as fresh as the last time Beloved was opened on
  that phone, and the web app has none. A day added on one spouse's phone is
  scheduled on the other's the next time they open Beloved. A server-side
  fallback was not built: it would have to learn at least the dates.

## Just us

A private calendar for husband and wife, on its own tab, discreetly named.
It exists only inside the unlocked private chat: nothing about it shows on
Beloved's home page, on the chat bar's heart, in a notice or in a reminder.

- **Mark a day**: one tap on "We were together" marks today, or tap a day on
  the calendar to mark it, with an optional private note. Either spouse can
  mark; both see it; each can edit the note of, or remove, only their own
  (`together` records are author-only on the server). A day can hold several
  marks (each of them, or more than once): every mark is its own record, the
  day shows a heart with the number of marks, and opening the day lists each
  one to edit or remove on its own.
- **Last time**: how many days since the last marked day, and how many days
  were marked this month and this year (a day marked more than once counts
  once). A month calendar (arrows, or swipe on a phone) shows a small heart on
  marked days and an outline on planned ones. No charts.
- **Plan an evening**: a day, an optional time, an optional idea (a candlelit
  dinner at home, a night away at a hotel, a no-phones evening and a few
  more, nothing explicit) and an optional note. The other spouse is told
  "Something from Sam is waiting" and nothing else. Either spouse can mark a
  plan done once its day has come (which marks that day) or cancel it
  (removes it).
- **Remind me after**: per phone, off by default (3, 5, 7, 10 or 14 days).
  This phone reminds its owner once, at 19:00, that many days after the last
  marked day; marking a day moves it. A reminder already past is not
  scheduled again, so it never nags. The choice is kept only on the phone
  (`localStorage`), never sent anywhere.
- **Reminders for plans**: both phones remind their owner on the morning of
  a plan (08:00, or an hour before an earlier time), scheduled the same way as
  special days (the next time Beloved is opened on that phone).

Privacy, on top of everything above:

- **Every reminder from this tab is generic, always**: title "Jarvis", text
  "A little reminder for the two of you.", whatever "Show what the day is in
  reminders" says. They share the "Special days" channel.
- **Kinds named to say nothing**: the server sees only `together` and `plan`
  with who and when; the day, time, idea and note are sealed. The plan notice
  says only that something is waiting, and a marked day makes no notice at
  all. The server's log line for an unreadable record names its id, never its
  kind.
- **Not counted by the bar's heart** (`unseenLove` counts hearts and notes
  only) and **not previewed on the home page**.
- Files: `lib/beloved/justus.ts` (with `justus.test.ts`),
  `components/chat/beloved/JustUsSection.tsx`; the e2e test in
  `e2e/layout/beloved.spec.ts` checks that nothing typed or picked, and no
  revealing word, reaches the server or a reminder in clear.

## Children's birthdays

Our details keeps a list of the children's birthdays (name and date, up to 20),
sealed in the profile record like every other date. The overview
(`GET .../beloved`) also lists the caller's own children as Jarvis knows them
(`children`: username, name, and the birth date the parents set for app
ratings, from identity's internal guardian list, only children the caller is a
parent of). The sheet offers each one not yet listed as a one-tap button that
fills the row in; the couple can add anyone else by hand (an adult child, a
godchild). Editing the list changes nothing in Jarvis. A name is never cut: a
row with a name and no date (or the reverse) says so and is not kept
(`lib/beloved/children.ts`).

## Weather and the night sky

Days in the coming week show their weather beside the countdown, and in Just us
the selected day, each plan, and the plan form (as soon as a day is picked) show
the day's weather. An evening plan (no time, or 5 PM on) or a stargazing one
also shows the night: the forecast's mean cloud cover from 9 PM to 2 AM
(`night_cloud`, docs/WEATHER.md) with a plain verdict ("Clear skies, good for
stars", "Clear, but a bright moon hides the faint stars", "Some clouds, stars
in the gaps", "Cloudy, the stars will be hidden") and the moon's phase. The
moon is worked out on the phone from the mean synodic month, so it shows for
any date; the clouds only exist for the week the forecast covers
(`lib/beloved/sky.ts`, `components/chat/beloved/BelovedWeather.tsx`). The
weather comes from the same `/api/weather` request every page makes, which
carries no day or plan.

## Android

This needs a new APK (built by CI): the reminder plugin and receiver
(`BelovedRemindersPlugin`, `BelovedReminderReceiver`, registered in
`MainActivity` and the manifest). Hearts and notes reuse the existing `nudge`
channel and sound, so they need nothing new. On an older APK everything works
except reminders, and Our details says the app needs an update for them.

## Files

- Server: `services/gateway/beloved.py`, `private_chat.is_couple`,
  `services/gateway/beloved_encouragements.py`, tests in
  `services/gateway/tests/test_beloved.py` and
  `services/gateway/tests/test_beloved_encouragements.py`.
- App: `services/ui/src/lib/beloved/` (dates, days, content, records,
  reminders, access, with `beloved.test.ts`),
  `services/ui/src/components/chat/beloved/`, `plugins/belovedReminders.ts`,
  `e2e/layout/beloved.spec.ts`.
