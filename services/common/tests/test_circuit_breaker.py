from __future__ import annotations

import asyncio

import pytest

from services.common.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    breaker_snapshots,
    get_breaker,
    reset_breakers,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def run(coro):
    return asyncio.run(coro)


def test_a_breaker_opens_after_consecutive_failures_and_recovers():
    clock = Clock()
    breaker = CircuitBreaker("execution", fail_max=2, reset_timeout=30.0, clock=clock)

    async def fail():
        raise RuntimeError("down")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            run(breaker.call(fail))

    assert breaker.snapshot()["state"] == CircuitState.OPEN.value
    with pytest.raises(CircuitOpenError) as blocked:
        run(breaker.call(fail))
    assert blocked.value.retry_after == 30.0

    clock.now += 31.0
    assert breaker.snapshot()["state"] == CircuitState.HALF_OPEN.value

    async def succeed():
        return "ok"

    assert run(breaker.call(succeed)) == "ok"
    assert breaker.snapshot() == {
        "name": "execution",
        "state": "closed",
        "failures": 0,
        "fail_max": 2,
        "reset_timeout_s": 30.0,
        "retry_after_s": 0.0,
    }


def test_a_failed_half_open_trial_reopens_the_breaker():
    clock = Clock()
    breaker = CircuitBreaker("rag", fail_max=1, reset_timeout=10.0, clock=clock)

    async def fail():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        run(breaker.call(fail))
    clock.now += 11.0

    with pytest.raises(RuntimeError):
        run(breaker.call(fail))

    snapshot = breaker.snapshot()
    assert snapshot["state"] == CircuitState.OPEN.value
    assert snapshot["retry_after_s"] == 10.0


def test_an_http_5xx_counts_as_a_failure_without_an_exception():
    breaker = CircuitBreaker("storage", fail_max=1)

    class Response:
        status = 503

    async def unavailable():
        return Response()

    response = run(
        breaker.call(unavailable, is_failure=lambda item: item.status >= 500)
    )

    assert response.status == 503
    assert breaker.snapshot()["state"] == CircuitState.OPEN.value


def test_only_one_half_open_trial_is_allowed():
    clock = Clock()
    breaker = CircuitBreaker("workspace", fail_max=1, reset_timeout=5.0, clock=clock)

    async def fail():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        run(breaker.call(fail))
    clock.now += 6.0

    async def slow_trial():
        await asyncio.sleep(0.01)
        return "ok"

    async def two_tasks():
        first = asyncio.create_task(breaker.call(slow_trial))
        await asyncio.sleep(0)
        with pytest.raises(CircuitOpenError):
            await breaker.call(slow_trial)
        return await first

    assert run(two_tasks()) == "ok"
    assert breaker.snapshot()["state"] == CircuitState.CLOSED.value


def test_breakers_are_independent_and_listed_for_health():
    reset_breakers()
    rag = get_breaker("rag", fail_max=3, reset_timeout=15.0)
    execution = get_breaker("execution")

    assert rag is get_breaker("rag")
    assert execution is not rag
    assert {item["name"] for item in breaker_snapshots()} == {"rag", "execution"}
