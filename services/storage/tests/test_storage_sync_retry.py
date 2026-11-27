"""One slow RAG batch must not stop a whole library crawl (TimeoutError at 600 of 4,383)."""
import asyncio

import aiohttp
import pytest

from services.storage import main


class Client:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def post(self, url, json, headers, timeout):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    async def instant(_s):
        return None
    monkeypatch.setattr(main.asyncio, "sleep", instant)


async def test_a_timeout_is_retried_and_the_batch_still_goes_through():
    ok = type("Resp", (), {"status": 200})()
    client = Client([TimeoutError(), aiohttp.ServerDisconnectedError(), ok])
    assert await main._sync_batch(client, [{"text": "x"}], "default", "calibre_files") is ok
    assert client.calls == 3


async def test_a_batch_that_keeps_timing_out_still_fails_loudly():
    client = Client([TimeoutError()] * main._SYNC_ATTEMPTS)
    with pytest.raises(TimeoutError):
        await main._sync_batch(client, [{"text": "x"}], "default", "calibre_files")
    assert client.calls == main._SYNC_ATTEMPTS


def test_the_batch_timeout_allows_slow_cpu_embedding():
    assert main._SYNC_TIMEOUT_S >= 120
    assert asyncio.iscoroutinefunction(main._sync_batch)
