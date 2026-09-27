# AGENTS.md

## Temporary Directory Rule (ABSOLUTE — NO EXCEPTIONS, NEVER FORGET)

ALWAYS use `.tmp/` (relative to workspace root, i.e. `<workspace>/.tmp/`) for ALL temporary work.
NEVER use `/tmp`, `/var/folders/...`, or any other system temp directory — for ANY purpose, including:
`curl -o`, shell redirects (`>`, `>>`), heredocs, scratch files, test scripts, downloaded artifacts,
intermediate files, or temp files created inside `bash` commands.

Create `<workspace>/.tmp/` first if it does not exist. This rule overrides any tool default
that suggests `/tmp` or `/var/folders/.../T/opencode`.

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
