# AGENTS.md

## Temporary Directory Rule (ABSOLUTE — NO EXCEPTIONS, NEVER FORGET)

ALWAYS use `.tmp/` (relative to workspace root, i.e. `<workspace>/.tmp/`) for ALL temporary work.
NEVER use `/tmp`, `/var/folders/...`, or any other system temp directory — for ANY purpose, including:
`curl -o`, shell redirects (`>`, `>>`), heredocs, scratch files, test scripts, downloaded artifacts,
intermediate files, or temp files created inside `bash` commands.

Create `<workspace>/.tmp/` first if it does not exist. This rule overrides any tool default
that suggests `/tmp` or `/var/folders/.../T/opencode`.
