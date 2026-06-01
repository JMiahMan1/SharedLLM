> [!CAUTION]
> **THIS IS A LONG-RUNNING TASK. YOU MUST NOT STOP, PAUSE, OR DECLARE COMPLETION UNTIL EVERY SINGLE CHECKLIST ITEM BELOW IS FULLY SATISFIED.** Treat this as a persistent background job that loops indefinitely until all quality gates are green. There is no partial success — every step must pass before you are done.

# Prompt: Jarvis OS 2.0 UI Stabilization, Comprehensive State-Based Testing, & APK Build Integrity

You are an advanced autonomous AI coding assistant. Your mission is to perform a deep stabilization pass on the Jarvis OS 2.0 frontend UI, resolve all existing TypeScript lint errors and configuration conflicts, create robust state-based end-to-end (E2E) and unit tests for every key component and button, and execute a self-correcting git workflow to push these changes cleanly.

Additionally, you must ensure that all continuous integration (CI) workflows in GitHub Actions (GHA) compile flawlessly, with a special emphasis on verifying that the native Capacitor Android APK is successfully built.

> [!IMPORTANT]
> **UNINTERRUPTED CONTINUOUS EXECUTION POLICY ("DO NOT STOP UNTIL DONE")**
> You must run in a continuous, self-correcting loop. You are strictly forbidden from terminating your execution or declaring your task complete until:
> 1. All existing and new TypeScript/ESLint warnings and errors are reduced to **exactly zero**.
> 2. The entire application successfully builds without errors (`npm run build` succeeds).
> 3. All Vitest unit tests pass 100% (`npm run test` succeeds).
> 4. All Playwright E2E specs pass 100% locally against a running UI instance.
> 5. Your changes are cleanly committed and pushed, and ALL GitHub Actions CI workflows—including the Android APK build (`android-build.yml`) and testing pipelines (`ui-tests.yml`, `e2e-tests.yml`, `python-tests.yml`)—complete with **100% green success status**.
> 6. Both the **Debug APK** (`jarvis-os-debug-apk`) and **Release APK** (`jarvis-os-release-unsigned-apk`) are successfully built and compiled without any Gradle or resource sync failures.
> If any test, lint check, build, or GitHub Actions pipeline fails, you MUST analyze the failure, correct the code, and re-run the validation. **Never stop on a failure—correct it, push updates, and proceed!**
> 
> **Resource conservation**: This environment has limited compute resources. **Do not spawn subtasks or parallel agent invocations.** Execute everything sequentially in a single continuous session. Prefer direct file edits and shell commands over delegating work to sub-agents.

---

## 1. Architectural & Aesthetic Context

Before modifying any code, read the following definitive reference guides located in the codebase:
- **`docs/jarvis_os_2_master_guide.md`**: Master system architecture, LiveKit intercom, Picovoice Porcupine native integrations, media pipelines, RAG isolation, and device discovery.
- **`docs/jarvis_os_2_ui_wireframes.md`**: Wireframes, detailed button actions, smart inbox states, light clusters, chore manager dashboards, voice assistant overlays, and user flows.
- **`docs/UI_RAVEN_INTEGRATION_PLAN.md`**: Current Sentinel controls, Active Missions queue, websocket live traces, and Phase 2/3 roadmaps.

### The "Neon Glass" Aesthetic System
The UI is styled using **Tailwind CSS v4** and **Framer Motion** inside a React 19 + TypeScript stack. 
- Avoid generic colors. Use sleek, tailored dark-mode gradients (e.g., slate, zinc, deep indigo, violet, and cyber-neon accents).
- Ensure all interactive elements have responsive hover micro-interactions, subtle glassmorphism (`backdrop-blur-md bg-slate-900/60 border border-slate-800`), and smooth transitions.
- Interactive states (disabled, pending, active) must be visually distinct and use lucide icons with micro-animations.

---

## 2. Immediate Technical Bug Fixes Required

### A. Resolve Vitest & Playwright Test Runner Separation
- **The Issue**: Vitest currently attempts to run Playwright E2E spec files (`e2e/*.spec.ts`) as unit tests, causing import failures since `@playwright/test` is not built for the jsdom/unit test runner environment.
- **The Fix**: Modify `services/ui/vite.config.ts` to explicitly exclude all files in `e2e/**/*` from the `test` block.
- **Verification**: Run `npm run test` and verify that Vitest ONLY runs the React unit/integration tests (e.g., `Admin.test.tsx`, `Communication.test.tsx`, etc.), and is completely green.

