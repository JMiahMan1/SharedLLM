"""Per-player state for the Audiobookshelf playback sessions we start.

A voice request starts an ABS session and hands its HLS URL to a non-MA
player. Later transport commands (pause / stop / seek / chapter / sleep) need
to find that session again so progress can be written back to ABS and so the
extra audiobook controls have the book's chapters to work with.

This is deliberately a small module-level dict: playback is owned by one
execution process, and a player can have only one ABS book session at a time.
Keeping it here (rather than in either handler) avoids an import cycle between
the audiobookshelf and media handlers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from services.execution import abs_client

log = logging.getLogger("execution.abs_session_state")

# entity_id -> everything needed to sync / close an ABS session and to resolve
# chapter positions for the enhanced transport commands.
_SESSIONS: dict[str, dict[str, Any]] = {}
# entity_id -> the asyncio task implementing the current sleep timer.
_SLEEP_TASKS: dict[str, asyncio.Task] = {}


def remember(
    entity_id: str,
    *,
    session_id: str,
    abs_url: str,
    abs_key: str,
    item_id: str,
    current_time: float = 0.0,
    track_start: float = 0.0,
    duration: float = 0.0,
    chapters: list[dict] | None = None,
) -> dict[str, Any]:
    """Record the session we just started on ``entity_id`` (replacing any old one)."""
    info: dict[str, Any] = {
        "session_id": session_id,
        "abs_url": abs_url,
        "abs_key": abs_key,
        "item_id": item_id,
        "current_time": float(current_time or 0),
        "track_start": float(track_start or 0),
        "duration": float(duration or 0),
        "chapters": chapters or [],
        "started_at": time.monotonic(),
    }
    _SESSIONS[entity_id] = info
    return info


def active(entity_id: str) -> dict[str, Any] | None:
    """The active ABS session for this player, if this handler started one."""
    return _SESSIONS.get(entity_id)


def forget(entity_id: str) -> None:
    _SESSIONS.pop(entity_id, None)
    cancel_sleep(entity_id)


def clear() -> None:
    """Test / shutdown helper: drop all sessions and cancel all sleep timers."""
    for entity_id in list(_SESSIONS):
        forget(entity_id)
    for entity_id in list(_SLEEP_TASKS):
        cancel_sleep(entity_id)


def _absolute_position(info: dict[str, Any], media_position: float | None) -> float:
    """ABS book time from the player's position inside the current audio track."""
    return float(info["track_start"]) + max(0.0, float(media_position or 0))


async def sync_position(entity_id: str, media_position: float | None = None) -> bool:
    """POST the current position to ABS when the person pauses the player."""
    info = _SESSIONS.get(entity_id)
    if not info:
        return False
    current = _absolute_position(info, media_position)
    listened = max(0.0, time.monotonic() - float(info["started_at"]))
    await abs_client.sync_session_position(
        info["abs_url"], info["abs_key"], info["session_id"],
        current, listened, info["duration"],
    )
    info["current_time"] = current
    return True


async def close(entity_id: str, media_position: float | None = None) -> bool:
    """POST the final position to ABS and stop tracking the player's session."""
    info = _SESSIONS.get(entity_id)
    if not info:
        return False
    current = _absolute_position(info, media_position)
    try:
        await abs_client.close_session(
            info["abs_url"], info["abs_key"], info["session_id"],
            current, info["duration"] or None,
        )
    finally:
        forget(entity_id)
    return True


def cancel_sleep(entity_id: str) -> None:
    """Cancel a pending sleep timer (a newer timer or a stop cancels the old one)."""
    task = _SLEEP_TASKS.pop(entity_id, None)
    if task is not None and not task.done():
        task.cancel()


def sleep_task(entity_id: str) -> asyncio.Task | None:
    return _SLEEP_TASKS.get(entity_id)


def schedule_sleep(
    entity_id: str,
    delay_seconds: float,
    pause: Callable[[], Awaitable[None]],
) -> asyncio.Task:
    """Replace any existing timer with one that pauses ``entity_id`` after a delay."""
    cancel_sleep(entity_id)

    async def _sleep_then_pause() -> None:
        try:
            await asyncio.sleep(max(0.0, float(delay_seconds)))
            await pause()
        except asyncio.CancelledError:
            pass
        finally:
            _SLEEP_TASKS.pop(entity_id, None)

    task = asyncio.create_task(_sleep_then_pause())
    _SLEEP_TASKS[entity_id] = task
    return task
