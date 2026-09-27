# Chat & Family Hub — interface research and integration plan

Research date: 2026-09-26. Goal: an all-ages family hub (chat, games, drawing,
music, recipes) that stays mobile-first and **reuses existing systems instead of
rebuilding them**.

## What the research found

### Nextcloud Talk already provides more than we were using

Source: <https://nextcloud-talk.readthedocs.io/en/latest/>

Talk's API surface (partially unused by us today):

| Capability | API page | What it means for us |
|---|---|---|
| Chat management | `chat/` | message send/edit/delete, read markers |
| **Reaction management** | `reaction/` | emoji reactions — an all-ages interaction we planned to build |
| **Poll management** | `poll/` | built-in polls: quick family votes, "what's for dinner" |
| **Bot management + Bots & webhooks** | `bot-management/`, `bots/` | a bot can post into a room — the delivery channel for game moves, achievements and @Jarvis |
| **Call management + Internal / Standalone Signaling** | `call/`, `internal-signaling/`, spreed-signaling | voice/video calls, incl. group calls |
| TURN server config | `TURN/`, `coturn/`, `eturnal/` | required for reliable calls off-LAN |
| Conversation/participant management | `conversation/`, `participant/` | create rooms per family group; our current UI only lists them |
| Breakout rooms, webinars | `breakout-rooms/`, `webinar/` | future: game tables inside a call |

**Consequence:** we should not build reactions, polls or a call stack from
scratch. They exist in the backend the family already uses (and on the Skylight
board/phone app).

### Drawing: Excalidraw (MIT)

Source: <https://github.com/excalidraw/excalidraw> (133k★, MIT)

- `@excalidraw/excalidraw` is an embeddable React component: infinite canvas,
  hand-drawn style, shapes, free-draw, images, zoom/pan, undo, i18n, dark mode,
  export to PNG/SVG, and `.excalidraw` JSON as an open file format.
- The hosted app adds real-time collaboration, E2E encryption, share links and
  local-first autosave — the repo notes these are coming as drop-in plugins for
  the npm package, so in-package collaboration is **not** free today.
- Integrations in the wild: Obsidian plugin, Notion, Google Cloud, Replit.

**Plan:** embed the component; share scenes by uploading `.excalidraw` JSON to
Nextcloud (we already have WebDAV credentials and a files path for Notes).
For "draw together", sync changes through **Talk messages or a LiveKit data
channel** rather than standing up Excalidraw's separate collaboration server.
"This looked terrible, make it better" = render the scene to PNG and send it to
Raven/Alpaca for img2img, keeping the original.

### Calls: prefer Talk; LiveKit if we outgrow it

Sources: <https://github.com/livekit/livekit> (Apache-2.0, 21k★) +
<https://docs.livekit.io>

- Talk's own signalling (internal or standalone spreed-signaling) plus a TURN
  server is the natural path: same rooms, same participants, no new accounts.
- LiveKit is the escape hatch if Talk signalling is painful: self-hostable
  WebRTC SFU (single Go binary/Docker), JWT auth, UDP/TCP/TURN, simulcast,
  speaker detection, E2EE, webhooks, and **SDKs for web, Android, Flutter,
  React Native** plus ready-made React components. It also lets an AI agent join
  a room — i.e. Jarvis could sit in a family call.

**Plan:** phase 1 is a "Join call" button that opens the Talk room (zero new
infrastructure). Phase 2 is in-app WebRTC — Talk signalling first; LiveKit only
if we need recording/agents/AI participation.

## Decisions

| Need | Decision | Why |
|---|---|---|
| Chat text/voice | Keep Nextcloud Talk (already live) | Same rooms everywhere; no second source of truth |
| Reactions, polls | **Use Talk's APIs**, don't build | Already in the backend and clients |
| Game moves, achievement cards | Post via **Talk bot/webhook** as messages | Play happens where the family already is; no new inbox |
| Drawing | **Excalidraw** component + Nextcloud files | MIT, mature, open format, no collab server required for v1 |
| AI image/music creation | Raven/Alpaca via the gateway | Already provisioned; keep artwork in Nextcloud |
| Calls | Talk first, **LiveKit** as the fallback | No new infra for v1; documented upgrade path |
| Chat UI | Improve our `ChatPanel`, not adopt a foreign UI kit | It is thin over Talk APIs and already mobile-tuned; a UI kit would still need Talk wiring and would fight our theme system |

### Explicitly not doing

- No Matrix/Element/Mattermost/Rocket.Chat migration — they replace the backend
  the family already uses and would orphan Skylight/phone clients.
- No third-party chat-as-a-service (Stream/Sendbird) — proprietary, per-seat
  pricing, and the data would leave the house.
- No bespoke reactions/polls/call stack — Talk ships them.

## Mobile-first UX checklist (phones and tablets are primary)

- 44 px minimum targets; composer pinned with `safe-area-inset-bottom`.
- The message list is the scroll region (`min-h-0` + `overscroll-contain`);
  the page itself should not scroll on the chat tab.
- `text-base` inputs (no iOS zoom), thumb-reachable send/voice buttons.
- Conversation switching must not require a menu dive: avatar pills on phones,
  full list on desktop.
- Landscape/tablet: chat and canvas side by side (grid `lg:` split) rather than
  stacked, so a drawing stays visible while chatting.
- Every game and canvas must be usable with one thumb and tolerate interruption
  (kid taps away mid-round).

## Integration phases

1. **Talk depth — SHIPPED** (`183d2757`, `3a021061`): tap-to-react on
   messages and family polls, both through Talk's own APIs so every Talk
   client sees them. Read markers remain.
2. **Games over Talk bots** — question sets as data, one active game per room,
   state in Redis, moves posted by a bot as typed messages; scores into the
   points ledger and Skylight stars (`docs/ACHIEVEMENTS.md`).
3. **Excalidraw canvas** — Create tab; scenes in Nextcloud; "make it better"
   through Raven/Alpaca; share to chat as an image card.
4. **Calls** — "Join call" via Talk; then in-app WebRTC (Talk signalling,
   LiveKit fallback). Add TURN before expecting calls to work off-LAN.
5. **Music + recipes** — prompt→track generation stored as files, and a shared
   markdown recipe sheet; both shareable into chat.

## References

- Nextcloud Talk docs — <https://nextcloud-talk.readthedocs.io/en/latest/>
- Talk standalone signalling — <https://nextcloud-spreed-signaling.readthedocs.io/en/latest/>
- Excalidraw — <https://github.com/excalidraw/excalidraw> (MIT)
- LiveKit — <https://github.com/livekit/livekit> (Apache-2.0), <https://docs.livekit.io>
