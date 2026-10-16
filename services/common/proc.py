"""Run a command with a time limit, killing everything it started on timeout."""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal


async def run_process(
    cmd: str | list[str], *, cwd: str | None, env: dict | None, shell: bool, timeout: float
) -> tuple[int | None, str, str]:
    """Returns (returncode, stdout, stderr); returncode is None when it timed out.

    The command gets its own process group, and a timeout kills the group.
    Killing only the process left a shell command's children (a dev server,
    each stage of a pipeline) running with nothing to stop them.
    """
    kwargs = {"stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE,
              "cwd": cwd, "env": env, "start_new_session": True}
    if shell:
        proc = await asyncio.create_subprocess_shell(cmd if isinstance(cmd, str) else " ".join(cmd), **kwargs)
    else:
        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.wait()
        return None, "", ""
    return (proc.returncode if proc.returncode is not None else 0), stdout.decode(errors="replace"), stderr.decode(errors="replace")
