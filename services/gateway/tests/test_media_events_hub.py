"""P2-T25: per-user media event hub — normalization, snapshot-first SSE,
heartbeats, idle stop with an injectable clock, and user isolation."""
import json

import pytest
from fastapi.testclient import TestClient

from services.gateway import media_events
from services.gateway.main import app
from services.gateway.media_events import (
    MediaEventHub,
    acquire_media_hub,
    normalize_ha_state,
    normalize_ma_event,
    stop_all_media_hubs,
)


class FakeSource:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _parse_sse(chunk: str) -> tuple[str, dict]:
    lines = chunk.strip().split("\n")
    event_type = lines[0].removeprefix("event: ")
    payload = json.loads(lines[1].removeprefix("data: "))
    return event_type, payload


@pytest.fixture(autouse=True)
async def _clean_hubs():
    await stop_all_media_hubs()
    yield
    await stop_all_media_hubs()


def test_normalize_ha_state_media_player():
    event = normalize_ha_state({
        "entity_id": "media_player.kitchen",
        "state": "playing",
        "attributes": {
            "media_title": "Song",
            "media_artist": "Artist",
            "media_album_name": "Album",
            "media_content_id": "uri://song",
            "media_position": 42.1,
            "media_position_updated_at": "2026-09-26T12:00:00Z",
            "media_duration": 213,
            "volume_level": 0.35,
            "is_volume_muted": False,
            "shuffle": True,
            "repeat": "all",
            "entity_picture": "/api/media_proxy/abc",
            "group_members": ["media_player.office"],
        },
    })

    assert event["type"] == "player"
    assert event["output_id"] == "media_player.kitchen"
    assert event["state"] == "playing"
    assert event["item"] == {
        "uri": "uri://song",
        "title": "Song",
        "artists": ["Artist"],
        "album": "Album",
        "image": "/api/media_proxy/abc",
        "duration": 213,
    }
    assert event["position"] == 42.1
    assert event["position_updated_at"] == "2026-09-26T12:00:00Z"
    assert event["volume"] == 0.35
    assert event["muted"] is False
    assert event["shuffle"] is True
    assert event["repeat"] == "all"
    assert event["available"] is True
    assert event["group_members"] == ["media_player.office"]


def test_normalize_ha_state_ignores_other_domains():
    assert normalize_ha_state({"entity_id": "light.kitchen", "state": "on"}) is None
    assert normalize_ha_state(None) is None


def test_normalize_ma_player_and_queue_events():
    player = normalize_ma_event("player_updated", {
        "player_id": "kitchen",
        "state": "playing",
        "current_item": {
            "uri": "spotify://track/1",
            "name": "Song",
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
            "duration": 200,
            "metadata": {"image_url": "http://ma/image.jpg"},
        },
        "elapsed_time": 12.5,
        "volume_level": 40,
        "volume_muted": False,
        "shuffle": True,
        "repeat_mode": "all",
        "available": True,
        "group_childs": ["office"],
    })

    assert player["type"] == "player"
    assert player["output_id"] == "ma:kitchen"
    assert player["item"]["title"] == "Song"
    assert player["item"]["artists"] == ["Artist"]
    assert player["item"]["album"] == "Album"
    assert player["item"]["image"] == "http://ma/image.jpg"
    assert player["position"] == 12.5
    assert player["repeat"] == "all"

    queue = normalize_ma_event("queue_updated", {"queue_id": "q1"})
    assert queue == {"type": "queue", "output_id": "ma:q1", "items_changed": True}

    time_event = normalize_ma_event("queue_time_updated", {"queue_id": "q1", "elapsed_time": 5})
    assert time_event == {
        "type": "queue",
        "output_id": "ma:q1",
        "items_changed": False,
        "position": 5,
    }

    assert normalize_ma_event("something_else", {}) is None


