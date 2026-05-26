# services/logging/main.py
import os
import sys
import json
import re
import time
import asyncio
sys.path.insert(0, os.path.dirname(__file__))

from config import INTERNAL_SECRET, REDIS_URL, LOG_RETENTION_DAYS, LOG_MAX_ENTRIES
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, List, Optional

import redis.asyncio as redis
from fastapi import FastAPI, Request, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import logging as py_logging
import traceback

_redis_client: Optional[redis.Redis] = None

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
    # Resolve runtime config from Identity service
    from config import resolve_runtime_config
    await resolve_runtime_config()
    
    py_logging.info(f"[Logging] Redis backend initialized (retention={LOG_RETENTION_DAYS}d, max_entries={LOG_MAX_ENTRIES})")
    task = asyncio.create_task(retention_cleanup_task())
    yield
    task.cancel()

app = FastAPI(title="SOA Logging Service", lifespan=lifespan)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Logging Service Error: {type(exc).__name__}: {str(exc)}"
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

# Redis client (initialized in lifespan)
_redis_client: Optional[redis.Redis] = None

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

def sanitize_log_payload(value: Any, parent_key: Optional[str] = None) -> Any:
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

def _require_internal_secret(x_internal_secret: Optional[str]) -> None:
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

def _resolve_limit(limit: Optional[int], lines: Optional[int], default: int = 100) -> int:
    value = lines if lines is not None else limit
    if value is None:
        return default
    return max(1, min(int(value), 5000))

class LogEntry(BaseModel):
    user_id: str = "system"
    service: str
    level: str = "INFO"
    message: str
    context: Optional[dict] = None

async def _fetch_logs(
    service: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 100,
):
    r = await get_redis()
    cutoff = time.time() - (LOG_RETENTION_DAYS * 86400)
    
    # Fetch all entries within retention window (sorted by timestamp desc)
    entries = await r.zrevrangebyscore("logs:entries", "+inf", cutoff, start=0, num=limit * 10)
    
    results = []
    for entry_json in entries:
        try:
            entry = json.loads(entry_json)
            # Apply filters
            if service and entry.get("service") != service:
                continue
            if user_id and user_id != "admin" and entry.get("user_id") != user_id:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        except json.JSONDecodeError:
            continue
    
    return results

@app.get("/logs")
async def get_logs(user_id: Optional[str] = None, service: Optional[str] = None, limit: Optional[int] = None, lines: Optional[int] = None):
    return await _fetch_logs(service=service, user_id=user_id, limit=_resolve_limit(limit, lines))

@app.get("/api/logs")
async def get_logs_api(user_id: Optional[str] = None, service: Optional[str] = None, limit: Optional[int] = None, lines: Optional[int] = None):
    return await _fetch_logs(service=service, user_id=user_id, limit=_resolve_limit(limit, lines))

@app.get("/api/admin/logs")
async def get_logs_admin_api(service: Optional[str] = None, limit: Optional[int] = None, lines: Optional[int] = None):
    return await _fetch_logs(service=service, user_id="admin", limit=_resolve_limit(limit, lines))

@app.delete("/api/logs")
async def clear_logs_api(x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    r = await get_redis()
    await r.delete("logs:entries")
    return {"status": "success", "message": "Logs cleared"}

@app.delete("/api/admin/logs")
async def clear_logs_admin_api(x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    r = await get_redis()
    await r.delete("logs:entries")
    return {"status": "success", "message": "Logs cleared"}

@app.post("/log")
@app.post("/logs")
@app.post("/api/logs")
async def log_event(entry: LogEntry, x_internal_secret: Optional[str] = Header(default=None)):
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
active_ws: List[WebSocket] = []

async def _ws_handler(websocket: WebSocket):
    await websocket.accept()
    active_ws.append(websocket)
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("logs:stream")
    
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    await websocket.send_text(message["data"])
                except Exception:
                    break
            elif message["type"] == "ping":
                try:
                    await websocket.send_text(json.dumps({"ping": True}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe("logs:stream")
        await pubsub.close()
        if websocket in active_ws:
            active_ws.remove(websocket)

@app.websocket("/api/logs/stream")
async def websocket_endpoint(websocket: WebSocket):
    await _ws_handler(websocket)

@app.websocket("/logs/stream")
async def websocket_endpoint_direct(websocket: WebSocket):
    await _ws_handler(websocket)

@app.get("/health")
def health():
    return {"status": "ok", "service": "logging"}
