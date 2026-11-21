"""Synthetic opencode and Cline sessions in the shapes those tools store.

Only the structure is real (learned from the schemas); every value is made up.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

ROOT_DIR = "/work/SharedLLM"
MODEL = {"id": "nvidia/teacher-ultra", "providerID": "openrouter", "variant": "high"}


def _tool(name: str, inp: dict[str, Any], text: str, *, status: str = "completed", exit_code: int | None = None) -> dict[str, Any]:
    state: dict[str, Any] = {"status": status, "input": inp}
    if status == "error":
        state["error"] = {"type": "tool.execution", "message": text}
    else:
        state["content"] = [{"type": "text", "text": text}]
        state["metadata"] = {"truncated": False, **({"exit": exit_code} if exit_code is not None else {})}
    return {"type": "tool", "id": f"call-{name}", "name": name, "executed": True, "state": state}


def opencode_messages(extra_output: str = "") -> list[tuple[str, int, int, str]]:
    """Two tasks: a verified fix, then a correction that fails it... and a third, unverified."""
    rows: list[tuple[str, int, int, dict[str, Any]]] = [
        ("user", 1, 1_790_000_000_000, {"text": "Fix the off-by-one in the pager so the last page shows", "files": []}),
        ("assistant", 2, 1_790_000_001_000, {"model": MODEL, "content": [
            {"type": "reasoning", "text": "Look at the pager first."},
            _tool("read", {"path": f"{ROOT_DIR}/services/gateway/pager.py", "offset": 1, "limit": 80}, "def pages(n): ..."),
            _tool("grep", {"pattern": "def pages", "path": ROOT_DIR, "include": "*.py"}, "services/gateway/pager.py:1"),
        ]}),
        ("assistant", 3, 1_790_000_002_000, {"model": MODEL, "content": [
            _tool("edit", {"path": f"{ROOT_DIR}/services/gateway/pager.py", "oldString": "range(n - 1)", "newString": "range(n)"}, "Edit applied"),
            _tool("shell", {"command": "pytest services/gateway/tests/test_pager.py -q"}, "3 passed in 0.2s" + extra_output, exit_code=0),
            _tool("shell", {"command": "git commit -m \"fix(pager): show the last page\""}, "1 file changed", exit_code=0),
            {"type": "text", "text": "Fixed: the pager now includes the last page; tests pass."},
        ]}),
        ("idle", 4, 1_790_000_003_000, {"outcome": "completed"}),
        ("user", 5, 1_790_000_100_000, {"text": "Now make the page size configurable", "files": []}),
        ("assistant", 6, 1_790_000_101_000, {"model": MODEL, "content": [
            _tool("edit", {"path": f"{ROOT_DIR}/services/gateway/pager.py", "oldString": "SIZE = 20", "newString": "SIZE = int(os.getenv('PAGE_SIZE', '20'))"}, "Edit applied"),
            _tool("shell", {"command": "pytest services/gateway/tests/test_pager.py -q"}, "1 failed, 2 passed", exit_code=1),
            {"type": "text", "text": "Done."},
        ]}),
        ("user", 7, 1_790_000_200_000, {"text": "That didn't work, the tests still fail", "files": []}),
        ("assistant", 8, 1_790_000_201_000, {"model": MODEL, "content": [
            _tool("read", {"path": f"{ROOT_DIR}/docs/PAGER.md"}, "# Pager"),
            {"type": "text", "text": "The docs describe the pager."},
        ]}),
    ]
    return [(kind, seq, created, json.dumps(data)) for kind, seq, created, data in rows]


def make_opencode_db(path: pathlib.Path, *, session_id: str = "ses_synthetic01", extra_output: str = "",
                     messages: list[tuple[str, int, int, str]] | None = None) -> pathlib.Path:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session_v2 (id text primary key, project_id text, parent_id text, directory text, "
        "model text, time_created integer, time_updated integer)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session_message (id text primary key, session_id text, type text, seq integer, "
        "time_created integer, time_updated integer, data text)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO session_v2 VALUES (?, 'p', NULL, ?, ?, ?, ?)",
        (session_id, ROOT_DIR, json.dumps(MODEL), 1_790_000_000_000, 1_790_000_300_000),
    )
    conn.execute("DELETE FROM session_message WHERE session_id = ?", (session_id,))
    for kind, seq, created, data in messages if messages is not None else opencode_messages(extra_output):
        conn.execute(
            "INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"{session_id}-{seq}", session_id, kind, seq, created, created, data),
        )
    conn.commit()
    conn.close()
    return path


def cline_session(folder: pathlib.Path, session_id: str = "1790000000000_abcde") -> pathlib.Path:
    target = folder / session_id
    target.mkdir(parents=True, exist_ok=True)
    meta = {
        "session_id": session_id, "provider": "cline", "model": "deepseek/teacher-flash",
        "cwd": ROOT_DIR, "workspace_root": ROOT_DIR, "started_at": "2026-11-20T10:00:00.000Z",
    }
    messages = {"messages": [
        {"id": "1", "role": "user", "ts": 1_790_000_000_000, "content": [{"type": "text", "text": "Add a health check test for the pager"}]},
        {"id": "2", "role": "assistant", "content": [
            {"type": "thinking", "thinking": "Find the tests."},
            {"type": "tool_use", "id": "t1", "name": "search_codebase", "input": {"queries": ["test_pager"]}},
        ]},
        {"id": "3", "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "name": "search_codebase",
             "content": [{"query": "test_pager", "result": "services/gateway/tests/test_pager.py", "success": True}]},
        ]},
        {"id": "4", "role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "editor", "input": {
                "path": f"{ROOT_DIR}/services/gateway/tests/test_pager_health.py", "new_text": "def test_health():\n    assert True\n"}},
            {"type": "tool_use", "id": "t3", "name": "run_commands", "input": {"commands": ["pytest services/gateway/tests -q"]}},
        ]},
        {"id": "5", "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2", "name": "editor", "content": "created"},
            {"type": "tool_result", "tool_use_id": "t3", "name": "run_commands",
             "content": [{"query": "pytest", "result": "4 passed in 0.3s", "success": True}]},
        ]},
        {"id": "6", "role": "assistant", "content": [{"type": "text", "text": "Added test_pager_health.py; 4 passed."}]},
    ]}
    (target / f"{session_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    (target / f"{session_id}.messages.json").write_text(json.dumps(messages), encoding="utf-8")
    return target
