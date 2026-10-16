# Contributing

Project rules for working on this repository. They apply to every change.

## Temporary files

Use `.tmp/` at the workspace root (`<workspace>/.tmp/`) for all temporary work.
Do not use `/tmp`, `/var/folders/...` or any other system temp directory for any
purpose, including `curl -o`, shell redirects (`>`, `>>`), heredocs, scratch files,
test scripts, downloaded artifacts, intermediate files, and temp files created inside
shell commands.

Create `<workspace>/.tmp/` first if it does not exist. This rule takes precedence over
any tool default that points at `/tmp` or `/var/folders/.../T/`.

This covers work done from a checkout (development, tests, scripts run on a host).
Code running inside a service container may use the container's own `/tmp`; it is
private to that container and gone when it is replaced.

## Mobile parity

This app ships as a Capacitor Android/iOS app (`services/ui/android`) built from the
same React code in `services/ui/src`. A desktop change is not done until its mobile
form is done too.

- Every desktop UI change lands with its mobile form unless the owner explicitly says
  otherwise: the same widget or screen, correct at phone widths, not merely not broken.
- Touch targets: interactive rows, menu items and steppers need a hit area of at least
  44px. Keep desktop density with `pointer-coarse:min-h-11` (Tailwind 4.2 variant)
  rather than inflating the desktop layout.
- Never rely on hover to reveal an affordance, and never make mouse-only events
  (`onMouseEnter`, drag) the only path to a feature; provide tap/pointer equivalents.
- Respect the phone grid: widgets render in `BentoBoxDashboard` in columns of at least
  280px and 200px row bands, so content must fit `small` (1x1) up through `tall` and
  scale with its container (SVG `viewBox` plus percentage sizes, no fixed pixel canvas).
- Use native polish where it fits: `useHaptics()` for tactile feedback,
  `Capacitor.isNativePlatform()` for platform branches, and the shell's `safe-area-*`
  handling for insets.
- Verify both forms: check the narrow layout (or the native app) before calling a UI
  change finished.

## Fail fast; never fall back to a hardcoded value

When a value is missing, wrong or stale, fail loudly at the point of use. Never
substitute a hardcoded constant and carry on.

- **No hardcoded failovers.** If a URL, token, username, key or path is not configured,
  raise or return a clear error naming the missing setting. Do not fall back to a
  guessed or legacy literal (for example `http://localhost`, `https://mail.sumemail.com`,
  or an old host or user ID baked into code). A stale literal is far harder to find
  later than an error.
- **Config is configuration.** Defaults belong in env/config/settings that are readable
  and overridable (env vars, Identity `GlobalSetting`s, compose config), not inline in
  logic. If a value must be discoverable, derive it from a configured source or ask.
- **Prefer a visible warning over a silent skip** when continuing is genuinely correct
  (for example an optional integration that is not configured): the response says what
  was skipped and how to configure it, and tests assert that message.
- **Never swallow an exception into a default value.** A failed lookup is an error the
  operator must see, not a `None` that quietly becomes "default".
- Tests cover the unconfigured case explicitly, so a missing value fails the test
  instead of passing on a baked-in constant.

## Build and deploy

Image builds run in GitHub Actions. Do not run `docker build` for a service image
locally or on the production server.

- Pushing to `microservices` triggers the "Build & Push Images" workflow, which builds
  and pushes every changed service to GHCR. Wait for that run to succeed.
- Deploy only by pulling the CI-built image:
  `scripts/deploy_remote.sh <host> <path> <service...>`. It waits for the workflow,
  syncs the server checkout, runs `docker compose pull` and
  `up -d --force-recreate` for the named services, and health-gates the gateway.
- A server-side rebuild duplicates CI work, loads production CPU and disk for about
  20 minutes, and can drift from the pushed commit without anyone noticing. If CI is
  unavailable, say so and get agreement before building anywhere.
