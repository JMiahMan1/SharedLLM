"""Drive a real browser and return what the page shows.

The web scraper reads product pages; this handler is for the other half of
Raven's loop: open a UI, click through it, and look at the result. It is
deliberately one bounded request (navigate, a few actions, screenshot) rather
than a long-lived browser session: the caller gets a screenshot and the visible
text, and can decide what to do next.
"""
from __future__ import annotations

import base64
import logging

from services.execution.artifacts import create_artifact

try:
    from schemas import BrowserAutomationRequest, ExecutionResult
except ImportError:
    from ..schemas import BrowserAutomationRequest, ExecutionResult

log = logging.getLogger("execution.browser_automation")

MAX_TEXT = 20_000


async def handle_browser_automation(req: BrowserAutomationRequest) -> ExecutionResult:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ExecutionResult(
            status="FAILURE",
            message="Playwright is not installed in the execution service.",
            service="browser_automation",
        )

    log.info("[browser] %s (%d actions)", req.url, len(req.actions))
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                response = await page.goto(
                    req.url, wait_until="domcontentloaded", timeout=req.timeout_ms
                )
                for action in req.actions:
                    kind = (action.type or "").lower()
                    if kind == "click":
                        if not action.selector:
                            raise ValueError("click needs a selector")
                        await page.click(action.selector, timeout=req.timeout_ms)
                    elif kind == "type":
                        if not action.selector:
                            raise ValueError("type needs a selector")
                        await page.fill(action.selector, action.text or "", timeout=req.timeout_ms)
                    elif kind == "press":
                        if not action.selector:
                            raise ValueError("press needs a selector")
                        await page.press(action.selector, action.text or "Enter", timeout=req.timeout_ms)
                    elif kind == "wait":
                        await page.wait_for_timeout(action.wait_ms or 1000)
                    else:
                        raise ValueError(f"unsupported browser action: {action.type}")

                title = await page.title()
                text = (await page.inner_text("body"))[:MAX_TEXT]
                screenshot = b""
                if req.screenshot:
                    screenshot = await page.screenshot(full_page=req.full_page)
                status = response.status if response is not None else None
                final_url = page.url
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("[browser] failed: %s", exc)
        return ExecutionResult(
            status="FAILURE",
            message=f"Browser automation failed: {exc}",
            service="browser_automation",
        )

    detail: dict = {
        "url": final_url,
        "status": status,
        "title": title,
        "text": text,
        "actions": len(req.actions),
    }
    if screenshot:
        encoded = base64.b64encode(screenshot).decode("ascii")
        detail["screenshot_base64"] = encoded
        detail["screenshot_mime"] = "image/png"
        artifact = await create_artifact(
            user_context=req.user_context,
            kind="image",
            name="browser-screenshot.png",
            data=screenshot,
            mime_type="image/png",
            generator="browser_automation",
            metadata={"url": final_url, "title": title},
        )
        if artifact:
            detail["artifact"] = artifact

    return ExecutionResult(
        status="SUCCESS",
        message=f"Opened {final_url} ({title or 'untitled'}).",
        service="browser_automation",
        detail=detail,
    )
