# services/tests/test_searxng_search.py
"""Tests for SearXNG JSON API integration with Playwright fallback."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from execution.schemas import UserContext, WebSearchRequest
from execution.handlers import browser


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
                "content": "A fully featured HTTP client for Python 3.",
                "engine": "google",
                "score": 1.0,
            },
            {
                "title": "Async HTTP in Python",
                "url": "https://example.com/async-http",
                "content": "Guide to async HTTP requests.",
                "engine": "duckduckgo",
                "score": 0.8,
            },
        ],
        "number_of_results": 2,
    }


@pytest.mark.asyncio
async def test_searxng_json_search_success(search_req, mock_json_response):
    """Verify JSON API returns structured results with engine source and score."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_json_response
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await browser._searxng_json_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.detail["source"] == "searxng_json"
    assert len(result.detail["results"]) == 2
    assert result.detail["results"][0]["engine"] == "google"
    assert result.detail["results"][0]["score"] == 1.0
    assert "httpx" in result.detail["formatted_content"]


@pytest.mark.asyncio
async def test_searxng_json_search_no_results(search_req):
    """Verify empty results return SUCCESS with empty list."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [], "number_of_results": 0}
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await browser._searxng_json_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.detail["results"] == []
    assert "no results" in result.message.lower()


@pytest.mark.asyncio
async def test_searxng_json_search_with_optional_params(user_ctx):
    """Verify optional params (category, engines, time_range) are passed to API."""
    req = WebSearchRequest(
        user_context=user_ctx,
        query="latest python news",
        category="news",
        engines="google,bing",
        time_range="week",
        language="en",
        safesearch=1,
    )

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [], "number_of_results": 0}
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        await browser._searxng_json_search(req)

        call_args = mock_client.get.call_args[0][0]
        assert "categories=news" in call_args
        assert "engines=google%2Cbing" in call_args or "engines=google,bing" in call_args
        assert "time_range=week" in call_args
        assert "safesearch=1" in call_args


@pytest.mark.asyncio
async def test_handle_web_search_json_api_first(search_req, mock_json_response):
    """Verify handle_web_search tries JSON API first."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_json_response
    mock_resp.raise_for_status = MagicMock()

    with patch("services.execution.handlers.browser.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        result = await browser.handle_web_search(search_req)

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.detail is not None
    assert result.detail["source"] == "searxng_json"


@pytest.mark.asyncio
async def test_handle_web_search_fallback_to_playwright(search_req):
    """Verify fallback to Playwright when JSON API raises."""
    with patch("services.execution.handlers.browser._searxng_json_search", side_effect=Exception("API down")):
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
    assert result.detail is not None
    assert result.detail["source"] == "playwright_fallback"
    assert len(result.detail["results"]) == 1


@pytest.mark.asyncio
async def test_handle_web_search_total_failure(search_req):
    """Verify FAILURE when both JSON API and Playwright fail."""
    with patch("services.execution.handlers.browser._searxng_json_search", side_effect=Exception("API down")):
        with patch("services.execution.handlers.browser._playwright_fallback", side_effect=Exception("Browser crash")):
            result = await browser.handle_web_search(search_req)

    assert result.status == "FAILURE"
    assert "Browser crash" in result.message
