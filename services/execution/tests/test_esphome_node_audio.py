"""Execution's direct ESPHome alerts send the node audio in its own format, through HomeHub's ffmpeg proxy."""
from __future__ import annotations

from typing import ClassVar

import pytest
from aioesphomeapi import DeviceInfo, MediaPlayerFormatPurpose, MediaPlayerInfo, MediaPlayerSupportedFormat

from services import config
from services.execution import esphome_client

HUB = "http://hub.example.com:8123"
CLIP = "http://jarvis.example.com:8888/media/alarm.wav"
FORMATS = [MediaPlayerSupportedFormat(format="flac", sample_rate=48000, num_channels=1, sample_bytes=2,
                                      purpose=MediaPlayerFormatPurpose.ANNOUNCEMENT)]


class _Resp:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def json(self, content_type=None):
        return self._data


class _Session:
    calls: ClassVar[list[tuple[str, dict, dict]]] = []
    answer: ClassVar[object] = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, timeout=None):
        _Session.calls.append((url, json, headers))
        if isinstance(_Session.answer, Exception):
            raise _Session.answer
        return _Resp(*_Session.answer)


@pytest.fixture
def hub(monkeypatch):
    _Session.calls = []
    _Session.answer = (200, {"url": f"{HUB}/api/esphome/ffmpeg_proxy/exec-den/t.flac", "proxied": True})
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB)
    monkeypatch.setattr(esphome_client.aiohttp, "ClientSession", _Session)
    return _Session


def _player(formats):
    return MediaPlayerInfo(object_id="speaker", key=8, name="Speaker", supported_formats=list(formats))


async def test_a_node_with_formats_gets_the_hubs_proxy_url(hub):
    url = await esphome_client.node_media_url("den speaker", _player(FORMATS), CLIP, announcement=True)
    assert url == f"{HUB}/api/esphome/ffmpeg_proxy/exec-den/t.flac"
    (posted, body, headers), = hub.calls
    assert posted == f"{HUB}/hub/esphome/audio_url" and "X-Internal-Secret" in headers
    assert body["url"] == CLIP and body["announcement"] is True and body["device"] == "exec-den-speaker"
    assert body["formats"] == [{"format": "flac", "sample_rate": 48000, "num_channels": 1, "sample_bytes": 2,
                                "purpose": 1}]


async def test_no_formats_no_hub_or_a_failing_hub_keep_the_original_url(hub, monkeypatch):
    assert await esphome_client.node_media_url("den", _player([]), CLIP, announcement=True) == CLIP
    assert hub.calls == []
    hub.answer = (500, {"message": "boom"})
    assert await esphome_client.node_media_url("den", _player(FORMATS), CLIP, announcement=True) == CLIP
    hub.answer = OSError("connection refused")
    assert await esphome_client.node_media_url("den", _player(FORMATS), CLIP, announcement=True) == CLIP
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    hub.calls = []
    assert await esphome_client.node_media_url("den", _player(FORMATS), CLIP, announcement=True) == CLIP
    assert hub.calls == []


async def test_an_alarm_on_the_node_plays_the_converted_url(hub, monkeypatch):
    commands = []

    class Node:
        async def device_info_and_list_entities(self):
            return DeviceInfo(name="den"), [_player(FORMATS)], []

        def media_player_command(self, key, **kw):
            commands.append((key, kw))

    async def get_device(name):
        return {"name": name}

    async def with_connection(cfg, op, name=None):
        return await op(Node())

    monkeypatch.setattr(esphome_client, "get_device", get_device)
    monkeypatch.setattr(esphome_client, "_with_connection", with_connection)
    out = await esphome_client.trigger_device_alert("den", title="Pasta", message="Timer done", media_url=CLIP,
                                                    flash_screen=False)
    assert out["has_speaker"] is True
    assert commands == [(8, {"media_url": f"{HUB}/api/esphome/ffmpeg_proxy/exec-den/t.flac", "announcement": True})]
