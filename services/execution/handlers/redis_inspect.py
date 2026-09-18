# services/execution/handlers/redis_inspect.py
"""Read-only Redis inspection for Raven self-tests and diagnostics.

Workspace containers cannot reach Redis (no client, no route), so missions
that must validate Redis-backed lessons (redis-chat:*, redis-logs:*,
redis-job:*) had no sanctioned path. This handler runs server-side with the
service's own Redis client and exposes a small read-only surface:

- ping: connectivity check
- get: fetch one key (value truncated to 500 chars)
- keys: list keys matching a pattern (capped at 50, bare "*" refused)
- ttl: remaining TTL of a key

Deliberately no write operations (no set/del/flush/expire).
"""
import logging
import os

from services.execution.schemas import ExecutionResult, RedisInspectRequest

log = logging.getLogger("execution.redis_inspect")

MAX_KEYS = 50
MAX_VALUE_CHARS = 500


def _redis_url() -> str:
    return (
        os.getenv("REDIS_URL")
        or os.getenv("HOST_REDIS_URL")
        or os.getenv("BRIDGE_REDIS_URL")
        or "redis://redis:6379/0"
    )


async def handle_redis_inspect(req: RedisInspectRequest) -> ExecutionResult:
    action = (req.action or "ping").lower()
    try:
        from redis.asyncio import Redis
    except ImportError:
        return ExecutionResult(status="FAILURE", message="redis library not installed.", service="redis_inspect")
    client = Redis.from_url(_redis_url())
    try:
        if action == "ping":
            ok = await client.ping()
            return ExecutionResult(status="SUCCESS", message=f"Redis ping: {ok}.", service="redis_inspect")
        if action == "get":
            if not req.key:
                return ExecutionResult(status="FAILURE", message="key required for get action.", service="redis_inspect")
            val = await client.get(req.key)
            if val is None:
                return ExecutionResult(status="SUCCESS", message=f"Key '{req.key}' not found (nil).", service="redis_inspect")
            text = val.decode("utf-8", errors="replace") if isinstance(val, bytes) else str(val)
            return ExecutionResult(
                status="SUCCESS",
                message=f"Key '{req.key}' = {text[:MAX_VALUE_CHARS]}",
                service="redis_inspect",
            )
        if action == "keys":
            pattern = req.pattern or ""
            if not pattern or pattern.strip() in ("*", "keys *"):
                return ExecutionResult(status="FAILURE", message="Refusing unbounded key scan; provide a prefixed pattern (e.g. 'redis-chat:*').", service="redis_inspect")
            found: list[str] = []
            async for k in client.scan_iter(match=pattern, count=100):
                found.append(k.decode("utf-8", errors="replace") if isinstance(k, bytes) else str(k))
                if len(found) >= MAX_KEYS:
                    break
            return ExecutionResult(
                status="SUCCESS",
                message=f"{len(found)} key(s) matching '{pattern}': {', '.join(found)}",
                service="redis_inspect",
            )
        if action == "ttl":
            if not req.key:
                return ExecutionResult(status="FAILURE", message="key required for ttl action.", service="redis_inspect")
            ttl = await client.ttl(req.key)
            return ExecutionResult(status="SUCCESS", message=f"TTL for '{req.key}': {ttl}s.", service="redis_inspect")
        return ExecutionResult(status="FAILURE", message=f"Unknown action '{req.action}'. Use ping|get|keys|ttl.", service="redis_inspect")
    except Exception as e:
        log.error(f"Redis inspect failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e)[:300], service="redis_inspect")
    finally:
        try:
            await client.close()
        except Exception:
            pass
