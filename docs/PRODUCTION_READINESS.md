# Production readiness - the bar, the method, the findings

Written 2026-10-26, ahead of merging `microservices` to `main`.

The owner's bar, verbatim: *"we need every aspect of the app (both frontend/backend and security)
to be production level and have production polish... everything not just works but works and
integrated well and is represented well in the UI in a clean and easy to use way."*

This file is the working document for that. It states the bar, the method, and then the findings
area by area, each with `file:line`, the user-visible symptom, and the fix. Findings are graded:

- **BROKEN** - does not work, or works in a way that would embarrass us. Fix before merge.
- **ROUGH** - works, but unpolished, inconsistent, or confusing. Fix before merge if cheap.
- **FINE** - meets the bar. Listed only where it is worth recording *why*.

## 1. What "production level" means here

This is a self-hosted app for one family of seven behind a proxy, not a public SaaS. So the bar is
not "multi-region, on-call rota, 99.99%". The bar is:

1. **Nothing is broken.** Every feature the UI offers either works end to end or is not offered.
   No dead ends, no buttons that do nothing, no screen that renders blank, no control that silently
   fails. This is the whole complaint that started this pass: the media page "semi-worked".
2. **Nothing is fake.** Every number, image, list and status is real data or an explicit
   loading / empty / error state. No placeholder that looks like data, no invented default.
   (Standing rule in HANDOVER §1.)
3. **Nothing is a dead end.** Every screen answers "what now?" - a next action, a back path, or an
   explanation of why it is empty.
4. **It is honest about failure.** If an upstream (Home Assistant, Music Assistant, Nextcloud,
   Audiobookshelf, the LLM) is down, the user sees a clear, calm message and the rest of the app
   keeps working. No raw stack traces, no silent no-ops, no infinite spinners.
5. **It is safe for the family it holds data for.** Children's health data, photos, chats and
   locations are in here. Authorization is enforced on the server for every object, not hidden in
   the UI.
6. **It is usable by everyone in the house.** A 7-year-old on a tablet and an adult on a laptop both
   get a clean, obvious layout. Meets WCAG 2.2 AA in the practical sense: contrast, accessible
   names, labelled inputs, visible focus, tap targets at least 24px (2.5.8) and ideally 44px.
7. **It is operable.** A failure is visible in logs, a deploy can be rolled back, and state survives
   a restart.

### Reference points (what the good ones do)

- **Navigation**: mobile gets a bottom tab bar of 3-5 primary destinations with icon + label and
  44-48px targets; desktop gets a 220-260px sidebar with a clear primary/secondary hierarchy and an
  obvious active state. Primary features are never buried in an overflow menu. (Alfdesign sidebar
  guide 2026, DesignStudio mobile navigation 2026, UXPin 2026.)
- **Accessibility**: WCAG 2.2 AA. The most common real-world failures, in order, are low-contrast
  text, missing alt text, missing form labels, empty links and empty buttons (WebAIM Million).
  WCAG 2.2 adds target size (2.5.8), focus not obscured (2.4.11) and stronger focus visibility.
- **Parental controls**: Family Link and Apple Screen Time set the free baseline - app limits,
  downtime/bedtime schedules, app blocking, content restrictions, app-approval requests. Qustodio
  and Bark add category-based web filtering, daily/weekly emailed reports, location + geofencing,
  and a panic button. Bark's strength is *reporting* (a readable activity feed) rather than raw
  enforcement; Qustodio's is per-category filtering and a 30-day history. A production-grade family
  guard therefore needs: enforceable limits, a **readable report of what actually happened**, and
  alerts - not just a lock.
- **Media**: Spotify/Apple Music/Plex set the bar for artwork everywhere (never a grey box when an
  image exists), a persistent now-playing surface, a queue you can see and reorder, and a playing
  indicator. An artist or album always plays something.

## 2. Method

1. Read the HANDOVER and the per-feature docs; treat them as claims to verify, not facts.
2. Walk each area in the running app (or its code where a live walk is impossible), on a phone-sized
   viewport and a desktop one.
3. Probe the live system read-only where a claim can be settled by evidence.
4. Grade every finding, fix the BROKEN and cheap ROUGH ones, and record the rest with a reason.

## 3. Findings

Area-by-area findings are appended below as each audit lands.

### 3.1 Media

Fixed in this pass (commits `9dbf4243`, `bd5a9dc9`):

- **BROKEN - no artwork loaded at all.** Music Assistant hands the gateway the *provider's* CDN URL
  (`https://r2.theaudiodb.com/...`) in `image.path`, and the image proxy's SSRF guard refuses any
  host the household did not configure, so every cover 404'd. The same entries carry MA's own
  `proxy_id`, which MA serves from its own host at `/imageproxy/<proxy_id>`. `_media_ma_image` in
  `services/gateway/main.py` now prefers `proxy_id`.
- **BROKEN - book authors and narrators listed as Artists.** MA's 500 artists are 198 `singer`,
  161 `narrator` and 140 `author`; `is_playable` is True for all of them, so `artist_type` is the
  only honest signal. An author has nothing to open, so the entry was a dead end. The Artists tab,
  the search artists bucket and favorited artists now drop `author`/`narrator`; the books remain in
  the Audiobooks tab where the author is already shown.
- **BROKEN - a real artist could not be played.** `playRequestFor` refused every artist, so the
  artist page had no Play button. MA expands an artist URI into every track that artist has, so the
  page now offers Play all and Shuffle, and the row menu offers Play next and Add to queue.

### 3.2 Merging `microservices` into `main` (investigated 2026-10-26)

**A plain merge is wrong and must not be used.** Measured facts:

- `main` tip is `52d3688b` (2026-01-05) - ten months stale. `git rev-list --left-right --count
  origin/main...origin/microservices` -> `374 3887`. The merge base is `2e6c7efb` (2025-12-20).
- All 374 `main`-only commits are from a 2.5-week window (2025-12/2026-01). `git cherry
  origin/microservices origin/main` reports **372 of them as patch-equivalent already present** in
  microservices, so `main` holds no work we have lost - only commit objects.
- **351 of those 374 commits touch the legacy `app/` tree**, which microservices deleted in
  `56d64620` (2026-04-30, an ancestor of microservices but *not* of main). `app/` is 0 files on
  microservices and 95 on main.
- A real merge in a throwaway worktree (`git merge origin/microservices --no-commit --no-ff` on
  `origin/main`) exits **1 with 60 conflicts** (`44 UD`, `11 AU`, `4 UU`, `1 AA`, plus `106 D`), and
  **resurrects `app/` with 58 files**. (`git merge-tree --write-tree` misleadingly reported exit 0.)

**The verified recipe** (tested 2026-10-26 in a throwaway worktree; result tree byte-identical to
`origin/microservices`, both parents kept, `app/` absent, and `microservices` becomes an ancestor of
`main` so every later merge is clean):

```bash
git checkout main
git merge -s ours --no-commit origin/microservices   # keeps both parents, tree still = main
git read-tree -u --reset origin/microservices        # take microservices' tree wholesale
git commit -m "Merge microservices into main"        # parents: main, microservices
git push origin main
```

Do this only after the production-readiness work below has landed and the tree is green, since a
push to `main` runs the same image-build pipeline as `microservices`.
