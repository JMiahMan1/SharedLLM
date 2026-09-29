# AGENTS.md

## Temporary Directory Rule (ABSOLUTE — NO EXCEPTIONS, NEVER FORGET)

ALWAYS use `.tmp/` (relative to workspace root, i.e. `<workspace>/.tmp/`) for ALL temporary work.
NEVER use `/tmp`, `/var/folders/...`, or any other system temp directory — for ANY purpose, including:
`curl -o`, shell redirects (`>`, `>>`), heredocs, scratch files, test scripts, downloaded artifacts,
intermediate files, or temp files created inside `bash` commands.

Create `<workspace>/.tmp/` first if it does not exist. This rule overrides any tool default
that suggests `/tmp` or `/var/folders/.../T/opencode`.

## Mobile Parity Rule (ABSOLUTE — NO EXCEPTIONS)

This app ships as a Capacitor Android/iOS app (`services/ui/android`) from the same React
code in `services/ui/src`. Desktop work is never "done" without the mobile surface.

- **Every desktop UI change MUST land with its mobile form** unless the user explicitly
  says otherwise. Same widget/screen, correct at phone widths — not merely not-broken.
- Touch targets: interactive rows, menu items and steppers need a ≥44px hit area. Keep
  desktop density with `pointer-coarse:min-h-11` (Tailwind 4.2 variant) rather than
  inflating the desktop layout.
- Never rely on hover to reveal an affordance, and never on mouse-only events
  (`onMouseEnter`/drag) as the sole path to a feature — provide tap/pointer equivalents.
- Respect the phone grid: widgets render in `BentoBoxDashboard` at ~280px columns and
  200px row bands, so content must fit `small` (1x1) up through `tall`, and scale with
  its container (SVG `viewBox` + percentage sizes, no fixed pixel canvas).
- Native polish is available and should be used where it fits: `useHaptics()` for tactile
  feedback, `Capacitor.isNativePlatform()` for platform branches, and the shell's
  `safe-area-*` handling for insets.
- Verify both forms: check the narrow layout (or native) before claiming a UI change works.

## Fail Fast, Never Fall Back To A Hardcoded Value (ABSOLUTE — NO EXCEPTIONS)

When a value is missing, wrong or stale, **fail loudly at the point of use**. Never
substitute a hardcoded constant and keep going.

- **No hardcoded failovers.** If a URL, token, username, key or path is not configured,
  raise/return a clear error naming the missing setting. Do not fall back to a guessed or
  legacy literal (e.g. `http://localhost`, `https://mail.sumemail.com`, an old host or
  user ID baked into code) — a stale literal is far harder to find later than an error.
- **Config is configuration.** Defaults belong in env/config/settings that are *readable
  and overridable* (env vars, Identity `GlobalSetting`s, compose config), not inline in
  logic. If a value must be discoverable, derive it from a configured source or ask.
- **Prefer a visible warning over a silent skip** when continuing is genuinely correct
  (e.g. an optional integration that is not configured): the response should say what was
  skipped and how to configure it, and tests should assert that message.
- **Never swallow an exception into a default value.** A failed lookup is an error the
  operator must see, not a `None` that quietly becomes "default".
- Tests must cover the unconfigured case explicitly, so a missing value fails the test
  rather than passing on a baked-in constant.
