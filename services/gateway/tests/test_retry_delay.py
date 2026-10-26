"""Retries back off exponentially, and do not march in step."""
import aiohttp
import pytest

from services.gateway import main as gateway_main
from services.gateway.main import _retry_delay


def test_the_delay_grows_with_the_attempt_and_is_jittered():
    assert 0.1 <= _retry_delay(0.1, 0) <= 0.15
    assert 0.2 <= _retry_delay(0.1, 1) <= 0.3
    # Jitter: two calls at the same attempt must not be identical, or every
    # request in flight would come back at the same instant.
    assert len({_retry_delay(0.1, 3) for _ in range(50)}) > 1


@pytest.mark.asyncio
async def test_a_retry_sleeps_a_jittered_backoff(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    async def flaky():
        raise aiohttp.ClientError("boom")

    monkeypatch.setattr(gateway_main.asyncio, "sleep", fake_sleep)

    with pytest.raises(aiohttp.ClientError):
        await gateway_main.retry_http_request(
            flaky, "test", max_retries=1, base_delay=0.2, dns_recovery=False
        )

    assert len(slept) == 1
    assert 0.2 <= slept[0] <= 0.3
