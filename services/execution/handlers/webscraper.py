# services/execution/handlers/webscraper.py
"""WebScraper handler - Price scraping via Playwright + Tesseract OCR."""

import asyncio
import json
import logging
import os
import re
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
    cmd.extend(url_args)

    if req.output_file:
        cmd.extend(["--output", req.output_file])

    # Pass OCR model/proxy from request
    if req.ocr_model:
        cmd.extend(["--ocr-model", req.ocr_model])
    if req.ocr_proxy:
        cmd.extend(["--ocr-proxy", req.ocr_proxy])

    # Browser engine - default to camoufox for better anti-bot evasion
    browser_engine = req.browser_engine or "camoufox"
    cmd.extend(["--browser", browser_engine])

    # Request structured JSON output for programmatic parsing
    cmd.append("--json-output")

    # OCR settings resolved at runtime from config DB via identity service
    env = os.environ.copy()

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
        # 300s: settle waits + full-page screenshot + vision OCR (capped at
        # 800 tokens, ~150s on a ~5 t/s local vision model) need headroom
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

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

        # Extract structured JSON from stdout (printed after formatted text when --json-output is used)
        structured_data = _parse_json_from_output(output_text)

        # Build human-readable summary from formatted text
        lines = output_text.split("\n")
        start_idx = 0
        for i, line in enumerate(lines):
            if "QUERY:" in line:
                start_idx = i
                break

        summary = output_text[start_idx:start_idx + 3000] if start_idx else output_text[:3000]

        # Build detail with both structured data and formatted output
        detail = {
            "formatted_output": summary,
            "output_length": len(output_text),
        }

        if structured_data:
            detail["structured"] = structured_data
            results = structured_data.get("results", [])
            if results:
                # Aggregate top-level fields from all results
                all_specs = []
                all_product_details = []
                full_desc = ""
                all_prices = []
                for r in results:
                    all_specs.extend(r.get("specifications", []))
                    all_product_details.extend(r.get("product_details", []))
                    if r.get("full_description"):
                        full_desc = r["full_description"]
                    all_prices.extend(r.get("prices", []))

                detail["specifications"] = all_specs
                detail["product_details"] = all_product_details
                if full_desc:
                    detail["full_description"] = full_desc
                detail["total_prices"] = len(all_prices)
                log.info(f"[webscraper] parsed {len(all_prices)} prices, {len(all_specs)} specs, {len(all_product_details)} product_details")

        return ExecutionResult(
            status="SUCCESS",
            message=f"Webscraper results for '{req.query}':\n{summary}",
            service="web_scraper",
            detail=detail,
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


def _parse_json_from_output(text: str) -> dict | None:
    """Extract and parse JSON from stdout output.

    When --json-output is used, webscraper prints formatted text first,
    then a JSON block at the end. This function finds and parses the JSON.
    """
    # Try to find JSON block at the end of output
    # JSON starts with { or [ and ends with } or ]
    json_match = None
    brace_count = 0
    json_start = -1

    for i in range(len(text) - 1, -1, -1):
        char = text[i]
        if char == '}':
            if json_start == -1:
                json_start = i
            brace_count += 1
        elif char == '{':
            if brace_count > 0:
                brace_count -= 1
                if brace_count == 0 and json_start != -1:
                    json_match = text[i:json_start + 1]
                    break
        elif char == ']':
            if json_start == -1:
                json_start = i
            brace_count += 1
        elif char == '[':
            if brace_count > 0:
                brace_count -= 1
                if brace_count == 0 and json_start != -1:
                    json_match = text[i:json_start + 1]
                    break

    if json_match:
        try:
            return json.loads(json_match)
        except json.JSONDecodeError:
            log.warning("[webscraper] found JSON-like block but failed to parse")

    # Fallback: try parsing entire output as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: try to find any JSON block in the middle
    pattern = r'\{[^{}]*"results"\s*:\s*\[[\s\S]*?\}\s*\]'
    match = re.search(pattern, text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
