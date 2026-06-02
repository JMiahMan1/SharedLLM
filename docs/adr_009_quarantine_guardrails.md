# ADR 009: Workspace File Quarantine & Automated Guardrails

## Status

Proposed

## Context

Raven uses the Workspace Runtime's `write-sync-commit` workflow to edit code. This workflow:

1. Writes file
2. Lints the file
3. Runs targeted pytest (if targets provided)
4. Commits and optionally pushes

Currently, if lint fails but `push=True`, the code **still commits** (the lint result is returned but not enforced). This risks polluting branches with un-lintable code, triggering CI failures and human frustration.

Furthermore, repeatedly problematic files (e.g., a file that never passes flake8) consume computational resources without progress.

## Decision

Implement an **automated quarantine system** for workspace files that repeatedly fail verification.

### Quarantine Thresholds

Configurable via environment:

```python
RAVEN_QUARANTINE_THRESHOLD = int(os.getenv("RAVEN_QUARANTINE_THRESHOLD", "3"))
RAVEN_QUARANTINE_WINDOW_SECONDS = int(os.getenv("RAVEN_QUARANTINE_WINDOW", "600"))  # 10m
```

A file enters quarantine after `N` failures within the window.

### Tracking Storage

Use Redis hash: `raven:quarantine:<workspace_id>`
Fields: `file_path -> {"failures": 3, "last_failed_at": 1712345678}`

TTL: auto-expire after `RAVEN_QUARANTINE_WINDOW_SECONDS + 60` to allow natural decay.

### Enforcement Points

1. **At workflow start (`/workflow/write-sync-commit`):**

```python
# Check if file is quarantined
quarantine_key = f"raven:quarantine:{workspace_id}"
quarantined = await redis.hget(quarantine_key, relative_path)
if quarantined:
    data = json.loads(quarantined)
    if data["failures"] >= RAVEN_QUARANTINE_THRESHOLD:
        raise HTTPException(
            status_code=409,
            detail=f"File {relative_path} is quarantined after {data['failures']} recent failures"
        )
```

1. **After lint/pytest run (if either fails):**

```python
# Increment failure counter
current = await redis.hincrby(quarantine_key, f"{relative_path}.failures", 1)
await redis.hset(quarantine_key, f"{relative_path}.last_failed_at", time.time())
await redis.expire(quarantine_key, RAVEN_QUARANTINE_WINDOW_SECONDS + 60)
```

1. **On lint/pytest success:**
   Reset counter to 0 (optional — conservative approach keeps failure count forever until window expiry).

### Admin Override

Admin users can clear quarantine via:

- `DELETE /workflow/quarantine/{workspace_id}/{file_path}` (internal secret required)
- Direct Redis deletion (emergency)

### Metrics

```python
workspace_quarantined_files_total{workspace_id="..."} 2
workspace_quarantine_clear_total{by="admin"} 1
```

### User Experience

When Raven attempts to edit a quarantined file:

```http
HTTP 409 Conflict
{
  "detail": "File services/gateway/agent_loop.py is quarantined after 3 failures in the last 10 minutes. Recent errors: flake8: E302 expected 2 blank lines, found 1"
}
```

Raven will interpret this as a hard failure and attempt alternative approaches (e.g., read file again, use different edit strategy) or escalate to human.

## Consequences

- ✅ Prevents hammering on broken files
- ✅ Forces human review when automated fix repeatedly fails
- ✅ Self-healing: window expiry auto-clears if issue fixed elsewhere
- ⚠️ False positives possible if flaky test; window mitigates
- ⚠️ Raven may need to edit different file to work around quarantine — monitor for workarounds

## Validation

1. Configure threshold=2, window=60s
2. Submit two bad lint edits to same file → third attempt rejected with 409
3. Wait 65s → third attempt succeeds (counter expired)
4. Admin clears → immediate retry allowed

---

**Related:** ADR 002 (Branch Push Guardrails) already prevents pushing to protected branches; this ADR prevents commit creation itself when quality gates fail.
