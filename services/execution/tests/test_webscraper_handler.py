"""Tests for the webscraper execution handler."""

import json
from unittest.mock import AsyncMock

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


@pytest.mark.asyncio
async def test_handle_web_scraper_with_ocr_settings(sample_request, mocker):
    """Verify OCR settings in request do not affect subprocess env (resolved at runtime from config DB)."""
    from services.execution.schemas import WebScraperRequest

    req = WebScraperRequest(
        user_context=sample_request.user_context,
        query="RTX 5090",
        urls=["ebay"],
        ocr_model="qwen2.5-vl:7b",
        ocr_proxy="http://alpaca-proxy:7888",
    )

    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"done", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    await handle_web_scraper(req)

    call_kwargs = mock_create.call_args[1]
    env = call_kwargs.get("env", {})
    assert "VISION_OCR_MODEL" not in env
    assert "VISION_OCR_PROXY_URL" not in env


@pytest.mark.asyncio
async def test_handle_web_scraper_without_cr_settings(sample_request, mocker):
    """Verify no OCR env vars are set when fields are None."""
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

    call_kwargs = mock_create.call_args[1]
    env = call_kwargs.get("env", {})
    assert "VISION_OCR_MODEL" not in env
    assert "VISION_OCR_PROXY_URL" not in env


def test_parse_json_from_output_with_formatted_text_and_json():
    """Verify JSON is correctly extracted from output that contains formatted text followed by JSON."""
    from services.execution.handlers.webscraper import _parse_json_from_output

    output = """
============================================================
QUERY: RTX 5090
SOURCE: eBay
============================================================
Product                           Price     Ship      Total
---------------------------------------------------------------------
NVIDIA GeForce RTX 5090            $1,999.00    $9.99  $2,008.99
- $1,999.00    $0.00  $1,999.00
{
  "results": [
    {
      "query": "RTX 5090",
      "source": "eBay",
      "prices": [
        {"product": "RTX 5090", "price": 1999.00, "currency": "USD", "shipping": 0.00, "total": 1999.00}
      ],
      "specifications": [{"key": "Memory", "value": "32GB GDDR7"}],
      "product_details": [{"key": "Brand", "value": "NVIDIA"}],
      "full_description": "NVIDIA GeForce RTX 5090 graphics card"
    }
  ],
  "summary": {"total_queries": 1, "total_sources": 1, "total_prices_found": 1}
}
"""
    result = _parse_json_from_output(output)
    assert result is not None
    assert result["summary"]["total_queries"] == 1
    assert result["summary"]["total_prices_found"] == 1
    assert len(result["results"]) == 1
    assert result["results"][0]["specifications"] == [{"key": "Memory", "value": "32GB GDDR7"}]
    assert result["results"][0]["full_description"] == "NVIDIA GeForce RTX 5090 graphics card"


def test_parse_json_from_output_pure_json():
    """Verify pure JSON output (no formatted text) is still parsed."""
    from services.execution.handlers.webscraper import _parse_json_from_output

    output = '{"results": [{"query": "test", "source": "ebay", "prices": []}], "summary": {"total_queries": 1}}'
    result = _parse_json_from_output(output)
    assert result is not None
    assert result["summary"]["total_queries"] == 1


def test_parse_json_from_output_no_json():
    """Verify None is returned when no JSON is found."""
    from services.execution.handlers.webscraper import _parse_json_from_output

    output = "QUERY: RTX 5090\neBay: $1,499.99"
    result = _parse_json_from_output(output)
    assert result is None


def test_parse_json_from_output_malformed_json():
    """Verify None is returned when JSON block is found but malformed."""
    from services.execution.handlers.webscraper import _parse_json_from_output

    output = "QUERY: test\n{invalid json"
    result = _parse_json_from_output(output)
    assert result is None


@pytest.mark.asyncio
async def test_handle_web_scraper_with_structured_data(sample_request, mocker):
    """Verify handler returns structured data in detail when JSON is present."""
    structured_json = json.dumps({
        "results": [
            {
                "query": "RTX 5090",
                "source": "eBay",
                "prices": [{"product": "RTX 5090", "price": 1999.00, "currency": "USD", "shipping": 0.00, "total": 1999.00}],
                "specifications": [{"key": "Memory", "value": "32GB GDDR7"}],
                "product_details": [{"key": "Brand", "value": "NVIDIA"}],
                "full_description": "NVIDIA GeForce RTX 5090",
            }
        ],
        "summary": {"total_queries": 1, "total_sources": 1, "total_prices_found": 1},
    })

    formatted_text = """============================================================
QUERY: RTX 5090
SOURCE: eBay
============================================================
Product                           Price     Ship      Total
---------------------------------------------------------------------
NVIDIA GeForce RTX 5090            $1,999.00    $0.00  $1,999.00"""

    full_output = formatted_text + "\n" + structured_json

    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(return_value=(full_output.encode(), b""))
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    result = await handle_web_scraper(sample_request)

    assert result.status == "SUCCESS"
    assert result.detail is not None
    assert "structured" in result.detail
    assert result.detail["structured"]["summary"]["total_prices_found"] == 1
    assert result.detail["specifications"] == [{"key": "Memory", "value": "32GB GDDR7"}]
    assert result.detail["product_details"] == [{"key": "Brand", "value": "NVIDIA"}]
    assert result.detail["full_description"] == "NVIDIA GeForce RTX 5090"
    assert result.detail["total_prices"] == 1
    # Verify --json-output flag is passed
    call_args = mock_create.call_args[0]
    assert "--json-output" in call_args


@pytest.mark.asyncio
async def test_handle_web_scraper_without_json_fallback(sample_request, mocker):
    """Verify handler still works when structured JSON is not present (backward compat)."""
    mock_create = AsyncMock()
    mock_create.return_value.communicate = AsyncMock(
        return_value=(b"QUERY: RTX 5090\neBay: $1,499.99", b"")
    )
    mock_create.return_value.returncode = 0

    async def mock_wait_for(coro, timeout):
        return await coro

    mocker.patch("asyncio.create_subprocess_exec", mock_create)
    mocker.patch("asyncio.wait_for", mock_wait_for)

    result = await handle_web_scraper(sample_request)

    assert result.status == "SUCCESS"
    assert result.detail is not None
    assert "formatted_output" in result.detail
    assert "RTX 5090" in result.message
