# Push Notifications (ntfy, no Google)

All phone notifications travel over one self-hosted push server, ntfy, running
in the Jarvis stack. Nothing goes through Google. The Jarvis Android app holds
the only connection, and it is also a UnifiedPush distributor, so other apps
on the phone (Nextcloud Talk, DAVx5, Matrix and Mastodon clients) can use the
same connection.

This document is the contract the services and the app are built against.

## Pieces

| Piece | Where | Job |
|---|---|---|
| `ntfy` container | `docker-compose.yml`, config `ntfy/server.yml` | Stores and fans out messages. Internal only, except UnifiedPush endpoints. |
| Telemetry | `services/telemetry` | The only publisher of Jarvis notices. `POST /api/telemetry/notify` checks the person's preferences, writes the in-app outbox, sends browser web push, then publishes to ntfy. |
| Gateway | `services/gateway` | Authenticates phones and proxies their subscription stream to ntfy; hosts action callbacks and UnifiedPush registration. |
| Identity | `services/identity` | Stores each person's notification preferences and their UnifiedPush registrations. |
| Android app | `services/ui/android` | One background service reads the stream, shows notifications, and acts as a UnifiedPush distributor. |

## ntfy

- Image `binwiederhier/ntfy:v2.28.0` (upstream, nothing to build), `serve`
  with `ntfy/server.yml`; users, tokens, ACL and message cache live in the
  `ntfy_data` volume. No web app, no sign-up, no login.
- `auth-default-access: deny-all`. One ntfy user, `jarvis`, with read-write on
  every topic. ntfy provisions the user and its access token on every start
  from `NTFY_AUTH_USERS` and `NTFY_AUTH_TOKENS` in `.env` (never in the repo;
  `.env.example` says how to generate them). The same token is `NTFY_TOKEN`,
  which telemetry and the gateway use; phones never see it.
- `everyone` gets write-only on `up*` topics, so outside servers can deliver
  to UnifiedPush endpoints. Nobody but `jarvis` can read any topic.
- Message cache 12h, attachments off, `behind-proxy: true`, keepalive every
  30s. The stack network (172.26.0.0/16) is exempt from request rate limits
  and may hold 200 subscriptions, because every stream and publish is the
  one `jarvis` user.
- No Caddy route to ntfy except: `POST /push/up*` on the public site goes to
  ntfy with the `/push` prefix stripped and `up=1` added (so a binary,
  encrypted body is kept as the message, base64-encoded, whatever headers the
  sender used). Other methods there get 405.
- `scripts/deploy_remote.sh` runs `docker compose up -d ntfy` on every deploy,
  which starts it when it is not running. A change to `ntfy/server.yml` needs
  `docker compose restart ntfy`; a Caddyfile change needs Caddy restarted.

### Topics

- `jarvis-<user>-<secret>`: a person's Jarvis notices. `<secret>` is 24 random
  URL-safe characters, created once per person and kept in identity
  (`user.push_topic`). Every device of that person reads this topic.
- `up<32 random chars>`: one per UnifiedPush registration (one app on one
  device). Unguessable; it is the capability.

## Publishing a Jarvis notice

Every service keeps calling telemetry; nobody else talks to ntfy.

`POST /api/telemetry/notify` (internal secret), body:

```json
{
  "user": "michele",
  "category": "chat",
  "kind": "chat",
  "title": "Jeremiah",
  "body": "On my way",
  "priority": 4,
  "tag": "chat:abc123",
  "data": {"url": "/family?chat=abc123"},
  "actions": [
    {"label": "Reply", "type": "reply", "callback": "chat_reply", "params": {"token": "abc123"}},
    {"label": "Open", "type": "view", "url": "/family?chat=abc123"}
  ],
  "once": "chat:abc123:42"
}
```

- `category` (required for new senders, defaults to `general`) is what
  preferences switch on and off. Categories:
  `chat`, `family_presence`, `family_safety`, `leave_by`, `calendar`,
  `timers`, `bible`, `reports`, `home`, `jobs`, `raven`, `app_updates`,
  `health`, `admin` (admins only).
