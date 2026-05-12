# ADR 003: Tiered Inference Locking

## Status
Proposed

## Context
Raven's `AgentLoop` currently holds a global `INFERENCE_LOCK` (asyncio.Lock) for its entire multi-turn execution, which can last 5-30 minutes. Librarian queries (standard chat) also indirectly acquire this lock via `call_ollama`. This creates a single point of contention: **while Raven is debugging, no one can turn on a light or ask a simple question**.

The lock exists to protect 8GB VRAM from concurrent Ollama requests. However, we can differentiate between:
- **Autonomous (Raven):** long-running, needs exclusive GPU
- **Interactive (Librarian):** short single-turn, can share a pool

## Decision
Replace the single `INFERENCE_LOCK` with two distinct synchronization primitives:

1. **`INFERENCE_LOCK_RAVEN`** = `asyncio.Lock()` — exclusive, one Raven job at a time
2. **`INFERENCE_LOCK_LIBRARIAN`** = `asyncio.Semaphore(4)` — up to 4 concurrent librarian queries

**Allocation Logic:**

```python
# gateway/main.py
INFERENCE_LOCK_RAVEN = asyncio.Lock()
INFERENCE_LOCK_LIBRARIAN = asyncio.Semaphore(4)

def get_lock(is_autonomous: bool):
    return INFERENCE_LOCK_RAVEN if is_autonomous else INFERENCE_LOCK_LIBRARIAN

# Usage
lock = get_lock(is_autonomous)
async with lock:
    # inference call
```

**Rationale:**
- Raven tasks remain isolated (GPU contention avoided)
- Librarian queries get 4× parallelism, improving UI responsiveness
- Semaphore allows controlled oversubscription; 4 is safe for 8GB VRAM with 7B/9B models (each ~5-8GB peak)
- Back-pressure via semaphore acquisition; if all 4 slots busy, request queues (already at FastAPI/Uvicorn level)

## Consequences

- ✅ Librarian latency drops from potentially minutes to seconds during Raven debugging sessions
- ✅ System maintains core functionality (lighting, climate) while Raven works
- ⚠️ Increased peak VRAM usage if 4 librarian jobs overlap with Raven (unlikely, Raven holds exclusive lock)
- ⚠️ Requires update to health check: monitor `INFERENCE_LOCK_LIBRARIAN._value` (available slots)

## Validation

- Load test: 10 concurrent librarian requests with 1 Raven job → median librarian latency < 500ms
- Monitor VRAM: `nvidia-smi` logs show no OOM during mixed load

---

**Related:** ADR 008 (Streaming-First Inference) complements this by reducing per-request memory footprint.
