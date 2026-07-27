# services/execution/handlers/webscraper.py
"""WebScraper handler - Price scraping via Playwright + Tesseract OCR."""

import asyncio
import logging
import os
import sys
from pathlib import Path

try:
    from schemas import ExecutionResult, WebScraperRequest
except ImportError:
    from ..schemas import ExecutionResult, WebScraperRequest

log = logging.getLogger("execution.webscraper")

SCRAPER_SCRIPT = Path(__file__).resolve().parent.parent.parent.parent / "tools" / "webscraper.py"


async def handle_web_scraper(req: WebScraperRequest) -> ExecutionResult:
    """Execute the webscraper CLI tool with the provided query and sources."""
    log.info(f"[webscraper] query='{req.query}' urls={req.urls} mobile={req.mobile}")

    cmd = [
        sys.executable,
        str(SCRAPER_SCRIPT),
        "--query", req.query,
    ]

    # Mobile by default for better bot evasion; override if explicitly false
    if req.mobile or not req.mobile:
        cmd.append("--mobile")
    cmd.append("--headless" if req.headless else "--no-headless")

    # Add URL sources
    url_args = []
    for u in req.urls:
        url_args.extend(["--urls", u])

    if req.output_file:
        cmd.extend(["--output", req.output_file])

    # Pass OCR settings via environment variables
    env = os.environ.copy()
    if req.ocr_model:
        env["VISION_OCR_MODEL"] = req.ocr_model
        log.info(f"[webscraper] using OCR model from request: {req.ocr_model}")
    if req.ocr_proxy:
        env["VISION_OCR_PROXY_URL"] = req.ocr_proxy
        log.info(f"[webscraper] using OCR proxy from request: {req.ocr_proxy}")

    # Filter empty args
    cmd = [c for c in cmd if c]

    log.info(f"[webscraper] running: {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)

        output_text = stdout.decode(errors="replace").strip()
        error_text = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            log.error(f"[webscraper] command failed (rc={proc.returncode}): {error_text[:500]}")
            return ExecutionResult(
                status="FAILURE",
                message=f"Webscraper command failed: {error_text[:500]}",
                service="web_scraper",
                detail={"returncode": proc.returncode, "stderr": error_text[:2000]},
            )

        if not output_text:
            return ExecutionResult(
                status="SUCCESS",
                message=f"Webscraper completed for '{req.query}'. No output captured.",
                service="web_scraper",
            )

        # Try to extract summary from output
        lines = output_text.split("\n")
        # Find the first formatted results header
        start_idx = 0
        for i, line in enumerate(lines):
            if "QUERY:" in line:
                start_idx = i
                break

        summary = output_text[start_idx:start_idx + 3000] if start_idx else output_text[:3000]

        return ExecutionResult(
            status="SUCCESS",
            message=f"Webscraper results for '{req.query}':\n{summary}",
            service="web_scraper",
            detail={"output": output_text[:10000], "output_length": len(output_text)},
        )

    except TimeoutError:
        log.error("[webscraper] command timed out")
        return ExecutionResult(
            status="FAILURE",
            message="Webscraper timed out after 180 seconds",
            service="web_scraper",
        )
    except Exception as e:
        log.error(f"[webscraper] execution error: {e}")
        return ExecutionResult(
            status="FAILURE",
            message=f"Webscraper failed: {e!s}",
            service="web_scraper",
        )
