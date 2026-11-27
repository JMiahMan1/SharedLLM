"""Did playback really start? Never tell a person something is playing when it is not.

A player accepting ``play_media`` only means the call was taken. On 2026-11-24 an
Echo showed "playing" for the four seconds of the spoken reply, then went idle because
its stream was refused, and Jarvis had already said "Playing ...". So a play is
confirmed only when the player (or the Music Assistant player that drives it) reports
playing real media and is still playing ``HOLD_S`` later.

Players that never report playback (some TVs only say "on" or "off") cannot be
confirmed either way: that is ``None``, "could not tell", and is not reported as a
failure.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("execution.playback_check")

#: Seconds to wait for playback to start.
START_TIMEOUT_S = 10.0
#: Playback must still be on this long after it was first seen.
HOLD_S = 3.0
#: Seconds between state reads.
POLL_S = 1.0

PLAYING = frozenset({"playing", "buffering"})
#: States of a player that reports playback (an on/off-only device never shows these).
REPORTS_PLAYBACK = frozenset({"playing", "buffering", "paused", "idle"})
#: Media that is Jarvis talking (the reply, an announcement, hold music), not what was asked for.
_NOT_THE_MEDIA = ("ffmpeg_proxy", "/api/tts", "tts_proxy", "/tts/", "announce")

GetState = Callable[[str], Awaitable[dict | None]]


def _media_of(state: dict) -> str:
    attrs = state.get("attributes") or {}
    if not isinstance(attrs, dict):
        return ""
    return str(attrs.get("media_content_id") or attrs.get("media_title") or "")


def _playing_media(state: dict | None) -> bool:
    if not state or str(state.get("state") or "") not in PLAYING:
        return False
    media = _media_of(state).lower()
    return not any(marker in media for marker in _NOT_THE_MEDIA)


async def confirm_playing(get_state: GetState, entity_ids: list[str], *, start_timeout_s: float | None = None,
                          hold_s: float | None = None, poll_s: float | None = None) -> tuple[bool | None, str]:
    """``(True, "")`` when one of ``entity_ids`` plays and keeps playing, ``(False, why)`` when none does,
    ``(None, why)`` when none of them reports playback at all."""
    start_timeout_s = START_TIMEOUT_S if start_timeout_s is None else start_timeout_s
    hold_s = HOLD_S if hold_s is None else hold_s
    poll_s = POLL_S if poll_s is None else poll_s
    ids = [e for i, e in enumerate(entity_ids) if e and e not in entity_ids[:i]]
    if not ids:
        return None, "no player to check"
    began = time.monotonic()
    started_at: float | None = None
    started_on = ""
    seen: dict[str, str] = {}
    while True:
        states: dict[str, Any] = {}
        for eid in ids:
            try:
                states[eid] = await get_state(eid)
            except Exception as exc:  # a failed read is not a verdict
                log.warning(f"[playback] could not read {eid}: {exc}")
                states[eid] = None
            seen[eid] = str((states[eid] or {}).get("state") or "unknown")
        now = time.monotonic()
        playing = [eid for eid in ids if _playing_media(states[eid])]
        if playing:
            if started_at is None:
                started_at, started_on = now, playing[0]
            elif now - started_at >= hold_s:
                return True, ""
        elif started_at is not None:
            return False, (f"{started_on} started, then stopped after {now - started_at:.0f} s "
                           f"(now {seen.get(started_on)})")
        elif now - began >= start_timeout_s:
            states_said = ", ".join(f"{e} {s}" for e, s in seen.items())
            if not any(s in REPORTS_PLAYBACK for s in seen.values()):
                return None, f"the player does not report playback ({states_said})"
            return False, f"nothing started playing within {start_timeout_s:.0f} s ({states_said})"
        await asyncio.sleep(poll_s)