async def test_subscribe_streams_retry_then_snapshot_then_events():
    source = FakeSource()
    hub = MediaEventHub("alice", sources=[source], heartbeat_interval=10)
    stream = hub.subscribe()

    first = await anext(stream)
    assert first == "retry: 3000\n\n"
    assert source.started

    event_type, payload = _parse_sse(await anext(stream))
    assert event_type == "snapshot"
    assert payload == {"type": "snapshot", "players": []}

    hub.ingest_ha_state({
        "entity_id": "media_player.kitchen",
        "state": "playing",
        "attributes": {"media_title": "Song"},
    })

    event_type, payload = _parse_sse(await anext(stream))
    assert event_type == "player"
    assert payload["output_id"] == "media_player.kitchen"
    assert payload["state"] == "playing"

    await stream.aclose()
    assert hub.subscriber_count == 0


async def test_snapshot_contains_players_seen_before_subscribe():
    hub = MediaEventHub("alice", sources=[])
    hub.ingest_ma_player({"player_id": "kitchen", "state": "idle"})

    stream = hub.subscribe()
    await anext(stream)
    _, payload = _parse_sse(await anext(stream))

    assert payload["type"] == "snapshot"
    assert payload["players"] == [{
        "type": "player",
        "output_id": "ma:kitchen",
        "state": "idle",
        "item": None,
        "position": None,
        "position_updated_at": None,
        "volume": None,
        "muted": False,
        "shuffle": None,
        "repeat": None,
        "available": True,
        "group_members": [],
    }]
    await stream.aclose()


async def test_heartbeat_when_nothing_is_happening():
    hub = MediaEventHub("alice", sources=[], heartbeat_interval=0.05)
    stream = hub.subscribe()
    await anext(stream)
    await anext(stream)

    event_type, payload = _parse_sse(await anext(stream))

    assert event_type == "heartbeat"
    assert payload == {"type": "heartbeat"}
    await stream.aclose()


async def test_stops_after_idle_timeout_with_injectable_clock():
    source = FakeSource()
    now = [0.0]
    hub = MediaEventHub(
        "alice", sources=[source], clock=lambda: now[0], idle_timeout=60.0,
    )
    stream = hub.subscribe()
    await anext(stream)
    await anext(stream)
    await stream.aclose()

    assert not await hub.check_idle()
    now[0] = 59.0
    assert not await hub.check_idle()
    now[0] = 61.0
    assert await hub.check_idle()
    assert hub.stopped
    assert source.stopped

    await hub.stop()


async def test_events_do_not_leak_between_users(monkeypatch):
    hub_a = MediaEventHub("alice", sources=[], heartbeat_interval=0.05)
    hub_b = MediaEventHub("bob", sources=[], heartbeat_interval=0.05)
    stream_a = hub_a.subscribe()
    stream_b = hub_b.subscribe()
    for stream in (stream_a, stream_b):
        await anext(stream)
        await anext(stream)

    hub_a.ingest_ha_state({"entity_id": "media_player.kitchen", "state": "playing"})

    event_type, payload = _parse_sse(await anext(stream_a))
    assert event_type == "player"
    assert payload["output_id"] == "media_player.kitchen"

    event_type, payload = _parse_sse(await anext(stream_b))
    assert event_type == "heartbeat"

    await stream_a.aclose()
    await stream_b.aclose()


async def test_acquire_media_hub_reuses_and_removes_on_stop(monkeypatch):
    created = []

    def fake_build_sources(hub, creds):
        created.append(hub)
        return [FakeSource()]

    monkeypatch.setattr(media_events, "_build_sources", fake_build_sources)

    hub_a = await acquire_media_hub("alice", {"user": "alice"})
    hub_b = await acquire_media_hub("alice", {"user": "alice"})
    assert hub_a is hub_b
    assert created == [hub_a]

    await hub_a.stop()
    assert media_events.get_media_hub("alice") is None

    hub_c = await acquire_media_hub("alice", {"user": "alice"})
    assert hub_c is not hub_a
    assert len(created) == 2


def test_media_events_endpoint_requires_auth():
    response = TestClient(app).get("/api/media/events")
    assert response.status_code == 401
