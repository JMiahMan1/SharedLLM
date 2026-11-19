"""How long each announcement clip lasts, and putting a speaker's volume back once it has played.

An announcement turns a speaker up, plays a clip and must then put the volume
back. The restore used to wait a guessed number of seconds (or never happen,
so the speaker stayed at the announcement volume for good). Every clip the
execution service serves is made here, so its real length is read from its WAV
header when it is cached (:func:`remember`) and looked up again by URL
(:func:`seconds_for_url`).

:func:`schedule_volume_restore` puts a volume back after the clip. Two
announcements on one speaker in a row keep the volume from before the first:
the second would otherwise take the raised volume for the one to go back to.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote, urlparse

from services.common.audio_clip import wav_duration

log = logging.getLogger("execution.announce_clips")

#: Used only when a clip's length is unknown (a URL this service did not make).
DEFAULT_CLIP_SECONDS = 10.0
#: Time for the speaker to fetch and start the clip, and to finish its last buffer.
RESTORE_MARGIN_S = 2.0
#: Longest wait before a volume is put back, whatever a header says.
MAX_CLIP_SECONDS = 600.0
_MAX_REMEMBERED = 256

_durations: OrderedDict[str, float] = OrderedDict()
#: entity_id -> (volume before the first pending announcement, the task that will restore it)
_pending: dict[str, tuple[float, asyncio.Task[Any]]] = {}
_sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep


def remember(media_id: str, audio: bytes) -> float | None:
    """Note the length of a clip the media server is about to serve; returns it."""
    seconds = wav_duration(audio)
    if seconds is None:
        return None
    _durations.pop(media_id, None)
    _durations[media_id] = seconds
    while len(_durations) > _MAX_REMEMBERED:
        _durations.popitem(last=False)
    return seconds


def media_id_of(url: str | None) -> str | None:
    path = urlparse(str(url or "")).path
    if "/media/" not in path:
        return None
    return unquote(path.rsplit("/media/", 1)[1]) or None


def seconds_for_url(url: str | None) -> float | None:
    """The clip's real length when this service made it, else None."""
    mid = media_id_of(url)
    return _durations.get(mid) if mid else None


def restore_delay(url: str | None, duration_s: float | None = None) -> float:
    """How long to wait before undoing what an announcement changed."""
    seconds = duration_s if duration_s is not None else seconds_for_url(url)
    if seconds is None:
        seconds = DEFAULT_CLIP_SECONDS
    return min(MAX_CLIP_SECONDS, max(0.0, float(seconds))) + RESTORE_MARGIN_S


def pending_volume(entity_id: str) -> float | None:
    """The volume a speaker will be put back to, while an announcement's restore is waiting."""
    entry = _pending.get(entity_id)
    return entry[0] if entry else None


def schedule_volume_restore(entity_id: str, previous: float, delay_s: float,
                            set_volume: Callable[[str, float], Awaitable[Any]]) -> asyncio.Task[Any]:
    """Put ``entity_id`` back to ``previous`` after ``delay_s``; a later call for it replaces this one.

    The earliest volume wins: a restore already waiting keeps its volume and only moves its deadline.
    """
    old = _pending.pop(entity_id, None)
    if old is not None:
        previous = old[0]
        old[1].cancel()

    async def later() -> None:
        try:
            await _sleep(delay_s)
            log.info(f"[announce] Restoring {entity_id} volume to {previous}")
            await set_volume(entity_id, previous)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(f"[announce] Could not restore {entity_id} volume: {exc}")
        finally:
            entry = _pending.get(entity_id)
            if entry is not None and entry[1] is asyncio.current_task():
                _pending.pop(entity_id, None)

    task = asyncio.ensure_future(later())
    _pending[entity_id] = (previous, task)
    return task


def reset() -> None:
    """Tests: forget clips and cancel waiting restores."""
    _durations.clear()
    for _vol, task in _pending.values():
        with contextlib.suppress(Exception):
            task.cancel()
    _pending.clear()
