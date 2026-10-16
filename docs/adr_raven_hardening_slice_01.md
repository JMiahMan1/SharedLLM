# ADR: Raven Hardening Slice 01

## Status

Accepted. **Superseded in part:** item 3 below (restricting Raven to read-only
shell/git) was reversed on 2026-05-11 (`081c5ba7`, "enable autonomous git
operations for Raven"); Raven now runs shell and git writes inside its sandboxed
workspaces under the capability policy.

## Context

Raven already has a useful autonomous loop, but several failure modes remained:

* queued jobs could be lost if a worker died mid-run
* log writes depended on caller-side redaction and lacked service-side enforcement
* the legacy execution layer still exposed unsafe shell and git mutation paths

These gaps made long-running repair tasks fragile and increased the risk of secret leakage or unsafe autonomous edits.

## Decision

We will harden the current architecture in-place with three immediate controls:

1. Raven jobs use Redis lease-based processing with recovery and dead-letter behavior (`InferenceJobQueue` in `services/gateway/messaging.py`).
2. The logging service sanitizes secrets server-side (`sanitize_log_payload` in `services/logging/main.py`) and requires the internal secret for log writes and clears.
3. The legacy execution layer blocks mutating shell commands and blocks autonomous git mutation workflows in favor of the workspace runtime review workflow.

## Consequences

* Raven jobs are now recoverable after worker interruption instead of silently disappearing.
* Secret scrubbing is enforced at the logging boundary even if an upstream caller forgets to redact.
* Autonomous self-editing is pushed toward the safer workspace runtime path that already requires linting, pytest, and review-branch discipline.
* Some older autonomous flows that relied on direct `git` or arbitrary shell mutation will now fail fast and must migrate to the workspace runtime workflow.
