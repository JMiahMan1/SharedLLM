# Family Hub — Chat, Games, Create

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
| Chat UI | `src/components/chat/ChatPanel.tsx` — the **only** chat surface |
| Drawing / recipes / creations | Nextcloud files (same credentials as Notes) |
| Pictures, music, polish | Raven / Jarvis / Alpaca (`/v1/chat/completions`, image + audio paths) |
| Points, badges, cheers | Achievements engine + points ledger (`docs/ACHIEVEMENTS.md`) |
| Chores & rewards | Skylight integration (`/api/integrations/skylight/*`) |

`Communication` no longer hosts chat: it renders a card linking here, and its
tests assert the Talk composer is *not* present there — so a second chat UI
cannot reappear.

## Shipped

- **Family page with Chat / Games / Create tabs** (segmented control on
  phones, thumb-sized targets, bottom-nav clearance).
- **ChatPanel on Nextcloud Talk**: conversation picker (full list on desktop,
  horizontally scrolling pills with avatars on phones), message bubbles aligned
  by author with per-person avatar tints, Enter-to-send composer, and voice
  messages that **preview before sending** (playback + optional caption +
  discard) before posting into Talk.
- **Mobile-first rules** applied: 44 px minimum targets, sticky composer with
  `safe-area-inset-bottom`, no hover-only affordances, `text-base` inputs so
  iOS does not zoom, single-column stacking on phones, 60 vh feed on phones /
  560 px on desktop.
- Games and Create tabs list the planned features honestly ("planned") rather
  than showing dead buttons.

## Next slices

### 1a. Talk depth — reactions and polls SHIPPED

Tap any message to open a quick reaction bar (👍 ❤️ 😂 🎉 🙏 😮) and chips show
the tallies. Runs entirely on Talk's reaction API through the gateway
(`/api/communication/talk/reactions`, `/react`) and the execution action pair
`reactions`/`react`, so every Talk client (phone, Skylight board) sees them too.
Polls ride the same path: a poll button next to the composer creates one
(Talk poll API, 2-4 options, blanks rejected before we call Nextcloud) and
open polls render above the feed with tap-to-vote and live counts. Both
surfaces therefore stay in sync with the Nextcloud phone app and the Skylight
board, because Talk owns the data.

### 1b. Games (server first)

The Games tab also features the highest-rated **Alpaca arcade** titles
(admin-curated, rating fallback): see **docs/ARCADE_INTEGRATION.md**.
- Question sets as **data** (`services/games/*.json`, like theme packs and
  achievement definitions) so new quizzes need no deploy:
  Bible trivia, Bible memorisation (fill-in-the-blank with hints), family
  trivia (generated from names/dates you already have), draw & guess.
- One active game per Talk room, state in Redis
  (`game:{room}:{id}`), moves posted as **typed chat messages** so everyone
  plays in the conversation they are already in and no new client is needed.
- Scoring feeds the points ledger; winners can earn Skylight stars.

### 2. Create
- **Draw together**: shared canvas (one document per drawing, stored in
  Nextcloud), then "make it better" sends the image + prompt to Raven/Alpaca
  for an improved version (img2img), keeping the original.
- **Make a picture**: prompt → generated image → saved to the family folder
  and shareable into a chat as a card.
- **Make music**: mood/lyrics → generated track, saved and shareable.
- **Recipes**: a sheet-like Nextcloud note (markdown checklist) editable by the
  whole family; "what's for dinner" can be asked in chat.

### Admin identity switching in chat (shipped)

Admins may post as either **their own user** or the shared **Admin (default)**
identity — for house announcements, chores and games that should look like
they come from the house account rather than a parent.

Rules:

- **Default is always the caller's own user**, and the composer shows which
  identity is active (e.g. a "Sending as Admin" badge) so nothing is sent
  under the wrong name by accident.
- The choice is **per admin and per device**; turning it on never changes
  anyone else's default.
