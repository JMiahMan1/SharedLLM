# UI Stabilization Plan

## Completed

- [x] Analyzed project structure and quality gates
- [x] Confirmed ESLint passes with 0 problems
- [x] Confirmed TypeScript type-check passes with 0 diagnostics
- [x] Identified Vitest test failure: MSW missing handlers for `/api/groups/media`, `/api/groups/lights`, `/api/groups/patterns`, `/api/telemetry/enroll`, `/api/intercom/sessions`, `/api/intercom/config`, `/api/auth/import/nextcloud`
- [x] Created living plan file
- [x] Added MSW handlers for `/api/groups/*`, `/api/telemetry/enroll`, `/api/intercom/*`, and `/api/auth/import/nextcloud`
- [x] Switched Vitest environment from `jsdom` to `happy-dom` for better MSW XHR interception
- [x] Removed `vi.mock` on API services from Admin tests (integration test approach)
- [x] Fixed Identity.test.tsx `navigator.clipboard` assignment (happy-dom read-only property)
- [x] Fixed Admin.tsx `filteredDiscoveredUsers` useMemo — added missing `discoveredUsers` dependency (was suppressed by eslint-disable, causing memo to lock at `[]`)
- [x] Cleaned up unused import in `src/test/setup.ts` (`request` param in discover handler)
- [x] All 31 tests pass (Admin: 9, Admin integration, Identity, Communication, Dashboard, Docs, JarvisLab, KnowledgeHub, Workspaces)
- [x] ESLint: 0 errors/warnings
- [x] TypeScript: 0 diagnostics
- [x] Production build: clean

## In Progress

- [ ] Run `npx cap sync android` to verify Capacitor sync
- [ ] Check and fix GHA android-build.yml if needed
- [ ] Monitor GHA pipelines after push

## Remaining

- [ ] Enhance E2E Playwright test coverage
- [ ] Verify all GHA pipelines pass (ui-tests.yml, e2e-tests.yml, python-tests.yml, android-build.yml)
- [ ] Final commit and push
