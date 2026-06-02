# ADR 008: Streaming-First Inference

## Status

Proposed

## Context

The `OllamaProvider.generate()` method already supports streaming (`stream=True`) and delivers chunks via `chunk_callback`. However, `AgentLoop` explicitly sets `"stream": False` in its payload (line 254), forcing Ollama to buffer the entire response before returning. For a 1000-token output, this means:

- VRAM holds full KV cache + full output
- Memory peak = input tokens + output tokens + KV cache
- No intermediate tokens available for early user feedback

With `stream=True`, tokens arrive incrementally, reducing peak VRAM (KV cache can be evicted incrementally) and enabling **progressive display** in the UI.

## Decision

Switch `AgentLoop` to streaming mode by default.

### Changes Required

1. **agent_loop.py:249-254** — Set `"stream": True`
2. **agent_loop.py:77-92 streaming path** — Already implemented; accumulates `full_content` and returns it.
3. **Background worker** — Already uses `chunk_callback` to push chunks to job-specific Redis list (`raven:job:chunks:{job_id}`). Maintain this behavior.
4. **Orchestrator** — When `chunk_callback` provided, forward chunks to caller via Server-Sent Events (SSE) or just accumulate for final result (current behavior).
5. **Gateway main.py `/api/chat` endpoint** — If request came from UI with `stream=true`, return `StreamingResponse` that yields chunks as they arrive from worker queue.

**Why not stream all the way to user?**
Current UI (`/services/ui`) buffers; but enabling streaming in gateway is forward-compatible. Keep existing non-stream path for backward compatibility.

### Additional Benefits

- **Lower VRAM footprint:** KV cache can discard past tokens sooner
- **Faster perceived latency:** first token ~500ms vs full response in 5s
- **Heartbeat correlation:** Each chunk can carry iteration counter

### Trade-offs

- ⚠️ Slightly more complex error handling (stream interruption mid-stream)
- ⚠️ Chunk ordering must be preserved when pushing to Redis list (current code is correct: `rpush` preserves order)
- ✅ Better user experience

## Validation

1. Enable streaming → monitor `nvidia-smi` VRAM during Raven job → should show ~15-20% lower peak
2. Verify chunks arrive in-order at UI (if streaming endpoint enabled)
3. Ensure backward compatibility: non-streaming calls still work

---

**Configuration Flag:** `RAVEN_STREAMING_ENABLED` (default: true)

**Future Work:** Adaptive streaming — if Ollama queue builds up, fall back to non-stream to avoid back-pressure on in-memory buffers.