- **The server decides, never the client.** `401`/identity spoofing is the
  whole point of BUG-01 (`5ca9f00a`: client-supplied `user_context` is now
  ignored), so the request carries only a flag (`as: "admin"`); the gateway
  honours it **only when the authenticated caller is an admin**, otherwise
  403, and then substitutes the admin/default user before calling execution.
  A client can never name an arbitrary user.
- One mechanism covers the whole Talk surface — messages, voice notes,
  reactions and polls all ride the same `user_context` resolution, so the
  switch is implemented once in the gateway proxy rather than per feature.
- Switching identity is visible by design (messages display the author Talk
  attributes) and audit-logged when an admin posts as the Admin identity.

**Shipped** (gateway `450ec020`): `as_user: "admin"` is accepted only when the
resolved caller has `is_admin`, resolves user ID 1 (the same account Identity
falls back to) and is forwarded by every Talk write (send, voice, react, poll
create/vote, conversation open); reads deliberately ignore it. The composer
shows a "Send as: Me | Admin" selector to admins only, defaults to Me,
remembers the choice per device and badges "Sending as Admin" while active.
Tests: 7 gateway (`test_talk_identity_switch.py`) + 6 UI
(`SendAsSelector.test.tsx`).

### 3. Chat depth
- **Voice/video calls**: Nextcloud Talk WebRTC needs the Talk signalling API
  (`/ocs/v2.php/apps/spreed/api/v4/call/*`). Phase 1 is a join button that
  opens the Talk room; phase 2 is in-app WebRTC via a Talk client library.
- **Typed chat envelope** (`text | activity | game | creation | system`) — **shipped**
  (`services/ui/src/lib/chatEnvelope.ts`, `components/chat/EnvelopeBody.tsx`).
  The wire format is a fenced `jarvis-envelope` JSON block appended to
  human-readable text, so a bot can post a card while old clients still see
  plain text. Malformed cards degrade to text instead of blanking a
  conversation. `encodeEnvelope` / `decodeEnvelope` / `activityEnvelope` are the
  whole contract; 7 unit + 6 component tests.
  Posting side: execution action `post_card` (and gateway
  `POST /api/communication/talk/card`) sends `card_kind`/`card_title`/
  `card_detail`/`card_stars`/`card_stats`; an unknown kind or a missing title
  is rejected rather than posted.
  **Shipped:** `achievements.announce_awards()` posts an activity card when a
  badge is first earned — after banking, so a Talk outage can never cost the
  badge — and stays silent unless `FAMILY_CHAT_TOKEN` names a room, so a
  deployment with no family chat never posts anything. 3 tests.
- Kid-friendly touches: emoji reactions, big buttons, optionally read-aloud
  replies through the existing TTS path.

## Tests

- `src/test/Family.test.tsx` — opens on chat, tab switching, DM open, message
  send, voice preview → caption → send.
- `src/pages/Communication.test.tsx` — sections render and chat is *not*
  duplicated there.
- Target for the games slice: rules engine unit tests (deterministic question
  selection, scoring, no repeated questions) plus a room-level integration test.

See **docs/CHAT_UI_RESEARCH.md** for the build-vs-reuse research
(reactions/polls/calls already exist in Talk; Excalidraw for drawing; LiveKit as
the call fallback) that drives these phases.

## Family games (shipped)

`services/execution/handlers/family_games.py` is a small, dependency-free game
kit the Jarvis bot runs inside a conversation — no model in the loop, so it
never wanders off mid-game:

- **Trivia**: gentle multiple choice, forgiving answer matching (case, articles,
  punctuation). One ⭐ per correct answer.
- **Memory**: pick the words, then duplicate them, shuffle, and flip two at a
  time. A pair stays up; a miss flips back.

One game per room, a leaderboard in every card, and `FAMILY_GAMES_ENABLED` must
be set or the bot refuses to start — a deployment that does not want a bot
answering in chat never gets one by accident. Gateway:
`POST /api/communication/talk/game` with `game_command` (start/answer/flip/stop).
Correct answers are banked in geo's star ledger (`reason: "game"`) so stars
outlive the round; a geo outage is logged and never breaks a game in progress.
11 unit + 6 integration tests.
