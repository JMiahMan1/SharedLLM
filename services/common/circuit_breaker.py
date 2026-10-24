"""Process-local circuit breakers for outbound service calls.

The breaker is intentionally small: one object per downstream dependency, kept
for the life of the process. It opens after a run of failures, refuses calls
while open, then lets one trial through after the reset timeout. A successful
trial closes it; a failed one reopens it.

The breaker is safe to share across asyncio tasks. State transitions happen
without awaiting, so two tasks cannot interleave inside one transition. A task
that arrives while another task is making the half-open trial is refused
immediately rather than queued behind it.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")


class CircuitState(StrEnum):
    """The three states a breaker can be in."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised instead of calling a downstream service while its breaker is open."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = max(0.0, retry_after)
        super().__init__(
            f"The {name} circuit is open; retry in {self.retry_after:.0f}s"
        )


@dataclass
class CircuitBreaker:
    """A failure-counting breaker for one outbound dependency."""

    name: str
    fail_max: int = 5
    reset_timeout: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _half_open_trial: bool = field(default=False, init=False)

    def _refresh(self) -> None:
        """Move an expired open breaker to half-open before deciding a call."""
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return
        if self.clock() - self._opened_at >= self.reset_timeout:
            self._state = CircuitState.HALF_OPEN
            self._half_open_trial = False

    def _retry_after(self) -> float:
        if self._opened_at is None:
            return 0.0
        return self.reset_timeout - (self.clock() - self._opened_at)

    def _before_call(self) -> None:
        self._refresh()
        if self._state is CircuitState.OPEN:
            raise CircuitOpenError(self.name, self._retry_after())
        if self._state is CircuitState.HALF_OPEN:
            if self._half_open_trial:
                raise CircuitOpenError(self.name, self._retry_after())
            self._half_open_trial = True

    def _record_failure(self) -> None:
        self._half_open_trial = False
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = self.clock()
            return
        self._failures += 1
        if self._failures >= self.fail_max:
            self._state = CircuitState.OPEN
            self._opened_at = self.clock()

    def _record_success(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = None
        self._half_open_trial = False

    async def call(
        self,
        action: Callable[[], Awaitable[T]],
        *,
        is_failure: Callable[[T], bool] | None = None,
    ) -> T:
        """Run ``action`` unless the breaker is open.

        ``is_failure`` classifies a returned value that is a downstream failure
        even though it did not raise, such as an HTTP 503 response. It is not
        called when ``action`` raises.
        """
        self._before_call()
        try:
            result = await action()
        except Exception:
            self._record_failure()
            raise
        if is_failure is not None and is_failure(result):
            self._record_failure()
        else:
            self._record_success()
        return result

    def snapshot(self) -> dict[str, object]:
        """The breaker's state, for a health endpoint or an operator's log."""
        self._refresh()
        return {
            "name": self.name,
            "state": self._state.value,
            "failures": self._failures,
            "fail_max": self.fail_max,
            "reset_timeout_s": self.reset_timeout,
            "retry_after_s": round(self._retry_after(), 1)
            if self._state is CircuitState.OPEN
            else 0.0,
        }


_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    *,
    fail_max: int = 5,
    reset_timeout: float = 30.0,
) -> CircuitBreaker:
    """Return the process-wide breaker for ``name``, creating it on first use.

    A later call with different limits does not reconfigure a live breaker: the
    limits are fixed when the dependency is first used so one caller cannot
    silently change the policy of another.
    """
    breaker = _BREAKERS.get(name)
    if breaker is None:
        breaker = CircuitBreaker(
            name=name,
            fail_max=fail_max,
            reset_timeout=reset_timeout,
        )
        _BREAKERS[name] = breaker
    return breaker


def breaker_snapshots() -> list[dict[str, object]]:
    """Snapshots of every breaker this process has created."""
    return [breaker.snapshot() for breaker in _BREAKERS.values()]


def reset_breakers() -> None:
    """Forget every breaker. Intended for tests, not for runtime use."""
    _BREAKERS.clear()