### B. Eliminate TypeScript & ESLint Problems
- **The Issue**: Running `npm run lint` reports 30+ problems (primarily `Unexpected any. Specify a different type` warnings/errors) in files such as `src/pages/Admin.tsx` and `src/services/api.ts`.
- **The Fix**: Replace all `any` types with precise TypeScript interfaces, standard utilities, generic type parameters, or `unknown` / `Record<string, unknown>` where appropriate. Do not use `any` as a shortcut; enforce strict typing to improve editor-level compile safety.
- **Safety Rule**: Before removing any imports flagged as unused, verify they are not referenced dynamically or inside TSX props.

---

## 3. Code Quality Tooling — Best Practices, Lint & LSP (MANDATORY)

Every file you touch — new or modified — **must** satisfy all three quality layers before it is committed. This is non-negotiable.

### A. Language Server Protocol (LSP)
- Run the TypeScript language server (`tsserver`) or use `tsc --noEmit` after every edit to surface type errors that ESLint alone may not catch.
- Treat **every LSP diagnostic** (errors and warnings) as a blocking issue. Do not commit any file that has unresolved LSP diagnostics.
- If a type cannot be inferred cleanly, define an explicit interface or type alias in `src/types/`. Do not suppress diagnostics with `// @ts-ignore` or `// eslint-disable` comments unless there is a documented, unavoidable third-party incompatibility.

### B. ESLint (Lint)
- After every batch of file edits, run `npm run lint` from `services/ui/`.
- Zero errors and zero warnings is the only acceptable exit state.
- Lint rules in `eslint.config.js` are authoritative — do not relax or override rules to make the output pass. Fix the underlying code instead.
- Enforce React Hooks rules (`eslint-plugin-react-hooks`), refresh safety (`eslint-plugin-react-refresh`), and strict TypeScript rules (`typescript-eslint`).

### C. Best Practices Checklist
For every React component and utility you create or modify:
- **Hooks**: Only call hooks at the top level. Extract complex hook logic into `src/hooks/`.
- **State management**: Use `useState` + `useReducer` for local state, `@tanstack/react-query` for all async server state. Never store server-fetched data in raw `useState` when a query hook is available.
- **Props**: All component props must have an explicit TypeScript interface — no implicit `any`, no object spreading of unknown shapes.
- **Accessibility (a11y)**: Every interactive element must have an appropriate `role`, `aria-label`, or semantic HTML equivalent so Playwright can locate it via `getByRole`.
- **Side effects**: All `useEffect` calls must declare a complete dependency array. Cleanup functions must be returned where subscriptions, timers, or WebSocket connections are opened.
- **Error boundaries**: Wrap page-level components in `<ErrorBoundary>` to prevent full-app crashes from isolated failures.
- **No magic strings**: Extract repeated string constants (API paths, tab labels, status values) into typed constant files.

---

## 4. Real Data Policy & Scope of Fixes

### A. No Mock Data — All UI Must Use Real Backend Data
- **Mock data is strictly forbidden.** Every list, dropdown, table, chart, status indicator, and interactive widget in the UI must be populated exclusively from live API responses.
- Audit every component for hardcoded arrays, placeholder strings, or `useState` initialized with fake data (e.g., `const [items, setItems] = useState([{ id: 1, name: 'Example' }])`). Replace every instance with a `@tanstack/react-query` hook that fetches from the real backend endpoint.
- If a UI section is currently gated behind a `TODO`, a feature flag, or simply renders empty because no endpoint exists yet, **that is a backend gap you must fix**. Implement the missing backend endpoint in the appropriate FastAPI service (e.g., `services/gateway/`, `services/control_plane/`, or `services/automation/`) and wire it to the frontend.
- MSW (`msw`) mocks in test files are acceptable **only inside unit/integration test setups** (`src/test/`). They must never be imported or active during the real app runtime.

### B. Fix All Issues — Regardless of Scope
- Your mandate is not limited to UI files. If during your work you discover bugs, broken endpoints, missing routes, incorrect response schemas, or mismatched data contracts between the frontend and backend — **fix them all**.
- Do not skip a problem because it appears unrelated to the primary UI task. Every issue you find is within scope.
- After fixing any backend file, re-run the backend test suite (`pytest services/tests/ -x`) to confirm nothing regressed.
- Keep all fixes committed incrementally with descriptive conventional commit messages so the change history remains clean and reviewable.

