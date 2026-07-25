# services/tests/test_searxng_search.py
"""Tests for SearXNG JSON search integration with Playwright fallback."""
from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers import browser
from services.execution.schemas import UserContext, WebSearchRequest


@pytest.fixture
def user_ctx():
    return UserContext(user="test_user", is_admin=False)


@pytest.fixture
def search_req(user_ctx):
    return WebSearchRequest(user_context=user_ctx, query="python async httpx")


@pytest.fixture
def mock_json_response():
    return {
        "results": [
            {
                "title": "httpx Documentation",
                "url": "https://www.python-httpx.org/",
                "content": "httpx is a fully featured HTTP client for Python 3.",
                "engines": ["google", "bing"],
                "publishedDate": "2024-01-01",
            },
            {
                "title": "Async HTTP in Python",
                "url": "https://example.com/async-http",
                "content": "Guide to async HTTP requests.",
                "engines": ["duckduckgo"],
                "publishedDate": None,
            },
        ]
    }


@pytest.fixture
def mock_json_no_results():
    return {"results": []}


class _FakeSearchResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class _FakeSearchSession:
    """Mimics `async with aiohttp.ClientSession() as c, c.get(url) as r:`."""

    def __init__(self, payload, raise_exc=None):
        self._payload = payload
        self._raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        self.last_url = url
        if self._raise_exc:
            raise self._raise_exc

        response = _FakeSearchResponse(self._payload)

        class _GetCM:
            def __init__(self, resp):
                self._resp = resp

            async def __aenter__(self):
                return self._resp

            async def __aexit__(self, *exc):
                return False

        return _GetCM(response)


def _patch_aiohttp(json_payload, raise_exc=None):
    """Patch aiohttp.ClientSession so client.get().json() returns json_payload.

    If `raise_exc` is set, client.get() raises it (to exercise the fallback)."""
    return _FakeSearchSession(json_payload, raise_exc=raise_exc)


@pytest.mark.asyncio
async def test_searxng_json_search_success(search_req, mock_json_response):
    """Verify JSON search returns structured results with title, url and snippet."""
    mock_session = _patch_aiohttp(mock_json_response)
    with patch("services.execution.handlers.browser.aiohttp.ClientSession", return_value=mock_session):
        result = await browser._searxng_json_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.detail["source"] == "searxng_json"
    assert len(result.detail["results"]) == 2
    assert result.detail["results"][0]["title"] == "httpx Documentation"
    assert result.detail["results"][0]["engine"] == "google, bing"
    assert "httpx Documentation" in result.message


@pytest.mark.asyncio
async def test_searxng_json_search_no_results(search_req, mock_json_no_results):
    """Verify empty results returns None (triggers Playwright fallback)."""
    mock_session = _patch_aiohttp(mock_json_no_results)
    with patch("services.execution.handlers.browser.aiohttp.ClientSession", return_value=mock_session):
        result = await browser._searxng_json_search(search_req)

    assert result is None


@pytest.mark.asyncio
async def test_searxng_json_search_with_optional_params(user_ctx):
    """Verify optional params (category, engines, safesearch) are passed to API."""
    req = WebSearchRequest(
        user_context=user_ctx,
        query="latest python news",
        category="news",
        engines="google,bing",
        language="en",
        safesearch=1,
    )
    mock_session = _patch_aiohttp({"results": []})
    with patch("services.execution.handlers.browser.aiohttp.ClientSession", return_value=mock_session):
        await browser._searxng_json_search(req)

        call_url = mock_session.last_url
        assert "categories=news" in call_url
        assert "engines=google%2Cbing" in call_url or "engines=google,bing" in call_url
        assert "safesearch=1" in call_url
        assert "format=json" in call_url


@pytest.mark.asyncio
async def test_handle_web_search_uses_json_search_first(search_req, mock_json_response):
    """Verify handle_web_search uses the JSON API path first."""
    mock_session = _patch_aiohttp(mock_json_response)
    with patch("services.execution.handlers.browser.aiohttp.ClientSession", return_value=mock_session):
        result = await browser.handle_web_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.detail["source"] == "searxng_json"
    assert "httpx Documentation" in result.message


@pytest.mark.asyncio
async def test_handle_web_search_fallback_to_playwright(search_req):
    """Verify fallback to Playwright when the JSON API raises."""
    mock_session = _patch_aiohttp({"results": []}, raise_exc=Exception("JSON API down"))
    # Force the JSON path to raise so the Playwright fallback runs

    mock_page = AsyncMock()
    mock_page.evaluate.return_value = [
        {"title": "Fallback Result", "url": "https://example.com", "snippet": "Found via fallback"}
    ]
    mock_browser = AsyncMock()
    mock_browser.new_page.return_value = mock_page

    mock_playwright = AsyncMock()
    mock_playwright.chromium.launch.return_value = mock_browser

    with patch("services.execution.handlers.browser.aiohttp.ClientSession", return_value=mock_session), \
         patch("services.execution.handlers.browser.async_playwright",
               return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright), __aexit__=AsyncMock(return_value=False))):
        result = await browser.handle_web_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert "Fallback Result" in result.detail["formatted_content"]


@pytest.mark.asyncio
async def test_handle_web_search_total_failure(search_req):
    """Verify FAILURE when both JSON search and Playwright fail."""
    mock_session = _patch_aiohttp({"results": []}, raise_exc=Exception("JSON API down"))
    mock_playwright = AsyncMock()
    mock_playwright.chromium.launch.side_effect = Exception("Browser crash")

    with patch("services.execution.handlers.browser.aiohttp.ClientSession", return_value=mock_session), \
         patch("services.execution.handlers.browser.async_playwright",
               return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright), __aexit__=AsyncMock(return_value=False))):
        result = await browser.handle_web_search(search_req)

    assert result.status == "FAILURE"
    assert "Browser crash" in result.message
