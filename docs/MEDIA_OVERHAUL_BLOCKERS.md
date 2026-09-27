# MEDIA OVERHAUL — Blockers & Pre-existing Failures

## Pre-existing failures (baseline, before any media-overhaul work)

Recorded per P0-T1. Baseline outputs are in `.tmp/baseline-*.log`.

1. **`npx tsc -b` — 40 errors, all pre-existing and outside media scope.**
   **FIXED** (user directive "Fix ALL Errors"):
   - `src/context/LocationContext.tsx(736,749)` — object literal widened
     `permission` to `string`; fixed with an explicit `SensorsState` annotation.
   - `src/pages/Wander.tsx` — missing lucide imports `Lock`, `Zap`, `Activity`;
     added to the import block.
   - `src/ma-stream-test.ts` (24 errors) — debug harness; deleted per BUG-71
     (along with `e2e/ma-stream.spec.ts` and its `rollupOptions.input` entry),
     which the plan had scheduled for Phase 5.
   - `src/pages/Media.tsx(1428)` — `.name` → `.friendly_name` (also BUG-44's
     prescription); `(1548)` — `setSelectedTarget(active.entity_id || '')`
     null-safety.
   - After the fix: `npx tsc -b` exits 0; vitest 171 passed; lint 0 errors;
     `vite build` succeeds.

2. **`npx playwright test --list` — fails with 0 tests listed** because
   `services/ui/e2e/web-player-sendspin.spec.ts:17` throws at module load when
   env vars are missing. This is BUG-73, fixed in P0-T2 (part of this plan).

3. **Gates that PASS at baseline** (must stay green):
   - `npm run lint` — 0 errors, 10 warnings (pre-existing warnings only).
   - `npx vitest run` — 30 files, 171 tests passed.
   - `pytest services/gateway -q` — 390 passed, 2 skipped.
   - `pytest services/execution -q` — 298 passed, 59 skipped.
   - `pytest services/tests -q` — 231 passed, 22 skipped.

## Blockers encountered during implementation

(none yet)