### C. Tests Must Use Actual Backend Data
- **Both unit/integration tests (Vitest) and E2E tests (Playwright) must run against a live backend.** Mocked API responses via MSW or `vi.mock` are forbidden except where a dependency is a third-party service completely outside the repository (e.g., Nextcloud, Home Assistant). Even then, the mock must be documented with a `// MOCK: <reason>` comment.
- Playwright E2E specs must point at a running instance of the backend (the `UI_URL` env var already handles this). The backend must be seeded with at minimum the `default` admin user and one real workspace before the suite runs.
- Vitest integration tests that render components fetching data must use a real test server (`supertest` or a local FastAPI instance via `conftest.py`) — not intercepted fake responses — so that any data contract mismatch is caught as a test failure rather than silently hidden.
- If the backend is not available in a CI environment, provision it as a service container in the relevant GHA workflow file (`.github/workflows/ui-tests.yml`, `.github/workflows/e2e-tests.yml`) rather than substituting mocks.

---

## 5. Living Plan File — Survive Context Compaction

This is a long-running task. Your context **will** be compacted. To ensure no progress is lost across compaction events, you must maintain a living plan file that is updated and committed continuously alongside your code changes.

- **File location**: `docs/ui_stabilization_plan.md` in the repository root.
- **Create it on your very first step** before touching any code. It must exist in the repo from the start.
- **Update it after every meaningful action** — after each fix, after each test run, after each commit, after each GHA result check. Do not batch updates; write immediately after each event.
- **Structure the file as a running log** with three sections:
  1. **Completed** — a checklist of every task fully finished and committed (include the commit SHA).
  2. **In Progress** — the exact step currently being worked on, with enough detail that a fresh agent with zero prior context can resume from this point.
  3. **Remaining** — the ordered queue of all outstanding tasks yet to be started.
- **Commit the plan file with every code commit.** Use the message suffix ` [plan updated]` in any commit that also updates the plan, e.g.: `fix(types): resolve explicit-any in Admin.tsx [plan updated]`.
- **If you detect that your context has been compacted** (you have lost memory of earlier steps), your first action must be to read `docs/ui_stabilization_plan.md` to reconstruct state before doing anything else. Never re-do work already marked Completed in the plan.

---

## 6. Comprehensive State-Based UI Testing Strategy

You must verify that **every page, tab, modal, and button** is fully state-tested. Write state-based tests that assert visual and structural changes as inputs are entered and clicks are dispatched.

### E2E Testing with Playwright (`services/ui/e2e/`)
Expand and complete the specs (`full-suite.spec.ts`, `admin.spec.ts`, `media.spec.ts`):
1. **Admin System Matrix (`/admin`)**:
   - **Users & Devices**: Inputting name/password in "Create Profile", toggling user roles, importing discovered users, and assigning entities.
   - **Device Groups**: Creating media groups (multi-selecting speakers/TVs), selecting light clusters, and defining scene/pattern options.
   - **Telemetry**: Selecting an entity, inputting custom telemetry parameters, clicking "Enroll", and checking the status list.
   - **Intercom**: Initiating broadcast modes (LiveKit vs. Mumble vs. TV Overlay), asserting target selection lists, and checking active broadcast status indicators.
   - **Raven Ops**: Clicking "Run Fix Now" on triage items, watching websocket live traces in the modal, killing running missions, and opening logs in the Audit Log modal. Assert the blue (reasoning), yellow (action), green (success), and red (error) color-coding.
   - **LLM & Settings**: Changing active coding models, downloading TTS voice styles, and clicking save.
2. **Communication Page (`/communication`)**:
   - Creating/dismissing alarms, adding timers, notes management, and triggering Talk messages.
3. **Media Page (`/media`)**:
   - Searching for media, selecting target playback devices, checking state changes in the player controls (Play, Pause, Volume sliders), and verifying progressive download triggers.
4. **Workspaces Page (`/workspaces`)**:
   - Creating workspaces, checking quarantine badges, and verifying the git patch rollback triggers.
5. **Identity Vault (`/identity`)**:
   - Toggling standard user vs. admin credentials, adding API keys, and editing integration credentials.

*All Playwright assertions must be highly specific, checking elements using appropriate locators (e.g. `getByRole`, `getByPlaceholder`, `getByTestId`) and verifying state-based UI changes (e.g. `.toBeVisible()`, `.toHaveClass()`, `.toBeDisabled()`).*

---

## 5. Mobile App (Capacitor Android) & GHA APK Build Integrity

