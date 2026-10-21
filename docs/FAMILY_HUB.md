# Family Hub: Chat, Games, Create

One interactive space for the whole household, designed **mobile-first**:
phones and tablets are the primary way this gets used, desktops are the
exception.

Route: `/family` (`src/pages/Family.tsx`). Navigation entry is called
**Family** on both the sidebar and the bottom nav.

## Rule: one implementation of everything

This page reuses existing systems and must not grow parallel copies:

| Need | Reuse |
|---|---|
| Chat (text, voice) | **Nextcloud Talk** via the existing `/api/communication/talk/*` APIs |
| Chat UI | `src/components/chat/ChatPanel.tsx`, the **only** chat surface |
| Drawing / recipes / creations | Nextcloud files (same credentials as Notes) |
| Pictures, music, polish | Raven / Jarvis / Alpaca (`/v1/chat/completions`, image + audio paths) |
| Points, badges, cheers | Achievements engine + points ledger (`docs/ACHIEVEMENTS.md`) |
| Chores & rewards | Skylight integration (`/api/integrations/skylight/*`) |

`Communication` (timers and spoken announcements) hosts no chat, calendar or
notes of its own: it links to Family, `/calendar` and `/notes`, and its tests
assert the old copies are gone (its notes editor appended the whole note to
itself on every save; its calendar only printed "Loaded N events").

## What is there now

- **Family page** with Chat / Games / Create tabs. On phones the chat tab is
  exactly the screen's height and only the conversation list (or the thread)
  scrolls; the shell sizes to the dynamic viewport (`dvh`).
