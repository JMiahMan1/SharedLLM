# services/logging/main.py
import asyncio
import json
import logging as py_logging
import os
import re
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import aiohttp
import redis.asyncio as redis
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, LOG_MAX_ENTRIES, LOG_RETENTION_DAYS, REDIS_URL
from services.shared.info_endpoint import info_router
from services.common.secrets import secret_matches

_redis_client: redis.Redis | None = None

async def retention_cleanup_task():
    """Periodically remove logs older than LOG_RETENTION_DAYS."""
    while True:
        try:
            r = await get_redis()
            cutoff = time.time() - (LOG_RETENTION_DAYS * 86400)
            removed = await r.zremrangebyscore("logs:entries", 0, cutoff)
            if removed > 0:
                py_logging.info(f"[Retention] Purged {removed} logs older than {LOG_RETENTION_DAYS} days")
        except Exception as e:
            py_logging.error(f"[Retention] Cleanup error: {e}")
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Resolve runtime config from Identity service. The limits were imported
    # by value before this ran, so take the resolved ones (log_retention_days /
    # log_max_entries settings) or the Identity settings would never apply.
    global LOG_RETENTION_DAYS, LOG_MAX_ENTRIES
    from services import config as runtime_config
    await runtime_config.resolve_runtime_config()
    LOG_RETENTION_DAYS = runtime_config.LOG_RETENTION_DAYS
    LOG_MAX_ENTRIES = runtime_config.LOG_MAX_ENTRIES

    py_logging.info(f"[Logging] Redis backend initialized (retention={LOG_RETENTION_DAYS}d, max_entries={LOG_MAX_ENTRIES})")
    task = asyncio.create_task(retention_cleanup_task())
    yield
    task.cancel()

app = FastAPI(title="SOA Logging Service", lifespan=lifespan)

app.include_router(info_router)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Logging Service Error: {type(exc).__name__}: {exc!s}"
    py_logging.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal Logging Error", "detail": str(exc)}
    )

MAX_LOG_FIELD_LENGTH = 4000
SECRET_FIELD_NAMES = {
    "api_key", "authorization", "cookie", "github_token", "gitlab_token",
    "git_token", "ha_token", "nextcloud_pass", "password", "secret", "token",
}
SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"\bghp_[A-Za-z0-9]+\b"),
    re.compile(r"\bglpat-[A-Za-z0-9\-_]+\b"),
]

async def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client

def _sanitize_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    sanitized = value
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    if len(sanitized) > MAX_LOG_FIELD_LENGTH:
        return sanitized[:MAX_LOG_FIELD_LENGTH] + "...[TRUNCATED]"
    return sanitized

