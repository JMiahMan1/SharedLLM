# ADR 010: Tiered Inference Concurrency Control (TIER2/TIER3)

**Status:** Accepted  
**Date:** 2026-05-12  
**Authors:** Kilo (AI Architect)  
**Context:** Raven background worker monopolized inference, blocking Librarian

---

## Problem

The original `INFERENCE_LOCK` was a single global `asyncio.Lock()` that serialized _all_ LLM inference — both Librarian (fast, 1–2 step) and Raven (long-running, multi-iteration) jobs. This caused critical path blockage: while Raven was performing a 10-minute autonomous audit, simple queries like "turn on the lights" or "what's the weather" were queued behind it, despite needing only a single fast inference.

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
* **Tier 3 (Raven):** Autonomous/audit/repair queries. Contains `"raven"` in query or user_id `raven_admin`. Acquires exclusive lock.

**Detection:** `RavenWorker._is_autonomous_job()` mirrors the `autonomy_signals` list from `orchestrator.py` to ensure consistent classification.

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

* `background_worker.py`: Acquires lock/semaphore around `process_full_orchestration()` call (lines 112–126).
* `INFERENCE_LOCK` removed from `main.py`, `agent_loop.py`, `history.py`.
* `messaging.py`: Replaced `INFERENCE_LOCK` with tiered primitives.
* All imports updated accordingly.

**Related ADRs:**

* ADR 003 (Tiered Inference Lock) – early proposal
* ADR 006 (Circuit Breaker) – separate resilience work
* ADR 007 (Hard Timeout) – Raven job timeout enforcement
