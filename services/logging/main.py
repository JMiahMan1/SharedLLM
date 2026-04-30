# services/logging/main.py
import os
import sqlite3
import json
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="SOA Logging Service")

DB_PATH = "/app/data/logs.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
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

@app.post("/log")
async def add_log(entry: LogEntry):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO logs (service, level, message, context) VALUES (?, ?, ?, ?)",
        (entry.service, entry.level, entry.message, json.dumps(entry.context) if entry.context else None)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/logs")
async def get_logs(service: Optional[str] = None, limit: int = 100):
    conn = sqlite3.connect(DB_PATH)
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

@app.get("/health")
def health():
    return {"status": "ok", "service": "logging"}