def sanitize_log_payload(value: Any, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, inner_value in value.items():
            key_lower = str(key).lower()
            if key_lower in SECRET_FIELD_NAMES:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_log_payload(inner_value, parent_key=key_lower)
        return sanitized
    if isinstance(value, list):
        return [sanitize_log_payload(item, parent_key=parent_key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_log_payload(item, parent_key=parent_key) for item in value]
    return _sanitize_scalar(value)


def _resolve_limit(limit: int | None, lines: int | None, default: int = 100) -> int:
    value = lines if lines is not None else limit
    if value is None:
        return default
    return max(1, min(int(value), 5000))

class LogEntry(BaseModel):
    user_id: str = "system"
    service: str
    level: str = "INFO"
    message: str
    context: dict | None = None

def _parse_levels(levels: str | None) -> set[str]:
    """``"warning,error"`` -> ``{"WARNING", "WARN", "ERROR"}``; empty means any."""
    wanted = {part.strip().upper() for part in (levels or "").split(",") if part.strip()}
    if "WARNING" in wanted or "WARN" in wanted:
        wanted |= {"WARNING", "WARN"}
    return wanted


async def _fetch_logs(
    service: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    levels: str | None = None,
):
    r = await get_redis()
    cutoff = time.time() - (LOG_RETENTION_DAYS * 86400)
    wanted_levels = _parse_levels(levels)

    # Fetch entries within the retention window, newest first. A level filter
    # scans the whole buffer: warnings are rare among the request lines, and a
    # 10x window would only ever see the last few minutes.
    scan = LOG_MAX_ENTRIES if wanted_levels else limit * 10
    entries = await r.zrevrangebyscore("logs:entries", "+inf", cutoff, start=0, num=scan)

    results = []
    for entry_json in entries:
        try:
            entry = json.loads(entry_json)
            # Apply filters
            if service and entry.get("service") != service:
                continue
            if user_id and user_id != "admin" and entry.get("user_id") != user_id:
                continue
            if wanted_levels and str(entry.get("level", "")).upper() not in wanted_levels:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        except json.JSONDecodeError:
            continue

    return results

async def _token_is_admin_api_key(token: str) -> bool:
    """Is a browser-supplied API key an administrator's?

    Asks Identity's validate-api-key, which has no fallback. This used
    /api/resolve, which answers with the default (admin) account when nothing
    matches, so any string at all opened the stream of every service's logs.
    The logs are the whole system's, so only an admin may read them, as on
    the HTTP routes.
    """
    try:
        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/internal/validate-api-key",
                # A header, not a query parameter: URLs land in access logs.
                headers={"X-Internal-Secret": INTERNAL_SECRET, "X-Api-Key": token},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status == 200:
                return bool((await resp.json()).get("is_admin"))
    except Exception as e:
        py_logging.warning(f"[logs] API key validation failed: {e}")
    return False

def _require_internal_secret(x_internal_secret: str | None) -> None:
    """Every HTTP route is service-to-service: reads expose every user's entries."""
    if not x_internal_secret or not secret_matches(x_internal_secret, INTERNAL_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")

@app.get("/logs")
@app.get("/api/logs")
async def get_logs(user_id: str | None = None, service: str | None = None, limit: int | None = None, lines: int | None = None, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    return await _fetch_logs(service=service, user_id=user_id, limit=_resolve_limit(limit, lines))

@app.get("/api/admin/logs")
async def get_logs_admin_api(service: str | None = None, limit: int | None = None, lines: int | None = None, level: str | None = None, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    return await _fetch_logs(service=service, user_id="admin", limit=_resolve_limit(limit, lines), levels=level)

@app.delete("/api/logs")
@app.delete("/api/admin/logs")
async def clear_logs(x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    r = await get_redis()
    await r.delete("logs:entries")
    return {"status": "success", "message": "Logs cleared"}

@app.post("/log")
@app.post("/logs")
@app.post("/api/logs")
async def log_event(entry: LogEntry, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    sanitized_message = sanitize_log_payload(entry.message)
    sanitized_context = sanitize_log_payload(entry.context or {})

    now = time.time()
    log_dict = {
        "user_id": entry.user_id,
        "timestamp": datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S'),
        "service": entry.service,
        "level": entry.level,
        "message": sanitized_message,
        "context": sanitized_context,
        "_ts": now,
    }

    r = await get_redis()

    # Store in sorted set (score = timestamp for range queries and retention)
    await r.zadd("logs:entries", {json.dumps(log_dict): now})

    # Enforce max entries (trim oldest)
    await r.zremrangebyrank("logs:entries", 0, -LOG_MAX_ENTRIES - 1)

    # Broadcast via PubSub for WebSocket streaming
    broadcast_payload = {k: v for k, v in log_dict.items() if k != "_ts"}
    await r.publish("logs:stream", json.dumps(broadcast_payload))

    return {"status": "success"}

# --- WebSocket Streaming via Redis PubSub ---
async def _ws_authorized(websocket: WebSocket) -> bool:
    """Accept the shared internal secret or an admin's API key (?token=...).

    Browsers cannot set custom headers on WebSocket connections, so the
    stream is authenticated via the query token.
    """
    token = websocket.query_params.get("token") or ""
    if not token:
        return False
    if secret_matches(token, INTERNAL_SECRET):
        return True
    return await _token_is_admin_api_key(token)

async def _forward_logs(websocket: WebSocket, pubsub, send_lock: asyncio.Lock):
    async for message in pubsub.listen():
        if message["type"] == "message":
            async with send_lock:
                await websocket.send_text(message["data"])


async def _answer_pings(websocket: WebSocket, send_lock: asyncio.Lock):
    """Reply to the client's heartbeat (lib/wsManager.ts sends {"type":"ping"}
    and reconnects when nothing comes back), and notice the client leaving
    even when no log line is being sent."""
    while True:
        text = await websocket.receive_text()
        try:
            is_ping = json.loads(text).get("type") == "ping"
        except (ValueError, AttributeError):
            is_ping = False
        if is_ping:
            async with send_lock:
                await websocket.send_text(json.dumps({"ping": True}))


async def _ws_handler(websocket: WebSocket):
    if not await _ws_authorized(websocket):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("logs:stream")
    send_lock = asyncio.Lock()
    tasks = [
        asyncio.create_task(_forward_logs(websocket, pubsub, send_lock)),
        asyncio.create_task(_answer_pings(websocket, send_lock)),
    ]
    try:
        # Whichever ends first (client gone, send failed) ends the stream.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pubsub.unsubscribe("logs:stream")
        await pubsub.close()

@app.websocket("/api/logs/stream")
@app.websocket("/logs/stream")
async def websocket_endpoint(websocket: WebSocket):
    await _ws_handler(websocket)

START_TIME = time.time()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "logging",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME
    }
