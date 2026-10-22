"""Liveness probes for external service URLs.

The gateway talks to several self-hosted services (Audiobookshelf, Music
Assistant, the desktop GPU box). When one of them wedges, every caller sees a
different failure: a hang, a 502, an empty body. These helpers turn all of that
into one classified answer an admin notice or a diagnostics script can print:

* ``OK``         — answered with an expected status.
* ``UNAVAILABLE`` — no URL is configured for this service.
* ``UNREACHABLE`` — timed out or the connection failed (DNS, refused, reset).
* ``ERROR``       — answered HTTP with an unexpected status.

A probe never raises: a bad service must not take the caller down with it.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import aiohttp

OK = "OK"
UNAVAILABLE = "UNAVAILABLE"
UNREACHABLE = "UNREACHABLE"
ERROR = "ERROR"

#: A liveness probe has to fail fast. A wedged host can accept TCP and never
#: answer, so waiting the data-call timeout (12 s) just delays every caller.
DEFAULT_PROBE_TIMEOUT = 4.0


@dataclass
class ProbeResult:
    """One service's answer, with enough detail to act on."""

    name: str
    url: str
    status: str
    detail: str = ""
    code: int | None = None
    milliseconds: int | None = None
    expected: tuple[int, ...] | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def line(self) -> str:
        """A compact status for logs and admin notices."""
        if self.ok:
            return OK
        if self.code is not None:
            return f"{self.status} ({self.code})"
        return f"{self.status} ({self.detail})" if self.detail else self.status

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "status": self.status,
            "ok": self.ok,
            "detail": self.detail,
            "code": self.code,
            "milliseconds": self.milliseconds,
        }


def _clean_url(url: str | None) -> str:
    return str(url or "").strip().rstrip("/")


async def probe_http(
    client: aiohttp.ClientSession,
    name: str,
    url: str | None,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    expect: Iterable[int] | None = (200,),
) -> ProbeResult:
    """Probe one URL and classify the answer.

    ``expect=None`` accepts any HTTP status (use it when "the server answered
    at all" is the question). ``expect=(200,)`` means a 502 from the fronting
    proxy is an ``ERROR``, which is what a caller actually experiences.
    """
    clean = _clean_url(url)
    if not clean:
        return ProbeResult(name, "", UNAVAILABLE, "No URL configured")
    if not clean.startswith(("http://", "https://")):
        return ProbeResult(name, clean, ERROR, "URL must start with http:// or https://")

    expected = tuple(expect) if expect is not None else None
    started = time.monotonic()
    try:
        async with client.get(
            clean,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        ) as resp:
            code = int(resp.status)
            ms = int((time.monotonic() - started) * 1000)
            if expected is None or code in expected:
                return ProbeResult(name, clean, OK, f"HTTP {code}", code, ms, expected)
            return ProbeResult(name, clean, ERROR, f"HTTP {code}", code, ms, expected)
    except (asyncio.TimeoutError, TimeoutError):
        return ProbeResult(name, clean, UNREACHABLE, f"No answer within {timeout:g}s", None, None, expected)
    except (aiohttp.ClientConnectionError, OSError) as e:
        return ProbeResult(name, clean, UNREACHABLE, type(e).__name__, None, None, expected)
    except Exception as e:  # a probe must never take its caller down
        return ProbeResult(name, clean, ERROR, type(e).__name__, None, None, expected)


async def probe_many(
    client: aiohttp.ClientSession,
    targets: dict[str, str],
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    expectations: dict[str, Iterable[int] | None] | None = None,
) -> list[ProbeResult]:
    """Probe every ``name -> url`` target concurrently, preserving input order."""
    expectations = expectations or {}
    results = await asyncio.gather(
        *(
            probe_http(client, name, url, timeout=timeout, expect=expectations.get(name, (200,)))
            for name, url in targets.items()
        )
    )
    return list(results)
