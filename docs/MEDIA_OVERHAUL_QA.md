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
| UX-12 | **GAP** | `NowPlayingCard.tsx` still shows "Live" whenever `duration <= 0`; there is no `isLive` from the item kind and no reducer test. Needs `media_type`/kind plumbed through `ingest` → store before this can PASS. |
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
| UX-32 | **GAP** | No visual baseline for the dashboard `ActiveMediaWidget` (component tests exist: `ActiveMediaWidgetWebPlayer.test.tsx`, `ActiveMediaWidgetClock.test.tsx`). |
| UX-33 | **GAP** | Skeletons exist and `listenNow.test.tsx` "UX-33" checks them at card size, but the required E2E `slow` scenario with a `PerformanceObserver` CLS assertion does not exist. |
| UX-34 | PASS | `e2e/media/now-playing.spec.ts` (swipe-up, mobile); sheets use `dvh` |
| UX-35 | PASS | `visual.spec.ts-snapshots/*-hermetic-desktop-darwin.png` (responsive grid) |
| UX-36 | PASS | `__tests__/format.test.ts` "UX-36 shows --:-- for unknown" |
| UX-37 | PASS | `e2e/media/listen-now.spec.ts` journey 2; `OutputPicker` `nowDoing()` |
| UX-38 | PASS | `__tests__/usePlayback.test.ts` "UX-38 sends only the settled volume" |
| UX-39 | PASS | `__tests__/haptics.test.ts` (added: transport/play use `light`, never `heavy`) |
| UX-40 | PASS | `__tests__/listenNow.test.tsx` "Connect your music"; `libraryPage.test.tsx` UX-40 |
| UX-41 | **GAP** | No `expect.poll(... within 1500 ms)` timing assertion in the E2E (journeys 2/6). |
| UX-42 | PASS | `e2e/media/search.spec.ts` "UX-42 the query and filter survive back and forward" |
| UX-43 | PASS | `StateViews` driven by query state (`isError`); `libraryPage.test.tsx`, `bookPage.test.tsx` states |
| UX-44 | PASS | `__tests__/listenNow.test.tsx` "UX-44 shows time left … else the percentage" |
| UX-45 | PASS | `src/test/reconnectToastThrottle.test.ts`; `OutputSheet.test.tsx` "reads the status silently"; `eventStream.test.ts` |
| UX-46 | PASS | `__tests__/eventStream.test.ts` "names the media service for media calls" |
| UX-47 | **GAP** | No scroll-hide/float E2E and no mobile mini-player baseline. |
| UX-48 | **GAP** | No landscape (844×390) visual baseline. |
| UX-49 | PASS | `e2e/media/keyboard.spec.ts`; `focus-visible` tokens in `components/ui.ts` |
| UX-50 | PASS | grep: "Jump Back In" / "Browse All Media" / "Select Device" all removed |
| UX-51 | **GAP** | Persistence is unit-tested (`mediaStore.test.ts` "persists only the output choice"), but the required E2E reload is absent (`persistence.spec.ts` covers navigation, not reload). |
| UX-52 | PASS | `__tests__/mediaStore.test.ts` (item keeps `album` for every output) |
| UX-53 | PASS | `__tests__/usePlayback.test.ts` "UX-53 rolls back mute, shuffle and repeat" |

## Open gaps (block Phase 6 completion)

Seven rows are not PASS. Six are missing **verification artifacts that live
outside `services/ui/src`** (Playwright visual baselines, an E2E CLS/scroll/
timing/reload assertion) and are therefore outside this workstream's scope:

- UX-32 (widget visual baseline), UX-33 (E2E CLS), UX-41 (E2E timing),
  UX-47 (scroll/float E2E + mobile baseline), UX-48 (landscape baseline),
  UX-51 (E2E reload).

One is a real code gap:

- **UX-12** — the "Live" badge is still derived from `duration <= 0` rather than
  the item kind; fixing it needs `media_type`/kind carried through
  `store/ingest.ts` → `RemotePlayer.item` (and the web-player handle) first.

## P7-T4 performance budget (2026-10-25)

- **Render-count test — done:** `src/features/media/__tests__/performance.test.tsx`
  proves, with fake timers, that the running playhead re-renders only the time
  readout and never the page around it.
- **Idle page makes no periodic calls — covered by unit tests:** the shared feed
  opens the SSE stream and never polls while it is up
  (`__tests__/eventStream.test.ts` "a stream that opens within 5 s never polls").
  The entry asks for an E2E request count over 15 s; that spec is not in
  `services/ui/src` and was not added.
- **Media route chunk lazy-loaded — pending.** The entry names `React.lazy` in
  `App.tsx`, which this workstream is not allowed to touch (a lazy boundary in
  `pages/Media.tsx` would break the many component tests that render it
  synchronously).
- **Initial-JS ≤ baseline + 60 KB gzip — not recorded.** The entry measures with
  `npx vite build`; running the build is out of scope here and there is no
  committed baseline to compare against, so no number is recorded (an invented
  one would be worse than none).
