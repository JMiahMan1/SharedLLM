from __future__ import annotations

import base64
import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from services.execution.handlers import browser_automation
from services.execution.schemas import BrowserAction, BrowserAutomationRequest, UserContext


class _Response:
    status = 200


class _Page:
    def __init__(self):
        self.url = "https://example.test/"
        self.calls = []
        self.screenshot_bytes = b"png-bytes"

    async def goto(self, url, **kwargs):
        self.calls.append(("goto", url, kwargs))
        self.url = url
        return _Response()

    async def click(self, selector, **kwargs):
        self.calls.append(("click", selector, kwargs))

    async def fill(self, selector, text, **kwargs):
        self.calls.append(("fill", selector, text, kwargs))

    async def press(self, selector, key, **kwargs):
        self.calls.append(("press", selector, key, kwargs))

    async def wait_for_timeout(self, ms):
        self.calls.append(("wait", ms))

    async def title(self):
        return "Example"

    async def inner_text(self, selector):
        self.calls.append(("inner_text", selector))
        return "Hello from the page"

    async def screenshot(self, full_page=False):
        self.calls.append(("screenshot", full_page))
        return self.screenshot_bytes


class _Browser:
    def __init__(self, page):
        self.page = page
        self.closed = False

    async def new_page(self, **kwargs):
        self.page.calls.append(("new_page", kwargs))
        return self.page

    async def close(self):
        self.closed = True


class _Chromium:
    def __init__(self, browser):
        self.browser = browser

    async def launch(self, **kwargs):
        self.browser.page.calls.append(("launch", kwargs))
        return self.browser


class _Playwright:
    def __init__(self, browser):
        self.chromium = _Chromium(browser)


def _install_playwright(monkeypatch, page):
    browser = _Browser(page)

    @asynccontextmanager
    async def async_playwright():
        yield _Playwright(browser)

    package = types.ModuleType("playwright")
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = async_playwright
    package.async_api = async_api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    return browser


@pytest.mark.asyncio
async def test_browser_automation_returns_text_and_a_screenshot(monkeypatch):
    page = _Page()
    browser = _install_playwright(monkeypatch, page)
    monkeypatch.setattr(browser_automation, "create_artifact", AsyncMock(return_value=None))

    result = await browser_automation.handle_browser_automation(
        BrowserAutomationRequest(
            user_context=UserContext(user="jeremiah"),
            url="https://example.test/login",
            actions=[
                BrowserAction(type="type", selector="#email", text="a@b.test"),
                BrowserAction(type="click", selector="#submit"),
                BrowserAction(type="wait", wait_ms=250),
            ],
        )
    )

    assert result.status == "SUCCESS"
    assert result.detail["title"] == "Example"
    assert result.detail["text"] == "Hello from the page"
    assert result.detail["screenshot_base64"] == base64.b64encode(b"png-bytes").decode()
    assert result.detail["screenshot_mime"] == "image/png"
    assert ("goto", "https://example.test/login", {"wait_until": "domcontentloaded", "timeout": 30000}) in page.calls
    assert ("fill", "#email", "a@b.test", {"timeout": 30000}) in page.calls
    assert ("click", "#submit", {"timeout": 30000}) in page.calls
    assert ("wait", 250) in page.calls
    assert browser.closed is True


@pytest.mark.asyncio
async def test_browser_automation_explains_a_missing_playwright(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)

    result = await browser_automation.handle_browser_automation(
        BrowserAutomationRequest(
            user_context=UserContext(user="jeremiah"),
            url="https://example.test/",
            screenshot=False,
        )
    )

    assert result.status == "FAILURE"
    assert "Playwright" in result.message
