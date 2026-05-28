# services/tests/test_searxng_search.py
"""Tests for SearXNG HTML search integration with Playwright fallback."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from services.execution.schemas import UserContext, WebSearchRequest
from services.execution.handlers import browser


@pytest.fixture
def user_ctx():
    return UserContext(user="test_user", is_admin=False)


@pytest.fixture
def search_req(user_ctx):
    return WebSearchRequest(user_context=user_ctx, query="python async httpx")


@pytest.fixture
def mock_html_response():
    return (
        '<div class="result__title">'
        '<a href="https://www.python-httpx.org/">httpx Documentation</a>'
        '</div>'
        '<div class="result__title">'
        '<a href="https://example.com/async-http">Async HTTP in Python</a>'
        '</div>'
    )


@pytest.fixture
def mock_html_no_results():
    return '<div>No results found</div>'


@pytest.mark.asyncio
async def test_searxng_html_search_success(search_req, mock_html_response):
    """Verify HTML search returns structured results with title and URL."""
    mock_resp = MagicMock()
    mock_resp.text = mock_html_response
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await browser._searxng_html_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert "httpx Documentation" in result.message
    assert "Async HTTP in Python" in result.message


@pytest.mark.asyncio
async def test_searxng_html_search_no_results(search_req, mock_html_no_results):
    """Verify empty results returns None (triggers Playwright fallback)."""
    mock_resp = MagicMock()
    mock_resp.text = mock_html_no_results
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await browser._searxng_html_search(search_req)

    assert result is None


@pytest.mark.asyncio
async def test_searxng_html_search_with_optional_params(user_ctx):
    """Verify optional params (category, engines, safesearch) are passed to API."""
    req = WebSearchRequest(
        user_context=user_ctx,
        query="latest python news",
        category="news",
        engines="google,bing",
        language="en",
        safesearch=1,
    )

    mock_resp = MagicMock()
    mock_resp.text = '<div class="result__title"><a href="https://example.com">Test</a></div>'
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        await browser._searxng_html_search(req)

        call_args = mock_client.get.call_args[0][0]
        assert "categories=news" in call_args
        assert "engines=google%2Cbing" in call_args or "engines=google,bing" in call_args
        assert "safesearch=1" in call_args


@pytest.mark.asyncio
async def test_handle_web_search_uses_html_search_first(search_req, mock_html_response):
    """Verify handle_web_search tries HTML search first."""
    mock_resp = MagicMock()
    mock_resp.text = mock_html_response
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await browser.handle_web_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert "httpx Documentation" in result.message


@pytest.mark.asyncio
async def test_handle_web_search_fallback_to_playwright(search_req):
    """Verify fallback to Playwright when HTML search returns no results."""
    mock_resp = MagicMock()
    mock_resp.text = '<div>No results</div>'
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_page = AsyncMock()
        mock_page.evaluate.return_value = [
            {"title": "Fallback Result", "url": "https://example.com", "snippet": "Found via fallback"}
        ]
        mock_browser = AsyncMock()
        mock_browser.new_page.return_value = mock_page

        mock_playwright = AsyncMock()
        mock_playwright.chromium.launch.return_value = mock_browser

        with patch("services.execution.handlers.browser.async_playwright", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright), __aexit__=AsyncMock())):
            result = await browser.handle_web_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert "Fallback Result" in result.message


@pytest.mark.asyncio
async def test_handle_web_search_total_failure(search_req):
    """Verify FAILURE when both HTML search and Playwright fail."""
    mock_resp = MagicMock()
    mock_resp.text = '<div>No results</div>'
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_playwright = AsyncMock()
        mock_playwright.chromium.launch.side_effect = Exception("Browser crash")

        with patch("services.execution.handlers.browser.async_playwright", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_playwright), __aexit__=AsyncMock())):
            result = await browser.handle_web_search(search_req)

    assert result.status == "FAILURE"
    assert "Browser crash" in result.message