You must guarantee the compile and bundle stability of the Ionic Capacitor Android application.
1. **Local Capacitor Sync**:
   - Run `npx cap sync android` inside `services/ui` to sync React build assets to the Android Gradle project.
   - Inspect and verify that the sync succeeds without missing references or XML parser errors.
2. **GHA Android Pipeline (`.github/workflows/android-build.yml`)**:
   - Understand the steps in the workflow: JDK 21 setup, Capacitor Android platform creation, asset bundling (`npm run build`), Android SDK licensing, and Gradle tasks (`assembleDebug` and `assembleRelease`).
   - Monitor the GitHub Actions runs after pushing your branch. If the GHA Android build fails, you MUST download/inspect the Gradle build logs, identify structural or configuration issues (e.g., Gradle wrapper version mismatches, Android manifest configurations, SDK target constraints, or missing key files), apply corrections, and push again.
3. **Verify Build Artifacts**:
   - Ensure the pipeline successfully generates:
     - `jarvis-os-debug-apk` (from `services/ui/android/app/build/outputs/apk/debug/app-debug.apk`)
     - `jarvis-os-release-unsigned-apk` (from `services/ui/android/app/build/outputs/apk/release/app-release-unsigned.apk`)
   - Confirm that the uploaded artifacts are complete and operational.

---

## 6. Git & Sync Workflow

Your work will be synchronized with a remote server via git. Execute this workflow autonomously:
1. **Branch Management**: Perform all modifications directly on the **current active git branch** (do not create or switch to a new branch, unless explicitly instructed by the user).
2. **Incremental Commits**: Commit changes incrementally in small, focused chunks. Use conventional commit messages:
   - `fix(ui): exclude e2e directory from vitest runner in vite.config`
   - `fix(types): resolve eslint explicit-any warnings in admin page`
   - `test(e2e): add comprehensive state assertions for intercom and telemetry`
   - `style(ui): polish premium glassmorphism styling on settings tabs`
3. **Remote Push & Pull**: Run the remote sync pipeline. Push your branch to the origin, and if the remote requires integration, execute a clean `git pull --rebase` to merge concurrent changes before final validation.

---

## 7. Coding Standards & Video Rules Checklist

As you modify frontend or backend files, you MUST adhere to the project's strict rules:
- **No Assumptions**: Do not guess how API parameters work. If you need to make changes that interface with the backend, check `api_reference.md` or existing code first.
- **Unused Code Policy**: Never delete imports or functions without tracing all potential references across the entire codebase. When in doubt, deprecate or comment out with explanation rather than delete.
- **Video Playback Rules**: Ensure that any video playback feature relies on `download_video_progressive()` (FastAPI port 8888 serving `.mp4.part` streams with Range headers) to allow immediate streaming. Do not block on full downloads.
- **Android TV Delegation**: Ensure TV playback routes to the Cast sibling when standard protocols fail, using capability-based resolution instead of name matching.

---

## 8. Execution Runbook (Step-by-Step for the AI)

1. **Step 1: Exclude E2E Specs from Vitest**
   - Edit `services/ui/vite.config.ts`.
   - Run `npm run test` and check that E2E specs are no longer compiled by Vitest.
2. **Step 2: LSP Type-Check + Lint**
   - Run `npx tsc --noEmit` first. Fix every LSP/compiler diagnostic before moving on.
   - Then run `npm run lint`. Iterate through files under `services/ui/src/` resolving type errors until ESLint returns `0 problems, 0 warnings`.
   - Repeat both commands after every batch of fixes — they must both be clean simultaneously.
3. **Step 3: Enhance Test Suite coverage**
   - Inspect existing specs in `services/ui/e2e/`.
   - Add detailed assertions for user actions, input forms, state transitions, and modal triggers.
   - Run `npx playwright test` (if playwright is set up locally) to verify assertions.
4. **Step 4: Build Verification**
   - Run `npm run build`. Verify a completely successful build.
5. **Step 5: Git Commit & Integration**
   - Check the repository status using `git status` and `git diff`.
   - Commit all changes cleanly to the **current active branch**.
   - Synchronize with the remote server by pushing the current branch.
6. **Step 6: Monitor GitHub Actions & APK Builds**
   - Monitor the CI pipelines for your push.
   - Ensure `android-build.yml` is executed and that Gradle successfully compiles both Debug and Release APKs.
   - If any GHA job fails, modify the code locally, commit, push, and monitor until the build is 100% successful.

**Do not stop on any step until all checks pass perfectly. Keep self-correcting and iterating until you achieve a robust, premium, and fully tested state!**