- `priority` 1 to 5 (ntfy's scale; 5 = urgent, used for SOS and leaks).
- `tag` groups and replaces: a newer notice with the same tag replaces the
  older one on the phone (timer countdowns, "N new messages").
- `actions` (up to 3):
  - `view`: opens `url` in the app.
  - `callback`: the phone calls `POST /api/push/action` on the gateway with
    `{"callback": ..., "params": ..., "reply"?: text}` and the person's own
    API key. Allowed callbacks are a fixed list in the gateway
    (`PUSH_CALLBACKS`); unknown names are rejected.
  - `reply`: a callback that first asks for text inline in the notification.
- Telemetry drops a notice whose category the person turned off (except
  `family_safety` priority 5 SOS, which cannot be turned off), and records it
  nowhere.
- ntfy message: topic = the person's topic, `message` = JSON of the notice
  above plus `id` and `created_at`, so the phone has everything in one field.
  ntfy's `title`, `priority` and `tags` (`[category]`) are set too. A notice
  whose JSON is over 4096 bytes is not published (ntfy would make it an
  attachment).
- `priority` defaults to 3; `label` is 1 to 40 characters; a `view` needs
  `url`; `callback`/`reply` need a lowercase `callback` name and an object
  `params`. Anything else is a 422.
- `"user": "@admins"` sends the notice to every admin (identity's
  `GET /api/internal/push/admins`, cached 60s), each with their own
  preference check and `once` key; the answer is
  `{"status": "SUCCESS", "recipients": {user: result}}`, or 503 when identity
  cannot say who the admins are.
- Order per person: preference check (identity's
  `GET /api/internal/push/preferences`, cached 60s; unreadable counts as on),
  `once` dedupe (3 days), in-app outbox, browser web push, ntfy. Answer for
  one person: `{"status": "SUCCESS", "push": {...}, "ntfy": bool}`, or
  `{"status": "SUCCESS", "dropped": "preference"}`, or
  `{"status": "SUCCESS", "duplicate": true}`. A failing ntfy never fails the
  request: it is logged once until a publish works again, and the outbox
  still has the notice. Code: `services/telemetry/notices.py`,
  `services/telemetry/ntfy.py`.

## Phones: the stream

`GET /api/push/stream` on the gateway, authenticated with the person's API key
(`X-Api-Key` or bearer). The gateway resolves the person, then streams ntfy's
JSON stream for their topic plus every `up*` topic registered to the calling
device (`X-Device-Id` header), using `NTFY_TOKEN`. Query `since=<ntfy id>` to
catch up after a reconnect (a message id, unix time, duration, `all`). The
response is ntfy's line-delimited JSON (`event: open | keepalive | message`,
`application/x-ndjson`), passed through unchanged: each message keeps its
`topic` (the phone routes `up*` messages by it) and ntfy's
`"encoding": "base64"` for binary UnifiedPush bodies. The client only ever
sees its own topic and its own device's `up*` topics.

- The topic list is fixed when the stream opens: reconnect after a register
  or unregister.
- ntfy's keepalive (every 30s) is the heartbeat. 75s without a line from ntfy
  ends the stream, as does ntfy closing it; the phone reconnects with
  `since`. A phone disconnecting closes the upstream connection.
- 401 without a real key, 400 without `X-Device-Id`, 503 when the server has
  no `NTFY_TOKEN`, 502 when ntfy is unreachable or refuses.
- Successful streams are routine polls (`_ROUTINE_POLL_PREFIXES`): not sent to
  the logging service.
- Code: `services/gateway/push_routes.py`.

The old long poll `/api/telemetry/notifications/wait` stays until every phone
runs a build that uses the stream, then is removed.

## UnifiedPush (other apps)

The Android app implements the distributor side of UnifiedPush AND_3
(`REGISTER`, `UNREGISTER`, `MESSAGE_ACK`; sends `NEW_ENDPOINT`, `MESSAGE`,
`UNREGISTERED`, `REGISTRATION_FAILED`; link activity for
`unifiedpush://link`).

