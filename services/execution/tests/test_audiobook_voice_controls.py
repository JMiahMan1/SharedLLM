"""End-to-end voice control of an audiobook session.

No network: the ABS session, Home Assistant state and Music Assistant command
are all fakes. Covers the per-person MA identity, resuming a plain URL player at
the right track, writing progress back on pause/stop, and the extra transport
commands (relative seek, chapters, speed, sleep timer).
"""
import asyncio

import pytest

from services.execution import abs_client, abs_session_state
from services.execution.handlers import audiobookshelf, media
from services.execution.schemas import AudiobookshelfRequest, MediaTransportRequest, UserContext

ENTITY = "media_player.living_room"

BOOK = {
    "id": "book-1",
    "media": {
        "metadata": {"title": "A Book"},
        "duration": 600.0,
        "tracks": [{"index": 1, "duration": 60.0}, {"index": 2, "duration": 120.0}],
        "chapters": [
            {"title": "One", "start": 0.0, "end": 60.0},
            {"title": "Two", "start": 60.0, "end": 180.0},
            {"title": "Three", "start": 180.0, "end": 600.0},
        ],
    },
}

SESSION = {
    "id": "sess-1",
    "currentTime": 125.0,
    "audioTracks": [
        {"index": 1, "startOffset": 0.0, "duration": 60.0},
        {"index": 2, "startOffset": 60.0, "duration": 120.0},
        {"index": 3, "startOffset": 180.0, "duration": 420.0},
    ],
}

MA_STATE = {
    "state": "playing",
    "attributes": {
        "source": "Music Assistant Queue",
        "active_queue": "queue-1",
        "active_queue_id": "queue-1",
        "media_title": "A Book",
    },
}
PLAIN_STATE = {
    "state": "playing",
    "attributes": {"media_title": "A Book", "media_position": 65.0, "supported_features": 16384},
}


@pytest.fixture(autouse=True)
def _clean_sessions():
    abs_session_state.clear()
    yield
    abs_session_state.clear()


def _ctx(**kw):
    fields = {
        "user": "tester",
        "ha_url": "http://ha.test",
        "ha_token": "ha-token",
        "audiobookshelf_url": "http://abs.test",
        "audiobookshelf_api_key": "abs-key",
        "mass_url": "http://ma.test",
        "mass_token": "ma-token",
        "mass_server": "household",
        "mass_user_id": "person-1",
    }
    fields.update(kw)
    return UserContext(**fields)


def _abs_req(**kw):
    fields = {"action": "play", "book_id": "book-1", "entity_id": ENTITY, "user_context": _ctx()}
    fields.update(kw)
    return AudiobookshelfRequest(**fields)


def _transport_req(command, **kw):
    fields = {"entity_id": ENTITY, "command": command, "user_context": _ctx()}
    fields.update(kw)
    return MediaTransportRequest(**fields)


def _patch_play(monkeypatch, state, session, calls, ma=False):
    async def get_book(*a, **k):
        return BOOK

    async def start(*a, **k):
        return session

    async def get_state(*a, **k):
        return state

    async def is_roku(*a, **k):
        return False

    async def call_service(ha_url, ha_token, domain, service, entity=None, data=None):
        calls.append({"domain": domain, "service": service, "entity": entity, "data": data or {}})
        return {"ok": True}

    async def resolve(*a, **k):
        return type("R", (), {"ma_uri": "library://audiobook/1", "abs_item_id": "book-1", "title": "A Book"})()

    monkeypatch.setattr(audiobookshelf.abs_client, "get_book", get_book)
    monkeypatch.setattr(audiobookshelf.abs_client, "start_playback_session", start)
    monkeypatch.setattr(audiobookshelf.ha_client, "get_state", get_state)
    monkeypatch.setattr(audiobookshelf.ha_client, "call_service", call_service)
    monkeypatch.setattr("services.execution.handlers.roku.is_roku_device", is_roku)
    monkeypatch.setattr(audiobookshelf.ma_library, "resolve_audiobook_uri", resolve)
    monkeypatch.setattr(audiobookshelf, "PLAYBACK_VERIFY_INTERVAL", 0)


def _patch_transport(monkeypatch, state, calls):
    async def get_state(*a, **k):
        return state

    async def call_service(ha_url, ha_token, domain, service, entity=None, data=None):
        calls.append({"domain": domain, "service": service, "entity": entity, "data": data or {}})
        return {"ok": True}

    async def no_index(*a, **k):
        return None

    monkeypatch.setattr(media.ha_client, "get_state", get_state)
    monkeypatch.setattr(media.ha_client, "call_service", call_service)
    monkeypatch.setattr(media, "_routing_index", no_index)


