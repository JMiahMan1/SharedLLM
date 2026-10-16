"""Slow commands run off the event loop, so one slow command does not stall
every other request the execution service is serving."""
import asyncio
import subprocess
import time
from types import SimpleNamespace

from services.execution.handlers import diagnostics


async def test_a_slow_command_does_not_block_other_requests(monkeypatch):
    running = {"now": False}

    def slow_run(*args, **kwargs):
        running["now"] = True
        time.sleep(0.5)
        running["now"] = False
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", slow_run)
    command = asyncio.create_task(diagnostics.handle_get_system_logs({"action": "ls", "path": "/tmp"}))
    progressed_while_running = 0
    for _ in range(10):
        await asyncio.sleep(0.02)
        progressed_while_running += running["now"]
    await command
    # Blocked, nothing else would run until the command had finished.
    assert progressed_while_running >= 5
