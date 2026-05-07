# services/logging/main.py
import os
import sqlite3
import json
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="SOA Logging Service")

from fastapi.responses import JSONResponse
import traceback
import logging as py_logging

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Logging Service Error: {type(exc).__name__}: {str(exc)}"
    py_logging.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal Logging Error", "detail": str(exc)}
    )

DB_PATH = "/app/data/logs.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            service TEXT,
            level TEXT,
            message TEXT,
            context TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

class LogEntry(BaseModel):
    service: str
    level: str = "INFO"
    message: str
    context: Optional[dict] = None

async def _fetch_logs(service: Optional[str] = None, limit: int = 100):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM logs"
    params = []
    if service:
        query += " WHERE service = ?"
        params.append(service)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _resolve_limit(limit: Optional[int], lines: Optional[int], default: int = 100) -> int:
    """Support both legacy `lines` and current `limit` query params."""
    value = lines if lines is not None else limit
    if value is None:
        return default
    return max(1, min(int(value), 5000))

# Direct service path (used internally)
@app.get("/logs")
async def get_logs(service: Optional[str] = None, limit: Optional[int] = None, lines: Optional[int] = None):
    return await _fetch_logs(service=service, limit=_resolve_limit(limit, lines))

# /api/logs path — Caddy routes /api/logs* directly to logging:8006
# so the logging service must handle the /api/logs path itself.
@app.get("/api/logs")
async def get_logs_api(service: Optional[str] = None, limit: Optional[int] = None, lines: Optional[int] = None):
    return await _fetch_logs(service=service, limit=_resolve_limit(limit, lines))


@app.get("/api/admin/logs")
async def get_logs_admin_api(service: Optional[str] = None, limit: Optional[int] = None, lines: Optional[int] = None):
    return await _fetch_logs(service=service, limit=_resolve_limit(limit, lines))

@app.delete("/api/logs")
async def clear_logs_api():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("DELETE FROM logs")
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Logs cleared"}

@app.delete("/api/admin/logs")
async def clear_logs_admin_api():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("DELETE FROM logs")
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Logs cleared"}

# --- WebSocket Streaming ---
from fastapi import WebSocket, WebSocketDisconnect
import asyncio

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

async def _ws_handler(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(10)
            await websocket.send_text(json.dumps({"ping": True}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Caddy routes /api/logs/stream → logging:8006 so we handle it here
@app.websocket("/api/logs/stream")
async def websocket_endpoint(websocket: WebSocket):
    await _ws_handler(websocket)

# Also handle /logs/stream for any direct calls
@app.websocket("/logs/stream")
async def websocket_endpoint_direct(websocket: WebSocket):
    await _ws_handler(websocket)

# Update add_log to broadcast
@app.post("/log")
async def add_log(entry: LogEntry):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_dict = {
        "timestamp": now,
        "service": entry.service,
        "level": entry.level,
        "message": entry.message,
        "context": entry.context
    }
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(
        "INSERT INTO logs (timestamp, service, level, message, context) VALUES (?, ?, ?, ?, ?)",
        (now, entry.service, entry.level, entry.message, json.dumps(entry.context) if entry.context else None)
    )
    conn.commit()
    conn.close()
    
    # Broadcast to websocket clients
    await manager.broadcast(json.dumps(log_dict))
    
    return {"status": "success"}

@app.get("/health")
def health():
    return {"status": "ok", "service": "logging"}