def _seed_session(**kw):
    fields = {
        "session_id": "sess-1",
        "abs_url": "http://abs.test",
        "abs_key": "abs-key",
        "item_id": "book-1",
        "current_time": 125.0,
        "track_start": 60.0,
        "duration": 600.0,
        "chapters": BOOK["media"]["chapters"],
    }
    fields.update(kw)
    return abs_session_state.remember(ENTITY, **fields)


class TestMusicAssistantIdentity:
    async def test_act_as_is_sent_on_ma_playback(self, monkeypatch):
        calls = []
        _patch_play(monkeypatch, MA_STATE, {"id": "sess-1"}, calls, ma=True)

        result = await audiobookshelf.handle_audiobookshelf(_abs_req())

        assert result.status == "SUCCESS", result.message
        ma_calls = [c for c in calls if c["domain"] == "music_assistant"]
        assert len(ma_calls) == 1
        assert ma_calls[0]["data"]["user"] == "person-1"

    async def test_no_identity_is_sent_without_a_household_account(self, monkeypatch):
        calls = []
        _patch_play(monkeypatch, MA_STATE, {"id": "sess-1"}, calls, ma=True)
        req = _abs_req(user_context=_ctx(mass_server="own", mass_user_id=""))

        await audiobookshelf.handle_audiobookshelf(req)

        ma_calls = [c for c in calls if c["domain"] == "music_assistant"]
        assert "user" not in ma_calls[0]["data"]


class TestPlainPlayerResume:
    async def test_resume_seeks_to_current_time_in_the_containing_track(self, monkeypatch):
        calls = []
        _patch_play(monkeypatch, PLAIN_STATE, SESSION, calls)

        result = await audiobookshelf.handle_audiobookshelf(_abs_req())

        assert result.status == "SUCCESS", result.message
        plays = [c for c in calls if c["service"] == "play_media"]
        seeks = [c for c in calls if c["service"] == "media_seek"]
        assert len(plays) == 1 and len(seeks) == 1
        # 125 s sits in track 2 (startOffset 60), so the seek is 65 s into it.
        assert "abs-session/sess-1/2" in plays[0]["data"]["media_content_id"]
        assert seeks[0]["data"] == {"seek_position": 65.0}

    async def test_no_current_time_means_no_seek(self, monkeypatch):
        calls = []
        _patch_play(monkeypatch, PLAIN_STATE, {"id": "sess-1"}, calls)

        await audiobookshelf.handle_audiobookshelf(_abs_req())

        assert not [c for c in calls if c["service"] == "media_seek"]

    async def test_abs_session_is_remembered_for_later_sync(self, monkeypatch):
        calls = []
        _patch_play(monkeypatch, PLAIN_STATE, SESSION, calls)

        await audiobookshelf.handle_audiobookshelf(_abs_req())

        info = abs_session_state.active(ENTITY)
        assert info["session_id"] == "sess-1"
        assert info["track_start"] == 60.0
        assert [c["number"] for c in info["chapters"]] == [1, 2, 3]


class TestProgressWriteBack:
    async def test_pause_syncs_the_abs_session(self, monkeypatch):
        calls = []
        _seed_session()
        synced = []

        async def sync(url, key, sid, current, listened, duration):
            synced.append((sid, current, duration))

        monkeypatch.setattr(abs_client, "sync_session_position", sync)
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        result = await media.handle_media_transport(_transport_req("pause"))

        assert result.status == "SUCCESS", result.message
        # Player is 65 s into the track that starts at 60 s: 125 s of book time.
        assert synced == [("sess-1", 125.0, 600.0)]
        assert abs_session_state.active(ENTITY) is not None

    async def test_stop_closes_the_abs_session(self, monkeypatch):
        calls = []
        _seed_session()
        closed = []

        async def close(url, key, sid, current, duration):
            closed.append((sid, current, duration))

        monkeypatch.setattr(abs_client, "close_session", close)
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        result = await media.handle_media_transport(_transport_req("stop"))

        assert result.status == "SUCCESS", result.message
        assert closed == [("sess-1", 125.0, 600.0)]
        assert abs_session_state.active(ENTITY) is None


