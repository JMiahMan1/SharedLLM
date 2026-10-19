# ADR 010: Tiered Inference Concurrency Control (TIER2/TIER3)

**Status:** Accepted (implemented; see "Current state" for what differs)\
**Date:** 2026-05-12, reviewed 2026-10-15\
**Supersedes:** ADR 003 (Tiered Inference Lock), the early proposal of the same split\
**Context:** Raven background worker monopolized inference, blocking Librarian

---

## Problem

The original `INFERENCE_LOCK` was a single global `asyncio.Lock()` that serialized _all_ LLM inference: both Librarian (fast, 1-2 step) and Raven (long-running, multi-iteration) jobs. This caused critical path blockage: while Raven was performing a 10-minute autonomous audit, simple queries like "turn on the lights" or "what's the weather" were queued behind it, despite needing only a single fast inference.

**Symptoms:**

* Librarian queries timed out or experienced multi-second latency during Raven jobs
* No ability to run any concurrent background processing
* System felt "locked" during autonomous operations

---

## Decision

Replace `INFERENCE_LOCK` with a tiered concurrency scheme in `services/gateway/messaging.py`:

```python
TIER2_SEMAPHORE = asyncio.Semaphore(3)   # Librarian: up to 3 concurrent jobs
TIER3_LOCK = asyncio.Lock()              # Raven: exclusive, single job at a time
```

**Tier Classification:**

* **Tier 2 (Librarian):** Non-autonomous queries. Fast-path tool use, general conversation, HA control. Acquires semaphore (up to 3 concurrent).
* **Tier 3 (Raven):** Autonomous/audit/repair work. Acquires the exclusive lock.

**Detection:** `RavenWorker._is_autonomous_job()` (`services/gateway/background_worker.py`):
a job is Tier 3 when it carries a `_mission_id`, when `is_raven_intent(query)`
says so, or when the user is `raven_admin` or the system identity.

**Why a semaphore and not unlimited concurrency (from ADR 003):** the lock
existed to protect the 8 GB card from concurrent model calls. Raven's
multi-turn runs (minutes long) need it exclusively; interactive single-turn
queries can share a small pool. ADR 003 proposed 4 slots; 3 shipped as the
safe starting point on 8 GB with 7-9B models.

---

## Consequences

**Positive:**

* Librarian can now serve up to 3 simultaneous background jobs, eliminating starvation during Raven runs.
* Raven maintains exclusive access to protect VRAM and avoid contention with itself (only one Raven job at a time is still enforced).
* System responsiveness preserved for interactive use cases.

**Negative:**

* Librarian jobs now compete for 3 slots; if all are occupied, they queue. This is acceptable because Librarian jobs are short-lived (typically <10s).
* Requires careful classification; mis-categorized jobs could cause unexpected contention. The dual signaling (query keywords + user_id) provides redundancy.

---

## Alternatives Considered

| Alternative | Reason Rejected |
| ----------- | --------------- |
| Keep single lock with timeout | Doesn't solve starvation; still serial |
| Make Raven queue-priority only | Librarian would still block on lock if Raven held it |
| Dynamic semaphore count based on VRAM | Adds complexity; 3 is a safe starting point empirically |

---

## Implementation Notes

* `messaging.py`: `TIER2_SEMAPHORE = asyncio.Semaphore(3)`, `TIER3_LOCK = asyncio.Lock()`.
* `background_worker.py`: `_inference_loop` claims jobs sequentially and runs
  each in its own task. `_process_inference_job` acquires the lock or the
  semaphore around `process_full_orchestration()`.
* Queued chat jobs get the user's recent turns attached by the worker before
  orchestration, and the completed turn is written back to the history store.
  Missions and Nextcloud Talk turns do not read the chat transcript.

## Current state (2026-10-18)

* `INFERENCE_LOCK` was **not** fully removed: `services/gateway/main.py` still
  defines it and holds it around the external-agent chat path
  (`run_external_agent`).
* The worker no longer processes jobs one at a time. It claims up to
  `MAX_INFLIGHT_INFERENCE_JOBS` jobs and dispatches each into its own task, so
  a running Raven mission holds only `TIER3_LOCK` while Tier 2 jobs fill
  `TIER2_SEMAPHORE`. Claims remain sequential, and Tier 3 jobs still wait on
  the lock, which keeps missions serialized.

**Related ADRs:**

* ADR 006 (Circuit Breaker): separate resilience work
* ADR 007 (Hard Timeout): Raven job timeout enforcement