- `POST /api/push/up/register` (person's key, `X-Device-Id`), body
  `{"app": "com.nextcloud.talk2", "token": "<connector token>"}` returns
  `{"endpoint": "https://jarvis.sumemail.com/push/up<id>", "topic": "up<id>"}`.
  Stored in identity against the person and device.
  Registering the same app and token again returns the same topic. At most
  50 registrations per device. The endpoint's host is `JARVIS_HOST`.
- `POST /api/push/up/unregister` with the same body removes it. Without
  `X-Device-Id` (the web settings page) it matches app and token on any of the
  person's devices. Answers `{"status": "SUCCESS", "removed": n}`.
- `GET /api/push/up` lists the caller's registrations on all their devices
  (Settings screen): `{"status": "SUCCESS", "registrations": [{"device_id",
  "app", "token", "topic", "endpoint", "created_at", "this_device"}]}`.
- Identity keeps them in `UnifiedPushRegistration`; internal routes
  `POST /api/internal/push/up/register`, `POST /api/internal/push/up/unregister`,
  `GET /api/internal/push/up?username=&device_id=`.
- Messages arriving on an `up*` topic are handed to that app as `MESSAGE` with
  the raw body (already RFC 8291 encrypted by the sender).

## Preferences

Identity stores per person `{category: bool}` (`UserNotificationPreference`;
absent = default on, `admin` default on for admins and never on for anyone
else). `GET/PUT /api/users/me/notification-preferences`, served by identity
and proxied by the gateway: GET answers
`{"status": "SUCCESS", "preferences": {category: bool}}` with the categories
the person can change; PUT takes a partial or full `{category: bool}` map
(also accepted wrapped as `{"preferences": {...}}`) and answers the same as
GET. An unknown category or a non-boolean is a 422. Settings shows one switch
per category with a one-line description (labels live in the clients).
Telemetry reads every category through identity's
`GET /api/internal/push/preferences` with a 60s cache.

Identity also creates the person's topic on first use:
`GET /api/internal/push/topic?username=` (`user.push_topic`). Deleting a
person drops their preferences and registrations. Code:
`services/identity/push.py`.

## Action callbacks

`POST /api/push/action` (person's key) with `{"callback", "params", "reply"?}`
runs `PUSH_CALLBACKS[callback](request, params, reply)` from
`services/gateway/push_callbacks.py` as the caller; an unknown name is a 400.
Adding one is a single function decorated with `@push_callback("name")`.
`chat_reply` posts `reply` into Talk conversation `params.token` as the
caller (the chat page's send).

## Android

All in `services/ui/android/app/src/main/java/com/jarvisos/app/`.

- `PushService`: the one foreground service (type `remoteMessaging`, the
  hidden "Background services" notice from `BackgroundNotice.java`). Started
  at sign-in (`TokenBridgePlugin.setCredentials`), app start and boot or app
  update (`BootReceiver`); stopped at sign-out. It holds
  `GET /api/push/stream` with `Authorization: Bearer <key>` and
  `X-Device-Id` (a random id made once per install, kept through sign-outs,
  `PushGateway.deviceId`). After every message it stores the ntfy id and
  reconnects with `since=<id>`; a drop retries from 2 s doubling to 5 min,
  sooner when the network comes back; 401/403 waits 5 min; a 120 s read
  timeout (no keepalive) counts as a drop. A new or removed UnifiedPush
  registration reconnects at once so the gateway streams the right topics.
- `PushEvent` parses stream lines. A message whose `topic` is `up*` goes to
  UnifiedPush (body base64-decoded when ntfy marks `encoding: base64`);
  anything else is a Jarvis notice (`PushNotice`), or shown as plain text
  under "Other notices" if it is not one.
- `PushNotifier` posts notices. Channels `jarvis_<category>` per category,
  grouped Family / Reminders / Home and system, with names people know
  (`PushCategories`); unknown categories use `jarvis_general` ("Other
  notices"); priority 5 uses `jarvis_urgent` ("Urgent alerts") whatever the
  category. Priority maps onto Android's (1 = min ... 5 = max); 1 and 2 are
  silent. The old `family_chat` and `family_notices` channels are deleted.
  A `tag` is the notification id, so a newer notice replaces the older one.
  `data.url` (an in-app path only) opens that page. Chat notices (category or
  kind `chat`, conversation from `data.token` or the `chat:<token>` tag) are
  grouped per conversation in a messaging-style notification, not shown while
  that conversation is on screen, and cleared when it is opened.
- Actions become buttons: `view` opens its url in the app; `callback` and
  `reply` go to `PushActionReceiver`, which calls `POST /api/push/action`
  with `{callback, params, reply?}`, the person's key and `X-Device-Id`.
  `reply` uses Android's inline reply; the notification is then updated (a
  chat shows the reply as the person's own line, another notice says "You
  replied"). A callback that worked clears the notification; a failure says
  so on it.
- UnifiedPush: `UnifiedPushReceiver` (REGISTER, UNREGISTER, MESSAGE_ACK) and
  `UnifiedPushLinkActivity` (`unifiedpush://link`, returns an immutable `pi`)
  are disabled in the manifest and enabled only while someone is signed in.
  The caller is identified by `getSentFromPackage()` on SDK 34+ or the `pi`
  PendingIntent's creator; requests with invalid extras or no identity are
  ignored. REGISTER calls `/api/push/up/register` and answers NEW_ENDPOINT
  (an unchanged registration gets its stored endpoint again), or
  REGISTRATION_FAILED with `NETWORK`, `INTERNAL_ERROR` or `ACTION_REQUIRED`
  (signed out). UNREGISTER, removal in Settings and sign-out send
  UNREGISTERED and call `/api/push/up/unregister`. Messages are delivered as
  MESSAGE with `bytesMessage` (1 to 4096 bytes) and the ntfy id as `id`,
  after binding the app's RAISE_TO_FOREGROUND service for 5 s when it has
  one. MESSAGE_ACK is accepted; messages are not resent. Registrations are
  kept on the phone (`UnifiedPushRegistration`, SharedPreferences
  `jarvis_unifiedpush`); the manifest `<queries>` makes connector apps
  visible.
- JS plugin (still named `ChatNotify` so web bundles and APKs of different
  ages match): `start`, `stop`, `setActiveConversation`, `appLabels`,
  `removeApp`, and the `open` event for notification taps.
- Settings (web and app): a Notifications section with one switch per
  category (`GET/PUT /api/users/me/notification-preferences`, the full map is
  sent on each change; `admin` shown to admins only; a note that SOS cannot
  be turned off). In the app, "Apps using Jarvis for push" lists
  `GET /api/push/up` with a Remove button per app.

## Senders

| Category | Sent by | When |
|---|---|---|
| chat | gateway `chat_notify` | new family message, Jarvis answer (Reply, Open); tag per conversation |
| family_presence | geo `_notify_presence` | arrivals and departures (Map) |
| family_safety | identity `battery_alerts`, geo `safety`, gateway `family_safety` | low phone battery, arrived at or left a place marked important (school), unusually long stop somewhere unknown, "On my way" and SOS (priority 5) from the app |
| leave_by | execution `leave_by` | time to leave for an event with a location (tag per event) |
| calendar | execution `calendar_reminders` | event reminders, `reminder_minutes` before (calendar settings, default 30, 0 = off); all-day events at 8:00 on each of their days; in the person's `timezone` calendar setting, else the household's |
| timers | automation / execution `notify_timer_done` | timer or alarm done, tag per timer (Stop, Snooze 5 min, +1 min: callbacks `timer_stop`, `timer_snooze`, `timer_add_minute`) |
| bible | bible `reminders` | daily reading reminder |
| reports | telemetry `deliver_notice` | scheduled report ready or failed |
| home | execution `home_alerts` (Home Assistant) | doorbell, motion while nobody is home, garage open too long, door unlocked at night, washer or dryer done, water leak (priority 5); only entities mapped in the `home_alert_entities` setting, to everyone or the people listed |
| jobs | gateway `notices` / execution | song generated, picture improved, audiobook converted (Open) |
| raven | gateway `notices` | mission finished (to its owner), mission needs approval (to `@admins`: Approve, Reject) |
| app_updates | gateway `notices` | new APK published (once per build) |
| health | geo | daily step goal reached, weekly summary |
| admin | gateway background worker, logging, control_plane, `deploy_remote.sh`, `scripts/notify_admins.sh` | service down (2 failed checks in a row) or back up; error spike (more than `alert_error_spike_threshold` errors in 5 min, or one error more than `alert_error_repeat_threshold` times, once per 30 min per error); deploy finished or failed (with the step); disk nearly full (below `alert_disk_free_percent`) or recovered; desktop GPU services unreachable for `alert_desktop_down_minutes` or back. Backup failed: the stack has no backup job; one added later calls `notify_admins.sh` on failure |

Admin notices replace by tag: `admin:service:<name>`, `admin:desktop`,
`admin:errors:<fingerprint>`, `admin:disk:<disk>`, `admin:deploy`. Details in
`docs/BACKGROUND_JOBS.md` (Admin alerts).

Scripts and cron jobs on the server notify admins with
`scripts/notify_admins.sh "<title>" "<body>" [priority] [tag]`. It posts
`{"user": "@admins", "category": "admin", ...}` from inside the telemetry
container, which already holds `INTERNAL_SECRET`, so the secret never appears
in a command line or log. (`"user": "@admins"` fans out to every admin.)
