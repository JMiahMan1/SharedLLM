"""
Blocked engines must surface as a failure, not as an empty answer.

A SearXNG instance whose engines are all suspended still answers HTTP 200 with
``results: []`` and a populated ``unresponsive_engines`` list. Treating that as
"no results" hands control to the Playwright fallback, which scrapes the very
same instance, finds the same empty page, and returns SUCCESS with
"Search completed but no results were found." -- so an operator outage reads
as a confident negative to every caller, including Raven's WebSearchRequest.

These tests pin the three outcomes apart: blocked engines fail loudly, a
genuinely empty search still returns None for the fallback to try, and results
win regardless of how many engines were unhappy.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from unittest.mock import AsyncMock

import pytest

from services.execution.handlers import browser
from services.execution.schemas import UserContext, WebSearchRequest

SEARXNG = "http://searxng.test"
BROKEN = [
    ["brave", "Suspended: too many requests"],
    ["duckduckgo", "CAPTCHA"],
    ["startpage", "Suspended: CAPTCHA"],
]
ONE_BROKEN = [["startpage", "Suspended: CAPTCHA"]]


def _engines_in(url: str) -> str:
    return (parse_qs(urlparse(url).query).get("engines") or [""])[0]


def _req(**kwargs) -> WebSearchRequest:
    kwargs.setdefault("user_context", UserContext(user="tester"))
    kwargs.setdefault("query", "Macduff trusting God in poverty and trial")
    return WebSearchRequest(**kwargs)


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict:
        return self._payload


class _ResponseCtx:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def __aenter__(self) -> _Response:
        return _Response(self._payload)

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeSession:
    """A stand-in for aiohttp.ClientSession that replays canned payloads."""

    payloads: list[dict] = []
    urls: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def get(self, url: str) -> _ResponseCtx:
        FakeSession.urls.append(url)
        if not FakeSession.payloads:
            raise AssertionError(f"unexpected extra request: {url}")
        return _ResponseCtx(FakeSession.payloads.pop(0))


def _install(monkeypatch, *payloads: dict) -> None:
    FakeSession.payloads = list(payloads)
    FakeSession.urls = []
    monkeypatch.setattr(browser.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(
        browser,
        "_get_searxng_url",
        AsyncMock(return_value=SEARXNG),
    )


async def test_blocked_engines_fail_loudly_instead_of_reporting_no_results(monkeypatch) -> None:
    _install(monkeypatch, {"results": [], "unresponsive_engines": BROKEN})

    result = await browser._searxng_json_search(_req())

    assert result is not None
    assert result.status == "FAILURE"
    assert "every engine was blocked" in result.message
    for engine, reason in BROKEN:
        assert f"- {engine}: {reason}" in result.message
    assert "no results were found" not in result.message
    assert result.detail["unresponsive_engines"] == BROKEN


async def test_blocked_engines_retry_with_a_broader_list_before_giving_up(monkeypatch) -> None:
    monkeypatch.setattr(browser, "_is_testing", lambda: False)
    _install(
        monkeypatch,
        {"results": [], "unresponsive_engines": BROKEN},
        {"results": [{"title": "Memories of Bethany", "url": "http://b.test", "content": "x"}]},
    )

    result = await browser._searxng_json_search(_req())

    assert result.status == "SUCCESS"
    assert len(FakeSession.urls) == 2
    assert _engines_in(FakeSession.urls[1]) == browser.BROADER_ENGINES


async def test_a_broader_retry_that_still_finds_nothing_still_fails(monkeypatch) -> None:
    monkeypatch.setattr(browser, "_is_testing", lambda: False)
    _install(
        monkeypatch,
        {"results": [], "unresponsive_engines": BROKEN},
        {"results": [], "unresponsive_engines": BROKEN},
    )

    result = await browser._searxng_json_search(_req())

    assert result.status == "FAILURE"
    assert "every engine was blocked" in result.message
    assert len(FakeSession.urls) == 2


async def test_results_win_even_when_some_engines_were_unhappy(monkeypatch) -> None:
    _install(
        monkeypatch,
        {
            "results": [{"title": "The Home Scene.", "url": "http://b.test", "content": "faith"}],
            "unresponsive_engines": ONE_BROKEN,
        },
    )

    result = await browser._searxng_json_search(_req())

    assert result.status == "SUCCESS"
    assert result.detail["results"][0]["title"] == "The Home Scene."
    assert len(FakeSession.urls) == 1


async def test_an_empty_search_with_every_engine_responsive_still_falls_back(monkeypatch) -> None:
    _install(monkeypatch, {"results": [], "unresponsive_engines": []})

    assert await browser._searxng_json_search(_req()) is None
    assert len(FakeSession.urls) == 1


async def test_a_transport_failure_still_raises_for_the_fallback_to_handle(monkeypatch) -> None:
    monkeypatch.setattr(browser, "_get_searxng_url", AsyncMock(return_value=SEARXNG))

    class _Down(FakeSession):
        def get(self, url: str) -> "_ResponseCtx":
            raise ConnectionError("json endpoint down")

    monkeypatch.setattr(browser.aiohttp, "ClientSession", _Down)

    with pytest.raises(ConnectionError):
        await browser._searxng_json_search(_req())


async def test_an_explicit_engine_list_is_not_overwritten_by_the_default(monkeypatch) -> None:
    _install(monkeypatch, {"results": [{"title": "t", "url": "u", "content": "c"}]})

    await browser._searxng_json_search(_req(engines="mojeek"))

    assert _engines_in(FakeSession.urls[0]) == "mojeek"


async def test_an_unconfigured_search_still_names_an_engine_set(monkeypatch) -> None:
    _install(monkeypatch, {"results": [{"title": "t", "url": "u", "content": "c"}]})

    await browser._searxng_json_search(_req())

    assert _engines_in(FakeSession.urls[0]) == browser.DEFAULT_ENGINES


async def test_blocked_engines_never_reach_the_playwright_fallback(monkeypatch) -> None:
    _install(monkeypatch, {"results": [], "unresponsive_engines": BROKEN})

    async def _must_not_run(req) -> object:
        raise AssertionError("the fallback must not scrape a broken instance")

    monkeypatch.setattr(browser, "_playwright_fallback", _must_not_run)

    result = await browser.handle_web_search(_req())

    assert result.status == "FAILURE"
    assert "every engine was blocked" in result.message
