# services/execution/handlers/code_search.py
"""CodeSearch handler - GitHub/GitLab code search."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

try:
    from schemas import ExecutionResult, CodeSearchRequest
except ImportError:
    from ..schemas import ExecutionResult, CodeSearchRequest

log = logging.getLogger("execution.code_search")

SEARCH_SCRIPT = Path(__file__).resolve().parent.parent.parent.parent / "tools" / "code_search.py"


async def handle_code_search(req: CodeSearchRequest) -> ExecutionResult:
    """Execute the code_search CLI tool with the provided query and sources."""
    log.info(f"[code_search] query='{req.query}' sources={req.sources} language={req.language}")

    cmd = [
        sys.executable,
        str(SEARCH_SCRIPT),
        "--query", req.query,
    ]

    # Add sources
    cmd.extend(["--sources"] + req.sources)

    # Optional language filter
    if req.language:
        cmd.extend(["--language", req.language])

    # Optional owner/repo filters
    if req.owner:
        cmd.extend(["--owner", req.owner])
    if req.repo:
        cmd.extend(["--repo", req.repo])

    # Max results
    cmd.extend(["--max-results", str(req.max_results)])

    # Optional output file
    if req.output_file:
        cmd.extend(["--output", req.output_file])

    log.info(f"[code_search] running: {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        output_text = stdout.decode(errors="replace").strip()
        error_text = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            log.error(f"[code_search] command failed (rc={proc.returncode}): {error_text[:500]}")
            return ExecutionResult(
                status="FAILURE",
                message=f"Code search command failed: {error_text[:500]}",
                service="code_search",
                detail={"returncode": proc.returncode, "stderr": error_text[:2000]},
            )

        if not output_text:
            return ExecutionResult(
                status="SUCCESS",
                message=f"Code search completed for '{req.query}'. No output captured.",
                service="code_search",
            )

        # Extract summary from output
        lines = output_text.split("\n")
        start_idx = 0
        for i, line in enumerate(lines):
            if "QUERY:" in line:
                start_idx = i
                break

        summary = output_text[start_idx:start_idx + 5000] if start_idx else output_text[:5000]

        return ExecutionResult(
            status="SUCCESS",
            message=f"Code search results for '{req.query}':\n{summary}",
            service="code_search",
            detail={"output": output_text[:15000], "output_length": len(output_text)},
        )

    except asyncio.TimeoutError:
        log.error("[code_search] command timed out")
        return ExecutionResult(
            status="FAILURE",
            message="Code search timed out after 60 seconds",
            service="code_search",
        )
    except Exception as e:
        log.error(f"[code_search] execution error: {e}")
        return ExecutionResult(
            status="FAILURE",
            message=f"Code search failed: {e!s}",
            service="code_search",
        )
