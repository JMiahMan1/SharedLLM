# Media Overhaul QA Ledger (P6-T5)

The §3.4 UI/UX issue ledger walked in order. For each row: the result and the
artifact that proves it. Evidence is one of a vitest test name, a committed
Playwright visual baseline, a hermetic E2E spec, or a grep of the source.

**How this was checked (2026-10-25):**
- `npx vitest run src/features/media src/test/Media*.test.tsx
  src/test/ActiveMediaWidget*.test.tsx` → **36 files, 333 tests passed**.
- `npm run typecheck` → clean; `npm run lint` → 0 errors / 11 warnings.
- Visual baselines live in `services/ui/e2e/media/visual.spec.ts-snapshots/`
  (28 files: 7 screens × 2 theme packs × desktop/mobile).
- Hermetic E2E specs live in `services/ui/e2e/media/`.

| ID | Result | Evidence |
|---|---|---|
| UX-01 | PASS | `e2e/media/output.spec.ts`; `visual.spec.ts-snapshots/listen-now-aurora-hermetic-desktop-darwin.png` (header "Playing on …" chip) |
| UX-02 | PASS | `src/test/MediaWebPlayerPage.test.tsx` (FAILED → "Try again", RECONNECTING → "try 2 of 10", connected/paused) |
| UX-03 | PASS | grep: no user-facing "HA/MA" / "Identity service" / "Music Assistant" in `features/media` (matches are code comments/types only) |
| UX-04 | PASS | `src/test/MediaWebPlayerPage.test.tsx` "Couldn't connect this device" + Try again |
| UX-05 | PASS | grep: "Select a device above" removed; the active output card is always shown |
| UX-06 | PASS | grep: "No Active Playback" removed; `listen-now-*.png` |
| UX-07 | PASS | `visual.spec.ts-snapshots/now-playing-*.png` (large hero art + blurred backdrop) |
| UX-08 | PASS | `now-playing-*-hermetic-mobile-darwin.png`; mini-player `MiniPlayer.test.tsx` |
| UX-09 | PASS | `MiniPlayer.test.tsx` (only Previous / Play-Pause / Next; no Stop) |
| UX-10 | PASS | `Scrubber.tsx` `aria-valuetext` ("40 percent" / "muted"); volume slider tests in `OutputSheet.test.tsx`, `NowPlayingSheet.test.tsx` |
| UX-11 | PASS | `NowPlayingSheet.test.tsx` (seek commits on release); `MediaShortcuts.test.tsx` (←/→ 5 s, Shift 30 s) |
| UX-12 | PASS | `store/ingest.ts` (`isLiveItem`); `NowPlayingCard.tsx` (`data-testid="scrubber-skeleton"` when `!isLive && duration <= 0`); `__tests__/mediaStore.test.ts` "UX-12 sets isLive only for radio/stream items"; `__tests__/liveAndOverlays.test.tsx` "NowPlayingCard (UX-12)" |
| UX-13 | PASS | `e2e/media/queue.spec.ts`; `e2e/media/collection.spec.ts` (journey 4, Shuffle fills the queue) |
| UX-14 | PASS | `__tests__/listenNow.test.tsx`; `listen-now-*.png` (Continue listening / Recently played shelves) |
| UX-15 | PASS | `visual.spec.ts-snapshots/library`-style shelves; `MediaCard`/`TrackRow` with `Artwork` |
| UX-16 | PASS | `__tests__/libraryPage.test.tsx` "UX-40 points to settings"; `ListenNowPage` "Connect your music" CTA |
| UX-17 | PASS | `e2e/media/search.spec.ts`, `e2e/media/collection.spec.ts` (real routes; the explorer modal is deleted) |
| UX-18 | PASS | `__tests__/libraryPage.test.tsx` "UX-18 pages through a whole library with no cap" |
| UX-19 | PASS | `__tests__/searchPage.test.tsx` "UX-19 shows skeletons, not a (0) count" |
| UX-20 | PASS | `__tests__/searchPage.test.tsx` "UX-20 opens an author as a search of their books, never disabled" |
| UX-21 | PASS | `StateViews.tsx` `ErrorRetry`; inline banner in `MediaScreen.tsx`; `searchPage.test.tsx` failure states |
| UX-22 | PASS | `e2e/media/listen-now.spec.ts` journey 1; `NowPlayingAnnouncer` (P7-T3) live-region test |
| UX-23 | PASS | `MediaCard.tsx` / `TrackRow.tsx` stay enabled with `aria-busy`; `e2e/media/keyboard.spec.ts` (P7-T2 found and fixed the focus drop) |
| UX-24 | PASS | `visual.spec.ts-snapshots/listen-now-*-mobile-darwin.png`; `MediaCard` `pointer-fine` overlay |
| UX-25 | PASS | `components/ui.ts` `shelfTitle`; `listen-now-*.png` typography |
| UX-26 | PASS | grep: no emoji in `features/media` |
| UX-27 | PASS | `e2e/media/reduced-motion.spec.ts`; the only `animate-pulse` (Live dot) now carries `motion-reduce:animate-none` |
| UX-28 | PASS | `Artwork.tsx` (`KIND_ICON`, round artists); `listen-now-*.png` |
| UX-29 | PASS | `e2e/media/a11y.spec.ts` (axe, colour-contrast rule enabled) |
| UX-30 | PASS | `e2e/media/output.spec.ts` ("Play here" vs "Control") |
| UX-31 | PASS | `__tests__/itemContextMenu.test.tsx` "UX-31 favorites optimistically and rolls back" |
| UX-32 | PASS | `src/test/ActiveMediaWidgetWebPlayer.test.tsx`, `src/test/ActiveMediaWidgetClock.test.tsx` (rebuilt on shared `formatDuration` and theme tokens) |
| UX-33 | PASS | `e2e/media/listen-now.spec.ts` "P5-T1 slow scenario keeps cumulative layout shift under 0.05 (UX-33)"; `__tests__/listenNow.test.tsx` "UX-33" |
| UX-34 | PASS | `e2e/media/now-playing.spec.ts` (swipe-up, mobile); sheets use `dvh` |
| UX-35 | PASS | `visual.spec.ts-snapshots/*-hermetic-desktop-darwin.png` (responsive grid) |
| UX-36 | PASS | `__tests__/format.test.ts` "UX-36 shows --:-- for unknown" |
| UX-37 | PASS | `e2e/media/listen-now.spec.ts` journey 2; `OutputPicker` `nowDoing()` |
| UX-38 | PASS | `__tests__/usePlayback.test.ts` "UX-38 sends only the settled volume" |
| UX-39 | PASS | `__tests__/haptics.test.ts` (added: transport/play use `light`, never `heavy`) |
| UX-40 | PASS | `__tests__/listenNow.test.tsx` "Connect your music"; `libraryPage.test.tsx` UX-40 |
| UX-41 | PASS | `e2e/media/listen-now.spec.ts` (journey 2) and `e2e/media/output.spec.ts` `expect.poll(..., { timeout: 1500 })` |
| UX-42 | PASS | `e2e/media/search.spec.ts` "UX-42 the query and filter survive back and forward" |
| UX-43 | PASS | `StateViews` driven by query state (`isError`); `libraryPage.test.tsx`, `bookPage.test.tsx` states |
| UX-44 | PASS | `__tests__/listenNow.test.tsx` "UX-44 shows time left … else the percentage" |
| UX-45 | PASS | `src/test/reconnectToastThrottle.test.ts`; `OutputSheet.test.tsx` "reads the status silently"; `eventStream.test.ts` |
| UX-46 | PASS | `__tests__/eventStream.test.ts` "names the media service for media calls" |
| UX-47 | PASS | `e2e/media/now-playing.spec.ts` "UX-47 mini-player floats above the mobile bottom bar without overlapping it during scroll" (`hermetic-mobile`) |
| UX-48 | PASS | `e2e/media/now-playing.spec.ts` "UX-48 Now Playing controls stay reachable in 844x390 landscape" |
| UX-49 | PASS | `e2e/media/keyboard.spec.ts`; `focus-visible` tokens in `components/ui.ts` |
| UX-50 | PASS | grep: "Jump Back In" / "Browse All Media" / "Select Device" all removed |
| UX-51 | PASS | `e2e/media/persistence.spec.ts` "UX-51 restores the persisted output choice across a full page reload"; `__tests__/mediaStore.test.ts` "persists only the output choice" |
| UX-52 | PASS | `__tests__/mediaStore.test.ts` (item keeps `album` for every output) |
| UX-53 | PASS | `__tests__/usePlayback.test.ts` "UX-53 rolls back mute, shuffle and repeat" |

## Open gaps (block Phase 6 completion)

None — all 53 rows in the §3.4 UI/UX issue ledger are **PASS** as of 2026-10-26.

## P7-T4 performance budget (2026-10-26)

- **Render-count test — done:** `src/features/media/__tests__/performance.test.tsx`
  proves, with fake timers, that the running playhead re-renders only the time
  readout and never the page around it.
- **Idle page makes no periodic calls — covered by unit tests:** the shared feed
  opens the SSE stream and never polls while it is up
  (`__tests__/eventStream.test.ts` "a stream that opens within 5 s never polls").
- **Media route chunk lazy-loaded — done:** `App.tsx` lazy-loads `pages/Media`
  via `React.lazy(() => import('./pages/Media'))` inside `<Suspense>`.
