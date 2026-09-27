# Alpaca Arcade integration (Family hub Games tab)

How the family hub pulls the **highest-rated games from the Alpaca arcade**
(`../alpaca`) alongside the games we build ourselves (Bible trivia, Bible
memorisation, family trivia, draw & guess).

## What the arcade already gives us

Source: `../alpaca/arcade/app.py` (Flask, port 5001, container
`alpaca-arcade`, `ARCADE_DIR=/app/data/arcade`, `Dockerfile.arcade`).

| Thing | Where | Notes |
|---|---|---|
| Games index (HTML) | `app.py` `"/"` (377) | renders `list_games()` cards |
| Games index (JSON) | `app.py` `"/api/games"` (684) | `{success, games: [card…]}` with `?category=` filter; each card includes `"rating": _rating_stats(d)` (342), `plays`, `top_score`; regression-tested in `../alpaca` `20cf10c` |
| Game detail (JSON) | `app.py` `"/api/games/<slug>"` (690) | card + full `scores` list + `prompt` — what the Jarvis Games tab should list |
| Star ratings | `app.py:95-99` | `ratings.json` per game: votes with `stars` 1-5 → `{count, average}` |
| Scores / bests | per game dir (`scores.json`, `bests.json`) | top-five tables; scoring contract writes `/tmp/alpaca_score.json` on every finished run |
| Web game | `/game/<slug>/index.html` (471) | HTML5/three.js games |
| Desktop game | `/game/<slug>/game.py` (490) | launched via `/api/games/<slug>/launch` (498) and streamed |
| Player page | `/play/<slug>` (440) | also `/game/<slug>/screenshot.png` |
| Achievements | `arcade/achievements.py` | player cards, crowns, "give five stars" etc. |
| Embed mode | `web/templates/ui_launcher.html` | the launcher supports `?embed=1` so a game screen can be embedded |

So ratings, scores and play routes already exist — we integrate, we do not
re-implement.

## Shipped: game shelf in the Family hub (slice 1)

- **Gateway** `GET /api/arcade/games` proxies the arcade JSON endpoint and
  returns `{success, arcade_available, play_base, featured_source, featured,
  games, count}`. It always answers 200, so an unreachable arcade renders an
  honest offline card instead of an error.
- **Featured order**: admin curation wins (`PUT /api/arcade/featured` stores a
  JSON slug array in the Identity `GlobalSetting` `arcade_featured_games`;
  Identity enforces admin rights on the forwarded credentials), else
  highest-rated games with ≥1 vote, else best benchmark score.
- **UI**: `services/ui/src/components/family/ArcadeGames.tsx` on the Family →
  Games tab — featured shelf, full list, star ratings, plays, play links.
- **Config**: `ALPACA_ARCADE_URL` (default `http://jeremiah-home-desktop.local:5001`)
  and `ALPACA_ARCADE_PUBLIC_URL` (default = the internal URL) decide fetch and
  play targets.
- **Tests**: `services/gateway/tests/test_arcade_proxy.py`,
  `services/ui/src/components/family/ArcadeGames.test.tsx`.
- **Still open**: in-app play. Games are opened on the arcade host directly for
  now; embedding the play screen needs the gateway to also own the game pages'
  own `/api/games/...` calls (score bridge), which is the phase-2 transport
  question below.

## Design

1. **Admin curation (explicit request).** New admin-only setting
   `arcade_featured_games`: an ordered list of `{slug, label?}`.
   - If unset, fall back to **top-N by average rating** with a minimum vote
     count (so one 5★ vote cannot outrank a well-played game), N default 6.
   - Stored with the other global settings so it is managed from
     Settings → Integrations and survives restarts.
2. **Gateway passthrough.** `/api/arcade/games` (read-only list: slug, label,
   rating, votes, kind web|desktop, thumbnail/screenshot), plus
   `/api/arcade/games/{slug}/launch|stop` passthrough for desktop titles, all
   behind the normal Jarvis auth and the internal secret. The arcade never
   needs to be exposed publicly.
3. **Family → Games tab** shows two groups:
   - **Family games** (ours, later slices): Bible Trivia, Bible Memory Verses,
     Family Trivia, Draw & Guess — state lives in Talk rooms, scores feed the
     points ledger.
   - **Arcade**: curated/auto-selected games sorted by rating, each with its
     stars and vote count, so the family sees *why* something is featured.
4. **Playing.**
   - Web games: embed `/play/<slug>?embed=1` in an iframe sized for phones
     (portrait-friendly, fullscreen button); desktop games: phase 2 via the
     existing launch + stream path, with a clear "opens on the big screen"
     label until then.
   - Everything launched from inside the Family page, not a separate app.
5. **Scores come home (the fun part).** A small poller on the arcade's
   `/tmp/alpaca_score.json` (or an arcade webhook) forwards finished runs to
   the gateway, which:
   - adds points to the achievements ledger (`docs/ACHIEVEMENTS.md`),
   - posts a Talk message via the existing bot path — e.g.
     "🎮 Michele scored 12,300 on Tetris", and
   - can mirror points into Skylight stars (`docs/FAMILY_HUB.md`).
   Kid-safe by default: initials only, no free-text.

## Phases

1. Read-only arcade list + admin curation setting + Games tab display.
2. Embed play for web games (mobile-first sizing).
3. Score bridge → points ledger → Talk message ("X scored N on Y").
4. Desktop-game streaming from the Family page.
5. Ratings from the family: let Jarvis users vote 1-5 stars, written back to
   `ratings.json` (same endpoint the arcade already uses).

## Open questions for the arcade side

- Best transport for the score bridge: poll `alpaca_score.json` vs an arcade
  webhook on game-over (a webhook avoids polling and drops the file
  dependency).
- Whether desktop titles are ever played in the Family page or stay on the
  big screen by design.
