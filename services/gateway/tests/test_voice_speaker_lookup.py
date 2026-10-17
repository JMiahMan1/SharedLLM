"""The gateway answers a voice satellite's request as the person the
speech-to-text proxy recognised, once, and only for the same words."""
import pytest

import services.gateway.cache as gateway_cache
from services.common import voice_speaker
from services.gateway.main import _recognised_speaker


class _SyncRedis:
    def __init__(self, values):
        self.values = values

    def getdel(self, key):
        return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_the_recognised_speaker_is_used_once(monkeypatch):
    redis = _SyncRedis({voice_speaker.key("turn on the kitchen lights"): voice_speaker.encode("michele", 0.9)})
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: redis)

    assert await _recognised_speaker("Turn on the kitchen lights") == "michele"
    assert await _recognised_speaker("Turn on the kitchen lights") is None


@pytest.mark.asyncio
async def test_other_words_find_nobody(monkeypatch):
    redis = _SyncRedis({voice_speaker.key("turn on the kitchen lights"): voice_speaker.encode("michele", 0.9)})
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: redis)

    assert await _recognised_speaker("unlock the front door") is None
