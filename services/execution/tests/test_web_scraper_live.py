"""Live webscraper integration tests — hit the execution service directly.

These tests exercise the full webscraper pipeline: Playwright → screenshot →
vision_ocr (Qwen2.5-VL) → structured extraction.

Requires:
  - Execution service running on localhost:8003
  - Playwright browsers installed
  - OCR model + proxy configured (VISION_OCR_MODEL / VISION_OCR_PROXY_URL
    env vars, or identity settings with vision_ocr_model + llm_local_url)
"""

import os

os.environ["INTERNAL_SECRET"] = "RAVEN_SECURE_2026"

import pytest
import httpx

from services.execution.tests.test_live_integration import (
    INTERNAL_HEADERS,
    _get_user_credentials,
)

EXECUTION_URL = os.getenv("EXECUTION_SVC_URL", "http://localhost:8003")


@pytest.mark.asyncio
@pytest.mark.local_only
async def test_web_scraper_simple_query():
    """Basic webscraper call with a simple product query."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_scraper",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "test",
                "urls": ["ebay"],
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("SUCCESS", "FAILURE")
    assert "message" in data
    if data["status"] == "SUCCESS":
        assert "test" in data["message"].lower() or "QUERY:" in data["message"]


@pytest.mark.asyncio
@pytest.mark.local_only
async def test_web_scraper_multiple_sources():
    """Scrape from multiple sources simultaneously."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_scraper",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "laptop",
                "urls": ["ebay", "amazon"],
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
@pytest.mark.local_only
async def test_web_scraper_mobile_mode():
    """Web scraper with mobile viewport flag."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_scraper",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "phone case",
                "urls": ["amazon"],
                "mobile": True,
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
@pytest.mark.local_only
async def test_web_scraper_custom_url():
    """Scrape a custom URL instead of named sources."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_scraper",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "test product",
                "urls": ["https://example.com"],
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
@pytest.mark.local_only
async def test_web_scraper_with_ocr_model_override():
    """Web scraper with explicit OCR model and proxy settings."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_scraper",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "graphics card",
                "urls": ["ebay"],
                "ocr_model": "qwen2.5-vl:7b",
                "ocr_proxy": "http://localhost:7888",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "message" in data


@pytest.mark.asyncio
@pytest.mark.local_only
async def test_web_scraper_empty_result_handling():
    """Web scraper with a query that may return no results should not crash."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_scraper",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "xyzzy nonexistentsource 12345",
                "urls": ["ebay"],
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
@pytest.mark.local_only
async def test_web_scraper_with_output_file():
    """Web scraper with output_file parameter."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_scraper",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "test product",
                "urls": ["ebay"],
                "output_file": "/tmp/webscraper_test_output.json",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
