"""Tests for the webscraper execution handler."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.execution.handlers.webscraper import (
    SCRAPER_SCRIPT,
    handle_web_scraper,
)
from services.execution.schemas import (
    UserContext,
    WebScraperRequest,
)

BASE_URL = "http://ha.local"


@pytest.fixture
def mock_user_context():
    return UserContext(
        user="testuser",
        ha_url=BASE_URL,
        ha_token="mock-token",
        mass_token_enc="mock-enc",
    )


@pytest.fixture
def sample_request(mock_user_context):
    return WebScraperRequest(
        user_context=mock_user_context,
        query="RTX 5090",
        urls=["ebay"],
        mobile=False,
        headless=True,
    )


def test_scraper_script_path_resolves():
    """Verify the webscraper.py script path is valid."""
    assert SCRAPER_SCRIPT.exists(), f"Webscraper script not found at {SCRAPER_SCRIPT}"
    assert SCRAPER_SCRIPT.suffix == ".py"


def test_webscraper_request_schema(sample_request):
    """Verify the schema validates correctly."""
    assert sample_request.query == "RTX 5090"
    assert sample_request.urls == ["ebay"]
    assert sample_request.mobile is False
    assert sample_request.headless is True


def test_webscraper_request_default_urls():
    """Verify default URLs are ebay, amazon, newegg."""
    req = WebScraperRequest(
        user_context=UserContext(
            user="testuser",
            ha_url=BASE_URL,
            ha_token="mock-token",
            mass_token_enc="mock-enc",
        ),
        query="test",
    )
    assert req.urls == ["ebay", "amazon", "newegg"]


def test_webscraper_request_custom_urls(mock_user_context):
    """Verify custom URLs work."""
    req = WebScraperRequest(
        user_context=mock_user_context,
        query="test",
        urls=["amazon", "google_shopping"],
    )
    assert req.urls == ["amazon", "google_shopping"]


@pytest.mark.asyncio
async def test_handle_web_scraper_success(sample_request, mocker):
    """Verify successful scrape returns SUCCESS status."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(
            b"QUERY: RTX 5090\neBay: $1,499.99",
            b"",
        )
    )
    mock_proc.returncode = 0

    mock_create = AsyncMock(return_value=mock_proc)
    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", new_callable=lambda: AsyncMock(return_value=(b"QUERY: RTX 5090\n", b"")))

    result = await handle_web_scraper(sample_request)

    assert result.status == "SUCCESS"
    assert "RTX 5090" in result.message
    assert result.service == "web_scraper"


@pytest.mark.asyncio
async def test_handle_web_scraper_command_failure(sample_request, mocker):
    """Verify failed command returns FAILURE status."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"", b"playwright not installed")
    )
    mock_create.return_value.returncode = 1

    mocker.patch("asyncio.create_subprocess_exec", mock_create)

    # Mock asyncio.wait_for to raise the error
    async def mock_wait_for(coro, timeout):
        stdout, stderr = await coro
        return stdout, stderr

    mocker.patch("asyncio.wait_for", mock_wait_for)

    result = await handle_web_scraper(sample_request)

    assert result.status == "FAILURE"
    assert "playwright not installed" in result.message


@pytest.mark.asyncio
async def test_handle_web_scraper_timeout(sample_request, mocker):
    """Verify timeout returns FAILURE status."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock()
    mock_create.return_value.returncode = 0

    mocker.patch("asyncio.create_subprocess_exec", mock_create)

    # Simulate timeout
    async def mock_wait_for(coro, timeout):
        raise TimeoutError("timed out")

    mocker.patch("asyncio.wait_for", mock_wait_for)

    result = await handle_web_scraper(sample_request)

    assert result.status == "FAILURE"
    assert "timed out" in result.message


@pytest.mark.asyncio
async def test_handle_web_scraper_with_output_file(sample_request, mocker, tmp_path):
    """Verify output_file argument is passed correctly."""
    output_file = str(tmp_path / "results.json")
    sample_request.output_file = output_file

    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_web_scraper(sample_request)

    call_args = mock_create.call_args
    assert "--output" in call_args[0]
    assert output_file in call_args[0]


@pytest.mark.asyncio
async def test_handle_web_scraper_mobile_flag(sample_request, mocker):
    """Verify mobile flag is passed as CLI arg."""
    sample_request.mobile = True
    sample_request.headless = False

    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_web_scraper(sample_request)

    call_args = mock_create.call_args
    assert "--mobile" in call_args[0]
    assert "--no-headless" in call_args[0]


@pytest.mark.asyncio
async def test_handle_web_scraper_general_error(sample_request, mocker):
    """Verify general exceptions return FAILURE."""
    mock_create = AsyncMock(side_effect=RuntimeError("subprocess error"))
    mocker.patch("asyncio.create_subprocess_exec", mock_create)

    result = await handle_web_scraper(sample_request)

    assert result.status == "FAILURE"
    assert "subprocess error" in result.message
