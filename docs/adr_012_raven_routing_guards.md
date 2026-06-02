# ADR 012: Raven Routing Guards (Fast-Path Bypass)

**Status:** Accepted  
**Date:** 2026-05-12  
**Authors:** Kilo (AI Architect)  
**Context:** Raven queries were incorrectly routed through direct code orchestration fast-path, bypassing AgentLoop

---

## Problem

The chat handler (`main.py:1694`) performs fast-path routing for quick responses. Two detectors were unintentionally capturing Raven queries:

1. `wants_workspace_readme_generation()` — matched "readme" workspace tasks
2. `wants_direct_code_orchestration()` — matched file edit/create intents

When a Raven query (e.g., "Raven: audit services/gateway/ and fix bugs") triggered these detectors, it was routed directly to `orchestrate_code_change()` via the **Librarian** fast-path instead of being enqueued for the **Raven** background worker and `AgentLoop`.

**Impact:**

* Raven lost access to its autonomous multi-iteration loop and tool streaming
* The coding model was called in a single-shot mode with strict JSON grammar, causing "Invalid JSON plan" errors
* Self-repair and audit workloads failed immediately

---

## Decision

Add explicit Raven bypass guards to both fast-path detectors:

```python
def wants_direct_code_orchestration(query: str) -> bool:
    q = (query or "").strip().lower()
    if "raven" in q:           # ← NEW: Route Raven through AgentLoop
        return False
    # ... existing logic

def wants_workspace_readme_generation(query: str) -> bool:
    q = (query or "").strip().lower()
    if "raven" in q:           # ← NEW: Prevent Raven fast-path hijack
        return False
    # ... existing logic
```

**Location:** `services/gateway/main.py:859–864` and `850–857`

---

## Consequences

**Positive:**

* Raven queries now consistently enqueue via the background worker (`job_queue.enqueue_job()`)
* Full `AgentLoop` control flow restored: multi-iteration, tool call/execute cycles, heartbeat, hard timeout
* Self-repair and audit workflows operational again

**Negative:**

* Adds a small conditional branch to fast-path detectors
* Requires maintainers to remember to add `"raven" in q` guard to any future fast-path shortcuts

---

## Alternatives Considered

| Alternative | Reason Rejected |
| ------------- | --------------- |
| Route all code edits through Raven by default | Would break existing UI quick-edit UX; Librarian fast-path needs to stay for simple edits |
| Detect autonomous signals instead of string match | More robust but complex for guards; string check is sufficient and explicit |
| Remove fast-path code orchestration entirely | Too disruptive; Librarian's single-turn code edits are valuable |

---

## Implementation Notes

* The guard should be placed **before** any other detection logic to short-circuit early.
* Consider extracting a helper `is_raven_query(query)` that checks both `"raven"` keyword and potential `user_id == "raven_admin"` if routing ever moves to a separate decision point.
