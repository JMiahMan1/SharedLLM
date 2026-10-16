"""Run the two directions of a relay (browser <-> shell, browser <-> service)
and stop both as soon as either ends.

``asyncio.gather`` waits for both. When the browser left, the other direction
kept waiting for output that never came, so the session, its worker thread
and its shell were never cleaned up.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from typing import Any


async def until_either_ends(*directions: Coroutine[Any, Any, Any]) -> None:
    tasks = [asyncio.ensure_future(d) for d in directions]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
