# ADR 004: Structured Request Tracing & Centralized Secret Sanitization

## Status

Proposed

## Context

Current logging is free-form string-based. Requests flowing through Gateway → Execution → WorkspaceRuntime generate logs that cannot be correlated across services. Additionally, secret redaction is ad-hoc:

* `agent_loop.py` manually redacts before logging tool payloads
* `workspace_runtime` redacts in some handlers but not all
* New code often forgets to sanitize

A single leaked log line containing `nextcloud_pass` or `github_token` is a critical security incident.

## Decision

### 1. Structured Logging Standard

All services must emit JSON-formatted logs with mandatory fields:

| Field | Description |
| --- | --- |
| `timestamp` | ISO 8601 UTC |
| `level` | `DEBUG/INFO/WARNING/ERROR` |
| `service` | `gateway&#124;execution&#124;workspace_runtime&#124;...` |
| `request_id` | UUID from Gateway entrypoint (propagated via header) |
| `job_id` | UUID for Raven jobs (else omitted) |
| `iteration` | AgentLoop iteration number (Raven only) |
| `user_id` | Resolved identity |
| `action` | Tool name or route handler |
| `duration_ms` | Time from request start to log emission |
| `status` | `success&#124;failure&#124;timeout&#124;blocked` |
| `error_code` | Optional machine-readable error tag |

**Implementation:**

* Create `services/common/logging_config.py` (shared module via volume mount)
* Configure `logging.config.dictConfig` with `python-json-logger` or custom formatter
* FastAPI middleware sets context vars: `current_request_id`, `current_job_id`, `current_user`
* Custom `logging.Filter` reads these and injects into every `LogRecord`

### 2. Centralized Sanitizer Module

**File:** `services/gateway/sanitizer.py` (mounted read-only to all services)

```python
import re
from typing import Any

# Keys known to contain secrets (case-insensitive substring match)
SECRET_KEYS = {
    "api_key", "token", "password", "secret", "key", "passwd",
    "ha_token", "nextcloud_pass", "github_token", "gitlab_token",
    "git_token", "fernet_key", "internal_secret"
}

# Regex patterns for tokens that may appear in unstructured text
TOKEN_PATTERNS = [
    re.compile(r'ghp_[a-zA-Z0-9]{36,}'),          # GitHub PAT
    re.compile(r'gho_[a-zA-Z0-9]{36,}'),          # GitHub OAuth
    re.compile(r'glpat-[a-zA-Z0-9\-]+'),           # GitLab PAT
    re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.eyJ'),     # JWT-ish
]

def sanitize_key(key: str, value: Any) -> Any:
    """Redact value if key matches secret pattern."""
    key_lower = key.lower()
    if any(secret in key_lower for secret in SECRET_KEYS):
        return "[REDACTED]"
    if isinstance(value, str):
        for pattern in TOKEN_PATTERNS:
            if pattern.search(value):
                return "[REDACTED]"
    return value

def sanitize_dict(data: dict) -> dict:
    """Recursively redact secrets in dict/list structure."""
    if not isinstance(data, dict):
        return data
    result = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = sanitize_dict(v)
        elif isinstance(v, list):
            result[k] = [sanitize_dict(i) if isinstance(i, dict) else sanitize_key(k, i) for i in v]
        else:
            result[k] = sanitize_key(k, v)
    return result
```

### 3. Enforced Sanitization Points

| Entry Point | Action |
| --- | --- |
| FastAPI response JSON serialization | Apply `sanitize_dict` to all response bodies (except for admin endpoints) |
| Incoming request body logs | Sanitize before any `log.info()` |
| Outbound HTTP calls to downstream services | Sanitize payload (except for known trusted recipients) |
| Exception traceback dumps | Redact any secret-like strings in traceback vars |
| Logging service write (`/log` endpoint) | Server-side re-sanitization regardless of caller claim |

### 4. Propagation Header

`X-Request-ID: <uuid>` flows from Gateway → Execution → WorkspaceRuntime → Storage.  
If header absent, Gateway generates it.

## Consequences

* ✅ Zero secret leakage from structured logs (assuming sanitizer coverage complete)
* ✅ End-to-end request tracing across service mesh
* ✅ Debugging complex Raven loops: all iterations share same `request_id`
* ⚠️ Minor CPU overhead (~0.5ms per dict sanitization)
* ⚠️ Need to audit all `log.info()` calls to ensure they use structured logging, not f-strings with dicts

## Validation

* Pen test: send request with `{"github_token":"ghp_FAKE123"}` in payload → grep all service logs → verify `[REDACTED]`
* Load test: 100 req/s → measure formatter overhead

---

**Related:** ADR 006 (Circuit Breaker) uses same tracing IDs.
