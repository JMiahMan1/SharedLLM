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
| Games index (JSON) | `app.py` `"/api/games"` (673) | `{success, games: [card…]}` with `?category=` filter; each card includes `"rating": _rating_stats(d)` (342), `plays`, `top_score`; regression-tested in `../alpaca` `20cf10c` |
| Game detail (JSON) | `app.py` `"/api/games/<slug>"` (679) | card + full `scores` list + `prompt`: what the Jarvis Games tab should list |
| Star ratings | `app.py:95-99` | `ratings.json` per game: votes with `stars` 1-5 → `{count, average}`; votes arrive at `POST /api/games/<slug>/rate` (902) |
| Scores / bests | per game dir (`scores.json`, `bests.json`) | top-five tables; scoring contract writes `/tmp/alpaca_score.json` on every finished run |
| Web game | `/game/<slug>/index.html` (471) | HTML5/three.js games |
| Desktop game | `/game/<slug>/game.py` (490) | launched via `/api/games/<slug>/launch` (498) and streamed; `/api/games/<slug>/stop` (540) |
| Player page | `/play/<slug>` (440) | also `/game/<slug>/screenshot.png` |
| Achievements | `arcade/achievements.py` | player cards, crowns, "give five stars" etc. |
| Embed mode | `web/templates/ui_launcher.html` | the launcher supports `?embed=1` so a game screen can be embedded |

So ratings, scores and play routes already exist: we integrate, we do not
re-implement.

## Shipped: game shelf in the Family hub (slice 1)

- **Gateway** `GET /api/arcade/games` proxies the arcade JSON endpoint and
  returns `{success, arcade_available, play_base, featured_source, featured,
  games, count}`. It always answers 200, so an unreachable arcade renders an
  honest offline card instead of an error.
- **Featured order**: admin curation wins (`PUT /api/arcade/featured` stores a
  JSON slug array in the Identity `GlobalSetting` `arcade_featured_games`;
  Identity enforces admin rights on the forwarded credentials), else
  the six highest-rated games with ≥1 vote, else best benchmark score.
- **UI**: `services/ui/src/components/family/ArcadeGames.tsx` on the Family →
  Games tab: featured shelf, full list, star ratings, plays, play links.
- **Config**: `ALPACA_ARCADE_URL` (default `http://jeremiah-home-desktop.local:5001`)
  is the gateway's server-side fetch target; `ALPACA_ARCADE_PUBLIC_URL`
  (default `https://games.sumemail.com`) is the browser-facing play host. The
  public URL must be reachable from phones and the native app, and must be
  HTTPS when the app is served over HTTPS.
- **Tests**: `services/gateway/tests/test_arcade_proxy.py`,
  `services/ui/src/components/family/ArcadeGames.test.tsx`.
- **In-app play**: Play opens the arcade launcher in a full-screen embedded
  player (`/play/<slug>?embed=1`) so a game can be played on a phone without
  leaving the Family hub. Desktop-only code games are hidden on narrow
  screens; the "Open in a new tab" link remains for troubleshooting.
- **Curation**: Alpaca decides which published games appear. The arcade keeps
  a `data/arcade/curated.json` list; publishing a game adds it, hiding it
  removes it without deleting the game or its player scores. The bulk
  publish-all action is gone. The Family shelf therefore only shows the
  chosen, highest-rated mobile-friendly games (plus chosen desktop games).
- **High scores**: `GET /api/arcade/scoreboard` maps each household account
  to its three-letter arcade callsign and reads `/api/players/{initials}`.
  The Family Games tab shows each member's best score, games scored, podiums
  and crowns. `POST /api/arcade/games/{slug}/scores` records a score under the
  authenticated member's callsign, so one member cannot submit as another.
  Desktop streaming remains open.
- **Sorting**: the shelf can be ordered by **Alpaca score** (benchmark),
  **Family rating** (stars) or **Most played**. The gateway forwards `?sort=`
  and Alpaca owns the ordering; nothing is hardcoded in the UI.
- **High score table**: `GET /api/arcade/highscores` returns overall family
  standings plus the top scores per game, shown under the scoreboard.
- **Per-user scores**: score entries carry a stable `player_id` (the household
  username) as well as initials, so a display-name change does not lose a
  member's history. The gateway sends `player_id` with every score and looks
  players up by it.
- **Mobile runtime**: Alpaca injects a viewport meta and fit CSS into every
  playable game at serve time, so fixed-size games scale to the phone. Touch
  controls default on for coarse pointers, and the embedded player fills the
  screen.
- **Score bridge**: when the embedded player detects a finished run it posts
  `{source: 'arcade-score', slug, score}` to the Family page, which prefills
  the score box for the member to confirm and save. No silent submission.
- **Dynamic curation (OODA)**: Alpaca observes benchmark results, orients by
  benchmark score, mobile readiness, duplicate titles, family rating and
  plays, decides the best mobile-ready game per title, and acts by publishing
  only those (capped), respecting admin hides. Admins can also search the
  eligible benchmark candidates and **Add** one, or **Hide**/**Unpublish** a
  game. The arcade is never bulk-published.

## Design

Status: item 1 (curation + list), the Games-tab display, item 2's embedded web
play, item 3's score bridge (embedded auto-detect plus confirmed save), item 5
(family ratings), sorting, the high score table, per-user scores and OODA
curation are **shipped**. Item 4 (desktop-game streaming) is **not built**;
there is no `/api/arcade/games/{slug}/launch` or `/stop` route yet.

1. **Admin curation.** Admin-only setting `arcade_featured_games`, stored as a
   plain JSON array of slugs (no labels).
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
     Family Trivia, Draw & Guess. State lives in Talk rooms, scores feed the
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
   - posts a Talk message via the existing bot path, e.g.
     "🎮 Michele scored 12,300 on Tetris", and
   - can mirror points into Skylight stars (`docs/FAMILY_HUB.md`).
   Kid-safe by default: initials only, no free-text.

## Phases

1. Read-only arcade list + admin curation setting + Games tab display.
2. Embed play for web games (mobile-first sizing). **Shipped**: the Play link
   opens `/play/<slug>?embed=1` in a modal inside the Family hub.
3. Score bridge → points ledger → Talk message ("X scored N on Y").
   **Shipped** for playable games: the embedded arcade posts the detected
   score to the Family page, which saves it under the signed-in member.
4. Desktop-game streaming from the Family page.
5. Ratings from the family: let Jarvis users vote 1-5 stars, written back to
   `ratings.json` (same endpoint the arcade already uses). **Shipped**: the
   signed-in user's initials are sent through the authenticated gateway route,
   so a client cannot vote as someone else.

## Open questions for the arcade side

- Best transport for the score bridge: poll `alpaca_score.json` vs an arcade
  webhook on game-over (a webhook avoids polling and drops the file
  dependency).
- Whether desktop titles are ever played in the Family page or stay on the
  big screen by design.