class TestEnhancedTransport:
    async def test_seek_relative_moves_from_current_position(self, monkeypatch):
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        forward = await media.handle_media_transport(_transport_req("seek_relative", seconds=30))
        backward = await media.handle_media_transport(_transport_req("seek_relative", seconds=-30))

        assert forward.status == "SUCCESS" and backward.status == "SUCCESS"
        seeks = [c for c in calls if c["service"] == "media_seek"]
        assert [c["data"]["seek_position"] for c in seeks] == [95.0, 35.0]

    async def test_seek_relative_defaults_to_thirty_seconds(self, monkeypatch):
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        await media.handle_media_transport(_transport_req("seek_relative"))

        seeks = [c for c in calls if c["service"] == "media_seek"]
        assert seeks[0]["data"]["seek_position"] == 95.0

    async def test_chapter_next_previous_and_goto(self, monkeypatch):
        _seed_session()
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        nxt = await media.handle_media_transport(_transport_req("chapter_next"))
        prev = await media.handle_media_transport(_transport_req("chapter_previous"))
        goto = await media.handle_media_transport(_transport_req("chapter_goto", chapter=3))

        assert nxt.status == "SUCCESS" and prev.status == "SUCCESS" and goto.status == "SUCCESS"
        seeks = [c for c in calls if c["service"] == "media_seek"]
        # Chapter starts are book time; subtract the 60 s track offset.
        assert [c["data"]["seek_position"] for c in seeks] == [120.0, 0.0, 120.0]

    async def test_chapter_goto_rejects_an_out_of_range_number(self, monkeypatch):
        _seed_session()
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        result = await media.handle_media_transport(_transport_req("chapter_goto", chapter=9))

        assert result.status == "FAILURE"
        assert "out of range" in result.message
        assert not [c for c in calls if c["service"] == "media_seek"]

    async def test_chapters_without_a_session_fail_cleanly(self, monkeypatch):
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        result = await media.handle_media_transport(_transport_req("chapter_next"))

        assert result.status == "FAILURE"
        assert "not available" in result.message

    async def test_speed_uses_the_music_assistant_queue(self, monkeypatch):
        from services.execution.handlers import mass_client

        ma_state = {"state": "playing", "attributes": dict(MA_STATE["attributes"])}
        calls = []
        commands = []
        _patch_transport(monkeypatch, ma_state, calls)

        async def ma_command(url, token, command, args=None, **kw):
            commands.append((command, args))
            return None

        monkeypatch.setattr(mass_client, "ma_command", ma_command)

        result = await media.handle_media_transport(_transport_req("speed", rate=1.5))

        assert result.status == "SUCCESS", result.message
        assert commands == [("player_queues/set_speed", {"queue_id": "queue-1", "speed": 1.5})]

    async def test_speed_is_not_supported_on_a_plain_speaker(self, monkeypatch):
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        result = await media.handle_media_transport(_transport_req("speed", rate=1.5))

        assert result.status == "FAILURE"
        assert result.message == "Speed is not supported on this speaker."


class TestSleepTimer:
    async def test_sleep_schedules_a_pause(self, monkeypatch):
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        result = await media.handle_media_transport(_transport_req("sleep", minutes=0.0005))

        assert result.status == "SUCCESS", result.message
        assert "pause" in result.message
        assert abs_session_state.sleep_task(ENTITY) is not None
        await asyncio.sleep(0.1)
        assert [c["service"] for c in calls if c["service"] == "media_pause"]

    async def test_newer_sleep_replaces_the_older_one(self, monkeypatch):
        calls = []
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        await media.handle_media_transport(_transport_req("sleep", minutes=0.0005))
        first = abs_session_state.sleep_task(ENTITY)
        await media.handle_media_transport(_transport_req("sleep", minutes=30))
        second = abs_session_state.sleep_task(ENTITY)

        assert first is not second
        await asyncio.sleep(0)
        assert first.cancelled()
        await asyncio.sleep(0.1)
        assert not [c for c in calls if c["service"] == "media_pause"]

    async def test_stop_cancels_a_pending_sleep(self, monkeypatch):
        calls = []
        _seed_session()

        async def noop(*a, **k):
            return {}

        monkeypatch.setattr(abs_client, "close_session", noop)
        _patch_transport(monkeypatch, PLAIN_STATE, calls)

        await media.handle_media_transport(_transport_req("sleep", minutes=30))
        task = abs_session_state.sleep_task(ENTITY)
        await media.handle_media_transport(_transport_req("stop"))
        await asyncio.sleep(0)

        assert task.cancelled()
        assert abs_session_state.sleep_task(ENTITY) is None
