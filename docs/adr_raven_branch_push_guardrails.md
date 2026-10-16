# ADR: Raven Branch Push Guardrails

## Status

Accepted

## Context

Raven (SharedLLM's own autonomous coding agent) must be able to commit and
push autonomously so it can work like a real developer, but direct pushes to shared integration branches create too much
risk. The existing workspace workflow also allowed provider sync before
verification and did not return a review packet that could be reused for PR
creation or human review.

## Decision

- Raven may push autonomously only to non-protected branches.
- Protected branches are enforced in `workspace_runtime`, not only in prompts
  (`WORKSPACE_RUNTIME_PROTECTED_BRANCHES`, default
  `main,master,development,dev,release/*`).
- When Raven starts from a protected branch, the runtime may automatically
  create a review branch from the configured workspace `default_branch`
  (`create_review_branch()` in `services/workspace_runtime/git_ops.py`, prefix
  `raven` by default).
- `POST /workflow/write-sync-commit` must verify edits before push:
  write, lint, pytest, commit, push, then provider sync.
- Autonomous push requires targeted `pytest` coverage (`pytest_targets`; the
  request is refused without them).
- Workflow responses include PR-ready `review` metadata with branch, changed
  files, verification results, and reviewer checklist text.

## Consequences

- Raven can continue operating with low friction on `raven/*` or other review
  branches.
- Branch protection and PR review remain the merge boundary for `main` and
  `development`.
- Review packets become structured training material for Raven because the
  runtime now states what evidence a good code review handoff requires.