- **Chat on Nextcloud Talk** (`ChatPanel` and `components/chat/*`): avatars
  from the family directory, conversation management (groups, participants,
  delete), read markers, reactions and polls through Talk's own APIs, voice
  notes with preview, attachments, a verse banner and quick-action chips, and
  `@Jarvis` questions (answered as a Jarvis bubble via the `assistant`
  envelope, posted with the asker's login). A thread opens on the latest 50
  messages; "Show earlier messages" loads 50 more at a time (Talk's cap: 200).
- **@Jarvis, one answer**: a message *starting* with `@Jarvis` sent from the
  app is answered by execution's Talk `send` and claimed in Redis
  (`jarvis:talk:answered:<token>:<id>`); the gateway's Talk monitor answers
  `@jarvis` typed in other Talk apps (rooms the system account is in) and
  skips claimed messages.
- **Phone notifications** for new family messages (no outside push service):
  a gateway worker (`services/gateway/chat_notify.py`) reads each member's own
  Talk list and queues one notice per new, unread message from someone else;
  telemetry publishes it to ntfy, the Android app reads it from the push
  stream `GET /api/push/stream` (`PushService`, see [PUSH.md](PUSH.md)) and
  posts one conversation notification per chat with inline reply; tapping
  opens `/family?chat=<token>`. A
  person's first pass only records where conversations stand; after that a
  brand-new conversation's first message is announced. A Jarvis answer is
  announced as "Jarvis" to the whole room, including the person who asked.
- **Family safety** (`components/family/SafetyButtons.tsx`, beside the title
  on every tab, 44 px targets on phone and desktop): **On my way**
  (`POST /api/family/on-my-way`) tells everyone else you are heading home,
  with the drive time by road from geo when it has your position; **SOS**
  asks first, then `POST /api/family/sos` `{"confirm": true}` sends a
  priority 5 `family_safety` notice to everyone else with your last position
  (how long ago, coordinates, an OpenStreetMap link). Telemetry delivers it
  even to people who switched family safety off. Both live in
  `services/gateway/family_safety.py`. Each person can send one SOS a minute
  and one On my way every five minutes; sooner, the server answers 429 with
  when it can go again, which the app shows as is (an SOS repeat also closes
  the dialog, since the first one went out). A send nobody received does not
  start the wait. The other safety notices: identity
  tells the family once per discharge when a phone drops below 15% and is not
  charging (`services/identity/battery_alerts.py`, `LOW_BATTERY_PCT`; only a
  charging report, or a reading of at least `LOW_BATTERY_PCT` + 5, arms it
  again, and each notice carries a `once` key per person per day, so a
  reading wobbling around 15% or two phones on one account never repeat it
  the same day). The announcement runs after the location upload has been
  answered. Geo sends important places and long stops (GEO_SERVICE.md,
  Notices). Only admins can mark places important (Wander's Places panel shows
  the settings read only to everyone else).
- **Job notices** (category `jobs`): a finished song (with "expires in 30
  minutes"), Jarvis's improved drawing and a finished audiobook conversion
  notify whoever asked; the first two open `/family?tab=create`.
- **No standing notification for background work**: Android requires a
  notice while a service runs in the background. Location sharing and the chat
  connection post theirs to the "Background services" channel
  (`BackgroundNotice.java`), which the app creates turned off. On Android 13
  and later the services keep running and appear only under "Active apps" at
  the bottom of the notification shade. Chat messages and family notices use
  their own channel per kind and pop up normally, clearing once read. Android 12 and
  earlier may show a system "running in the background" notice instead.
  Steps need no service of their own: the hardware step counter keeps counting
  and is read on the location service's periodic wake-ups.
- **Background media**: one app-wide player (`MediaPlayerContext`, above the
  router) so switching tabs never stops playback, and an Android media
  foreground service (`MediaPlaybackService`) with lock-screen controls.
- **Location sharing that survives the app closing**: the app asks for
  "Allow all the time" (Android only offers "While using the app" in the first
  prompt) and the battery exemption (`LocationBackgroundNotice`, shown on the
  Dashboard and Location panel); `BootReceiver` resumes location sharing and
  chat notifications after a reboot or app update.
- **Games** (trivia, memory), **Draw together**, **Make music**, **Recipes**,
  **Voice signatures** and **Calls** (sections below). Bible games and
  memorisation are linked from the Games tab (`/bible?tab=play`,
  `/bible?tab=memorize`).
- **Send to chat** (`components/chat/SendToChat.tsx`): a drawing, Jarvis's
  improved picture or a finished song goes into a conversation the person
  picks, as a Talk attachment (25 MB cap, like the composer).

## Explicitly not doing

- No Matrix/Element/Mattermost/Rocket.Chat migration: they replace the
  backend the family already uses and would orphan Skylight and phone clients.
- No third-party chat-as-a-service (Stream/Sendbird): proprietary, per-seat
  pricing, and the data would leave the house.
- No bespoke reactions/polls/call stack; Talk ships them.

## Mobile-first checklist (phones and tablets are primary)

- 44 px minimum targets; composer pinned with `safe-area-inset-bottom`.
- The message list is the scroll region (`min-h-0` + `overscroll-contain`);
  the page itself does not scroll on the chat tab.
- `text-base` inputs (no iOS zoom), thumb-reachable send/voice buttons.
- Conversation switching without a menu dive.
- Every game and canvas usable with one thumb and tolerant of interruption.
- Canvas pointer positions are scaled into the canvas grid (`canvasPoint`),
  so strokes land under the finger whatever the displayed size.

## Admin identity switching in chat (shipped)

Admins may post as their own user or as the shared **Admin (default)**
identity. The request carries only `as_user: "admin"`; the gateway honours it
only when the authenticated caller is an admin (otherwise 403) and forwards it
on every Talk write (send, voice, react, poll create/vote, conversation open);
reads ignore it. The composer's "Send as: Me | Admin" selector is admin-only,
defaults to Me and badges "Sending as Admin" while active. Tests:
`test_talk_identity_switch.py`, `SendAsSelector.test.tsx`.

## Typed chat envelope (shipped)

`services/ui/src/lib/chatEnvelope.ts` + `components/chat/EnvelopeBody.tsx`:
kinds `text | activity | game | creation | system | assistant`, carried as a
fenced `jarvis-envelope` JSON block appended to readable text, so other Talk
clients still see plain text and malformed cards degrade to text. Posting side:
execution action `post_card` (gateway `POST /api/communication/talk/card`).
`achievements.announce_awards()` posts an activity card when a badge is first
earned, only when `FAMILY_CHAT_TOKEN` names a room.

## Tests

- `src/test/Family.test.tsx`: opens on chat, tab switching, DM open, message
  send, voice preview → caption → send, a drawing sent to a picked chat.
- `src/test/ChatFeatures.test.tsx`: envelopes, replies, edits, attachments,
  "Show earlier messages".
- `src/pages/Communication.test.tsx`: timers and announcements; chat,
  calendar and notes are links, not copies.
- `services/gateway/tests/test_talk_monitor.py`: the monitor answers other
  Talk apps and skips questions the app already claimed.
- `services/execution/tests/test_family_games.py` and `test_talk_games.py` for games;
  `src/components/family/*.test.*` for drawing and music;
  `services/gateway/tests/test_chat_notify.py` for notifications.

## Family games (shipped)

`services/execution/handlers/family_games.py` is a small, dependency-free game
kit the Jarvis bot runs inside a conversation, with no model in the loop, so it
never wanders off mid-game:

- **Trivia**: gentle multiple choice, forgiving answer matching (case, articles,
  punctuation). One star per correct answer.
- **Bible trivia**: the same engine with a Bible question pool, started from the
  chat composer's **Bible trivia** action.
- **Memory**: pick the words, then duplicate them, shuffle, and flip two at a
  time. A pair stays up; a miss flips back.

One game per room, a leaderboard in every card, and `FAMILY_GAMES_ENABLED` must
be set or the bot refuses to start; compose enables it by default
(`FAMILY_GAMES_ENABLED=1`) and an operator can set `0` to turn games off.
Gateway: `POST /api/communication/talk/game` with `game_command`
(start/answer/flip/stop).
Correct answers are banked in geo's star ledger (`reason: "game"`) so stars
outlive the round; a geo outage is logged and never breaks a game in progress.
Tests: `services/execution/tests/test_family_games.py`, `test_talk_games.py`.

## Draw together (shipped, single-device first)

`components/family/DrawCanvas.tsx` lives on Family → Create: colour and brush
pickers, undo, clear, download, **Send to chat**, and a **Make it way better**
pass that sends the PNG to the existing image-edit backend and shows the result
(which can be sent too). A refusal is reported in place; nothing pretends to
have worked. A blank canvas is not sent.

How the improve pass runs: the PNG goes as a data URL to
`POST /api/images/edit/jobs`, which answers at once with a job id. The gateway
hands the edit to execution's `/execute/image_edit_inline` in the background,
and the app polls `GET /api/images/edit/jobs/{id}` every few seconds until the
picture or a refusal is ready. Execution sends the image as multipart to the
`llm_local_url` proxy's `/v1/images/edits` using the
**`family_drawing_edit_model`** setting, falling back to `image_edit_model` if
that setting is blank. The seeded default is
`qwen-image-edit-2511:q3_k_s`, the newest Qwen edit model that runs on an 8 GB
card; it can be changed from **Settings → AI & Compute → Family Drawing**. The
prompt asks the model to keep the child's subject, characters, colours and
playful style while cleaning the lines and adding gentle detail, rather than
replacing the drawing. The same proxy and error wording are used as the
workspace image edit (`handlers/image_edit.py`). The finished job answers
`{"status": "SUCCESS", "data": [{"b64_json": ...}]}` or a `FAILURE` naming the
cause (no model set, proxy down, backend refused). The canvas is 640x416
because edit models work in 16 or 32 pixel blocks.

The job route exists because an edit on a CPU-offloaded model takes minutes;
the UI waits up to 25 minutes and the backend up to 20 minutes for the slower
8 GB-friendly model.
Holding one HTTP request open that long failed as soon as a proxy had a shorter
timeout: the public nginx used its 60s default, answered 504, and the old app
retried the POST as a fresh edit. Jobs also carry an optional `request_id`, so a
repeated tap or a retried start returns the same job instead of starting a
second GPU edit. The job table is in memory and expires after an hour; a gateway
restart loses an unfinished edit, and the app says so rather than polling
forever. `POST /api/images/edit` remains as the synchronous path for app builds
that predate the job route. Measured on the desktop, a 640x416 edit spent 276 s
sampling at the backend's default 20 steps; the `image_edit_steps` setting sends
a lower count (the rapid edit models are made for about 4 to 8), and a model
switch on the desktop adds about a minute. Until this was fixed the gateway
forwarded the JSON body straight to the legacy `ALPACA_SD_URL`
(`jeremiah-home-desktop.local:8081`), which the server cannot reach and which
only takes multipart, so every press answered 502 in under 100ms.

Deliberately dependency-free (pointer events, `toDataURL`, the `/api/images/edit`
proxy we already had). On a family device that has to download an APK to get a
change, a new build-time dependency is a real cost, and the component's
interface is small enough that dropping in Excalidraw later is a swap rather
than a rewrite. Multi-user live drawing is shipped through a small gateway
WebSocket (`services/gateway/drawing.py`): the board relays brush segments,
replays them to a late joiner, and holds them in memory for the gateway's
lifetime. Toggle **Draw together**, enter a board code (default `family`), and
everyone on that code draws on the same canvas; **Leave shared board** returns
to the private canvas.

Handling notes worth keeping: pointer capture and PNG export are feature-
detected, and "has anyone drawn yet" tracks the first stroke rather than
reading pixels back.

## Family recipes (shipped)

`/recipes` (also on Family → Create) keeps a shared cookbook as markdown in
Nextcloud under `Recipes/`. It reuses the notes storage the house already has,
so there is one backup, one sync and one mobile app, and no new service. Opening a
recipe and saving edits it in place; changing its name renames the file
(`lib/noteSave.ts`, shared with Notes); delete asks first.

Because notes resolve to the caller's own account, an admin can flip the same
"Send as" control to the Admin identity to keep one house cookbook, and every
other member keeps their own. Non-admins never see the control.

## Voice signatures (shipped)

Each person enrolls under Identity -> Vocal Signature by reading three short
sentences aloud. Every recording goes to identity (`POST /api/users/me/enroll`),
which has execution turn it into a 256-number speaker signature
(`POST /voice/signature`, Resemblyzer GE2E on CPU, weights bundled in the image)
and keeps only that vector, never the audio. A take that does not sound like
the person's earlier ones is refused rather than averaged in. "Test: who am I?"
(`POST /api/users/me/voice/check`) scores a new recording against everyone
enrolled, exactly as identification does.

Once enrolled, a person can refresh the profile by voice: say "Hey Jarvis,
learn my voice" at a satellite and speak three normal commands. Each take is
checked against the existing profile before it is added, and the audio is still
never stored.

`sharedllm_speaker_identify` signs a workspace clip the same way and asks
identity (`POST /api/voice/identify`, internal) who it is: a match needs
similarity 0.80 and a 0.04 lead over the runner-up, so two family members who
sound alike come back as "not sure" instead of the wrong name. Nothing in this
path leaves the server. Calibration and limits are in `services/identity/voice.py`.

## Make music (shipped)

Songs, not just clips: pick a shape (song, pop, ballad, short, or a single
clip) and a length up to 4 minutes. `POST /api/music/songs` queues a job on
the alpaca audio server (`/api/music/jobs`), which writes the song section by
section (intro, verse, chorus, bridge, outro); each section continues the last
4 s of the one before, choruses after the first are reused, and seams are
crossfaded (alpaca `music_song.py`). `GET /api/music/songs/{id}` reports the
place in line or the section being written, and the finished song as a data
URL; the page remembers the job, so leaving it does not lose the song. The
audio server keeps a finished song for 30 minutes, so the way to keep one is
**Send the song to chat**.

Music jobs run one at a time on one queue because the 8 GB card is shared
with the resident LLM: a job uses the GPU only when there is room, otherwise
(or when the GPU runs out mid-song) a CPU copy, about 6x slower but never a
500. `ALPACA_AUDIO_URL` has **no default**: an unset value is a 503 naming the
setting.

## Calls

Family → Calls lists the family rooms and one-to-one conversations. **Drop in**
starts a peer-to-peer WebRTC call inside Jarvis; nothing opens in Nextcloud or
another tab. An active call shows **Join** instead, and the chat header's phone
button starts the same drop-in.

Calls behave like a family drop-in line. Pressing **Drop in** puts you in the
room immediately. Any device with auto drop-in enabled joins automatically with
its microphone and speaker after a short beep, so the caller is not left waiting
for someone to accept. There is no ringing screen or accept alert. Devices that
are not auto-joined see a quiet banner above the mobile bottom navigation and can
join with one tap.

Auto drop-in is a per-device setting in **Settings → Family drop-in**. It is off
by default. Browsers may still ask for microphone permission the first time; if
auto-join is blocked, the banner is the fallback. The setting and the media
streams stay on the device.

The gateway is only the signalling relay. Browsers connect to
`/api/communication/calls/ws`, exchange SDP offers, answers and ICE candidates,
then send audio and video directly to each other. Media never touches the
gateway and nothing is recorded. `GET /api/communication/calls` lists rooms
that currently have someone in them, which is what the Calls tab and the
in-app banner poll.

Calls are a mesh: each participant encodes one stream per other participant.
`CALL_MAX_PEERS` (default 8) refuses a room beyond that, rather than melting
the family's phones. The default ICE configuration uses public STUN, which is
enough on a home network and most NATs; supply a TURN service without changing
code with `CALL_ICE_SERVERS`, a JSON list in the usual
`[{ "urls": "turn:...", "username": "...", "credential": "..." }]` form.
Rooms live in the gateway's memory, so restarting the gateway ends active
calls; signalling reconnects are not preserved across a deploy.

Nextcloud Talk still owns the chat and its conversations. Talk calls are not
used by this surface; the room token is only the shared name both people join.
Audio-capable ESP devices can join the same rooms as WebRTC clients; a native
ESP client is still a separate piece of work. Tests:
`services/gateway/tests/test_calls.py`,
`services/ui/src/test/FamilyCalls.test.tsx`,
`services/ui/src/test/CallContext.test.ts`,
`services/ui/src/test/ChatManage.test.tsx`.
