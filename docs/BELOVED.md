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
| **Days** | The wedding anniversary, both birthdays, the first date, and any days of their own (yearly or once), each with a countdown. Milestones are added automatically: every 100 days to 1,000, every 500 to 10,000, then every 1,000, plus every yearly anniversary. A 29 February date is kept on 28 February in other years. |
| **Today** | A verse (fetched from the family's Bible service), a short devotion and prayer, and a question to talk about together. Each spouse can mark it done and write an answer the other can read. The same content shows on both phones for a given calendar day. |
| **Notes** | Love notes (the other spouse is told one arrived) and prayer requests for each other, with "Prayed for this" counts and answered-prayer tracking (with how it was answered). |
| **Dates** | 54 date night ideas (at home, out, outdoors, serving together, faith), a fresh handful each day; tap one to suggest it to your spouse, mark it done, or suggest your own. |
| **Our details** | Wedding date, first date, both birthdays, what the couple calls themselves, the cover photo, and this phone's reminder setting. |

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
  day), `idea`. The kind is visible to the server (it has to name a notice);
  the contents are not. Notes, hearts and devotion answers can be changed or
  removed only by their author; profile, days, prayers and ideas by either
  spouse. At most 5,000 records per kind per couple.
- **The cover photo** is encrypted on the device and kept as an opaque blob in
  the chat's blob store, exactly like a private chat picture.
- **Opened only while the chat is unlocked**: Beloved lives inside the unlocked
  chat, so a lock (the button, leaving the chat, the app going to the
  background, the lock timer) unmounts it and drops every decrypted record and
  picture. Screenshots and the recents thumbnail are blocked while it is open
  (the private chat's `FLAG_SECURE`).
- **Deleted with the chat**: deleting the private chat removes the `beloved`
  folder with everything else.
- **Notices carry no content**: "Sam is thinking of you", "Sam left you a love
  note", "Sam shared a prayer request with you", "Sam has a date night idea for
  you". They use the private chat's `nudge` category (the heartbeat vibration,
  and the heart over the screen when Jarvis is open) and carry no chat token,
  so they never add an unread count to the thread. One notice per kind per
  sender per minute (hearts) or 30 seconds (others); a heart sent sooner is
  still kept, just not announced again.

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

## Android

This needs a new APK (built by CI): the reminder plugin and receiver
(`BelovedRemindersPlugin`, `BelovedReminderReceiver`, registered in
`MainActivity` and the manifest). Hearts and notes reuse the existing `nudge`
channel and sound, so they need nothing new. On an older APK everything works
except reminders, and Our details says the app needs an update for them.

## Files

- Server: `services/gateway/beloved.py`, `private_chat.is_couple`, tests in
  `services/gateway/tests/test_beloved.py`.
- App: `services/ui/src/lib/beloved/` (dates, days, content, records,
  reminders, access, with `beloved.test.ts`),
  `services/ui/src/components/chat/beloved/`, `plugins/belovedReminders.ts`,
  `e2e/layout/beloved.spec.ts`.
