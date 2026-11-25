"""playback_check: a play counts only when the player keeps playing real media."""
from __future__ import annotations

import pytest

from services.execution import playback_check as pc


def _states(*sequence):
    """get_state answering each poll with the next state for every player (the last repeats)."""
    seq = list(sequence)

    async def get(_eid):
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return get


PLAYING = {"state": "playing", "attributes": {"media_content_id": "library://audiobook/1", "media_title": "Book"}}
IDLE = {"state": "idle", "attributes": {}}


@pytest.mark.asyncio
async def test_playing_that_holds_is_confirmed():
    ok, why = await pc.confirm_playing(_states(PLAYING), ["media_player.den"], start_timeout_s=1, hold_s=0, poll_s=0)
    assert (ok, why) == (True, "")


@pytest.mark.asyncio
async def test_playing_for_a_moment_then_idle_is_a_failure():
    ok, why = await pc.confirm_playing(_states(PLAYING, IDLE), ["media_player.den"], start_timeout_s=1, hold_s=5,
                                       poll_s=0)
    assert ok is False and "started, then stopped" in why


@pytest.mark.asyncio
async def test_jarvis_talking_is_not_the_media():
    reply = {"state": "playing", "attributes": {"media_content_id": "http://hub/api/esphome/ffmpeg_proxy/x/y.wav"}}
    ok, why = await pc.confirm_playing(_states(reply), ["media_player.den"], start_timeout_s=0, hold_s=0, poll_s=0)
    assert ok is False and "nothing started playing" in why


@pytest.mark.asyncio
async def test_a_player_that_only_says_on_cannot_be_told():
    ok, why = await pc.confirm_playing(_states({"state": "on"}), ["media_player.tv"], start_timeout_s=0, hold_s=0,
                                       poll_s=0)
    assert ok is None and "does not report playback" in why


@pytest.mark.asyncio
async def test_the_music_assistant_twin_playing_is_enough():
    async def get(eid):
        return PLAYING if eid == "media_player.den_ma" else IDLE
    ok, _why = await pc.confirm_playing(get, ["media_player.den_ma", "media_player.den"], start_timeout_s=1, hold_s=0,
                                        poll_s=0)
    assert ok is True
